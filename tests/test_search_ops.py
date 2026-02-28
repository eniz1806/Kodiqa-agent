"""Tests for search operation handlers (actions.py)."""

import os
import pytest
from actions import do_list_dir, do_tree, do_glob, do_grep


class TestListDir:
    def test_lists_files_and_dirs(self, sample_tree):
        result = do_list_dir(str(sample_tree))
        assert "src/" in result
        assert "README.md" in result
        assert "config.json" in result

    def test_shows_item_count(self, sample_tree):
        result = do_list_dir(str(sample_tree))
        assert "items" in result

    def test_not_a_directory(self, sample_file):
        result = do_list_dir(str(sample_file))
        assert "Not a directory" in result


class TestTree:
    def test_shows_structure(self, sample_tree):
        result = do_tree(str(sample_tree))
        assert "src/" in result
        assert "main.py" in result
        assert "utils.py" in result

    def test_respects_depth(self, sample_tree):
        result = do_tree(str(sample_tree), depth=1)
        assert "src/" in result
        # depth=1 should show src/ but not files inside src/
        assert "deep.py" not in result

    def test_skips_skip_dirs(self, sample_tree):
        result = do_tree(str(sample_tree))
        assert "node_modules" not in result

    def test_not_a_directory(self, sample_file):
        result = do_tree(str(sample_file))
        assert "Not a directory" in result

    def test_truncates_at_200(self, tmp_path):
        # Create many files to exceed 200 lines
        for i in range(250):
            (tmp_path / f"file_{i:03d}.txt").write_text(f"content {i}")
        result = do_tree(str(tmp_path))
        assert "truncated" in result


class TestGlob:
    def test_finds_matching_files(self, sample_tree):
        result = do_glob("**/*.py", str(sample_tree))
        assert "main.py" in result
        assert "utils.py" in result
        assert "deep.py" in result

    def test_no_matches(self, sample_tree):
        result = do_glob("**/*.rs", str(sample_tree))
        assert "No files matching" in result

    def test_not_a_directory(self, sample_file):
        result = do_glob("*.py", str(sample_file))
        assert "Not a directory" in result


class TestGrep:
    def test_finds_matches(self, sample_tree):
        result = do_grep("def main", str(sample_tree))
        assert "main.py" in result
        assert "def main" in result

    def test_line_numbers(self, sample_tree):
        result = do_grep("return 42", str(sample_tree))
        assert ":2:" in result  # line 2 of utils.py

    def test_no_matches(self, sample_tree):
        result = do_grep("nonexistent_string_xyz", str(sample_tree))
        assert "No matches" in result

    def test_invalid_regex(self, sample_tree):
        result = do_grep("[invalid", str(sample_tree))
        assert "Invalid regex" in result

    def test_skips_skip_dirs(self, sample_tree):
        result = do_grep("module", str(sample_tree))
        # node_modules/pkg.js has "module" but should be skipped
        assert "node_modules" not in result
