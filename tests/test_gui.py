"""Window-level smoke tests + scroll-performance sanity (offscreen Qt)."""
import time

import pytest

from PyQt6.QtCore import QObject, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QMouseEvent
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
    row_ready = pyqtSignal(int, str, str, str, str, str, str, str, str, str)
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

    def test_tab_texts_and_counts(self, window, qapp):
        # Tabs left to right: Updates | Tracked | Untracked | Obsolete |
        # Security.
        assert not hasattr(window, "_search")
        # The obsolete preload may land before or after this test; drive
        # it so the tab text is deterministic.
        window._on_obs_preload_done([])
        qapp.processEvents()
        tabs = window._view_buttons
        assert [b.text() for b in
                (tabs["updates"], tabs["tracked"], tabs["untracked"],
                 tabs["obsolete"], tabs["security"])] == [
            "Updates (4)", "Tracked (0)", "Untracked (0)", "Obsolete (0)",
            "Security"]
        assert tabs["updates"].isChecked()
        head = window.centralWidget().layout().itemAt(0).layout()
        order = [head.indexOf(tabs[k]) for k in
                 ("updates", "tracked", "untracked", "obsolete", "security")]
        assert order == sorted(order)
        assert head.indexOf(window._log_btn) > order[-1]

    def test_pages_have_no_summary_lines(self, window, qapp):
        # The numbers live in the stat strip; loading info lives on the
        # one status line, top left. No page carries a line of its own.
        for gone in ("_track_summary", "_un_summary", "_obs_summary",
                     "_sec_status"):
            assert not hasattr(window, gone)

    def test_a_load_reports_on_the_status_line(self, window, qapp):
        # A list reading itself says so at the top left — while you are
        # looking at it, which is where the page's own loading line used
        # to sit. The box inside the frame says it too.
        window._switch_view("tracked")
        window._track_loaded = False
        window._track_loading = True
        window._refresh_tracked_summary()
        assert "Reading the installed package list" in window._sub.text()
        # Leaving the page hands the line back to the resting summary…
        window._switch_view("updates")
        assert "Reading the installed package list" not in window._sub.text()
        # …and coming back shows it again, until the load is done.
        window._switch_view("tracked")
        assert "Reading the installed package list" in window._sub.text()
        window._track_loading = False
        window._track_loaded = True
        window._refresh_tracked_summary()
        assert "Reading the installed package list" not in window._sub.text()

    def test_a_load_never_steals_the_line_from_a_job(self, window, qapp):
        # Background reads are quiet: a check or install owns the line,
        # and a load ending must not wipe that message either.
        window._switch_view("tracked")
        window._set_status("Installing <b>konsole</b> — 50%", "info")
        window._track_loaded = False
        window._track_loading = True
        window._refresh_tracked_summary()
        assert "Installing" in window._sub.text()
        assert "Reading" not in window._sub.text()
        window._track_loading = False
        window._track_loaded = True
        window._refresh_tracked_summary()
        assert "Installing" in window._sub.text()
        # The job is what ends it, and then the resting text is back.
        window._set_status("")
        assert "Installing" not in window._sub.text()
        assert "Reading" not in window._sub.text()

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
            "full", "", "", "extra")
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
        # The count stays at zero: dropping it would move the switcher.
        assert window._view_buttons["updates"].text() == "Updates (0)"
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
        assert window._view_buttons["updates"].text() == "Updates (0)"

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
        assert "No packages selected" in window._up_footer_label.text()
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
        # the slim progress track lives in the header, under the status line
        assert window._progress.parent() is not None
        assert window._progress.geometry().bottom() < \
            window._content_stack.geometry().top()
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
        # The control row reads: filter field, the page's cards, Select all.
        row = window._controls.layout()
        assert row.indexOf(window._controls.search) == 0
        assert row.indexOf(window._controls.cells) == 1
        assert row.indexOf(window._controls.check) > 1

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

    def test_status_area_opens_the_log(self, window, qapp):
        # The status area — line and track — is the shortcut to the log;
        # the log itself is only ever drawn in the side panel.
        assert not hasattr(window, "_log_preview")
        assert window._sub.isVisible()
        assert window._progress.isVisible()
        window._on_log_line(":: installing konsole (1/2)")
        qapp.processEvents()
        assert "konsole" in window._sub.toolTip()   # latest line on hover
        # HTML-unsafe log output must not turn the tooltip into markup.
        window._on_log_line("<b>not a tag</b> & done")
        assert "&lt;b&gt;not a tag&lt;/b&gt; &amp; done" in \
            window._sub.toolTip()
        assert "Click to open the live log" in window._sub.toolTip()
        assert not window._log_panel.isVisible()
        # A click on the line opens the panel…
        self._click(window._sub, qapp)
        assert window._log_panel.isVisible()
        window._log_btn.setChecked(False)
        qapp.processEvents()
        # …and so does a click on the progress track under it.
        self._click(window._progress, qapp)
        assert window._log_panel.isVisible()
        window._on_log_toggled(False)

    @staticmethod
    def _click(widget, qapp):
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease,
                         QPointF(8, 8), QPointF(8, 8),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(widget, ev)

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

    def test_progress_track_is_the_header_hairline(self, window):
        window.on_fetch_done()
        # Idle: an empty track, never a full green bar before work ran.
        assert window._progress.maximum() == 1
        assert window._progress.value() == 0
        # It sits in the header, above the content, not above the footer.
        assert window._progress.geometry().bottom() < \
            window._content_stack.geometry().top()

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

    def test_show_more_unfolds_the_notes_in_place(self, window, qapp):
        window._model.set_info(0, "d", "short", "2026-01-01",
                               "line one\nline two\nline three",
                               "GitHub release", "https://example.com")
        d = window._delegate
        h0 = d._layout(0, window._model.item(0), 900)["height"]
        window._on_more_requested(0)
        qapp.processEvents()
        lay = d._layout(0, window._model.item(0), 900)
        assert lay["height"] > h0          # the card grew to hold it all
        assert lay["more_txt"] == "Show less  ↑"
        window._on_more_requested(0)
        assert d._layout(0, window._model.item(0), 900)["height"] == h0
        # Never a dialog: the notes open inside the card itself.
        assert not hasattr(window, "show_popover")
        assert not hasattr(app, "NotesPopover")

    def test_a_huge_changelog_stops_at_the_cap_and_says_so(self, window):
        window._model.set_info(
            0, "d", "short", "2026-01-01",
            "\n".join(f"line {i}" for i in range(30)),
            "GitHub release", "https://example.com")
        d = window._delegate
        window._on_more_requested(0)
        lay = d._layout(0, window._model.item(0), 900)
        assert lay["hint_txt"] == "… 17 more lines"
        spacing = QFontMetrics(d._f_text).lineSpacing()
        assert lay["notes"].height() <= (
            app.PackageDelegate.NOTES_LINES * spacing + 8)
        assert lay["hint"].top() >= lay["notes"].top()

    def test_the_fetcher_swaps_a_guessed_repo_for_the_real_one(self, window):
        # checkupdates prints no repo, so every repo row starts out
        # "official". The fetcher reads the truth off pacman itself, and
        # the pill has to follow it.
        window.on_info_ready(0, "desc", "notes", "2026-01-01", "full",
                             "CachyOS release", "https://example.com",
                             "cachyos-extra-v3")
        assert window._model.item(0)["repo"] == "cachyos-extra-v3"
        lay = window._delegate._layout(0, window._model.item(0), 900)
        assert lay["badge_txt"] == "CACHYOS"

    def test_a_feed_without_a_repo_leaves_the_pill_alone(self, window):
        before = window._model.item(0)["repo"]
        window.on_info_ready(0, "desc", "notes", "2026-01-01", "full",
                             "", "", "")
        assert window._model.item(0)["repo"] == before

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
        assert w._controls.isVisible()
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
        # This test feeds the preload by hand: silence the stubbed checker
        # thread, or its empty result can land after these names and wipe
        # them (see _on_obs_preload_done).
        monkeypatch.setattr(app.ObsoleteChecker, "start",
                            lambda self: None)
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
        assert window._obs_empty.title.text() == "Nothing to clean up"

    def test_obsolete_switch_never_blocks(self, window, qapp, monkeypatch):
        # Even if pacman hangs, switching tabs must return instantly:
        # computation happens in the checker thread, never on the GUI.
        def _boom():
            raise AssertionError("must not run on GUI thread")

        monkeypatch.setattr(app, "get_obsolete_packages", _boom)
        window._obs_loaded = False
        window._switch_view("obsolete")  # would raise if sync
        # The page owns no loading line of its own: the status line says
        # so at once, and the box keeps the frame (see AGENTS.md).
        assert "no repository provides" in window._sub.text()
        qapp.processEvents()
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
        # The scan reports itself on the status line, and its progress
        # rides the header track (the page has no line of its own).
        assert "Scanning installed packages" in window._sub.text()
        window._sec_scanner.progress.emit(1, 2, "evil")
        qapp.processEvents()
        assert "Scanning… 1/2" in window._sub.text()
        assert window._progress.value() == 1
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
        assert window._progress.value() == window._progress.maximum()
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
             {"name": "a-off", "version": "2-1", "repo": "official"}])
        qapp.processEvents()
        assert window._track_model.rowCount() == 2
        # Alphabetical by the name on screen.
        assert window._track_model.item(0)["name"] == "a-off"
        assert window._view_buttons["tracked"].text() == "Tracked (2)"
        window._switch_view("tracked")
        assert window._content_stack.currentIndex() == 1
        assert window._footer_stack.currentIndex() == 1

    def test_activity_log_records_actions(self, window, qapp):
        window._log_action("Removing foo…")
        window._log_action("Opened bar")
        text = window._log_view.toPlainText()
        assert "◆ Removing foo…" in text
        assert "◆ Opened bar" in text
        # Preview follows the latest line; the panel stays shut.
        assert "Opened bar" in window._sub.toolTip()
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

    def _stage_tracked(self, window, qapp):
        self._settle_track_loader(window, qapp)
        window._on_tracked_loaded(
            [{"name": "del-me", "version": "1-1", "repo": "aur"}])
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
            [{"name": "del-me", "version": "1-1", "repo": "aur"}])
        window._track_uninstaller = FakeRunner()
        window._request_track_uninstall("del-me")
        assert "already running" in window._sub.text()
        window._track_uninstaller = None

    def _stage_untracked(self, window, qapp):
        apps = [app._tracked_row("firefox", "1.0-1", "official",
                                 title="Firefox", desktop="/u/firefox.desktop",
                                 desc="Web browser", kind="app")]
        pkgs = [app._tracked_row("libfoo", "2-1", "official",
                                 kind="dependency")]
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
        assert window._un_model.rowCount() == 2   # one app + one package
        assert window._view_buttons["untracked"].text() == "Untracked (2)"
        # The strip carries the numbers; the page has no summary line.
        assert window._stat_un_apps._value.text() == "1"
        assert window._un_stat_cards["dependencies"]._value.text() == "1"
        # App row shows the title; ops use the package name.
        d = window._un_view.itemDelegate()
        from PyQt6.QtWidgets import QStyleOptionViewItem as _Opt
        idx = window._un_model.index(0)
        opt = _Opt()
        opt.rect = window._un_view.visualRect(idx)
        opt.palette = window._un_view.palette()
        opt.font = window._un_view.font()
        lay = d._geom(0, window._un_model.item(0), opt.rect.width())
        assert lay["title"] == "Firefox"

    def test_untracked_batch_footer(self, window, qapp):
        self._stage_untracked(window, qapp)
        assert window._un_uninstall_btn.text() == "Uninstall selected"
        assert not window._un_uninstall_btn.isEnabled()
        window._un_model.set_all(True)
        window._refresh_untracked_footer()
        qapp.processEvents()
        # One list now: the app and the dependency package are both here.
        assert window._un_uninstall_btn.text() == "Uninstall 2 selected"
        assert window._un_uninstall_btn.isEnabled()
        assert "All 2" in window._un_footer_label.text()

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
        row = window._un_model.row_of("libfoo")
        assert window._un_model.item(row)["state"] == "uninstalling"
        window._untracked_uninstaller.done.emit(True, "")
        qapp.processEvents()
        assert window._un_model.row_of("libfoo") is None
        assert window._un_model.row_of("firefox") is not None  # untouched
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
             {"name": "b", "version": "1", "repo": "extra"}])
        qapp.processEvents()
        # Nothing checked: the button says what it is and is off.
        assert window._track_uninstall_btn.text() == "Uninstall selected"
        assert not window._track_uninstall_btn.isEnabled()
        # Check rows → the button names the action it will take.
        window._track_model.set_all(True)
        window._refresh_tracked_footer()
        qapp.processEvents()
        assert window._track_uninstall_btn.text() == "Uninstall 2 selected"
        assert window._track_uninstall_btn.isEnabled()
        # The control row carries the selection state: ticked when
        # everything in view is selected; the footer says it in words.
        assert window._controls.check.checkState() == \
            Qt.CheckState.Checked
        assert "All 2 packages selected" in window._track_footer_label.text()
        window._select_tracked_all(False)
        assert window._track_uninstall_btn.text() == "Uninstall selected"
        assert not window._track_uninstall_btn.isEnabled()

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
             {"name": "b", "version": "1", "repo": "extra"}])
        window._track_model.set_all(True)
        window._refresh_tracked_footer()
        assert window._track_uninstall_btn.text() == "Uninstall 2 selected"
        window._track_uninstall_btn.click()
        assert isinstance(window._track_uninstaller, FakeRunner)
        assert window._track_uninstall_names == ["a", "b"]
        window._track_uninstaller.done.emit(True, "")
        qapp.processEvents()
        assert window._track_model.rowCount() == 0
        assert "uninstalled" in window._sub.text()

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
        assert "Upkeep" in box.title
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
            [{"name": "perplexity", "version": "1.6.0-1", "repo": "aur"}])
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

    STRUCTURE = ("_stats_row", "_controls", "_list", "_sub", "_progress")

    def _snap(self, window, skip=()):
        snap = {name: getattr(window, name).geometry()
                for name in self.STRUCTURE if name not in skip}
        # The switcher must not move either: a tab that gains or loses a
        # count would shove its neighbours (see _update_view_badges).
        for key, btn in window._view_buttons.items():
            snap[f"tab:{key}"] = btn.geometry()
        snap["_log_btn"] = window._log_btn.geometry()
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
        # exactly that shift), so their followers _controls/_list are
        # out of scope for this phase — and must land back on `before`
        # when the check returns rows (asserted below, strongest form).
        rows_snapshot = {k: v for k, v in before.items()
                         if k not in ("_controls", "_list")}
        window._on_recheck_empty()
        qapp.processEvents()
        assert window._empty_title.text() == "You're up to date"
        assert not window._slot_security.isVisible()
        assert self._snap(window, skip=("_controls", "_list")) \
            == rows_snapshot
        window._recheck_updates()
        qapp.processEvents()
        assert window._empty_state.isVisible()
        assert window._empty_title.text() == "Loading…"
        assert window._empty_label.text() == "Checking for updates…"
        assert self._snap(window, skip=("_controls", "_list")) \
            == rows_snapshot

        # …and lands back on rows, in the same place — banners restored.
        window._on_recheck_found(_updates())
        qapp.processEvents()
        assert not window._empty_state.isVisible()
        assert window._slot_security.isVisible()
        assert self._snap(window) == before

    def test_the_switcher_keeps_its_width_when_the_count_drops(
            self, window, qapp, monkeypatch):
        # 13 updates, refresh, 0: the tab keeps the shape of its count, so
        # neither it nor the status area beside it moves (see AGENTS.md).
        monkeypatch.setattr(app, "UpdateChecker", self._SilentChecker)
        window.resize(1280, 800)
        qapp.processEvents()
        tabs = window._view_buttons
        tab_w = tabs["updates"].width()
        status_w = window._sub.width()
        window._on_recheck_empty()
        qapp.processEvents()
        assert tabs["updates"].text() == "Updates (0)"
        assert tabs["updates"].width() == tab_w
        assert window._sub.width() == status_w

    def test_log_line_does_not_move_the_list(self, window, qapp):
        before = window._list.geometry()
        window._on_log_line(":: installing konsole (1/2)")
        qapp.processEvents()
        # The line is used (in the status tooltip)…
        assert "konsole" in window._sub.toolTip()
        assert window._list.geometry() == before  # …but nothing moved

    def test_filter_does_not_move_the_layout(self, window, qapp):
        window.resize(1280, 800)
        qapp.processEvents()
        before = self._snap(window)
        window._controls.search.setText("aur")
        qapp.processEvents()
        assert self._snap(window) == before
        window._controls.search.clear()
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

    def test_banner_counts_follow_the_list(self, window, qapp):
        window._swap_updates_model(_updates(4), {})
        qapp.processEvents()
        assert window._slot_security.isVisible()
        assert "1 compromised" in self._banner_text(window._slot_security)
        window._swap_updates_model(_updates(8), {})
        qapp.processEvents()
        assert "2 compromised" in self._banner_text(window._slot_security)

    def test_banner_never_moves_the_switcher(self, window, qapp):
        """The banners sit *below* the control row, so the tabs stay put."""
        window._swap_updates_model([], {})
        window._mark_checked()
        window._refresh_empty_state()
        qapp.processEvents()
        assert not window._slot_security.isVisible()
        tabs = {k: b.geometry() for k, b in window._view_buttons.items()}
        row = window._controls.geometry()
        window._swap_updates_model(_updates(4), {})
        qapp.processEvents()
        assert window._slot_security.isVisible()
        assert {k: b.geometry()
                for k, b in window._view_buttons.items()} == tabs
        assert window._controls.geometry() == row

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
        window._un_model.set_items([
            app._tracked_row("zed", "1.0-1", "aur")])
        window._log_btn.setChecked(True)
        qapp.processEvents()
        new_w = window._track_list.viewport().width()
        d = window._track_list.itemDelegate()
        assert d is not window._list.itemDelegate()
        idx = window._un_model.index(0)
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
        w._on_tracked_loaded([])
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
        for holder in (w._un_empty,):
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
        w._controls.search.setText("nothing-matches")
        qapp.processEvents()
        assert w._empty_state.isVisible()
        assert w._controls.isVisible()
        assert w._empty_title.text() == "No matches"
        w.close()


class TestControlRowAndFilter:
    """The window's one control row: the filter field and the filter cards.

    The row is shared by all five pages: the field on the left, the page's
    own stat cells (numbers that double as source filters) right beside
    it, Select all at the far right. It holds its shape when pages change;
    the pages that have nothing to filter hide the whole row. The count of
    what is selected lives in the list footer instead (see AGENTS.md).
    """

    def test_row_holds_the_pages_cards_next_to_the_filter(
            self, window, qapp):
        left = window._left_col
        widgets = [left.itemAt(i).widget() for i in range(left.count())]
        assert window._controls in widgets
        assert window._content_stack in widgets
        assert widgets.index(window._controls) < \
            widgets.index(window._content_stack)
        # The current page's cards sit *in* the row, beside the field,
        # and its list below it.
        assert window._controls.cells.currentWidget() is window._stats_row
        assert window._content_stack.currentWidget() is window._up_page
        # ...and the footer no longer carries All/None buttons.
        assert not hasattr(window, "_select_all_btn")
        assert not hasattr(window, "_select_none_btn")

    def test_the_cards_are_the_source_filters(self, window, qapp):
        """Each cell in the row filters the list it stands for."""
        window._toggle_update_filter("aur")
        qapp.processEvents()
        assert window._model.filter_source() == "aur"
        assert window._stat_aur.property("active") is True
        assert window._stat_ready.property("active") is False
        window._toggle_update_filter("aur")
        qapp.processEvents()
        assert window._model.filter_source() == ""
        assert window._stat_aur.property("active") is False

    def test_selection_text_reads_in_english(self):
        """One package is not "All 1 packages selected"."""
        assert app._selection_text(0, 0) == "Nothing selected"
        assert app._selection_text(0, 3) == "No packages selected"
        assert app._selection_text(1, 1) == "1 package selected"
        assert app._selection_text(2, 2) == "All 2 packages selected"
        assert app._selection_text(1, 3) == "1 of 3 selected"

    def test_switcher_stays_put_across_pages(self, window, qapp):
        """The tabs are in the window header: pages cannot move them."""
        before = {k: b.geometry() for k, b in window._view_buttons.items()}
        for key in ("tracked", "untracked", "obsolete", "security",
                    "updates"):
            window._switch_view(key)
            qapp.processEvents()
            assert {k: b.geometry()
                    for k, b in window._view_buttons.items()} == before

    def test_the_filter_field_does_not_move_between_pages(self, window,
                                                          qapp):
        """The field leads the row on every page that has one, and the
        row steps aside on the two pages that have nothing to filter."""
        places = []
        for key in ("updates", "tracked", "untracked"):
            window._switch_view(key)
            qapp.processEvents()
            assert window._controls.isVisible()
            places.append(window._controls.search.geometry())
        assert places[0] == places[1] == places[2]
        for key in ("obsolete", "security"):
            window._switch_view(key)
            qapp.processEvents()
            # Nothing to filter, nothing to tick: no row at all, so the
            # page starts at the top of the window.
            assert not window._controls.isVisible()
        window._switch_view("updates")
        qapp.processEvents()

    def test_untracked_search_filters_that_page(self, window, qapp):
        """The one filter field serves the Untracked list too."""
        if window._obs_checker is not None:
            window._obs_checker.wait(25000)
        qapp.processEvents()
        if window._track_loader is not None:
            window._track_loader.wait(25000)
        qapp.processEvents()
        window._on_untracked_loaded(
            [app._tracked_row("firefox", "1.0-1", "official",
                              title="Firefox", desc="Web browser",
                              kind="app"),
             app._tracked_row("libfoo", "2-1", "official",
                              kind="dependency")], [])
        window._switch_view("untracked")
        qapp.processEvents()
        window._controls.search.setText("firefox")
        qapp.processEvents()
        assert window._un_model.rowCount() == 1
        assert window._un_model.item(0)["name"] == "firefox"
        # Nothing ticked: the footer says so, next to the list's total.
        assert window._un_footer_label.text() == \
            "1 untracked · No packages selected"
        # A filter that hides everything says so, instead of claiming the
        # machine has nothing pacman misses.
        window._controls.search.setText("nothing-matches")
        qapp.processEvents()
        assert window._un_model.rowCount() == 0
        assert window._un_empty.title.text() == "No matches"
        window._controls.search.clear()
        qapp.processEvents()
        window._switch_view("updates")

    def test_tracked_empty_filter_says_no_matches(self, window, qapp):
        if window._obs_checker is not None:
            window._obs_checker.wait(25000)
        qapp.processEvents()
        if window._track_loader is not None:
            window._track_loader.wait(25000)
        qapp.processEvents()
        window._on_tracked_loaded(
            [{"name": "hyprland", "version": "1", "repo": "aur"}])
        window._switch_view("tracked")
        qapp.processEvents()
        window._controls.search.setText("nothing-matches")
        qapp.processEvents()
        assert window._track_model.rowCount() == 0
        assert window._track_empty.title.text() == "No matches"
        window._controls.search.clear()
        qapp.processEvents()
        window._switch_view("updates")

    def test_select_all_checkbox_drives_selection(self, window, qapp):
        window._select_all(False)
        qapp.processEvents()
        assert window._controls.check.checkState() == \
            Qt.CheckState.Unchecked
        window._controls.check.click()
        qapp.processEvents()
        assert window._controls.check.checkState() == Qt.CheckState.Checked
        assert window._model.selected_count() == window._model.checkable_count()
        window._controls.check.click()
        qapp.processEvents()
        assert window._controls.check.checkState() == \
            Qt.CheckState.Unchecked
        assert window._model.selected_count() == 0

    def test_partial_selection_shows_a_count(self, window, qapp):
        window._select_all(False)
        window._on_row_toggled(0)
        qapp.processEvents()
        assert window._controls.check.checkState() == \
            Qt.CheckState.PartiallyChecked
        # The count lives in the footer now, next to the action it feeds.
        assert window._up_footer_label.isVisible()
        assert "1 of 3 selected" in window._up_footer_label.text()

    def test_filter_hides_non_matching_rows(self, window, qapp):
        window._controls.search.setText("aur")
        qapp.processEvents()
        assert window._model.rowCount() == 2
        names = [window._model.item(r)["name"] for r in
                 range(window._model.rowCount())]
        assert names == ["aur-pkg", "evil"]
        window._controls.search.setText("konsole")
        qapp.processEvents()
        assert window._model.rowCount() == 1
        window._controls.search.clear()
        qapp.processEvents()
        assert window._model.rowCount() == 4

    def test_filter_matches_description_and_notes(self, window, qapp):
        window.on_info_ready(0, "A terminal emulator", "notes", "", "full",
                             "", "")
        qapp.processEvents()
        window._controls.search.setText("emulator")
        qapp.processEvents()
        assert [window._model.item(r)["name"] for r in
                range(window._model.rowCount())] == ["konsole"]

    def test_filter_reports_what_it_hides(self, window, qapp):
        cards = {k: c.geometry() for k, c in window._update_stat_cards.items()}
        window._controls.search.setText("aur")
        qapp.processEvents()
        assert "2 of 4" in window._controls.filtered.text()
        # The tab still answers "how many updates are pending".
        assert window._view_buttons["updates"].text() == "Updates (4)"
        # Saying how much is hidden must not resize the cards beside it:
        # the info keeps its reserved slot either way.
        assert {k: c.geometry()
                for k, c in window._update_stat_cards.items()} == cards
        window._controls.search.clear()
        qapp.processEvents()
        assert window._controls.filtered.text() == ""
        assert window._controls.filtered.isVisible()
        assert {k: c.geometry()
                for k, c in window._update_stat_cards.items()} == cards

    def test_filter_offers_a_way_back(self, window, qapp):
        window._controls.search.setText("nothing-matches-this")
        qapp.processEvents()
        assert window._model.rowCount() == 0
        assert window._empty_label.isVisible()
        assert "Nothing here matches" in window._empty_label.text()
        assert window._empty_title.text() == "No matches"
        # With a filter on, the box that clears it stays put.
        assert window._controls.isVisible()

    def test_select_all_applies_to_visible_rows_only(self, window, qapp):
        # Selecting with a filter on must not silently reach hidden rows.
        window._controls.search.setText("aur")
        qapp.processEvents()
        window._select_all(True)
        qapp.processEvents()
        assert window._model.checked_names() == ["aur-pkg"]
        assert window._model.rowCount() == 2

    def test_row_info_still_lands_while_filtered(self, window, qapp):
        window._controls.search.setText("konsole")
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
             {"name": "firefox", "version": "2", "repo": "official"}])
        qapp.processEvents()
        # One filter field, five pages: it filters whichever page is up.
        window._switch_view("tracked")
        qapp.processEvents()
        window._controls.search.setText("hypr")
        qapp.processEvents()
        assert window._track_model.rowCount() == 1
        assert window._track_model.item(0)["name"] == "hyprland"
        window._controls.search.clear()
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


class _FakeBtn(QObject):
    """Minimal push-button stand-in for the confirmation box."""
    clicked = pyqtSignal()

    def __init__(self, text):
        super().__init__()
        self._text = text

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text


class _AcceptingBox:
    """A QMessageBox that always reports 'the user pressed Enable'."""

    class Icon:
        Question = 0
        Warning = 1
        Information = 2
        Critical = 3

    class ButtonRole:
        AcceptRole = 0
        ActionRole = 1
        RejectRole = 2

    class StandardButton:
        Close = 0
        Ok = 1
        Cancel = 2

    def __init__(self, parent=None):
        self._accept = None
        self.buttons = []

    def setIcon(self, *_a):
        pass

    def setWindowTitle(self, *_a):
        pass

    def setText(self, *_a):
        pass

    def setInformativeText(self, *_a):
        pass

    def setDetailedText(self, *_a):
        pass

    def setStyleSheet(self, *_a):
        pass

    def addButton(self, text, role=0):
        if not isinstance(text, str):
            text = "Close"      # StandardButton overload
        btn = _FakeBtn(text)
        self.buttons.append(btn)
        if role == self.ButtonRole.AcceptRole:
            self._accept = btn
        return btn

    def exec(self):
        return 0

    def clickedButton(self):
        return self._accept


class _CancellingBox(_AcceptingBox):
    """A QMessageBox where the user pressed Cancel (or closed it)."""

    def clickedButton(self):
        return None


@pytest.fixture()
def sources_dialog(qapp, monkeypatch):
    """A ManagedSourcesDialog with no real pacman.conf and a fake runner."""
    monkeypatch.setattr(app, "enabled_repos", lambda: set())
    monkeypatch.setattr(app, "_flatpak_ready", lambda: False)
    monkeypatch.setattr(app, "_SourceEnableRunner", FakeSourceEnableRunner)
    monkeypatch.setattr(app, "QMessageBox", _AcceptingBox)
    dlg = app.ManagedSourcesDialog()
    yield dlg
    dlg.close()


def _enable_card(dlg):
    """The first card that offers an Enable button, with that button."""
    for w in dlg.findChildren(app.QWidget):
        btn = getattr(w, "_enable_btn", None)
        if btn is not None:
            return w, btn
    raise AssertionError("no source card offered an Enable button")


class TestManagedSourcesEnable:
    """The inline enable flow: one card, no modal progress, honest state."""

    def test_enable_is_inline_and_updates_every_state_label(
            self, sources_dialog):
        d = sources_dialog
        card, btn = _enable_card(d)
        fired = []
        d.restart_needed.connect(lambda: fired.append(True))

        d._enable_source("Chaotic-AUR", "#!/bin/bash\necho hi", card, btn)

        # Progress lives inside the card; the button becomes the state.
        assert btn.text() == "Enabling…"
        assert not btn.isEnabled()
        assert card._bar.property("state") == "running"
        assert card._status_lbl.text() == "Starting…"
        runner = d._enable_runner
        assert isinstance(runner, FakeSourceEnableRunner)
        runner.line.emit("installing keyring")
        assert card._status_lbl.text() == "installing keyring"

        runner.done.emit(True, "all good")

        assert btn.text() == "Enabled ✓"
        assert not btn.isEnabled()
        assert "enabled ✓" in card._status_lbl.text()
        assert card._bar.property("state") == "done"
        assert d._enable_runner is None
        assert fired

    def test_success_flips_the_badge_too(self, sources_dialog):
        # The bug the user hit: button said Enabled while the badge still
        # said NOT ENABLED. Both must move together.
        d = sources_dialog
        card, btn = _enable_card(d)
        assert card._badge.text() == "NOT ENABLED"
        assert card._badge.property("tint") == "muted"

        d._enable_source("Chaotic-AUR", "#!/bin/bash\necho hi", card, btn)
        d._enable_runner.done.emit(True, "")

        assert card._badge.text() == "ENABLED"
        assert card._badge.property("tint") == "positive"
        assert btn.text() == "Enabled ✓"

    def test_failure_keeps_the_badge_and_offers_a_retry(self, sources_dialog):
        d = sources_dialog
        card, btn = _enable_card(d)

        d._enable_source("archlinuxcn", "#!/bin/bash\nexit 1", card, btn)
        d._enable_runner.done.emit(
            False, "error: target not found: archlinuxcn-keyring\nboom")

        assert btn.text() == "Enable"
        assert btn.isEnabled()
        assert card._badge.text() == "NOT ENABLED"
        assert card._badge.property("tint") == "muted"
        assert card._status_lbl.text().startswith("Failed:")
        assert "boom" in card._status_lbl.text()
        assert card._bar.property("state") == "failed"

    def test_cancel_runs_nothing(self, sources_dialog, monkeypatch):
        d = sources_dialog
        monkeypatch.setattr(app, "QMessageBox", _CancellingBox)
        card, btn = _enable_card(d)

        d._enable_source("Chaotic-AUR", "#!/bin/bash\necho hi", card, btn)

        assert btn.text() == "Enable"
        assert btn.isEnabled()
        assert card._status_lbl.text() == card._desc
        assert card._bar.property("state") == "idle"
        assert getattr(d, "_enable_runner", None) is None

    def test_no_state_change_moves_the_card(self, sources_dialog):
        # The UI law: showing progress must not move anything. The status
        # takes the description's fixed slot and the track is always there.
        d = sources_dialog
        d.show()
        card, btn = _enable_card(d)
        before = card.height()

        d._enable_source("Chaotic-AUR", "#!/bin/bash\necho hi", card, btn)
        assert card.height() == before, "progress moved the card"

        d._enable_runner.line.emit("some quite long pacman output line here")
        assert card.height() == before

        d._enable_runner.done.emit(True, "")
        assert card.height() == before

    def test_every_card_is_the_same_height(self, sources_dialog):
        # Uniform cards: one slot for the description, one for the track.
        d = sources_dialog
        d.show()
        cards = [w for w in d.findChildren(app.QWidget)
                 if hasattr(w, "_bar")]
        assert len(cards) >= 4
        heights = {c.height() for c in cards}
        assert len(heights) == 1, f"cards differ: {heights}"


class TestAutoRowText:
    """Auto-added dependency rows explain themselves exactly once."""

    def test_auto_row_has_no_version_text_to_overlap(self, qapp):
        from PyQt6.QtWidgets import QListView
        m = app.PackageModel(_updates(1), {})
        r = m.ensure_row("libfoo")
        item = m.item(r)
        assert item["auto"]
        v = QListView()
        v.resize(1240, 800)
        v.setModel(m)
        d = app.PackageDelegate(v)
        v.setItemDelegate(d)
        lay = d._layout(r, item, 1240)
        # The version slot is empty: the description is the only text on
        # that line, so the two can never print on top of each other.
        assert lay["ver_old"] == ""
        assert lay["ver_new"] == ""
        assert lay["desc_txt"]

    def test_auto_row_description_is_one_clean_sentence(self, qapp):
        m = app.PackageModel(_updates(1), {})
        item = m.item(m.ensure_row("libfoo"))
        assert item["desc"] == (
            "Added automatically as a dependency while installing — "
            "it wasn't in the update list.")
        assert "No changelog for auto-added packages." == item["notes"]


class FakeRemover(QObject):
    """ObsoleteRemover stub that records being started, never runs."""
    line = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    started: list = []

    def __init__(self, packages=None, force=False, *a, **k):
        super().__init__()
        self.packages = list(packages or [])
        self.force = force
        FakeRemover.started.append(self)

    def isRunning(self):
        return False

    def start(self):
        pass

    def terminate(self):
        pass

    def wait(self, ms=0):
        return True


@pytest.fixture()
def busy_window(qapp, monkeypatch):
    """A window with one tracked package, no real removals."""
    FakeRemover.started = []
    monkeypatch.setattr(app, "_notify_update", lambda *a, **k: 0)
    monkeypatch.setattr(app, "_notify_close", lambda *a, **k: None)
    monkeypatch.setattr(app, "ObsoleteRemover", FakeRemover)
    monkeypatch.setattr(app, "pacman_lock_held", lambda: False)
    w = app.UpdateWindow(_updates(), {}, None)
    w.resize(1280, 800)
    w.show()
    w._on_tracked_loaded([{"name": "konsole", "version": "25.08.1-1",
                           "repo": "official"}])
    qapp.processEvents()
    yield w
    w.close()


def _accept_box(monkeypatch, label=None):
    """Make every confirmation dialog report 'the user pressed the button
    whose text contains `label`' (any button when label is None)."""
    class Box(_AcceptingBox):
        def clickedButton(self):
            if label is None:
                return self._accept
            for b in self.buttons:
                if label in b.text():
                    return b
            return None

    monkeypatch.setattr(app, "QMessageBox", Box)


class TestRemovalWhileBusy:
    """The bug the user hit: uninstall during an update did nothing."""

    def test_uninstall_during_an_update_queues_instead_of_failing(
            self, busy_window, monkeypatch):
        w = busy_window
        _accept_box(monkeypatch, "Remove when it finishes")
        w._updating = True

        w._start_track_uninstall(["konsole"])

        assert FakeRemover.started == [], "a doomed removal was launched"
        assert w._pending_removals == [("track", ["konsole"])]
        assert "when the update finishes" in w._sub.text()

    def test_declining_the_queue_changes_nothing(self, busy_window,
                                                 monkeypatch):
        w = busy_window
        _accept_box(monkeypatch, "Cancel")
        w._updating = True

        w._start_track_uninstall(["konsole"])

        assert w._pending_removals == []
        assert FakeRemover.started == []

    def test_queued_removal_runs_when_the_update_ends(self, busy_window,
                                                      monkeypatch):
        w = busy_window
        _accept_box(monkeypatch, "Remove when it finishes")
        w._updating = True
        w._start_track_uninstall(["konsole"])

        w._updating = False
        w._drain_pending_removals()

        assert w._pending_removals == []
        assert len(FakeRemover.started) == 1
        assert FakeRemover.started[0].packages == ["konsole"]

    def test_a_locked_database_refuses_before_the_password_prompt(
            self, busy_window, monkeypatch):
        w = busy_window
        _accept_box(monkeypatch)
        monkeypatch.setattr(app, "pacman_lock_held", lambda: True)

        w._start_track_uninstall(["konsole"])

        assert FakeRemover.started == []
        assert w._pending_removals == []
        assert "locked" in w._sub.text()

    def test_update_will_not_start_while_a_removal_runs(self, busy_window):
        w = busy_window
        w._track_uninstaller = FakeRemover(["konsole"])
        w._safe_names = ["konsole"]
        w.start_update()
        assert w._updating is False
        assert "removal is still running" in w._sub.text()

    def test_close_stops_an_untracked_removal(self, busy_window):
        # A live QThread destroyed by close is a hard abort — the untracked
        # remover must be in the shutdown list like the others.
        w = busy_window
        remover = FakeRemover(["konsole"])
        w._untracked_uninstaller = remover
        w._stop_runners()
        assert w._untracked_uninstaller is None

    def test_a_failed_update_still_runs_the_queued_removal(
            self, busy_window, monkeypatch):
        # The exact shape of the user's report: uninstall during an update,
        # update fails, package must still be removed afterwards.
        w = busy_window
        _accept_box(monkeypatch, "Remove when it finishes")
        w._updating = True
        w._safe_names = ["konsole"]
        w._start_track_uninstall(["konsole"])
        assert w._pending_removals == [("track", ["konsole"])]

        w._on_update_done(False, "error: boom")

        assert w._pending_removals == []
        assert len(FakeRemover.started) == 1
        assert FakeRemover.started[0].packages == ["konsole"]


def _card_named(dlg, name):
    """The sources card whose title label reads `name`."""
    for w in dlg.findChildren(app.QWidget):
        lbl = w.findChild(app.QLabel, "package-name")
        if lbl is not None and lbl.text() == name:
            return w
    raise AssertionError(f"no card named {name!r}")


class TestSourcesStateAgreement:
    """Every label on a card must tell the same story — the user caught
    the badge saying NOT ENABLED while the button said Enabled."""

    def test_flatpak_card_flips_its_badge_on_success(self, sources_dialog):
        # The exact card from the report (last in the list).
        d = sources_dialog
        card = _card_named(d, "Flatpak · Flathub")
        assert card._badge.text() == "NOT ENABLED"
        btn = card._enable_btn
        assert btn is not None and btn.text() == "Enable"

        d._enable_source("Flatpak · Flathub", "#!/bin/bash\necho hi",
                         card, btn)
        d._enable_runner.done.emit(True, "")

        assert card._badge.text() == "ENABLED"
        assert card._badge.property("tint") == "positive"
        assert btn.text() == "Enabled ✓"
        assert card._bar.property("state") == "done"
        assert "enabled ✓" in card._status_lbl.text()

    def test_every_card_offers_a_button_or_says_enabled(self, sources_dialog):
        # No dead ends: a card that says "not enabled" must offer the
        # Enable button, and nothing already enabled may offer one.
        d = sources_dialog
        for w in d.findChildren(app.QWidget):
            badge = getattr(w, "_badge", None)
            if badge is None:
                continue
            has_btn = getattr(w, "_enable_btn", None) is not None
            if has_btn:
                assert badge.text() == "NOT ENABLED"
            else:
                assert badge.text() in ("ENABLED", "NOT SET UP"), \
                    f"{badge.text()} with nothing to do about it"

    def test_a_ready_source_is_reported_ready(self, sources_dialog,
                                             monkeypatch):
        # Flatpak installed with Flathub registered: the card must say so
        # on open, with nothing left to enable.
        d = sources_dialog
        monkeypatch.setattr(app, "_flatpak_ready", lambda: True)
        fresh = app.ManagedSourcesDialog()
        try:
            card = _card_named(fresh, "Flatpak · Flathub")
            assert card._badge.text() == "ENABLED"
            assert card._badge.property("tint") == "positive"
            assert card._enable_btn is None
        finally:
            fresh.close()


class TestStatStripFilters:
    """The stat strip is a filter bar: every cell counts exactly what
    clicking it shows, on every tab that has one."""

    def test_updates_cells_filter_the_list(self, window, qapp):
        cells = window._update_stat_cards
        assert list(cells) == ["ready", "repos", "aur", "blocked"]
        counts = {k: int(c._value.text()) for k, c in cells.items()}
        for key, n in counts.items():
            cells[key].clicked.emit()
            qapp.processEvents()
            assert window._model.rowCount() == n, f"{key} counted {n}"
            assert cells[key].property("active") is True
            cells[key].clicked.emit()
            qapp.processEvents()
            assert window._model.rowCount() == 4
            assert cells[key].property("active") is False

    def test_clicking_the_active_cell_clears_it(self, window, qapp):
        window._stat_aur.clicked.emit()
        qapp.processEvents()
        assert window._model.filter_source() == "aur"
        window._stat_aur.clicked.emit()
        qapp.processEvents()
        assert window._model.filter_source() == ""

    def test_tracked_strip_counts_and_filters(self, window, qapp):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "konsole", "version": "1-1", "repo": "official"},
             {"name": "aur-pkg", "version": "1-1", "repo": "aur"},
             {"name": "cachy", "version": "1-1", "repo": "cachyos-v3"}])
        qapp.processEvents()
        cells = window._track_stat_cards
        assert {k: c._value.text() for k, c in cells.items()} == {
            "": "3", "official": "1", "aur": "1", "managed": "1",
            "flatpak": "0", "appimage": "0"}
        # The "everything" cell is a readout, not a filter: nothing is
        # highlighted until a real filter is on.
        assert not any(c.property("active") for c in cells.values())
        cells["managed"].clicked.emit()
        qapp.processEvents()
        assert window._track_model.rowCount() == 1
        assert window._track_model.item(0)["name"] == "cachy"
        cells[""].clicked.emit()
        qapp.processEvents()
        assert window._track_model.rowCount() == 3

    def test_untracked_strip_separates_apps_and_dependencies(
            self, window, qapp):
        _settle_loaders(window, qapp)
        window._on_untracked_loaded(
            [_row("zen", "Zen", "manual"), _row("whisper-flow", "WF",
                                                "manual")],
            [_row("libfoo", "libfoo", "official", kind="dependency")])
        qapp.processEvents()
        cells = window._un_stat_cards
        assert {k: c._value.text() for k, c in cells.items()} == {
            "": "3", "applications": "2", "dependencies": "1"}
        cells["dependencies"].clicked.emit()
        qapp.processEvents()
        assert window._un_model.rowCount() == 1
        assert window._un_model.item(0)["name"] == "libfoo"
        cells["applications"].clicked.emit()
        qapp.processEvents()
        assert window._un_model.rowCount() == 2
        assert window._un_model.row_of("libfoo") is None
        cells["applications"].clicked.emit()
        qapp.processEvents()
        assert window._un_model.rowCount() == 3

    def test_every_strip_is_the_same_height(self, window, qapp):
        # Thin and uniform: the strips are one row of 38px cells.
        for cards in (window._update_stat_cards, window._track_stat_cards,
                      window._un_stat_cards):
            heights = {c.height() for c in cards.values()}
            assert heights == {app._sp(38)}, heights

    def test_filtering_moves_nothing(self, window, qapp):
        # The UI law: a filter changes what is listed, never the frame.
        before = (window._stats_row.height(), window._controls.height(),
                  window._list.height())
        window._stat_aur.clicked.emit()
        qapp.processEvents()
        after = (window._stats_row.height(), window._controls.height(),
                 window._list.height())
        assert after == before


def _settle_loaders(window, qapp):
    """Let the window's real background loaders finish first: their
    (empty) results arrive after a staged one and would replace it."""
    for attr in ("_obs_checker", "_track_loader", "_untracked_loader"):
        thread = getattr(window, attr, None)
        if thread is not None:
            thread.wait(25000)
    qapp.processEvents()
    qapp.processEvents()


def _row(name, title, repo, kind="app"):
    return {"name": name, "title": title, "version": "1", "repo": repo,
            "target": "", "source": "", "relation": "", "state": "idle",
            "failed_op": "", "desktop": None, "desc": "", "checked": False,
            "url": "", "path": "", "kind": kind}


class TestFlatpakRows:
    """Flatpak apps are a source you can see, filter and remove."""

    def test_flatpak_apps_are_tracked_rows(self, monkeypatch):
        monkeypatch.setattr(app, "_flatpak_apps",
                            lambda: {"com.discordapp.Discord":
                                     ("1.0.151", "system"),
                                     "com.ticktick.TickTick":
                                     ("8.0.20", "user")})
        rows = app.flatpak_tracked_rows()
        by_name = {r["name"]: r for r in rows}
        assert by_name["com.discordapp.Discord"]["repo"] == "flatpak"
        assert by_name["com.discordapp.Discord"]["version"] == "1.0.151"
        assert len(rows) == 2

    def test_the_tracked_load_includes_flatpak(self, monkeypatch):
        monkeypatch.setattr(app, "get_installed_packages",
                            lambda: [{"name": "konsole", "version": "1-1",
                                      "repo": "official"}])
        monkeypatch.setattr(app, "appimage_rows", lambda: [])
        monkeypatch.setattr(app, "_flatpak_apps",
                            lambda: {"com.discordapp.Discord":
                                     ("1.0.151", "system")})
        installed = app.load_tracked()
        names = {r["name"] for r in installed}
        assert names == {"konsole", "com.discordapp.Discord"}

    def test_a_broken_flatpak_call_still_lists_pacman_packages(
            self, monkeypatch):
        monkeypatch.setattr(app, "get_installed_packages",
                            lambda: [{"name": "konsole", "version": "1-1",
                                      "repo": "official"}])
        monkeypatch.setattr(app, "appimage_rows", lambda: [])

        def boom():
            raise OSError("flatpak exploded")

        monkeypatch.setattr(app, "flatpak_tracked_rows", boom)
        installed = app.load_tracked()
        assert [r["name"] for r in installed] == ["konsole"]

    def test_untracked_leaves_flatpak_apps_to_tracked(self, monkeypatch,
                                                      tmp_path):
        exports = tmp_path / "exports"
        apps = tmp_path / "applications"
        exports.mkdir()
        apps.mkdir()
        (exports / "com.discordapp.Discord.desktop").write_text(
            "[Desktop Entry]\nName=Discord\nExec=/usr/bin/flatpak run "
            "com.discordapp.Discord\nType=Application\n")
        (apps / "zen.desktop").write_text(
            "[Desktop Entry]\nName=Zen\nExec=/usr/bin/zen\n"
            "Type=Application\n")
        monkeypatch.setattr(app, "_flatpak_export_dirs", lambda: [str(exports)])
        monkeypatch.setattr(app, "scan_desktop_files",
                            lambda dirs=None: [
                                {"file": str(exports /
                                             "com.discordapp.Discord.desktop"),
                                 "title": "Discord", "comment": "Chat",
                                 "exec": "/usr/bin/flatpak run "
                                         "com.discordapp.Discord"},
                                {"file": str(apps / "zen.desktop"),
                                 "title": "Zen", "comment": "",
                                 "exec": "/usr/bin/zen"}])
        monkeypatch.setattr(app, "_flatpak_apps",
                            lambda: {"com.discordapp.Discord":
                                     ("1.0.151", "system")})
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        rows, _titles = app.UntrackedLoader()._load_apps()
        names = {r["name"] for r in rows}
        assert "com.discordapp.Discord" not in names
        assert "zen" in names

    def test_a_flatpak_row_resolves_its_title_without_pacman(
            self, monkeypatch, tmp_path):
        exports = tmp_path / "exports"
        exports.mkdir()
        (exports / "com.discordapp.Discord.desktop").write_text(
            "[Desktop Entry]\nName=Discord\nComment=Chat for gamers\n"
            "Exec=/usr/bin/flatpak run com.discordapp.Discord\n"
            "Type=Application\n")
        monkeypatch.setattr(app, "_flatpak_export_dirs", lambda: [str(exports)])
        monkeypatch.setattr(
            app, "_run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no pacman")))
        desktop = app.TrackedEnricher._flatpak_desktop(
            "com.discordapp.Discord")
        assert desktop.endswith("com.discordapp.Discord.desktop")
        assert app._desktop_title(desktop) == "Discord"
        assert app.TrackedEnricher._flatpak_desc(
            "com.discordapp.Discord") == "Chat for gamers"

    def test_uninstalling_a_tracked_flatpak_uses_flatpak(self, window, qapp,
                                                        monkeypatch):
        started = []

        class FakeFlatpak(QObject):
            line = pyqtSignal(str)
            done = pyqtSignal(bool, str)

            def __init__(self, ids, installs=None):
                super().__init__()
                started.append(list(ids))
                self.ids = list(ids)

            def isRunning(self):
                return False

            def wait(self, ms=0):
                return True

            def start(self):
                pass

            def terminate(self):
                pass

        monkeypatch.setattr(app, "FlatpakRemover", FakeFlatpak)
        monkeypatch.setattr(app, "_flatpak_apps",
                            lambda: {"com.discordapp.Discord":
                                     ("1.0.151", "system")})
        _accept_box(monkeypatch, "Remove")
        _settle_loaders(window, qapp)
        window._on_untracked_loaded(
            [_row("com.discordapp.Discord", "Discord", "flatpak")], [])
        qapp.processEvents()

        window._request_untracked_uninstall("com.discordapp.Discord")

        assert started == [["com.discordapp.Discord"]]
        assert window._un_kind == "flatpak"

    def test_a_gone_flatpak_app_is_removed_from_the_list(self, window, qapp,
                                                         monkeypatch):
        class FakeFlatpak(QObject):
            line = pyqtSignal(str)
            done = pyqtSignal(bool, str)

            def __init__(self, ids, installs=None):
                super().__init__()

            def isRunning(self):
                return False

            def wait(self, ms=0):
                return True

            def start(self):
                pass

            def terminate(self):
                pass

        monkeypatch.setattr(app, "FlatpakRemover", FakeFlatpak)
        monkeypatch.setattr(app, "_flatpak_apps", lambda: {})
        _accept_box(monkeypatch, "Remove")
        _settle_loaders(window, qapp)
        window._on_untracked_loaded(
            [_row("com.discordapp.Discord", "Discord", "flatpak")], [])
        qapp.processEvents()
        window._request_untracked_uninstall("com.discordapp.Discord")
        window._untracked_uninstaller.done.emit(True, "uninstalled")

        assert window._un_model.row_of("com.discordapp.Discord") is None
        assert "uninstalled" in window._sub.text()


class TestRebootButton:
    """The reboot call to action is a filled warning button."""

    def test_it_is_a_button_with_the_warning_fill(self, window, qapp,
                                                 monkeypatch):
        monkeypatch.setattr(app, "reboot_required", lambda: True)
        window._refresh_reboot_chip()
        btn = window._reboot_btn
        assert btn.isVisible() or btn.isVisibleTo(window)
        assert btn.text() == "Reboot to finish"
        assert btn.objectName() == "reboot"
        assert not btn.icon().isNull()
        assert btn.height() == app._sp(34)
        qss = app._themed_qss()
        assert "QPushButton#reboot" in qss
        assert app._warning_hex() in qss

    def test_showing_it_does_not_move_the_other_buttons(self, window, qapp,
                                                        monkeypatch):
        monkeypatch.setattr(app, "reboot_required", lambda: False)
        window._refresh_reboot_chip()
        qapp.processEvents()
        before = (window._recheck_btn.x(), window._update_btn.x())
        monkeypatch.setattr(app, "reboot_required", lambda: True)
        window._refresh_reboot_chip()
        qapp.processEvents()
        after = (window._recheck_btn.x(), window._update_btn.x())
        assert after == before

    def test_confirming_asks_the_session_to_reboot(self, window, qapp,
                                                   monkeypatch):
        asked = []
        monkeypatch.setattr(app, "request_reboot",
                            lambda: (asked.append(True), True)[1])
        _accept_box(monkeypatch, "Reboot now")
        window._reboot_now()
        assert asked == [True]
        assert "Rebooting" in window._sub.text()

    def test_declining_does_nothing(self, window, qapp, monkeypatch):
        asked = []
        monkeypatch.setattr(app, "request_reboot",
                            lambda: (asked.append(True), True)[1])
        _accept_box(monkeypatch, "Not yet")
        window._reboot_now()
        assert asked == []

    def test_a_failed_request_says_so(self, window, qapp, monkeypatch):
        monkeypatch.setattr(app, "request_reboot", lambda: False)
        _accept_box(monkeypatch, "Reboot now")
        window._reboot_now()
        assert "power menu" in window._sub.text()


class TestAppImageRows:
    """An AppImage is software too — tracked, titled, removable."""

    def test_a_launcher_that_runs_one_makes_it_installed(self, monkeypatch,
                                                         tmp_path):
        image = tmp_path / "WhisperFlow-0.4.409-x86_64.AppImage"
        image.write_text("#!/bin/sh\n")
        monkeypatch.setattr(app, "scan_desktop_files",
                            lambda dirs=None, include_hidden=False: [
                                {"file": "/home/s/.local/share/applications/"
                                         "whisper-flow.desktop",
                                 "title": "WhisperFlow", "comment": "Dictation",
                                 "exec": f'"{image}"'}])
        monkeypatch.setattr(app, "_appimage_dirs", lambda: [])
        rows = app.appimage_rows()
        assert len(rows) == 1
        assert rows[0]["name"] == "WhisperFlow-0.4.409-x86_64"
        assert rows[0]["title"] == "WhisperFlow"
        assert rows[0]["version"] == "0.4.409"
        assert rows[0]["repo"] == "appimage"
        assert rows[0]["path"] == str(image)

    def test_an_app_folder_counts_even_without_a_launcher(self, monkeypatch,
                                                          tmp_path):
        apps = tmp_path / "Applications"
        apps.mkdir()
        (apps / "Claude.AppImage").write_text("#!/bin/sh\n")
        (apps / "notes.txt").write_text("not an app\n")
        monkeypatch.setattr(app, "scan_desktop_files",
                            lambda dirs=None, include_hidden=False: [])
        monkeypatch.setattr(app, "_appimage_dirs", lambda: [str(apps)])
        rows = app.appimage_rows()
        assert [r["name"] for r in rows] == ["Claude"]
        assert rows[0]["title"] == "Claude"

    def test_the_same_file_found_twice_is_one_row(self, monkeypatch,
                                                  tmp_path):
        apps = tmp_path / "Applications"
        apps.mkdir()
        image = apps / "T3-Code-0.0.24-x86_64.AppImage"
        image.write_text("#!/bin/sh\n")
        monkeypatch.setattr(app, "scan_desktop_files",
                            lambda dirs=None, include_hidden=False: [
                                {"file": "/x/t3code.desktop",
                                 "title": "T3 Code", "comment": "",
                                 "exec": f'"{image}" %U'}])
        monkeypatch.setattr(app, "_appimage_dirs", lambda: [str(apps)])
        rows = app.appimage_rows()
        assert len(rows) == 1
        assert rows[0]["title"] == "T3 Code"

    def test_version_and_name_come_from_the_filename(self):
        assert app._appimage_version("/x/T3-Code-0.0.24-x86_64.AppImage") \
            == "0.0.24"
        assert app._appimage_version("/x/paper-desktop-0.1.10x86_64.AppImage") \
            == "0.1.10"
        assert app._appimage_version("/x/FreeBoomShare-latest.AppImage") == ""
        assert app._appimage_pretty("WhisperFlow-0.4.409-x86_64") \
            == "WhisperFlow"
        assert app._appimage_pretty("Claude") == "Claude"

    def test_exec_parsing_handles_quotes_and_env(self):
        assert app._appimage_in_exec('"/home/s/Applications/Claude.AppImage" '
                                     '--no-sandbox %u') \
            == "/home/s/Applications/Claude.AppImage"
        assert app._appimage_in_exec(
            'env "WINEPREFIX=/tmp/x" wine start %u') == ""
        assert app._appimage_in_exec("/usr/bin/zen %U") == ""

    def test_untracked_leaves_appimage_launchers_to_tracked(
            self, monkeypatch, tmp_path):
        image = tmp_path / "Zen-1.0.AppImage"
        image.write_text("#!/bin/sh\n")
        monkeypatch.setattr(app, "scan_desktop_files",
                            lambda dirs=None, include_hidden=False: [
                                {"file": str(tmp_path / "zen.desktop"),
                                 "title": "Zen", "comment": "",
                                 "exec": f'"{image}" %U'},
                                {"file": str(tmp_path / "other.desktop"),
                                 "title": "Other", "comment": "",
                                 "exec": "/usr/bin/other"}])
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        rows, _titles = app.UntrackedLoader()._load_apps()
        names = {r["name"] for r in rows}
        assert "zen" not in names
        assert "other" in names

    def test_removing_one_deletes_the_file_and_its_launcher(
            self, monkeypatch, tmp_path):
        apps = tmp_path / "Applications"
        apps.mkdir()
        image = apps / "Claude.AppImage"
        image.write_text("#!/bin/sh\n")
        launcher = tmp_path / "claude-desktop.desktop"
        launcher.write_text("[Desktop Entry]\nName=Claude\n"
                            f"Exec={image}\nType=Application\n")
        monkeypatch.setattr(
            app.AppImageRemover, "_launchers_for",
            staticmethod(lambda path: [str(launcher)]))
        seen = []
        remover = app.AppImageRemover([str(image)])
        remover.done.connect(lambda ok, out: seen.append((ok, out)))
        remover.run()
        assert seen and seen[0][0] is True
        assert not image.exists()
        assert not launcher.exists()

    def test_a_tracked_appimage_uninstalls_as_a_file(self, window, qapp,
                                                     monkeypatch):
        started = []

        class FakeImages(QObject):
            line = pyqtSignal(str)
            done = pyqtSignal(bool, str)

            def __init__(self, paths):
                super().__init__()
                started.append(list(paths))

            def isRunning(self):
                return False

            def wait(self, ms=0):
                return True

            def start(self):
                pass

            def terminate(self):
                pass

        monkeypatch.setattr(app, "AppImageRemover", FakeImages)
        monkeypatch.setattr(app, "find_blocked_obsolete_packages",
                            lambda pkgs: [])
        _accept_box(monkeypatch, "Remove")
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "Claude", "version": "", "repo": "appimage",
              "title": "Claude", "path": "/home/s/Applications/Claude.AppImage",
              "desktop": ""}])
        qapp.processEvents()
        assert window._track_source("Claude") == "appimage"

        window._request_track_uninstall("Claude")

        assert started == [["/home/s/Applications/Claude.AppImage"]]
        assert window._track_kind == "appimage"


class TestViewPackageLinks:
    """The row link follows the source, and disappears when there is no
    honest page for it."""

    def _link_rect(self, window, row):
        d = window._track_list.itemDelegate()
        return d._geom(row, window._track_model.item(row), 1240)["link"]

    def test_a_flatpak_row_links_to_flathub(self, window, qapp, monkeypatch):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "com.discordapp.Discord", "version": "1",
              "repo": "flatpak", "title": "Discord", "desktop": ""}])
        qapp.processEvents()
        url = app._package_url("com.discordapp.Discord", "flatpak")
        assert url == "https://flathub.org/apps/com.discordapp.Discord"
        assert not self._link_rect(window, 0).isNull()

    def test_an_appimage_without_a_page_shows_no_link(self, window, qapp,
                                                      monkeypatch):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "Claude", "version": "", "repo": "appimage",
              "title": "Claude", "path": "/x/Claude.AppImage"}])
        qapp.processEvents()
        assert self._link_rect(window, 0).isNull(), "link offered, no page"

    def test_an_appimage_with_a_page_shows_one(self, window, qapp,
                                              monkeypatch):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "Paper", "version": "0.1.10", "repo": "appimage",
              "title": "Paper", "url": "https://paper.example/",
              "path": "/x/Paper.AppImage"}])
        qapp.processEvents()
        assert not self._link_rect(window, 0).isNull()

    def test_a_pacman_row_keeps_its_link(self, window, qapp, monkeypatch):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "konsole", "version": "1", "repo": "official"}])
        qapp.processEvents()
        assert not self._link_rect(window, 0).isNull()

    def test_clicking_a_row_without_a_link_opens_nothing(self, window, qapp,
                                                         monkeypatch):
        opened = []
        monkeypatch.setattr(app.QDesktopServices, "openUrl",
                            lambda url: opened.append(url))
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "Claude", "version": "", "repo": "appimage",
              "title": "Claude", "path": "/x/Claude.AppImage"}])
        qapp.processEvents()
        d = window._track_list.itemDelegate()
        lay = d._geom(0, window._track_model.item(0), 1240)
        assert lay["link"].isNull()
        assert opened == []


class TestOpeningFlatpakApps:
    """Open on a Flatpak row runs Flatpak, and says why when it can't."""

    def _stage(self, window, qapp, monkeypatch):
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "com.discordapp.Discord", "version": "1",
              "repo": "flatpak", "title": "Discord",
              "desktop": "/var/lib/flatpak/exports/share/applications/"
                         "com.discordapp.Discord.desktop"}])
        qapp.processEvents()

    def test_open_runs_flatpak_run_with_the_app_id(self, window, qapp,
                                                  monkeypatch):
        calls = []
        monkeypatch.setattr(app, "flatpak_portal_missing", lambda: False)
        monkeypatch.setattr(
            app.QProcess, "startDetached",
            staticmethod(lambda prog, args=None: (
                calls.append((prog, list(args or []))), True)[1]))
        self._stage(window, qapp, monkeypatch)

        window._open_tracked_app("com.discordapp.Discord")

        assert calls == [("flatpak", ["run", "com.discordapp.Discord"])]
        assert "Opened" in window._log_view.toPlainText()

    def test_a_dead_document_portal_is_named_as_the_reason(
            self, window, qapp, monkeypatch):
        # The real failure that reads as "Flatpak apps don't open any
        # more": bwrap dies on the missing doc dir, everywhere.
        monkeypatch.setattr(app, "flatpak_portal_missing", lambda: True)
        started = []
        monkeypatch.setattr(app.QProcess, "startDetached",
                            staticmethod(lambda *a: started.append(a)))
        _accept_box(monkeypatch, "Not now")
        self._stage(window, qapp, monkeypatch)

        window._open_tracked_app("com.discordapp.Discord")

        assert started == []
        # The status line is elided to one line, so check the log too.
        text = window._sub.text() + window._log_view.toPlainText()
        assert "document portal" in text
        assert "xdg-document-portal" in text

    def test_it_offers_to_restart_the_portal_and_then_opens(
            self, window, qapp, monkeypatch):
        ran = []
        missing = {"yes": True}

        class R:
            returncode = 0

        monkeypatch.setattr(app, "flatpak_portal_missing",
                            lambda: missing["yes"])
        monkeypatch.setattr(
            app, "_run",
            lambda cmd, **k: (ran.append(cmd), missing.update(yes=False),
                              R())[2])
        started = []
        monkeypatch.setattr(
            app.QProcess, "startDetached",
            staticmethod(lambda prog, args=None: (
                started.append((prog, list(args or []))), True)[1]))
        _accept_box(monkeypatch, "Restart it")
        self._stage(window, qapp, monkeypatch)

        window._open_tracked_app("com.discordapp.Discord")

        assert ran == [["systemctl", "--user", "restart",
                        "xdg-document-portal"]]
        assert started == [("flatpak", ["run", "com.discordapp.Discord"])]

    def test_other_rows_still_launch_their_desktop_entry(self, window, qapp,
                                                         monkeypatch):
        seen = []
        monkeypatch.setattr(app, "_launch_desktop",
                            lambda path: (seen.append(path), True)[1])
        _settle_loaders(window, qapp)
        window._on_tracked_loaded(
            [{"name": "zen", "version": "1", "repo": "manual",
              "title": "Zen", "desktop": "/u/zen.desktop"}])
        qapp.processEvents()

        window._open_tracked_app("zen")

        assert seen == ["/u/zen.desktop"]
