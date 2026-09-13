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
        # What's-new row shares one baseline: notes, more, link aligned
        assert lay["notes"].top() == lay["link"].top()
        if not lay["more"].isNull():
            assert lay["more"].top() == lay["notes"].top()

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


class TestTrackedModel:
    def _model(self):
        m = app.TrackedModel()
        m.set_packages(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"},
             {"name": "a-off", "version": "2-1", "repo": "official"},
             {"name": "c-aur", "version": "3-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "official": "1.1-1",
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
        [{"name": "b-aur", "installed": "1-1", "official": "1.1-1",
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
        assert tracked_view.itemDelegate().sizeHint(opt, idx).height() == 52

    def test_action_only_when_movable(self, tracked_view):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        lay = d._geom(0, tracked_view.model().item(0), opt.rect.width())
        assert lay["action_txt"] == "Switch"
        assert not lay["action"].isNull()
        opt1, _ = _topt(tracked_view, 1)
        lay1 = d._geom(1, tracked_view.model().item(1), opt1.rect.width())
        assert lay1["action"].isNull()

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

    def test_paint_ok(self, tracked_view, qapp):
        d = tracked_view.itemDelegate()
        opt, idx = _topt(tracked_view, 0)
        pm = QPixmap(900, 100)
        p = QPainter(pm)
        d.paint(p, opt, idx)
        p.end()
        qapp.processEvents()
