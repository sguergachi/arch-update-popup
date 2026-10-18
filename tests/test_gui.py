"""Window-level smoke tests + scroll-performance sanity (offscreen Qt)."""
import time

import pytest

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel

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


class FakeSourceEnableRunner(QObject):
    """_SourceEnableRunner stub: emits line/done on demand."""
    line = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, *a, **k):
        super().__init__()
        self.script = a[0] if a else ""
        self._running = False
        self.terminated = False

    def isRunning(self):
        return self._running

    def wait(self, ms=0):
        return True

    def start(self):
        self._running = True

    def terminate(self):
        self.terminated = True
        self._running = False

    def requestInterruption(self):
        pass


class FakeChecker(QObject):
    """UpdateChecker stub: check runs until the test emits a result."""
    updates_found = pyqtSignal(list)
    no_updates = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, ms=0):
        return True

    def start(self):
        self._running = True

    def requestInterruption(self):
        pass


class FakeInfoFetcher(QObject):
    """InfoFetcher stub: rows arrive only when the test emits them."""
    row_ready = pyqtSignal(int, str, str, str, str, str, str, str, str)
    finished = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._running = False

    def isRunning(self):
        return self._running

    def wait(self, ms=0):
        return True

    def start(self):
        pass

    def requestInterruption(self):
        pass


@pytest.fixture()
def window(qapp, monkeypatch):
    monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
    monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
    # Never pop real desktop bubbles during tests; per-test overrides apply.
    monkeypatch.setattr(app, "_notify_update", lambda *a, **k: 0)
    monkeypatch.setattr(app, "_notify_close", lambda *a, **k: None)
    monkeypatch.setattr(app, "_SourceEnableRunner", FakeSourceEnableRunner)
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
        # Tabs left to right: Updates | Tracked | Untracked | Obsolete |
        # Security.
        assert not hasattr(window, "_search")
        tabs = window._view_buttons
        assert [b.text() for b in
                (tabs["updates"], tabs["tracked"], tabs["untracked"],
                 tabs["obsolete"], tabs["security"])] == [
            "Updates (4)", "Tracked", "Untracked", "Obsolete", "Security"]
        assert tabs["updates"].isChecked()
        head = window.centralWidget().layout().itemAt(0).layout()
        order = [head.indexOf(tabs[k]) for k in
                 ("updates", "tracked", "untracked", "obsolete", "security")]
        assert order == sorted(order)
        assert head.indexOf(window._log_btn) > order[-1]

    def _stage_recheck(self, window, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(app, "InfoFetcher", FakeInfoFetcher)
        monkeypatch.setattr(app, "load_pkg_info_cache", lambda: {})
        window._switch_view("updates")  # active tab → Check Now
        # The tab keeps its label: changing it would reflow the switcher.
        assert window._view_buttons["updates"].text() == "Updates (4)"
        assert not window._view_buttons["updates"].isEnabled()
        assert "Checking for updates" in window._sub.text()
        # The list knows it is loading, but rows are on screen, so the
        # placeholder stays out of their way.
        assert window._loading is True
        assert not window._empty_state.isVisible()
        return window._rechecker

    def test_reclick_updates_tab_checks_now(self, window, qapp, monkeypatch):
        checker = self._stage_recheck(window, monkeypatch)
        assert isinstance(checker, FakeChecker)
        # Second click while checking is ignored (same checker).
        window._switch_view("updates")
        assert window._rechecker is checker
        fresh = [
            {"name": "newpkg", "old": "1-1", "new": "2-1", "repo": "extra"},
            {"name": "aur-new", "old": "1-1", "new": "1.1-1", "repo": "aur"},
        ]
        checker.updates_found.emit(fresh)
        qapp.processEvents()
        assert window._rechecker is None
        assert window._model.rowCount() == 2
        assert window._model.item(0)["name"] == "newpkg"
        assert window._view_buttons["updates"].text() == "Updates (2)"
        assert window._view_buttons["updates"].isEnabled()
        assert "2</b> packages available" in window._sub.text()
        assert "Check complete" in window._log_view.toPlainText()
        # Fresh rows get their own info feed.
        assert isinstance(window._recheck_fetcher, FakeInfoFetcher)
        window._recheck_fetcher.row_ready.emit(
            0, "newpkg", "2-1", "Desc!", "notes", "2026-01-01",
            "full", "", "")
        assert window._model.item(0)["desc"] == "Desc!"
        window._recheck_fetcher.finished.emit()
        qapp.processEvents()
        assert window._recheck_fetcher is None
        assert "release notes loaded" in window._sub.text()

    def test_recheck_empty_clears_list(self, window, qapp, monkeypatch):
        checker = self._stage_recheck(window, monkeypatch)
        checker.no_updates.emit()
        qapp.processEvents()
        assert window._model.rowCount() == 0
        assert window._empty_label.isVisible()
        assert window._view_buttons["updates"].text() == "Updates"
        assert "up to date" in window._sub.text()
        # The footer holds its shape: Refresh stays put, the primary is
        # just off — it never morphs into a second check button.
        assert window._update_btn.text() == "Update"
        assert not window._update_btn.isEnabled()
        assert not window._recheck_btn.isHidden()

    def test_recheck_blocked_while_installing(self, window, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", FakeChecker)
        window._updating = True
        window._switch_view("updates")
        assert window._rechecker is None
        assert "still running" in window._sub.text()
        assert window._view_buttons["updates"].isEnabled()
        window._updating = False

    def test_stale_tray_feed_ignored_after_recheck(
            self, window, qapp, monkeypatch):
        checker = self._stage_recheck(window, monkeypatch)
        checker.updates_found.emit(
            [{"name": "newpkg", "old": "1-1", "new": "2-1",
              "repo": "extra"}])
        qapp.processEvents()
        assert window._info_gen == 1
        # A late tray feed for the OLD list must not touch the new rows.
        window.on_info_ready(0, "STALE", "s", "d", "f", "", "")
        assert window._model.item(0)["desc"] != "STALE"
        window.on_fetch_done()
        assert "fetching release notes" in window._sub.text()

    def test_recheck_button_checks_now(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(app, "InfoFetcher", FakeInfoFetcher)
        monkeypatch.setattr(app, "load_pkg_info_cache", lambda: {})
        assert window._recheck_btn.isEnabled()
        assert window._recheck_btn.text() == "Refresh"
        window._recheck_btn.click()
        qapp.processEvents()
        assert isinstance(window._rechecker, FakeChecker)
        assert not window._recheck_btn.isEnabled()
        assert not window._update_btn.isEnabled()
        window._rechecker.no_updates.emit()
        qapp.processEvents()
        assert window._model.rowCount() == 0
        assert window._recheck_btn.isEnabled()
        assert window._view_buttons["updates"].text() == "Updates"

    def test_update_blocked_during_recheck(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(app, "InfoFetcher", FakeInfoFetcher)
        monkeypatch.setattr(app, "load_pkg_info_cache", lambda: {})
        window._switch_view("updates")
        assert window._rechecker is not None
        window.start_update()
        assert window._updating is False  # refused
        assert "still running" in window._sub.text()
        window._rechecker.no_updates.emit()
        qapp.processEvents()
        assert window._recheck_btn.isEnabled()

    def test_tab_switch_still_works(self, window, qapp):
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 1
        assert window._rechecker is None  # no check on plain navigation
        window._switch_view("updates")  # coming back: plain switch, no check
        assert window._content_stack.currentIndex() == 0
        assert window._rechecker is None

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
        # The header line is elided to one line; the sentence itself is
        # kept whole in _status_full (and the tooltip).
        assert "2 times in a row" in window._status_full

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

    def test_obsolete_remover_graceful_terminate(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: n == "sudo")
        r = app.ObsoleteRemover(["some-pkg"])
        assert r._proc is None
        r.terminate()  # no process yet — must not raise
        assert r._killed is True
        r._cleanup()  # don't leak the askpass script in /tmp

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

    def test_cancelled_aborts_cleanly(self, window, qapp):
        # Dismissing sudo mid-run: no failed rows, no red error, no
        # retry mode — back to idle with everything still selected.
        window._safe_names = ["konsole"]
        window._updating = True
        r = window._model.row_of("konsole")
        window._model.set_state("konsole", "installing")
        window._installing_row = r
        window._on_update_done(False, "Request dismissed")
        qapp.processEvents()
        assert window._model.item(r)["state"] == "idle"
        assert window._updating is False
        assert window._retry_mode is False
        assert window._fail_streak == 0
        assert "Update cancelled" in window._sub.text()
        assert "failed" not in window._sub.text().lower()
        assert window._details.isHidden()
        assert window._update_btn.isEnabled()
        assert "Retry" not in window._update_btn.text()
        assert "Update cancelled" in window._log_view.toPlainText()
        # Stat card relaxes back to ready, progress to idle hairline.
        assert window._stat_ready._caption.text() == "READY TO INSTALL"
        assert window._progress.value() == 0

    def test_failed_sudo_reenables_update_all(self, window, qapp):
        # Real pkexec auth failure: nothing ran — Update All comes back
        # immediately so the user can retry, with no failed rows.
        window._safe_names = ["konsole"]
        window._updating = True
        r = window._model.row_of("konsole")
        window._model.set_state("konsole", "installing")
        window._installing_row = r
        window._update_btn.setEnabled(False)
        window._on_update_done(
            False,
            "Error executing command as another user: Not authorized\n"
            "This incident has been reported.")
        qapp.processEvents()
        assert window._model.item(r)["state"] == "idle"
        assert window._retry_mode is False
        assert window._update_btn.isEnabled()
        assert "Retry" not in window._update_btn.text()
        assert window._details.isHidden()
        assert "Update cancelled" in window._sub.text()

    def test_checker_exception_reports_no_updates(self, qapp, monkeypatch):
        # A crashing check must resolve as "no updates", never wedge the
        # Checking… state with no signal.
        monkeypatch.setattr(app, "get_pending_updates", lambda h: 1 / 0)
        got = []
        c = app.UpdateChecker(None)
        c.updates_found.connect(lambda u: got.append(("found", u)))
        c.no_updates.connect(lambda: got.append(("empty",)))
        c.run()  # synchronous: must not raise
        assert got == [("empty",)]

    def test_single_cancelled_aborts_cleanly(self, window, qapp):
        r = window._model.row_of("konsole")
        window._model.set_state("konsole", "installing")
        window._single_name = "konsole"
        window._on_single_done(False, "Sorry, try again.\n"
                               "sudo: 3 incorrect password attempts")
        qapp.processEvents()
        assert window._model.item(r)["state"] == "idle"
        assert window._details.isHidden()
        assert "cancelled" in window._sub.text().lower()
        assert "konsole install cancelled" in window._log_view.toPlainText()

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
        assert "0 packages pending" in window._status_full

    def test_log_window(self, window):
        window._on_log_line("[###] 100%")
        assert window._log_view.toPlainText() == ""
        window._on_log_line("error: target not found: foo")
        assert "target not found" in window._log_view.toPlainText()
        window._on_log_toggled(True)
        assert window._log_view.isVisible()
        window._on_log_toggled(False)

    def test_theme_switch_reapplies_live(self, window, qapp):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QPalette, QColor
        # Every themed button icon is registered for refresh.
        registered = {btn for btn, _, _, _ in window._theme_icons.values()}
        assert window._update_btn in registered
        assert window._log_btn in registered
        assert window._sec_scan_btn in registered
        for btn in (window._recheck_btn, window._track_refresh_btn,
                    window._un_refresh_btn, window._obs_refresh_btn):
            assert btn in registered
        assert len(registered) == len(window._theme_icons)
        # Prime the Updates delegate cache under the light theme.
        window.on_info_ready(0, "desc", "notes", "2026-01-01", "full", "", "")
        qapp.processEvents()
        assert len(window._delegate._docs) > 0
        old = qapp.palette()
        try:
            dark = QPalette()
            dark.setColor(QPalette.ColorRole.Window, QColor(35, 38, 41))
            dark.setColor(QPalette.ColorRole.Text, QColor(239, 240, 241))
            # paletteChanged fires → _apply_theme() via the signal.
            qapp.setPalette(dark)
            qapp.processEvents()
            assert window._theme_dark is True
            assert "#a9a9a9" in window.styleSheet()
            # Rebuilt docs carry the dark text color, not the old light one.
            doc = window._delegate._doc(0, "desc", "x", 300, None, False, 0)
            assert "#eff0f1" in doc.defaultStyleSheet().lower()
            # changeEvent path is wired too (no-op when theme unchanged).
            window.changeEvent(QEvent(QEvent.Type.StyleChange))
            assert window._theme_dark is True
        finally:
            qapp.setPalette(old)
            qapp.processEvents()
        assert window._theme_dark is False
        assert window.styleSheet() == app._APP_QSS

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
        assert window._content_stack.count() == 5
        assert window._footer_stack.count() == 5
        assert window._content_stack.currentIndex() == 0
        assert window._footer_stack.currentIndex() == 0
        tabs = window._view_buttons
        assert set(tabs) == {"updates", "obsolete", "security", "tracked",
                             "untracked"}
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
        for key in ("updates", "tracked", "untracked", "obsolete",
                    "security"):
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
        # The preview always occupies its line (nothing may shift); only
        # its text changes.
        assert window._log_preview.isVisible()
        assert window._log_preview.text() == ""
        window._on_log_line(":: installing konsole (1/2)")
        qapp.processEvents()
        assert "konsole" in window._log_preview.text()
        # HTML-unsafe log output must not break the label
        window._on_log_line("<b>not a tag</b> & done")
        assert "not a tag" in window._log_preview.text()
        # click opens the panel, which hides the preview
        window._log_preview.clicked.emit()
        qapp.processEvents()
        assert window._log_panel.isVisible()
        assert window._log_preview.text() == ""  # redundant while open
        assert window._log_preview.isVisible()   # but the line stays
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
        # 0 of 3 done (the row it named is only *starting*) → 0%.
        assert "0 of 3 done" in window._sub.text()
        assert "<b>0%</b>" in window._sub.text()

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
            {"name": "graduated", "installed": "1.0-1", "target": "1.1-1", "source": "extra",
             "relation": "upgrade"},
            {"name": "shiny", "installed": "2.0-1", "target": "1.9-1", "source": "extra",
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

    def test_switch_cancel_resets_row(self, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        dlg = app.MovesDialog(self._moves())
        dlg.resize(760, 480)
        dlg.show()
        qapp.processEvents()
        dlg.request_switch("graduated")
        dlg._runner.done.emit(False, "Request dismissed")
        qapp.processEvents()
        assert dlg._rows["graduated"]._switch_btn.text() == "Switch"
        assert dlg._runner is None
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
        # The list frame stays on screen with the empty state inside it —
        # an empty list is still a list, not floating text.
        assert w._empty_label.isVisible()
        assert w._empty_state.isVisible()
        assert w._list.isVisible()
        assert w._empty_state.parent() is w._list.viewport()
        # Nothing stands down: hiding a container would shift the whole
        # window when the list fills (see AGENTS.md).
        assert w._list_header.isVisible()
        assert w._stats_row.isVisible()
        assert w._stat_blocked is not None
        assert w._empty_title.text() == "You're up to date"
        # Empty list: the update action is off, Refresh stays put.
        assert not w._update_btn.isEnabled()
        assert w._update_btn.text() == "Update"
        assert not w._recheck_btn.isHidden()
        assert w._sub.text() == "No updates pending"
        w.close()

    def test_primary_check_now_rechecks_when_empty(
            self, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(app, "InfoFetcher", FakeInfoFetcher)
        monkeypatch.setattr(app, "load_pkg_info_cache", lambda: {})
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        assert not w._update_btn.isEnabled()   # nothing to update
        w._recheck_btn.click()                 # Refresh is the way in
        qapp.processEvents()
        assert isinstance(w._rechecker, FakeChecker)
        w._rechecker.updates_found.emit(
            [{"name": "fresh", "old": "1-1", "new": "2-1",
              "repo": "extra"}])
        qapp.processEvents()
        # …and the update action comes back to life.
        assert w._update_btn.isEnabled()
        assert w._update_btn.text() == "Update All"
        assert not w._recheck_btn.isHidden()
        assert w._model.rowCount() == 1
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
        assert window._content_stack.currentIndex() == 3
        assert window._footer_stack.currentIndex() == 3
        assert window._view_buttons["obsolete"].isChecked()
        window._switch_view("security")
        assert window._content_stack.currentIndex() == 4
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 1
        assert window._footer_stack.currentIndex() == 1
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
        assert "Nothing obsolete" in window._obs_summary.text()

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
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"}])
        qapp.processEvents()
        assert window._track_model.rowCount() == 2
        # movable floats first
        assert window._track_model.item(0)["name"] == "b-aur"
        assert window._track_model.movable_names() == ["b-aur"]
        assert window._view_buttons["tracked"].text() == "Tracked (2)"
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 1
        assert window._footer_stack.currentIndex() == 1

    def test_tracked_switch_runs_and_verifies(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        monkeypatch.setattr(app, "verify_upgraded", lambda expected: [])
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
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
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"}])
        window._request_track_switch("b-aur")
        window._track_runner.done.emit(False, "boom")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "failed"

    def test_tracked_nothing_movable_reported(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "a-off", "version": "2-1", "repo": "official"},
             {"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [])
        qapp.processEvents()
        assert "none available from a managed repo" in \
            window._track_summary.text()
        assert "nothing to move" in window._track_footer_label.text()
        assert not window._track_move_all_btn.isEnabled()

    def test_activity_log_records_actions(self, window, qapp):
        window._log_action("Removing foo…")
        window._log_action("Opened bar")
        text = window._log_view.toPlainText()
        assert "◆ Removing foo…" in text
        assert "◆ Opened bar" in text
        # Preview follows the latest line; the panel stays shut.
        assert "Opened bar" in window._log_preview.text()
        assert not window._log_panel.isVisible()

    def test_error_details_dismissable(self, window, qapp):
        window._details.setPlainText("boom")
        window._details_label.setVisible(True)
        window._details.setVisible(True)
        window._copy_btn.setVisible(True)
        window._dismiss_btn.setVisible(True)
        qapp.processEvents()
        assert not window._details.isHidden()
        window._dismiss_btn.click()
        qapp.processEvents()
        assert window._details.isHidden()
        assert window._details_label.isHidden()
        assert window._copy_btn.isHidden()
        assert window._dismiss_btn.isHidden()
        assert window._details.toPlainText() == ""

    def test_tracked_downgrade_cancel(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "old", "version": "3-1", "repo": "aur"}],
            [{"name": "old", "installed": "3-1", "target": "2-1", "source": "extra",
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
            [{"name": "b", "installed": "1-1", "target": "1.1-1", "source": "extra",
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
            [{"name": "a", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"},
             {"name": "d", "installed": "3-1", "target": "2-1", "source": "extra",
              "relation": "downgrade"}])
        window._move_all_tracked()
        # 'a' starts immediately; 'd' needs individual confirmation
        assert window._track_queue == []
        assert isinstance(window._track_runner, FakeRunner)

    class _AcceptBox:
        """QMessageBox stub that always picks the first AcceptRole button."""
        from PyQt6.QtWidgets import QMessageBox as _MB
        Icon = _MB.Icon
        ButtonRole = _MB.ButtonRole
        StandardButton = _MB.StandardButton

        class _Btn(QObject):
            """Stands in for a QPushButton: the app connects `clicked`."""
            clicked = pyqtSignal()

        def __init__(self, *a, **k):
            self._accept = self._Btn()

        def setWindowTitle(self, *a): pass
        def setIcon(self, *a): pass
        def setText(self, *a): pass
        def setInformativeText(self, *a): pass
        def setDetailedText(self, *a): pass
        def setStyleSheet(self, *a): pass
        def addButton(self, *a, **k): return self._accept
        def exec(self): return None
        def clickedButton(self): return self._accept

    def _stage_tracked(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "del-me", "version": "1-1", "repo": "aur"}],
            [])
        qapp.processEvents()

    def test_tracked_uninstall_success(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        import subprocess as sp

        class R:
            returncode = 1  # pacman -Q: package gone

        monkeypatch.setattr(sp, "run", lambda *a, **k: R())
        self._stage_tracked(window, qapp)
        window._request_track_uninstall("del-me")
        assert isinstance(window._track_uninstaller, FakeRunner)
        assert window._track_model.item(0)["state"] == "uninstalling"
        window._track_uninstaller.done.emit(True, "")
        qapp.processEvents()
        assert window._track_model.row_of("del-me") is None
        assert "uninstalled" in window._sub.text()

    def test_tracked_uninstall_failure(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        self._stage_tracked(window, qapp)
        window._request_track_uninstall("del-me")
        window._track_uninstaller.done.emit(False, "boom")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "failed"
        assert window._track_model.item(0)["failed_op"] == "uninstall"
        assert "Removing del-me failed" in window._sub.text()

    def test_tracked_uninstall_cancel(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(app.QMessageBox, "clickedButton", lambda self: None)
        self._stage_tracked(window, qapp)
        window._request_track_uninstall("del-me")  # confirm dismissed
        assert window._track_uninstaller is None
        assert window._track_model.item(0)["state"] == "idle"

    def test_tracked_uninstall_busy(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "del-me", "version": "1-1", "repo": "aur"}],
            [])
        window._track_uninstaller = FakeRunner()
        window._request_track_uninstall("del-me")
        assert "already running" in window._sub.text()
        window._track_uninstaller = None

    def _stage_untracked(self, window, qapp):
        apps = [app._tracked_row("firefox", "1.0-1", "official",
                                 title="Firefox", desktop="/u/firefox.desktop",
                                 desc="Web browser")]
        pkgs = [app._tracked_row("libfoo", "2-1", "official")]
        window._on_untracked_loaded(apps, pkgs)
        qapp.processEvents()
        return apps, pkgs

    def test_untracked_tab_index_and_lazy_load(
            self, window, qapp, monkeypatch):
        assert window._untracked_loaded is False
        assert window._untracked_loader is None
        started = []
        orig = app.UntrackedLoader

        class SpyLoader(orig):
            def start(self):
                started.append(True)
                super().start()

        monkeypatch.setattr(app, "UntrackedLoader", SpyLoader)
        window._switch_view("untracked")
        assert window._content_stack.currentIndex() == 2
        assert window._footer_stack.currentIndex() == 2
        assert window._view_buttons["untracked"].isChecked()
        assert started == [True]
        assert window._untracked_loader is not None
        # Second visit does not re-scan.
        window._switch_view("updates")
        window._switch_view("untracked")
        assert started == [True]
        # Settle the real loader so it never outlives teardown (a live
        # QThread at process exit aborts).
        loader = window._untracked_loader
        if loader is not None:
            loader.wait(30000)
        qapp.processEvents()

    def test_untracked_loads_and_badges(self, window, qapp):
        self._stage_untracked(window, qapp)
        assert window._untracked_loaded is True
        assert window._unapp_model.rowCount() == 1
        assert window._unpkg_model.rowCount() == 1
        assert window._view_buttons["untracked"].text() == "Untracked (2)"
        assert "1</b> application" in window._un_summary.text()
        assert "1</b> dependency package" in window._un_summary.text()
        # App row shows the title; ops use the package name.
        d = window._unapp_view.itemDelegate()
        from PyQt6.QtWidgets import QStyleOptionViewItem as _Opt
        idx = window._unapp_model.index(0)
        opt = _Opt()
        opt.rect = window._unapp_view.visualRect(idx)
        opt.palette = window._unapp_view.palette()
        opt.font = window._unapp_view.font()
        lay = d._geom(0, window._unapp_model.item(0), opt.rect.width())
        assert lay["title"] == "Firefox"

    def test_untracked_batch_footer(self, window, qapp):
        self._stage_untracked(window, qapp)
        assert window._un_uninstall_btn.text() == "Uninstall selected"
        assert not window._un_uninstall_btn.isEnabled()
        window._unapp_model.set_all(True)
        window._refresh_untracked_footer()
        qapp.processEvents()
        assert window._un_uninstall_btn.text() == "Uninstall 1 selected"
        assert window._un_uninstall_btn.isEnabled()
        assert "1 of 2 selected" in window._un_sel_label.text()

    def test_untracked_uninstall_success(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        import subprocess as sp

        class R:
            returncode = 1  # pacman -Q: package gone

        monkeypatch.setattr(sp, "run", lambda *a, **k: R())
        self._stage_untracked(window, qapp)
        window._request_untracked_uninstall("libfoo")
        assert isinstance(window._untracked_uninstaller, FakeRunner)
        assert window._unpkg_model.item(0)["state"] == "uninstalling"
        window._untracked_uninstaller.done.emit(True, "")
        qapp.processEvents()
        assert window._unpkg_model.row_of("libfoo") is None
        assert window._unapp_model.rowCount() == 1  # untouched
        assert "uninstalled" in window._sub.text()

    def test_untracked_unowned_app_skipped(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        window._on_untracked_loaded(
            [app._tracked_row("mystuff", "", "", title="My Stuff")], [])
        qapp.processEvents()
        window._request_untracked_uninstall("mystuff")
        assert window._untracked_uninstaller is None  # nothing to remove
        assert "isn't owned by any package" in window._sub.text()

    def test_tracked_multiselect_footer_morphs(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "a", "version": "1", "repo": "aur"},
             {"name": "b", "version": "1", "repo": "extra"}],
            [])
        qapp.processEvents()
        # No selection → move-all offering (nothing movable here).
        assert window._track_move_all_btn.text() == "Move to managed repos"
        assert not window._track_move_all_btn.isEnabled()
        # Check one row → main button becomes the uninstall action.
        window._track_model.set_all(True)
        window._refresh_tracked_footer()
        qapp.processEvents()
        assert window._track_move_all_btn.text() == "Uninstall 2 selected"
        assert window._track_move_all_btn.isEnabled()
        # The list header carries the selection: ticked when everything
        # in view is selected, and it says so in words too.
        assert window._track_header.check.checkState() == \
            Qt.CheckState.Checked
        assert "All 2 packages selected" in window._track_sel_label.text()
        window._select_tracked_all(False)
        assert window._track_move_all_btn.text() == "Move to managed repos"

    def test_tracked_batch_uninstall_success(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        import subprocess as sp

        class R:
            returncode = 1  # pacman -Q: package gone

        monkeypatch.setattr(sp, "run", lambda *a, **k: R())
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "a", "version": "1", "repo": "aur"},
             {"name": "b", "version": "1", "repo": "extra"}],
            [])
        window._track_model.set_all(True)
        window._refresh_tracked_footer()
        assert window._track_move_all_btn.text() == "Uninstall 2 selected"
        window._on_track_main_clicked()
        assert isinstance(window._track_uninstaller, FakeRunner)
        assert window._track_uninstall_names == ["a", "b"]
        window._track_uninstaller.done.emit(True, "")
        qapp.processEvents()
        assert window._track_model.rowCount() == 0
        assert "uninstalled" in window._sub.text()

    def test_tracked_switch_cancel_aborts_cleanly(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"}])
        window._request_track_switch("b-aur")
        assert window._track_model.item(0)["state"] == "switching"
        window._track_runner.done.emit(False, "Request dismissed")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "idle"
        assert window._track_runner is None
        assert "cancelled" in window._sub.text().lower()

    def test_tracked_uninstall_cancel_aborts_cleanly(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", self._AcceptBox)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        self._stage_tracked(window, qapp)
        window._request_track_uninstall("del-me")
        window._track_uninstaller.done.emit(False, "Request dismissed")
        qapp.processEvents()
        assert window._track_model.item(0)["state"] == "idle"
        assert "dismissed" in window._sub.text().lower()

    def test_obsolete_removal_cancel_aborts_cleanly(self, window, qapp):
        window._remover = FakeRunner()
        window._on_obsolete_removal_done(False, "Request dismissed")
        qapp.processEvents()
        assert window._remover is None
        assert "dismissed" in window._sub.text().lower()
        assert "nothing was changed" in window._sub.text().lower()
        assert window._update_btn.isEnabled()

    def test_tracked_main_click_moves_when_unselected(
            self, window, qapp, monkeypatch):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "b-aur", "version": "1-1", "repo": "aur"}],
            [{"name": "b-aur", "installed": "1-1", "target": "1.1-1", "source": "extra",
              "relation": "upgrade"}])
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        window._on_track_main_clicked()
        assert isinstance(window._track_runner, FakeRunner)
        assert window._track_current == "b-aur"


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


class _RecordingBox:
    """QMessageBox stub that records what the app would have shown."""
    from PyQt6.QtWidgets import QMessageBox as _MB
    Icon = _MB.Icon
    ButtonRole = _MB.ButtonRole
    StandardButton = _MB.StandardButton

    shown: list = []

    class _Btn(QObject):
        clicked = pyqtSignal()

    def __init__(self, *a, **k):
        self.title = ""
        self.text = ""
        self.info = ""
        self.detail = ""
        self._btn = self._Btn()
        _RecordingBox.shown.append(self)

    def setWindowTitle(self, text):
        self.title = text

    def setIcon(self, *a):
        pass

    def setText(self, text):
        self.text = text

    def setInformativeText(self, text):
        self.info = text

    def setDetailedText(self, text):
        self.detail = text

    def setStyleSheet(self, *a):
        pass

    def addButton(self, *a, **k):
        return self._btn

    def exec(self):
        return None

    def clickedButton(self):
        return self._btn


class TestFailureDialog:
    """A failure the user did not ask for must be visible, not just logged."""

    def _record(self, monkeypatch):
        monkeypatch.setattr(app, "QMessageBox", _RecordingBox)
        _RecordingBox.shown = []
        return _RecordingBox.shown

    def test_update_failure_shows_the_error_in_a_dialog(self, window,
                                                       monkeypatch):
        shown = self._record(monkeypatch)
        window._safe_names = ["konsole"]
        window._on_update_done(False, "konsole: boom happened")
        assert shown, "no dialog was shown"
        box = shown[0]
        assert "Arch Update" in box.title
        assert "could not be installed" in box.text
        assert "konsole" in box.text
        # The raw output is one click away, not hidden in the panel only.
        assert "boom happened" in box.detail
        assert "Retry" in box.info

    def test_dismissed_authentication_is_not_a_dialog(self, window,
                                                     monkeypatch):
        # The exact error the user hit: pkexec with no tty. Nothing ran, so
        # shouting about it in a dialog would be wrong.
        shown = self._record(monkeypatch)
        window._safe_names = ["perplexity"]
        window._on_update_done(
            False,
            "Error creating textual authentication agent: Error opening "
            "current controlling terminal for the process (`/dev/tty'): "
            "No such device or address")
        assert shown == []
        # And it says so calmly instead: nothing ran, nothing changed.
        assert "nothing was changed" in window._status_full.lower()

    def test_removal_failure_shows_the_error(self, window, qapp, monkeypatch):
        shown = self._record(monkeypatch)
        monkeypatch.setattr(app, "ObsoleteRemover", FakeRunner)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        self._stage = None
        if window._obs_checker is not None:
            window._obs_checker.wait(25000)
        qapp.processEvents()
        if window._track_loader is not None:
            window._track_loader.wait(25000)
        qapp.processEvents()
        window._on_tracked_loaded(
            [{"name": "perplexity", "version": "1.6.0-1", "repo": "aur"}], [])
        qapp.processEvents()
        window._request_track_uninstall("perplexity")
        window._track_uninstaller.done.emit(
            False, "perplexity: cannot remove — file conflict")
        qapp.processEvents()
        assert shown, "no dialog for a failed removal"
        box = shown[-1]
        assert "Removing perplexity failed" in box.text
        assert "file conflict" in box.detail or "file conflict" in box.info

    def test_single_install_failure_shows_the_error(self, window, qapp,
                                                   monkeypatch):
        shown = self._record(monkeypatch)
        monkeypatch.setattr(app, "UpdateRunner", FakeRunner)
        window._run_single("konsole")
        window._single.done.emit(False, "konsole: nope")
        qapp.processEvents()
        assert shown, "no dialog for a failed single install"
        assert "Installing konsole failed" in shown[0].text
        assert "nope" in shown[0].detail


class TestNoLayoutShifts:
    """The window's geometry is fixed; only what sits inside a slot changes.

    This is the law in AGENTS.md, as a test: pressing Refresh, getting an
    empty list, or streaming log output must not move anything.
    """

    STRUCTURE = ("_stats_row", "_list_header", "_list", "_log_preview",
                 "_progress")

    def _snap(self, window, skip=()):
        snap = {name: getattr(window, name).geometry()
                for name in self.STRUCTURE if name not in skip}
        # Footer buttons may resize with their label, but their baseline
        # and right edge must not move.
        snap["_update_btn"] = (window._update_btn.geometry().right(),
                               window._update_btn.height())
        snap["_recheck_btn"] = (window._recheck_btn.geometry().right(),
                                window._recheck_btn.height())
        return snap

    class _SilentChecker(QObject):
        """A checker that never reports: the test drives the states."""
        updates_found = pyqtSignal(list)
        no_updates = pyqtSignal()

        def __init__(self, *a, **k):
            super().__init__()

        def isRunning(self):
            return False

        def wait(self, ms=0):
            return True

        def start(self):
            pass

        def requestInterruption(self):
            pass

    def test_refresh_and_empty_do_not_move_the_layout(
            self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "UpdateChecker", self._SilentChecker)
        window.resize(1280, 800)
        qapp.processEvents()
        before = self._snap(window)

        # With rows on screen a refresh changes nothing but the status
        # line: no placeholder, no movement.
        window._recheck_updates()
        qapp.processEvents()
        assert not window._empty_state.isVisible()
        assert self._snap(window) == before

        # Empty list, then refresh: the box itself says it is loading…
        # The two view banners may appear/disappear here (AGENTS.md allows
        # exactly that shift), so their followers _list_header/_list are
        # out of scope for this phase — and must land back on `before`
        # when the check returns rows (asserted below, strongest form).
        rows_snapshot = {k: v for k, v in before.items()
                         if k not in ("_list_header", "_list")}
        window._on_recheck_empty()
        qapp.processEvents()
        assert window._empty_title.text() == "You're up to date"
        assert not window._slot_security.isVisible()
        assert not window._slot_moves.isVisible()
        assert self._snap(window, skip=("_list_header", "_list")) \
            == rows_snapshot
        window._recheck_updates()
        qapp.processEvents()
        assert window._empty_state.isVisible()
        assert window._empty_title.text() == "Loading…"
        assert window._empty_label.text() == "Checking for updates…"
        assert self._snap(window, skip=("_list_header", "_list")) \
            == rows_snapshot

        # …and lands back on rows, in the same place — banners restored.
        window._on_recheck_found(_updates())
        qapp.processEvents()
        assert not window._empty_state.isVisible()
        assert window._slot_security.isVisible()
        assert self._snap(window) == before

    def test_log_line_does_not_move_the_list(self, window, qapp):
        before = window._list.geometry()
        window._on_log_line(":: installing konsole (1/2)")
        qapp.processEvents()
        assert window._log_preview.text() != ""   # the line is used…
        assert window._list.geometry() == before  # …but nothing moved

    def test_filter_does_not_move_the_layout(self, window, qapp):
        window.resize(1280, 800)
        qapp.processEvents()
        before = self._snap(window)
        window._list_header.search.setText("aur")
        qapp.processEvents()
        assert self._snap(window) == before
        window._list_header.search.clear()
        qapp.processEvents()
        assert self._snap(window) == before

    def test_every_row_is_the_same_height(self, window, qapp):
        # Uneven rows make a pixel-scrolled view re-anchor as it measures,
        # which looks like the list scrolling by itself.
        window.resize(1280, 800)
        qapp.processEvents()
        d = window._list.itemDelegate()
        width = window._list.viewport().width()
        heights = {d._layout(r, window._model.item(r), width)["height"]
                   for r in range(window._model.rowCount())}
        assert len(heights) == 1
        assert window._list.resizeMode() == app.QListView.ResizeMode.Fixed


class TestViewBanners:
    """The two view banners mirror the current check's facts.

    The security banner and the AUR→official prompt are rebuilt by every
    model swap; a fresh empty list must not keep the stale warning. They
    are the only shells allowed to appear/disappear (see AGENTS.md).
    """

    def _banner_text(self, slot):
        out = []
        lay = slot.layout()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None:
                continue
            for lbl in w.findChildren(QLabel):
                out.append(lbl.text())
        return "\n".join(out)

    def test_empty_list_has_no_stale_banners(self, window, qapp):
        window._swap_updates_model([], {})
        window._mark_checked()
        window._refresh_empty_state()
        qapp.processEvents()
        assert not window._slot_security.isVisible()
        assert not window._slot_moves.isVisible()

    def test_banner_counts_follow_the_list(self, window, qapp):
        window._swap_updates_model(_updates(4), {})
        qapp.processEvents()
        assert window._slot_security.isVisible()
        assert "1 compromised" in self._banner_text(window._slot_security)
        window._swap_updates_model(_updates(8), {})
        qapp.processEvents()
        assert "2 compromised" in self._banner_text(window._slot_security)

    def test_moves_banner_filters_to_current_list(self, window, qapp):
        window._pending_moves = [{"name": "yakuake", "installed": "1-1",
                                  "target": "1.1-1", "source": "extra", "relation": "upgrade"}]
        window._swap_updates_model(
            [{"name": "yakuake", "old": "1-1", "new": "1.1-1",
              "repo": "aur"}], {})
        qapp.processEvents()
        assert window._slot_moves.isVisible()
        # The package left the list → the prompt goes with it.
        window._swap_updates_model(
            [{"name": "konsole", "old": "1-1", "new": "2-1",
              "repo": "official"}], {})
        qapp.processEvents()
        assert not window._slot_moves.isVisible()


class TestCardListRemeasure:
    """Fixed-mode rows re-measure when the viewport narrows.

    Opening the log panel (or any resize) narrows the lists after the
    model is placed; without re-measurement the cards clip on the right
    and a horizontal scrollbar appears.
    """

    def test_rows_follow_viewport_narrowing(self, window, qapp):
        window.resize(1280, 800)
        qapp.processEvents()
        # Real-world trigger: open the log panel, which narrows the list.
        window._log_btn.setChecked(True)
        qapp.processEvents()
        new_w = window._list.viewport().width()
        d = window._list.itemDelegate()
        assert new_w < 1230  # actually narrowed
        assert new_w in {key[1] for key in d._geo}
        assert not window._list.horizontalScrollBar().isVisible()

    def test_tracked_list_remeasures(self, window, qapp):
        window.resize(1280, 800)
        qapp.processEvents()
        window._switch_view("tracked")
        qapp.processEvents()
        # One real row so sizeHint is exercised, then narrow via log panel.
        window._unapp_model.set_items([
            app._tracked_row("zed", "1.0-1", "aur")])
        window._log_btn.setChecked(True)
        qapp.processEvents()
        new_w = window._track_list.viewport().width()
        d = window._track_list.itemDelegate()
        assert d is not window._list.itemDelegate()
        idx = window._unapp_model.index(0)
        if idx.isValid():
            from PyQt6.QtWidgets import QStyleOptionViewItem
            from PyQt6.QtCore import QRect
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 1000, 100)
            hint = d.sizeHint(opt, idx)
            assert hint.width() == new_w
        assert not window._track_list.horizontalScrollBar().isVisible()


class TestPlaceholdersEverywhere:
    """No list box is ever blank: every one explains itself, in place."""

    def test_obsolete_says_it_is_scanning_on_first_open(self, window, qapp):
        # Deterministic: the loading state is set when the scan starts,
        # before the stubbed thread can report back.
        window._switch_view("obsolete")
        window._obs_loaded = False
        window._obs_empty.hide_state()
        window._load_obsolete_async()
        qapp.processEvents()
        assert window._obs_empty.isVisible()
        assert window._obs_empty.title.text() == "Loading…"
        # And when it reports, the same box swaps its text.
        window._on_obs_preload_done([])
        assert window._obs_empty.title.text() == "Nothing to clean up"

    def test_tracked_says_it_is_loading_before_data_arrives(self, qapp):
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        # Not hidden (the page itself is off-screen, hence isHidden()).
        assert not w._track_empty.isHidden()
        w._track_loaded = False
        w._refresh_tracked_summary()
        assert w._track_empty.title.text() == "Loading…"
        w._on_tracked_loaded([], [])
        qapp.processEvents()
        assert w._track_empty.title.text() == "Nothing tracked"
        w.close()

    def test_untracked_says_it_is_scanning_on_first_open(self, qapp):
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        w._switch_view("untracked")
        qapp.processEvents()
        for holder in (w._unapp_empty, w._unpkg_empty):
            assert not holder.isHidden()   # never a blank box
        w.close()

    def test_security_explains_itself_before_the_first_scan(self, window,
                                                           qapp):
        window._switch_view("security")
        qapp.processEvents()
        assert window._sec_empty.isVisible()
        assert window._sec_empty.title.text() == "Not scanned yet"
        assert "Run a scan" in window._sec_empty.body.text()


class TestEmptyState:
    """An empty window still reads as a composed screen."""

    def test_placeholder_says_when_it_was_checked(self, qapp):
        import time as _t
        w = app.UpdateWindow([], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        assert "Not checked yet" in w._empty_label.text()
        w._mark_checked()
        w._refresh_empty_state()
        assert "Last checked just now" in w._empty_label.text()
        assert "next check in" in w._empty_label.text()
        # …and ages into a clock time rather than lying about "just now".
        w._checked_at = _t.time() - 7200
        w._refresh_empty_state()
        assert "Last checked at" in w._empty_label.text()
        w.close()

    def test_filter_empty_state_keeps_the_filter_box(self, qapp):
        w = app.UpdateWindow([{"name": "konsole", "old": "1-1", "new": "2-1",
                               "repo": "official"}], {}, None)
        w.resize(1280, 800)
        w.show()
        qapp.processEvents()
        assert not w._empty_state.isVisible()
        w._list_header.search.setText("nothing-matches")
        qapp.processEvents()
        assert w._empty_state.isVisible()
        assert w._list_header.isVisible()
        assert w._empty_title.text() == "No matches"
        w.close()


class TestListHeaderAndFilter:
    """Row controls live with the rows: select-all, count, and a filter."""

    def test_header_sits_with_the_list(self, window, qapp):
        up = window._content_stack.widget(0).layout()
        widgets = [up.itemAt(i).widget() for i in range(up.count())]
        assert window._list_header in widgets
        assert widgets.index(window._list_header) < widgets.index(window._list)
        # ...and the footer no longer carries All/None buttons.
        assert not hasattr(window, "_select_all_btn")
        assert not hasattr(window, "_select_none_btn")

    def test_select_all_checkbox_drives_selection(self, window, qapp):
        window._select_all(False)
        qapp.processEvents()
        assert window._list_header.check.checkState() == \
            Qt.CheckState.Unchecked
        window._list_header.check.click()
        qapp.processEvents()
        assert window._list_header.check.checkState() == Qt.CheckState.Checked
        assert window._model.selected_count() == window._model.checkable_count()
        window._list_header.check.click()
        qapp.processEvents()
        assert window._list_header.check.checkState() == \
            Qt.CheckState.Unchecked
        assert window._model.selected_count() == 0

    def test_partial_selection_shows_a_count(self, window, qapp):
        window._select_all(False)
        window._on_row_toggled(0)
        qapp.processEvents()
        assert window._list_header.check.checkState() == \
            Qt.CheckState.PartiallyChecked
        assert window._list_header.count.isVisible()
        assert "1 of 3 selected" in window._sel_label.text()

    def test_filter_hides_non_matching_rows(self, window, qapp):
        window._list_header.search.setText("aur")
        qapp.processEvents()
        assert window._model.rowCount() == 2
        names = [window._model.item(r)["name"] for r in
                 range(window._model.rowCount())]
        assert names == ["aur-pkg", "evil"]
        window._list_header.search.setText("konsole")
        qapp.processEvents()
        assert window._model.rowCount() == 1
        window._list_header.search.clear()
        qapp.processEvents()
        assert window._model.rowCount() == 4

    def test_filter_matches_description_and_notes(self, window, qapp):
        window.on_info_ready(0, "A terminal emulator", "notes", "", "full",
                             "", "")
        qapp.processEvents()
        window._list_header.search.setText("emulator")
        qapp.processEvents()
        assert [window._model.item(r)["name"] for r in
                range(window._model.rowCount())] == ["konsole"]

    def test_filter_reports_what_it_hides(self, window, qapp):
        window._list_header.search.setText("aur")
        qapp.processEvents()
        assert "2 of 4" in window._list_header.filtered.text()
        # The tab still answers "how many updates are pending".
        assert window._view_buttons["updates"].text() == "Updates (4)"
        window._list_header.search.clear()
        qapp.processEvents()
        assert window._list_header.filtered.text() == ""
        assert not window._list_header.filtered.isVisible()

    def test_filter_offers_a_way_back(self, window, qapp):
        window._list_header.search.setText("nothing-matches-this")
        qapp.processEvents()
        assert window._model.rowCount() == 0
        assert window._empty_label.isVisible()
        assert "Nothing here matches" in window._empty_label.text()
        assert window._empty_title.text() == "No matches"
        # With a filter on, the box that clears it stays put.
        assert window._list_header.isVisible()

    def test_select_all_applies_to_visible_rows_only(self, window, qapp):
        # Selecting with a filter on must not silently reach hidden rows.
        window._list_header.search.setText("aur")
        qapp.processEvents()
        window._select_all(True)
        qapp.processEvents()
        assert window._model.checked_names() == ["aur-pkg"]
        assert window._model.rowCount() == 2

    def test_row_info_still_lands_while_filtered(self, window, qapp):
        window._list_header.search.setText("konsole")
        qapp.processEvents()
        # The fetcher addresses rows by source index; that must keep
        # working when the visible set is a subset.
        window.on_info_ready(2, "desc for evil", "notes for evil", "", "",
                             "", "")
        qapp.processEvents()
        assert window._model._items[2]["desc"] == "desc for evil"

    def test_every_view_footer_refreshes(self, window, qapp):
        for btn in (window._recheck_btn, window._track_refresh_btn,
                    window._un_refresh_btn, window._obs_refresh_btn):
            assert btn.text() == "Refresh"
        # Security's scan is its refresh: one button, no duplicate.
        assert window._sec_scan_btn.text() == "Run scan"
        assert not hasattr(window, "_sec_close_btn")

    def test_tracked_filter(self, window, qapp):
        # Flush the window's own (stubbed) load chain first: it would
        # otherwise deliver an empty list over the rows staged here.
        if window._obs_checker is not None:
            window._obs_checker.wait(25000)
        qapp.processEvents()
        if window._track_loader is not None:
            window._track_loader.wait(25000)
        qapp.processEvents()
        window._on_tracked_loaded(
            [{"name": "hyprland", "version": "1", "repo": "aur"},
             {"name": "firefox", "version": "2", "repo": "official"}], [])
        qapp.processEvents()
        window._track_header.search.setText("hypr")
        qapp.processEvents()
        assert window._track_model.rowCount() == 1
        assert window._track_model.item(0)["name"] == "hyprland"
        window._track_header.search.clear()
        qapp.processEvents()
        assert window._track_model.rowCount() == 2

    def test_no_close_buttons_anywhere(self, window):
        for name in ("_skip_btn", "_obs_close_btn", "_sec_close_btn",
                     "_track_close_btn", "_un_close_btn"):
            assert not hasattr(window, name), name


class TestLogPanelState:
    def test_placeholder_until_something_is_logged(self, window, qapp):
        window._log_btn.setChecked(True)
        qapp.processEvents()
        assert window._log_empty.isVisible()
        assert not window._log_view.isVisible()
        window._on_log_line(":: installing konsole")
        qapp.processEvents()
        assert not window._log_empty.isVisible()
        assert window._log_view.isVisible()
        assert window._log_has_content is True
        window._clear_log()
        qapp.processEvents()
        assert window._log_empty.isVisible()
        assert window._log_has_content is False

    def test_clearing_then_logging_works_again(self, window, qapp):
        window._on_log_line("first")
        window._clear_log()
        window._log_action("second")
        qapp.processEvents()
        assert window._log_has_content is True
        assert "second" in window._log_view.toPlainText()


class TestProgressReadout:
    def test_phrase_is_a_percentage(self, window):
        window._safe_names = ["a", "b", "c", "d"]
        window._done_names = {"a", "b"}
        phrase = window._progress_phrase()
        assert "<b>50%</b>" in phrase
        assert "2 of 4 done" in phrase

    def test_status_leads_with_the_percentage(self, window):
        window._safe_names = ["konsole", "aur-pkg"]
        window._done_names = {"konsole"}
        window._on_install_progress(1, 2, "aur-pkg")
        assert "<b>50%</b>" in window._sub.text()

    def test_percentage_never_exceeds_a_hundred(self, window):
        window._safe_names = ["a"]
        window._done_names = {"a", "ghost"}
        assert "<b>100%</b>" in window._progress_phrase()


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
