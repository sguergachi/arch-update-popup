"""Model + delegate tests (offscreen Qt)."""
import pytest

from PyQt6.QtCore import QEvent, QPointF, Qt
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

    def test_more_hit_opens_popover_signal(self, view, qapp):
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
        m.set_packages(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"},
             {"name": "a-off", "version": "2-1", "repo": "official"},
             {"name": "c-aur", "version": "3-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"}])
        return m

    def test_sort_movable_first(self):
        m = self._model()
        assert m.rowCount() == 3
        assert m.item(0)["name"] == "b-aur"
        assert [m.item(r)["name"] for r in (1, 2)] == ["a-off", "c-aur"]

    def test_movable_names(self):
        assert self._model().movable_names() == ["b-aur"]

    def test_state_signal(self):
        m = self._model()
        seen = []
        m.dataChanged.connect(lambda *a: seen.append(a))
        m.set_state("b-aur", "switching")
        assert len(seen) == 1
        assert m.item(0)["state"] == "switching"
        m.set_state("nope", "done")  # unknown: silent no-op
        assert len(seen) == 1

    def test_display_role(self):
        m = self._model()
        assert m.data(m.index(0)) == "b-aur"


@pytest.fixture()
def tracked_view(qapp):
    m = app.TrackedModel()
    m.set_packages(
        [{"name": "b-aur", "version": "1-1", "repo": "aur"},
         {"name": "a-off", "version": "2-1", "repo": "official"}],
        [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
          "relation": "upgrade"}])
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

    def test_action_only_when_movable(self, tracked_view):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, tracked_view.model().item(0), opt.rect.width())
        assert lay["action_txt"] == "Switch"
        assert not lay["action"].isNull()
        assert lay["action2_txt"] == "Uninstall"
        assert not lay["action2"].isNull()
        opt1, _ = _topt(tracked_view, 1)
        lay1 = d._geom(1, tracked_view.model().item(1), opt1.rect.width())
        assert lay1["action"].isNull()
        assert not lay1["action2"].isNull()  # uninstall always offered
        assert lay1["action2_txt"] == "Uninstall"

    def test_uninstall_hit(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 1)  # non-movable row
        lay = d._geom(1, tracked_view.model().item(1), opt.rect.width())
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
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert lay["action2_txt"] == "Retry"
        assert lay["action_txt"] == "Switch"  # switch still available
        m.set_state("b-aur", "failed", op="switch")
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert lay["action_txt"] == "Retry"
        assert lay["action2_txt"] == "Uninstall"

    def test_remove_row(self):
        m = app.TrackedModel()
        m.set_packages(
            [{"name": "a", "version": "1", "repo": "aur"},
             {"name": "b", "version": "1", "repo": "official"}],
            [])
        assert m.rowCount() == 2
        m.remove_row("a")
        assert m.rowCount() == 1 and m.item(0)["name"] == "b"
        m.remove_row("nope")  # silent no-op
        assert m.rowCount() == 1

    def test_switch_hit(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, tracked_view.model().item(0), opt.rect.width())
        got = []
        d.switch_requested.connect(got.append)
        c = lay["action"].center() + opt.rect.topLeft()
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        assert d.editorEvent(ev, tracked_view.model(), opt, idx) is True
        assert got == ["b-aur"]

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
        assert m.checked_names() == ["b-aur"]
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

    def test_pick_desktop_file(self, tmp_path):
        exact = tmp_path / "foo.desktop"
        exact.write_text("[Desktop Entry]\nName=Foo\nExec=foo\n")
        hidden = tmp_path / "zzz.desktop"
        hidden.write_text("[Desktop Entry]\nName=Z\nNoDisplay=true\nExec=z\n")
        paths = [str(hidden), str(exact)]
        assert app.pick_desktop_file("foo", paths) == str(exact)
        assert app.pick_desktop_file("other", paths) == str(exact)  # fallback
        assert app.pick_desktop_file("zzz", [str(hidden)]) == ""
        assert app.pick_desktop_file("foo", []) == ""
        term = tmp_path / "term.desktop"
        term.write_text("[Desktop Entry]\nName=T\nTerminal=true\nExec=t\n")
        assert app.pick_desktop_file("term", [str(term)]) == ""

    def test_set_desktop(self, tracked_view, qapp):
        m = tracked_view.model()
        assert m.item(0)["desktop"] is None
        seen = []
        m.dataChanged.connect(lambda *a: seen.append(a))
        m.set_desktop("b-aur", "/x/b-aur.desktop")
        assert m.item(0)["desktop"] == "/x/b-aur.desktop"
        assert len(seen) == 1
        m.set_desktop("nope", "/x.desktop")  # unknown: silent no-op
        assert len(seen) == 1

    def test_open_pill_only_with_launcher(self, tracked_view):
        m = tracked_view.model()
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert lay["open"].isNull()
        m.set_desktop("b-aur", "/x/b-aur.desktop")
        lay = d._geom(0, m.item(0), opt.rect.width())
        assert not lay["open"].isNull()
        assert lay["open_txt"] == "Open"
        # Pills share the bottom baseline inside the card, below the
        # top-right link row.
        card = lay["card"]
        for k in ("action", "action2", "open"):
            assert lay[k].top() > lay["link"].bottom()
            assert lay[k].bottom() <= card.bottom() - app.TrackedDelegate.PBOT

    def test_open_hit(self, tracked_view, qapp):
        m = tracked_view.model()
        m.set_desktop("b-aur", "/x/b-aur.desktop")
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, m.item(0), opt.rect.width())
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
        # Version line splits installed → official for movable rows.
        assert lay["ver_left"].startswith("1-1")
        # The target names its source: an official repo reads as
        # "official", a third-party one by name.
        assert lay["ver_right"] == "official 1.1-1"
        assert "switch for signed builds" in lay["hint_txt"]
        # Non-movable official row: no label at all.
        m = tracked_view.model()
        m.set_packages([{"name": "x", "version": "1", "repo": "extra"}], [])
        layx = d._geom(0, m.item(0), opt.rect.width())
        assert layx["hint_txt"] == ""
        assert layx["ver_right"] == ""
        # Descriptions land via set_desc with a single-row repaint.
        seen = []
        m.dataChanged.connect(lambda *a: seen.append(a))
        m.set_desc("x", "Terminal emulator")
        assert m.item(0)["desc"] == "Terminal emulator"
        assert len(seen) == 1

    def test_pills_bottom_right(self, tracked_view):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        m = tracked_view.model()
        m.set_desktop("b-aur", "/x/b-aur.desktop")
        lay = d._geom(0, m.item(0), opt.rect.width())
        card = lay["card"]
        # All pills share the bottom baseline, right-aligned inside the
        # card: Switch rightmost, then Uninstall, then Open.
        for k in ("action", "action2", "open"):
            assert not lay[k].isNull()
            assert card.top() < lay[k].top()
            assert lay[k].bottom() <= card.bottom() - app.TrackedDelegate.PBOT
            assert lay[k].right() <= card.right() - app.TrackedDelegate.PAD
        assert lay["action"].right() >= lay["action2"].right() >= lay["open"].right()
        assert lay["action"].left() > lay["action2"].left() > lay["open"].left()
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
        assert got == ["https://aur.archlinux.org/packages/b-aur"]

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

    def test_loader_uses_partial_qo_output(self, qapp, monkeypatch):
        """pacman -Qo exits nonzero over unowned files but still prints
        the owned lines — the loader must use them, not discard."""
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
            if cmd == ["pacman", "-Q"]:
                return R(0, "foo 1-1\n")
            if cmd == ["pacman", "-Qm"]:
                return R(0, "")
            if cmd == ["pacman", "-Qd"]:
                return R(0, "dep 2-1\n")
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": "/a.desktop", "title": "A", "comment": "",
             "exec": "a"},
            {"file": "/b.desktop", "title": "B", "comment": "",
             "exec": "b"}])
        got = []
        loader = app.UntrackedLoader()
        loader.done.connect(lambda a, p: got.append((a, p)))
        loader.run()
        apps, pkgs = got[0]
        assert [(r["title"], r["name"], r["version"]) for r in apps] == [
            ("A", "foo", "1-1"), ("B", "b", "")]
        assert [(r["name"], r["version"]) for r in pkgs] == [("dep", "2-1")]

    def test_loader_dedupes_same_app(self, qapp, monkeypatch):
        class R:
            def __init__(self, rc, out):
                self.returncode = rc
                self.stdout = out

        def fake_run(cmd, **k):
            if cmd[:2] == ["pacman", "-Qo"]:
                return R(0, "\n".join(
                    f"{f} is owned by foo 1-1" for f in cmd[2:]))
            if cmd == ["pacman", "-Q"]:
                return R(0, "foo 1-1\n")
            if cmd == ["pacman", "-Qm"]:
                return R(0, "")
            if cmd == ["pacman", "-Qd"]:
                return R(0, "")
            return R(1, "")

        monkeypatch.setattr(app, "_run", fake_run)
        # System + user copy of the same app: one row.
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [
            {"file": "/u/app.desktop", "title": "App", "comment": "",
             "exec": "app"},
            {"file": "/s/app.desktop", "title": "App", "comment": "",
             "exec": "app"}])
        got = []
        loader = app.UntrackedLoader()
        loader.done.connect(lambda a, p: got.append((a, p)))
        loader.run()
        assert len(got[0][0]) == 1
        assert got[0][0][0]["name"] == "foo"

    def test_tracked_row_complete(self):
        row = app._tracked_row("p", "1-1", "official", title="T")
        assert row == {"name": "p", "title": "T", "version": "1-1",
                       "repo": "official", "target": "", "source": "",
                       "relation": "", "state": "idle", "failed_op": "",
                       "desktop": None, "desc": "", "checked": False,
                       # The app's own page (AppImages / manual installs)
                       # and the file behind an AppImage row.
                       "url": "", "path": ""}

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
