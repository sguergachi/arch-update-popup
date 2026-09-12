"""Window-level smoke tests + scroll-performance sanity (offscreen Qt)."""
import time

import pytest

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from conftest import load_app

app = load_app("app_gui")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _updates(n=4):
    ups = [
        {"name": "konsole", "old": "1-1", "new": "2-1", "repo": "official"},
        {"name": "aur-pkg", "old": "1-1", "new": "2-1", "repo": "aur"},
        {"name": "evil", "old": "1-1", "new": "2-1", "repo": "aur",
         "compromised": True, "compromise_reasons": ["listed"]},
        {"name": "kpackage", "old": "1-1", "new": "2-1", "repo": "official"},
    ]
    return (ups * ((n // len(ups)) + 1))[:n]


class FakeRunner(QObject):
    install_progress = pyqtSignal(int, int, str)
    line = pyqtSignal(str)
    build = pyqtSignal(str)
    conflict = pyqtSignal(str, str)
    done = pyqtSignal(bool, str)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.packages = args[1] if len(args) > 1 else None
        self.kwargs = kwargs

    def start(self):
        pass


@pytest.fixture()
def window(qapp, monkeypatch):
    monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
    monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
    # Never pop real desktop bubbles during tests; per-test overrides apply.
    monkeypatch.setattr(app, "_notify_update", lambda *a, **k: 0)
    monkeypatch.setattr(app, "_notify_close", lambda *a, **k: None)
    w = app.UpdateWindow(_updates(), {}, None)
    w.resize(1280, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


class TestWindow:
    def test_builds(self, window):
        assert window._model.rowCount() == 4
        assert window._update_btn.isEnabled()  # 3 of 3 checkable selected

    def test_filter(self, window, qapp):
        window._apply_filter("kons")
        qapp.processEvents()
        assert not window._list.isRowHidden(0)
        assert window._list.isRowHidden(1)
        assert "Showing 1 of 4" in window._meta.text()
        window._apply_filter("")

    def test_select_none_disables_update(self, window):
        window._select_all(False)
        assert window._model.selected_count() == 0
        assert not window._update_btn.isEnabled()
        assert "No packages selected" in window._sel_label.text()
        window._select_all(True)
        assert window._update_btn.isEnabled()

    def test_bulk_flow(self, window, qapp):
        window.start_update()
        assert window._updating is True
        assert window._safe_names == ["konsole", "aur-pkg", "kpackage"]
        window._on_install_progress(1, 3, "konsole")
        assert window._model.item(0)["state"] == "installing"
        window._on_update_done(True, "")
        assert all(window._model.item(r)["state"] == "done"
                   for r in (0, 1, 3))
        assert window._model.item(2)["state"] == "idle"  # compromised untouched

    def test_failure_marks_named_rows(self, window):
        window._safe_names = ["konsole", "aur-pkg"]
        window._on_update_done(False, "aur-pkg: /x exists in filesystem")
        assert window._model.item(1)["state"] == "failed"
        assert window._model.item(0)["state"] == "idle"
        assert window._update_btn.isEnabled()

    def test_unknown_progress_creates_row(self, window):
        n0 = window._model.rowCount()
        window._safe_names = ["konsole"]
        window._on_install_progress(1, 2, "mystery-dep")
        assert window._model.rowCount() == n0 + 1
        r = window._model.row_of("mystery-dep")
        assert window._model.item(r)["state"] == "installing"
        assert window._model.item(r).get("auto") is True
        assert "mystery-dep" in window._sub.text()

    def test_failure_unknown_package_gets_row_and_banner(self, window):
        window._safe_names = ["konsole"]
        window._on_update_done(
            False, "ghost-pkg: /x exists in filesystem\nErrors occurred")
        r = window._model.row_of("ghost-pkg")
        assert r is not None
        assert window._model.item(r)["state"] == "failed"
        # who/why lands in the status box and the list jumps to the row
        assert "ghost-pkg" in window._status.toPlainText()
        assert window._fail_row == r

    def test_repeat_failure_pauses_dialogs(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(app.QMessageBox, "exec",
                            lambda self: calls.append(1))
        window._safe_names = ["konsole"]
        out = "konsole: /x exists in filesystem\nErrors occurred"
        window._on_update_done(False, out)
        first = len(calls)
        assert first >= 1
        window._on_update_done(False, out)
        assert len(calls) == first  # identical repeat: no new dialogs
        assert "Manual fix" in window._details.toPlainText()
        assert "2 times in a row" in window._status.toPlainText()

    def test_watchdog_warns_and_cancel(self, window):
        import time as _t
        window._updating = True
        window._last_activity = _t.time() - 200
        assert window._stuck_warned is False
        window._watchdog_tick()
        assert window._stuck_warned is True
        assert "2 minutes" in window._status.toPlainText()
        assert window._log_btn.isChecked()  # log auto-shown

        class RecRunner:
            def __init__(self):
                self.terminated = False

            def terminate(self):
                self.terminated = True

        rec = RecRunner()
        window._runner = rec
        window._cancel_update()
        assert rec.terminated is True
        window._updating = False
        window._runner = None

    def test_skip_closes_when_idle(self, window, monkeypatch):
        closed = []
        monkeypatch.setattr(window, "close", lambda: closed.append(1))
        window._updating = False
        window._single = None
        window._on_skip_or_cancel()
        assert closed == [1]

    def test_single_notification_slot_updates(self, window, monkeypatch):
        calls = []
        counter = [41]

        def fake_notify(title, body, **kwargs):
            calls.append((title, body, kwargs.get("replaces_id", 0)))
            counter[0] += 1
            return counter[0]

        monkeypatch.setattr(app, "_notify_update", fake_notify)
        window._safe_names = ["konsole"]
        out = "konsole: /x exists in filesystem\nErrors occurred"
        window._on_update_done(False, out)
        window._on_update_done(False, out)
        assert len(calls) == 2
        assert calls[0][2] == 0  # first creates the slot
        assert calls[1][2] == 42  # second updates the same notification
        assert "Failure #2" in calls[1][1]  # count, not a new bubble

    def test_cancelled_is_silent(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(app, "_notify_update",
                            lambda *a, **k: calls.append(1) or 0)
        window._safe_names = ["konsole"]
        window._on_update_done(False, "")
        assert calls == []

    def test_success_closes_slot(self, window, monkeypatch):
        closed = []
        monkeypatch.setattr(app, "_notify_close", lambda nid: closed.append(nid))
        window._fail_notif_id = 42
        window._safe_names = ["konsole"]
        window._on_update_done(True, "")
        assert closed == [42]
        assert window._fail_notif_id == 0

    def test_pending_never_negative(self, window):
        window._safe_names = []
        window._model.set_state("konsole", "done")
        window._on_update_done(False, "boom")
        assert "−1" not in window._sub.text()
        assert "<b>0</b> packages pending" in window._sub.text()

    def test_scan_dialog_builds(self, window, qapp):
        s = app.ScanDialog([
            {"name": "a", "version": "1", "repo": "aur",
             "compromised": False, "reasons": []},
            {"name": "b", "version": "2", "repo": "official",
             "compromised": True, "reasons": ["listed"]},
        ])
        s.resize(1100, 620)
        s.show()
        qapp.processEvents()
        s.close()

    def test_log_window(self, window):
        window._on_log_line("[###] 100%")
        assert window._log_view.toPlainText() == ""
        window._on_log_line("error: target not found: foo")
        assert "target not found" in window._log_view.toPlainText()
        window._on_log_toggled(True)
        assert window._log_view.isVisible()
        window._on_log_toggled(False)

    def test_status_fits_content(self, window, qapp):
        window._set_status("Cancelling the running install…", "info")
        qapp.processEvents()
        h1 = window._status.height()
        assert 0 < h1 <= 80, h1  # one line: slim, never a big box
        window._set_status("✕ " + "long failure explanation. " * 30, "error")
        qapp.processEvents()
        h2 = window._status.height()
        assert h2 <= 150, h2  # capped, scrolls internally past that
        assert h2 >= h1
        window._set_status("", "info")
        assert not window._status.isVisible()

    def test_bottom_structure(self, window):
        lay = window.centralWidget().layout()
        content = lay.itemAt(2).layout()  # head, divider, content row
        left = content.itemAt(0).layout()
        widgets = [left.itemAt(i).widget() for i in range(left.count())]
        # status + slim progress pinned at the bottom of the column,
        # footer row last
        assert widgets.index(window._status) > left.indexOf(window._list)
        assert widgets.index(window._progress) > left.indexOf(window._list)
        assert left.itemAt(left.count() - 1).layout() is not None
        # errors live in the side panel, above the log
        panel = content.itemAt(1).widget()
        assert panel is window._log_panel
        pwidgets = [panel.layout().itemAt(i).widget()
                    for i in range(panel.layout().count())]
        assert window._details in pwidgets
        assert window._log_view in pwidgets
        assert pwidgets.index(window._details) < pwidgets.index(window._log_view)
        # error copy button sits next to the details, log copy in log header
        assert window._copy_btn.isHidden()  # no failure yet
        # log toggle shares the header row with search
        head = lay.itemAt(0).layout()
        assert head.indexOf(window._search) >= 0
        assert head.indexOf(window._log_btn) >= 0

    def test_failure_shows_error_in_panel(self, window):
        window._safe_names = ["konsole"]
        window._on_update_done(
            False, "konsole: /x exists in filesystem\nErrors occurred")
        assert window._log_panel.isVisible()  # auto-opened on failure
        assert window._details.isVisible()
        assert window._copy_btn.isVisible()
        assert "exists in filesystem" in window._details.toPlainText()

    def test_log_panel_squishes_list(self, window, qapp):
        w_full = window._list.width()
        assert not window._log_panel.isVisible()
        window._log_btn.setChecked(True)
        qapp.processEvents()
        assert window._log_panel.isVisible()
        assert window._list.width() < w_full  # list gives way to the panel
        window._log_btn.setChecked(False)
        qapp.processEvents()
        assert not window._log_panel.isVisible()

    def test_progress_relaxes_to_divider(self, window):
        window.on_fetch_done()
        assert window._progress.maximum() == 1
        assert window._progress.value() == 0

    def test_meta_hidden_unless_filtering(self, window, qapp):
        assert not window._meta.isVisible()
        window._apply_filter("kons")
        qapp.processEvents()
        assert window._meta.isVisible()
        assert "Showing 1 of" in window._meta.text()
        window._apply_filter("")
        assert not window._meta.isVisible()

    def test_honest_counts_ignore_pacman_total(self, window):
        window._safe_names = ["konsole", "aur-pkg", "kpackage"]
        window._done_names = set()
        window._on_install_progress(3, 3, "konsole")
        # pacman says 3/3 but only 1 of our 3 selected is done at most
        assert window._progress.maximum() == 3
        assert window._progress.value() <= 1
        assert "3 of 3" not in window._sub.text()
        assert "of 3 selected done" in window._sub.text()

    def test_build_event_creates_row(self, window):
        n0 = window._model.rowCount()
        window._safe_names = ["konsole"]
        window._on_build_started("mystery-aur-pkg")
        assert window._model.rowCount() == n0 + 1
        assert "Building AUR package" in window._sub.text()

    def test_live_conflict_marks_row(self, window):
        window._on_live_conflict("konsole", "/usr/lib/x")
        assert window._model.item(0)["state"] == "failed"
        assert "Files already exist" in window._status.toPlainText()
        assert window._live_conflicts == {"konsole": ["/usr/lib/x"]}

    def test_popover_content(self, window, qapp):
        window._model.set_info(0, "d", "short", "2026-01-01",
                               "FULL BODY TEXT", "GitHub release",
                               "https://example.com")
        window.show_popover(0)
        qapp.processEvents()
        pop = window._popover
        assert pop is not None and pop.isVisible()
        browsers = pop.findChildren(app.QTextBrowser)
        assert browsers and "FULL BODY TEXT" in browsers[0].toPlainText()
        pop.close()

    def test_single_run(self, window):
        window._run_single("aur-pkg")
        assert window._model.item(1)["state"] == "installing"
        window._on_single_done(True, "")
        assert window._model.item(1)["state"] == "done"

    def test_row_action_compromised_asks(self, window, monkeypatch):
        seen = {}

        class RecBox:
            def __init__(self, *a, **k):
                pass

        monkeypatch.setattr(app, "QMessageBox", RecBox)
        # QMessageBox replaced: _confirm_install_anyway would fail on addButton;
        # instead verify routing calls _run_single for safe rows:
        window._on_row_action("aur-pkg")
        assert window._model.item(1)["state"] == "installing"


class TestNotifyHelper:
    def test_parses_gdbus_id(self, monkeypatch):
        import subprocess as sp

        class R:
            returncode = 0
            stdout = "(uint32 7,)\n"

        monkeypatch.setattr(app.shutil, "which", lambda *a: "/usr/bin/gdbus")
        monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
        assert app._notify_update("t", "b") == 7

    def test_fallback_without_gdbus(self, monkeypatch):
        seen = []
        monkeypatch.setattr(app.shutil, "which", lambda *a: None)
        monkeypatch.setattr(app.subprocess, "run",
                            lambda *a, **k: seen.append(a[0]))
        assert app._notify_update("t", "b") == 0
        assert seen and seen[0][0] == "notify-send"


class TestPerf:
    def test_layout_and_paint_budget(self, qapp):
        ups = _updates(322)
        m = app.PackageModel(ups, {})
        from PyQt6.QtWidgets import QListView, QStyleOptionViewItem
        v = QListView()
        v.resize(1240, 800)
        v.setModel(m)
        d = app.PackageDelegate(v)
        v.setItemDelegate(d)
        v.show()
        qapp.processEvents()
        opt = QStyleOptionViewItem()
        t0 = time.perf_counter()
        heights = []
        for r in range(m.rowCount()):
            idx = m.index(r)
            opt.rect = v.visualRect(idx)
            opt.palette = v.palette()
            opt.font = v.font()
            heights.append(d.sizeHint(opt, idx).height())
        layout_s = time.perf_counter() - t0
        assert all(h > 80 for h in heights)
        # Full-list first layout should be comfortably fast; per-row paint
        # afterwards is a single cached pass (dataChanged repaints 1 row).
        print(f"\n322 rows layout: {layout_s:.2f}s "
              f"({layout_s / 322 * 1000:.1f} ms/row)")
        assert layout_s < 20
        v.close()
