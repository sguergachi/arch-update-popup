"""Pure-logic tests: changelog detection, failure classification, helpers."""
import os

import pytest

from conftest import load_app

app = load_app("app_logic")


class TestStripVersion:
    @pytest.mark.parametrize("raw,want", [
        ("2:1.2.3-4", "1.2.3"),
        ("1.2.3-4", "1.2.3"),
        ("26.08.1-1.1", "26.08.1-1.1"),  # pkgrel strip is trailing -N only
        ("6.30.0-1", "6.30.0"),
        ("1.0", "1.0"),
    ])
    def test_strip(self, raw, want):
        assert app.strip_version(raw) == want


class TestMatchTag:
    def test_exact(self):
        assert app._match_tag(["v6.30.0", "v6.29.0"], "6.30.0") == "v6.30.0"

    def test_no_v_prefix(self):
        assert app._match_tag(["6.30.0"], "6.30.0") == "6.30.0"

    def test_no_false_patch_match(self):
        # 6.30 must not match a 6.30.1 tag
        assert app._match_tag(["v6.30.1"], "6.30") is None

    def test_prefix_ok(self):
        assert app._match_tag(["release-6.30.0"], "6.30.0") == "release-6.30.0"

    def test_empty(self):
        assert app._match_tag([], "1.0") is None
        assert app._match_tag(["v1"], "") is None


class TestCompareSummary:
    def _commits(self):
        return [
            {"sha": "aaa", "html_url": "u1",
             "commit": {"message": "Fix crash on empty input\n\nDetails",
                        "author": {"name": "Ann", "date": "2026-09-01T10:00:00Z"}}},
            {"sha": "bbb", "html_url": "u2",
             "commit": {"message": "Bump deps",
                        "author": {"name": "Bob", "date": "2026-09-02T10:00:00Z"}}},
        ]

    def test_summary_and_full(self):
        summary, full = app.build_compare_summary(
            self._commits(), "v1.0", "v2.0", "http://x/compare")
        assert "2 commits" in summary
        assert "Fix crash on empty input" in summary
        assert "Ann" in full and "2026-09-01" in full
        assert "http://x/compare" in full

    def test_dedup_and_truncate(self):
        commits = [{"sha": str(i), "html_url": "u",
                    "commit": {"message": "Same msg",
                               "author": {"name": "", "date": ""}}}
                   for i in range(20)]
        summary, full = app.build_compare_summary(commits, "a", "b", "u")
        assert "20 commits" in summary
        assert len(summary) <= 301


class TestSummarize:
    def test_bullets_kept(self):
        text = ("Release 2.0\n\n- Fix crash on empty input\n"
                "- Add feature X for export\n\nFull changelog at http://x")
        out = app.summarize_notes(text)
        assert "Fix crash on empty input" in out
        assert "Add feature X for export" in out
        assert "http" not in out

    def test_empty(self):
        assert app.summarize_notes("") == ""
        assert app.summarize_notes("   ") == ""


class TestVersionSection:
    def test_extract(self):
        md = "# Changelog\n\n## 2.0\n\n- new stuff\n\n## 1.0\n\n- old\n"
        assert "new stuff" in app.extract_version_section(md, "2.0")

    def test_no_prefix_collision(self):
        md = "## 25.01.1\n\n- patch\n\n## 25.01\n\n- minor\n"
        sec = app.extract_version_section(md, "25.01")
        assert sec is not None and "minor" in sec

    def test_missing(self):
        assert app.extract_version_section("# Hi\n\ntext\n", "9.9") is None


class TestVersionMatchesRelease:
    def test_match(self):
        assert app._version_matches_release(
            "6.30.0", {"tag_name": "v6.30.0", "name": ""})
        assert app._version_matches_release(
            "26.08.1", {"tag_name": "26.08.1-1", "name": ""})

    def test_no_match(self):
        assert not app._version_matches_release(
            "6.30.0", {"tag_name": "v6.29.0", "name": ""})


class TestClassify:
    @pytest.mark.parametrize("output,want", [
        ("", "cancelled"),
        ("Authorization failed: dismissed", "cancelled"),
        ("Cancelled by user.", "cancelled"),
        ("Sorry, try again.\nSorry, try again.\n"
         "sudo: 3 incorrect password attempts", "cancelled"),
        ("authentication failure; log file incorrect", "cancelled"),
        # Real pkexec/sudo auth failures: nothing ran → clean retry state.
        ("Error executing command as another user: Not authorized\n"
         "This incident has been reported.", "cancelled"),
        ("sudo: no password was provided", "cancelled"),
        ("Authentication failed", "cancelled"),
        ("pkg: /x exists in filesystem", "file-conflict"),
        ("invalid or corrupted package (PGP signature)", "signature"),
        ("failed to synchronize all databases", "sync"),
        ("could not satisfy dependencies", "dependency"),
        ("Partition / too full", "disk-space"),
        ("weird unknown boom", "generic"),
    ])
    def test_kinds(self, output, want):
        kind, title, advice = app.classify_update_failure(output)
        assert kind == want
        assert title and advice


class TestConflicts:
    def test_extract(self):
        out = ("a: /x exists in filesystem\na: /y exists in filesystem\n"
               "b: /z exists in filesystem")
        pkgs, files = app.extract_file_conflicts(out)
        assert pkgs == ["a", "b"] and files == ["/x", "/y", "/z"]

    def test_failed_names(self):
        out = ("error: failed to commit\nfoo: /x exists in filesystem\n"
               "could not satisfy dependencies for bar")
        assert app._failed_package_names(out, ["foo", "bar", "baz"]) == ["foo", "bar"]

    def test_failed_names_empty(self):
        assert app._failed_package_names("", ["foo"]) == []


class TestUrls:
    def test_aur(self):
        assert app._package_url("yay", "aur") == \
            "https://aur.archlinux.org/packages/yay"

    def test_official(self):
        assert "archlinux.org" in app._package_url("konsole", "official")

    def test_tints(self):
        for repo in ("aur", "official", "extra-testing", ""):
            rgb, fg = app._repo_tint(repo)
            assert len(rgb) == 3 and all(0 <= c <= 255 for c in rgb)
            assert fg.startswith("#")

    def test_pkgbuild_github(self):
        raw = 'pkgname=x\nsource=("https://github.com/KDE/konsole/archive/1.0.tar.gz")'
        assert app._github_from_pkgbuild_text(raw) == ("KDE", "konsole")
        assert app._github_from_pkgbuild_text("source=(http://x)") is None


class TestObsolete:
    def test_culprits(self):
        out = (":: installing q (1) breaks dependency 'q=1' required by old-pkg")
        assert app.obsolete_conflict_culprits(out, ["old-pkg", "other"]) == ["old-pkg"]

    def test_none(self):
        assert app.obsolete_conflict_culprits("all good", ["a"]) == []


class TestLogNoise:
    def test_noise(self):
        assert app.is_log_noise("[##################] 100%")
        assert app.is_log_noise("12.3 MiB/50.0 MiB")

    def test_build_lines(self):
        assert app.match_build_line("==> Making package: snapd 2.77-1") == \
            "snapd"
        assert app.match_build_line(":: Building python2 ...") == "python2"
        assert app.match_build_line(":: installing foo (1/2)") is None
        assert app.match_build_line("") is None

    def test_conflict_lines(self):
        assert app.match_conflict_line(
            "python: /usr/lib/x exists in filesystem") == \
            ("python", "/usr/lib/x")
        assert app.match_conflict_line("all good") is None

    def test_signal_kept(self):
        assert not app.is_log_noise("error: target not found: foo")
        assert not app.is_log_noise(":: installing konsole (1/2)")


class TestVerifyUpgraded:
    def _run(self, monkeypatch, stdout):
        import subprocess as sp

        class R:
            returncode = 0

            def __init__(self):
                self.stdout = stdout

        monkeypatch.setattr(app.subprocess, "run",
                            lambda *a, **k: R())
        return app.verify_upgraded

    def test_mismatch_detected(self, monkeypatch):
        verify = self._run(monkeypatch, "fzf 0.74.3-1.1\n")
        assert verify({"fzf": "0.74.4-1.1"}) == [
            ("fzf", "0.74.3-1.1", "0.74.4-1.1")]

    def test_match_clean(self, monkeypatch):
        verify = self._run(monkeypatch, "fzf 0.74.4-1.1\n")
        assert verify({"fzf": "0.74.4-1.1"}) == []

    def test_empty_skipped(self, monkeypatch):
        verify = self._run(monkeypatch, "")
        assert verify({}) == []
        assert verify({"": "1.0"}) == []

    def test_stale_db_classified(self):
        kind, title, advice = app.classify_update_failure(
            "fzf: still 0.74.3, expected 0.74.4 — "
            "was not upgraded (stale sync database)")
        assert kind == "stale-db"
        assert title and advice



class TestScanStopHook:
    def test_should_stop_aborts_early(self, monkeypatch):
        calls = {"n": 0}

        def fake_installed():
            calls["n"] += 1
            return [{"name": f"p{i}", "version": "1",
                     "repo": "official"} for i in range(5)]

        monkeypatch.setattr(app, "get_installed_packages", fake_installed)
        monkeypatch.setattr(app, "_load_all_compromised_packages",
                            lambda: {})
        out = app.scan_installed_for_compromise(
            should_stop=lambda: True)
        assert out == []
        assert calls["n"] == 1

    def test_no_hook_scans_all(self, monkeypatch):
        monkeypatch.setattr(
            app, "get_installed_packages",
            lambda: [{"name": "p0", "version": "1", "repo": "official"}])
        monkeypatch.setattr(app, "_load_all_compromised_packages",
                            lambda: {})
        out = app.scan_installed_for_compromise()
        assert out == []


class TestRunnerCommands:
    def test_explicit_names_refresh_sync_dbs(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: n == "sudo")
        r = app.UpdateRunner("paru", ["fzf", "konsole"])
        assert "-Sy" in r._cmd and "-Syu" not in r._cmd
        r._cleanup()
        r = app.UpdateRunner(None, ["fzf"])
        assert r._cmd[:4] == ["sudo", "-A", "pacman", "-Sy"]
        assert "SUDO_ASKPASS" in r._child_env()
        r._cleanup()

    def test_full_upgrade_unchanged(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: n == "sudo")
        r = app.UpdateRunner("paru", None)
        assert "-Syu" in r._cmd
        r._cleanup()
        r = app.UpdateRunner(None, None)
        assert r._cmd[:3] == ["sudo", "-A", "pacman"]
        r._cleanup()


class TestAskpass:
    """Command shape + script lifecycle for privileged operations.

    Never runs pacman/pkexec/sudo: only construction, env export and
    file cleanup are exercised (the thread bodies are stubbed hermetic
    in conftest anyway).
    """

    @pytest.fixture(autouse=True)
    def _sudo_present(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: n == "sudo")

    def _remover(self, *args, **kw):
        r = app.ObsoleteRemover(*args, **kw)
        self._cleanup_later.append(r)
        return r

    def setup_method(self):
        self._cleanup_later = []

    def teardown_method(self):
        for r in self._cleanup_later:
            r._cleanup()

    def test_remover_uses_sudo_askpass(self):
        r = self._remover(["perplexity"])
        assert r._cmd[0] == "sudo"
        assert "-A" in r._cmd[:3]
        assert r._cmd[2:4] == ["pacman", "-Rns"]
        assert r._cmd[-1] == "--noconfirm"
        env = r._child_env()
        assert env["SUDO_ASKPASS"] == r._askpass
        assert os.path.isfile(r._askpass)

    def test_askpass_script_is_0700(self):
        r = self._remover(["pkg"])
        assert app.stat.S_IMODE(os.stat(r._askpass).st_mode) == 0o700

    def test_askpass_removed_after_cleanup(self):
        r = self._remover(["pkg"])
        path = r._askpass
        r._cleanup()
        assert not os.path.exists(path)
        assert r._askpass is None

    def test_askpass_script_prefer_kdialog(self):
        body = open(self._remover(["pkg"])._askpass).read()
        assert "kdialog" in body
        assert "zenity" in body  # fallback without kdialog

    def test_remover_force_uses_rdd(self):
        r = self._remover(["pkg"], force=True)
        assert "-Rdd" in r._cmd and "-Rns" not in r._cmd

    def test_no_sudo_falls_back_to_pkexec(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: False)
        r = self._remover(["pkg"])
        assert r._cmd[:2] == ["pkexec", "pacman"]
        assert r._askpass is None
        assert "SUDO_ASKPASS" not in r._child_env()
        r = app.UpdateRunner(None, ["pkg"])
        self._cleanup_later.append(r)
        assert r._cmd[:2] == ["pkexec", "pacman"]
        assert r._askpass is None

    def test_update_runner_no_helper_uses_sudo(self):
        r = app.UpdateRunner(None, ["fzf"])
        self._cleanup_later.append(r)
        assert r._cmd[0] == "sudo" and "-A" in r._cmd[:3]
        assert r._cmd[2:4] == ["pacman", "-Sy"]

    def test_pkexec_tty_error_classifies_cancelled(self):
        kind = app.classify_update_failure(
            "✕ Removing perplexity failed: Error creating textual "
            "authentication agent: Error opening current controlling "
            "terminal for the process (`/dev/tty'): "
            "No such device or address")[0]
        assert kind == "cancelled"


class TestIcons:
    def test_all_defined(self):
        for name in ("download", "refresh", "copy", "terminal", "chevron-down",
                     "check-circle", "error-circle", "warn-triangle",
                     "info-circle", "box", "select-all", "select-none"):
            assert name in app._ICON_SVGS


class TestManagedSources:
    """Getting a package off the AUR: detection and classification."""

    def test_official_repos_are_recognised(self):
        for repo in ("core", "extra", "multilib", "extra-testing"):
            assert app._source_key(repo) == app.SRC_OFFICIAL
            assert app._source_label(repo) == "official"

    def test_binary_aur_repos_are_managed_and_named(self):
        for repo in ("cachyos", "chaotic-aur", "archlinuxcn", "alhp",
                     "garuda"):
            assert app._source_key(repo) == app.SRC_MANAGED
            # Named, not swallowed into "official": which farm built it
            # matters when you are deciding what to trust.
            assert app._source_label(repo) == repo

    def test_parse_sync_info_reads_the_repo(self):
        out = (
            "Repository      : cachyos\n"
            "Name            : brave-bin\n"
            "Version         : 1.80.0-1\n"
            "\n"
            "Repository      : extra\n"
            "Name            : discord\n"
            "Version         : 0.0.100-1\n")
        info = app.parse_sync_info(out)
        assert info["brave-bin"] == ("1.80.0-1", "cachyos")
        assert info["discord"] == ("0.0.100-1", "extra")

    def test_enabled_repos_are_read_from_pacman_conf(self, tmp_path,
                                                    monkeypatch):
        conf = tmp_path / "pacman.conf"
        conf.write_text("[options]\nHoldPkg = pacman\n\n[cachyos-v3]\n"
                        "Include = /etc/pacman.d/cachyos-v3-mirrorlist\n"
                        "\n[extra]\n")
        # Point the reader at the fixture without touching the real file.
        real_open = open

        def fake_open(path, *a, **k):
            if str(path) == "/etc/pacman.conf":
                return real_open(conf, *a, **k)
            return real_open(path, *a, **k)

        monkeypatch.setattr("builtins.open", fake_open)
        assert app.enabled_repos() == {"cachyos-v3", "extra"}

    def test_sources_table_covers_the_known_good_ones(self):
        probes = {probe for _name, probe, _steps, _why in app._MANAGED_SOURCES}
        assert {"core", "cachyos", "chaotic-aur", "archlinuxcn",
                "flatpak"} <= probes
        for name, _probe, _steps, why in app._MANAGED_SOURCES:
            assert name and len(why) > 40, name


class TestDesignSystem:
    def test_type_scale_is_ordered(self):
        sizes = [app._sp(n) for n in (11, 12, 13, 20, 22)]
        assert sizes == sorted(sizes)
        assert len(set(sizes)) == len(sizes)

    def test_grid_values_are_multiples_of_four(self):
        # The strict grid: every structural spacing value is a 4px step.
        for n in (4, 8, 12, 16, 24, 52):
            assert app._sp(n) % 4 == 0 or app._SCALE != 1.0

    def test_semantic_colors_come_from_the_scheme(self, monkeypatch):
        monkeypatch.setattr(app, "_KDE_CACHE", {}, raising=False)
        monkeypatch.setattr(app, "_TOKEN_CACHE", {}, raising=False)
        colors = [app._accent_hex(), app._positive_hex(),
                  app._warning_hex(), app._negative_hex(), app._link_hex()]
        for color in colors:
            assert color.startswith("#") and len(color) == 7

    def test_qss_has_no_stray_placeholders(self):
        qss = app._themed_qss()
        assert "%(" not in qss
        assert "pxpx" not in qss

    def test_soft_color_is_rgba(self):
        assert app._soft("#ff0000").startswith("rgba(255,0,0,")


class TestRebootProbe:
    def test_true_when_running_modules_are_gone(self, monkeypatch):
        monkeypatch.setattr(app.os.path, "isdir", lambda p: False)
        assert app.reboot_required() is True

    def test_false_when_running_modules_exist(self, monkeypatch):
        monkeypatch.setattr(app.os.path, "isdir", lambda p: True)
        assert app.reboot_required() is False


class TestSettings:
    def test_roundtrip(self, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        monkeypatch.setattr(app, "_settings_file", lambda: str(target))
        assert app.load_settings() == {}
        app.save_settings({"geometry": [1, 2, 3, 4]})
        assert app.load_settings() == {"geometry": [1, 2, 3, 4]}

    def test_broken_file_is_not_fatal(self, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        target.write_text("{not json")
        monkeypatch.setattr(app, "_settings_file", lambda: str(target))
        assert app.load_settings() == {}


class TestAskpassEnable:
    """Source-enable runner: command shape and askpass lifecycle."""

    def test_runner_uses_sudo_a_not_pkexec(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda name: "/usr/bin/sudo")
        r = app._SourceEnableRunner("echo hello")
        # Construction-time shape (the real script path is filled in run()).
        assert r._cmd == ["sudo", "-A", "bash", "<script>"]
        assert r._askpass is not None

    def test_askpass_script_is_0700_and_kdialog(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda name: "/usr/bin/sudo")
        r = app._SourceEnableRunner("echo hello")
        askpass = r._askpass
        assert askpass is not None
        assert os.path.exists(askpass)
        mode = os.stat(askpass).st_mode & 0o777
        assert mode == 0o700
        body = open(askpass).read()
        assert "kdialog" in body
        assert "zenity" in body
        assert "exit 1" in body
        r._cleanup()
        assert not os.path.exists(askpass)
        assert r._askpass is None

    def test_pkexec_fallback_when_no_sudo(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda name: None)
        r = app._SourceEnableRunner("echo hello")
        assert r._cmd[0] == "pkexec"
        r._cleanup()

    def test_env_exports_askpass(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda name: "/usr/bin/sudo")
        r = app._SourceEnableRunner("echo hello")
        env = r._child_env()
        assert env["SUDO_ASKPASS"] == r._askpass
        r._cleanup()

    def test_cleanup_runs_on_every_exit_path(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda name: "/usr/bin/sudo")
        r = app._SourceEnableRunner("echo hello")
        # Simulate the runner finishing.
        r._cleanup()
        assert r._askpass is None
        # Second cleanup is a no-op (no crash).
        r._cleanup()
        assert r._askpass is None


class TestManagedSourceProbe:
    """The Sources panel must never guess a source's state."""

    def test_pacman_repo_probe_reads_pacman_conf(self, monkeypatch):
        monkeypatch.setattr(app, "enabled_repos", lambda: {"chaotic-aur"})
        assert app._managed_source_ready("chaotic-aur", {"chaotic-aur"})
        assert not app._managed_source_ready("archlinuxcn", {"chaotic-aur"})

    def test_flatpak_probe_is_not_a_pacman_repo(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)

            class R:
                stdout = "flathub\n"
            return R()

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/flatpak")
        monkeypatch.setattr(app.subprocess, "run", fake_run)
        assert app._flatpak_ready() is True
        assert calls and calls[0][0] == "flatpak"
        # A pacman.conf without flatpak in it must not deny Flatpak.
        assert app._managed_source_ready("flatpak", set()) is True

    def test_flatpak_probe_needs_the_binary(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        assert app._flatpak_ready() is False
        assert app._managed_source_ready("flatpak", set()) is False

    def test_flatpak_probe_needs_the_flathub_remote(self, monkeypatch):
        class R:
            stdout = "flathub-beta\n"

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/flatpak")
        monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
        assert app._flatpak_ready() is False

    def test_flatpak_probe_survives_a_broken_flatpak(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/flatpak")

        def boom(*a, **k):
            raise OSError("no flatpak here")

        monkeypatch.setattr(app.subprocess, "run", boom)
        assert app._flatpak_ready() is False


class TestPacmanLock:
    """A locked database must be recognised before a removal is launched."""

    def test_lock_file_is_the_signal(self, monkeypatch, tmp_path):
        lock = tmp_path / "db.lck"
        monkeypatch.setattr(app, "_PACMAN_LOCK", str(lock))
        assert app.pacman_lock_held() is False
        lock.write_text("")
        assert app.pacman_lock_held() is True
        lock.unlink()
        assert app.pacman_lock_held() is False

    def test_lock_failure_is_classified_as_busy(self):
        kind, title, advice = app.classify_update_failure(
            "error: failed to init transaction (unable to lock database)\n"
            "  if you're sure a package manager is not already\n"
            "  running, you can remove /var/lib/pacman/db.lck")
        assert kind == "busy"
        assert "package manager" in title.lower()
        assert "nothing was changed" in advice.lower()

    def test_a_plain_lock_message_is_busy_too(self):
        # Some pacman builds only print the lock line.
        kind, _title, _advice = app.classify_update_failure(
            "error: could not lock database: File exists")
        assert kind == "busy"

    def test_busy_is_not_confused_with_a_real_package_failure(self):
        kind, _t, _a = app.classify_update_failure(
            "error: failed to commit transaction (conflicting files)")
        assert kind != "busy"


class TestSourceKeys:
    """One vocabulary for where a package came from."""

    def test_official_repos_and_the_pseudo_name(self):
        for repo in ("official", "core", "extra", "multilib",
                     "core-testing", "kde-unstable"):
            assert app._source_key(repo) == app.SRC_OFFICIAL

    def test_aur_and_managed_repos(self):
        assert app._source_key("aur") == app.SRC_AUR
        for repo in ("cachyos-v3", "chaotic-aur", "archlinuxcn"):
            assert app._source_key(repo) == app.SRC_MANAGED

    def test_flatpak_and_manual_are_sources_of_their_own(self):
        assert app._source_key("flatpak") == app.SRC_FLATPAK
        assert app._source_key("") == app.SRC_MANUAL
        assert app._source_label("flatpak") == "flatpak"
        assert app._source_label("cachyos-v3") == "cachyos"

    def test_every_source_has_a_badge_colour(self):
        for repo in ("official", "aur", "flatpak", "", "cachyos-v3",
                     "extra-testing"):
            rgb, fg = app._repo_tint(repo)
            assert len(rgb) == 3 and fg.startswith("#")


class TestRepoBadges:
    """A row's pill names the repo that actually provides the package."""

    def test_archs_own_repos_are_one_word(self):
        for repo in ("official", "core", "extra", "multilib",
                     "extra-testing"):
            assert app._repo_badge(repo) == "OFFICIAL"

    def test_a_cachyos_build_never_wears_archs_badge(self):
        # The four cachyos-* repos are one build farm, and the pill has no
        # room for "cachyos-extra-v3": the whole family reads CACHYOS.
        for repo in ("cachyos", "cachyos-v3", "cachyos-extra-v3",
                     "cachyos-core-v3"):
            assert app._repo_family(repo) == "CachyOS"
            assert app._repo_badge(repo) == "CACHYOS"

    def test_other_farms_keep_their_own_name(self):
        assert app._repo_badge("chaotic-aur") == "CHAOTIC-AUR"
        assert app._repo_badge("archlinuxcn") == "ARCHLINUXCN"
        assert app._repo_badge("alhp") == "ALHP"

    def test_a_pseudo_source_names_itself(self):
        assert app._repo_badge("aur") == "AUR"
        assert app._repo_badge("flatpak") == "FLATPAK"
        assert app._repo_badge("appimage") == "APPIMAGE"
        assert app._repo_badge("manual") == "MANUAL"

    def test_nothing_known_means_no_pill(self):
        assert app._repo_badge("") == ""


class TestInstalledRepos:
    """Where a tracked package came from: pacman's own local DB."""

    def _db(self, tmp_path, entries):
        for entry, name, repo in entries:
            d = tmp_path / entry
            d.mkdir()
            text = f"%NAME%\n{name}\n\n%VERSION%\n1\n\n"
            if repo:
                text += f"%INSTALLED_DB%\n{repo}\n\n"
            (d / "desc").write_text(text, encoding="utf-8")
        return str(tmp_path)

    def test_local_db_records_are_read(self, monkeypatch, tmp_path):
        root = self._db(tmp_path, [
            ("cachy-update-4.4.3-1", "cachy-update", "cachyos"),
            ("coreutils-9.12-2.1", "coreutils", "core"),
            ("oldtimer-1.0-1", "oldtimer", None)])
        monkeypatch.setattr(app, "PACMAN_LOCAL_DB", root)
        assert app._installed_db_repos() == {"cachy-update": "cachyos",
                                             "coreutils": "core"}

    def test_a_missing_db_is_no_map(self, monkeypatch):
        monkeypatch.setattr(app, "PACMAN_LOCAL_DB", "/nonexistent/db")
        assert app._installed_db_repos() == {}

    def test_explicit_packages_are_labelled_by_where_they_came_from(
            self, monkeypatch):
        class R:
            stdout, returncode = "konsole 24.1-1\ncachy-update 4.4.3-1\n" \
                                 "yay-bin 12.0-1\n", 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {
            "konsole": "extra", "cachy-update": "cachyos",
            "yay-bin": "aur"})
        repos = {p["name"]: p["repo"] for p in app.get_installed_packages()}
        assert repos == {"konsole": "extra", "cachy-update": "cachyos",
                         "yay-bin": "aur"}

    def test_a_package_from_before_installed_db_keeps_its_old_answer(
            self, monkeypatch):
        class R:
            stdout, returncode = "konsole 24.1-1\n", 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        assert app.get_installed_packages() == [
            {"name": "konsole", "version": "24.1-1", "repo": "official"}]

    def test_where_it_came_from_beats_who_provides_it_now(self, monkeypatch):
        monkeypatch.setattr(app, "_installed_db_repos",
                            lambda: {"konsole": "extra"})
        monkeypatch.setattr(app, "_sync_repo_map",
                            lambda: {"konsole": "cachyos-extra-v3",
                                     "ghost": "extra"})
        monkeypatch.setattr(app, "_foreign_pkg_set", lambda: {"yay-bin"})
        assert app._installed_repo_map() == {
            "konsole": "extra", "ghost": "extra", "yay-bin": "aur"}

    def test_the_sync_map_keeps_the_first_repo_a_name_appears_in(
            self, monkeypatch):
        class R:
            stdout = ("konsole\tcachyos-extra-v3\n"
                      "konsole\textra\n"
                      "firefox\tcachyos-extra-v3\n")
            returncode = 0

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/expac")
        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        assert app._sync_repo_map() == {"konsole": "cachyos-extra-v3",
                                        "firefox": "cachyos-extra-v3"}

    def test_without_expac_there_is_no_sync_map(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        assert app._sync_repo_map() == {}

    def test_a_cachyos_package_lands_in_the_cachyos_bucket(self):
        # The strip filters by source key, so the honest repo name has to
        # keep counting as "managed" — not as official.
        assert app._source_key("cachyos-extra-v3") == app.SRC_MANAGED
        assert app._source_key("extra") == app.SRC_OFFICIAL


class TestFlatpakDetection:
    """A Flatpak app must be recognisable from its launcher."""

    def test_launcher_in_an_exports_dir_is_flatpak(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.setattr(app, "_flatpak_export_dirs",
                            lambda: [str(tmp_path)])
        path = tmp_path / "com.discordapp.Discord.desktop"
        path.write_text("[Desktop Entry]\nName=Discord\n")
        assert app._flatpak_app_id(str(path)) == "com.discordapp.Discord"

    def test_a_normal_launcher_is_not_flatpak(self):
        assert app._flatpak_app_id("/usr/share/applications/zen.desktop") == ""

    def test_exec_that_runs_through_flatpak_gives_the_app_id(self):
        exec_line = ("/usr/bin/flatpak run --branch=stable --arch=x86_64 "
                     "--command=ticktick --file-forwarding "
                     "com.ticktick.TickTick @@u %U @@")
        assert app._flatpak_exec_id(exec_line) == "com.ticktick.TickTick"
        assert app._flatpak_exec_id("/usr/bin/zen %u") == ""
        assert app._flatpak_exec_id("") == ""

    def test_the_exports_dir_wins_over_the_exec(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "_flatpak_export_dirs",
                            lambda: [str(tmp_path)])
        path = tmp_path / "org.openshot.OpenShot.desktop"
        path.write_text("[Desktop Entry]\nName=OpenShot\n")
        app_row = {"file": str(path),
                   "exec": "/usr/bin/flatpak run com.ticktick.TickTick"}
        assert app._flatpak_id_for(app_row) == "org.openshot.OpenShot"

    def test_flatpak_apps_parses_the_list(self, monkeypatch):
        class R:
            stdout = ("com.discordapp.Discord\t1.0.151\tsystem\n"
                      "com.ticktick.TickTick\t8.0.20\tuser\n"
                      "broken line\n")

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/flatpak")
        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        apps = app._flatpak_apps()
        assert apps["com.discordapp.Discord"] == ("1.0.151", "system")
        assert apps["com.ticktick.TickTick"] == ("8.0.20", "user")

    def test_no_flatpak_binary_means_no_flatpak_source(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        assert app._flatpak_apps() == {}


class TestFlatpakRemoverCommands:
    """Flatpak is not pacman: no -Rns, and only system apps need root."""

    def test_user_apps_run_without_sudo(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/sudo")
        r = app.FlatpakRemover(["com.ticktick.TickTick"],
                               {"com.ticktick.TickTick": "user"})
        assert r._batches == [
            ["flatpak", "uninstall", "--noninteractive", "--assumeyes",
             "--user", "com.ticktick.TickTick"]]
        assert r._askpass is None

    def test_system_apps_run_under_sudo_with_askpass(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/sudo")
        r = app.FlatpakRemover(["com.discordapp.Discord"],
                               {"com.discordapp.Discord": "system"})
        assert r._batches[0][:3] == ["sudo", "-A", "flatpak"]
        assert "--system" in r._batches[0]
        assert r._askpass is not None
        r._cleanup()
        assert r._askpass is None

    def test_a_mixed_selection_runs_the_user_batch_first(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/sudo")
        r = app.FlatpakRemover(
            ["com.discordapp.Discord", "com.ticktick.TickTick"],
            {"com.ticktick.TickTick": "user"})
        assert len(r._batches) == 2
        assert "--user" in r._batches[0]
        assert "--system" in r._batches[1]
        r._cleanup()


class TestRequestReboot:
    """The reboot button must ask the desktop, not fight polkit."""

    def test_kde_session_call_comes_first(self, monkeypatch):
        seen = []

        class R:
            returncode = 0

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(app, "_run",
                            lambda cmd, **k: (seen.append(cmd), R())[1])
        assert app.request_reboot() is True
        assert seen == [["qdbus6", "org.kde.Shutdown", "/Shutdown",
                         "org.kde.Shutdown.logoutAndReboot"]]

    def test_systemctl_is_the_last_resort(self, monkeypatch):
        seen = []

        class R:
            def __init__(self, code):
                self.returncode = code

        def fake_run(cmd, **k):
            seen.append(cmd)
            return R(1 if cmd[0] != "systemctl" else 0)

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(app, "_run", fake_run)
        assert app.request_reboot() is True
        assert [c[0] for c in seen] == ["qdbus6", "qdbus", "systemctl"]

    def test_nothing_available_reports_failure(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        assert app.request_reboot() is False

    def test_a_broken_call_does_not_stop_the_next_one(self, monkeypatch):
        seen = []

        class R:
            returncode = 0

        def fake_run(cmd, **k):
            seen.append(cmd[0])
            if cmd[0] == "qdbus6":
                raise OSError("no bus")
            return R()

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(app, "_run", fake_run)
        assert app.request_reboot() is True
        assert seen == ["qdbus6", "qdbus"]

    def test_on_warning_ink_stays_legible(self):
        ink = app.QColor(app._on_warning_hex())
        fill = app.QColor(app._warning_hex())
        assert ink.isValid() and ink != fill
        # The ink is picked against the fill's lightness, whichever way the
        # scheme's warning colour leans: dark ink on a bright fill, light
        # ink on a deep one (Breeze's amber takes white).
        assert ((ink.lightness() > fill.lightness())
                == (fill.lightness() <= 150))


class TestPackageLinks:
    """Every source links to its own home — or to nothing at all."""

    def test_aur_goes_to_the_aur(self):
        assert app._package_url("zen-browser-bin", "aur") == \
            "https://aur.archlinux.org/packages/zen-browser-bin"

    def test_flatpak_goes_to_flathub(self):
        assert app._package_url("com.discordapp.Discord", "flatpak") == \
            "https://flathub.org/apps/com.discordapp.Discord"

    def test_repo_packages_go_to_the_arch_search(self):
        for repo in ("official", "core", "extra", "extra-testing"):
            url = app._package_url("konsole", repo)
            assert url == "https://archlinux.org/packages/?q=konsole"

    def test_a_cachyos_build_links_to_cachyos_own_search(self):
        # Not Arch's page for the name: the package you are running came
        # off CachyOS's build farm, and their search shows that build.
        for repo in ("cachyos", "cachyos-extra-v3", "cachyos-core-v3"):
            assert app._package_url("cachyos-hello", repo) == \
                "https://packages.cachyos.org/?q=cachyos-hello"

    def test_an_appimage_links_only_to_its_own_page(self):
        # A file has no registry: no URL in the launcher, no link.
        assert app._package_url("Claude", "appimage") == ""
        assert app._package_url("Paper", "appimage",
                                "https://paper.example/") == \
            "https://paper.example/"

    def test_a_manual_install_links_only_to_its_own_page(self):
        assert app._package_url("Stray", "manual") == ""
        assert app._package_url("Stray", "manual", "https://stray.game") == \
            "https://stray.game"

    def test_a_flatpak_app_never_links_to_the_aur(self):
        # The reported bug: a Flatpak app opened an AUR-shaped page.
        url = app._package_url("com.obsproject.Studio", "flatpak")
        assert "aur.archlinux.org" not in url
        assert "archlinux.org/packages" not in url


class TestDependencyRequirers:
    """A stray dependency should name the applications that need it."""

    def test_a_direct_requirer(self):
        req = {"libfoo": ["steam"]}
        assert app._explicit_requirers("libfoo", req, {"steam"}) == ["steam"]

    def test_a_chain_is_walked_to_the_application(self):
        # libfoo ← libbar ← steam: the user wants to see Steam.
        req = {"libfoo": ["libbar"], "libbar": ["steam"]}
        assert app._explicit_requirers("libfoo", req, {"steam"}) == ["steam"]

    def test_a_chain_that_ends_in_nothing_explicit(self):
        # libfoo ← libbar ← (nothing): no application needs it.
        req = {"libfoo": ["libbar"]}
        assert app._explicit_requirers("libfoo", req, {"steam"}) == []

    def test_a_cycle_does_not_hang(self):
        req = {"a": ["b"], "b": ["a"]}
        assert app._explicit_requirers("a", req, set()) == []

    def test_every_requirer_is_reported(self):
        req = {"libfoo": ["a", "b", "c", "d", "e"]}
        found = app._explicit_requirers("libfoo", req, {"a", "b", "c", "d", "e"})
        assert found == ["a", "b", "c", "d", "e"]

    def test_required_by_map_parses_expac(self, monkeypatch):
        class R:
            stdout = ("libfoo|steam lutris\n"
                      "libbar|0\n")

        monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/expac")
        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        req = app.required_by_map()
        assert req["libfoo"] == ["steam", "lutris"]
        assert req["libbar"] == ["0"]      # expac prints 0 for "nothing"

    def test_required_by_map_without_expac_says_nothing(self, monkeypatch):
        monkeypatch.setattr(app.shutil, "which", lambda n: None)
        assert app.required_by_map() == {}


class TestDependencyRows:
    """Dependency rows carry the apps that need them, by their names."""

    def test_load_deps_names_the_apps_that_need_each_package(
            self, monkeypatch):
        class R:
            stdout = "libfoo 1-1\nlibbar 2-1\n"
            returncode = 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "required_by_map",
                            lambda: {"libfoo": ["steam"]})
        monkeypatch.setattr(app, "_explicit_package_set", lambda: {"steam"})
        monkeypatch.setattr(app, "all_package_descs",
                            lambda: {"libfoo": "A foo library"})
        rows = app.LibraryLoader()._load_deps({"steam": "Steam"})
        by_name = {r["name"]: r for r in rows}
        assert by_name["libfoo"]["needs"] == ["Steam"]
        assert by_name["libfoo"]["needs_known"] is True
        assert by_name["libfoo"]["kind"] == "dependency"
        # The description line stays the package's own description: the
        # applications that need it live in their own block, never
        # inside it.
        assert by_name["libfoo"]["desc"] == "A foo library"
        # Nothing needs libbar — a known fact, not a missing list.
        assert by_name["libbar"]["needs"] == []
        assert by_name["libbar"]["needs_known"] is True

    def test_the_names_fall_back_to_the_package_name(self, monkeypatch):
        class R:
            stdout = "libfoo 1-1\n"
            returncode = 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "required_by_map",
                            lambda: {"libfoo": ["gaze-bin"]})
        monkeypatch.setattr(app, "_explicit_package_set", lambda: {"gaze-bin"})
        monkeypatch.setattr(app, "all_package_descs", lambda: {})
        rows = app.LibraryLoader()._load_deps({})
        assert rows[0]["needs"] == ["gaze-bin"]

    def test_without_requirer_data_the_row_claims_nothing(self, monkeypatch):
        class R:
            stdout = "libfoo 1-1\n"
            returncode = 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "required_by_map", lambda: {})
        monkeypatch.setattr(app, "all_package_descs", lambda: {})
        rows = app.LibraryLoader()._load_deps({})
        assert rows[0]["needs"] == []
        assert rows[0]["needs_known"] is False


class TestLoadSteps:
    """A list load reports its stages as a bar that fills (see _LoadSteps)."""

    def test_each_stage_takes_the_share_its_weight_declares(self):
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append((p, what)))
        steps.plan([("First…", 3), ("Second…", 1)])
        steps.begin()
        assert seen == [(0, "First…")]
        steps.item(1, 2)
        assert seen[-1][0] == 375           # half of the first stage
        steps.begin()
        assert seen[-1] == (750, "Second…")
        steps.finish()
        assert seen[-1] == (1000, "Second…")

    def test_items_fill_the_stage_that_is_running(self):
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append(p))
        steps.plan([("Work…", 1)])
        steps.begin()
        steps.item(50, 100)
        assert seen == [0, 500]

    def test_the_bar_only_ever_moves_forward(self):
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append(p))
        steps.plan([("Work…", 1)])
        steps.begin()
        for i in range(1, 2001):
            steps.item(i, 2000)
        assert seen == sorted(set(seen))
        assert seen[0] == 0 and seen[-1] == 1000
        # ~1% steps: a 1900-item loop never writes what it cannot show.
        assert len(seen) <= 102

    def test_a_new_stage_is_said_even_at_the_same_permille(self):
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append((p, what)))
        steps.plan([("One…", 1), ("Two…", 1)])
        steps.begin()
        steps.item(10, 10)
        steps.begin()               # same place on the bar, new label
        assert seen[-2:] == [(500, "One…"), (500, "Two…")]

    def test_a_load_with_nothing_to_say_stays_silent(self):
        # The loaders hand a no-op reporter to helpers called on their own
        # (see _load_apps), so those calls must never fabricate progress.
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append(p))
        steps.begin()
        steps.item(1, 2)
        steps.finish()
        assert seen == []

    def test_one_stage_alone_still_reaches_a_full_bar(self):
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append(p))
        steps.plan([("Only…", 7)])
        steps.begin()
        steps.finish()
        assert seen == [0, 1000]


class TestLoadProgress:
    """The Library load narrates its stages for the status line and track."""

    def test_the_library_load_names_every_stage_it_fills(self, monkeypatch):
        class R:
            stdout = ""
            returncode = 1      # nothing to answer for; it still narrates

        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "get_installed_packages",
                            lambda repos=None: [])
        monkeypatch.setattr(app, "required_by_map", lambda: {})
        monkeypatch.setattr(app, "flatpak_tracked_rows", lambda: [])
        monkeypatch.setattr(app, "appimage_rows", lambda: [])
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [])
        monkeypatch.setattr(app, "all_package_descs", lambda: {})
        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        seen = []
        loader = app.LibraryLoader()
        loader.progress.connect(lambda p, what: seen.append((p, what)))
        loader._load_all()
        assert seen == [
            (0, "Reading the installed packages…"),
            (349, "Working out what needs what…"),
            (523, "Adding Flatpak apps and AppImages…"),
            (555, "Matching applications to packages…"),
            (714, "Naming the apps behind each dependency…"),
            (1000, "Naming the apps behind each dependency…")]

    def test_the_dependency_rows_fill_their_stage_row_by_row(self,
                                                             monkeypatch):
        class R:
            stdout = "".join(f"lib{i} 1-1\n" for i in range(400))
            returncode = 0

        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "required_by_map", lambda: {"lib1": ["x"]})
        monkeypatch.setattr(app, "_explicit_package_set", lambda: {"x"})
        monkeypatch.setattr(app, "all_package_descs", lambda: {})
        seen = []
        steps = app._LoadSteps(lambda p, what: seen.append(p))
        steps.plan([("Reading the dependency list…", 3), ("Rows…", 1)])
        steps.begin()
        rows = app.LibraryLoader()._load_deps({}, {}, steps)
        assert len(rows) == 400
        assert seen[0] == 0 and seen[-1] == 1000
        assert len(seen) > 10       # 400 rows fill it, they do not jump it


class TestLibraryRequirers:
    """Library rows say what would go with them, like stray dependencies.

    Every row wears the same one-line requirer summary; for your own
    packages the question is the other way round — what would come off
    with this package — and the whole list opens in the panel under the
    list.
    """

    def _loaded(self, monkeypatch, required_by):
        """One whole Library read, with the machine's answers patched in."""
        class R:
            stdout = ""
            returncode = 1

        monkeypatch.setattr(app, "_installed_repo_map", lambda: {})
        monkeypatch.setattr(app, "scan_desktop_files", lambda: [])
        monkeypatch.setattr(app, "all_package_descs", lambda: {})
        monkeypatch.setattr(app, "flatpak_tracked_rows", lambda: [])
        monkeypatch.setattr(app, "appimage_rows", lambda: [])
        monkeypatch.setattr(app, "required_by_map", lambda: required_by)
        monkeypatch.setattr(app, "_run", lambda *a, **k: R())
        got = []
        loader = app.LibraryLoader()
        loader.done.connect(got.append)
        loader._load_all()
        return {r["name"]: r for r in got[0]}

    def test_the_library_names_the_explicit_packages_that_need_each(
            self, monkeypatch):
        monkeypatch.setattr(app, "get_installed_packages",
                            lambda repos=None: [
            {"name": "bluez-libs", "version": "1-1", "repo": "official"},
            {"name": "pipewire", "version": "2-1", "repo": "official"}])
        monkeypatch.setattr(app, "_explicit_package_set",
                            lambda: {"plasma-desktop", "pipewire"})
        rows = self._loaded(monkeypatch, {
            "bluez-libs": ["kdeconnect"],
            "kdeconnect": ["plasma-desktop"],
            "pipewire": []})
        # The walk climbs to the package the user installed, not the
        # library in between.
        assert rows["bluez-libs"]["needs"] == ["plasma-desktop"]
        assert rows["bluez-libs"]["needs_known"] is True
        # Nothing needs pipewire — a known fact, not a missing list.
        assert rows["pipewire"]["needs"] == []
        assert rows["pipewire"]["needs_known"] is True

    def test_without_requirer_data_the_rows_claim_nothing(self, monkeypatch):
        monkeypatch.setattr(app, "get_installed_packages",
                            lambda repos=None: [
            {"name": "pipewire", "version": "1-1", "repo": "official"}])
        rows = self._loaded(monkeypatch, {})
        assert rows["pipewire"]["needs"] == []
        assert rows["pipewire"]["needs_known"] is False


class TestDesktopLaunch:
    """A .desktop Exec is not a command line."""

    def test_field_codes_are_dropped(self, tmp_path):
        f = tmp_path / "zen.desktop"
        f.write_text("[Desktop Entry]\nName=Zen\nExec=/usr/bin/zen %U\n"
                     "Type=Application\n")
        assert app.desktop_argv(str(f)) == ["/usr/bin/zen"]

    def test_flatpak_forwarding_markers_are_dropped(self, tmp_path):
        # The shape Flatpak writes: @@u %U @@ around the forwarded file.
        # With no file to forward, the markers are arguments the app never
        # asked for — and bwrap used to die on them.
        f = tmp_path / "com.discordapp.Discord.desktop"
        f.write_text(
            "[Desktop Entry]\nName=Discord\n"
            "Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 "
            "--command=com.discordapp.Discord --file-forwarding "
            "com.discordapp.Discord @@u %U @@\nType=Application\n")
        argv = app.desktop_argv(str(f))
        assert argv[:2] == ["/usr/bin/flatpak", "run"]
        assert argv[-1] == "com.discordapp.Discord"
        assert not [a for a in argv if a.startswith("@@")]

    def test_a_launcher_with_no_exec_launches_nothing(self, tmp_path):
        f = tmp_path / "empty.desktop"
        f.write_text("[Desktop Entry]\nName=Nothing\nType=Application\n")
        assert app.desktop_argv(str(f)) == []

    def test_name_and_icon_codes_expand(self, tmp_path):
        f = tmp_path / "app.desktop"
        f.write_text("[Desktop Entry]\nName=My App\n"
                     "Exec=/usr/bin/app --name %c\nType=Application\n")
        assert app.desktop_argv(str(f)) == ["/usr/bin/app", "--name", "My App"]

    def test_the_document_portal_check_follows_the_doc_dir(self, monkeypatch,
                                                           tmp_path):
        doc = tmp_path / "doc"
        monkeypatch.setattr(app.os, "getuid", lambda: 1234)
        monkeypatch.setattr(app.os.path, "isdir",
                            lambda p: p == str(doc) if "doc" in p else False)
        # No by-app under it → the portal is not providing anything.
        assert app.flatpak_portal_missing() is True
