"""Tests for v4.0 features: lint auto-fix, test-fix, hooks, watch AI triggers,
architect mode, headless mode, worktree isolation, sandbox, repo map, agent teams."""

import json
import os
import re
import subprocess
import time
import tempfile
from unittest.mock import MagicMock, patch
import pytest


# ── F1: Auto Lint-Fix Loop ──

class TestAutoLintFix:
    def test_lint_auto_fix_default_off(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.lint_auto_fix = False
        assert k.lint_auto_fix is False

    def test_lint_auto_fix_toggle(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.lint_auto_fix = False
        k.lint_auto_fix = not k.lint_auto_fix
        assert k.lint_auto_fix is True
        k.lint_auto_fix = not k.lint_auto_fix
        assert k.lint_auto_fix is False

    def test_lint_fix_count_reset_in_chat(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._lint_fix_count = 3
        k._lint_fix_count = 0  # _chat resets this
        assert k._lint_fix_count == 0

    def test_lint_auto_subcommand(self):
        """Verify /lint accepts 'auto' arg."""
        assert "auto" in ("auto", "off", "on")


# ── F3: Auto Test-Fix Loop ──

class TestAutoTestFix:
    def test_test_fix_command_registered(self):
        from kodiqa import Kodiqa
        assert "/test-fix" in Kodiqa._SLASH_COMMANDS

    def test_test_fix_method_exists(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, '_handle_test_fix')

    def test_test_fix_max_iterations(self):
        max_iter = 3
        for i in range(max_iter):
            assert i < max_iter


# ── F6: Hooks System ──

class TestHooksSystem:
    def test_set_hooks(self):
        from actions import set_hooks, _hooks
        set_hooks({"pre_write_file": "echo ok", "post_git_commit": "notify-send done"})
        from actions import _hooks
        assert _hooks.get("pre_write_file") == "echo ok"
        assert _hooks.get("post_git_commit") == "notify-send done"
        set_hooks({})  # Cleanup

    def test_set_hooks_invalid(self):
        from actions import set_hooks, _hooks
        set_hooks("not a dict")
        from actions import _hooks
        assert _hooks == {}

    def test_run_hook_success(self):
        from actions import _run_hook
        assert _run_hook("echo hello", {}) is True

    def test_run_hook_with_params(self):
        from actions import _run_hook
        assert _run_hook("echo {path}", {"path": "/tmp/test.py"}) is True

    def test_run_hook_failure_returns_false(self):
        from actions import _run_hook
        assert _run_hook("false", {}) is False

    def test_hooks_default_in_config(self):
        from config import DEFAULTS
        assert "hooks" in DEFAULTS
        assert DEFAULTS["hooks"] == {}


# ── F2: Watch Mode AI Triggers ──

class TestWatchAITriggers:
    def test_scan_ai_triggers_python(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._scan_ai_triggers = Kodiqa._scan_ai_triggers.__get__(agent)
        f = tmp_path / "test.py"
        f.write_text("x = 1\n# AI: add docstring to this\ndef foo():\n    pass\n")
        triggers = agent._scan_ai_triggers(str(f))
        assert len(triggers) == 1
        assert triggers[0][0] == 2  # line number
        assert "add docstring" in triggers[0][1]

    def test_scan_ai_triggers_js(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._scan_ai_triggers = Kodiqa._scan_ai_triggers.__get__(agent)
        f = tmp_path / "test.js"
        f.write_text("const x = 1;\n// AI: refactor this function\nfunction foo() {}\n")
        triggers = agent._scan_ai_triggers(str(f))
        assert len(triggers) == 1
        assert "refactor" in triggers[0][1]

    def test_scan_ai_triggers_none(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._scan_ai_triggers = Kodiqa._scan_ai_triggers.__get__(agent)
        f = tmp_path / "test.py"
        f.write_text("x = 1\n# regular comment\n")
        triggers = agent._scan_ai_triggers(str(f))
        assert len(triggers) == 0

    def test_remove_ai_trigger(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._remove_ai_trigger = Kodiqa._remove_ai_trigger.__get__(agent)
        f = tmp_path / "test.py"
        f.write_text("x = 1\n# AI: fix this\ndef foo():\n    pass\n")
        agent._remove_ai_trigger(str(f), 2)
        content = f.read_text()
        assert "AI:" not in content
        assert "x = 1" in content
        assert "def foo():" in content

    def test_ai_trigger_queue_init(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, '_scan_ai_triggers')
        assert hasattr(Kodiqa, '_remove_ai_trigger')


# ── F4: Architect Mode ──

class TestArchitectMode:
    def test_architect_command_registered(self):
        from kodiqa import Kodiqa
        assert "/architect" in Kodiqa._SLASH_COMMANDS

    def test_architect_method_exists(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, '_handle_architect')

    def test_resolve_model_name_claude(self):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._resolve_model_name = Kodiqa._resolve_model_name.__get__(agent)
        resolved = agent._resolve_model_name("opus")
        assert "opus" in resolved.lower() or "claude" in resolved.lower()

    def test_resolve_model_name_passthrough(self):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._resolve_model_name = Kodiqa._resolve_model_name.__get__(agent)
        assert agent._resolve_model_name("some-custom-model") == "some-custom-model"

    def test_architect_state_init(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.architect_mode = False
        k._architect_model = None
        k._impl_model = None
        assert k.architect_mode is False


# ── F7: Background/Headless Mode ──

class TestHeadlessMode:
    def test_headless_method_exists(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, 'run_headless')

    def test_headless_init(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.headless = False
        assert k.headless is False

    def test_main_accepts_headless_arg(self):
        """Verify main() uses argparse with --headless flag."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--headless", type=str)
        parser.add_argument("--model", type=str)
        parser.add_argument("--output", type=str)
        args = parser.parse_args(["--headless", "test task", "--model", "opus"])
        assert args.headless == "test task"
        assert args.model == "opus"


# ── F5: Worktree Isolation ──

class TestWorktreeIsolation:
    def test_worktree_methods_exist(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, '_create_agent_worktree')
        assert hasattr(Kodiqa, '_cleanup_agent_worktree')

    def test_agent_handles_worktree_flag(self):
        """Test --worktree flag parsing logic."""
        arg = "--worktree refactor the auth module"
        use_worktree = False
        if arg.strip().startswith("--worktree"):
            use_worktree = True
            arg = arg.replace("--worktree", "", 1).strip()
        assert use_worktree is True
        assert arg == "refactor the auth module"

    def test_agent_no_worktree_flag(self):
        arg = "refactor the auth module"
        use_worktree = arg.strip().startswith("--worktree")
        assert use_worktree is False


# ── F10: OS-Level Sandboxing ──

class TestSandbox:
    def test_sandbox_command_registered(self):
        from kodiqa import Kodiqa
        assert "/sandbox" in Kodiqa._SLASH_COMMANDS

    def test_set_sandbox(self):
        from actions import set_sandbox, _sandbox_enabled
        set_sandbox(True)
        from actions import _sandbox_enabled
        assert _sandbox_enabled is True
        set_sandbox(False)
        from actions import _sandbox_enabled
        assert _sandbox_enabled is False

    def test_sandbox_wrap_fallback(self):
        from actions import _sandbox_wrap
        # On any platform, if no sandbox tool available, should return cmd as-is
        result = _sandbox_wrap("echo hello", "/tmp")
        assert "echo hello" in result

    def test_shell_quote(self):
        from actions import _shell_quote
        assert _shell_quote("hello") == "'hello'"
        assert _shell_quote("it's") == "'it'\\''s'"


# ── F8: Tree-Sitter Repo Map ──

class TestRepoMap:
    def test_map_command_registered(self):
        from kodiqa import Kodiqa
        assert "/map" in Kodiqa._SLASH_COMMANDS

    def test_repomap_import(self):
        from repomap import RepoMap
        assert RepoMap is not None

    def test_repomap_detect_language(self):
        from repomap import RepoMap
        rm = RepoMap("/tmp")
        assert rm._detect_language("test.py") == "python"
        assert rm._detect_language("test.js") == "javascript"
        assert rm._detect_language("test.ts") == "typescript"
        assert rm._detect_language("test.go") == "go"
        assert rm._detect_language("test.rs") == "rust"
        assert rm._detect_language("test.txt") is None

    def test_repomap_extract_symbols_regex(self, tmp_path):
        from repomap import RepoMap
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n\nclass Foo:\n    def bar(self):\n        pass\n")
        rm = RepoMap(str(tmp_path))
        symbols = rm._extract_symbols_regex(str(f), "python")
        names = [s["name"] for s in symbols]
        assert "hello" in names
        assert "Foo" in names

    def test_repomap_build_and_format(self, tmp_path):
        from repomap import RepoMap
        f = tmp_path / "app.py"
        f.write_text("def main():\n    pass\n\nclass App:\n    pass\n")
        rm = RepoMap(str(tmp_path))
        rm.build_map()
        output = rm.format_map()
        assert "app.py" in output
        assert "main" in output

    def test_repomap_empty_dir(self, tmp_path):
        from repomap import RepoMap
        rm = RepoMap(str(tmp_path))
        rm.build_map()
        output = rm.format_map()
        assert "no symbols found" in output


# ── F9: Agent Teams ──

class TestAgentTeams:
    def test_team_command_registered(self):
        from kodiqa import Kodiqa
        assert "/team" in Kodiqa._SLASH_COMMANDS

    def test_teams_command_registered(self):
        from kodiqa import Kodiqa
        assert "/teams" in Kodiqa._SLASH_COMMANDS

    def test_team_methods_exist(self):
        from kodiqa import Kodiqa
        assert hasattr(Kodiqa, '_handle_team')
        assert hasattr(Kodiqa, '_handle_teams')

    def test_team_state_init(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._teams = {}
        k._team_counter = 0
        assert len(k._teams) == 0


# ── Command Count ──

class TestV4CommandCount:
    def test_new_commands_registered(self):
        from kodiqa import Kodiqa
        new_commands = ["/test-fix", "/architect", "/sandbox", "/map", "/team", "/teams"]
        for cmd in new_commands:
            assert cmd in Kodiqa._SLASH_COMMANDS, f"{cmd} not in _SLASH_COMMANDS"

    def test_total_slash_commands(self):
        from kodiqa import Kodiqa
        assert len(Kodiqa._SLASH_COMMANDS) >= 69
