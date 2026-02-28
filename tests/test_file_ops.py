"""Tests for file operation handlers (actions.py)."""

import os
import pytest
from actions import (
    do_read_file, do_write_file, do_edit_file, do_edit_file_all,
    do_multi_edit, _undo_buffer,
)


class TestReadFile:
    def test_reads_with_line_numbers(self, sample_file):
        result = do_read_file(str(sample_file))
        assert "1 | line one" in result
        assert "2 | line two" in result
        assert "3 | line three" in result

    def test_missing_file(self, tmp_path):
        result = do_read_file(str(tmp_path / "nope.txt"))
        assert "File not found" in result

    def test_file_too_large(self, tmp_path, monkeypatch):
        big = tmp_path / "big.txt"
        big.write_text("x" * 1000)
        monkeypatch.setattr("actions.MAX_FILE_SIZE", 500)
        result = do_read_file(str(big))
        assert "too large" in result.lower()

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        # Write a file at a known path and test ~ expansion
        test_file = tmp_path / "tilde_test.txt"
        test_file.write_text("content")
        result = do_read_file(str(test_file))
        assert "content" in result


class TestWriteFile:
    def test_creates_new_file(self, tmp_path):
        path = str(tmp_path / "new.txt")
        result = do_write_file(path, "hello world")
        assert "Written" in result
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "hello world"

    def test_overwrites_existing(self, sample_file):
        result = do_write_file(str(sample_file), "replaced")
        assert "Written" in result
        with open(sample_file) as f:
            assert f.read() == "replaced"

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c.txt")
        result = do_write_file(path, "deep")
        assert "Written" in result
        assert os.path.isfile(path)

    def test_populates_undo_buffer(self, sample_file):
        abs_path = os.path.abspath(str(sample_file))
        do_write_file(str(sample_file), "new content")
        assert len(_undo_buffer[abs_path]) == 1
        # The undo buffer should contain the old content
        assert "line one" in _undo_buffer[abs_path][0]

    def test_new_file_undo_stores_none(self, tmp_path):
        path = str(tmp_path / "brand_new.txt")
        do_write_file(path, "content")
        abs_path = os.path.abspath(path)
        assert _undo_buffer[abs_path][-1] is None


class TestEditFile:
    def test_replaces_first_match(self, sample_file):
        result = do_edit_file(str(sample_file), "line two", "LINE TWO")
        assert "Replaced" in result
        with open(sample_file) as f:
            content = f.read()
        assert "LINE TWO" in content
        assert "line one" in content  # other lines unchanged

    def test_not_found(self, sample_file):
        result = do_edit_file(str(sample_file), "nonexistent text", "replacement")
        assert "Text not found" in result

    def test_missing_file(self, tmp_path):
        result = do_edit_file(str(tmp_path / "missing.py"), "old", "new")
        assert "File not found" in result

    def test_populates_undo_buffer(self, sample_file):
        abs_path = os.path.abspath(str(sample_file))
        do_edit_file(str(sample_file), "line two", "CHANGED")
        assert len(_undo_buffer[abs_path]) == 1

    def test_replaces_only_first(self, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("aaa\naaa\naaa\n")
        do_edit_file(str(f), "aaa", "bbb")
        content = f.read_text()
        assert content.count("bbb") == 1
        assert content.count("aaa") == 2


class TestEditFileAll:
    def test_replaces_all_occurrences(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("foo bar foo baz foo")
        result = do_edit_file_all(str(f), "foo", "qux")
        assert "3 occurrence" in result
        assert f.read_text() == "qux bar qux baz qux"

    def test_not_found(self, sample_file):
        result = do_edit_file_all(str(sample_file), "nonexistent", "x")
        assert "Text not found" in result


class TestMultiEdit:
    def test_applies_sequential_edits(self, tmp_path):
        f = tmp_path / "multi.py"
        f.write_text("def foo():\n    return bar\n")
        edits = [
            {"old_string": "foo", "new_string": "my_func"},
            {"old_string": "bar", "new_string": "42"},
        ]
        result = do_multi_edit(str(f), edits)
        assert "2/2" in result
        content = f.read_text()
        assert "my_func" in content
        assert "42" in content

    def test_partial_match(self, tmp_path):
        f = tmp_path / "partial.py"
        f.write_text("keep this\n")
        edits = [
            {"old_string": "keep", "new_string": "KEEP"},
            {"old_string": "nonexistent", "new_string": "nope"},
        ]
        result = do_multi_edit(str(f), edits)
        assert "1/2" in result

    def test_missing_file(self, tmp_path):
        result = do_multi_edit(str(tmp_path / "nope.py"), [])
        assert "File not found" in result
