"""Tests for edit queue (batch mode) and undo buffer."""

import os
import pytest
from actions import (
    set_batch_mode, get_edit_queue, clear_edit_queue,
    apply_queued_edit, reject_queued_edit,
    do_write_file, do_edit_file, do_edit_file_all,
    do_undo_edit, _undo_buffer,
)


class TestBatchMode:
    def test_write_queues_in_batch_mode(self, tmp_path):
        path = str(tmp_path / "queued.txt")
        set_batch_mode(True)
        result = do_write_file(path, "hello")
        assert "[queued]" in result
        assert not os.path.isfile(path)  # file should NOT exist yet
        queue = get_edit_queue()
        assert len(queue) == 1
        assert queue[0]["path"] == path
        assert queue[0]["new_content"] == "hello"

    def test_edit_queues_in_batch_mode(self, sample_file):
        set_batch_mode(True)
        result = do_edit_file(str(sample_file), "line two", "CHANGED")
        assert "[queued]" in result
        # Original file should be unchanged
        assert "line two" in sample_file.read_text()

    def test_replace_all_queues_in_batch_mode(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("aaa bbb aaa")
        set_batch_mode(True)
        result = do_edit_file_all(str(f), "aaa", "xxx")
        assert "[queued]" in result
        assert f.read_text() == "aaa bbb aaa"  # unchanged

    def test_apply_queued_edit(self, tmp_path):
        path = str(tmp_path / "apply.txt")
        set_batch_mode(True)
        do_write_file(path, "applied content")
        set_batch_mode(False)
        result = apply_queued_edit(0)
        assert "Applied" in result
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "applied content"

    def test_reject_queued_edit(self, tmp_path):
        path = str(tmp_path / "reject.txt")
        set_batch_mode(True)
        do_write_file(path, "should not exist")
        set_batch_mode(False)
        result = reject_queued_edit(0)
        assert "Rejected" in result
        assert not os.path.isfile(path)

    def test_clear_edit_queue(self, tmp_path):
        set_batch_mode(True)
        do_write_file(str(tmp_path / "a.txt"), "a")
        do_write_file(str(tmp_path / "b.txt"), "b")
        assert len(get_edit_queue()) == 2
        clear_edit_queue()
        assert len(get_edit_queue()) == 0

    def test_invalid_index(self):
        assert "Invalid" in apply_queued_edit(-1)
        assert "Invalid" in apply_queued_edit(999)
        assert "Invalid" in reject_queued_edit(-1)


class TestUndoEdit:
    def test_undo_restores_content(self, sample_file):
        original = sample_file.read_text()
        do_edit_file(str(sample_file), "line two", "CHANGED")
        assert "CHANGED" in sample_file.read_text()
        result = do_undo_edit(str(sample_file))
        assert "Undone" in result
        assert sample_file.read_text() == original

    def test_undo_new_file_deletes(self, tmp_path):
        path = str(tmp_path / "new_file.txt")
        do_write_file(path, "content")
        assert os.path.isfile(path)
        result = do_undo_edit(path)
        assert "removed" in result.lower()
        assert not os.path.isfile(path)

    def test_undo_no_history(self, tmp_path):
        path = str(tmp_path / "no_history.txt")
        (tmp_path / "no_history.txt").write_text("original")
        result = do_undo_edit(path)
        assert "No undo history" in result
