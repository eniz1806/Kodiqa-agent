"""Tests for action dispatch and execution."""

import os
import pytest
from unittest.mock import MagicMock
from actions import _dispatch, _describe_action, execute_action, execute_tool_call


class TestDispatch:
    def test_routes_read_file(self, sample_file):
        result = _dispatch("read_file", {"path": str(sample_file)}, None)
        assert "line one" in result

    def test_routes_list_dir(self, sample_tree):
        result = _dispatch("list_dir", {"path": str(sample_tree)}, None)
        assert "src" in result

    def test_unknown_tool(self):
        result = _dispatch("nonexistent_tool", {}, None)
        assert "Unknown tool" in result

    def test_routes_glob(self, sample_tree):
        result = _dispatch("glob", {"pattern": "**/*.py", "path": str(sample_tree)}, None)
        assert "main.py" in result

    def test_routes_grep(self, sample_tree):
        result = _dispatch("grep", {"pattern": "def main", "path": str(sample_tree)}, None)
        assert "main.py" in result


class TestDescribeAction:
    def test_write_file(self):
        desc = _describe_action("write_file", {"path": "/tmp/test.py"})
        assert "Write file" in desc
        assert "/tmp/test.py" in desc

    def test_edit_file(self):
        desc = _describe_action("edit_file", {"path": "/tmp/test.py"})
        assert "Edit file" in desc

    def test_run_command(self):
        desc = _describe_action("run_command", {"command": "ls -la"})
        assert "Run command" in desc
        assert "ls -la" in desc

    def test_git_commit(self):
        desc = _describe_action("git_commit", {"message": "fix bug"})
        assert "Git commit" in desc
        assert "fix bug" in desc

    def test_move_file(self):
        desc = _describe_action("move_file", {"source": "/a", "destination": "/b"})
        assert "Move" in desc
        assert "/a" in desc
        assert "/b" in desc

    def test_unknown_action(self):
        desc = _describe_action("some_action", {"key": "val"})
        assert "some_action" in desc


class TestExecuteAction:
    def test_calls_confirm_for_write(self, tmp_path):
        confirm = MagicMock(return_value=True)
        action = {
            "name": "write_file",
            "params": {"path": str(tmp_path / "out.txt"), "content": "hello"},
        }
        result = execute_action(action, None, confirm)
        confirm.assert_called_once()
        assert "Written" in result

    def test_denied_by_user(self, tmp_path):
        confirm = MagicMock(return_value=False)
        action = {
            "name": "write_file",
            "params": {"path": str(tmp_path / "out.txt"), "content": "hello"},
        }
        result = execute_action(action, None, confirm)
        assert "Denied" in result
        assert not os.path.isfile(str(tmp_path / "out.txt"))

    def test_no_confirm_for_read(self, sample_file):
        confirm = MagicMock(return_value=True)
        action = {
            "name": "read_file",
            "params": {"path": str(sample_file)},
        }
        result = execute_action(action, None, confirm)
        confirm.assert_not_called()  # read_file is not in CONFIRM_ACTIONS
        assert "line one" in result
