from claude_reviewer.diff_filter import filter_diff, filter_stat, matches_any


def test_matches_any_basic():
    assert matches_any("uv.lock", ["uv.lock"])
    assert matches_any("a.pyc", ["*.pyc"])
    assert not matches_any("a.py", ["*.pyc"])
    # fnmatch's * matches /, so these all work as expected:
    assert matches_any("path/to/a.pyc", ["*.pyc"])
    assert matches_any("Pods/Foo/Bar.swift", ["Pods/**"])
    assert matches_any("src/build/output.o", ["**/build/**"])


def test_filter_diff_skips_matched_block():
    diff = """diff --git a/uv.lock b/uv.lock
index 1..2 100644
--- a/uv.lock
+++ b/uv.lock
@@ -1,2 +1,3 @@
 line1
+new
 line2
diff --git a/src/foo.py b/src/foo.py
index 3..4 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,1 +1,2 @@
 a
+b
"""
    out, skipped = filter_diff(diff, ["uv.lock"])
    assert "uv.lock" in skipped
    assert "skipped" in out.lower()
    # foo.py block remains intact
    assert "+b\n" in out
    # original lock contents are gone
    assert "line1\n" not in out


def test_filter_diff_no_globs_returns_unchanged():
    diff = "diff --git a/x.py b/x.py\n+y\n"
    out, skipped = filter_diff(diff, [])
    assert out == diff
    assert skipped == []


def test_filter_stat_drops_matched_path():
    stat = " uv.lock | 99 +++++\n src/foo.py | 3 +-\n"
    out = filter_stat(stat, ["uv.lock"])
    assert "uv.lock" not in out
    assert "src/foo.py" in out


def test_filter_stat_no_globs_unchanged():
    stat = " a.py | 1 +\n"
    assert filter_stat(stat, []) == stat
