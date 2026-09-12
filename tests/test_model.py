"""Model + delegate tests (offscreen Qt)."""
import pytest

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
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
        assert 100 < h < 1500, h

    def test_layout_inside_card(self, view):
        d = view.itemDelegate()
        opt, idx = _option(view, 0)
        lay = d._layout(0, view.model().item(0), opt.rect.width())
        card = lay["card"]
        for key in ("check", "name", "ver", "desc", "notes", "link"):
            r = lay[key]
            assert card.left() <= r.left() and r.right() <= card.right(), key

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
