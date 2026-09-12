"""Pure-logic tests: changelog detection, failure classification, helpers."""
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

    def test_signal_kept(self):
        assert not app.is_log_noise("error: target not found: foo")
        assert not app.is_log_noise(":: installing konsole (1/2)")


class TestIcons:
    def test_all_defined(self):
        for name in ("download", "refresh", "copy", "terminal", "chevron-down",
                     "check-circle", "error-circle", "warn-triangle",
                     "info-circle", "box", "select-all", "select-none"):
            assert name in app._ICON_SVGS
