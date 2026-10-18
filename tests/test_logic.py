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


class TestRepoMoves:
    def test_parse_sync_versions(self):
        out = ("Repository      : extra\nName            : foo\n"
               "Version         : 1.2-3\nDescription     : x\n\n"
               "Repository      : extra\nName            : bar\n"
               "Version         : 4.5-6\n")
        assert app.parse_sync_versions(out) == {"foo": "1.2-3", "bar": "4.5-6"}
        assert app.parse_sync_versions("") == {}

    def test_compare_versions(self, monkeypatch):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            a, b = cmd[1], cmd[2]

            class R:
                stdout = "-1\n" if a < b else ("0\n" if a == b else "1\n")

            return R()

        monkeypatch.setattr(app.subprocess, "run", fake_run)
        assert app.compare_versions("1.0", "2.0") == -1
        assert app.compare_versions("2.0", "2.0") == 0
        assert app.compare_versions("2.0", "1.0") == 1

    def test_find_repo_moves(self, monkeypatch):
        monkeypatch.setattr(app, "get_foreign_packages",
                            lambda: {"newer": "1.0-1", "same": "2.0-1",
                                     "older": "3.0-1", "auronly": "1.0-1"})
        import subprocess as sp

        class R:
            stdout = ("Name            : newer\nVersion         : 1.1-1\n\n"
                      "Name            : same\nVersion         : 2.0-1\n\n"
                      "Name            : older\nVersion         : 2.9-1\n")

        monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(app, "compare_versions",
                            lambda a, b: -1 if a < b else (0 if a == b else 1))
        moves = {m["name"]: m for m in app.find_repo_moves()}
        assert set(moves) == {"newer", "same", "older"}
        assert moves["newer"]["relation"] == "upgrade"
        assert moves["same"]["relation"] == "same"
        assert moves["older"]["relation"] == "downgrade"

    def test_find_repo_moves_scoped(self, monkeypatch):
        seen = {}

        def fake_foreign():
            return {"a": "1-1", "b": "1-1"}

        monkeypatch.setattr(app, "get_foreign_packages", fake_foreign)

        def fake_run(cmd, **kwargs):
            seen["args"] = [c for c in cmd if not c.startswith("-")]
            class R:
                stdout = "Name            : a\nVersion         : 2-1\n"
            return R()

        monkeypatch.setattr(app.subprocess, "run", fake_run)
        monkeypatch.setattr(app, "compare_versions", lambda a, b: -1)
        moves = app.find_repo_moves(["a"])
        assert [m["name"] for m in moves] == ["a"]
        assert "pacman" in seen["args"][0]


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
            assert app._repo_kind(repo) == "official"
            assert app._repo_label(repo) == "official"

    def test_binary_aur_repos_are_managed_and_named(self):
        for repo in ("cachyos", "chaotic-aur", "archlinuxcn", "alhp",
                     "garuda"):
            assert app._repo_kind(repo) == "managed"
            # Named, not swallowed into "official": which farm built it
            # matters when you are deciding what to trust.
            assert app._repo_label(repo) == repo

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
        # The old name→version view still works.
        assert app.parse_sync_versions(out) == {
            "brave-bin": "1.80.0-1", "discord": "0.0.100-1"}

    def test_moves_carry_the_providing_repo(self, monkeypatch):
        monkeypatch.setattr(app, "get_foreign_packages",
                            lambda: {"brave-bin": "1.79.1-1"})
        monkeypatch.setattr(app, "compare_versions", lambda a, b: -1)
        monkeypatch.setattr(app, "_run", lambda *a, **k: type(
            "R", (), {"stdout": "Repository      : cachyos\n"
                                "Name            : brave-bin\n"
                                "Version         : 1.80.0-1\n",
                      "returncode": 0})())
        moves = app.find_repo_moves(["brave-bin"])
        assert moves == [{"name": "brave-bin", "installed": "1.79.1-1",
                          "target": "1.80.0-1", "source": "cachyos",
                          "source_kind": "managed", "relation": "upgrade"}]

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
