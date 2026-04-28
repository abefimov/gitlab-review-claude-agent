from claude_reviewer.diff_parser import parse_addressable_lines, LineInfo


def test_simple_added_lines():
    diff = """diff --git a/foo.py b/foo.py
index 1234..5678 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,5 @@
 x = 1
+y = 2
+z = 3
 print(x)
"""
    a = parse_addressable_lines(diff)
    # context: 1 (was 1, still 1), 4 (was 2, now after +'s)
    # added: 2, 3
    assert ("foo.py", 1) in a
    assert ("foo.py", 2) in a
    assert ("foo.py", 3) in a
    assert ("foo.py", 4) in a
    assert ("foo.py", 5) not in a  # outside hunk


def test_deletion_does_not_advance_new_line():
    diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,3 @@
 a
-b
 c
 d
"""
    a = parse_addressable_lines(diff)
    assert ("foo.py", 1) in a  # 'a'
    # 'b' is deleted — its new_line is not addressable
    assert ("foo.py", 2) in a  # 'c' is now line 2
    assert ("foo.py", 3) in a  # 'd' is now line 3
    assert ("foo.py", 4) not in a


def test_lines_outside_hunks_not_addressable():
    diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -10,2 +10,2 @@
 line10
+line11
"""
    a = parse_addressable_lines(diff)
    assert ("foo.py", 10) in a
    assert ("foo.py", 11) in a
    assert ("foo.py", 5) not in a
    assert ("foo.py", 100) not in a


def test_multiple_files():
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
 a
+aa
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,1 +5,1 @@
-b
+bb
"""
    a = parse_addressable_lines(diff)
    assert ("a.py", 1) in a
    assert ("a.py", 2) in a
    assert ("b.py", 5) in a
    assert ("a.py", 5) not in a
    assert ("b.py", 1) not in a


def test_line_kinds_added_vs_context():
    diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -10,3 +10,5 @@
 a
+b
+c
 d
 e
"""
    a = parse_addressable_lines(diff)
    # added lines have no old_line
    assert a[("foo.py", 11)] == LineInfo(kind="add", old_path=None, old_line=None)
    assert a[("foo.py", 12)] == LineInfo(kind="add", old_path=None, old_line=None)
    # context lines carry old_path + old_line so we can build a valid line_code
    assert a[("foo.py", 10)] == LineInfo(kind="context", old_path="foo.py", old_line=10)
    # 'd' was line 11 in old file, now 13 in new (after two '+')
    assert a[("foo.py", 13)] == LineInfo(kind="context", old_path="foo.py", old_line=11)
    assert a[("foo.py", 14)] == LineInfo(kind="context", old_path="foo.py", old_line=12)


def test_new_file_after_modified_file_does_not_inherit_old_path():
    # A modified file followed by a newly-added file. For the new file git
    # emits '--- /dev/null', which our _OLD_FILE_RE doesn't match. Without
    # a file-boundary reset, current_old would still hold "a.py" while
    # parsing b.py.
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 x
-old
+new
diff --git a/b.py b/b.py
new file mode 100644
--- /dev/null
+++ b/b.py
@@ -0,0 +1,2 @@
+hello
+world
"""
    a = parse_addressable_lines(diff)
    # b.py has only added lines, so old_path/old_line must be None
    assert a[("b.py", 1)] == LineInfo(kind="add", old_path=None, old_line=None)
    assert a[("b.py", 2)] == LineInfo(kind="add", old_path=None, old_line=None)
    # a.py context line still carries its own old_path
    assert a[("a.py", 1)] == LineInfo(kind="context", old_path="a.py", old_line=1)


def test_old_line_advances_past_deletions():
    diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,3 @@
 a
-b
 c
 d
"""
    a = parse_addressable_lines(diff)
    # 'a' is context at new=1, old=1
    assert a[("foo.py", 1)] == LineInfo(kind="context", old_path="foo.py", old_line=1)
    # 'c' was old=3 (after deleted 'b' at old=2), now new=2
    assert a[("foo.py", 2)] == LineInfo(kind="context", old_path="foo.py", old_line=3)
    # 'd' was old=4, now new=3
    assert a[("foo.py", 3)] == LineInfo(kind="context", old_path="foo.py", old_line=4)
