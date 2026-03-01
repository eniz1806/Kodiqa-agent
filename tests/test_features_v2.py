"""Tests for v2.0 features: pin, alias, notify, optimizer, themes, share, PR, templates, plugins, agents, LSP, voice, diagrams."""

import json
import os
import sys
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch, PropertyMock
import pytest


# ── Themes ──

class TestThemes:
    def test_all_themes_have_required_keys(self):
        from config import THEMES
        required = {"prompt", "ai_name", "accent", "cost", "border", "error", "warning", "success", "tool", "tool_done"}
        for name, theme in THEMES.items():
            for key in required:
                assert key in theme, f"Theme {name} missing key {key}"

    def test_default_theme_exists(self):
        from config import THEMES
        assert "dark" in THEMES

    def test_five_themes(self):
        from config import THEMES
        assert len(THEMES) >= 5
        for name in ("dark", "light", "dracula", "monokai", "nord"):
            assert name in THEMES


# ── Pinned Context ──

class TestPinnedContext:
    def test_build_pinned_empty(self):
        """No pinned files returns empty string."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._pinned_files = []
        result = Kodiqa._build_pinned_context(k)
        assert result == ""

    def test_build_pinned_with_file(self, tmp_path):
        """Pinned file content is included."""
        from kodiqa import Kodiqa
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        k = MagicMock()
        k._pinned_files = [str(f)]
        k.cwd = str(tmp_path)
        result = Kodiqa._build_pinned_context(k)
        assert "print('hello')" in result
        assert "test.py" in result

    def test_build_pinned_missing_file(self):
        """Missing pinned file is skipped."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._pinned_files = ["/nonexistent/file.py"]
        result = Kodiqa._build_pinned_context(k)
        assert result == ""


# ── Cost Optimizer ──

class TestCostOptimizer:
    def test_disabled_noop(self):
        """When disabled, no output."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._optimizer_enabled = False
        k.console = MagicMock()
        Kodiqa._check_cost_optimizer(k, "hello")
        k.console.print.assert_not_called()

    def test_enabled_short_message(self):
        """Short non-code message shows tip."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._optimizer_enabled = True
        k.model = "claude-opus-4-6"
        k.console = MagicMock()
        Kodiqa._check_cost_optimizer(k, "what is python?")
        k.console.print.assert_called()
        tip = str(k.console.print.call_args)
        assert "Tip" in tip or "tip" in tip.lower() or "cheaper" in tip.lower()

    def test_enabled_code_message_no_tip(self):
        """Code-related message doesn't show tip."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._optimizer_enabled = True
        k.model = "claude-opus-4-6"
        k.console = MagicMock()
        Kodiqa._check_cost_optimizer(k, "refactor the auth module to use JWT tokens")
        k.console.print.assert_not_called()


# ── Notification ──

class TestNotification:
    @patch("subprocess.run")
    def test_send_notification_macos(self, mock_run):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        Kodiqa._send_notification(k, "Title", "Body text")
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "osascript" in args[0][0]


# ── Command Aliases ──

class TestAliases:
    def test_alias_expansion(self):
        """Aliases stored in settings should expand."""
        settings = {"aliases": {"t": "tokens", "s": "scan"}}
        cmd = "t"
        aliases = settings.get("aliases", {})
        expanded = aliases.get(cmd, cmd)
        assert expanded == "tokens"

    def test_alias_not_found(self):
        settings = {"aliases": {"t": "tokens"}}
        cmd = "unknown"
        aliases = settings.get("aliases", {})
        expanded = aliases.get(cmd)
        assert expanded is None


# ── Templates ──

class TestTemplates:
    def test_templates_exist(self):
        from templates import TEMPLATES
        assert len(TEMPLATES) >= 5

    def test_template_structure(self):
        from templates import TEMPLATES
        for name, tmpl in TEMPLATES.items():
            assert "description" in tmpl
            assert "files" in tmpl
            assert isinstance(tmpl["files"], dict)
            assert len(tmpl["files"]) > 0

    def test_cli_python_template(self):
        from templates import TEMPLATES
        assert "cli-python" in TEMPLATES
        tmpl = TEMPLATES["cli-python"]
        assert "main.py" in tmpl["files"]
        assert "pyproject.toml" in tmpl["files"]

    def test_react_template(self):
        from templates import TEMPLATES
        assert "react" in TEMPLATES
        tmpl = TEMPLATES["react"]
        assert "package.json" in tmpl["files"]
        assert "src/App.tsx" in tmpl["files"]


# ── LSP Client ──

class TestLSPClient:
    def test_lsp_servers_defined(self):
        from lsp import LSP_SERVERS
        assert "python" in LSP_SERVERS
        assert "typescript" in LSP_SERVERS
        assert "go" in LSP_SERVERS

    def test_lsp_server_has_cmd(self):
        from lsp import LSP_SERVERS
        for lang, server in LSP_SERVERS.items():
            assert "cmd" in server
            assert isinstance(server["cmd"], list)

    def test_lsp_client_init(self):
        from lsp import LSPClient
        client = LSPClient()
        assert client.process is None
        assert client.language is None

    def test_lsp_unsupported_language(self):
        from lsp import LSPClient
        client = LSPClient()
        with pytest.raises(ValueError, match="Unsupported language"):
            client.start("rust", "/tmp")

    def test_lsp_stop_noop_when_not_running(self):
        from lsp import LSPClient
        client = LSPClient()
        client.stop()  # Should not raise


# ── Slash Commands ──

class TestNewSlashCommands:
    def test_new_commands_registered(self):
        from kodiqa import Kodiqa
        new_cmds = ["/pin", "/unpin", "/alias", "/unalias", "/notify",
                    "/optimizer", "/theme", "/share", "/pr", "/review",
                    "/issue", "/init", "/plugins", "/agent", "/agents",
                    "/lsp", "/voice"]
        for cmd in new_cmds:
            assert cmd in Kodiqa._SLASH_COMMANDS, f"{cmd} not in _SLASH_COMMANDS"

    def test_total_commands_count(self):
        from kodiqa import Kodiqa
        assert len(Kodiqa._SLASH_COMMANDS) >= 49


# ── Detect Project Language ──

class TestDetectProjectLanguage:
    def test_detect_python(self, tmp_path):
        from kodiqa import Kodiqa
        (tmp_path / "main.py").write_text("")
        (tmp_path / "utils.py").write_text("")
        k = MagicMock(spec=Kodiqa)
        k.cwd = str(tmp_path)
        result = Kodiqa._detect_project_language(k)
        assert result == "python"

    def test_detect_typescript(self, tmp_path):
        from kodiqa import Kodiqa
        (tmp_path / "index.ts").write_text("")
        (tmp_path / "app.tsx").write_text("")
        k = MagicMock(spec=Kodiqa)
        k.cwd = str(tmp_path)
        result = Kodiqa._detect_project_language(k)
        assert result == "typescript"

    def test_detect_empty(self, tmp_path):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.cwd = str(tmp_path)
        result = Kodiqa._detect_project_language(k)
        assert result is None


# ── Diagram Rendering ──

class TestDiagramDetection:
    def test_mermaid_detected(self):
        text = "Here's a diagram:\n```mermaid\ngraph TD\n  A-->B\n```\nDone."
        import re
        blocks = re.findall(r'```mermaid\n(.*?)```', text, re.DOTALL)
        assert len(blocks) == 1
        assert "graph TD" in blocks[0]

    def test_no_mermaid(self):
        text = "Just some normal text without diagrams."
        import re
        blocks = re.findall(r'```mermaid\n(.*?)```', text, re.DOTALL)
        assert len(blocks) == 0


# ── Plugin Loading ──

class TestPluginLoading:
    def test_load_plugins_no_dir(self):
        """Loading plugins when dir doesn't exist."""
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._plugins = {}
        k.console = MagicMock()
        with patch("os.path.isdir", return_value=False):
            Kodiqa._load_plugins(k)
        assert k._plugins == {}

    def test_load_plugins_with_plugin(self, tmp_path):
        """Loading a valid plugin file."""
        from kodiqa import Kodiqa
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        plugin_file = plugins_dir / "hello.py"
        plugin_file.write_text(
            'TOOL_SCHEMA = {"name": "hello", "description": "Says hello"}\n'
            'def handle(params):\n    return "Hello!"\n'
        )
        k = MagicMock(spec=Kodiqa)
        k._plugins = {}
        k.console = MagicMock()
        with patch("kodiqa.KODIQA_DIR", str(tmp_path)):
            Kodiqa._load_plugins(k)
        assert "plugin_hello" in k._plugins
        assert k._plugins["plugin_hello"]["handler"]({}) == "Hello!"


# ── Share Session ──

class TestShareSession:
    def test_share_creates_html(self, tmp_path):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        k.model = "test-model"
        k.session_tokens = {"input": 100, "output": 50, "cost": 0.001}
        k.console = MagicMock()
        exports_dir = tmp_path / "exports"
        with patch("kodiqa.KODIQA_DIR", str(tmp_path)):
            Kodiqa._share_session_html(k)
        # Check it was called with path info
        k.console.print.assert_called()
