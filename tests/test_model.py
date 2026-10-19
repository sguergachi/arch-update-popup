"""Model + delegate tests (offscreen Qt)."""
import pytest

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QListView, QStyleOptionViewItem

from conftest import load_app

app = load_app("app_model")

_updates = [
    {"name": "konsole", "old": "1-1", "new": "2-1", "repo": "official"},
    {"name": "aur-pkg", "old": "1-1", "new": "2-1", "repo": "aur"},
    {"name": "evil", "old": "1-1", "new": "2-1", "repo": "aur",
     "compromised": True, "compromise_reasons": ["listed"]},
]

_cached = {
    0: ("desc0", "notes0 " * 30, "2026-09-10", "full notes0 " * 60,
        "GitHub release", "https://example.com/r"),
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def model(qapp):
    return app.PackageModel(_updates, _cached)


class TestModel:
    def test_rows_and_display(self, model):
        assert model.rowCount() == 3
        assert model.data(model.index(0)) == "konsole"

    def test_default_checked(self, model):
        assert model.data(model.index(0), Qt.ItemDataRole.CheckStateRole) == \
            Qt.CheckState.Checked
        assert model.data(model.index(2), Qt.ItemDataRole.CheckStateRole) == \
            Qt.CheckState.Unchecked

    def test_compromised_not_checkable(self, model):
        idx = model.index(2)
        assert not (model.flags(idx) & Qt.ItemFlag.ItemIsUserCheckable)
        assert model.flags(model.index(0)) & Qt.ItemFlag.ItemIsUserCheckable
        assert model.setData(idx, Qt.CheckState.Checked,
                             Qt.ItemDataRole.CheckStateRole) is False

    def test_toggle(self, model):
        idx = model.index(0)
        assert model.setData(idx, Qt.CheckState.Unchecked,
                             Qt.ItemDataRole.CheckStateRole) is True
        assert model.selected_count() == 1  # only aur-pkg left

    def test_set_all_skips_compromised(self, model):
        model.set_all(False)
        assert model.selected_count() == 0
        model.set_all(True)
        assert model.selected_count() == 2
        assert model.checked_names() == ["konsole", "aur-pkg"]

    def test_set_info(self, model):
        rev = model.item(1)["_rev"]
        model.set_info(1, "d", "n", "2026-01-01", "f", "s", "u")
        it = model.item(1)
        assert it["desc"] == "d" and it["_rev"] == rev + 1

    def test_state_granular_signal(self, model):
        seen = []
        model.dataChanged.connect(lambda *a: seen.append(a))
        model.set_state("konsole", "installing")
        assert len(seen) == 1  # exactly one row-level repaint
        assert model.item(0)["state"] == "installing"

    def test_row_of(self, model):
        assert model.row_of("aur-pkg") == 1
        assert model.row_of("nope") is None

    def test_ensure_row(self, model):
        n = model.rowCount()
        r = model.ensure_row("new-dep")
        assert r == n and model.rowCount() == n + 1
        it = model.item(r)
        assert it.get("auto") is True
        assert it["checked"] is False
        assert model.data(model.index(r)) == "new-dep"
        assert model.ensure_row("new-dep") == r  # idempotent
        assert model.rowCount() == n + 1


@pytest.fixture()
def view(qapp, model):
    v = QListView()
    v.resize(900, 600)
    v.setModel(model)
    d = app.PackageDelegate(v)
    v.setItemDelegate(d)
    v.show()
    return v


def _option(view, row):
    idx = view.model().index(row)
    opt = QStyleOptionViewItem()
    opt.rect = view.visualRect(idx)
    opt.palette = view.palette()
    opt.font = view.font()
    return opt, idx


class TestDelegate:
    def test_size_hint_sane(self, view):
        opt, idx = _option(view, 0)
        h = view.itemDelegate().sizeHint(opt, idx).height()
        assert 70 < h < 1500, h

    def test_layout_inside_card(self, view):
        d = view.itemDelegate()
        opt, idx = _option(view, 0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        card = lay["card"]
        for key in ("check", "name", "ver", "desc", "notes", "link", "more"):
            r = lay[key]
            if r.isNull():
                continue
            assert card.left() <= r.left() and r.right() <= card.right(), key
        # The row action lives on the name line; Show more shares the
        # what's-new line with the notes text.
        assert lay["link"].top() == lay["name"].top()
        assert lay["link"].left() > lay["name"].right()
        if not lay["more"].isNull():
            assert lay["more"].top() == lay["notes"].top()
            assert lay["more"].left() > lay["notes"].left()
        # Notes text stops before the Show more link — never underneath it.
        assert lay["notes"].right() <= lay["more"].left()

    def test_checkbox_hit_toggles(self, view, qapp):
        d = view.itemDelegate()
        opt, idx = _option(view, 0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        got = []
        d.toggled.connect(got.append)
        c = lay["check"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, view.model(), opt, idx) is True
        assert got == [0]

    def test_link_hit(self, view, qapp):
        d = view.itemDelegate()
        opt, idx = _option(view, 1)
        lay = d._layout(1, view.model().item(1), opt.rect.width())
        got = []
        d.link_activated.connect(got.append)
        c = lay["link"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, view.model(), opt, idx) is True
        assert got == ["https://aur.archlinux.org/packages/aur-pkg"]

    def test_more_hit_asks_to_unfold(self, view, qapp):
        d = view.itemDelegate()
        opt, idx = _option(view, 0)  # row 0 has longer full text
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        assert not lay["more"].isNull()
        got = []
        d.more_requested.connect(got.append)
        c = lay["more"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, view.model(), opt, idx) is True
        assert got == [0]

    def test_show_more_unfolds_the_row_in_place(self, view, qapp):
        d = view.itemDelegate()
        opt, _idx = _option(view, 0)
        w = opt.rect.width()
        before = d._layout(0, view.model().item(0), w)
        assert before["more_txt"] == "Show more  ↓"
        seen = []
        view.model().layoutChanged.connect(lambda *a: seen.append(1))
        d.toggle_notes(0)
        after = d._layout(0, view.model().item(0), w)
        assert after["height"] > before["height"]
        assert after["notes"].height() > before["notes"].height()
        assert after["more_txt"] == "Show less  ↑"
        assert seen  # the view was told to re-measure the rows

    def test_folding_back_restores_the_uniform_height(self, view, qapp):
        d = view.itemDelegate()
        opt, _idx = _option(view, 0)
        w = opt.rect.width()
        d.toggle_notes(0)
        d.toggle_notes(0)
        # Rows are uniform again: the unfolded one folded back to the same
        # height every other row has.
        assert d._layout(0, view.model().item(0), w)["height"] \
            == d._layout(1, view.model().item(1), w)["height"]

    def test_an_unfolded_row_with_a_row_action_keeps_a_fold_control(
            self, view, qapp):
        # The Retry pill takes the notes line's right side, so the fold
        # control drops under the block — a row is never stuck open.
        view.model().set_state("konsole", "failed")
        d = view.itemDelegate()
        opt, _idx = _option(view, 0)
        d.toggle_notes(0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        assert lay["action_txt"] == "Retry"
        assert lay["more"].isNull()
        assert not lay["more_end"].isNull()
        assert lay["more_end"].top() > lay["notes"].top()
        assert lay["more_txt"] == "Show less  ↑"

    def test_failed_row_action(self, view, qapp):
        view.model().set_state("konsole", "failed")
        d = view.itemDelegate()
        opt, idx = _option(view, 0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        assert lay["action_txt"] == "Retry"
        got = []
        d.action_requested.connect(got.append)
        c = lay["action"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, view.model(), opt, idx) is True
        assert got == ["konsole"]

    def test_compromised_action(self, view):
        view.model()  # row 2
        d = view.itemDelegate()
        opt = QStyleOptionViewItem()
        idx = view.model().index(2)
        opt.rect = view.visualRect(idx)
        lay = d._layout(2, view.model().item(2), opt.rect.width())
        assert lay["action_txt"] == "Install anyway"

    def test_hover_move_repaints_without_crash(self, view, qapp):
        # Regression: QListView.update() takes QModelIndex, not QRect —
        # passing a rect aborted the whole app (SIGABRT) on mouse move.
        d = view.itemDelegate()
        opt, idx = _option(view, 0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        c = lay["check"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(c),
                         Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, view.model(), opt, idx) is False
        assert d._hover == (0, "check")
        qapp.processEvents()

    def test_paint_guard_degrades_row(self, view, monkeypatch, qapp):
        d = view.itemDelegate()
        monkeypatch.setattr(d, "_layout", lambda *a, **k: 1 / 0)
        opt, idx = _option(view, 0)
        pm = QPixmap(400, 200)
        p = QPainter(pm)
        d.paint(p, opt, idx)  # must not raise / abort
        p.end()
        qapp.processEvents()

    def test_sizehint_fallback(self, view, monkeypatch):
        d = view.itemDelegate()
        monkeypatch.setattr(d, "_layout", lambda *a, **k: 1 / 0)
        opt, idx = _option(view, 0)
        assert d.sizeHint(opt, idx).height() == 120


class TestThemeHelpers:
    def test_light_defaults(self, qapp):
        assert app._is_dark_theme() is False
        assert app._secondary_hex() == "#757575"
        assert app._themed_qss() == app._APP_QSS

    def test_dark_theme(self, qapp):
        from PyQt6.QtGui import QPalette, QColor
        old = qapp.palette()
        dark = QPalette()
        dark.setColor(QPalette.ColorRole.Window, QColor(35, 38, 41))
        dark.setColor(QPalette.ColorRole.Text, QColor(239, 240, 241))
        qapp.setPalette(dark)
        try:
            assert app._is_dark_theme() is True
            assert app._secondary_hex() == "#a9a9a9"
            assert app._text_hex() == "#eff0f1"
            assert "#757575" not in app._themed_qss()
            # Neutral icons re-resolve per theme instead of sharing cache.
            assert app.app_icon("copy", 14) is app.app_icon("copy", 14)
        finally:
            qapp.setPalette(old)

    def test_doc_base_color_follows_palette(self, qapp):
        from PyQt6.QtWidgets import QListView
        view = QListView()
        d = app.PackageDelegate(view)
        doc = d._doc(0, "probe", "plain desc", 300, None, False, 0)
        assert app._text_hex().lower() in doc.defaultStyleSheet().lower()

    def test_refresh_theme_clears_docs(self, qapp):
        from PyQt6.QtWidgets import QListView
        view = QListView()
        d = app.PackageDelegate(view)
        d._doc(0, "probe", "plain desc", 300, None, False, 0)
        assert len(d._docs) == 1
        d.refresh_theme()
        assert len(d._docs) == 0


class TestTrackedModel:
    def _model(self):
        m = app.TrackedModel()
        # The loader sorts and builds complete rows; the model keeps
        # exactly that order (see set_items).
        m.set_items([
            app._tracked_row("a-off", "2-1", "official"),
            app._tracked_row("b-aur", "1-1", "aur"),
            app._tracked_row("c-aur", "3-1", "aur")])
        return m

    def test_rows_keep_the_order_the_loader_built(self):
        m = self._model()
        assert m.rowCount() == 3
        assert [m.item(r)["name"] for r in range(3)] == \
            ["a-off", "b-aur", "c-aur"]

    def test_state_signal(self):
        m = self._model()
        seen = []
        m.dataChanged.connect(lambda *a: seen.append(a))
        m.set_state("b-aur", "uninstalling")
        assert len(seen) == 1
        assert m.item(m.row_of("b-aur"))["state"] == "uninstalling"
        m.set_state("nope", "done")  # unknown: silent no-op
        assert len(seen) == 1

    def test_display_role(self):
        m = self._model()
        assert m.data(m.index(0)) == "a-off"


@pytest.fixture()
def tracked_view(qapp):
    m = app.TrackedModel()
    m.set_items([
        app._tracked_row("a-off", "2-1", "official"),
        app._tracked_row("b-aur", "1-1", "aur",
                         desktop="/x/b-aur.desktop")])
    v = QListView()
    v.resize(900, 400)
    v.setModel(m)
    d = app.TrackedDelegate(v)
    v.setItemDelegate(d)
    v.show()
    return v


def _topt(view, row):
    idx = view.model().index(row)
    opt = QStyleOptionViewItem()
    opt.rect = view.visualRect(idx)
    opt.palette = view.palette()
    opt.font = view.font()
    return opt, idx


class TestTrackedDelegate:
    def test_size_hint(self, tracked_view):
        opt, idx = _topt(tracked_view, 0)
        d = tracked_view.itemDelegate()
        h = d.sizeHint(opt, idx).height()
        # Dynamic Updates-style card: name + version + desc + pills rows.
        assert h == d._geom(0, tracked_view.model().item(0),
                            opt.rect.width())["height"]
        assert h > 78

    def test_uninstall_is_always_offered(self, tracked_view):
        d = tracked_view.itemDelegate()
        for row in (0, 1):
            opt, _idx = _topt(tracked_view, row)
            lay = d._geom(row, tracked_view.model().item(row),
                          opt.rect.width())
            assert lay["action2_txt"] == "Uninstall"
            assert not lay["action2"].isNull()

    def test_uninstall_hit(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        row = tracked_view.model().row_of("a-off")
        opt, idx = _topt(tracked_view, row)
        lay = d._geom(row, tracked_view.model().item(row), opt.rect.width())
        got = []
        d.uninstall_requested.connect(got.append)
        c = lay["action2"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, tracked_view.model(), opt, idx) is True
        assert got == ["a-off"]

    def test_failed_op_pills(self, tracked_view):
        m = tracked_view.model()
        m.set_state("b-aur", "failed", op="uninstall")
        d = tracked_view.itemDelegate()
        opt, _idx = _topt(tracked_view, 0)
        row = m.row_of("b-aur")
        lay = d._geom(row, m.item(row), opt.rect.width())
        assert lay["action2_txt"] == "Retry"

    def test_remove_row(self):
        m = app.TrackedModel()
        m.set_items([app._tracked_row("a", "1", "aur"),
                     app._tracked_row("b", "1", "official")])
        assert m.rowCount() == 2
        m.remove_row("a")
        assert m.rowCount() == 1 and m.item(0)["name"] == "b"
        m.remove_row("nope")  # silent no-op
        assert m.rowCount() == 1

    def test_checkbox_toggle(self, tracked_view, qapp):
        m = tracked_view.model()
        assert m.checked_names() == []
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert not lay["check"].isNull()
        got = []
        d.toggled.connect(got.append)
        c = lay["check"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, m, opt, idx) is True
        assert got == [0]
        # Model check-state round-trips through Qt roles.
        idx0 = m.index(0)
        from PyQt6.QtCore import Qt as _Qt
        assert m.data(idx0, _Qt.ItemDataRole.CheckStateRole) == \
            _Qt.CheckState.Unchecked
        m.setData(idx0, _Qt.CheckState.Checked,
                  _Qt.ItemDataRole.CheckStateRole)
        assert m.checked_names() == ["a-off"]
        m.set_all(True)
        assert sorted(m.checked_names()) == ["a-off", "b-aur"]
        m.set_all(False)
        assert m.checked_names() == []

    def test_paint_ok(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        pm = QPixmap(900, 100)
        p = QPainter(pm)
        d.paint(p, opt, idx)
        p.end()
        qapp.processEvents()

    def test_open_pill_only_with_launcher(self, tracked_view):
        m = tracked_view.model()
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert lay["open"].isNull()
        row = m.row_of("b-aur")
        opt, idx = _topt(tracked_view, row)
        lay = d._geom(row, m.item(row), opt.rect.width())
        assert not lay["open"].isNull()
        assert lay["open_txt"] == "Open"
        # Pills share the bottom baseline inside the card, below the
        # top-right link row.
        card = lay["card"]
        for k in ("action2", "open"):
            assert lay[k].top() > lay["link"].bottom()
            assert lay[k].bottom() <= card.bottom() - app.TrackedDelegate.PBOT

    def test_open_hit(self, tracked_view, qapp):
        m = tracked_view.model()
        d = tracked_view.itemDelegate()
        row = m.row_of("b-aur")
        opt, idx = _topt(tracked_view, row)
        lay = d._geom(row, m.item(row), opt.rect.width())
        got = []
        d.open_requested.connect(got.append)
        c = lay["open"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, m, opt, idx) is True
        assert got == ["b-aur"]

    def test_link_top_right(self, tracked_view):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, tracked_view.model().item(0), opt.rect.width())
        assert not lay["link"].isNull()
        card = lay["card"]
        # Top-right corner, on the name baseline — like the Updates date.
        assert lay["link"].top() <= lay["name"].bottom()
        assert lay["link"].right() <= card.right() - app.TrackedDelegate.PAD
        assert lay["link"].left() > lay["name"].right()
        assert card.left() <= lay["link"].left()
        # Version line: the installed version, nothing else.
        assert lay["ver_left"].startswith(
            tracked_view.model().item(0)["version"])

    def test_pills_bottom_right(self, tracked_view):
        d = tracked_view.itemDelegate()
        m = tracked_view.model()
        row = m.row_of("b-aur")
        opt, idx = _topt(tracked_view, row)
        lay = d._geom(row, m.item(row), opt.rect.width())
        card = lay["card"]
        # Pills share the bottom baseline, right-aligned inside the card:
        # Uninstall rightmost, then Open.
        for k in ("action2", "open"):
            assert not lay[k].isNull()
            assert card.top() < lay[k].top()
            assert lay[k].bottom() <= card.bottom() - app.TrackedDelegate.PBOT
            assert lay[k].right() <= card.right() - app.TrackedDelegate.PAD
        assert lay["action2"].right() >= lay["open"].right()
        assert lay["action2"].left() > lay["open"].left()
        # Hint clears the pill cluster.
        assert lay["hint"].right() <= lay["open"].left()

    def test_launch_desktop(self, tmp_path, monkeypatch):
        desk = tmp_path / "foo.desktop"
        desk.write_text("[Desktop Entry]\nName=Foo\nExec=foo --bar %U\n")
        term = tmp_path / "term.desktop"
        term.write_text("[Desktop Entry]\nName=T\nTerminal=true\nExec=t\n")
        assert not app._desktop_launchable(str(term))
        started = []
        monkeypatch.setattr(app.QProcess, "startDetached",
                            lambda prog, args: started.append((prog, args))
                            or True)
        monkeypatch.setattr(app.shutil, "which", lambda p: f"/usr/bin/{p}")
        assert app._launch_desktop(str(desk)) is True
        assert started == [("/usr/bin/foo", ["--bar"])]
        assert app._launch_desktop(str(tmp_path / "nope.desktop")) is False

    def test_link_hit(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, tracked_view.model().item(0), opt.rect.width())
        got = []
        d.link_activated.connect(got.append)
        c = lay["link"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, tracked_view.model(), opt, idx) is True
        assert got == [
            "https://archlinux.org/packages/?q=a-off"]

    def test_title_display(self, tracked_view, qapp):
        # Untracked app rows show the app title, but ops use the package.
        m = tracked_view.model()
        m.set_items([app._tracked_row("firefox", "1-1", "official",
                                      title="Firefox Web Browser",
                                      desc="Browse the web")])
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert lay["title"] == "Firefox Web Browser"
        got = []
        d.uninstall_requested.connect(got.append)
        c = lay["action2"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, m, opt, idx) is True
        assert got == ["firefox"]


class TestUntrackedScan:
    def test_scan_desktop_files(self, tmp_path):
        good = tmp_path / "firefox.desktop"
        good.write_text("[Desktop Entry]\nName=Firefox\nComment=Browser\n"
                        "Exec=firefox %u\n")
        hidden = tmp_path / "secret.desktop"
        hidden.write_text("[Desktop Entry]\nName=Secret\nNoDisplay=true\n"
                          "Exec=secret\n")
        noname = tmp_path / "noname.desktop"
        noname.write_text("[Desktop Entry]\nExec=noname\n")
        (tmp_path / "zzz.txt").write_text("nope")
        found = app.scan_desktop_files([str(tmp_path)])
        assert len(found) == 1
        assert found[0]["title"] == "Firefox"
        assert found[0]["comment"] == "Browser"
        assert found[0]["exec"] == "firefox %u"

    def test_scan_dedupe_user_wins(self, tmp_path):
        sysd = tmp_path / "sys"
        userd = tmp_path / "user"
        sysd.mkdir()
        userd.mkdir()
        (sysd / "app.desktop").write_text(
            "[Desktop Entry]\nName=System App\nExec=app\n")
        (userd / "app.desktop").write_text(
            "[Desktop Entry]\nName=User App\nExec=app\n")
        found = app.scan_desktop_files([str(userd), str(sysd)])
        assert len(found) == 1
        assert found[0]["title"] == "User App"

    def test_parse_qo_owner(self):
        f, owner, ver = app.parse_qo_owner(
            "/usr/bin/firefox is owned by firefox 1.0-1")
        assert (f, owner, ver) == ("/usr/bin/firefox", "firefox", "1.0-1")
        f, owner, ver = app.parse_qo_owner(
            "error: No package owns /opt/myapp/bin/x")
        assert owner is None

    def test_loader_uses_partial_qo_output(self, qapp, monkeypatch,
                                           tmp_path):
        """pacman -Qo exits nonzero over unowned files but still prints
        the owned lines — the loader must use them, not discard."""
        owned = tmp_path / "a.desktop"
        owned.write_text("[Desktop Entry]\nName=A\nExec=a\n")
        stray = tmp_path / "b.desktop"
        stray.write_text("[Desktop Entry]\nName=B\nExec=b\n")

        class R:
            def __init__(self, rc, out):
                self.returncode = rc
                self.stdout = out

        def fake_run(cmd, **k):
            if cmd[:2] == ["pacman", "-Qo"] and len(cmd) > 3:
                return R(1, f"{cmd[2]} is owned by foo 1-1\n"
                            f"error: No package owns {cmd[3]}")
            if cmd[:2] == ["pacman", "-Qo"]:
                return R(1, "error: No package owns x")
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": str(owned), "title": "A", "comment": "",
             "exec": "a"},
            {"file": str(stray), "title": "B", "comment": "",
             "exec": "b"}])
        launchers, rows, titles = app.LibraryLoader()._load_apps()
        # The owned launcher belongs to foo's row now.
        assert launchers["foo"]["title"] == "A"
        assert launchers["foo"]["desktop"] == str(owned)
        assert titles == {"foo": "A"}
        # The unowned one is a row of its own (nothing can remove it).
        assert [(r["title"], r["name"], r["kind"], r["repo"])
                for r in rows] == [("B", "b", "app", app.SRC_MANUAL)]

    def test_a_batch_that_answered_is_never_asked_again_file_by_file(
            self, qapp, monkeypatch):
        """An unowned file answers the same way however it is asked, so a
        batched reply settles the whole desktop — asking the leftovers one
        by one cost 5.8 seconds for zero rows on a real machine."""
        calls = []

        class R:
            def __init__(self, rc, out):
                self.returncode = rc
                self.stdout = out

        def fake_run(cmd, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["pacman", "-Qo"]:
                return R(1, "/u/owned.desktop is owned by foo 1-1\n"
                            "error: No package owns /u/stray.desktop")
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": "/u/owned.desktop", "title": "Owned", "comment": "",
             "exec": "owned"},
            {"file": "/u/stray.desktop", "title": "Stray", "comment": "",
             "exec": "stray"}])
        app.LibraryLoader()._load_apps()
        asked = [c for c in calls if c[:2] == ["pacman", "-Qo"]]
        assert asked == [["pacman", "-Qo", "/u/owned.desktop",
                          "/u/stray.desktop"]]

    def test_a_batch_that_could_not_run_falls_back_file_by_file(
            self, qapp, monkeypatch, tmp_path):
        """A refused command line (too many launchers, say) is the one case
        the per-file query still answers: the batch never spoke."""
        desk = tmp_path / "zen.desktop"
        desk.write_text("[Desktop Entry]\nName=Zen\nExec=/usr/bin/zen %U\n"
                        "Type=Application\n")

        class R:
            def __init__(self, rc, out):
                self.returncode = rc
                self.stdout = out

        def fake_run(cmd, **k):
            if cmd[:2] == ["pacman", "-Qo"] and len(cmd) > 3:
                raise OSError("argument list too long")
            if cmd[:2] == ["pacman", "-Qo"]:
                return R(0, f"{cmd[2]} is owned by foo 1-1")
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": str(desk), "title": "Zen", "comment": "",
             "exec": "zen"}])
        launchers, rows, titles = app.LibraryLoader()._load_apps()
        assert titles == {"foo": "Zen"}
        assert launchers["foo"]["desktop"] == str(desk)
        assert rows == []      # nothing unowned: no stray rows

    def test_loader_dedupes_same_app(self, qapp, monkeypatch, tmp_path):
        class R:
            def __init__(self, rc, out):
                self.returncode = rc
                self.stdout = out

        def fake_run(cmd, **k):
            if cmd[:2] == ["pacman", "-Qo"]:
                return R(0, "\n".join(
                    f"{f} is owned by foo 1-1" for f in cmd[2:]))
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        # System + user copy of the same app: one launcher for the
        # package (the first in scan order wins).
        user = tmp_path / "app.desktop"
        user.write_text("[Desktop Entry]\nName=App\nExec=app\n")
        sysd = tmp_path / "sys" / "app.desktop"
        sysd.parent.mkdir()
        sysd.write_text("[Desktop Entry]\nName=App\nExec=app\n")
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": str(user), "title": "App", "comment": "",
             "exec": "app"},
            {"file": str(sysd), "title": "App", "comment": "",
             "exec": "app"}])
        launchers, rows, titles = app.LibraryLoader()._load_apps()
        assert list(launchers) == ["foo"]
        assert launchers["foo"]["desktop"] == str(user)
        assert rows == []

    def test_tracked_row_complete(self):
        row = app._tracked_row("p", "1-1", "official", title="T")
        assert row == {"name": "p", "title": "T", "version": "1-1",
                       "repo": "official", "state": "idle", "failed_op": "",
                       "desktop": None, "desc": "", "checked": False,
                       # The app's own page (AppImages / manual installs),
                       # the file behind an AppImage row, and whether the
                       # row is an application or a stray dependency.
                       "url": "", "path": "", "kind": "",
                       # Dependency rows: the applications that need the
                       # package (human names), and whether we know.
                       "needs": [], "needs_known": True}

    def test_set_items(self, qapp):
        m = app.TrackedModel()
        seen = []
        # set_items resets even without row insert/remove signals.
        m.set_items([app._tracked_row("b", "2", "aur"),
                     app._tracked_row("a", "1", "official")])
        assert m.rowCount() == 2
        assert m.item(0)["name"] == "b"  # order preserved, no re-sort
        m.dataChanged.connect(lambda *a: seen.append(a))
        m.set_all(True)
        assert m.checked_names() == ["b", "a"]
        assert len(seen) == 1


class _R:
    """Stand-in for a subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class TestDescriptionMap:
    """Descriptions for the whole list, in one call.

    Asking pacman once per package took ~65 s for a tracked list, and a
    row had no description until its turn came.
    """

    def test_descriptions_skip_the_null_placeholder(self, monkeypatch):
        out = "1password|Password manager and secure wallet\n" \
              "linux|(null)\n" \
              "zsh|The Z shell\n"
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/expac")
        monkeypatch.setattr(app, "_run", lambda cmd, **kw: _R(out))

        assert app.all_package_descs() == {
            "1password": "Password manager and secure wallet",
            "zsh": "The Z shell"}

    def test_without_expac_there_is_no_map(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        monkeypatch.setattr(app, "_run",
                            lambda *a, **k: pytest.fail("ran a query"))

        assert app.all_package_descs() == {}

    def test_a_failed_query_is_no_map(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/expac")
        monkeypatch.setattr(app, "_run",
                            lambda cmd, **kw: _R("", returncode=1))

        assert app.all_package_descs() == {}


class TestRequirerLine:
    """A row names what needs it — one line, with the rest in the panel.

    The line rides the row's bottom row, so every row is the same height
    whether it names one requirer or a dozen; the whole list opens in the
    strip under the list (see _DetailPanel), never inside the row.
    """

    @pytest.fixture()
    def deps_view(self, qapp):
        m = app.TrackedModel()
        m.set_items([
            app._tracked_row("libfoo", "1-1", "official", kind="dependency",
                             needs=["Steam"], desc="A foo library"),
            app._tracked_row("libbar", "2-1", "official", kind="dependency",
                             needs=["Plasma Workspace", "Steam Big Picture",
                                    "KDE Connect", "GIMP", "Firefox",
                                    "LibreOffice", "Blender", "Inkscape"]),
            app._tracked_row("libbaz", "3-1", "official", kind="dependency"),
            app._tracked_row("libqux", "4-1", "official", kind="dependency",
                             needs_known=False),
            app._tracked_row("zen", "5-1", "manual", title="Zen",
                             desktop="/x/zen.desktop", kind="app"),
            # A tracked package wears the same line from the other side.
            app._tracked_row("pipewire", "6-1", "official",
                             needs=["plasma-desktop", "firefox"]),
        ])
        v = QListView()
        v.resize(700, 400)
        v.setModel(m)
        d = app.TrackedDelegate(v)
        v.setItemDelegate(d)
        v.show()
        return v

    def _lay(self, view, name):
        m = view.model()
        row = m.row_of(name)
        opt, _idx = _topt(view, row)
        return view.itemDelegate()._geom(row, m.item(row), opt.rect.width())

    def test_the_line_sits_below_the_description(self, deps_view):
        lay = self._lay(deps_view, "libfoo")
        assert lay["req_label_txt"] == "\u21b3 DEPENDENCY FOR:"
        assert lay["desc"].bottom() <= lay["req_label"].top()
        assert lay["req_names"].left() > lay["req_label"].left()

    def test_one_requirer_is_named_and_needs_no_control(self, deps_view):
        lay = self._lay(deps_view, "libfoo")
        assert lay["req_names_txt"] == "Steam"
        assert lay["req_more_txt"] == ""
        assert lay["req_more"].isNull()

    def test_a_long_list_shows_what_fits_and_counts_the_rest(
            self, deps_view):
        lay = self._lay(deps_view, "libbar")
        assert lay["req_names_txt"].startswith("Plasma Workspace")
        assert "+" in lay["req_names_txt"]
        assert "more" in lay["req_names_txt"]
        # Fresh names only: nothing is repeated, nothing is cut mid-word.
        shown = lay["req_names_txt"].split("  +")[0].split(" · ")
        assert shown == ["Plasma Workspace", "Steam Big Picture",
                         "KDE Connect", "GIMP", "Firefox", "LibreOffice",
                         "Blender", "Inkscape"][:len(shown)]
        assert 0 < len(shown) < 8

    def test_the_control_rides_the_line_right_end(self, deps_view):
        lay = self._lay(deps_view, "libbar")
        assert lay["req_more_txt"] == "Show more"
        assert lay["req_more"].right() <= lay["hint"].right() + 1
        assert lay["hint"].left() <= lay["req_more"].left()
        assert lay["req_more"].top() >= lay["hint"].top()

    def test_a_package_nothing_needs_says_so(self, deps_view):
        lay = self._lay(deps_view, "libbaz")
        assert lay["req_note_txt"] == (
            "\u21b3 No explicitly installed package needs it.")
        assert lay["req_label_txt"] == "" and lay["req_more_txt"] == ""

    def test_without_requirer_data_the_row_claims_nothing(self, deps_view):
        lay = self._lay(deps_view, "libqux")
        assert lay["req_label_txt"] == ""
        assert lay["req_names_txt"] == ""
        assert lay["req_note_txt"] == ""
        assert lay["req_more"].isNull()

    def test_app_rows_carry_no_line(self, deps_view):
        lay = self._lay(deps_view, "zen")
        assert lay["req_label_txt"] == ""
        assert lay["req_names_txt"] == ""
        assert lay["req_note_txt"] == ""
        assert lay["req_more"].isNull()

    def test_tracked_rows_ask_the_other_way(self, deps_view):
        # The same relation, read from the side of a package you installed
        # yourself: what would go with it.
        lay = self._lay(deps_view, "pipewire")
        assert lay["req_label_txt"] == "\u21b3 NEEDED BY:"
        assert lay["req_names_txt"] == "plasma-desktop · firefox"

    def test_every_row_is_the_same_height(self, deps_view):
        # The line is part of the row: a row that names eight requirers is
        # exactly as tall as one that names none, so the list can never
        # re-anchor itself as rows are measured (see AGENTS.md).
        heights = {self._lay(deps_view, name)["height"]
                   for name in ("libfoo", "libbar", "libbaz", "libqux",
                                "zen", "pipewire")}
        assert len(heights) == 1

    def test_the_tooltip_lists_every_requirer(self, deps_view):
        m = deps_view.model()
        row = m.row_of("libbar")
        tip = m.data(m.index(row), Qt.ItemDataRole.ToolTipRole)
        assert tip.splitlines()[:2] == ["DEPENDENCY FOR:", "\u2022 Plasma "
                                        "Workspace"]
        assert len(tip.splitlines()) == 9
        pipe = m.row_of("pipewire")
        assert m.data(m.index(pipe), Qt.ItemDataRole.ToolTipRole) \
            .splitlines() == ["NEEDED BY:", "\u2022 plasma-desktop",
                              "\u2022 firefox"]
        assert m.data(m.index(m.row_of("zen")),
                      Qt.ItemDataRole.ToolTipRole) is None

    def test_show_more_opens_the_panel_never_a_taller_row(
            self, deps_view, qapp):
        d = deps_view.itemDelegate()
        m = deps_view.model()
        row = m.row_of("libbar")
        opened, closed = [], []
        d.detail_requested.connect(
            lambda *a: opened.append(a) if a[0] else closed.append(a))
        d.more_requested.connect(d.toggle_detail)
        before = self._lay(deps_view, "libbar")
        d.more_requested.emit(row)
        qapp.processEvents()
        assert opened == [(("libbar"), "\u21b3 DEPENDENCY FOR:",
                           ["Plasma Workspace", "Steam Big Picture",
                            "KDE Connect", "GIMP", "Firefox", "LibreOffice",
                            "Blender", "Inkscape"])]
        # The row says "Show less" instead of "Show more", and is exactly
        # as tall as it was: the list under it does not move.
        lay = self._lay(deps_view, "libbar")
        assert lay["req_more_txt"] == "Show less"
        assert lay["height"] == before["height"]
        d.more_requested.emit(row)
        qapp.processEvents()
        assert closed and self._lay(deps_view, "libbar")["req_more_txt"] \
            == "Show more"

    def test_clicking_show_more_asks_for_the_panel(self, deps_view, qapp):
        d = deps_view.itemDelegate()
        m = deps_view.model()
        row = m.row_of("libbar")
        opt, idx = _topt(deps_view, row)
        lay = d._geom(row, m.item(row), opt.rect.width())
        seen = []
        d.more_requested.connect(seen.append)
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease,
                         QPointF(lay["req_more"].center()
                                 + opt.rect.topLeft()),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, m, opt, idx) is True
        assert seen == [row]

    def test_the_view_keeps_its_scroll_through_a_relayout(self, qapp):
        # The helper reads the offset before the layout pass and puts it
        # back after, so the row the user clicked never moves.
        class _Sb:
            def __init__(self):
                self.v = 131
                self.writes = []

            def value(self):
                return self.v

            def setValue(self, x):  # noqa: N802 (Qt spelling)
                self.writes.append(x)
                self.v = x

        class _Vp:
            def update(self):
                pass

        class _View:
            def __init__(self):
                self.sb = _Sb()

            def verticalScrollBar(self):  # noqa: N802
                return self.sb

            def viewport(self):
                return _Vp()

        class _Model(QObject):
            layoutChanged = pyqtSignal()

        m, view = _Model(), _View()
        seen = []
        m.layoutChanged.connect(lambda: seen.append(view.sb.value()))
        app._relayout_after_row_event(view, m)
        assert seen == [131]
        assert view.sb.value() == 131
        assert view.sb.writes == [131]
