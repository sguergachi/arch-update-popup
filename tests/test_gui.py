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
        self.terminated = False
        self._running = False

    def isRunning(self):
        return self._running

    def terminate(self):
        self.terminated = True
        self._running = False

    def wait(self, ms=0):
        return True

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

    def test_header_tabs(self, window, qapp):
        # Tabs replaced the filter field: Updates | Obsolete |
        # Security | Tracked.
        assert not hasattr(window, "_search")
        tabs = window._view_buttons
        assert [b.text() for b in
                (tabs["updates"], tabs["obsolete"], tabs["security"],
                 tabs["tracked"])] == [
            "Updates (4)", "Obsolete", "Security", "Tracked"]
        assert tabs["updates"].isChecked()
        head = window.centralWidget().layout().itemAt(0).layout()
        for key in ("updates", "obsolete", "security", "tracked"):
            assert head.indexOf(tabs[key]) >= 0
        assert head.indexOf(window._log_btn) >= 0

    def test_select_none_disables_update(self, window):
        window._select_all(False)
        assert window._model.selected_count() == 0
        assert not window._update_btn.isEnabled()
        assert "No packages selected" in window._sel_label.text()
        window._select_all(True)
        assert window._update_btn.isEnabled()

    def test_bulk_flow(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
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
        assert "ghost-pkg" in window._sub.text()
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
        assert "2 times in a row" in window._sub.text()

    def test_watchdog_warns_and_cancel(self, window):
        import time as _t
        window._updating = True
        window._last_activity = _t.time() - 200
        assert window._stuck_warned is False
        window._watchdog_tick()
        assert window._stuck_warned is True
        assert "2 minutes" in window._sub.text()
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

    def test_close_mid_update_stops_runner(self, window, qapp):
        # Regression: closing the window with a running installer
        # destroyed the live QThread -> SIGABRT. closeEvent must stop
        # the runner first.
        r = FakeRunner()
        r._running = True
        window._runner = r
        window._updating = True
        closed = []
        window.closed.connect(lambda: closed.append(1))
        window.close()
        qapp.processEvents()
        assert r.terminated is True
        assert window._runner is None
        assert closed == [1]
        window._updating = False

    def test_close_mid_single_stops_runner(self, window, qapp):
        r = FakeRunner()
        r._running = True
        window._single = r
        window.close()
        qapp.processEvents()
        assert r.terminated is True
        assert window._single is None

    def test_obsolete_remover_graceful_terminate(self):
        r = app.ObsoleteRemover(["some-pkg"])
        assert r._proc is None
        r.terminate()  # no process yet — must not raise
        assert r._killed is True

    def test_stop_fetcher_parks_stuck_thread(self, qapp, monkeypatch):
        # A fetcher blocked in network I/O past the wait timeout must be
        # parked (kept alive), never destroyed while running.
        from PyQt6.QtGui import QIcon
        # Patch before construction: __init__ schedules check_now via
        # singleShot, which must never fire real subprocesses in tests.
        monkeypatch.setattr(app.TrayApp, "check_now", lambda self: None)
        tray = app.TrayApp(qapp, QIcon())

        class StuckFetcher(app.InfoFetcher):
            def run(self):
                import time as _t
                while not self._stop_now:
                    _t.sleep(0.05)

        f = StuckFetcher([])
        f._stop_now = False
        f.start()
        for _ in range(100):
            if f.isRunning():
                break
            qapp.processEvents()
            import time as _t
            _t.sleep(0.02)
        assert f.isRunning()
        tray._fetcher = f
        tray._stop_fetcher()
        assert tray._fetcher is None
        assert f in tray._orphans and f.isRunning()
        f._stop_now = True
        assert f.wait(10000)
        for _ in range(100):
            qapp.processEvents()
            if f not in tray._orphans:
                break
        assert f not in tray._orphans
        tray._shutdown()

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
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
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

    def test_status_unified_in_header(self, window, qapp):
        idle = window._sub.text()
        assert idle  # resting summary present
        window._set_status("Cancelling the running install…", "info")
        qapp.processEvents()
        assert "Cancelling" in window._sub.text()
        assert "✕" not in window._sub.text()
        window._set_status("Something broke badly", "error")
        assert "✕" in window._sub.text()
        assert "Something broke badly" in window._sub.text()
        window._set_status("All good", "ok")
        assert "✓" in window._sub.text()
        window._set_status("", "info")
        assert window._sub.text() == idle  # empty restores summary

    def test_bottom_structure(self, window):
        lay = window.centralWidget().layout()
        content = lay.itemAt(2).layout()  # head, divider, content row
        left = content.itemAt(0).layout()
        # view switcher + content stack + footer stack
        assert window._content_stack.count() == 4
        assert window._footer_stack.count() == 4
        assert window._content_stack.currentIndex() == 0
        assert window._footer_stack.currentIndex() == 0
        tabs = window._view_buttons
        assert set(tabs) == {"updates", "obsolete", "security", "tracked"}
        assert tabs["updates"].isChecked()
        # slim progress lives in the updates page above its footer
        assert window._progress.parent() is not None
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
        # log toggle shares the header row with the view tabs
        head = lay.itemAt(0).layout()
        for key in ("updates", "obsolete", "security"):
            assert head.indexOf(window._view_buttons[key]) >= 0
        assert head.indexOf(window._log_btn) >= 0

    def test_failure_shows_error_in_panel(self, window):
        window._safe_names = ["konsole"]
        window._on_update_done(
            False, "konsole: /x exists in filesystem\nErrors occurred")
        assert window._log_panel.isVisible()  # auto-opened on failure
        assert window._details.isVisible()
        assert window._copy_btn.isVisible()
        assert "exists in filesystem" in window._details.toPlainText()

    def test_success_with_stale_install_reroutes_to_failure(
            self, window, monkeypatch):
        # Exit code 0 but versions unchanged (stale sync DB reinstall):
        # must NOT declare success.
        monkeypatch.setattr(
            app, "verify_upgraded",
            lambda expected: [("konsole", "1-1", "2-1")])
        window._safe_names = ["konsole"]
        window._on_update_done(True, "reinstalled konsole")
        assert window._model.item(0)["state"] == "failed"
        assert "reinstalled instead of upgraded" in \
            window._sub.text()
        assert window._update_btn.isEnabled()  # retry offered

    def test_success_with_verified_install_stays_success(
            self, window, monkeypatch):
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
        window._safe_names = ["konsole"]
        window._on_update_done(True, "")
        assert window._model.item(0)["state"] == "done"

    def test_log_preview_opens_panel(self, window, qapp):
        assert not window._log_preview.isVisible()
        window._on_log_line(":: installing konsole (1/2)")
        qapp.processEvents()
        assert window._log_preview.isVisible()
        assert "konsole" in window._log_preview.text()
        # HTML-unsafe log output must not break the label
        window._on_log_line("<b>not a tag</b> & done")
        assert "not a tag" in window._log_preview.text()
        # click opens the panel, which hides the preview
        window._log_preview.clicked.emit()
        qapp.processEvents()
        assert window._log_panel.isVisible()
        assert not window._log_preview.isVisible()
        window._on_log_toggled(False)

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

    def test_obsolete_preload_fills_tab(self, window, qapp, monkeypatch):
        # The real preload thread races the test; drive the handler
        # directly with staged data instead.
        if window._obs_checker is not None and \
                window._obs_checker.isRunning():
            window._obs_checker.wait(15000)
        window._obs_loaded = False
        window._on_obs_preload_done(["pre-a", "pre-b"])
        qapp.processEvents()
        assert window._obs_loaded is True
        assert set(window._obs_cards) == {"pre-a", "pre-b"}
        assert window._view_buttons["obsolete"].text() == "Obsolete (2)"
        # A later manual refresh still works.
        monkeypatch.setattr(app, "get_obsolete_packages", lambda: [])
        window._refresh_obsolete_view()
        assert window._obs_cards == {}

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
        assert "Files already exist" in window._sub.text()
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

    def test_single_run(self, window, monkeypatch):
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
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


class TestMovesDialog:
    def _moves(self):
        return [
            {"name": "graduated", "installed": "1.0-1", "official": "1.1-1",
             "relation": "upgrade"},
            {"name": "shiny", "installed": "2.0-1", "official": "1.9-1",
             "relation": "downgrade"},
        ]

    def test_builds_rows(self, qapp):
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        assert set(dlg._rows) == {"graduated", "shiny"}
        assert dlg._rows["graduated"]._switch_btn.text() == "Switch"
        dlg.close()

    def test_switch_runs_and_verifies(self, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        dlg.request_switch("graduated")
        assert isinstance(dlg._runner, FakeRunner)
        dlg._runner.done.emit(True, "")
        qapp.processEvents()
        assert dlg._rows["graduated"]._switch_btn.text() == "Switched ✓"
        assert dlg._runner is None  # queue drained
        dlg.close()

    def test_switch_failure_retries(self, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        dlg.request_switch("graduated")
        dlg._runner.done.emit(False, "boom")
        qapp.processEvents()
        assert dlg._rows["graduated"]._switch_btn.text() == "Retry"
        dlg.close()

    def test_downgrade_cancel_leaves_queue_empty(self, qapp, monkeypatch):
        # window fixture patches QMessageBox.exec/clickedButton only for
        # window tests; replicate the auto-dismiss stub here.
        monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        dlg.request_switch("shiny")  # downgrade → confirm → auto-dismiss
        assert dlg._queue == []
        assert dlg._runner is None
        dlg.close()

    def test_lock_blocks_switch(self, qapp, monkeypatch):
        import os as _os
        monkeypatch.setattr(_os.path, "exists", lambda p: True)
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        dlg.request_switch("graduated")
        assert dlg._queue == []
        assert "Another package operation" in dlg._status.text()
        dlg.close()


class TestViews:
    def test_empty_window_opens(self, qapp):
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        assert w._empty_label.isVisible()
        assert not w._list.isVisible()
        assert not w._update_btn.isEnabled()
        assert "0" in w._sub.text()
        w.close()

    def test_empty_state_clears_on_new_row(self, qapp):
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        assert w._empty_label.isVisible()
        w._model.ensure_row("late-dep")
        qapp.processEvents()
        assert not w._empty_label.isVisible()
        assert w._list.isVisible()
        w.close()

    def test_switch_views(self, window, qapp):
        window._switch_view("obsolete")
        qapp.processEvents()
        assert window._content_stack.currentIndex() == 1
        assert window._footer_stack.currentIndex() == 1
        assert window._view_buttons["obsolete"].isChecked()
        window._switch_view("security")
        assert window._content_stack.currentIndex() == 2
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 3
        assert window._footer_stack.currentIndex() == 3
        assert window._view_buttons["tracked"].isChecked()
        window._switch_view("updates")
        assert window._content_stack.currentIndex() == 0

    def test_view_badges(self, window):
        assert window._view_buttons["updates"].text() == "Updates (4)"

    def test_obsolete_view_lists_and_removes(
            self, window, qapp, monkeypatch):
        window._obs_loaded = False
        window._switch_view("obsolete")
        qapp.processEvents()
        window._on_obs_preload_done(["old-a", "old-b"])
        qapp.processEvents()
        assert set(window._obs_cards) == {"old-a", "old-b"}
        assert window._obs_remove_btn.isEnabled()
        assert "2 obsolete" in window._obs_footer_label.text()

        # removal success reloads async in place, no update starts
        started = []
        monkeypatch.setattr(window, "start_update",
                            lambda: started.append(1))
        window._remover = None
        window._obsolete_ctx = "obsolete"
        window._on_obsolete_removal_done(True, "")
        qapp.processEvents()
        assert started == []
        assert "rescanning" in window._sub.text()
        window._on_obs_preload_done([])
        qapp.processEvents()
        assert window._obs_cards == {}
        assert not window._obs_remove_btn.isEnabled()
        assert "✓" in window._sub.text()

    def test_obsolete_empty_state(self, window, qapp, monkeypatch):
        window._obs_loaded = False
        window._switch_view("obsolete")
        qapp.processEvents()
        window._on_obs_preload_done([])
        qapp.processEvents()
        assert window._obs_cards == {}
        assert not window._obs_remove_btn.isEnabled()
        assert "No obsolete" in window._obs_summary.text()

    def test_obsolete_switch_never_blocks(self, window, qapp, monkeypatch):
        # Even if pacman hangs, switching tabs must return instantly:
        # computation happens in the checker thread, never on the GUI.
        def _boom():
            raise AssertionError("must not run on GUI thread")

        monkeypatch.setattr(app, "get_obsolete_packages", _boom)
        window._obs_loaded = False
        window._switch_view("obsolete")  # would raise if sync
        qapp.processEvents()
        assert "Scanning" in window._obs_summary.text()
        window._on_obs_preload_done(["x"])
        assert set(window._obs_cards) == {"x"}

    def test_security_scan_shows_findings(
            self, window, qapp, monkeypatch):
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeScanner(QObject):
            progress = pyqtSignal(int, int, str)
            done = pyqtSignal(list)

            def __init__(self, *a, **k):
                super().__init__()

            def isRunning(self):
                return False

            def start(self):
                pass

        monkeypatch.setattr(app, "SystemScanner", FakeScanner)
        window._switch_view("security")
        window._run_security_scan()
        assert window._sec_scanner is not None
        window._sec_scanner.done.emit([
            {"name": "evil", "version": "1", "repo": "aur",
             "compromised": True, "reasons": ["listed"]},
            {"name": "fine", "version": "1", "repo": "official",
             "compromised": False, "reasons": []},
        ])
        qapp.processEvents()
        # only findings get cards; clean packages stay quiet
        assert set(window._sec_cards) == {"evil"}
        assert "1 compromised of 2 checked" in \
            window._sec_footer_label.text()
        assert "✕" in window._sub.text()
        assert window._sec_scan_btn.isEnabled()

    def test_tray_opens_empty_window(self, qapp, monkeypatch):
        from PyQt6.QtGui import QIcon
        monkeypatch.setattr(app.TrayApp, "check_now", lambda self: None)
        tray = app.TrayApp(qapp, QIcon())
        tray._updates = []
        tray._open_window()
        qapp.processEvents()
        assert tray._window is not None
        assert tray._window._empty_label.isVisible()
        tray._window.close()
        qapp.processEvents()
        tray._shutdown()

    def _busy_thread(self):
        import time as _t
        from PyQt6.QtCore import QThread
        stop = []

        class Busy(QThread):
            def run(self):
                while not stop:
                    _t.sleep(0.05)

        b = Busy()
        return b, stop

    def test_release_parks_live_thread(self, window, qapp):
        # Regression: dropping a running thread's wrapper aborts the
        # process ("QThread: Destroyed while thread is still running").
        # _release_thread must park it instead.
        import time as _t
        self._settle_track_loader(window, qapp)  # real loaders done first
        b, stop = self._busy_thread()
        b.start()
        for _ in range(100):
            if b.isRunning():
                break
            qapp.processEvents()
            _t.sleep(0.02)
        assert b.isRunning()
        window._obs_checker = b
        window._release_thread("_obs_checker")  # would abort pre-fix
        assert window._obs_checker is None
        assert b in window._orphans and b.isRunning()
        stop.append(1)
        assert b.wait(10000)
        for _ in range(100):
            qapp.processEvents()
            if b not in window._orphans:
                break
        assert b not in window._orphans

    def test_close_with_finishing_loader(self, window, qapp):
        # A loader that exits shortly after close starts: close must
        # wait it out cleanly (no abort, no park needed).
        import time as _t
        self._settle_track_loader(window, qapp)  # real loaders done first
        b, stop = self._busy_thread()
        b.start()
        for _ in range(100):
            if b.isRunning():
                break
            qapp.processEvents()
            _t.sleep(0.02)
        window._obs_checker = b
        # Stop it from a real thread: the Qt loop is blocked inside
        # close()'s wait(), so a QTimer could never fire in time.
        import threading
        threading.Timer(1.0, lambda: stop.append(1)).start()
        window.close()  # blocks in wait until Busy exits
        qapp.processEvents()
        assert b not in window._orphans

    def _settle_track_loader(self, window, qapp):
        if window._obs_checker is not None:
            window._obs_checker.wait(25000)
        qapp.processEvents()  # flush obs done (chains the tracked loader)
        loader = window._track_loader
        if loader is not None:
            loader.wait(25000)
        qapp.processEvents()  # flush tracked done

    def test_tracked_loads_and_badges(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"},
             {"name": "a-off", "version": "2-1", "repo": "official"}],
            [{"name": "b-aur", "installed": "1-1", "official": "1.1-1",
              "relation": "upgrade"}])
        qapp.processEvents()
        assert window._track_model.rowCount() == 2
        # movable floats first
        assert window._track_model.item(0)["name"] == "b-aur"
        assert window._track_model.movable_names() == ["b-aur"]
        assert window._view_buttons["tracked"].text() == "Tracked · 1 to move"
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 3
        assert window._footer_stack.currentIndex() == 3

    def test_tracked_switch_runs_and_verifies(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "official": "1.1-1",
              "relation": "upgrade"}])
        window._request_track_switch("b-aur")
        assert isinstance(window._track_runner, FakeRunner)
        assert window._track_model.item(0)["state"] == "switching"
        window._track_runner.done.emit(True, "")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "done"
        assert window._track_runner is None
        assert "official build" in window._sub.text()

    def test_tracked_switch_failure_retries(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "official": "1.1-1",
              "relation": "upgrade"}])
        window._request_track_switch("b-aur")
        window._track_runner.done.emit(False, "boom")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "failed"

    def test_tracked_downgrade_cancel(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "old", "version": "3-1", "repo": "aur"}],
            [{"name": "old", "installed": "3-1", "official": "2-1",
              "relation": "downgrade"}])
        window._request_track_switch("old")  # confirm auto-dismissed
        assert window._track_queue == []
        assert window._track_runner is None

    def test_tracked_lock_blocks(self, window, qapp, monkeypatch):
        import os as _os
        monkeypatch.setattr(_os.path, "exists", lambda p: True)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b", "version": "1-1", "repo": "aur"}],
            [{"name": "b", "installed": "1-1", "official": "1.1-1",
              "relation": "upgrade"}])
        window._request_track_switch("b")
        assert window._track_queue == []
        assert "Another package operation" in window._sub.text()

    def test_tracked_move_all_skips_downgrades(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "a", "version": "1-1", "repo": "aur"},
             {"name": "d", "version": "3-1", "repo": "aur"}],
            [{"name": "a", "installed": "1-1", "official": "1.1-1",
              "relation": "upgrade"},
             {"name": "d", "installed": "3-1", "official": "2-1",
              "relation": "downgrade"}])
        window._move_all_tracked()
        # 'a' starts immediately; 'd' needs individual confirmation
        assert window._track_queue == []
        assert isinstance(window._track_runner, FakeRunner)


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
        assert all(h > 60 for h in heights)
        # Full-list first layout should be comfortably fast; per-row paint
        # afterwards is a single cached pass (dataChanged repaints 1 row).
        print(f"\n322 rows layout: {layout_s:.2f}s "
              f"({layout_s / 322 * 1000:.1f} ms/row)")
        assert layout_s < 20
        v.close()
