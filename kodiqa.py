#!/usr/bin/env python3
"""Kodiqa - Local AI coding agent. Claude native tools + Ollama text-based actions."""

import json
import os
try:
    import readline
except ImportError:
    import types
    readline = types.SimpleNamespace(
        read_history_file=lambda *a: None, write_history_file=lambda *a: None,
        set_history_length=lambda *a: None, set_completer=lambda *a: None,
        set_completer_delims=lambda *a: None, parse_and_bind=lambda *a: None,
        get_line_buffer=lambda: "",
    )
import sys
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.status import Status

import logging
import subprocess
import time

from config import (
    OLLAMA_URL, OLLAMA_BIN, DEFAULT_MODEL, MODEL_ALIASES, CLAUDE_ALIASES,
    CLAUDE_API_URL, QWEN_ALIASES, QWEN_API_URL, CONTEXT_FILE, KODIQA_DIR,
    CONFIG_FILE, MAX_ITERATIONS, SYSTEM_PROMPT, SKIP_DIRS, SKIP_EXTENSIONS,
    MAX_FILE_SIZE, DEFAULTS,
    load_settings, save_settings, load_config, save_default_config,
    is_claude_model, is_qwen_api_model,
)
from memory import MemoryStore
from actions import (
    parse_actions, execute_action, execute_tool_call, execute_tools_parallel, set_console,
    set_batch_mode, get_edit_queue, clear_edit_queue, apply_queued_edit, reject_queued_edit,
)
from web import set_search_engine, get_search_engine, set_google_api_keys, get_google_api_keys
from tools import CLAUDE_TOOLS
from mcp import MCPManager

# ── Error logging and API retry ──

ERROR_LOG = os.path.join(KODIQA_DIR, "error.log")
_logger = None


def _setup_error_log():
    """Setup error logging to ~/.kodiqa/error.log with size cap."""
    global _logger
    os.makedirs(KODIQA_DIR, exist_ok=True)
    # Cap log at 1MB — keep last 500KB
    if os.path.isfile(ERROR_LOG) and os.path.getsize(ERROR_LOG) > 1_000_000:
        try:
            with open(ERROR_LOG, "rb") as f:
                f.seek(-500_000, 2)
                tail = f.read()
            with open(ERROR_LOG, "wb") as f:
                f.write(tail)
        except Exception:
            pass
    logger = logging.getLogger("kodiqa")
    if not logger.handlers:
        handler = logging.FileHandler(ERROR_LOG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    _logger = logger
    return logger


def _retry_api_call(fn, max_retries=3, backoff_base=2.0, provider_name="API"):
    """Retry an API call with exponential backoff on 429, 5xx, and connection errors."""
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = fn()
            if resp.status_code == 429:
                wait = backoff_base ** attempt
                if _logger:
                    _logger.warning(f"{provider_name} rate limited, retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = backoff_base ** attempt
                if _logger:
                    _logger.warning(f"{provider_name} server error {resp.status_code}, retry in {wait}s")
                time.sleep(wait)
                continue
            return resp
        except requests.ConnectionError as e:
            last_error = e
            wait = backoff_base ** attempt
            if _logger:
                _logger.warning(f"{provider_name} connection error, retry {attempt+1}/{max_retries} in {wait}s")
            time.sleep(wait)
        except requests.Timeout as e:
            last_error = e
            if _logger:
                _logger.warning(f"{provider_name} timeout, retry {attempt+1}/{max_retries}")
            time.sleep(1)
    raise last_error or Exception(f"{provider_name} failed after {max_retries} retries")


# ── Cost table (per 1M tokens: input, output) ──

COST_TABLE = {
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "qwen-plus": (0.40, 1.20),
    "qwen-max": (1.20, 6.0),
    "qwen3-coder-plus": (0.574, 2.294),
    "qwen-flash": (0.05, 0.40),
}


# Claude system prompt (more detailed since Claude can handle it)
CLAUDE_SYSTEM = """You are Kodiqa, an expert AI coding assistant running locally on the user's machine. You have direct access to their filesystem, terminal, and the web through your tools.

## How to work effectively
1. **Read before editing** - Always read a file before modifying it. Understand existing code first.
2. **Search before assuming** - Use glob and grep to find files and code patterns. Don't guess paths.
3. **Investigate thoroughly** - When analyzing a project, read the actual source files (package.json, pubspec.yaml, build.gradle, main entry points), don't just look at directory names.
4. **Chain tools** - Use multiple tools in sequence to complete complex tasks. Read → understand → search → plan → edit.
5. **Be precise with edits** - The old_string in edit_file must match exactly. Include enough context to be unique.
6. **Explain your work** - Tell the user what you're doing and why, but be concise.
7. **Use memory** - Store important project details and user preferences so you remember them next time.
8. **Write to files when asked** - When the user asks you to save analysis/notes to a file, use write_file, not memory_store.

## Context
- Current directory: {cwd}
- Current model: {model}
{memories}"""


class StreamWriter:
    """Filters streaming output: shows text, hides code blocks in compact mode."""

    def __init__(self, console, compact=True):
        self.console = console
        self.compact = compact
        self._buffer = []
        self._in_fence = False
        self._fence_lang = ""
        self._fence_lines = 0
        self._fence_chars = 0
        self._pending = ""  # partial line buffer for fence detection
        self._header_printed = False
        self._status = None  # live status for code block progress
        self._action_depth = 0  # track [ACTION:...] blocks
        self._in_think = False  # track <think>...</think> blocks
        self._think_lines = 0

    def write(self, token):
        """Process a token. Returns the token (always appended to full_text externally)."""
        if not self.compact:
            sys.stdout.write(token)
            sys.stdout.flush()
            return

        self._pending += token

        # Process complete lines (and partial last line)
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._process_line(line + "\n")

        # If pending has partial content without newline, check for fence markers
        # but don't output yet (wait for full line)
        # Exception: if not in fence and no backticks pending, flush text
        if self._pending and not self._in_fence and not self._in_action() and not self._in_think:
            if "```" not in self._pending and "[ACTION" not in self._pending and "<think" not in self._pending:
                sys.stdout.write(self._pending)
                sys.stdout.flush()
                self._pending = ""

    def _in_action(self):
        return self._action_depth > 0

    def _process_line(self, line):
        stripped = line.strip()

        # Track <think>...</think> blocks (Qwen/local reasoning models)
        if "<think>" in stripped and not self._in_think:
            self._in_think = True
            self._think_lines = 0
            self._start_progress("Thinking")
            return
        if "</think>" in stripped and self._in_think:
            self._in_think = False
            self._stop_progress()
            self.console.print(f"  [dim cyan]╰─ reasoning: {self._think_lines} lines[/]")
            return
        if self._in_think:
            self._think_lines += 1
            if self._status:
                self._status.update(f"  [dim]Thinking... ({self._think_lines} lines)[/]")
            return

        # Track [ACTION:...] blocks (Ollama text mode)
        if stripped.startswith("[ACTION:"):
            self._action_depth += 1
            if self._action_depth == 1:
                # Extract action name
                action_name = stripped.split("ACTION:", 1)[1].rstrip("]").strip()
                self._start_progress(f"Running action: {action_name}")
            return
        if stripped == "[/ACTION]":
            self._action_depth -= 1
            if self._action_depth <= 0:
                self._action_depth = 0
                self._stop_progress()
            return
        if self._in_action():
            # Inside an action block — suppress all output, just count
            self._fence_lines += 1
            if self._status:
                self._status.update(f"  [dim]Action in progress... ({self._fence_lines} lines)[/]")
            return

        # Check for code fence toggle
        if stripped.startswith("```"):
            if not self._in_fence:
                # Opening fence
                self._in_fence = True
                self._fence_lang = stripped[3:].strip().split()[0] if len(stripped) > 3 else ""
                self._fence_lines = 0
                self._fence_chars = 0
                lang_label = f" ({self._fence_lang})" if self._fence_lang else ""
                self._start_progress(f"Writing code{lang_label}")
            else:
                # Closing fence
                self._in_fence = False
                lang_label = f" {self._fence_lang}" if self._fence_lang else ""
                self._stop_progress()
                self.console.print(
                    f"  [dim cyan]╰─ code block:{lang_label} {self._fence_lines} lines, "
                    f"{self._fence_chars:,} chars[/]"
                )
                self._fence_lang = ""
            return

        if self._in_fence:
            # Inside code fence — suppress output, update counter
            self._fence_lines += 1
            self._fence_chars += len(line)
            if self._status:
                lang_label = f" ({self._fence_lang})" if self._fence_lang else ""
                self._status.update(
                    f"  [dim]Writing code{lang_label}... "
                    f"{self._fence_lines} lines, {self._fence_chars:,} chars[/]"
                )
        else:
            # Regular text — show it
            sys.stdout.write(line)
            sys.stdout.flush()

    def _start_progress(self, label):
        self._fence_lines = 0
        self._fence_chars = 0
        if self._status:
            self._status.stop()
        self._status = Status(f"  [dim]{label}...[/]", console=self.console, spinner="dots")
        self._status.start()

    def _stop_progress(self):
        if self._status:
            self._status.stop()
            self._status = None

    def flush_pending(self):
        """Flush any remaining buffered content."""
        if self._pending:
            if not self._in_fence and not self._in_action():
                sys.stdout.write(self._pending)
                sys.stdout.flush()
            elif self._in_fence:
                self._fence_lines += 1
                self._fence_chars += len(self._pending)
            self._pending = ""
        self._stop_progress()
        if self._in_fence:
            # Unclosed fence
            lang_label = f" {self._fence_lang}" if self._fence_lang else ""
            self.console.print(
                f"  [dim cyan]╰─ code block:{lang_label} {self._fence_lines} lines, "
                f"{self._fence_chars:,} chars[/]"
            )
            self._in_fence = False


class Kodiqa:
    def __init__(self):
        self.console = Console()
        set_console(self.console)  # share console with actions.py for diff display
        self.memory = MemoryStore()
        self.history = []
        self.cwd = os.getcwd()
        self.settings = load_settings()
        self.config = load_config()
        save_default_config()
        _setup_error_log()
        self.claude_key = self.settings.get("claude_api_key", "")
        self.session_file = os.path.join(KODIQA_DIR, "session.json")
        self.multi_models = self._discover_models()  # default: multi-model mode
        self._auto_approved = set()  # action types auto-approved this session
        self.session_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost": 0.0}
        # Setup readline for arrow keys + input history + tab completion
        self._history_file = os.path.join(KODIQA_DIR, "input_history")
        try:
            readline.read_history_file(self._history_file)
        except (FileNotFoundError, OSError):
            pass
        try:
            readline.set_history_length(500)
            readline.set_completer(self._completer)
            readline.set_completer_delims(" \t")
            readline.parse_and_bind("tab: complete")
        except Exception:
            pass
        self.qwen_key = self.settings.get("qwen_api_key", "")
        # Load Google API keys if saved
        g_key = self.settings.get("google_api_key", "")
        g_cx = self.settings.get("google_cx", "")
        if g_key and g_cx:
            set_google_api_keys(g_key, g_cx)
            set_search_engine("google_api")
        if self.claude_key:
            self.model = self.settings.get("default_model", "claude-sonnet-4-20250514")
        else:
            self.model = self.settings.get("default_model", DEFAULT_MODEL)
        # Shell environment detection
        self.shell_env = self._detect_shell_env()
        # Conversation checkpoints
        self._checkpoints = {}
        self._checkpoint_dir = os.path.join(KODIQA_DIR, "checkpoints")
        os.makedirs(self._checkpoint_dir, exist_ok=True)
        # Compact mode: hide code blocks during streaming (default on)
        self.compact_mode = True
        # Permission mode: "default" | "relaxed" | "auto"
        #   default = confirm all writes/edits/commands (current behavior)
        #   relaxed = auto-approve file edits/writes, confirm commands only
        #   auto    = no confirmations at all
        self.permission_mode = "default"
        # Plan mode: AI explores + plans before implementing
        self.plan_mode = False
        self._pending_plan = None
        self._plan_request = None
        # Edit queue: batch review mode for file edits
        self.batch_edits = True  # when True, queue edits for batch review
        # Project index cache
        self._project_index = {}  # {path: {"tree": ..., "symbols": ..., "timestamp": ...}}
        # Conversation branches
        self._branches = {}  # {name: {"history": [...], "model": ...}}
        # MCP server manager
        self.mcp = MCPManager()

    # ── Tab Completion ──

    _SLASH_COMMANDS = [
        "/model", "/models", "/multi", "/single", "/scan", "/clear", "/compact",
        "/memories", "/forget", "/context", "/key", "/tokens", "/config",
        "/export", "/checkpoint", "/restore", "/env", "/verbose", "/mode",
        "/plan", "/accept", "/search", "/cd", "/branch", "/mcp", "/help", "/quit",
    ]

    def _completer(self, text, state):
        """Readline tab completer for slash commands and file paths."""
        try:
            buf = readline.get_line_buffer().lstrip()
            if buf.startswith("/"):
                # Slash command completion
                if " " not in buf:
                    # Completing the command itself
                    matches = [c + " " for c in self._SLASH_COMMANDS if c.startswith(text)]
                else:
                    # Completing argument — context-aware
                    cmd = buf.split()[0]
                    if cmd in ("/model",):
                        all_aliases = list(MODEL_ALIASES.keys()) + list(CLAUDE_ALIASES.keys()) + list(QWEN_ALIASES.keys())
                        matches = [a for a in all_aliases if a.startswith(text)]
                    elif cmd in ("/mode",):
                        matches = [m for m in ("default", "relaxed", "auto") if m.startswith(text)]
                    elif cmd in ("/search",):
                        matches = [e for e in ("duckduckgo", "google", "api") if e.startswith(text)]
                    elif cmd in ("/cd", "/scan"):
                        matches = self._complete_path(text)
                    elif cmd in ("/restore",):
                        matches = [n for n in self._checkpoints.keys() if n.startswith(text)]
                    else:
                        matches = self._complete_path(text)
            else:
                # Regular text — complete file paths if it looks like a path
                if text.startswith(("/", "~", ".")) or "/" in text:
                    matches = self._complete_path(text)
                else:
                    return None
            return matches[state] if state < len(matches) else None
        except Exception:
            return None

    def _complete_path(self, text):
        """Complete file/directory paths."""
        expanded = os.path.expanduser(text)
        if os.path.isdir(expanded) and not expanded.endswith("/"):
            expanded += "/"
        dirname = os.path.dirname(expanded) or "."
        basename = os.path.basename(expanded)
        try:
            entries = os.listdir(dirname)
        except OSError:
            return []
        matches = []
        for entry in sorted(entries):
            if entry.startswith(".") and not basename.startswith("."):
                continue
            if entry.startswith(basename):
                full = os.path.join(dirname, entry)
                # Replace expanded prefix back with original (keep ~ if used)
                if text.startswith("~"):
                    result = "~" + full[len(os.path.expanduser("~")):]
                else:
                    result = full
                if os.path.isdir(full):
                    result += "/"
                matches.append(result)
        return matches

    def _discover_models(self):
        """Auto-discover all installed Ollama models for multi-mode default."""
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return models if models else []
        except Exception:
            return []

    def _shell_env_context(self):
        """Format shell environment for system prompt."""
        if not self.shell_env:
            return ""
        parts = [f"- {k}: {v}" for k, v in self.shell_env.items() if k not in ("cwd",)]
        if parts:
            return "## Shell Environment\n" + "\n".join(parts)
        return ""

    def _detect_shell_env(self):
        """Detect shell environment, OS, and dev tools."""
        env = {
            "os": os.uname().sysname,
            "arch": os.uname().machine,
            "shell": os.environ.get("SHELL", "unknown"),
            "python": sys.version.split()[0],
            "cwd": self.cwd,
        }
        # Detect common dev tools
        for tool in ["git", "node", "npm", "cargo", "go", "java", "docker"]:
            try:
                result = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    version = result.stdout.strip().split("\n")[0][:50]
                    env[tool] = version
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return env

    def run(self):
        self._first_run_setup()
        self._detect_git()
        self._load_session()
        self._welcome()
        self._check_updates()
        try:
            while True:
                try:
                    self.console.print()
                    user_input = input("\033[1;36mYou: \033[0m")
                except (EOFError, KeyboardInterrupt):
                    self._quit()
                    return
                if not user_input.strip():
                    continue
                if user_input.strip().startswith("/"):
                    self._handle_slash(user_input.strip())
                else:
                    self._chat(user_input)
        except KeyboardInterrupt:
            self._quit()

    def _first_run_setup(self):
        if "setup_done" in self.settings:
            return
        self.console.print(Panel(
            "[bold]Welcome to Kodiqa![/]\n\n"
            "Kodiqa works with [cyan]local Ollama models[/] (free, unlimited).\n"
            "You can also connect [bold yellow]Claude API[/] for much smarter responses.\n\n"
            "[dim]Claude API costs money per message but is far more capable.\n"
            "Get your key at: https://console.anthropic.com/settings/keys[/]",
            title="First Run Setup", border_style="green",
        ))
        try:
            choice = Prompt.ask("Add Claude API key?", choices=["y", "n"], default="n")
            if choice.lower() == "y":
                key = Prompt.ask("[bold yellow]Paste your Claude API key[/]")
                key = key.strip()
                if key.startswith("sk-ant-"):
                    self.claude_key = key
                    self.settings["claude_api_key"] = key
                    self.settings["default_model"] = "claude-sonnet-4-20250514"
                    self.model = "claude-sonnet-4-20250514"
                    self.console.print("[green]Claude API key saved! Using Claude Sonnet as default.[/]")
                else:
                    self.console.print("[yellow]Key doesn't look right (should start with sk-ant-). Skipping.[/]")
            else:
                self.console.print(f"[dim]Using local models. Default: {self.model}[/]")
        except (EOFError, KeyboardInterrupt):
            self.console.print(f"\n[dim]Skipped. Using local models.[/]")
        self.settings["setup_done"] = True
        save_settings(self.settings)
        self.console.print()

    def _detect_git(self):
        """Detect git repo info for current directory."""
        import subprocess
        try:
            # Check if in a git repo
            subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, check=True, cwd=self.cwd)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.git_info = None
            return
        info = {}
        try:
            r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=self.cwd)
            info["branch"] = r.stdout.strip() or "detached"
        except Exception:
            info["branch"] = "unknown"
        try:
            r = subprocess.run(["git", "log", "--oneline", "-5"], capture_output=True, text=True, cwd=self.cwd)
            info["recent_commits"] = r.stdout.strip()
        except Exception:
            info["recent_commits"] = ""
        try:
            r = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, cwd=self.cwd)
            changes = r.stdout.strip()
            info["changed_files"] = len(changes.splitlines()) if changes else 0
            info["status_short"] = changes
        except Exception:
            info["changed_files"] = 0
            info["status_short"] = ""
        # Capture short diff stat for context
        try:
            r = subprocess.run(["git", "diff", "--stat", "--no-color"], capture_output=True, text=True, cwd=self.cwd, timeout=5)
            info["diff_stat"] = r.stdout.strip()[:500] if r.stdout.strip() else ""
        except Exception:
            info["diff_stat"] = ""
        # Capture staged diff stat
        try:
            r = subprocess.run(["git", "diff", "--staged", "--stat", "--no-color"], capture_output=True, text=True, cwd=self.cwd, timeout=5)
            info["staged_stat"] = r.stdout.strip()[:500] if r.stdout.strip() else ""
        except Exception:
            info["staged_stat"] = ""
        self.git_info = info

    def _git_context(self):
        """Format git info for system prompt."""
        if not self.git_info:
            return ""
        g = self.git_info
        lines = ["## Git Repository"]
        lines.append(f"- Branch: {g['branch']}")
        if g["changed_files"]:
            lines.append(f"- Uncommitted changes: {g['changed_files']} files")
            if g.get("status_short"):
                lines.append(f"```\n{g['status_short']}\n```")
        if g.get("diff_stat"):
            lines.append(f"- Unstaged diff:\n```\n{g['diff_stat']}\n```")
        if g.get("staged_stat"):
            lines.append(f"- Staged diff:\n```\n{g['staged_stat']}\n```")
        if g["recent_commits"]:
            lines.append(f"- Recent commits:\n```\n{g['recent_commits']}\n```")
        return "\n".join(lines)

    # ── Session save/load for conversation recovery ──

    def _save_session(self):
        """Auto-save conversation to disk for recovery."""
        try:
            # Only save string-content messages (skip complex tool_use blocks for simplicity)
            saveable = []
            for msg in self.history:
                if isinstance(msg.get("content"), str):
                    saveable.append(msg)
            data = {"model": self.model, "cwd": self.cwd, "history": saveable}
            with open(self.session_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_session(self):
        """Offer to resume previous session if it exists."""
        if not os.path.isfile(self.session_file):
            return
        try:
            with open(self.session_file, "r") as f:
                data = json.load(f)
            history = data.get("history", [])
            if len(history) < 2:
                os.remove(self.session_file)
                return
            msg_count = len([m for m in history if m.get("role") == "user"])
            self.console.print(f"[dim]Previous session found ({msg_count} messages). Resume? (y/n)[/]")
            try:
                answer = Prompt.ask("Resume", choices=["y", "n"], default="y")
                if answer.lower() == "y":
                    self.history = history
                    self.model = data.get("model", self.model)
                    saved_cwd = data.get("cwd", self.cwd)
                    if os.path.isdir(saved_cwd):
                        self.cwd = saved_cwd
                        os.chdir(self.cwd)
                    self.console.print("[green]Session restored.[/]")
                else:
                    os.remove(self.session_file)
            except (EOFError, KeyboardInterrupt):
                os.remove(self.session_file)
        except Exception:
            pass

    def _clear_session(self):
        """Remove saved session file."""
        try:
            if os.path.isfile(self.session_file):
                os.remove(self.session_file)
        except Exception:
            pass

    def _get_project_context_path(self):
        safe_name = self.cwd.strip("/").replace("/", "-")
        return os.path.join(KODIQA_DIR, "projects", f"{safe_name}.md")

    def _load_context_file(self):
        parts = []
        if os.path.isfile(CONTEXT_FILE):
            try:
                with open(CONTEXT_FILE, "r") as f:
                    content = f.read().strip()
                if content:
                    parts.append(f"## Global Context (from ~/.kodiqa/KODIQA.md)\n{content}")
            except Exception:
                pass
        project_ctx = self._get_project_context_path()
        if os.path.isfile(project_ctx):
            try:
                with open(project_ctx, "r") as f:
                    content = f.read().strip()
                if content:
                    parts.append(f"## Project Context ({self.cwd})\n{content}")
            except Exception:
                pass
        return "\n\n".join(parts)

    def _welcome(self):
        if is_claude_model(self.model):
            provider = "[yellow]Claude API[/]"
        elif is_qwen_api_model(self.model):
            provider = "[blue]Qwen API[/]"
        else:
            provider = "[green]Local/Ollama[/]"
        git_line = ""
        if self.git_info:
            g = self.git_info
            git_line = f"\nGit: [cyan]{g['branch']}[/]"
            if g["changed_files"]:
                git_line += f" ({g['changed_files']} changed files)"
        mode_line = ""
        if self.multi_models:
            mode_line = f"\nMode: [magenta]Multi-model[/] ({len(self.multi_models)} models + consensus)"
        self.console.print(Panel(
            f"[bold green]Kodiqa[/] - AI Coding Agent\n"
            f"Model: [cyan]{self.model}[/] ({provider}){git_line}{mode_line}\n"
            f"Type [bold]/help[/] for commands, [bold]/single[/] for single model",
            border_style="green",
        ))

    def _quit(self):
        self._save_session()
        self.memory.close()
        self.mcp.stop_all()
        try:
            readline.write_history_file(self._history_file)
        except Exception:
            pass
        # Always stop Ollama on quit — no need to leave it running
        import subprocess
        try:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=5)
            self.console.print("\n[green]●[/] Ollama stopped")
        except Exception:
            pass
        self.console.print("[dim]Goodbye! Session saved.[/]")

    def _ensure_ollama(self):
        """Make sure Ollama is running, start it if not."""
        import subprocess
        import time
        try:
            requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            return True  # Already running
        except Exception:
            pass
        # Try to start Ollama
        self.console.print("[dim]Starting Ollama...[/]")
        try:
            subprocess.Popen(
                [OLLAMA_BIN, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for it to be ready (up to 10s)
            for _ in range(20):
                time.sleep(0.5)
                try:
                    requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
                    self.console.print("[green]●[/] Ollama started")
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        self.console.print("[yellow]●[/] Could not start Ollama [dim](start manually: ollama serve)[/]")
        return False

    def _check_updates(self):
        """Check for model updates and new models on startup."""
        import subprocess

        if not self._ensure_ollama():
            return

        try:
            # Get installed models
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            installed = {m["name"]: m for m in resp.json().get("models", [])}
        except Exception:
            return

        if not installed:
            return

        # 1. Check installed models for updates
        self.console.print(f"\n[dim]Checking {len(installed)} installed models for updates...[/]")
        updated_count = 0
        for model_name in list(installed.keys()):
            try:
                with Status(f"  [dim]Checking {model_name}...[/]", console=self.console, spinner="dots"):
                    result = subprocess.run(
                        [OLLAMA_BIN, "pull", model_name],
                        capture_output=True, text=True, timeout=120,
                    )
                output = result.stdout + result.stderr
                if "up to date" in output.lower():
                    self.console.print(f"  [green]●[/] {model_name} [dim]up to date[/]")
                elif result.returncode == 0:
                    self.console.print(f"  [green]●[/] {model_name} [bold green]updated![/]")
                    updated_count += 1
                else:
                    self.console.print(f"  [yellow]●[/] {model_name} [dim]check failed[/]")
            except subprocess.TimeoutExpired:
                self.console.print(f"  [yellow]●[/] {model_name} [dim]timeout[/]")
            except Exception:
                continue

        if updated_count > 0:
            self.console.print(f"\n[green]{updated_count} model(s) updated![/]")

        # 2. Check for popular new models not yet installed
        recommended = {
            "qwen3-coder": "Best coding agent (MoE, Alibaba)",
            "qwen3:14b": "General purpose with thinking mode (Alibaba)",
            "phi4-reasoning": "Reasoning that beats 70B models (Microsoft)",
            "gpt-oss": "OpenAI's first open model",
            "qwen3:30b-a3b": "30B brain at 3B speed (MoE)",
            "gemma3:12b": "Efficient general purpose (Google)",
            "llama4:scout": "Multimodal, 10M context (Meta)",
            "devstral": "Agentic coding (Mistral)",
            "deepcoder:14b": "Coding at O3-mini level (DeepSeek)",
            "phi4-reasoning-plus": "Enhanced reasoning (Microsoft)",
            "qwq": "Deep reasoning, math, science (Alibaba)",
            "mistral-small3.2": "Great all-rounder (Mistral)",
        }

        new_models = []
        for model, desc in recommended.items():
            # Check if any installed model starts with this name (handles tags)
            already_have = any(
                inst.startswith(model.split(":")[0]) for inst in installed.keys()
            )
            if not already_have:
                new_models.append((model, desc))

        if not new_models:
            return

        # Show new models available
        self.console.print(f"\n[bold yellow]New models available ({len(new_models)}):[/]")
        for i, (model, desc) in enumerate(new_models, 1):
            self.console.print(f"  [cyan bold]{i}.[/] [cyan]{model}[/] — {desc}")

        try:
            answer = Prompt.ask(
                "\n[bold]Pull new models?[/] [dim](enter numbers, 'all', or 'skip')[/]",
                default="skip"
            )
        except (EOFError, KeyboardInterrupt):
            return

        if answer.strip().lower() == "skip":
            return

        to_pull = []
        if answer.strip().lower() == "all":
            to_pull = [m for m, _ in new_models]
        else:
            # Parse numbers or model names
            parts = answer.replace(",", " ").split()
            for part in parts:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(new_models):
                        to_pull.append(new_models[idx][0])
                except ValueError:
                    # Maybe they typed a model name
                    for model, _ in new_models:
                        if part.lower() in model.lower():
                            to_pull.append(model)
                            break

        if not to_pull:
            return

        for model in to_pull:
            self.console.print(f"\n  [yellow]●[/] Pulling [cyan]{model}[/]...")
            try:
                import subprocess
                result = subprocess.run(
                    [OLLAMA_BIN, "pull", model],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    self.console.print(f"  [green]●[/] [cyan]{model}[/] installed!")
                else:
                    self.console.print(f"  [red]●[/] Failed to pull {model}: {result.stderr[:100]}")
            except subprocess.TimeoutExpired:
                self.console.print(f"  [red]●[/] Timeout pulling {model}")
            except Exception as e:
                self.console.print(f"  [red]●[/] Error: {e}")

        # Refresh multi-model list
        self.multi_models = self._discover_models()
        self.console.print(f"\n[green]Models updated! Now using {len(self.multi_models)} models in multi-mode.[/]")

    def _handle_slash(self, cmd):
        parts = cmd.split(None, 1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/quit", "/exit"):
            self._quit()
            sys.exit(0)
        elif command == "/help":
            claude_status = "[green]connected[/]" if self.claude_key else "[dim]not set[/]"
            qwen_status = "[green]connected[/]" if self.qwen_key else "[dim]not set[/]"
            self.console.print(Panel(
                "[bold]/model <name>[/]  - Switch model\n"
                "  [dim]Local: fast, qwen, coder, reason, gpt[/]\n"
                f"  [dim]Claude: claude, sonnet, haiku, opus ({claude_status})[/]\n"
                f"  [dim]Qwen API: qwen-api, qwen-max, qwen-coder-api, qwen-flash-api ({qwen_status})[/]\n"
                "[bold]/multi <models>[/] - Multi-model mode (e.g. /multi coder qwen reason)\n"
                "[bold]/single[/]        - Back to single model mode\n"
                "[bold]/models[/]       - List all available models\n"
                "[bold]/scan[/] [path]   - Scan project into context\n"
                "[bold]/clear[/]         - Clear conversation\n"
                "[bold]/memories[/]      - Show stored memories\n"
                "[bold]/forget <id>[/]   - Delete a memory\n"
                "[bold]/compact[/]       - Summarize conversation to save context\n"
                "[bold]/context[/]       - Show project context file\n"
                "[bold]/key[/]           - Add/update API key (Claude or Qwen)\n"
                "[bold]/tokens[/]        - Show session token usage and cost\n"
                "[bold]/config[/]        - Show/reload config\n"
                "[bold]/export[/]        - Export session to markdown\n"
                "[bold]/checkpoint[/] [n] - Save conversation checkpoint\n"
                "[bold]/restore[/] [n]   - Restore from checkpoint\n"
                "[bold]/env[/]           - Show shell environment\n"
                "[bold]/verbose[/]       - Toggle verbose mode (show/hide code in stream)\n"
                "[bold]/mode[/] <mode>   - Permission mode: default/relaxed/auto\n"
                "[bold]/plan[/]          - Toggle plan mode (explore → plan → approve → implement)\n"
                "[bold]/accept[/]        - Toggle batch edit review (accept/reject per file)\n"
                "[bold]/search[/]        - Switch search engine (google/duckduckgo)\n"
                "[bold]/cd <path>[/]     - Change working directory\n"
                "[bold]/branch[/]        - Save/switch/list conversation branches\n"
                "[bold]/mcp[/]           - Manage MCP tool servers (add/remove/list)\n"
                "[bold]/quit[/]          - Exit",
                title="Commands", border_style="blue",
            ))
        elif command == "/model":
            if not arg:
                provider = "Claude API" if is_claude_model(self.model) else ("Qwen API" if is_qwen_api_model(self.model) else "Local/Ollama")
                self.console.print(f"Current model: [cyan]{self.model}[/] ({provider})")
                self.console.print(f"Local aliases: {', '.join(MODEL_ALIASES.keys())}")
                if self.claude_key:
                    self.console.print(f"Claude aliases: {', '.join(CLAUDE_ALIASES.keys())}")
                if self.qwen_key:
                    self.console.print(f"Qwen API aliases: {', '.join(QWEN_ALIASES.keys())}")
                return
            if arg in CLAUDE_ALIASES:
                if not self.claude_key:
                    self.console.print("[yellow]No Claude API key set.[/]")
                    key = Prompt.ask("[bold]Enter your Claude API key[/] (or 'skip' to cancel)")
                    if key.strip().lower() == "skip" or not key.strip():
                        self.console.print("[dim]Cancelled. Staying on current model.[/]")
                        return
                    self.claude_key = key.strip()
                    self.settings["claude_api_key"] = self.claude_key
                    save_settings(self.settings)
                    self.console.print("[green]API key saved![/]")
                new_model = CLAUDE_ALIASES[arg]
            elif arg in QWEN_ALIASES:
                if not self.qwen_key:
                    self.console.print("[yellow]No Qwen API key set.[/]")
                    key = Prompt.ask("[bold]Enter your DashScope API key[/] (or 'skip' to cancel)")
                    if key.strip().lower() == "skip" or not key.strip():
                        self.console.print("[dim]Cancelled. Staying on current model.[/]")
                        return
                    self.qwen_key = key.strip()
                    self.settings["qwen_api_key"] = self.qwen_key
                    save_settings(self.settings)
                    self.console.print("[green]Qwen API key saved![/]")
                new_model = QWEN_ALIASES[arg]
            elif arg in MODEL_ALIASES:
                new_model = MODEL_ALIASES[arg]
            else:
                new_model = arg
            self.model = new_model
            self.multi_models = []  # switch to single mode
            if is_claude_model(self.model):
                provider = "[yellow]Claude API[/]"
            elif is_qwen_api_model(self.model):
                provider = "[blue]Qwen API[/]"
            else:
                provider = "[green]Local[/]"
            self.console.print(f"Switched to [cyan]{self.model}[/] ({provider}) [dim](single mode)[/]")
            self.console.print("[dim]Use /multi all to go back to multi-model mode[/]")
        elif command == "/multi":
            if not arg:
                self.console.print("[red]Usage: /multi coder qwen reason  or  /multi all[/]")
                return
            if arg.strip().lower() == "all":
                # Auto-discover all installed Ollama models
                resolved = []
                try:
                    resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
                    resp.raise_for_status()
                    for m in resp.json().get("models", []):
                        resolved.append(m["name"])
                except Exception:
                    self.console.print("[red]Can't reach Ollama to list models.[/]")
                    return
                if not resolved:
                    self.console.print("[yellow]No Ollama models found.[/]")
                    return
            else:
                names = arg.split()
                resolved = []
                for name in names:
                    if name in CLAUDE_ALIASES:
                        if not self.claude_key:
                            self.console.print(f"[red]{name} needs Claude API key. Use /key to add one.[/]")
                            return
                        resolved.append(CLAUDE_ALIASES[name])
                    elif name in QWEN_ALIASES:
                        if not self.qwen_key:
                            self.console.print(f"[red]{name} needs Qwen API key. Use /key qwen to add one.[/]")
                            return
                        resolved.append(QWEN_ALIASES[name])
                    elif name in MODEL_ALIASES:
                        resolved.append(MODEL_ALIASES[name])
                    else:
                        resolved.append(name)
            self.multi_models = resolved
            names_str = ", ".join(f"[cyan]{m}[/]" for m in resolved)
            self.console.print(f"Multi-model mode: {names_str}")
            self.console.print("[dim]All models will answer each question. Use /single to go back.[/]")
        elif command == "/single":
            self.multi_models = []
            self.console.print(f"Single model mode: [cyan]{self.model}[/]")
        elif command == "/models":
            self._list_models()
        elif command == "/clear":
            self.history = []
            self._clear_session()
            self.console.print("[dim]Conversation cleared.[/]")
        elif command == "/memories":
            result = self.memory.list_all()
            self.console.print(Panel(result, title="Memories", border_style="magenta"))
        elif command == "/forget":
            if not arg:
                self.console.print("[red]Usage: /forget <id>[/]")
                return
            try:
                self.console.print(self.memory.delete(int(arg)))
            except ValueError:
                self.console.print("[red]ID must be a number.[/]")
        elif command == "/scan":
            self._scan_project(os.path.expanduser(arg) if arg else self.cwd)
        elif command == "/compact":
            self._compact()
        elif command == "/context":
            ctx_path = self._get_project_context_path()
            if os.path.isfile(ctx_path):
                with open(ctx_path, "r") as f:
                    self.console.print(Panel(f.read(), title=f"Project Context ({self.cwd})", border_style="magenta"))
            else:
                self.console.print(f"[dim]No project context for {self.cwd}[/]")
            self.console.print(f"[dim]File: {ctx_path}[/]")
            self.console.print(f"[dim]Global: {CONTEXT_FILE}[/]")
        elif command == "/key":
            self._setup_api_key(arg.strip().lower() if arg.strip() else None)
        elif command == "/cd":
            path = os.path.expanduser(arg) if arg else os.path.expanduser("~")
            if os.path.isdir(path):
                self.cwd = os.path.abspath(path)
                os.chdir(self.cwd)
                self._detect_git()
                git_note = ""
                if self.git_info:
                    git_note = f" (git: {self.git_info['branch']})"
                self.console.print(f"[dim]Changed to {self.cwd}{git_note}[/]")
            else:
                self.console.print(f"[red]Not a directory: {path}[/]")
        elif command == "/search":
            if not arg:
                engine = get_search_engine()
                g_key, g_cx = get_google_api_keys()
                api_status = "[green]configured[/]" if (g_key and g_cx) else "[dim]not set[/]"
                self.console.print(f"Search engine: [cyan]{engine}[/]")
                self.console.print(f"Google API: {api_status}")
                self.console.print("[dim]Usage: /search google | /search duckduckgo | /search api[/]")
            elif arg.lower() in ("google", "g"):
                set_search_engine("google")
                self.console.print("[green]Switched to Google search (scraping, no API key)[/]")
            elif arg.lower() in ("duckduckgo", "ddg", "duck"):
                set_search_engine("duckduckgo")
                self.console.print("[green]Switched to DuckDuckGo search[/]")
            elif arg.lower() in ("api", "google_api", "gapi"):
                g_key, g_cx = get_google_api_keys()
                if not g_key or not g_cx:
                    self.console.print("[yellow]Google Custom Search API setup[/]")
                    self.console.print("[dim]Get API key: https://console.cloud.google.com/apis[/]")
                    self.console.print("[dim]Get Search Engine ID: https://programmablesearchengine.google.com/[/]")
                    try:
                        api_key = Prompt.ask("\n[bold]Google API key[/] (or 'skip')")
                        if api_key.strip().lower() == "skip":
                            return
                        cx = Prompt.ask("[bold]Search Engine ID (cx)[/]")
                        if not cx.strip():
                            return
                        set_google_api_keys(api_key.strip(), cx.strip())
                        self.settings["google_api_key"] = api_key.strip()
                        self.settings["google_cx"] = cx.strip()
                        save_settings(self.settings)
                        set_search_engine("google_api")
                        self.console.print("[green]Google API configured and set as search engine![/]")
                    except (EOFError, KeyboardInterrupt):
                        return
                else:
                    set_search_engine("google_api")
                    self.console.print("[green]Switched to Google API search (100 free/day)[/]")
            else:
                self.console.print("[red]Unknown engine. Use: /search google | /search duckduckgo | /search api[/]")
        elif command == "/config":
            if arg.strip().lower() == "reload":
                self.config = load_config()
                self.console.print("[green]Config reloaded.[/]")
            else:
                self.console.print(Panel(
                    json.dumps(self.config, indent=2, default=list),
                    title="Config", border_style="blue",
                ))
                self.console.print(f"[dim]Edit: {CONFIG_FILE}[/]")
                self.console.print(f"[dim]Reload: /config reload[/]")
        elif command == "/tokens":
            st = self.session_tokens
            # Estimate context usage
            ctx_est = self._estimate_tokens()
            limit = self._context_limit()
            pct = ctx_est * 100 // limit if limit > 0 else 0
            bar_len = 20
            filled = pct * bar_len // 100
            bar = "[green]" + "█" * filled + "[/][dim]" + "░" * (bar_len - filled) + "[/]"
            self.console.print(Panel(
                f"Input tokens:  {st['input']:,}\n"
                f"Output tokens: {st['output']:,}\n"
                f"Cache read:    {st['cache_read']:,}\n"
                f"Cache create:  {st['cache_creation']:,}\n"
                f"Total cost:    ${st['cost']:.4f}\n"
                f"Context:       ~{ctx_est:,} / {limit:,} tokens ({pct}%)\n"
                f"               {bar} {len(self.history)} messages",
                title="Session Token Usage", border_style="blue",
            ))
        elif command == "/export":
            self._export_session()
        elif command == "/checkpoint":
            name = arg.strip() if arg else f"cp_{len(self._checkpoints) + 1}"
            self._save_checkpoint(name)
        elif command == "/restore":
            if not arg:
                # List checkpoints
                if not self._checkpoints:
                    self.console.print("[dim]No checkpoints saved. Use /checkpoint <name> to create one.[/]")
                else:
                    self.console.print("[bold]Checkpoints:[/]")
                    for cp_name in self._checkpoints:
                        msgs = self._checkpoints[cp_name]["count"]
                        self.console.print(f"  [cyan]{cp_name}[/] ({msgs} messages)")
            else:
                self._restore_checkpoint(arg.strip())
        elif command == "/env":
            lines = [f"  [cyan]{k}[/]: {v}" for k, v in self.shell_env.items()]
            self.console.print(Panel("\n".join(lines), title="Shell Environment", border_style="blue"))
        elif command == "/verbose":
            self.compact_mode = not self.compact_mode
            if self.compact_mode:
                self.console.print("[green]Compact mode ON[/] — code blocks hidden during streaming")
            else:
                self.console.print("[yellow]Verbose mode ON[/] — full output shown during streaming")
        elif command == "/mode":
            if not arg:
                mode_desc = {"default": "confirm all writes/edits/commands",
                             "relaxed": "auto-approve file ops, confirm commands only",
                             "auto": "no confirmations"}
                self.console.print(f"Current mode: [bold cyan]{self.permission_mode}[/] — {mode_desc[self.permission_mode]}")
                self.console.print("[dim]Usage: /mode default | /mode relaxed | /mode auto[/]")
            elif arg.strip().lower() in ("default", "relaxed", "auto"):
                self.permission_mode = arg.strip().lower()
                labels = {"default": "[green]Default[/] — confirm all writes/edits/commands",
                          "relaxed": "[yellow]Relaxed[/] — auto-approve file ops, confirm commands only",
                          "auto": "[red]Auto[/] — no confirmations (be careful!)"}
                self.console.print(f"  Permission mode: {labels[self.permission_mode]}")
            else:
                self.console.print("[red]Unknown mode. Use: default, relaxed, or auto[/]")
        elif command == "/plan":
            if self.plan_mode:
                self.console.print("[dim]Already in plan mode. Type your request to get a plan.[/]")
            else:
                self.plan_mode = True
                self._pending_plan = None
                self.console.print(Panel(
                    "[bold]Plan mode ON[/]\n\n"
                    "The AI will now:\n"
                    "  1. Explore the codebase\n"
                    "  2. Design a step-by-step plan\n"
                    "  3. Show the plan for your approval\n"
                    "  4. Only implement after you approve\n\n"
                    "Type your request, or [bold]/plan off[/] to exit plan mode.",
                    border_style="magenta", title="Plan Mode",
                ))
            if arg and arg.strip().lower() == "off":
                self.plan_mode = False
                self._pending_plan = None
                self.console.print("[dim]Plan mode OFF — back to normal mode.[/]")
        elif command == "/accept":
            self.batch_edits = not self.batch_edits
            if self.batch_edits:
                self.console.print("[green]Batch edit review ON[/] — edits queued for review before applying")
            else:
                self.console.print("[yellow]Batch edit review OFF[/] — edits applied one at a time")
        elif command == "/branch":
            self._handle_branch(arg.strip())
        elif command == "/mcp":
            self._handle_mcp(arg.strip())
        else:
            self.console.print(f"[red]Unknown command: {command}. Type /help[/]")

    def _setup_api_key(self, provider=None):
        if provider == "qwen":
            self._setup_qwen_key()
            return
        if provider == "claude":
            self._setup_claude_key()
            return
        # No provider specified — ask which one
        claude_status = "[green]set[/]" if self.claude_key else "[dim]not set[/]"
        qwen_status = "[green]set[/]" if self.qwen_key else "[dim]not set[/]"
        self.console.print(f"  1. Claude API ({claude_status})")
        self.console.print(f"  2. Qwen API  ({qwen_status})")
        try:
            choice = Prompt.ask("[bold]Which provider?[/]", choices=["1", "2", "claude", "qwen"], default="1")
        except (EOFError, KeyboardInterrupt):
            self.console.print("\n[dim]Cancelled.[/]")
            return
        if choice in ("2", "qwen"):
            self._setup_qwen_key()
        else:
            self._setup_claude_key()

    def _setup_claude_key(self):
        if self.claude_key:
            masked = self.claude_key[:10] + "..." + self.claude_key[-4:]
            self.console.print(f"Current Claude key: [dim]{masked}[/]")
        try:
            key = Prompt.ask("[bold yellow]Paste Claude API key (or 'remove' to delete)[/]")
            key = key.strip()
            if key.lower() == "remove":
                self.claude_key = ""
                self.settings.pop("claude_api_key", None)
                if is_claude_model(self.model):
                    self.model = DEFAULT_MODEL
                    self.settings["default_model"] = DEFAULT_MODEL
                save_settings(self.settings)
                self.console.print("[dim]Claude API key removed. Switched to local models.[/]")
            elif key.startswith("sk-ant-"):
                self.claude_key = key
                self.settings["claude_api_key"] = key
                save_settings(self.settings)
                self.console.print("[green]Claude API key updated![/]")
                self.console.print("[dim]Use /model claude to switch to Claude.[/]")
            else:
                self.console.print("[yellow]Key should start with sk-ant-. Not saved.[/]")
        except (EOFError, KeyboardInterrupt):
            self.console.print("\n[dim]Cancelled.[/]")

    def _setup_qwen_key(self):
        if self.qwen_key:
            masked = self.qwen_key[:8] + "..." + self.qwen_key[-4:]
            self.console.print(f"Current Qwen key: [dim]{masked}[/]")
        try:
            key = Prompt.ask("[bold blue]Paste DashScope API key (or 'remove' to delete)[/]")
            key = key.strip()
            if key.lower() == "remove":
                self.qwen_key = ""
                self.settings.pop("qwen_api_key", None)
                if is_qwen_api_model(self.model):
                    self.model = DEFAULT_MODEL
                    self.settings["default_model"] = DEFAULT_MODEL
                save_settings(self.settings)
                self.console.print("[dim]Qwen API key removed. Switched to local models.[/]")
            elif key.startswith("sk-"):
                self.qwen_key = key
                self.settings["qwen_api_key"] = key
                save_settings(self.settings)
                self.console.print("[green]Qwen API key saved![/]")
                self.console.print("[dim]Use /model qwen-api to switch to Qwen API.[/]")
            else:
                self.console.print("[yellow]Key doesn't look right (should start with sk-). Not saved.[/]")
        except (EOFError, KeyboardInterrupt):
            self.console.print("\n[dim]Cancelled.[/]")

    def _list_models(self):
        lines = []
        if self.claude_key:
            lines.append("[bold yellow]Claude API Models:[/]")
            for alias, model in CLAUDE_ALIASES.items():
                marker = " [green]◀ current[/]" if model == self.model else ""
                lines.append(f"  [cyan]{model}[/] (/{alias}){marker}")
            lines.append("")
        if self.qwen_key:
            lines.append("[bold blue]Qwen API Models:[/]")
            for alias, model in QWEN_ALIASES.items():
                marker = " [green]◀ current[/]" if model == self.model else ""
                lines.append(f"  [cyan]{model}[/] (/{alias}){marker}")
            lines.append("")
        lines.append("[bold green]Local Ollama Models:[/]")
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            if not models:
                lines.append("  [dim]No models found. Is Ollama running?[/]")
            else:
                for m in models:
                    name = m["name"]
                    size = m.get("size", 0)
                    size_str = f"{size / 1e9:.1f}GB" if size > 1e9 else f"{size / 1e6:.0f}MB"
                    marker = " [green]◀ current[/]" if name == self.model else ""
                    lines.append(f"  [cyan]{name}[/] ({size_str}){marker}")
        except Exception:
            lines.append("  [dim]Can't reach Ollama (not running?)[/]")
        self.console.print(Panel("\n".join(lines), title="Available Models", border_style="blue"))

    def _scan_project(self, path):
        if not os.path.isdir(path):
            self.console.print(f"[red]Not a directory: {path}[/]")
            return
        files_content = []
        total_chars = 0
        file_count = 0
        symbols = []  # extracted function/class definitions
        file_list = []  # for index cache
        scan_status = Status(f"  [dim]Scanning {path}...[/]", console=self.console, spinner="dots")
        scan_status.start()
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in SKIP_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                try:
                    size = os.path.getsize(fpath)
                    if size > MAX_FILE_SIZE or size == 0:
                        continue
                    with open(fpath, "r", errors="replace") as f:
                        content = f.read()
                    rel = os.path.relpath(fpath, path)
                    file_list.append({"path": rel, "size": size, "lines": content.count("\n") + 1})
                    files_content.append(f"### {rel}\n```\n{content}\n```")
                    total_chars += len(content)
                    file_count += 1
                    # Extract symbols (functions, classes)
                    for i, line in enumerate(content.splitlines(), 1):
                        stripped = line.strip()
                        if stripped.startswith(("def ", "class ", "function ", "export function ", "export class ", "export default ")):
                            sym_name = stripped.split("(")[0].split(":")[0].strip()
                            symbols.append(f"{rel}:{i}: {sym_name}")
                    scan_status.update(f"  [dim]Scanning... {file_count} files ({total_chars:,} chars)[/]")
                    if total_chars > 500_000:
                        files_content.append("... (stopped, context limit)")
                        break
                except (PermissionError, OSError):
                    continue
            if total_chars > 500_000:
                break
        scan_status.stop()
        if not files_content:
            self.console.print("[yellow]No readable files found.[/]")
            return
        # Cache the index
        self._project_index[path] = {
            "files": file_list,
            "symbols": symbols[:200],  # cap at 200
            "file_count": file_count,
            "total_chars": total_chars,
            "timestamp": time.time(),
        }
        # Build scan text with symbols summary
        scan_text = f"Project scan of {path} ({file_count} files):\n\n"
        if symbols:
            scan_text += "## Key Symbols\n" + "\n".join(symbols[:100]) + "\n\n"
        scan_text += "\n\n".join(files_content)
        self.history.append({"role": "user", "content": f"[Project scan of {path}]"})
        self.history.append({"role": "assistant", "content": scan_text})
        self.console.print(f"[green]Scanned {file_count} files ({total_chars:,} chars) into context.[/]")
        if symbols:
            self.console.print(f"[dim]  Indexed {len(symbols)} symbols (functions/classes)[/]")

    def _compact(self):
        if len(self.history) < 4:
            self.console.print("[dim]Conversation too short to compact.[/]")
            return
        self.console.print("[dim]Compacting conversation...[/]")
        try:
            if is_claude_model(self.model):
                text = self._claude_nostream(
                    "Summarize this conversation concisely, keeping all key facts, decisions, code, and file paths discussed.",
                    self.history + [{"role": "user", "content": "Summarize our conversation keeping all important details."}],
                )
            elif is_qwen_api_model(self.model):
                text = self._qwen_nostream(
                    "Summarize this conversation concisely, keeping all key facts, decisions, code, and file paths discussed.",
                    self.history + [{"role": "user", "content": "Summarize our conversation keeping all important details."}],
                )
            else:
                msgs = [{"role": "system", "content": "Summarize this conversation concisely."}] + self.history
                msgs.append({"role": "user", "content": "Summarize our conversation keeping all important details."})
                resp = requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": self.model, "messages": msgs, "stream": False},
                    timeout=120,
                )
                resp.raise_for_status()
                text = resp.json().get("message", {}).get("content", "")
            if text:
                self.history = [{"role": "assistant", "content": f"[Conversation Summary]\n{text}"}]
                self.console.print("[green]Conversation compacted.[/]")
            else:
                self.console.print("[yellow]Couldn't generate summary.[/]")
        except Exception as e:
            self.console.print(f"[red]Compact error: {e}[/]")

    # ── Auto-compact when context is getting large ──

    def _estimate_tokens(self):
        """Estimate context tokens — use actual API counts if available, else heuristic."""
        if self.session_tokens["input"] > 0:
            return self.session_tokens["input"]
        total = sum(len(m.get("content", "")) for m in self.history if isinstance(m.get("content"), str))
        for m in self.history:
            if isinstance(m.get("content"), list):
                for block in m["content"]:
                    if isinstance(block, dict):
                        total += len(str(block.get("content", "")))
        return total // 4

    def _context_limit(self):
        """Get context window limit for current model."""
        if is_claude_model(self.model):
            return 200_000
        elif is_qwen_api_model(self.model):
            return 1_000_000
        else:
            return self.config.get("auto_compact_threshold", 80000) * 2  # rough Ollama limit

    def _auto_compact_if_needed(self):
        """Auto-compact when context approaches provider limit. Warn at 70%, compact at 85%."""
        try:
            tokens = self._estimate_tokens()
            limit = self._context_limit()
        except Exception:
            return
        warn_threshold = int(limit * 0.70)
        compact_threshold = int(limit * 0.85)
        if tokens > compact_threshold:
            self.console.print(f"[yellow]Context at ~{tokens:,}/{limit:,} tokens ({tokens*100//limit}%). Auto-compacting...[/]")
            self._compact()
        elif tokens > warn_threshold:
            pct = tokens * 100 // limit
            self.console.print(f"[dim yellow]Context: ~{tokens:,}/{limit:,} tokens ({pct}%). Use /compact if responses degrade.[/]")

    # ── Main chat dispatch ──

    def _chat(self, user_msg):
        self._auto_compact_if_needed()

        # Plan mode: intercept to handle planning flow
        if self.plan_mode and self._pending_plan is None:
            # Phase 1: Ask AI to explore and plan (no writes)
            plan_prefix = (
                "[PLAN MODE] You are in planning mode. Do NOT write or edit any files yet. "
                "Instead:\n"
                "1. Read and explore the relevant files to understand the codebase\n"
                "2. Design a clear, step-by-step implementation plan\n"
                "3. List every file you'll create or modify\n"
                "4. End your response with the complete plan\n\n"
                "User request: "
            )
            self._plan_request = user_msg
            user_msg = plan_prefix + user_msg
            # Temporarily force default mode to prevent accidental writes
            old_mode = self.permission_mode
            self.permission_mode = "default"
            self._dispatch_chat(user_msg)
            self.permission_mode = old_mode
            # After plan response, show approval
            self._show_plan_approval()
            return

        if self.plan_mode and self._pending_plan == "approved":
            # Phase 2: User approved the plan — execute it
            self.plan_mode = False
            self._pending_plan = None
            user_msg = (
                f"The user has approved your plan. Now implement it step by step. "
                f"Original request: {self._plan_request}"
            )
            self._plan_request = None

        self._dispatch_chat(user_msg)

    def _dispatch_chat(self, user_msg):
        """Route to the correct provider chat method."""
        if self.multi_models:
            self._chat_multi(user_msg)
        elif is_claude_model(self.model):
            self._chat_claude(user_msg)
        elif is_qwen_api_model(self.model):
            self._chat_qwen(user_msg)
        else:
            self._chat_ollama(user_msg)

    def _review_edit_queue(self):
        """Show batch edit review panel — cycle through queued edits, accept/reject each."""
        queue = get_edit_queue()
        if not queue:
            return []

        self.console.print()
        total = len(queue)
        self.console.print(Panel(
            f"[bold]{total} file edit{'s' if total > 1 else ''}[/] queued for review.\n\n"
            "  [cyan bold]a[/] accept    [cyan bold]r[/] reject    [cyan bold]A[/] accept all    [cyan bold]R[/] reject all\n"
            "  [cyan bold]n[/] next      [cyan bold]p[/] prev      [cyan bold]d[/] show diff     [cyan bold]q[/] finish",
            border_style="green", title="Edit Review",
        ))

        decisions = [None] * total  # None = pending, True = accepted, False = rejected
        current = 0

        while True:
            entry = queue[current]
            path = entry["path"]
            etype = entry["type"]
            desc = entry["description"]

            # Status indicator
            status_icon = "[dim]?[/]"
            if decisions[current] is True:
                status_icon = "[green]✓[/]"
            elif decisions[current] is False:
                status_icon = "[red]✗[/]"

            self.console.print(
                f"\n  {status_icon} [bold]({current + 1}/{total})[/] [cyan]{os.path.basename(path)}[/] "
                f"[dim]— {etype}: {desc}[/]"
            )

            # Show summary
            old = entry.get("old_content", "")
            new = entry.get("new_content", "")
            if old:
                old_lines = len(old.splitlines())
                new_lines = len(new.splitlines())
                added = max(0, new_lines - old_lines)
                removed = max(0, old_lines - new_lines)
                self.console.print(f"    [green]+{added}[/] [red]-{removed}[/] lines | {len(new):,} chars")
            else:
                self.console.print(f"    [green]+ new file[/] | {len(new):,} chars")

            try:
                choice = Prompt.ask(
                    f"  [bold green]({current + 1}/{total})[/]",
                    default="a"
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"

            if choice == "a":
                decisions[current] = True
                self.console.print(f"    [green]✓ Accepted[/]")
                if current < total - 1:
                    current += 1
                elif all(d is not None for d in decisions):
                    break
            elif choice == "r":
                decisions[current] = False
                self.console.print(f"    [red]✗ Rejected[/]")
                if current < total - 1:
                    current += 1
                elif all(d is not None for d in decisions):
                    break
            elif choice.upper() == "A":
                for i in range(total):
                    if decisions[i] is None:
                        decisions[i] = True
                self.console.print(f"  [green]✓ All {total} edits accepted[/]")
                break
            elif choice.upper() == "R":
                for i in range(total):
                    if decisions[i] is None:
                        decisions[i] = False
                self.console.print(f"  [red]✗ All {total} edits rejected[/]")
                break
            elif choice == "n":
                current = (current + 1) % total
            elif choice == "p":
                current = (current - 1) % total
            elif choice == "d":
                # Show full diff
                import difflib
                old_lines = old.splitlines(keepends=True) if old else []
                new_lines = new.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"a/{os.path.basename(path)}",
                    tofile=f"b/{os.path.basename(path)}",
                )
                diff_text = list(diff)
                for line in diff_text[:80]:
                    line = line.rstrip("\n")
                    if line.startswith("+++") or line.startswith("---"):
                        self.console.print(f"    [bold]{line}[/]")
                    elif line.startswith("@@"):
                        self.console.print(f"    [cyan]{line}[/]")
                    elif line.startswith("+"):
                        self.console.print(f"    [green]{line}[/]")
                    elif line.startswith("-"):
                        self.console.print(f"    [red]{line}[/]")
                    else:
                        self.console.print(f"    [dim]{line}[/]")
                if len(diff_text) > 80:
                    self.console.print(f"    [dim]... ({len(diff_text) - 80} more lines)[/]")
            elif choice == "q":
                # Mark remaining as rejected
                for i in range(total):
                    if decisions[i] is None:
                        decisions[i] = False
                break

        # Apply accepted edits
        applied = 0
        rejected = 0
        results = []
        for i, decision in enumerate(decisions):
            if decision:
                result = apply_queued_edit(i)
                results.append(result)
                applied += 1
            else:
                results.append(reject_queued_edit(i))
                rejected += 1

        self.console.print(
            f"\n  [bold]Result:[/] [green]{applied} applied[/] / [red]{rejected} rejected[/]"
        )
        clear_edit_queue()
        return results

    def _show_plan_approval(self):
        """Show plan approval panel after AI has written a plan."""
        self.console.print()
        self.console.print(Panel(
            "[bold]Plan complete![/] Review the plan above, then choose:\n\n"
            "  [cyan bold]1.[/] [bold]Approve[/] — implement the plan now\n"
            "  [cyan bold]2.[/] [bold]Revise[/] — give feedback and get a revised plan\n"
            "  [cyan bold]3.[/] [bold]Reject[/] — cancel and exit plan mode",
            border_style="magenta", title="Plan Approval",
        ))
        try:
            choice = Prompt.ask("[bold magenta]Choice[/]", choices=["1", "2", "3"], default="1")
            if choice == "1":
                self._pending_plan = "approved"
                self.console.print("[green]Plan approved! Implementing...[/]")
                self._chat("")  # Trigger phase 2
            elif choice == "2":
                self._pending_plan = None  # Reset to allow re-plan
                feedback = input("\033[1;36mFeedback: \033[0m")
                if feedback.strip():
                    self._chat(f"Revise the plan based on this feedback: {feedback}")
            else:
                self.plan_mode = False
                self._pending_plan = None
                self._plan_request = None
                self.console.print("[dim]Plan rejected. Back to normal mode.[/]")
        except (EOFError, KeyboardInterrupt):
            self.plan_mode = False
            self._pending_plan = None

    # ── Multi-model chat ──

    def _chat_multi(self, user_msg):
        """Send message to all selected models one at a time (sequential to save RAM)."""
        from rich.status import Status

        memories_ctx = self.memory.get_context()
        context_file_ctx = self._load_context_file()

        self.console.print(f"\n[dim]Querying {len(self.multi_models)} models sequentially (saves RAM)...[/]\n")

        # Query models one at a time so Ollama can unload between them
        results = {}
        for i, model_name in enumerate(self.multi_models, 1):
            with Status(f"[bold cyan]({i}/{len(self.multi_models)}) Asking {model_name}...[/]", console=self.console):
                try:
                    if is_claude_model(model_name):
                        results[model_name] = self._multi_query_claude(model_name, user_msg, memories_ctx, context_file_ctx)
                    elif is_qwen_api_model(model_name):
                        results[model_name] = self._multi_query_qwen(model_name, user_msg, memories_ctx, context_file_ctx)
                    else:
                        results[model_name] = self._multi_query_ollama(model_name, user_msg, memories_ctx, context_file_ctx)
                except Exception as e:
                    results[model_name] = f"Error: {e}"

            # Display result immediately after each model finishes
            response = results[model_name]
            if is_claude_model(model_name):
                color = "yellow"
            elif is_qwen_api_model(model_name):
                color = "blue"
            else:
                color = "green"
            self.console.print(Panel(
                response,
                title=f"[bold {color}]{model_name}[/]",
                border_style=color,
            ))
            self.console.print()

        # Consensus: pick the smartest available model to merge the best parts
        self.console.print("[dim]Generating consensus from all answers...[/]\n")
        consensus = self._generate_consensus(user_msg, results)
        self.console.print(Panel(
            consensus,
            title="[bold magenta]Final Answer (consensus)[/]",
            border_style="magenta",
        ))
        # Save consensus to history
        self.history.append({"role": "user", "content": user_msg})
        self.history.append({"role": "assistant", "content": consensus})
        self._save_session()

    def _multi_query_ollama(self, model_name, user_msg, memories_ctx, context_file_ctx):
        """Non-streaming Ollama query for multi-model mode."""
        system_prompt = SYSTEM_PROMPT.format(cwd=self.cwd, model=model_name, memories=memories_ctx)
        if context_file_ctx:
            system_prompt += "\n\n" + context_file_ctx
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": model_name, "messages": messages, "stream": False, "keep_alive": 0},
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "No response")
        except Exception as e:
            return f"Error: {e}"

    def _multi_query_claude(self, model_name, user_msg, memories_ctx, context_file_ctx):
        """Non-streaming Claude query for multi-model mode."""
        if not self.claude_key:
            return "No API key"
        system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=model_name, memories=memories_ctx)
        if context_file_ctx:
            system_prompt += "\n\n" + context_file_ctx
        try:
            resp = requests.post(
                CLAUDE_API_URL,
                headers={
                    "x-api-key": self.claude_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model_name,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_msg}],
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("content", [{}])[0].get("text", "No response")
        except Exception as e:
            return f"Error: {e}"

    def _generate_consensus(self, user_msg, results):
        """Use the smartest available model to combine all answers into the best one."""
        # Build the prompt with all model responses
        answers_text = ""
        for model_name, response in results.items():
            answers_text += f"\n### {model_name}:\n{response}\n"

        consensus_prompt = (
            f"The user asked: \"{user_msg}\"\n\n"
            f"Multiple AI models gave these answers:\n{answers_text}\n\n"
            "Your job: Analyze all answers above. Combine the best, most accurate, and most complete parts "
            "from each model into ONE final answer. If models disagree, go with the most correct one. "
            "If one model has unique good insights the others missed, include them. "
            "Write the final merged answer directly - don't mention the models or say 'Model X said...'. "
            "Just give the best possible answer."
        )

        # Use Claude if available (smartest), otherwise use the largest local model
        if self.claude_key:
            try:
                resp = requests.post(
                    CLAUDE_API_URL,
                    headers={
                        "x-api-key": self.claude_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": consensus_prompt}],
                    },
                    timeout=300,
                )
                resp.raise_for_status()
                return resp.json().get("content", [{}])[0].get("text", "Could not generate consensus.")
            except Exception:
                pass  # fall through to local model

        # Fallback: use the best local model for consensus
        judge_model = "qwen3-coder"  # best local model for analysis
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": judge_model,
                    "messages": [{"role": "user", "content": consensus_prompt}],
                    "stream": False,
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "Could not generate consensus.")
        except Exception as e:
            return f"Consensus error: {e}"

    # ── Ollama chat (text-based actions) ──

    def _chat_ollama(self, user_msg):
        self.history.append({"role": "user", "content": user_msg})
        for iteration in range(MAX_ITERATIONS):
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = SYSTEM_PROMPT.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx
            messages = [{"role": "system", "content": system_prompt}] + self.history

            assistant_text = self._stream_ollama(messages)
            if assistant_text is None:
                return
            self.history.append({"role": "assistant", "content": assistant_text})

            actions = parse_actions(assistant_text)
            if not actions:
                self._save_session()
                break
            # Enable batch mode if active
            if self.batch_edits:
                set_batch_mode(True)
            results = []
            for action in actions:
                action_label = _tool_label(action['name'], action.get('params', {}))
                with Status(f"  [yellow]●[/] {action_label}", console=self.console, spinner="dots"):
                    result = execute_action(action, self.memory, self._confirm)
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results.append(f"[Result of {action['name']}]\n{result}")
                self.console.print(f"  [green]●[/] {action_label}")
            # Review queued edits if any
            if self.batch_edits and get_edit_queue():
                set_batch_mode(False)
                review_results = self._review_edit_queue()
                for rr in review_results:
                    results.append(f"[Edit Review]\n{rr}")
            set_batch_mode(False)
            self.history.append({"role": "user", "content": f"[Action Results]\n" + "\n\n".join(results)})
            if iteration < MAX_ITERATIONS - 1:
                self.console.print(f"  [dim]({iteration + 1}/{MAX_ITERATIONS} iterations)[/]")
        else:
            self.console.print(f"[yellow]Reached max iterations ({MAX_ITERATIONS}). Stopping.[/]")

    # ── Claude chat (native tool_use API) ──

    def _chat_claude(self, user_msg):
        self.history.append({"role": "user", "content": user_msg})

        for iteration in range(MAX_ITERATIONS):
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx

            # Build Claude messages (must alternate user/assistant)
            messages = self._build_claude_messages()

            response = self._call_claude_stream(system_prompt, messages)
            if response is None:
                return

            text_content = response.get("text", "")
            tool_calls = response.get("tool_calls", [])
            stop_reason = response.get("stop_reason", "end_turn")

            # Build assistant message content blocks
            assistant_content = []
            if text_content:
                assistant_content.append({"type": "text", "text": text_content})
            for tc in tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            self.history.append({"role": "assistant", "content": assistant_content})

            if not tool_calls:
                self._save_session()
                break  # No tools = done

            # Enable batch mode if active
            if self.batch_edits:
                set_batch_mode(True)

            # Execute tools - parallel for read-only, sequential for writes
            # Split MCP tools from regular tools
            mcp_calls = [tc for tc in tool_calls if tc["name"].startswith("mcp_")]
            regular_calls = [tc for tc in tool_calls if not tc["name"].startswith("mcp_")]
            results_list = []
            if len(regular_calls) > 1:
                with Status(f"  [yellow]●[/] Running {len(regular_calls)} tools...", console=self.console, spinner="dots"):
                    results_list = execute_tools_parallel(regular_calls, self.memory, self._confirm)
                for tc_id, result in results_list:
                    tc_name = next((tc["name"] for tc in regular_calls if tc["id"] == tc_id), "?")
                    tc_input = next((tc.get("input", {}) for tc in regular_calls if tc["id"] == tc_id), {})
                    self.console.print(f"  [green]●[/] {_tool_label(tc_name, tc_input)}")
            elif len(regular_calls) == 1:
                tc = regular_calls[0]
                label = _tool_label(tc['name'], tc.get('input', {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self._execute_tool(tc["name"], tc["input"])
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self.console.print(f"  [green]●[/] {label}")
            for tc in mcp_calls:
                label = _tool_label(tc["name"], tc.get("input", {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self.mcp.call_tool(tc["name"], tc.get("input", {}))
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self.console.print(f"  [green]●[/] {label}")

            # Review queued edits if any
            if self.batch_edits and get_edit_queue():
                set_batch_mode(False)
                review_results = self._review_edit_queue()
                # Add review results to tool results
                for rr in review_results:
                    results_list.append(("review", rr))
            set_batch_mode(False)

            # Build tool results - handle images specially for Claude vision
            tool_results = []
            for tc_id, result in results_list:
                if result.startswith("__IMAGE__:"):
                    # Parse image data for Claude vision
                    parts = result.split(":", 2)
                    media_type = parts[1]
                    b64_data = parts[2]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc_id,
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_data,
                                }
                            }
                        ],
                    })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc_id,
                        "content": result,
                    })

            self.history.append({"role": "user", "content": tool_results})
            self._save_session()

            if iteration < MAX_ITERATIONS - 1:
                self.console.print(f"  [dim]({iteration + 1}/{MAX_ITERATIONS} iterations)[/]")

        else:
            self.console.print(f"[yellow]Reached max iterations ({MAX_ITERATIONS}). Stopping.[/]")

    def _build_claude_messages(self):
        """Convert history to Claude API format. Handles content blocks properly."""
        messages = []
        for msg in self.history:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                continue
            if role not in ("user", "assistant"):
                role = "user"
            # Handle string content and list content (tool_use/tool_result blocks)
            if isinstance(content, str):
                # Merge consecutive same-role text messages
                if messages and messages[-1]["role"] == role and isinstance(messages[-1]["content"], str):
                    messages[-1]["content"] += "\n\n" + content
                else:
                    messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Content blocks (tool_use or tool_result) - don't merge
                messages.append({"role": role, "content": content})
        # Ensure starts with user
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "Hello"})
        return messages

    def _call_claude_stream(self, system_prompt, messages):
        """Stream Claude API with native tool_use, prompt caching, and token tracking."""
        if not self.claude_key:
            self.console.print("[red]No Claude API key. Use /key to add one.[/]")
            return None

        # Prompt caching: system as blocks with cache_control
        system_blocks = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        # Cache the tool definitions too
        cached_tools = [dict(t) for t in self._get_all_tools()]
        cached_tools[-1] = dict(cached_tools[-1])
        cached_tools[-1]["cache_control"] = {"type": "ephemeral"}

        try:
            resp = _retry_api_call(
                lambda: requests.post(
                    CLAUDE_API_URL,
                    headers={
                        "x-api-key": self.claude_key,
                        "anthropic-version": "2023-06-01",
                        "anthropic-beta": "prompt-caching-2024-07-31",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": 8192,
                        "system": system_blocks,
                        "messages": messages,
                        "tools": cached_tools,
                        "stream": True,
                    },
                    stream=True,
                    timeout=300,
                ),
                provider_name="Claude",
            )
            if resp.status_code == 401:
                self.console.print("[red]Invalid Claude API key. Use /key to update it.[/]")
                return None
            if resp.status_code >= 400:
                self.console.print(f"[red]Claude API error {resp.status_code}: {resp.text[:200]}[/]")
                return None
        except Exception as e:
            if _logger:
                _logger.error(f"Claude API failed: {e}")
            self.console.print(f"[red]Claude error: {e}[/]")
            if self.qwen_key:
                self.console.print("[yellow]Try /model qwen-api to switch providers.[/]")
            return None

        # Parse streaming response
        self.console.print()
        stream_start = time.time()

        full_text = []
        tool_calls = []
        current_tool = None
        current_tool_json = []
        stop_reason = "end_turn"
        stream_usage = {}
        first_token = True
        writer = StreamWriter(self.console, compact=self.compact_mode)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()

        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace")
                if not line_str.startswith("data: "):
                    continue
                data = line_str[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "message_start":
                    msg = event.get("message", {})
                    stream_usage = msg.get("usage", {})

                elif event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        if first_token:
                            thinking_status.stop()
                            first_token = False
                        current_tool = {"id": block["id"], "name": block["name"], "input": {}}
                        current_tool_json = []

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        token = delta.get("text", "")
                        if token:
                            if first_token:
                                thinking_status.stop()
                                self.console.print("[bold green]Kodiqa[/] ", end="")
                                first_token = False
                            full_text.append(token)
                            writer.write(token)
                    elif delta.get("type") == "input_json_delta":
                        json_chunk = delta.get("partial_json", "")
                        if json_chunk:
                            current_tool_json.append(json_chunk)

                elif event_type == "content_block_stop":
                    if current_tool is not None:
                        try:
                            input_str = "".join(current_tool_json)
                            current_tool["input"] = json.loads(input_str) if input_str else {}
                        except json.JSONDecodeError:
                            current_tool["input"] = {}
                        tool_calls.append(current_tool)
                        current_tool = None
                        current_tool_json = []

                elif event_type == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason", stop_reason)
                    delta_usage = event.get("usage", {})
                    if delta_usage:
                        stream_usage.update(delta_usage)

                elif event_type == "error":
                    thinking_status.stop()
                    err = event.get("error", {})
                    self.console.print(f"\n[red]Claude error: {err.get('message', 'Unknown')}[/]")

        except KeyboardInterrupt:
            thinking_status.stop()
            writer.flush_pending()
            self.console.print("\n[dim](interrupted)[/]")

        if first_token:
            thinking_status.stop()
        writer.flush_pending()
        self.console.print()
        elapsed = time.time() - stream_start
        self._display_token_usage(stream_usage, elapsed=elapsed)
        return {"text": "".join(full_text), "tool_calls": tool_calls, "stop_reason": stop_reason}

    def _claude_nostream(self, system, messages):
        """Non-streaming Claude call (for compact)."""
        if not self.claude_key:
            return ""
        claude_msgs = self._build_claude_messages()
        try:
            resp = requests.post(
                CLAUDE_API_URL,
                headers={
                    "x-api-key": self.claude_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"model": self.model, "max_tokens": 4096, "system": system, "messages": claude_msgs},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("content", [{}])[0].get("text", "")
        except Exception:
            return ""

    # ── Qwen API chat (OpenAI-compatible with tool calling) ──

    def _execute_tool(self, name, params):
        """Execute a tool call, routing MCP tools to MCP manager."""
        if name.startswith("mcp_"):
            return self.mcp.call_tool(name, params or {})
        return execute_tool_call(name, params, self.memory, self._confirm)

    def _get_all_tools(self):
        """Get all tools: built-in + MCP server tools."""
        tools = list(CLAUDE_TOOLS)
        tools.extend(self.mcp.get_all_tools())
        return tools

    def _get_qwen_tools(self):
        """Convert Claude tool schemas to OpenAI function-calling format for Qwen."""
        tools = []
        for t in self._get_all_tools():
            tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            })
        return tools

    def _chat_qwen(self, user_msg):
        self.history.append({"role": "user", "content": user_msg})

        for iteration in range(MAX_ITERATIONS):
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx

            messages = self._build_qwen_messages(system_prompt)

            response = self._call_qwen_stream(messages)
            if response is None:
                return

            text_content = response.get("text", "")
            tool_calls = response.get("tool_calls", [])

            # Build assistant message
            assistant_msg = {"role": "assistant", "content": text_content or None}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
                    }
                    for tc in tool_calls
                ]
            self.history.append(assistant_msg)

            if not tool_calls:
                self._save_session()
                break

            # Enable batch mode if active
            if self.batch_edits:
                set_batch_mode(True)

            # Execute tools — split MCP from regular
            mcp_calls = [tc for tc in tool_calls if tc["name"].startswith("mcp_")]
            regular_calls = [tc for tc in tool_calls if not tc["name"].startswith("mcp_")]
            results_list = []
            if len(regular_calls) > 1:
                with Status(f"  [yellow]●[/] Running {len(regular_calls)} tools...", console=self.console, spinner="dots"):
                    results_list = execute_tools_parallel(regular_calls, self.memory, self._confirm)
                for tc_id, result in results_list:
                    tc_name = next((tc["name"] for tc in regular_calls if tc["id"] == tc_id), "?")
                    tc_input = next((tc.get("input", {}) for tc in regular_calls if tc["id"] == tc_id), {})
                    self.console.print(f"  [green]●[/] {_tool_label(tc_name, tc_input)}")
            elif len(regular_calls) == 1:
                tc = regular_calls[0]
                label = _tool_label(tc["name"], tc.get("input", {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self._execute_tool(tc["name"], tc["input"])
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self.console.print(f"  [green]●[/] {label}")
            for tc in mcp_calls:
                label = _tool_label(tc["name"], tc.get("input", {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self.mcp.call_tool(tc["name"], tc.get("input", {}))
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self.console.print(f"  [green]●[/] {label}")

            # Review queued edits if any
            if self.batch_edits and get_edit_queue():
                set_batch_mode(False)
                review_results = self._review_edit_queue()
                for rr in review_results:
                    results_list.append(("review", rr))
            set_batch_mode(False)

            # Add tool results as separate messages (OpenAI format)
            for tc_id, result in results_list:
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })
            self._save_session()

            if iteration < MAX_ITERATIONS - 1:
                self.console.print(f"  [dim]({iteration + 1}/{MAX_ITERATIONS} iterations)[/]")
        else:
            self.console.print(f"[yellow]Reached max iterations ({MAX_ITERATIONS}). Stopping.[/]")

    def _build_qwen_messages(self, system_prompt):
        """Convert history to OpenAI message format for Qwen API."""
        messages = [{"role": "system", "content": system_prompt}]
        for msg in self.history:
            role = msg.get("role")
            if role == "system":
                continue
            if role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
            elif role == "assistant":
                entry = {"role": "assistant", "content": msg.get("content") or ""}
                if "tool_calls" in msg:
                    entry["tool_calls"] = msg["tool_calls"]
                # Skip Claude-format content blocks (list of dicts)
                if isinstance(entry["content"], list):
                    text_parts = [b.get("text", "") for b in entry["content"] if isinstance(b, dict) and b.get("type") == "text"]
                    entry["content"] = "\n".join(text_parts) if text_parts else ""
                messages.append(entry)
            elif role == "user":
                content = msg.get("content", "")
                # Skip Claude tool_result blocks
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            text_parts.append(str(block.get("content", "")))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts) if text_parts else ""
                messages.append({"role": "user", "content": content})
        return messages

    def _call_qwen_stream(self, messages):
        """Stream Qwen API with OpenAI-compatible tool calling, retry, and token tracking."""
        if not self.qwen_key:
            self.console.print("[red]No Qwen API key. Use /key qwen to add one.[/]")
            return None

        try:
            resp = _retry_api_call(
                lambda: requests.post(
                    QWEN_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.qwen_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": self._get_qwen_tools(),
                        "max_tokens": 8192,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                    stream=True,
                    timeout=300,
                ),
                provider_name="Qwen",
            )
            if resp.status_code == 401:
                self.console.print("[red]Invalid Qwen API key. Use /key qwen to update it.[/]")
                return None
            if resp.status_code >= 400:
                self.console.print(f"[red]Qwen API error {resp.status_code}: {resp.text[:200]}[/]")
                return None
        except Exception as e:
            if _logger:
                _logger.error(f"Qwen API failed: {e}")
            self.console.print(f"[red]Qwen error: {e}[/]")
            if self.claude_key:
                self.console.print("[yellow]Try /model claude to switch providers.[/]")
            return None

        # Parse SSE streaming response (OpenAI format)
        self.console.print()
        stream_start = time.time()

        full_text = []
        tool_calls = {}  # index -> {id, name, arguments}
        stream_usage = {}
        first_token = True
        writer = StreamWriter(self.console, compact=self.compact_mode)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()

        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace")
                if not line_str.startswith("data: "):
                    continue
                data = line_str[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # Capture usage from final chunk
                usage_data = chunk.get("usage")
                if usage_data:
                    stream_usage = usage_data

                choice = chunk.get("choices", [{}])[0] if chunk.get("choices") else {}
                delta = choice.get("delta", {})

                # Text content
                text_chunk = delta.get("content")
                if text_chunk:
                    if first_token:
                        thinking_status.stop()
                        self.console.print("[bold green]Kodiqa[/] ", end="")
                        first_token = False
                    full_text.append(text_chunk)
                    writer.write(text_chunk)

                # Tool calls (streamed incrementally)
                tc_list = delta.get("tool_calls", [])
                for tc_delta in tc_list:
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": tc_delta.get("id", f"call_{idx}"),
                            "name": "",
                            "arguments": [],
                        }
                        if first_token:
                            thinking_status.stop()
                            first_token = False
                    if tc_delta.get("id"):
                        tool_calls[idx]["id"] = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if func.get("name"):
                        tool_calls[idx]["name"] = func["name"]
                    if func.get("arguments"):
                        tool_calls[idx]["arguments"].append(func["arguments"])

        except KeyboardInterrupt:
            thinking_status.stop()
            writer.flush_pending()
            self.console.print("\n[dim](interrupted)[/]")

        if first_token:
            thinking_status.stop()
        writer.flush_pending()
        self.console.print()
        elapsed = time.time() - stream_start
        self._display_token_usage(stream_usage, elapsed=elapsed)

        # Parse accumulated tool calls
        parsed_tools = []
        for idx in sorted(tool_calls.keys()):
            tc = tool_calls[idx]
            args_str = "".join(tc["arguments"])
            try:
                input_data = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                input_data = {}
            parsed_tools.append({"id": tc["id"], "name": tc["name"], "input": input_data})

        return {"text": "".join(full_text), "tool_calls": parsed_tools}

    def _multi_query_qwen(self, model_name, user_msg, memories_ctx, context_file_ctx):
        """Non-streaming Qwen API query for multi-model mode."""
        if not self.qwen_key:
            return "No API key"
        system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=model_name, memories=memories_ctx)
        if context_file_ctx:
            system_prompt += "\n\n" + context_file_ctx
        try:
            resp = requests.post(
                QWEN_API_URL,
                headers={
                    "Authorization": f"Bearer {self.qwen_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 4096,
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "No response")
        except Exception as e:
            return f"Error: {e}"

    def _qwen_nostream(self, system, messages):
        """Non-streaming Qwen call (for compact)."""
        if not self.qwen_key:
            return ""
        qwen_msgs = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m.get("content"), str):
                qwen_msgs.append({"role": m["role"], "content": m["content"]})
        try:
            resp = requests.post(
                QWEN_API_URL,
                headers={
                    "Authorization": f"Bearer {self.qwen_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "max_tokens": 4096, "messages": qwen_msgs},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            return ""

    # ── Ollama streaming ──

    def _stream_ollama(self, messages):
        try:
            resp = _retry_api_call(
                lambda: requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": self.model, "messages": messages, "stream": True},
                    stream=True, timeout=300,
                ),
                provider_name="Ollama",
            )
            resp.raise_for_status()
        except requests.ConnectionError:
            self.console.print("[red]Can't connect to Ollama. Is it running?[/]")
            if self.claude_key:
                self.console.print("[yellow]Try /model claude to use Claude API instead.[/]")
            elif self.qwen_key:
                self.console.print("[yellow]Try /model qwen-api to use Qwen API instead.[/]")
            return None
        except Exception as e:
            if _logger:
                _logger.error(f"Ollama failed: {e}")
            self.console.print(f"[red]Ollama error: {e}[/]")
            return None

        self.console.print()
        stream_start = time.time()
        full_text = []
        first_token = True
        token_count = 0
        writer = StreamWriter(self.console, compact=self.compact_mode)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("done"):
                    break
                token = chunk.get("message", {}).get("content", "")
                if token:
                    if first_token:
                        thinking_status.stop()
                        self.console.print("[bold green]Kodiqa[/] ", end="")
                        first_token = False
                    full_text.append(token)
                    token_count += 1
                    writer.write(token)
        except KeyboardInterrupt:
            thinking_status.stop()
            writer.flush_pending()
            self.console.print("\n[dim](interrupted)[/]")
        if first_token:
            thinking_status.stop()
        writer.flush_pending()
        self.console.print()
        elapsed = time.time() - stream_start
        if token_count > 0:
            tps = token_count / elapsed if elapsed > 0 else 0
            self.console.print(f"  [dim]{token_count} tokens | {tps:.1f} tok/s | {elapsed:.1f}s[/]")
        return "".join(full_text)

    # ── Shared ──

    def _display_token_usage(self, usage, model=None, elapsed=None):
        """Show token usage, cost, and response metrics after a response."""
        if not usage:
            if elapsed:
                self.console.print(f"  [dim]{elapsed:.1f}s[/]")
            return
        model = model or self.model
        inp = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
        out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        self.session_tokens["input"] += inp
        self.session_tokens["output"] += out
        self.session_tokens["cache_read"] += cache_read
        self.session_tokens["cache_creation"] += cache_create
        cost_rates = COST_TABLE.get(model, (0, 0))
        cost = (inp * cost_rates[0] + out * cost_rates[1]) / 1_000_000
        self.session_tokens["cost"] += cost
        parts = [f"[dim]{inp:,} in / {out:,} out[/]"]
        if cache_read:
            parts.append(f"[dim green]cache: {cache_read:,}[/]")
        if elapsed and out > 0:
            tps = out / elapsed
            parts.append(f"[dim]{tps:.1f} tok/s[/]")
        elif elapsed:
            parts.append(f"[dim]{elapsed:.1f}s[/]")
        if cost > 0:
            parts.append(f"[dim](${cost:.4f} / session: ${self.session_tokens['cost']:.4f})[/]")
        self.console.print("  " + " | ".join(parts))

    def _export_session(self):
        """Export the current conversation to a markdown file."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = os.path.join(KODIQA_DIR, "exports")
        os.makedirs(export_dir, exist_ok=True)
        filepath = os.path.join(export_dir, f"session_{timestamp}.md")
        lines = [f"# Kodiqa Session — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        lines.append(f"Model: {self.model}\n\n---\n")
        for msg in self.history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, dict) and block.get("type") == "tool_use":
                        text_parts.append(f"[Tool: {block.get('name', '?')}]")
                content = "\n".join(text_parts)
            if role == "user":
                lines.append(f"## User\n\n{content}\n\n")
            elif role == "assistant":
                lines.append(f"## Kodiqa\n\n{content}\n\n")
            else:
                lines.append(f"## {role}\n\n{content}\n\n")
        with open(filepath, "w") as f:
            f.write("".join(lines))
        self.console.print(f"  [green]Session exported to {filepath}[/]")

    def _save_checkpoint(self, name):
        """Save current conversation state as a checkpoint."""
        import copy
        self._checkpoints[name] = {
            "history": copy.deepcopy(self.history),
            "model": self.model,
            "count": len(self.history),
        }
        # Also save to disk
        filepath = os.path.join(self._checkpoint_dir, f"{name}.json")
        with open(filepath, "w") as f:
            json.dump(self._checkpoints[name], f, default=str)
        self.console.print(f"  [green]Checkpoint '{name}' saved ({len(self.history)} messages)[/]")

    def _restore_checkpoint(self, name):
        """Restore conversation from a checkpoint."""
        if name in self._checkpoints:
            import copy
            cp = self._checkpoints[name]
            self.history = copy.deepcopy(cp["history"])
            self.model = cp.get("model", self.model)
            self.console.print(f"  [green]Restored checkpoint '{name}' ({len(self.history)} messages)[/]")
            return
        # Try loading from disk
        filepath = os.path.join(self._checkpoint_dir, f"{name}.json")
        if os.path.isfile(filepath):
            try:
                with open(filepath, "r") as f:
                    cp = json.load(f)
                self.history = cp.get("history", [])
                self.model = cp.get("model", self.model)
                self._checkpoints[name] = cp
                self.console.print(f"  [green]Restored checkpoint '{name}' from disk ({len(self.history)} messages)[/]")
            except Exception as e:
                self.console.print(f"  [red]Failed to restore checkpoint: {e}[/]")
        else:
            self.console.print(f"  [red]Checkpoint '{name}' not found.[/]")

    def _handle_branch(self, arg):
        """Handle /branch commands: create, list, switch, delete."""
        import copy
        if not arg or arg == "list":
            if not self._branches:
                self.console.print("[dim]No branches. Use /branch save <name> to create one.[/]")
            else:
                self.console.print("[bold]Conversation branches:[/]")
                for name, data in self._branches.items():
                    msgs = len(data["history"])
                    model = data.get("model", "?")
                    self.console.print(f"  [cyan]{name}[/] ({msgs} messages, model: {model})")
            return
        parts = arg.split(None, 1)
        subcmd = parts[0].lower()
        name = parts[1] if len(parts) > 1 else ""

        if subcmd == "save":
            if not name:
                name = f"branch_{len(self._branches) + 1}"
            self._branches[name] = {
                "history": copy.deepcopy(self.history),
                "model": self.model,
            }
            self.console.print(f"  [green]Branch '{name}' saved ({len(self.history)} messages)[/]")
        elif subcmd == "switch":
            if not name or name not in self._branches:
                self.console.print(f"  [red]Branch '{name}' not found. Use /branch list[/]")
                return
            # Save current as "_previous" auto-branch
            self._branches["_previous"] = {
                "history": copy.deepcopy(self.history),
                "model": self.model,
            }
            data = self._branches[name]
            self.history = copy.deepcopy(data["history"])
            self.model = data.get("model", self.model)
            self.console.print(f"  [green]Switched to branch '{name}' ({len(self.history)} messages)[/]")
            self.console.print(f"  [dim]Previous state saved as '_previous'[/]")
        elif subcmd == "delete":
            if name in self._branches:
                del self._branches[name]
                self.console.print(f"  [dim]Branch '{name}' deleted.[/]")
            else:
                self.console.print(f"  [red]Branch '{name}' not found.[/]")
        else:
            self.console.print("[dim]Usage: /branch save <name> | /branch switch <name> | /branch delete <name> | /branch list[/]")

    def _handle_mcp(self, arg):
        """Handle /mcp commands: add, remove, list."""
        if not arg or arg == "list":
            info = self.mcp.list_servers()
            self.console.print(Panel(info, title="MCP Servers", border_style="blue"))
            return
        parts = arg.split(None, 2)
        subcmd = parts[0].lower()

        if subcmd == "add":
            if len(parts) < 3:
                self.console.print("[dim]Usage: /mcp add <name> <command> [args...][/]")
                return
            name = parts[1]
            cmd_parts = parts[2].split()
            command = cmd_parts[0]
            cmd_args = cmd_parts[1:] if len(cmd_parts) > 1 else []
            with Status(f"  [dim]Connecting to MCP server '{name}'...[/]", console=self.console, spinner="dots"):
                tools = self.mcp.add_server(name, command, cmd_args)
            if tools is not None:
                tool_names = [t["name"] for t in tools]
                self.console.print(f"  [green]Connected: {name}[/] ({len(tools)} tools: {', '.join(tool_names[:5])})")
            else:
                self.console.print(f"  [red]Failed to connect to '{name}'[/]")
        elif subcmd == "remove":
            if len(parts) < 2:
                self.console.print("[dim]Usage: /mcp remove <name>[/]")
                return
            name = parts[1]
            if self.mcp.remove_server(name):
                self.console.print(f"  [dim]Disconnected: {name}[/]")
            else:
                self.console.print(f"  [red]Server '{name}' not found.[/]")
        else:
            self.console.print("[dim]Usage: /mcp add <name> <command> | /mcp remove <name> | /mcp list[/]")

    def _confirm(self, description):
        self.console.print()
        # Extract action type from description (e.g. "Write file: ..." -> "write file")
        action_type = description.split(":")[0].strip().lower()

        # Auto mode: approve everything
        if self.permission_mode == "auto":
            self.console.print(f"  [green]●[/] {description} [dim](auto mode)[/]")
            return True

        # Relaxed mode: approve file ops, confirm commands/git only
        if self.permission_mode == "relaxed":
            command_types = {"run command", "git commit", "delete file"}
            if action_type not in command_types:
                self.console.print(f"  [green]●[/] {description} [dim](relaxed mode)[/]")
                return True

        # Check if this action type was auto-approved
        if not hasattr(self, "_auto_approved"):
            self._auto_approved = set()
        if action_type in self._auto_approved:
            self.console.print(f"  [green]●[/] {description} [dim](auto-approved)[/]")
            return True

        try:
            self.console.print(f"  [bold yellow]Allow:[/] {description}")
            self.console.print(f"    [cyan bold]1.[/] [bold]Yes[/]")
            self.console.print(f"    [cyan bold]2.[/] [bold]Yes, don't ask again[/] [dim](for this action type)[/]")
            self.console.print(f"    [cyan bold]3.[/] [bold]No[/]")
            choice = Prompt.ask("  [bold yellow]Choice[/]", choices=["1", "2", "3"], default="1")
            if choice == "1":
                return True
            elif choice == "2":
                self._auto_approved.add(action_type)
                self.console.print(f"  [dim]Auto-approving future \"{action_type}\" actions this session.[/]")
                return True
            else:
                return False
        except (EOFError, KeyboardInterrupt):
            return False


def _tool_label(name, params):
    """Create a human-readable label for a tool action."""
    p = params or {}
    labels = {
        "read_file": lambda: f"Read [cyan]{_short_path(p.get('path', '?'))}[/]",
        "write_file": lambda: f"Write [cyan]{_short_path(p.get('path', '?'))}[/]",
        "edit_file": lambda: f"Edit [cyan]{_short_path(p.get('path', p.get('old_string', '?')[:30]))}[/]",
        "list_dir": lambda: f"List [cyan]{_short_path(p.get('path', '?'))}[/]",
        "tree": lambda: f"Tree [cyan]{_short_path(p.get('path', '?'))}[/]",
        "glob": lambda: f"Find [cyan]{p.get('pattern', '?')}[/]",
        "grep": lambda: f"Search [cyan]{p.get('pattern', '?')}[/]",
        "run_command": lambda: f"Run [cyan]{p.get('command', '?')[:50]}[/]",
        "web_search": lambda: f"Search web [cyan]{p.get('query', '?')[:40]}[/]",
        "web_fetch": lambda: f"Fetch [cyan]{p.get('url', '?')[:50]}[/]",
        "git_status": lambda: "Git status",
        "git_diff": lambda: f"Git diff {p.get('args', '')}".strip(),
        "git_commit": lambda: f"Git commit [cyan]{p.get('message', '?')[:40]}[/]",
        "memory_store": lambda: f"Remember [cyan]{p.get('content', '?')[:40]}[/]",
        "memory_search": lambda: f"Recall [cyan]{p.get('query', '?')}[/]",
        "read_image": lambda: f"View image [cyan]{_short_path(p.get('path', '?'))}[/]",
        "read_pdf": lambda: f"Read PDF [cyan]{_short_path(p.get('path', '?'))}[/]",
        "undo_edit": lambda: f"Undo [cyan]{_short_path(p.get('path', '?'))}[/]",
        "search_replace_all": lambda: f"Replace all in [cyan]{_short_path(p.get('path', '?'))}[/]",
        "ask_user": lambda: f"Ask user",
        "create_directory": lambda: f"Mkdir [cyan]{_short_path(p.get('path', '?'))}[/]",
        "move_file": lambda: f"Move [cyan]{_short_path(p.get('source', '?'))}[/]",
        "delete_file": lambda: f"Delete [cyan]{_short_path(p.get('path', '?'))}[/]",
        "multi_edit": lambda: f"Multi-edit [cyan]{_short_path(p.get('path', '?'))}[/] ({len(p.get('edits', []))} edits)",
        "clipboard_read": lambda: "Read clipboard",
        "clipboard_write": lambda: f"Copy to clipboard ({len(p.get('content', ''))} chars)",
        "diff_apply": lambda: f"Apply patch [cyan]{_short_path(p.get('path', '?'))}[/]",
    }
    fn = labels.get(name)
    if fn:
        try:
            return fn()
        except Exception:
            pass
    return name


def _short_path(path):
    """Shorten a path for display."""
    if not path or path == "?":
        return "?"
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    # Show only last 2 parts if too long
    if len(path) > 60:
        parts = path.split("/")
        if len(parts) > 3:
            path = ".../" + "/".join(parts[-2:])
    return path


def main():
    kodiqa = Kodiqa()
    kodiqa.run()


if __name__ == "__main__":
    main()
