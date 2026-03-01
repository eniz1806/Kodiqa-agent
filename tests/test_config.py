"""Tests for config.py functions and constants."""

import json
import os
import pytest

from config import (
    is_claude_model, is_qwen_api_model,
    CONFIRM_ACTIONS, BLOCKED_COMMANDS,
    MODEL_ALIASES, CLAUDE_ALIASES, QWEN_ALIASES,
    DEFAULTS, load_config,
)


class TestIsClaudeModel:
    def test_alias_key(self):
        assert is_claude_model("claude") is True
        assert is_claude_model("sonnet") is True
        assert is_claude_model("haiku") is True
        assert is_claude_model("opus") is True

    def test_full_model_name(self):
        assert is_claude_model("claude-sonnet-4-20250514") is True
        assert is_claude_model("claude-haiku-4-5-20251001") is True

    def test_negative(self):
        assert is_claude_model("qwen3:14b") is False
        assert is_claude_model("gpt-4") is False
        assert is_claude_model("qwen-api") is False


class TestIsQwenApiModel:
    def test_alias_key(self):
        assert is_qwen_api_model("qwen-api") is True
        assert is_qwen_api_model("qwen-max") is True
        assert is_qwen_api_model("qwen-coder") is True
        assert is_qwen_api_model("qwen-flash") is True

    def test_alias_value(self):
        assert is_qwen_api_model("qwen-plus") is True
        assert is_qwen_api_model("qwen-flash") is True

    def test_negative_local_qwen(self):
        assert is_qwen_api_model("qwen3:14b") is False
        assert is_qwen_api_model("qwen3-coder") is False

    def test_negative_other(self):
        assert is_qwen_api_model("claude-sonnet-4-20250514") is False
        assert is_qwen_api_model("fast") is False


class TestConfirmActions:
    def test_write_tools_need_confirmation(self):
        for tool in ["write_file", "edit_file", "run_command", "git_commit",
                      "delete_file", "move_file", "multi_edit", "diff_apply"]:
            assert tool in CONFIRM_ACTIONS, f"{tool} should require confirmation"

    def test_read_tools_auto_approved(self):
        for tool in ["read_file", "list_dir", "tree", "glob", "grep",
                      "git_status", "git_diff", "web_search", "memory_search"]:
            assert tool not in CONFIRM_ACTIONS, f"{tool} should be auto-approved"


class TestBlockedCommands:
    def test_dangerous_patterns(self):
        assert any("rm -rf /" in cmd for cmd in BLOCKED_COMMANDS)
        assert any("sudo rm" in cmd for cmd in BLOCKED_COMMANDS)
        assert any("mkfs" in cmd for cmd in BLOCKED_COMMANDS)


class TestLoadConfig:
    def test_defaults_without_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CONFIG_FILE", str(tmp_path / "nonexistent.json"))
        cfg = load_config()
        assert cfg["max_iterations"] == DEFAULTS["max_iterations"]
        assert cfg["max_file_size"] == DEFAULTS["max_file_size"]
        assert cfg["command_timeout"] == DEFAULTS["command_timeout"]

    def test_merges_user_overrides(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"max_iterations": 50, "command_timeout": 300}))
        monkeypatch.setattr("config.CONFIG_FILE", str(config_file))
        cfg = load_config()
        assert cfg["max_iterations"] == 50
        assert cfg["command_timeout"] == 300
        # Unset keys should still have defaults
        assert cfg["max_file_size"] == DEFAULTS["max_file_size"]

    def test_invalid_json_returns_defaults(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")
        monkeypatch.setattr("config.CONFIG_FILE", str(config_file))
        cfg = load_config()
        assert cfg["max_iterations"] == DEFAULTS["max_iterations"]
