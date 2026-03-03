#!/usr/bin/env python3
"""Kodiqa - Local AI coding agent. Claude native tools + Ollama text-based actions."""

import json
import os
import sys
import threading
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
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
import select
import signal
import subprocess
import time

from config import (
    OLLAMA_URL, OLLAMA_BIN, DEFAULT_MODEL, MODEL_ALIASES, CLAUDE_ALIASES,
    CLAUDE_API_URL, QWEN_ALIASES, QWEN_API_URL, CONTEXT_FILE, KODIQA_DIR,
    CONFIG_FILE, SYSTEM_PROMPT, SKIP_DIRS, SKIP_EXTENSIONS,
    MAX_FILE_SIZE, DEFAULTS, OPENAI_COMPAT_PROVIDERS,
    CHANGELOG, PERSONAS,
    load_settings, save_settings, load_config, save_default_config, load_kodiqaignore,
    is_claude_model, is_qwen_api_model, get_openai_provider, is_openai_compat_model,
)
from memory import MemoryStore
from actions import (
    parse_actions, execute_action, execute_tool_call, execute_tools_parallel, set_console,
    set_batch_mode, get_edit_queue, clear_edit_queue, apply_queued_edit, reject_queued_edit,
    do_undo_edit, _undo_buffer, set_hooks, set_sandbox,
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


class StreamStallIndicator:
    """Shows a subtle spinner when streaming stalls (no chunks for 2+ seconds)."""

    DOTS = ["\u28fe", "\u28fd", "\u28fb", "\u28f7", "\u28ef", "\u28df", "\u28bf", "\u287f"]

    def __init__(self, console, stall_seconds=2.0):
        self.console = console
        self.stall_seconds = stall_seconds
        self._last_ping = time.time()
        self._stop = threading.Event()
        self._showing = False
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def ping(self):
        """Call this each time a chunk arrives."""
        was_showing = self._showing
        self._last_ping = time.time()
        if was_showing:
            self._showing = False
            # Clear the stall indicator
            self.console.file.write("\r\033[K")
            self.console.file.flush()

    def stop(self):
        self._stop.set()
        if self._showing:
            self.console.file.write("\r\033[K")
            self.console.file.flush()
            self._showing = False
        self._thread.join(timeout=1)

    def _monitor(self):
        frame = 0
        while not self._stop.is_set():
            self._stop.wait(0.2)
            if self._stop.is_set():
                break
            elapsed = time.time() - self._last_ping
            if elapsed >= self.stall_seconds:
                if not self._showing:
                    self._showing = True
                dot = self.DOTS[frame % len(self.DOTS)]
                secs = int(elapsed)
                self.console.file.write(f"\r\033[K  \033[2m{dot} waiting for response... {secs}s\033[0m")
                self.console.file.flush()
                frame += 1


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


class KodiqaCompleter(Completer):
    """Tab completer for slash commands, model aliases, modes, and file paths."""

    def __init__(self, agent):
        self.agent = agent

    def _complete_path(self, text):
        """Yield path completions."""
        expanded = os.path.expanduser(text)
        if os.path.isdir(expanded) and not expanded.endswith("/"):
            expanded += "/"
        dirname = os.path.dirname(expanded) or "."
        basename = os.path.basename(expanded)
        try:
            entries = os.listdir(dirname)
        except OSError:
            return
        for entry in sorted(entries):
            if entry.startswith(".") and not basename.startswith("."):
                continue
            if entry.startswith(basename):
                full = os.path.join(dirname, entry)
                if text.startswith("~"):
                    result = "~" + full[len(os.path.expanduser("~")):]
                else:
                    result = full
                if os.path.isdir(full):
                    result += "/"
                yield Completion(result, start_position=-len(text))

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        try:
            if text.lstrip().startswith("/"):
                if " " not in text.strip():
                    # Complete slash command itself
                    for cmd in self.agent._SLASH_COMMANDS:
                        if cmd.startswith(text.lstrip()):
                            yield Completion(cmd, start_position=-len(text.lstrip()))
                else:
                    # Context-aware argument completion
                    cmd = text.strip().split()[0]
                    if cmd in ("/model",):
                        all_aliases = list(MODEL_ALIASES.keys()) + list(CLAUDE_ALIASES.keys())
                        for pv in OPENAI_COMPAT_PROVIDERS.values():
                            all_aliases.extend(pv["aliases"].keys())
                        for a in all_aliases:
                            if a.startswith(word):
                                yield Completion(a, start_position=-len(word))
                    elif cmd in ("/mode",):
                        for m in ("default", "relaxed", "auto"):
                            if m.startswith(word):
                                yield Completion(m, start_position=-len(word))
                    elif cmd in ("/search",):
                        for e in ("duckduckgo", "google", "api"):
                            if e.startswith(word):
                                yield Completion(e, start_position=-len(word))
                    elif cmd in ("/key",):
                        for name in ["claude"] + list(OPENAI_COMPAT_PROVIDERS.keys()):
                            if name.startswith(word):
                                yield Completion(name, start_position=-len(word))
                    elif cmd in ("/theme",):
                        from config import THEMES
                        for t in THEMES:
                            if t.startswith(word):
                                yield Completion(t, start_position=-len(word))
                    elif cmd in ("/init",):
                        try:
                            from templates import TEMPLATES
                            for t in TEMPLATES:
                                if t.startswith(word):
                                    yield Completion(t, start_position=-len(word))
                        except ImportError:
                            pass
                    elif cmd in ("/lsp",):
                        for sub in ("start", "stop", "status"):
                            if sub.startswith(word):
                                yield Completion(sub, start_position=-len(word))
                    elif cmd in ("/persona",):
                        for name in list(PERSONAS.keys()) + ["off"]:
                            if name.startswith(word):
                                yield Completion(name, start_position=-len(word))
                    elif cmd in ("/profile",):
                        for sub in ("save", "load", "list", "delete"):
                            if sub.startswith(word):
                                yield Completion(sub, start_position=-len(word))
                    elif cmd in ("/history",):
                        for sub in ("resume",):
                            if sub.startswith(word):
                                yield Completion(sub, start_position=-len(word))
                    elif cmd in ("/refactor",):
                        for sub in ("rename", "extract"):
                            if sub.startswith(word):
                                yield Completion(sub, start_position=-len(word))
                    elif cmd in ("/watch",):
                        for sub in ("stop", "list"):
                            if sub.startswith(word):
                                yield Completion(sub, start_position=-len(word))
                        yield from self._complete_path(word)
                    elif cmd in ("/cd", "/scan", "/pin", "/unpin", "/test", "/debug"):
                        yield from self._complete_path(word)
                    elif cmd in ("/restore",):
                        for n in self.agent._checkpoints.keys():
                            if n.startswith(word):
                                yield Completion(n, start_position=-len(word))
                    else:
                        yield from self._complete_path(word)
            else:
                # @file references
                if word.startswith("@"):
                    prefix = word[1:]  # strip @
                    for c in self._complete_path(prefix):
                        yield Completion("@" + c.text, start_position=-len(word))
                # File paths
                elif word.startswith(("/", "~", ".")) or "/" in word:
                    yield from self._complete_path(word)
        except Exception:
            return


class Kodiqa:
    def __init__(self):
        self.console = Console()
        set_console(self.console)  # share console with actions.py for diff display
        set_hooks(load_config().get("hooks", {}))
        self.memory = MemoryStore()
        self.history = []
        self.cwd = os.getcwd()
        self.settings = load_settings()
        self.config = load_config()
        save_default_config()
        _setup_error_log()
        self.claude_key = self.settings.get("claude_api_key", "")
        # Load all OpenAI-compatible provider API keys
        self.api_keys = {}
        for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
            self.api_keys[prov_name] = self.settings.get(prov["key_setting"], "")
        self.session_file = os.path.join(KODIQA_DIR, "session.json")
        self.multi_models = []  # default: single model mode
        self._auto_approved = set()  # action types auto-approved this session
        self.session_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost": 0.0}
        self._ollama_started_by_us = False  # track if we started Ollama
        # Setup prompt_toolkit for Claude Code-style UI
        self._history_file = os.path.join(KODIQA_DIR, "input_history")
        self._pt_style = PTStyle.from_dict({
            'prompt': '#af5fff bold',
            'bottom-toolbar': 'bg:default #999999 noreverse',
            'bottom-toolbar.text': 'bg:default #999999 noreverse',
            'separator': '#666666',
        })
        self._pt_session = PromptSession(
            history=FileHistory(self._history_file),
            completer=KodiqaCompleter(self),
            style=self._pt_style,
        )
        self.qwen_key = self.api_keys.get("qwen", "")  # backward compat alias
        # Restore Qwen region endpoint if saved
        qwen_region = self.settings.get("qwen_region")
        if qwen_region:
            from config import QWEN_URLS
            if qwen_region in QWEN_URLS:
                OPENAI_COMPAT_PROVIDERS["qwen"]["url"] = QWEN_URLS[qwen_region]
                OPENAI_COMPAT_PROVIDERS["qwen"]["models_url"] = QWEN_URLS[qwen_region].replace("/chat/completions", "/models")
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
        # Auto git commit after AI edits
        self.auto_commit = self.settings.get("auto_commit", False)
        # Budget limit (0 = no limit)
        self.budget_limit = 0
        self._budget_exceeded = False
        # Auto-lint command after edits
        self.lint_cmd = ""
        self.lint_auto_fix = False
        # Pinned files — always in context
        self._pinned_files = []
        # Desktop notifications for long tasks
        self._notify_enabled = False
        # Cost optimizer — suggest cheaper models
        self._optimizer_enabled = self.settings.get("optimizer", False)
        # Theme
        from config import THEMES
        self.theme = THEMES.get(self.settings.get("theme", "dark"), THEMES["dark"])
        # Plugins
        self._plugins = {}
        self._load_plugins()
        # Sub-agents
        self._agents = {}
        self._agent_counter = 0
        # Agent teams
        self._teams = {}
        self._team_counter = 0
        # LSP client
        self._lsp_client = None
        # v3.0 features
        self._pending_files = []
        self._pending_images = []
        self._persona = None
        self._session_stats = {
            "files_read": 0, "files_written": 0, "files_edited": 0,
            "commands_run": 0, "searches": 0, "messages_sent": 0,
            "tools_used": {},
            "start_time": time.time(),
        }
        self._watchers = {}
        self._ai_trigger_queue = []
        self.headless = False
        self.sandbox_enabled = False
        # Architect mode: strong model plans, cheap model implements
        self.architect_mode = False
        self._architect_model = None
        self._impl_model = None
        # Load .kodiqaignore
        self._load_kodiqaignore()

    # ── Tab Completion ──

    _SLASH_COMMANDS = [
        "/model", "/models", "/multi", "/single", "/scan", "/clear", "/compact",
        "/memories", "/forget", "/context", "/key", "/tokens", "/config",
        "/export", "/checkpoint", "/restore", "/env", "/verbose", "/mode",
        "/plan", "/accept", "/search", "/cd", "/branch", "/mcp",
        "/autocommit", "/budget", "/undo", "/diff", "/lint",
        "/pin", "/unpin", "/alias", "/unalias", "/notify", "/optimizer", "/theme",
        "/share", "/pr", "/review", "/issue", "/init", "/plugins",
        "/agent", "/agents", "/lsp", "/voice",
        "/changelog", "/stats", "/review-local", "/test", "/test-fix", "/persona", "/patch",
        "/profile", "/refactor", "/history", "/watch", "/embed", "/rag",
        "/debug", "/diagram",
        "/architect", "/sandbox", "/map", "/team", "/teams",
        "/help", "/quit",
    ]


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

    def _build_pinned_context(self):
        """Read all pinned files and format as context block."""
        if not self._pinned_files:
            return ""
        parts = ["## Pinned Files"]
        for path in self._pinned_files:
            try:
                with open(path, "r", errors="replace") as f:
                    content = f.read()
                if len(content) > 10000:
                    content = content[:10000] + "\n... (truncated)"
                rel = os.path.relpath(path, self.cwd) if path.startswith(self.cwd) else path
                parts.append(f"### {rel}\n```\n{content}\n```")
            except Exception:
                pass
        return "\n\n".join(parts) if len(parts) > 1 else ""

    def _send_notification(self, title, body):
        """Send desktop notification (macOS)."""
        try:
            script = f'display notification "{body}" with title "{title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        except Exception:
            pass

    def _check_cost_optimizer(self, user_msg):
        """Suggest cheaper model if message is simple and model is expensive."""
        if not self._optimizer_enabled:
            return
        expensive = {"opus", "opus-4.6", "opus-4.5", "opus-4.1", "opus-4", "gpt", "gpt4",
                      "mistral", "qwen-max", "claude", "sonnet"}
        model_alias = self.model.split("/")[-1].split(":")[0]
        # Check if using expensive model
        is_expensive = any(model_alias.startswith(e) or self.model in CLAUDE_ALIASES.get(e, "")
                          for e in expensive)
        if not is_expensive:
            return
        code_keywords = {"edit", "create", "fix", "refactor", "debug", "write", "implement",
                         "build", "add", "remove", "delete", "update", "modify", "change",
                         "move", "rename", "search", "find", "replace", "commit"}
        msg_lower = user_msg.lower()
        if len(user_msg) < 100 and not any(kw in msg_lower for kw in code_keywords):
            self.console.print(
                f"  [dim]Tip: Simple question? Try /model haiku for cheaper responses.[/]"
            )

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

    # ── @file references, image paste, auto-detection ──

    def _process_at_references(self, user_input):
        """Parse @file references and auto-detect image paths in user input."""
        import re
        files = []
        images = []
        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        # 1) Explicit @file references
        at_pattern = r'@([\w./~\-]+)'
        for match in re.finditer(at_pattern, user_input):
            ref = match.group(1)
            path = os.path.expanduser(ref)
            if not os.path.isabs(path):
                path = os.path.join(self.cwd, path)
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTS:
                img = self._read_image_for_embed(path)
                if img:
                    images.append(img)
                    self.console.print(f"  [dim]+ {os.path.basename(path)} (image, {os.path.getsize(path) // 1024}KB)[/]")
            else:
                fc = self._read_file_for_embed(path)
                if fc:
                    files.append(fc)
                    lines = fc["content"].count("\n") + 1
                    self.console.print(f"  [dim]+ {fc['rel_path']} ({lines} lines)[/]")
        # Remove @refs from text
        cleaned = re.sub(at_pattern, lambda m: m.group(1), user_input)

        # 2) Auto-detect bare image paths (~/path/img.png, /abs/img.jpg, ./rel/img.png)
        img_pattern = r'((?:~/|/|\./)[\w./\-]+\.(?:png|jpg|jpeg|gif|webp))'
        for match in re.finditer(img_pattern, cleaned):
            img_path = os.path.expanduser(match.group(1))
            if not os.path.isabs(img_path):
                img_path = os.path.join(self.cwd, img_path)
            img_path = os.path.abspath(img_path)
            # Skip if already captured by @ref
            if any(i["path"] == img_path for i in images):
                continue
            if os.path.isfile(img_path):
                img = self._read_image_for_embed(img_path)
                if img:
                    images.append(img)
                    self.console.print(f"  [dim]+ {os.path.basename(img_path)} (image, {os.path.getsize(img_path) // 1024}KB)[/]")

        return cleaned, files, images

    def _read_image_for_embed(self, path):
        """Read an image file as base64 for embedding in messages."""
        import base64
        MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                       ".gif": "image/gif", ".webp": "image/webp"}
        ext = os.path.splitext(path)[1].lower()
        media_type = MEDIA_TYPES.get(ext)
        if not media_type:
            return None
        size = os.path.getsize(path)
        if size > 5_000_000:
            self.console.print(f"  [yellow]Image too large ({size // 1_000_000}MB), skipping[/]")
            return None
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            return {"path": path, "media_type": media_type, "data": data}
        except Exception:
            return None

    def _read_file_for_embed(self, path):
        """Read a text file for embedding in messages."""
        try:
            with open(path, "r", errors="replace") as f:
                content = f.read()
            if len(content) > 10_000:
                content = content[:10_000] + "\n... (truncated to 10KB)"
            return {"path": path, "rel_path": os.path.relpath(path, self.cwd), "content": content}
        except Exception:
            return None

    def _paste_clipboard_image(self):
        """Try to read an image from the system clipboard."""
        import base64, subprocess, platform
        tmp = "/tmp/kodiqa_clipboard.png"
        try:
            if platform.system() == "Darwin":
                # Try pngpaste first (brew install pngpaste)
                r = subprocess.run(["pngpaste", tmp], capture_output=True, timeout=5)
                if r.returncode != 0:
                    # Fallback: osascript
                    script = '''
                    set theFile to (POSIX file "/tmp/kodiqa_clipboard.png")
                    try
                        set imgData to the clipboard as «class PNGf»
                        set fp to open for access theFile with write permission
                        write imgData to fp
                        close access fp
                    on error
                        return "no image"
                    end try
                    '''
                    r2 = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
                    if "no image" in (r2.stdout + r2.stderr):
                        return None
            else:
                # Linux: xclip
                r = subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                                   capture_output=True, timeout=5)
                if r.returncode == 0 and r.stdout:
                    with open(tmp, "wb") as f:
                        f.write(r.stdout)
                else:
                    return None
            if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                with open(tmp, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                os.remove(tmp)
                self.console.print(f"  [dim]+ clipboard image ({len(data) * 3 // 4 // 1024}KB)[/]")
                return {"path": "clipboard", "media_type": "image/png", "data": data}
        except Exception:
            pass
        return None

    def _append_files_to_text(self, text, files):
        """Append file contents to message text."""
        if not files:
            return text
        parts = [text, "\n\n--- Attached files ---"]
        for f in files:
            parts.append(f"\n### {f['rel_path']}\n```\n{f['content']}\n```")
        return "\n".join(parts)

    def run_headless(self, task, output_file=None):
        """Run a task non-interactively. No prompt, auto-approve everything."""
        self.headless = True
        self.permission_mode = "auto"
        self.batch_edits = False
        self._detect_git()
        self.console.print(f"[cyan]Headless mode[/] — model: {self.model}")
        self.console.print(f"[cyan]Task:[/] {task}")
        try:
            self._chat(task)
        except Exception as e:
            self.console.print(f"[red]Headless error: {e}[/]")
        finally:
            if output_file:
                try:
                    with open(output_file, 'w') as f:
                        f.write(f"# Kodiqa Headless Output\n\nModel: {self.model}\nTask: {task}\n\n")
                        for msg in self.history:
                            role = msg.get("role", "?")
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                f.write(f"## {role}\n{content}\n\n")
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        f.write(f"## {role}\n{block['text']}\n\n")
                    self.console.print(f"[green]Output saved to: {output_file}[/]")
                except Exception as e:
                    self.console.print(f"[red]Error writing output: {e}[/]")
            self._save_session()

    def run(self):
        self._first_run_setup()
        self._detect_git()
        self._load_session()
        self._welcome_shown = False
        self._check_updates()
        if not self._welcome_shown:
            self._welcome()
        try:
            while True:
                try:
                    w = os.get_terminal_size().columns
                    user_input = self._pt_session.prompt(
                        [('class:separator', '─' * w + '\n'), ('class:prompt', '❯ ')],
                        reserve_space_for_menu=0,
                        bottom_toolbar='\n\n',
                    )
                except (EOFError, KeyboardInterrupt):
                    self._quit()
                    return
                # Process queued AI triggers from file watchers
                if not user_input.strip() and self._ai_trigger_queue:
                    trigger = self._ai_trigger_queue.pop(0)
                    rel = os.path.relpath(trigger["file"], self.cwd)
                    self.console.print(f"  [magenta]⚡ Processing AI trigger:[/] {rel}:{trigger['line']}")
                    self._remove_ai_trigger(trigger["file"], trigger["line"])
                    self._chat(f"In file {rel}, execute this instruction: {trigger['instruction']}\nRead the file first to understand the context.")
                    continue
                if not user_input.strip():
                    continue
                if user_input.strip().lower() in ("quit", "exit"):
                    self._quit()
                    return
                elif user_input.strip().startswith("/"):
                    self._handle_slash(user_input.strip())
                else:
                    # Process @file references, auto-detect images, !img paste
                    user_input, attached_files, attached_images = self._process_at_references(user_input)
                    if "!img" in user_input:
                        img = self._paste_clipboard_image()
                        if img:
                            attached_images.append(img)
                        elif not attached_images:
                            self.console.print("[dim]No image found on clipboard.[/]")
                        user_input = user_input.replace("!img", "").strip()
                    self._pending_files = attached_files
                    self._pending_images = attached_images
                    if user_input.strip():
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
        if is_claude_model(self.model) or self._is_live_claude(self.model):
            provider = "[yellow]Claude API[/]"
        else:
            prov_name = self._get_provider_for_model(self.model)
            if prov_name:
                prov = OPENAI_COMPAT_PROVIDERS[prov_name]
                provider = f"[{prov['color']}]{prov['label']} API[/]"
            else:
                # Check if the local model actually exists
                local_ok = False
                try:
                    resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
                    installed = [m["name"] for m in resp.json().get("models", [])]
                    local_ok = any(m.startswith(self.model.split(":")[0]) for m in installed)
                except Exception:
                    pass
                if local_ok:
                    provider = "[green]Local/Ollama[/]"
                else:
                    provider = "[red]not installed[/]"
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
        # Guide user if model isn't available
        if provider == "[red]not installed[/]":
            has_any_key = self.claude_key or any(self.api_keys.get(p, "") for p in OPENAI_COMPAT_PROVIDERS)
            if has_any_key:
                self.console.print("[yellow]Local model not found.[/] Use [bold]/model[/] to pick a cloud model.")
            else:
                self.console.print(
                    "[yellow]No local models found.[/] Either:\n"
                    "  • Pull a model: [bold]ollama pull qwen3-coder[/]\n"
                    "  • Add an API key: [bold]/key[/]"
                )

    def _quit(self):
        self._save_session()
        self._save_session_summary()
        self._save_session_to_history()
        # Stop watchers
        for w in self._watchers.values():
            w["active"] = False
        self._watchers.clear()
        self.memory.close()
        self.mcp.stop_all()
        self._stop_ollama()
        self.console.print("[dim]Goodbye! Session saved.[/]")

    def _track_tool(self, tool_name):
        """Track tool usage in session stats."""
        s = self._session_stats
        s["tools_used"][tool_name] = s["tools_used"].get(tool_name, 0) + 1
        if tool_name == "read_file":
            s["files_read"] += 1
        elif tool_name in ("write_file", "edit_file", "multi_edit", "search_replace_all", "diff_apply"):
            s["files_edited"] += 1
        elif tool_name == "run_command":
            s["commands_run"] += 1
        elif tool_name in ("web_search", "grep", "glob"):
            s["searches"] += 1

    def _save_session_to_history(self):
        """Save current session to history index on quit."""
        user_msgs = [m for m in self.history if m.get("role") == "user"]
        if len(user_msgs) < 2:
            return
        try:
            import datetime
            history_dir = os.path.join(KODIQA_DIR, "history")
            os.makedirs(history_dir, exist_ok=True)
            first_user = next(
                (m["content"] for m in self.history
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                "",
            )
            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "model": self.model,
                "cwd": self.cwd,
                "messages": len(self.history),
                "user_messages": len(user_msgs),
                "cost": self.session_tokens.get("cost", 0),
                "tools_used": sum(self._session_stats.get("tools_used", {}).values()),
                "topic": first_user[:100],
            }
            index_file = os.path.join(history_dir, "index.json")
            index = []
            if os.path.isfile(index_file):
                try:
                    with open(index_file, "r") as f:
                        index = json.load(f)
                except Exception:
                    index = []
            entry["id"] = len(index) + 1
            index.append(entry)
            if len(index) > 100:
                index = index[-100:]
            with open(index_file, "w") as f:
                json.dump(index, f, indent=2)
            # Save full session
            saveable = [m for m in self.history if isinstance(m.get("content"), str)]
            session_file = os.path.join(history_dir, f"session_{entry['id']}.json")
            with open(session_file, "w") as f:
                json.dump({"model": self.model, "cwd": self.cwd, "history": saveable}, f)
        except Exception:
            pass

    def _save_session_summary(self):
        """Auto-save conversation summary to project context file on quit."""
        # Only save if there was meaningful conversation (at least 2 exchanges)
        user_msgs = [m for m in self.history if m.get("role") == "user"]
        if len(user_msgs) < 2:
            return
        try:
            # Generate summary using current model
            summary_prompt = (
                "Write a brief session summary (5-10 lines max) of what was discussed and done. "
                "Include: key decisions, files changed, problems solved, and any pending tasks. "
                "Format as bullet points. Start with '## Last Session' header."
            )
            if is_claude_model(self.model):
                summary = self._claude_nostream(summary_prompt, self.history)
            elif self._get_provider_for_model(self.model):
                summary = self._openai_compat_nostream(summary_prompt, self.history)
            else:
                msgs = [{"role": "system", "content": summary_prompt}] + self.history
                msgs.append({"role": "user", "content": summary_prompt})
                resp = requests.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": self.model, "messages": msgs, "stream": False},
                    timeout=60,
                )
                resp.raise_for_status()
                summary = resp.json().get("message", {}).get("content", "")
            if not summary or not summary.strip():
                return
            # Save to project context file
            ctx_path = self._get_project_context_path()
            os.makedirs(os.path.dirname(ctx_path), exist_ok=True)
            # Read existing content (preserve manual notes)
            existing = ""
            if os.path.isfile(ctx_path):
                with open(ctx_path, "r") as f:
                    existing = f.read()
            # Replace old "Last Session" section if present, or append
            import re
            if "## Last Session" in existing:
                existing = re.sub(
                    r"## Last Session.*?(?=\n## |\Z)",
                    "", existing, flags=re.DOTALL
                ).strip()
            with open(ctx_path, "w") as f:
                if existing:
                    f.write(existing + "\n\n")
                f.write(summary.strip() + "\n")
            self.console.print(f"[dim]Session summary saved to {ctx_path}[/]")
        except Exception:
            pass  # Don't block quit on summary errors

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
                    self._ollama_started_by_us = True
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        self.console.print("[yellow]●[/] Could not start Ollama [dim](start manually: ollama serve)[/]")
        return False

    def _fetch_ollama_library(self, installed):
        """Fetch available models from ollama.com/library, filter out already installed."""
        import re
        from bs4 import BeautifulSoup
        try:
            with Status("[dim]Fetching available models from ollama.com...[/]", console=self.console, spinner="dots"):
                resp = requests.get("https://ollama.com/library", timeout=10)
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return []

        models = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("/library/"):
                continue
            name = href.replace("/library/", "")
            if "/" in name or not name:
                continue
            # Skip embedding models — not useful for chat
            text = a.get_text(" ", strip=True).lower()
            if "embed" in name or "embedding" in text:
                continue
            # Description
            p = a.find("p")
            desc = p.get_text(strip=True) if p else ""
            # Pull count
            pulls_match = re.search(r"([\d.]+[KMB]?)\s*Pulls", a.get_text(" ", strip=True))
            pulls = pulls_match.group(1) if pulls_match else ""
            # Skip if already installed
            already_have = any(
                inst.startswith(name.split(":")[0]) for inst in installed.keys()
            )
            if not already_have:
                models.append((name, desc, pulls))

        # Return top 20 by popularity (page is already sorted by pulls)
        return models[:20]

    def _stop_ollama(self):
        """Stop Ollama if we started it."""
        if not self._ollama_started_by_us:
            return
        import subprocess
        try:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=5)
            self.console.print("[green]●[/] Ollama stopped")
            self._ollama_started_by_us = False
        except Exception:
            pass

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

        # 1. Check installed models for updates
        if not installed:
            self.console.print("\n[yellow]No local models installed.[/]")
        else:
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

        # Show welcome before new models list
        self._welcome()
        self._welcome_shown = True

        # 2. Fetch available models from Ollama library
        new_models = self._fetch_ollama_library(installed)
        if not new_models:
            return

        # Show new models available
        self.console.print(f"\n[bold yellow]New models available ({len(new_models)}):[/]")
        for i, (model, desc, pulls) in enumerate(new_models, 1):
            pulls_str = f" [dim]({pulls} pulls)[/]" if pulls else ""
            self.console.print(f"  [cyan bold]{i}.[/] [cyan]{model}[/] — {desc[:70]}{pulls_str}")

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
            to_pull = [m for m, _, _ in new_models]
        else:
            # Parse numbers or model names
            parts = answer.replace(",", " ").split()
            for part in parts:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(new_models):
                        to_pull.append(new_models[idx][0])
                except ValueError:
                    # Maybe they typed a model name (require 3+ chars to avoid accidental matches)
                    if len(part) >= 3:
                        for model, _, _ in new_models:
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

        self.console.print(f"\n[green]Models pulled! Use /multi all for multi-model mode.[/]")
        # Auto-set model if current one wasn't installed
        if not installed and to_pull:
            self.model = to_pull[0]
            self.console.print(f"Model set to [cyan]{self.model}[/]")

    def _handle_slash(self, cmd):
        parts = cmd.split(None, 1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/quit", "/exit"):
            self._quit()
            sys.exit(0)
        elif command == "/help":
            claude_status = "[green]connected[/]" if self.claude_key else "[dim]not set[/]"
            provider_lines = [f"  [dim]Claude: claude, sonnet, haiku, opus ({claude_status})[/]"]
            for pn, pv in OPENAI_COMPAT_PROVIDERS.items():
                k = self.api_keys.get(pn, "")
                st = "[green]connected[/]" if k else "[dim]not set[/]"
                aliases = ", ".join(list(pv["aliases"].keys())[:4])
                provider_lines.append(f"  [dim]{pv['label']}: {aliases} ({st})[/]")
            self.console.print(Panel(
                "[bold]/model <name>[/]  - Switch model\n"
                "  [dim]Local: fast, qwen, coder, reason, gpt-local[/]\n"
                + "\n".join(provider_lines) + "\n"
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
                "[bold]/pin[/] <path>     - Pin file to always include in context\n"
                "[bold]/unpin[/] <path>   - Remove pinned file\n"
                "[bold]/alias[/]         - Create command alias\n"
                "[bold]/theme[/] <name>   - Switch theme (dark/light/dracula/monokai/nord)\n"
                "[bold]/pr[/] [title]     - Create GitHub PR\n"
                "[bold]/review[/] [n]     - Review PR diff\n"
                "[bold]/agent[/] <task>   - Spawn sub-agent\n"
                "[bold]/lsp[/]           - Language Server Protocol\n"
                "[bold]/changelog[/]     - Show version history\n"
                "[bold]/stats[/]         - Session metrics\n"
                "[bold]/review-local[/]  - AI review of staged git changes\n"
                "[bold]/test[/] <file>    - Generate unit tests\n"
                "[bold]/persona[/] <name> - Switch AI persona\n"
                "[bold]/patch[/]         - Apply diff from clipboard\n"
                "[bold]/profile[/]       - Save/load config profiles\n"
                "[bold]/refactor[/]      - Multi-file refactoring\n"
                "[bold]/history[/]       - Browse past sessions\n"
                "[bold]/watch[/] <path>   - Watch files for changes\n"
                "[bold]/embed[/] [path]   - Index files for RAG search\n"
                "[bold]/rag[/] <query>    - RAG search + AI answer\n"
                "[bold]/debug[/] <script> - Run and debug script\n"
                "[bold]/diagram[/] <desc> - Generate Mermaid diagram\n"
                "[bold]/quit[/]          - Exit",
                title="Commands", border_style="blue",
            ))
        elif command == "/model":
            if not arg:
                _prov = self._get_provider_for_model(self.model)
                provider = OPENAI_COMPAT_PROVIDERS[_prov]["label"] + " API" if _prov else ("Claude API" if is_claude_model(self.model) else "Local/Ollama")
                self.console.print(f"Current model: [cyan]{self.model}[/] ({provider})\n")
                # Build numbered list of all available models
                choices = []
                self.console.print("[bold green]Local (Ollama):[/]")
                for alias, full in MODEL_ALIASES.items():
                    choices.append((alias, full, "local"))
                    marker = " [cyan]◀[/]" if full == self.model else ""
                    self.console.print(f"  {len(choices)}. {alias} [dim]→ {full}[/]{marker}")
                extras = self._get_api_model_choices()
                if self.claude_key:
                    self.console.print("[bold yellow]Claude API:[/]")
                    for alias, full in CLAUDE_ALIASES.items():
                        choices.append((alias, full, "claude"))
                        marker = " [cyan]◀[/]" if full == self.model else ""
                        self.console.print(f"  {len(choices)}. {alias} [dim]→ {full}[/]{marker}")
                    for m in extras.get("claude", []):
                        choices.append((m, m, "claude"))
                        marker = " [cyan]◀[/]" if m == self.model else ""
                        self.console.print(f"  {len(choices)}. [dim]{m}[/] [dim](live)[/]{marker}")
                for pn, pv in OPENAI_COMPAT_PROVIDERS.items():
                    if not self.api_keys.get(pn, ""):
                        continue
                    self.console.print(f"[bold {pv['color']}]{pv['label']} API:[/]")
                    for alias, full in pv["aliases"].items():
                        choices.append((alias, full, pn))
                        marker = " [cyan]◀[/]" if full == self.model else ""
                        self.console.print(f"  {len(choices)}. {alias} [dim]→ {full}[/]{marker}")
                    for m in extras.get(pn, []):
                        choices.append((m, m, pn))
                        marker = " [cyan]◀[/]" if m == self.model else ""
                        self.console.print(f"  {len(choices)}. [dim]{m}[/] [dim](live)[/]{marker}")
                self.console.print()
                try:
                    pick = Prompt.ask("[bold]Pick a model[/] (number or name, or 'skip')")
                except (EOFError, KeyboardInterrupt):
                    self.console.print("\n[dim]Cancelled.[/]")
                    return
                pick = pick.strip()
                if pick.lower() in ("skip", ""):
                    return
                if pick.isdigit() and 1 <= int(pick) <= len(choices):
                    alias, full, prov = choices[int(pick) - 1]
                    self.model = full
                    self.multi_models = []
                    if prov == "claude":
                        self._stop_ollama()
                        prov_str = "[yellow]Claude API[/]"
                    elif prov == "local":
                        prov_str = "[green]Local[/]"
                        self._ensure_ollama()
                    else:
                        self._stop_ollama()
                        prov_str = f"[{OPENAI_COMPAT_PROVIDERS[prov]['color']}]{OPENAI_COMPAT_PROVIDERS[prov]['label']} API[/]"
                    self.console.print(f"Switched to [cyan]{self.model}[/] ({prov_str}) [dim](single mode)[/]")
                    self.console.print("[dim]Use /multi all to go back to multi-model mode[/]")
                    return
                else:
                    arg = pick
            # Resolve alias to model name
            new_model = arg
            resolved_prov = None
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
                resolved_prov = "claude"
            else:
                # Check all OpenAI-compat providers
                for pn, pv in OPENAI_COMPAT_PROVIDERS.items():
                    if arg in pv["aliases"]:
                        if not self.api_keys.get(pn, ""):
                            self.console.print(f"[yellow]No {pv['label']} API key set.[/]")
                            key = Prompt.ask(f"[bold]Enter your {pv['label']} API key[/] (or 'skip')")
                            if key.strip().lower() == "skip" or not key.strip():
                                self.console.print("[dim]Cancelled.[/]")
                                return
                            self.api_keys[pn] = key.strip()
                            self.settings[pv["key_setting"]] = key.strip()
                            if pn == "qwen":
                                self.qwen_key = key.strip()
                            save_settings(self.settings)
                            self.console.print(f"[green]{pv['label']} API key saved![/]")
                        new_model = pv["aliases"][arg]
                        resolved_prov = pn
                        break
                if not resolved_prov:
                    if arg in MODEL_ALIASES:
                        new_model = MODEL_ALIASES[arg]
            self.model = new_model
            self.multi_models = []
            # Determine provider for display
            if resolved_prov == "claude" or is_claude_model(self.model) or self._is_live_claude(self.model):
                self._stop_ollama()
                provider = "[yellow]Claude API[/]"
            else:
                _pn = self._get_provider_for_model(self.model)
                if _pn:
                    self._stop_ollama()
                    provider = f"[{OPENAI_COMPAT_PROVIDERS[_pn]['color']}]{OPENAI_COMPAT_PROVIDERS[_pn]['label']} API[/]"
                else:
                    provider = "[green]Local[/]"
                    self._ensure_ollama()
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
                if len(resolved) < 2:
                    self.console.print("[yellow]Multi-model needs at least 2 models. Only 1 installed.[/]")
                    self.console.print(f"  • Pull more models or use [bold]/model {resolved[0]}[/] for single mode")
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
                    elif get_openai_provider(name):
                        _pn = get_openai_provider(name)
                        _pv = OPENAI_COMPAT_PROVIDERS[_pn]
                        if not self.api_keys.get(_pn, ""):
                            self.console.print(f"[red]{name} needs {_pv['label']} API key. Use /key {_pn} to add one.[/]")
                            return
                        resolved.append(_pv["aliases"][name])
                    elif name in MODEL_ALIASES:
                        resolved.append(MODEL_ALIASES[name])
                    else:
                        resolved.append(name)
            if len(resolved) < 2:
                self.console.print("[yellow]Multi-model needs at least 2 models.[/]")
                return
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
                self._load_kodiqaignore()
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
                set_hooks(self.config.get("hooks", {}))
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
        elif command == "/architect":
            self._handle_architect(arg)
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
        elif command == "/autocommit":
            self.auto_commit = not self.auto_commit
            self.settings["auto_commit"] = self.auto_commit
            save_settings(self.settings)
            if self.auto_commit:
                self.console.print("[green]Auto-commit ON[/] — git commit after each AI edit")
            else:
                self.console.print("[yellow]Auto-commit OFF[/]")
        elif command == "/budget":
            if arg:
                try:
                    self.budget_limit = float(arg)
                    self._budget_exceeded = False
                    self.console.print(f"[green]Budget set to ${self.budget_limit:.2f}[/]")
                except ValueError:
                    self.console.print("[red]Usage: /budget <amount> (e.g. /budget 5)[/]")
            else:
                spent = self.session_tokens["cost"]
                if self.budget_limit > 0:
                    pct = (spent / self.budget_limit) * 100
                    bar_w = 20
                    filled = int(bar_w * min(pct, 100) / 100)
                    bar = "█" * filled + "░" * (bar_w - filled)
                    color = "green" if pct < 80 else ("yellow" if pct < 100 else "red")
                    self.console.print(f"Budget: [{color}]{bar}[/] ${spent:.4f} / ${self.budget_limit:.2f} ({pct:.0f}%)")
                else:
                    self.console.print(f"Session cost: ${spent:.4f} [dim](no budget set — /budget <amount>)[/]")
        elif command == "/undo":
            if arg:
                path = os.path.abspath(os.path.expanduser(arg))
                result = do_undo_edit(path)
                self.console.print(result)
            else:
                files_with_undo = [(p, len(buf)) for p, buf in _undo_buffer.items() if buf]
                if files_with_undo:
                    self.console.print("[bold]Files with undo history:[/]")
                    for p, count in files_with_undo:
                        rel = os.path.relpath(p, self.cwd) if p.startswith(self.cwd) else p
                        self.console.print(f"  [cyan]{rel}[/] [dim]({count} undo steps)[/]")
                    self.console.print("[dim]Usage: /undo <path>[/]")
                else:
                    self.console.print("[dim]No undo history yet.[/]")
        elif command == "/diff":
            try:
                cmd = ["git", "diff"] + (arg.split() if arg else [])
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                output = result.stdout.strip()
                if output:
                    self.console.print(Panel(output, title="git diff", border_style="cyan"))
                else:
                    self.console.print("[dim]No uncommitted changes.[/]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")
        elif command == "/lint":
            if not arg:
                if self.lint_cmd:
                    auto = " [cyan](auto-fix ON)[/]" if self.lint_auto_fix else ""
                    self.console.print(f"Lint command: [cyan]{self.lint_cmd}[/]{auto}")
                else:
                    self.console.print("[dim]No lint command set. Usage: /lint <command>[/]")
                self.console.print("[dim]  /lint auto — toggle auto-fix (AI fixes lint errors automatically)[/]")
            elif arg.strip().lower() == "off":
                self.lint_cmd = ""
                self.lint_auto_fix = False
                self.console.print("[yellow]Auto-lint OFF[/]")
            elif arg.strip().lower() == "auto":
                self.lint_auto_fix = not self.lint_auto_fix
                state = "ON" if self.lint_auto_fix else "OFF"
                color = "green" if self.lint_auto_fix else "yellow"
                self.console.print(f"[{color}]Auto lint-fix {state}[/] (max 3 iterations)")
            else:
                self.lint_cmd = arg.strip()
                self.console.print(f"[green]Auto-lint ON[/] — running [cyan]{self.lint_cmd}[/] after edits")
        elif command == "/sandbox":
            if arg and arg.strip().lower() == "on":
                self.sandbox_enabled = True
                from actions import set_sandbox
                set_sandbox(True)
                self.console.print("[green]Sandbox ON[/] — commands restricted to cwd + /tmp")
            elif arg and arg.strip().lower() == "off":
                self.sandbox_enabled = False
                from actions import set_sandbox
                set_sandbox(False)
                self.console.print("[yellow]Sandbox OFF[/]")
            else:
                state = "[green]ON[/]" if self.sandbox_enabled else "[dim]OFF[/]"
                self.console.print(f"Sandbox: {state}")
                self.console.print("[dim]  /sandbox on | /sandbox off[/]")
        elif command == "/pin":
            if not arg:
                if self._pinned_files:
                    self.console.print("[bold]Pinned files:[/]")
                    for p in self._pinned_files:
                        rel = os.path.relpath(p, self.cwd) if p.startswith(self.cwd) else p
                        try:
                            size = os.path.getsize(p)
                            self.console.print(f"  [cyan]{rel}[/] [dim]({size:,} bytes)[/]")
                        except OSError:
                            self.console.print(f"  [cyan]{rel}[/] [dim](missing)[/]")
                else:
                    self.console.print("[dim]No pinned files. Usage: /pin <path>[/]")
            else:
                path = os.path.abspath(os.path.expanduser(arg.strip()))
                if not os.path.isfile(path):
                    self.console.print(f"[red]File not found: {arg}[/]")
                elif path in self._pinned_files:
                    self.console.print(f"[dim]Already pinned: {arg}[/]")
                else:
                    self._pinned_files.append(path)
                    rel = os.path.relpath(path, self.cwd) if path.startswith(self.cwd) else path
                    self.console.print(f"[green]Pinned:[/] {rel}")
        elif command == "/unpin":
            if not arg:
                self.console.print("[dim]Usage: /unpin <path>[/]")
            else:
                path = os.path.abspath(os.path.expanduser(arg.strip()))
                if path in self._pinned_files:
                    self._pinned_files.remove(path)
                    rel = os.path.relpath(path, self.cwd) if path.startswith(self.cwd) else path
                    self.console.print(f"[yellow]Unpinned:[/] {rel}")
                else:
                    self.console.print(f"[dim]Not pinned: {arg}[/]")
        elif command == "/alias":
            aliases = self.settings.get("aliases", {})
            if not arg:
                if aliases:
                    self.console.print("[bold]Command aliases:[/]")
                    for short, full in sorted(aliases.items()):
                        self.console.print(f"  [cyan]/{short}[/] → [dim]/{full}[/]")
                else:
                    self.console.print("[dim]No aliases. Usage: /alias <short> <command>[/]")
            else:
                parts2 = arg.split(None, 1)
                if len(parts2) < 2:
                    self.console.print("[dim]Usage: /alias <short> <command>[/]")
                else:
                    short, full = parts2[0].lstrip("/"), parts2[1].lstrip("/")
                    aliases[short] = full
                    self.settings["aliases"] = aliases
                    save_settings(self.settings)
                    self.console.print(f"[green]Alias set:[/] /{short} → /{full}")
        elif command == "/unalias":
            if not arg:
                self.console.print("[dim]Usage: /unalias <name>[/]")
            else:
                name = arg.strip().lstrip("/")
                aliases = self.settings.get("aliases", {})
                if name in aliases:
                    del aliases[name]
                    self.settings["aliases"] = aliases
                    save_settings(self.settings)
                    self.console.print(f"[yellow]Removed alias:[/] /{name}")
                else:
                    self.console.print(f"[dim]No alias: /{name}[/]")
        elif command == "/notify":
            self._notify_enabled = not self._notify_enabled
            state = "ON" if self._notify_enabled else "OFF"
            self.console.print(f"[{'green' if self._notify_enabled else 'yellow'}]Desktop notifications {state}[/]")
        elif command == "/optimizer":
            self._optimizer_enabled = not self._optimizer_enabled
            self.settings["optimizer"] = self._optimizer_enabled
            save_settings(self.settings)
            state = "ON" if self._optimizer_enabled else "OFF"
            self.console.print(f"[{'green' if self._optimizer_enabled else 'yellow'}]Cost optimizer {state}[/]")
        elif command == "/theme":
            from config import THEMES
            if not arg:
                self.console.print("[bold]Available themes:[/]")
                current = self.settings.get("theme", "dark")
                for name in THEMES:
                    marker = " ← current" if name == current else ""
                    self.console.print(f"  [cyan]{name}[/]{' [dim]' + marker + '[/]' if marker else ''}")
            else:
                name = arg.strip().lower()
                if name in THEMES:
                    self.theme = THEMES[name]
                    self.settings["theme"] = name
                    save_settings(self.settings)
                    self.console.print(f"[green]Theme set:[/] {name}")
                else:
                    self.console.print(f"[red]Unknown theme: {name}. Use /theme to list.[/]")
        elif command == "/share":
            self._share_session_html()
        elif command == "/pr":
            self._handle_gh("pr", arg)
        elif command == "/review":
            self._handle_gh("review", arg)
        elif command == "/issue":
            self._handle_gh("issue", arg)
        elif command == "/init":
            self._handle_init(arg)
        elif command == "/plugins":
            self._handle_plugins(arg)
        elif command == "/agent":
            self._handle_agent(arg)
        elif command == "/agents":
            self._handle_agents()
        elif command == "/lsp":
            self._handle_lsp(arg)
        elif command == "/voice":
            self._handle_voice(arg)
        elif command == "/changelog":
            version_filter = arg.strip()
            for entry in CHANGELOG:
                if version_filter and version_filter not in entry["version"]:
                    continue
                lines = [f"[bold cyan]{entry['version']}[/] [dim]({entry['date']})[/]"]
                for c in entry["changes"]:
                    lines.append(f"  [green]\u2022[/] {c}")
                self.console.print(Panel("\n".join(lines), border_style="blue"))
        elif command == "/stats":
            s = self._session_stats
            elapsed = time.time() - s["start_time"]
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            total_tools = sum(s["tools_used"].values())
            top_tools = sorted(s["tools_used"].items(), key=lambda x: -x[1])[:5]
            lines = [
                f"Session time:   {mins}m {secs}s",
                f"Messages sent:  {s['messages_sent']}",
                f"Tools used:     {total_tools}",
                f"Files read:     {s['files_read']}",
                f"Files edited:   {s['files_edited']}",
                f"Commands run:   {s['commands_run']}",
                f"Searches:       {s['searches']}",
                f"Cost:           ${self.session_tokens.get('cost', 0):.4f}",
            ]
            if top_tools:
                lines.append("")
                lines.append("[bold]Top tools:[/]")
                for name, count in top_tools:
                    lines.append(f"  {name}: {count}")
            self.console.print(Panel("\n".join(lines), title="Session Stats", border_style="blue"))
        elif command == "/review-local":
            try:
                result = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True, timeout=10, cwd=self.cwd)
                diff = result.stdout.strip()
                label = "staged"
                if not diff:
                    result = subprocess.run(["git", "diff"], capture_output=True, text=True, timeout=10, cwd=self.cwd)
                    diff = result.stdout.strip()
                    label = "unstaged"
                if not diff:
                    self.console.print("[dim]No changes to review.[/]")
                    return
                self.console.print(f"[cyan]Reviewing {label} changes...[/]")
                self._chat(
                    f"Review this git diff for bugs, style issues, security concerns, and improvements. "
                    f"Be concise but thorough. Group feedback by file.\n\n```diff\n{diff[:15000]}\n```"
                )
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")
        elif command == "/test":
            if not arg:
                self.console.print("[dim]Usage: /test <file_path>[/]")
                return
            path = os.path.abspath(os.path.expanduser(arg.strip()))
            if not os.path.isfile(path):
                self.console.print(f"[red]File not found: {arg}[/]")
                return
            ext = os.path.splitext(path)[1]
            frameworks = {".py": "pytest", ".ts": "jest", ".tsx": "jest", ".js": "jest", ".go": "go test"}
            fw = frameworks.get(ext, "appropriate test framework")
            rel = os.path.relpath(path, self.cwd)
            self._chat(
                f"Read {rel} and generate comprehensive unit tests for it using {fw}. "
                f"Create a test file in the tests/ directory (or adjacent __tests__/ for JS/TS). "
                f"Cover all public functions/methods, edge cases, and error paths. "
                f"Follow existing test patterns if any tests exist in this project."
            )
        elif command == "/test-fix":
            self._handle_test_fix(arg)
        elif command == "/persona":
            if not arg:
                if self._persona:
                    self.console.print(f"Current persona: [cyan]{self._persona}[/]")
                self.console.print("[bold]Available personas:[/]")
                for name, p in PERSONAS.items():
                    marker = " [dim]<- active[/]" if name == self._persona else ""
                    self.console.print(f"  [cyan]{name}[/] \u2014 {p['name']}{marker}")
                self.console.print(f"  [cyan]off[/] \u2014 reset to default")
            elif arg.strip().lower() == "off":
                self._persona = None
                self.console.print("[yellow]Persona reset to default.[/]")
            elif arg.strip() in PERSONAS:
                self._persona = arg.strip()
                self.console.print(f"[green]Persona:[/] {PERSONAS[self._persona]['name']}")
            else:
                self.console.print(f"[red]Unknown persona: {arg}. Use /persona to list.[/]")
        elif command == "/patch":
            try:
                result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
                clip = result.stdout
            except Exception:
                self.console.print("[red]Clipboard not available.[/]")
                return
            if not clip.strip():
                self.console.print("[dim]Clipboard is empty.[/]")
                return
            if not any(clip.lstrip().startswith(p) for p in ("diff ", "--- ", "@@", "Index:")):
                self.console.print("[yellow]Clipboard doesn't look like a diff/patch.[/]")
                self.console.print("[dim]Expected unified diff format (from git diff, etc.)[/]")
                return
            self._chat(
                f"Apply this patch to the appropriate file(s). Read the target files first, "
                f"then apply the changes using edit_file or diff_apply.\n\n```diff\n{clip[:15000]}\n```"
            )
        elif command == "/profile":
            self._handle_profile(arg)
        elif command == "/refactor":
            if not arg:
                self.console.print("[dim]Usage:[/]")
                self.console.print("  [cyan]/refactor rename <old> <new>[/] \u2014 rename symbol across files")
                self.console.print("  [cyan]/refactor extract <description>[/] \u2014 extract code to function/file")
                self.console.print("  [cyan]/refactor <description>[/] \u2014 general refactoring")
                return
            parts = arg.split(None, 2)
            sub = parts[0]
            if sub == "rename" and len(parts) >= 3:
                old_name, new_name = parts[1], parts[2]
                self._chat(
                    f"Refactor: rename '{old_name}' to '{new_name}' across the entire project.\n"
                    f"1. Use grep to find all occurrences of '{old_name}' in {self.cwd}\n"
                    f"2. Read each file that contains it\n"
                    f"3. Use edit_file or search_replace_all to rename (be careful about partial matches)\n"
                    f"4. Update imports, comments, and string references\n"
                    f"5. Show a summary of all changes"
                )
            elif sub == "extract":
                desc = " ".join(parts[1:]) if len(parts) > 1 else ""
                self._chat(f"Refactor: extract code \u2014 {desc}. Read the relevant files, identify the code to extract, create the new function/module, and update all call sites.")
            else:
                self._chat(f"Refactor this codebase: {arg}. Use grep to find relevant files, read them, and apply the refactoring using edit_file or multi_edit.")
        elif command == "/history":
            self._handle_history(arg)
        elif command == "/watch":
            self._handle_watch(arg)
        elif command == "/embed":
            self._handle_embed(arg)
        elif command == "/rag":
            if not arg:
                self.console.print("[dim]Usage: /rag <question> \u2014 search codebase with embeddings + AI[/]")
                return
            self._handle_rag(arg)
        elif command == "/debug":
            if not arg:
                self.console.print("[dim]Usage: /debug <script> [args] \u2014 run script, catch errors, debug with AI[/]")
                return
            self._handle_debug(arg)
        elif command == "/diagram":
            if not arg:
                self.console.print("[dim]Usage: /diagram <description>[/]")
                self.console.print("[dim]Examples:[/]")
                self.console.print("  [cyan]/diagram class hierarchy for this project[/]")
                self.console.print("  [cyan]/diagram sequence diagram for login flow[/]")
                return
            self._chat(
                f"Generate a Mermaid diagram for: {arg}\n\n"
                f"Requirements:\n"
                f"- Output a single ```mermaid code block\n"
                f"- Use appropriate diagram type (flowchart, sequence, class, ER, etc.)\n"
                f"- Keep it clear and readable\n"
                f"- If describing this project's code, read the relevant files first"
            )
        elif command == "/map":
            self._handle_map(arg)
        elif command == "/team":
            self._handle_team(arg)
        elif command == "/teams":
            self._handle_teams()
        else:
            # Check user-defined aliases before giving up
            aliases = self.settings.get("aliases", {})
            cmd_name = command.lstrip("/")
            if cmd_name in aliases:
                expanded = "/" + aliases[cmd_name]
                if arg:
                    expanded += " " + arg
                self._handle_slash(expanded)
            else:
                self.console.print(f"[red]Unknown command: {command}. Type /help[/]")

    def _setup_api_key(self, provider=None):
        if provider == "claude":
            self._setup_claude_key()
            return
        if provider in OPENAI_COMPAT_PROVIDERS:
            self._setup_provider_key(provider)
            return
        # No provider specified — show all
        providers = [("claude", "Claude API", self.claude_key)]
        for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
            providers.append((prov_name, prov["label"] + " API", self.api_keys.get(prov_name, "")))
        for i, (name, label, key) in enumerate(providers, 1):
            status = "[green]set[/]" if key else "[dim]not set[/]"
            self.console.print(f"  {i}. {label} ({status})")
        valid = [str(i) for i in range(1, len(providers) + 1)] + [p[0] for p in providers]
        try:
            choice = Prompt.ask("[bold]Which provider?[/]", choices=valid, default="1")
        except (EOFError, KeyboardInterrupt):
            self.console.print("\n[dim]Cancelled.[/]")
            return
        if choice.isdigit():
            prov_name = providers[int(choice) - 1][0]
        else:
            prov_name = choice
        if prov_name == "claude":
            self._setup_claude_key()
        else:
            self._setup_provider_key(prov_name)

    def _setup_claude_key(self):
        if self.claude_key:
            masked = self.claude_key[:10] + "..." + self.claude_key[-4:]
            self.console.print(f"Current Claude key: [dim]{masked}[/]")
            self.console.print("[dim]Paste new key to replace, or type 'remove' to delete[/]")
        try:
            key = Prompt.ask("[bold yellow]Paste Claude API key[/]")
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

    def _setup_provider_key(self, provider):
        """Generic key setup for any OpenAI-compatible provider."""
        prov = OPENAI_COMPAT_PROVIDERS[provider]
        current_key = self.api_keys.get(provider, "")
        if current_key:
            masked = current_key[:8] + "..." + current_key[-4:]
            self.console.print(f"Current {prov['label']} key: [dim]{masked}[/]")
            self.console.print("[dim]Paste new key to replace, or type 'remove' to delete[/]")
        try:
            key = Prompt.ask(f"[bold {prov['color']}]Paste {prov['label']} API key[/]")
            key = key.strip()
            if key.lower() == "remove":
                self.api_keys[provider] = ""
                self.settings.pop(prov["key_setting"], None)
                if self._get_provider_for_model(self.model) == provider:
                    self.model = DEFAULT_MODEL
                    self.settings["default_model"] = DEFAULT_MODEL
                save_settings(self.settings)
                self.console.print(f"[dim]{prov['label']} API key removed.[/]")
            elif not prov["key_prefix"] or key.startswith(prov["key_prefix"]):
                self.api_keys[provider] = key
                self.settings[prov["key_setting"]] = key
                if provider == "qwen":
                    self.qwen_key = key
                    self._configure_qwen_endpoint(key)
                save_settings(self.settings)
                self.console.print(f"[green]{prov['label']} API key saved![/]")
                # Auto-switch to this provider's default model if currently on local
                if not is_claude_model(self.model) and not self._get_provider_for_model(self.model):
                    default_alias = list(prov["aliases"].keys())[0]
                    default_model = prov["aliases"][default_alias]
                    self.console.print(f"[dim]Use /model {default_alias} to switch to {prov['label']}.[/]")
            else:
                self.console.print(f"[yellow]Key should start with {prov['key_prefix']}. Not saved.[/]")
        except (EOFError, KeyboardInterrupt):
            self.console.print("\n[dim]Cancelled.[/]")

    def _configure_qwen_endpoint(self, key):
        """Auto-detect Qwen endpoint from key type, ask region if needed."""
        from config import QWEN_URLS
        is_coding = key.startswith("sk-sp-")
        if is_coding:
            self.console.print("[blue]Detected Coding Plan key (sk-sp-).[/]")
        # Ask region
        self.console.print("\n[bold blue]Qwen region:[/]")
        if is_coding:
            options = [
                ("1", "International (Singapore)", "coding-intl"),
                ("2", "China (Beijing)", "coding-china"),
            ]
        else:
            options = [
                ("1", "International (Singapore)", "intl"),
                ("2", "China (Beijing)", "china"),
            ]
        for num, label, _ in options:
            self.console.print(f"  {num}. {label}")
        try:
            choice = Prompt.ask("[bold]Region[/]", choices=["1", "2"], default="1")
        except (EOFError, KeyboardInterrupt):
            choice = "1"
        region_key = options[int(choice) - 1][2]
        url = QWEN_URLS[region_key]
        OPENAI_COMPAT_PROVIDERS["qwen"]["url"] = url
        models_url = url.replace("/chat/completions", "/models")
        OPENAI_COMPAT_PROVIDERS["qwen"]["models_url"] = models_url
        self.settings["qwen_region"] = region_key
        self.console.print(f"[dim]Endpoint: {url}[/]")

    def _fetch_api_models(self):
        """Fetch live model lists from Claude and all OpenAI-compat APIs. Caches results."""
        if not hasattr(self, "_cached_api_models"):
            self._cached_api_models = {"claude": [], "_ts": 0}
            for prov_name in OPENAI_COMPAT_PROVIDERS:
                self._cached_api_models[prov_name] = []
        import time
        # Cache for 10 minutes
        if time.time() - self._cached_api_models.get("_ts", 0) < 600:
            return self._cached_api_models
        # Fetch Claude models
        claude_models = []
        if self.claude_key:
            try:
                resp = requests.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": self.claude_key, "anthropic-version": "2023-06-01"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for m in data:
                        mid = m.get("id", "")
                        if mid.startswith("claude-") and "embed" not in mid:
                            claude_models.append(mid)
                    claude_models.sort()
            except Exception:
                pass
        # Fetch all OpenAI-compatible provider models
        result = {"claude": claude_models, "_ts": time.time()}
        for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
            key = self.api_keys.get(prov_name, "")
            if not key:
                result[prov_name] = []
                continue
            models = []
            try:
                resp = requests.get(
                    prov.get("models_url", prov["url"].replace("/chat/completions", "/models")),
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for m in data:
                        mid = m.get("id", "")
                        if mid:
                            models.append(mid)
                    models.sort()
            except Exception:
                pass
            result[prov_name] = models
        self._cached_api_models = result
        return self._cached_api_models

    def _get_api_model_choices(self):
        """Get models from APIs that aren't already in aliases."""
        live = self._fetch_api_models()
        extras = {}
        extras["claude"] = [m for m in live.get("claude", []) if m not in CLAUDE_ALIASES.values()]
        for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
            extras[prov_name] = [m for m in live.get(prov_name, []) if m not in prov["aliases"].values()]
        return extras

    def _list_models(self):
        choices = []  # list of (model_name, provider)
        n = 0
        lines = []
        if self.claude_key:
            lines.append("[bold yellow]Claude API:[/]")
            for alias, model in CLAUDE_ALIASES.items():
                n += 1
                choices.append((model, "claude"))
                marker = " [cyan]◀[/]" if model == self.model else ""
                lines.append(f"  [dim]{n:>3}.[/] [cyan]{model}[/] [dim](/{alias})[/]{marker}")
            extras = self._get_api_model_choices()
            extra_claude = extras.get("claude", [])
            if extra_claude:
                lines.append("  [dim]── additional (from API) ──[/]")
                for m in extra_claude:
                    n += 1
                    choices.append((m, "claude"))
                    marker = " [cyan]◀[/]" if m == self.model else ""
                    lines.append(f"  [dim]{n:>3}.[/] [cyan]{m}[/]{marker}")
            lines.append("")
        # All OpenAI-compatible providers
        extras = self._get_api_model_choices()
        for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
            key = self.api_keys.get(prov_name, "")
            if not key:
                continue
            lines.append(f"[bold {prov['color']}]{prov['label']} API:[/]")
            for alias, model in prov["aliases"].items():
                n += 1
                choices.append((model, prov_name))
                marker = " [cyan]◀[/]" if model == self.model else ""
                lines.append(f"  [dim]{n:>3}.[/] [cyan]{model}[/] [dim](/{alias})[/]{marker}")
            extra = extras.get(prov_name, [])
            if extra:
                lines.append("  [dim]── additional (from API) ──[/]")
                for m in extra:
                    n += 1
                    choices.append((m, prov_name))
                    marker = " [cyan]◀[/]" if m == self.model else ""
                    lines.append(f"  [dim]{n:>3}.[/] [cyan]{m}[/]{marker}")
            lines.append("")
        lines.append("[bold green]Local Ollama:[/]")
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            if not models:
                lines.append("  [dim]No models found. Is Ollama running?[/]")
            else:
                for m in models:
                    name = m["name"]
                    n += 1
                    choices.append((name, "local"))
                    size = m.get("size", 0)
                    size_str = f"{size / 1e9:.1f}GB" if size > 1e9 else f"{size / 1e6:.0f}MB"
                    marker = " [cyan]◀[/]" if name == self.model else ""
                    lines.append(f"  [dim]{n:>3}.[/] [cyan]{name}[/] [dim]({size_str})[/]{marker}")
        except Exception:
            lines.append("  [dim]Can't reach Ollama (not running?)[/]")
        self.console.print(Panel("\n".join(lines), title="Available Models", border_style="blue"))
        # Let user pick by number
        if choices:
            try:
                pick = Prompt.ask("[bold]Pick a model[/] (number or 'skip')")
            except (EOFError, KeyboardInterrupt):
                return
            pick = pick.strip()
            if pick.lower() in ("skip", ""):
                return
            if pick.isdigit() and 1 <= int(pick) <= len(choices):
                new_model, prov_name = choices[int(pick) - 1]
                self.model = new_model
                self.multi_models = []
                if prov_name == "claude":
                    self._stop_ollama()
                    provider_str = "[yellow]Claude API[/]"
                elif prov_name == "local":
                    provider_str = "[green]Local[/]"
                    self._ensure_ollama()
                else:
                    self._stop_ollama()
                    prov = OPENAI_COMPAT_PROVIDERS[prov_name]
                    provider_str = f"[{prov['color']}]{prov['label']} API[/]"
                self.console.print(f"Switched to [cyan]{self.model}[/] ({provider_str})")
            else:
                self.console.print(f"[dim]Invalid choice.[/]")

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
            elif self._get_provider_for_model(self.model):
                text = self._openai_compat_nostream(
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
        provider = self._get_provider_for_model(self.model)
        if provider:
            # Provider-specific limits
            limits = {"qwen": 1_000_000, "openai": 128_000, "deepseek": 64_000, "groq": 32_768, "mistral": 128_000}
            return limits.get(provider, 128_000)
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
        self._session_stats["messages_sent"] += 1
        self._lint_fix_count = 0
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

        # Architect mode: strong model plans, cheap model implements
        if self.architect_mode and not self.plan_mode:
            original_model = self.model
            # Phase 1: Plan with architect model
            self.model = self._architect_model
            self.console.print(f"  [cyan]Architect ({self._architect_model}) planning...[/]")
            plan_prefix = (
                "[ARCHITECT MODE - PLANNING] You are the architect. Explore the codebase and design "
                "a detailed step-by-step implementation plan. Do NOT write or edit files yet. "
                "List every file to create/modify, every function to add/change.\n\n"
                "User request: "
            )
            old_mode = self.permission_mode
            self.permission_mode = "default"
            self._dispatch_chat(plan_prefix + user_msg)
            self.permission_mode = old_mode
            # Show approval
            self.console.print()
            self.console.print("  [bold magenta]Architect plan complete![/]")
            options = [
                ("Approve", f"implement with {self._impl_model}"),
                ("Revise", "give feedback and re-plan"),
                ("Reject", "cancel"),
            ]
            try:
                choice = self._arrow_select(options, self.console, default=0)
            except (EOFError, KeyboardInterrupt):
                self.model = original_model
                return
            if choice == 0:
                self.model = self._impl_model
                self.console.print(f"  [green]Implementing with {self._impl_model}...[/]")
                self._dispatch_chat(
                    f"The architect has created a plan (see above). Now implement it step by step. "
                    f"Original request: {user_msg}"
                )
                self.model = original_model
            elif choice == 1:
                self.model = original_model
                feedback = input("\033[1;35m❯ \033[0m")
                if feedback.strip():
                    self._chat(f"{user_msg}\n\nArchitect feedback: {feedback}")
            else:
                self.model = original_model
                self.console.print("[dim]Architect plan rejected.[/]")
            return

        self._dispatch_chat(user_msg)
        # Send notification if task took >10s
        if self._notify_enabled and hasattr(self, '_chat_start_time'):
            elapsed = time.time() - self._chat_start_time
            if elapsed > 10:
                self._send_notification("Kodiqa", f"Response ready ({elapsed:.0f}s)")
        # Render any mermaid diagrams in the last response
        if self.history and self.history[-1].get("role") == "assistant":
            last_content = self.history[-1].get("content", "")
            if isinstance(last_content, str) and "```mermaid" in last_content:
                self._render_diagrams(last_content)

    def _dispatch_chat(self, user_msg):
        """Route to the correct provider chat method."""
        if self._budget_exceeded:
            self.console.print(f"[red]Budget exceeded (${self.session_tokens['cost']:.4f} / ${self.budget_limit:.2f}).[/]")
            self.console.print("[dim]Use /budget <amount> to increase, or start a new session.[/]")
            return
        self._check_cost_optimizer(user_msg)
        self._chat_start_time = time.time()
        if self.multi_models:
            self._chat_multi(user_msg)
        elif is_claude_model(self.model) or self._is_live_claude(self.model):
            self._chat_claude(user_msg)
        else:
            provider = self._get_provider_for_model(self.model)
            if provider:
                self._chat_openai_compat(user_msg, provider)
            else:
                self._chat_ollama(user_msg)

    def _is_live_claude(self, model_name):
        """Check if model is in cached live Claude model list."""
        cached = getattr(self, "_cached_api_models", None)
        return cached is not None and model_name in cached.get("claude", [])

    def _review_edit_queue(self):
        """Show batch edit review panel — cycle through queued edits, accept/reject each."""
        queue = get_edit_queue()
        if not queue:
            return []

        self.console.print()
        total = len(queue)

        decisions = [None] * total  # None = pending, True = accepted, False = rejected
        current = 0

        while True:
            entry = queue[current]
            path = entry["path"]
            etype = entry["type"]
            desc = entry["description"]
            old = entry.get("old_content", "")
            new = entry.get("new_content", "")

            # Status indicator
            status_icon = "[dim]?[/]"
            if decisions[current] is True:
                status_icon = "[green]✓[/]"
            elif decisions[current] is False:
                status_icon = "[red]✗[/]"

            # File summary
            if old:
                old_lines = len(old.splitlines())
                new_lines = len(new.splitlines())
                added = max(0, new_lines - old_lines)
                removed = max(0, old_lines - new_lines)
                change_desc = f"[green]+{added}[/] [red]-{removed}[/] lines"
            else:
                change_desc = "[green]+ new file[/]"

            self.console.print(
                f"\n  {status_icon} [bold]({current + 1}/{total})[/] [cyan]{os.path.basename(path)}[/] "
                f"[dim]— {etype}[/]  {change_desc}"
            )

            options = [
                ("Accept", ""),
                ("Reject", ""),
                ("Show diff", ""),
                ("Accept all", f"remaining {sum(1 for d in decisions if d is None)} edits"),
                ("Reject all", ""),
            ]
            choice = self._arrow_select(options, self.console, default=0)

            if choice == 0:  # Accept
                decisions[current] = True
                self.console.print(f"    [green]✓ Accepted[/]")
                if current < total - 1:
                    current += 1
                elif all(d is not None for d in decisions):
                    break
            elif choice == 1:  # Reject
                decisions[current] = False
                self.console.print(f"    [red]✗ Rejected[/]")
                if current < total - 1:
                    current += 1
                elif all(d is not None for d in decisions):
                    break
            elif choice == 2:  # Show diff
                import difflib
                old_lines_list = old.splitlines(keepends=True) if old else []
                new_lines_list = new.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines_list, new_lines_list,
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
            elif choice == 3:  # Accept all
                for i in range(total):
                    if decisions[i] is None:
                        decisions[i] = True
                self.console.print(f"  [green]✓ All {total} edits accepted[/]")
                break
            elif choice == 4:  # Reject all
                for i in range(total):
                    if decisions[i] is None:
                        decisions[i] = False
                self.console.print(f"  [red]✗ All {total} edits rejected[/]")
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
        self.console.print("  [bold magenta]Plan complete![/] Review the plan above:")
        options = [
            ("Approve", "implement the plan now"),
            ("Revise", "give feedback and re-plan"),
            ("Reject", "cancel and exit plan mode"),
        ]
        try:
            choice = self._arrow_select(options, self.console, default=0)
            if choice == 0:
                self._pending_plan = "approved"
                self.console.print("[green]Plan approved! Implementing...[/]")
                self._chat("")  # Trigger phase 2
            elif choice == 1:
                self._pending_plan = None  # Reset to allow re-plan
                feedback = input("\033[1;35m❯ \033[0m")
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

    def _handle_architect(self, arg):
        """Configure architect mode: strong model plans, cheap model implements."""
        if not arg:
            if self.architect_mode:
                self.console.print(f"[cyan]Architect mode ON[/]")
                self.console.print(f"  Planner:     [bold]{self._architect_model}[/]")
                self.console.print(f"  Implementer: [bold]{self._impl_model}[/]")
            else:
                self.console.print("[dim]Usage: /architect <planner_model> <impl_model>[/]")
                self.console.print("[dim]  Example: /architect opus haiku[/]")
                self.console.print("[dim]  /architect off — disable[/]")
            return
        if arg.strip().lower() == "off":
            self.architect_mode = False
            self.console.print("[yellow]Architect mode OFF[/]")
            return
        parts = arg.strip().split()
        if len(parts) < 2:
            self.console.print("[dim]Need two models: /architect <planner> <implementer>[/]")
            return
        self._architect_model = self._resolve_model_name(parts[0])
        self._impl_model = self._resolve_model_name(parts[1])
        self.architect_mode = True
        self.console.print(f"[green]Architect mode ON[/]")
        self.console.print(f"  Planner:     [bold]{self._architect_model}[/]")
        self.console.print(f"  Implementer: [bold]{self._impl_model}[/]")

    def _resolve_model_name(self, name):
        """Resolve a model alias to full model name."""
        from config import CLAUDE_ALIASES, MODEL_ALIASES, OPENAI_COMPAT_PROVIDERS, QWEN_EXTRA_ALIASES
        if name in CLAUDE_ALIASES:
            return CLAUDE_ALIASES[name]
        if name in MODEL_ALIASES:
            return MODEL_ALIASES[name]
        for prov_data in OPENAI_COMPAT_PROVIDERS.values():
            aliases = prov_data.get("aliases", {})
            if name in aliases:
                return aliases[name]
        if name in QWEN_EXTRA_ALIASES:
            return QWEN_EXTRA_ALIASES[name]
        return name

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
                    else:
                        prov = self._get_provider_for_model(model_name)
                        if prov:
                            results[model_name] = self._multi_query_openai_compat(prov, model_name, user_msg, memories_ctx, context_file_ctx)
                        else:
                            results[model_name] = self._multi_query_ollama(model_name, user_msg, memories_ctx, context_file_ctx)
                except Exception as e:
                    results[model_name] = f"Error: {e}"

            # Display result immediately after each model finishes
            response = results[model_name]
            if is_claude_model(model_name):
                color = "yellow"
            else:
                prov = self._get_provider_for_model(model_name)
                if prov:
                    color = OPENAI_COMPAT_PROVIDERS[prov]["color"]
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
        # Ensure Ollama is running (may have switched from cloud model)
        if not self._ensure_ollama():
            self.console.print("[red]Cannot chat — Ollama is not running.[/]")
            return
        # Check if model is installed
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            installed = [m["name"] for m in resp.json().get("models", [])]
            if not any(m.startswith(self.model.split(":")[0]) for m in installed):
                self.console.print(f"[red]Model [cyan]{self.model}[/red][red] is not installed.[/]")
                self.console.print(f"  • Pull it: [bold]ollama pull {self.model}[/]")
                has_any_key = self.claude_key or any(self.api_keys.get(p, "") for p in OPENAI_COMPAT_PROVIDERS)
                if has_any_key:
                    self.console.print(f"  • Or switch: [bold]/model[/] to pick a cloud model")
                else:
                    self.console.print(f"  • Or add API key: [bold]/key[/]")
                return
        except Exception:
            pass
        # Embed @file references (images as text fallback for local models)
        msg_text = self._append_files_to_text(user_msg, self._pending_files)
        if self._pending_images:
            # Ollama supports images via 'images' field for vision models
            ollama_images = [img["data"] for img in self._pending_images]
            self.history.append({"role": "user", "content": msg_text, "images": ollama_images})
        else:
            self.history.append({"role": "user", "content": msg_text})
        self._pending_files = []
        self._pending_images = []
        while True:
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = SYSTEM_PROMPT.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if self._persona and self._persona in PERSONAS:
                system_prompt = PERSONAS[self._persona]["prompt"] + "\n\n" + system_prompt
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx
            pinned_ctx = self._build_pinned_context()
            if pinned_ctx:
                system_prompt += "\n\n" + pinned_ctx
            messages = [{"role": "system", "content": system_prompt}] + self.history

            assistant_text = self._stream_ollama(messages)
            if assistant_text is None:
                return
            if self._stream_interrupted:
                if assistant_text:
                    self.history.append({"role": "assistant", "content": assistant_text})
                self._save_session()
                return
            self.history.append({"role": "assistant", "content": assistant_text})

            actions = parse_actions(assistant_text)
            if not actions:
                self._save_session()
                return
            # Enable batch mode if active
            if self.batch_edits:
                set_batch_mode(True)
            results = []
            for action in actions:
                action_label = _tool_label(action['name'], action.get('params', {}))
                with Status(f"  [yellow]●[/] {action_label}", console=self.console, spinner="dots"):
                    if not self._check_workspace_boundary(action['name'], action.get('params', {})):
                        result = "Denied: file is outside the workspace directory."
                    else:
                        result = execute_action(action, self.memory, self._confirm)
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results.append(f"[Result of {action['name']}]\n{result}")
                self._track_tool(action['name'])
                self.console.print(f"  [green]●[/] {action_label}")
            # Review queued edits if any
            if self.batch_edits and get_edit_queue():
                set_batch_mode(False)
                review_results = self._review_edit_queue()
                for rr in review_results:
                    results.append(f"[Edit Review]\n{rr}")
            set_batch_mode(False)
            self._auto_commit_if_enabled()
            lint_errors = self._run_lint_if_enabled()
            if lint_errors:
                results.append(f"[Lint Errors]\n{lint_errors}")
            self.history.append({"role": "user", "content": f"[Action Results]\n" + "\n\n".join(results)})
            # Auto lint-fix: if enabled, inject fix request and continue loop
            if lint_errors and self.lint_auto_fix:
                if not hasattr(self, '_lint_fix_count'):
                    self._lint_fix_count = 0
                self._lint_fix_count += 1
                if self._lint_fix_count <= 3:
                    self.console.print(f"  [cyan]●[/] Auto lint-fix iteration {self._lint_fix_count}/3...")
                    self.history.append({"role": "user", "content": f"Fix these lint errors (attempt {self._lint_fix_count}/3):\n{lint_errors}"})
                    continue
                else:
                    self.console.print(f"  [yellow]●[/] Lint auto-fix: max iterations reached, still has errors.")
                    self._lint_fix_count = 0

    # ── Claude chat (native tool_use API) ──

    def _chat_claude(self, user_msg):
        # Embed @file references and images into the message
        msg_text = self._append_files_to_text(user_msg, self._pending_files)
        if self._pending_images:
            content = [{"type": "text", "text": msg_text}]
            for img in self._pending_images:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": img["media_type"], "data": img["data"],
                }})
            self.history.append({"role": "user", "content": content})
        else:
            self.history.append({"role": "user", "content": msg_text})
        self._pending_files = []
        self._pending_images = []

        while True:
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if self._persona and self._persona in PERSONAS:
                system_prompt = PERSONAS[self._persona]["prompt"] + "\n\n" + system_prompt
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx
            pinned_ctx = self._build_pinned_context()
            if pinned_ctx:
                system_prompt += "\n\n" + pinned_ctx

            # Build Claude messages (must alternate user/assistant)
            messages = self._build_claude_messages()

            response = self._call_claude_stream(system_prompt, messages)
            if response is None:
                return
            if self._stream_interrupted:
                text_content = response.get("text", "")
                if text_content:
                    self.history.append({"role": "assistant", "content": [{"type": "text", "text": text_content}]})
                self._save_session()
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
                return  # No tools = done

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
                    self._track_tool(tc_name)
                    self.console.print(f"  [green]●[/] {_tool_label(tc_name, tc_input)}")
            elif len(regular_calls) == 1:
                tc = regular_calls[0]
                label = _tool_label(tc['name'], tc.get('input', {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self._execute_tool(tc["name"], tc["input"])
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self._track_tool(tc['name'])
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
            self._auto_commit_if_enabled()
            lint_errors = self._run_lint_if_enabled()
            if lint_errors:
                results_list.append(("lint", lint_errors))

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
            # Auto lint-fix: if enabled, inject fix request and continue loop
            if lint_errors and self.lint_auto_fix:
                if not hasattr(self, '_lint_fix_count'):
                    self._lint_fix_count = 0
                self._lint_fix_count += 1
                if self._lint_fix_count <= 3:
                    self.console.print(f"  [cyan]●[/] Auto lint-fix iteration {self._lint_fix_count}/3...")
                    self.history.append({"role": "user", "content": f"Fix these lint errors (attempt {self._lint_fix_count}/3):\n{lint_errors}"})
                    continue
                else:
                    self.console.print(f"  [yellow]●[/] Lint auto-fix: max iterations reached, still has errors.")
                    self._lint_fix_count = 0

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
            self.console.print("[yellow]Try /model to switch providers.[/]")
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
        stall = StreamStallIndicator(self.console)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()
        cleanup = self._start_stream_interrupt()

        try:
            for line in resp.iter_lines():
                if self._stream_interrupted:
                    resp.close()
                    break
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
                stall.ping()

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
            self._stream_interrupted = True
        finally:
            cleanup()
            stall.stop()

        if self._stream_interrupted:
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

    def _check_workspace_boundary(self, name, params):
        """Check if a tool accesses files outside the workspace. Returns True if allowed."""
        # Tools that access file paths
        path_params = {
            "read_file": ["path"], "write_file": ["path"], "edit_file": ["path"],
            "multi_edit": ["path"], "search_replace_all": ["path"],
            "delete_file": ["path"], "move_file": ["source", "destination"],
            "list_dir": ["path"], "tree": ["path"], "glob": ["path"],
            "grep": ["path"], "read_image": ["path"], "read_pdf": ["path"],
            "create_directory": ["path"], "diff_apply": ["path"],
        }
        if name not in path_params or not params:
            return True
        workspace = os.path.abspath(self.cwd)
        if not hasattr(self, "_allowed_dirs"):
            self._allowed_dirs = set()
        for key in path_params[name]:
            file_path = params.get(key, "")
            if not file_path:
                continue
            abs_path = os.path.abspath(os.path.expanduser(file_path))
            # Check if path is inside workspace
            if abs_path.startswith(workspace + "/") or abs_path == workspace:
                continue
            # Check if already allowed
            if any(abs_path.startswith(d + "/") or abs_path == d for d in self._allowed_dirs):
                continue
            # Ask permission
            parent_dir = os.path.dirname(abs_path)
            try:
                self.console.print(f"\n  [bold yellow]Outside workspace:[/] {abs_path}")
                self.console.print(f"  [dim]Workspace: {workspace}[/]")
                options = [
                    ("Allow once", ""),
                    ("Allow this directory", f"always allow {parent_dir}"),
                    ("Deny", ""),
                ]
                choice = self._arrow_select(options, self.console, default=0)
                if choice == 0:
                    pass  # allow once
                elif choice == 1:
                    self._allowed_dirs.add(parent_dir)
                    self.console.print(f"  [dim]Directory allowed for this session.[/]")
                else:
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
        return True

    def _execute_tool(self, name, params):
        """Execute a tool call, routing MCP tools to MCP manager."""
        if name.startswith("mcp_"):
            return self.mcp.call_tool(name, params or {})
        if not self._check_workspace_boundary(name, params):
            return "Denied: file is outside the workspace directory."
        return execute_tool_call(name, params, self.memory, self._confirm)

    def _get_all_tools(self):
        """Get all tools: built-in + MCP server tools."""
        tools = list(CLAUDE_TOOLS)
        tools.extend(self.mcp.get_all_tools())
        return tools

    def _get_openai_tools(self):
        """Convert Claude tool schemas to OpenAI function-calling format."""
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

    def _get_provider_for_model(self, model_name):
        """Return provider name for a model, checking aliases + live cache."""
        prov = get_openai_provider(model_name)
        if prov:
            return prov
        cached = getattr(self, "_cached_api_models", {})
        for prov_name in OPENAI_COMPAT_PROVIDERS:
            if model_name in cached.get(prov_name, []):
                return prov_name
        return None

    def _chat_openai_compat(self, user_msg, provider):
        """Generic OpenAI-compatible chat loop (used by Qwen, OpenAI, DeepSeek, Groq, Mistral)."""
        # Embed @file references and images into the message
        msg_text = self._append_files_to_text(user_msg, self._pending_files)
        if self._pending_images:
            content = [{"type": "text", "text": msg_text}]
            for img in self._pending_images:
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{img['media_type']};base64,{img['data']}",
                }})
            self.history.append({"role": "user", "content": content})
        else:
            self.history.append({"role": "user", "content": msg_text})
        self._pending_files = []
        self._pending_images = []

        while True:
            memories_ctx = self.memory.get_context()
            context_file_ctx = self._load_context_file()
            system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=self.model, memories=memories_ctx)
            if self._persona and self._persona in PERSONAS:
                system_prompt = PERSONAS[self._persona]["prompt"] + "\n\n" + system_prompt
            if context_file_ctx:
                system_prompt += "\n\n" + context_file_ctx
            git_ctx = self._git_context()
            if git_ctx:
                system_prompt += "\n\n" + git_ctx
            env_ctx = self._shell_env_context()
            if env_ctx:
                system_prompt += "\n\n" + env_ctx
            pinned_ctx = self._build_pinned_context()
            if pinned_ctx:
                system_prompt += "\n\n" + pinned_ctx

            messages = self._build_openai_messages(system_prompt)

            response = self._call_openai_compat_stream(messages, provider)
            if response is None:
                return
            if self._stream_interrupted:
                text_content = response.get("text", "")
                if text_content:
                    self.history.append({"role": "assistant", "content": text_content})
                self._save_session()
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
                return

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
                    self._track_tool(tc_name)
                    self.console.print(f"  [green]●[/] {_tool_label(tc_name, tc_input)}")
            elif len(regular_calls) == 1:
                tc = regular_calls[0]
                label = _tool_label(tc["name"], tc.get("input", {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = self._execute_tool(tc["name"], tc["input"])
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self._track_tool(tc["name"])
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
            self._auto_commit_if_enabled()
            lint_errors = self._run_lint_if_enabled()
            if lint_errors:
                results_list.append(("lint", lint_errors))

            # Add tool results as separate messages (OpenAI format)
            for tc_id, result in results_list:
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })
            self._save_session()
            # Auto lint-fix: if enabled, inject fix request and continue loop
            if lint_errors and self.lint_auto_fix:
                if not hasattr(self, '_lint_fix_count'):
                    self._lint_fix_count = 0
                self._lint_fix_count += 1
                if self._lint_fix_count <= 3:
                    self.console.print(f"  [cyan]●[/] Auto lint-fix iteration {self._lint_fix_count}/3...")
                    self.history.append({"role": "user", "content": f"Fix these lint errors (attempt {self._lint_fix_count}/3):\n{lint_errors}"})
                    continue
                else:
                    self.console.print(f"  [yellow]●[/] Lint auto-fix: max iterations reached, still has errors.")
                    self._lint_fix_count = 0

    def _build_openai_messages(self, system_prompt):
        """Convert history to OpenAI message format for OpenAI-compatible APIs."""
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
                if isinstance(content, list):
                    # Check if it contains image blocks — pass through for vision
                    has_images = any(isinstance(b, dict) and b.get("type") in ("image_url", "image")
                                     for b in content)
                    if has_images:
                        # Convert Claude image format to OpenAI if needed
                        openai_content = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "image":
                                    src = block.get("source", {})
                                    openai_content.append({"type": "image_url", "image_url": {
                                        "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}",
                                    }})
                                elif block.get("type") == "image_url":
                                    openai_content.append(block)
                                elif block.get("type") == "text":
                                    openai_content.append(block)
                                elif block.get("type") == "tool_result":
                                    openai_content.append({"type": "text", "text": str(block.get("content", ""))})
                        content = openai_content if openai_content else ""
                    else:
                        # Flatten text (existing behavior)
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                text_parts.append(str(block.get("content", "")))
                            elif isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content = "\n".join(text_parts) if text_parts else ""
                messages.append({"role": "user", "content": content})
        return messages

    def _build_openai_request_body(self, messages, provider):
        """Build request body for OpenAI-compatible API, with provider-specific params."""
        model = self.model
        # Coding Plan (sk-sp-) remapping — qwen3-max isn't directly available
        if provider == "qwen" and self.settings.get("qwen_region", "").startswith("coding"):
            from config import QWEN_CODING_PLAN_MODELS
            if model not in QWEN_CODING_PLAN_MODELS:
                remap = {
                    "qwen3-max": "qwen3-max-2026-01-23",
                    "qwq-plus": "qwen3.5-plus",  # qwq not on coding plan
                    "qwen3.5-flash": "qwen3.5-plus",
                    "qwen-turbo": "qwen3.5-plus",
                    "qwen-math-plus": "qwen3.5-plus",
                    "qwen3-coder-flash": "qwen3-coder-plus",
                }
                if model in remap:
                    model = remap[model]
                    self.console.print(f"[dim]Coding Plan: using {model}[/]")
        body = {
            "model": model,
            "messages": messages,
            "tools": self._get_openai_tools(),
            "max_tokens": 8192,
            "stream": True,
        }
        # stream_options not supported by all providers
        if provider not in ("groq",):
            body["stream_options"] = {"include_usage": True}
        # parallel_tool_calls only for providers that support it
        if provider in ("openai",):
            body["parallel_tool_calls"] = True
        return body

    def _call_openai_compat_stream(self, messages, provider):
        """Stream OpenAI-compatible API with tool calling, retry, and token tracking."""
        prov = OPENAI_COMPAT_PROVIDERS[provider]
        key = self.api_keys.get(provider, "")
        if not key:
            self.console.print(f"[red]No {prov['label']} API key. Use /key {provider} to add one.[/]")
            return None

        try:
            resp = _retry_api_call(
                lambda: requests.post(
                    prov["url"],
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=self._build_openai_request_body(messages, provider),
                    stream=True,
                    timeout=300,
                ),
                provider_name=prov["label"],
            )
            if resp.status_code == 401:
                detail = ""
                try:
                    detail = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    detail = resp.text[:200]
                self.console.print(f"[red]{prov['label']} API key rejected (401): {detail}[/]")
                self.console.print(f"[dim]Use /key {provider} to update it.[/]")
                return None
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    detail = resp.text[:200]
                self.console.print(f"[red]{prov['label']} API error {resp.status_code}: {detail}[/]")
                self.console.print(f"[dim]Model: {self.model} | Endpoint: {prov['url']}[/]")
                return None
        except Exception as e:
            if _logger:
                _logger.error(f"{prov['label']} API failed: {e}")
            self.console.print(f"[red]{prov['label']} error: {e}[/]")
            return None

        # Parse SSE streaming response (OpenAI format)
        self.console.print()
        stream_start = time.time()

        full_text = []
        tool_calls = {}  # index -> {id, name, arguments}
        stream_usage = {}
        first_token = True
        writer = StreamWriter(self.console, compact=self.compact_mode)
        stall = StreamStallIndicator(self.console)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()
        cleanup = self._start_stream_interrupt()

        try:
            for line in resp.iter_lines():
                if self._stream_interrupted:
                    resp.close()
                    break
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
                stall.ping()

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
            self._stream_interrupted = True
        finally:
            stall.stop()
            cleanup()

        if self._stream_interrupted:
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

    def _multi_query_openai_compat(self, provider, model_name, user_msg, memories_ctx, context_file_ctx):
        """Non-streaming OpenAI-compatible API query for multi-model mode."""
        prov = OPENAI_COMPAT_PROVIDERS[provider]
        key = self.api_keys.get(provider, "")
        if not key:
            return f"No {prov['label']} API key"
        system_prompt = CLAUDE_SYSTEM.format(cwd=self.cwd, model=model_name, memories=memories_ctx)
        if context_file_ctx:
            system_prompt += "\n\n" + context_file_ctx
        try:
            resp = requests.post(
                prov["url"],
                headers={
                    "Authorization": f"Bearer {key}",
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

    def _openai_compat_nostream(self, system, messages, provider=None):
        """Non-streaming OpenAI-compatible call (for compact/summary)."""
        if provider is None:
            provider = self._get_provider_for_model(self.model)
        if not provider:
            return ""
        prov = OPENAI_COMPAT_PROVIDERS[provider]
        key = self.api_keys.get(provider, "")
        if not key:
            return ""
        oai_msgs = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m.get("content"), str):
                oai_msgs.append({"role": m["role"], "content": m["content"]})
        try:
            resp = requests.post(
                prov["url"],
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "max_tokens": 4096, "messages": oai_msgs},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            return ""

    # ── Stream interrupt (Esc or Ctrl+C) ──

    def _start_stream_interrupt(self):
        """Start monitoring for Esc key to interrupt streaming. Returns cleanup fn."""
        self._stream_interrupted = False
        old_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum, frame):
            self._stream_interrupted = True

        signal.signal(signal.SIGINT, _sigint_handler)

        stop_event = threading.Event()

        def _esc_monitor():
            import tty, termios
            fd = sys.stdin.fileno()
            try:
                old_settings = termios.tcgetattr(fd)
            except termios.error:
                return
            try:
                tty.setcbreak(fd)
                while not stop_event.is_set() and not self._stream_interrupted:
                    if select.select([fd], [], [], 0.1)[0]:
                        ch = os.read(fd, 1)
                        if ch == b'\x1b':  # Esc
                            self._stream_interrupted = True
                            break
                        elif ch == b'\x03':  # Ctrl+C
                            self._stream_interrupted = True
                            break
            except Exception:
                pass
            finally:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass

        t = threading.Thread(target=_esc_monitor, daemon=True)
        t.start()

        def cleanup():
            stop_event.set()
            t.join(timeout=0.5)
            signal.signal(signal.SIGINT, old_handler)

        return cleanup

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
            has_any_key = self.claude_key or any(self.api_keys.get(p, "") for p in OPENAI_COMPAT_PROVIDERS)
            if has_any_key:
                self.console.print("[yellow]Try /model to switch to a cloud provider.[/]")
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
        stall = StreamStallIndicator(self.console)
        thinking_status = Status("  [dim]Thinking...[/]", console=self.console, spinner="dots")
        thinking_status.start()
        cleanup = self._start_stream_interrupt()
        try:
            for line in resp.iter_lines():
                if self._stream_interrupted:
                    resp.close()
                    break
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stall.ping()
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
            self._stream_interrupted = True
        finally:
            stall.stop()
            cleanup()
        if self._stream_interrupted:
            thinking_status.stop()
            writer.flush_pending()
            self.console.print("\n[dim](interrupted — press Esc or Ctrl+C to stop)[/]")
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
        # Budget warnings
        if self.budget_limit > 0:
            pct = (self.session_tokens["cost"] / self.budget_limit) * 100
            if pct >= 100 and not self._budget_exceeded:
                self._budget_exceeded = True
                self.console.print(f"  [red bold]⚠ Budget exceeded! (${self.session_tokens['cost']:.4f} / ${self.budget_limit:.2f})[/]")
            elif pct >= 80 and not self._budget_exceeded:
                self.console.print(f"  [yellow]⚠ {pct:.0f}% of budget used (${self.session_tokens['cost']:.4f} / ${self.budget_limit:.2f})[/]")

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

    def _load_kodiqaignore(self):
        """Load .kodiqaignore from cwd and merge into skip sets."""
        extra_dirs, extra_exts = load_kodiqaignore(self.cwd)
        if extra_dirs:
            SKIP_DIRS.update(extra_dirs)
        if extra_exts:
            SKIP_EXTENSIONS.update(extra_exts)

    def _auto_commit_if_enabled(self):
        """Auto git commit after AI edits if enabled and in a git repo."""
        if not self.auto_commit or not self.git_info:
            return
        try:
            result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            changed = [l.split()[-1] for l in result.stdout.strip().splitlines() if l.strip()]
            if not changed:
                return
            files_str = ", ".join(changed[:5])
            if len(changed) > 5:
                files_str += f" +{len(changed) - 5} more"
            msg = f"kodiqa: edit {files_str}"
            subprocess.run(["git", "add", "-A"], capture_output=True, timeout=10)
            subprocess.run(["git", "commit", "-m", msg], capture_output=True, timeout=10)
            self.console.print(f"  [green]●[/] [dim]Auto-committed: {msg}[/]")
        except Exception:
            pass

    def _run_lint_if_enabled(self):
        """Run lint command after edits if configured."""
        if not self.lint_cmd:
            return None
        try:
            result = subprocess.run(self.lint_cmd, shell=True, capture_output=True, text=True, timeout=60)
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0 and output:
                self.console.print(f"  [yellow]●[/] Lint issues:\n{output[:500]}")
                return output[:2000]
            elif output:
                self.console.print(f"  [green]●[/] [dim]Lint passed[/]")
        except Exception as e:
            self.console.print(f"  [red]●[/] Lint error: {e}")
        return None

    # ── Phase 2: Share, Git PR, Templates ──

    def _share_session_html(self):
        """Export session as styled HTML file."""
        if not self.history:
            self.console.print("[dim]No conversation to share.[/]")
            return
        export_dir = os.path.join(KODIQA_DIR, "exports")
        os.makedirs(export_dir, exist_ok=True)
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(export_dir, f"{ts}.html")
        html = [
            "<!DOCTYPE html><html><head>",
            "<meta charset='utf-8'>",
            f"<title>Kodiqa Session — {ts}</title>",
            "<style>",
            "body{background:#1a1b26;color:#c0caf5;font-family:'JetBrains Mono',monospace;max-width:800px;margin:0 auto;padding:20px}",
            ".user{background:#24283b;border-left:3px solid #7aa2f7;padding:12px;margin:10px 0;border-radius:6px}",
            ".assistant{background:#1e2030;border-left:3px solid #9ece6a;padding:12px;margin:10px 0;border-radius:6px}",
            ".tool{background:#1a1b26;border-left:3px solid #565f89;padding:8px;margin:5px 0;font-size:0.85em;border-radius:4px}",
            ".label{font-size:0.75em;color:#565f89;margin-bottom:4px}",
            "pre{background:#16161e;padding:10px;border-radius:4px;overflow-x:auto}",
            "code{color:#bb9af7}",
            "h1{color:#7aa2f7;border-bottom:1px solid #24283b;padding-bottom:10px}",
            ".meta{color:#565f89;font-size:0.8em;margin-top:20px;padding-top:10px;border-top:1px solid #24283b}",
            "</style></head><body>",
            f"<h1>Kodiqa Session</h1>",
            f"<div class='meta'>Model: {self.model} | "
            f"Tokens: {self.session_tokens['input']+self.session_tokens['output']:,} | "
            f"Cost: ${self.session_tokens['cost']:.4f} | {ts}</div>",
        ]
        for msg in self.history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Claude-style content blocks
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = "\n".join(text_parts)
            if not content:
                continue
            content_html = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            content_html = content_html.replace("\n", "<br>")
            if role == "user":
                html.append(f"<div class='user'><div class='label'>You</div>{content_html}</div>")
            elif role == "assistant":
                html.append(f"<div class='assistant'><div class='label'>Kodiqa</div>{content_html}</div>")
            elif role == "tool":
                html.append(f"<div class='tool'><div class='label'>Tool Result</div>{content_html}</div>")
        html.append("</body></html>")
        with open(path, "w") as f:
            f.write("\n".join(html))
        self.console.print(f"[green]Session exported:[/] {path}")

    def _handle_gh(self, action, arg):
        """Handle /pr, /review, /issue commands using gh CLI."""
        try:
            subprocess.run(["gh", "--version"], capture_output=True, timeout=5, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.console.print("[red]GitHub CLI (gh) not installed.[/] Install: [cyan]brew install gh[/]")
            return

        if action == "pr":
            try:
                cmd = ["gh", "pr", "create", "--fill"]
                if arg:
                    cmd = ["gh", "pr", "create", "--title", arg, "--fill"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                output = (result.stdout + result.stderr).strip()
                if output:
                    self.console.print(Panel(output, title="PR Created", border_style="green"))
                else:
                    self.console.print("[dim]No output from gh pr create.[/]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")

        elif action == "review":
            if not arg:
                self.console.print("[dim]Usage: /review <pr-number>[/]")
                return
            try:
                result = subprocess.run(
                    ["gh", "pr", "diff", arg.strip()],
                    capture_output=True, text=True, timeout=30,
                )
                diff = result.stdout.strip()
                if diff:
                    if len(diff) > 15000:
                        diff = diff[:15000] + "\n... (truncated)"
                    self.history.append({
                        "role": "user",
                        "content": f"Please review this pull request diff (PR #{arg.strip()}):\n\n```diff\n{diff}\n```\n\nProvide a thorough code review."
                    })
                    self.console.print(f"[green]PR #{arg.strip()} diff loaded.[/] Asking for review...")
                    self._dispatch_chat(self.history[-1]["content"])
                else:
                    self.console.print(f"[dim]No diff found for PR #{arg}[/]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")

        elif action == "issue":
            if not arg:
                self.console.print("[dim]Usage: /issue <number>[/]")
                return
            try:
                result = subprocess.run(
                    ["gh", "issue", "view", arg.strip()],
                    capture_output=True, text=True, timeout=15,
                )
                body = result.stdout.strip()
                if body:
                    if len(body) > 10000:
                        body = body[:10000] + "\n... (truncated)"
                    self.history.append({
                        "role": "user",
                        "content": f"Here is GitHub issue #{arg.strip()}:\n\n{body}\n\nPlease analyze and help implement this."
                    })
                    self.console.print(f"[green]Issue #{arg.strip()} loaded into context.[/]")
                    self._dispatch_chat(self.history[-1]["content"])
                else:
                    self.console.print(f"[dim]No issue found: #{arg}[/]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")

    def _handle_init(self, arg):
        """Handle /init command for project templates."""
        try:
            from templates import TEMPLATES
        except ImportError:
            self.console.print("[dim]No templates module found.[/]")
            return
        if not arg:
            self.console.print("[bold]Available templates:[/]")
            for name, tmpl in TEMPLATES.items():
                self.console.print(f"  [cyan]{name}[/] — {tmpl.get('description', '')}")
            self.console.print("[dim]Usage: /init <template>[/]")
            return
        name = arg.strip().lower()
        if name not in TEMPLATES:
            self.console.print(f"[red]Unknown template: {name}. Use /init to list.[/]")
            return
        tmpl = TEMPLATES[name]
        files = tmpl.get("files", {})
        if not files:
            self.console.print("[dim]Template has no files.[/]")
            return
        # Confirm
        self.console.print(f"[bold]Template: {name}[/] — {len(files)} files")
        for fp in files:
            self.console.print(f"  [dim]{fp}[/]")
        options = [("Yes", f"Create {len(files)} files"), ("No", "")]
        choice = self._arrow_select(options, self.console, default=0)
        if choice != 0:
            self.console.print("[dim]Cancelled.[/]")
            return
        for fp, content in files.items():
            full = os.path.join(self.cwd, fp)
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            with open(full, "w") as f:
                f.write(content)
            self.console.print(f"  [green]●[/] {fp}")
        # Run setup commands
        for cmd in tmpl.get("commands", []):
            self.console.print(f"  [yellow]●[/] Running: {cmd}")
            try:
                subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60, cwd=self.cwd)
            except Exception:
                pass
        self.console.print(f"[green]Project initialized from template: {name}[/]")

    # ── Phase 3: Plugins ──

    def _load_plugins(self):
        """Scan ~/.kodiqa/plugins/ for custom tool plugins."""
        self._plugins = {}
        plugins_dir = os.path.join(KODIQA_DIR, "plugins")
        if not os.path.isdir(plugins_dir):
            return
        import importlib.util
        for fname in sorted(os.listdir(plugins_dir)):
            if not fname.endswith(".py"):
                continue
            name = fname[:-3]
            path = os.path.join(plugins_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(f"kodiqa_plugin_{name}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                schema = getattr(mod, "TOOL_SCHEMA", None)
                handler = getattr(mod, "handle", None)
                if schema and handler:
                    tool_name = f"plugin_{name}"
                    schema = dict(schema)
                    schema["name"] = tool_name
                    self._plugins[tool_name] = {"schema": schema, "handler": handler, "file": fname}
            except Exception as e:
                self.console.print(f"  [red]Plugin error ({fname}): {e}[/]")

    def _handle_plugins(self, arg):
        """Handle /plugins command."""
        if arg and arg.strip().lower() == "reload":
            self._load_plugins()
            self.console.print(f"[green]Reloaded {len(self._plugins)} plugins.[/]")
            return
        if not hasattr(self, '_plugins'):
            self._load_plugins()
        if not self._plugins:
            self.console.print("[dim]No plugins loaded. Add .py files to ~/.kodiqa/plugins/[/]")
            self.console.print("[dim]Each plugin needs TOOL_SCHEMA dict and handle(params) function.[/]")
            return
        self.console.print(f"[bold]Loaded plugins ({len(self._plugins)}):[/]")
        for name, info in self._plugins.items():
            desc = info["schema"].get("description", "")[:60]
            self.console.print(f"  [cyan]{name}[/] [dim]({info['file']})[/] — {desc}")

    # ── Phase 4: Sub-agents, LSP, Voice, Image Gen ──

    def _create_agent_worktree(self, agent_id):
        """Create a git worktree for isolated agent work."""
        worktree_dir = os.path.join(self.cwd, ".kodiqa_worktrees", agent_id)
        branch = f"kodiqa-{agent_id}"
        try:
            os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, worktree_dir],
                capture_output=True, text=True, timeout=10, cwd=self.cwd, check=True,
            )
            return worktree_dir
        except Exception as e:
            self.console.print(f"  [red]Worktree creation failed: {e}[/]")
            return None

    def _cleanup_agent_worktree(self, agent_id):
        """Remove the git worktree for an agent."""
        worktree_dir = os.path.join(self.cwd, ".kodiqa_worktrees", agent_id)
        branch = f"kodiqa-{agent_id}"
        try:
            subprocess.run(["git", "worktree", "remove", worktree_dir, "--force"],
                          capture_output=True, text=True, timeout=10, cwd=self.cwd)
            subprocess.run(["git", "branch", "-D", branch],
                          capture_output=True, text=True, timeout=5, cwd=self.cwd)
        except Exception:
            pass

    def _handle_agent(self, arg):
        """Spawn a sub-agent to handle a task."""
        if not arg:
            self.console.print("[dim]Usage: /agent <task description>[/]")
            self.console.print("[dim]  /agent --worktree <task> — run in isolated git worktree[/]")
            return
        use_worktree = False
        if arg.strip().startswith("--worktree"):
            use_worktree = True
            arg = arg.replace("--worktree", "", 1).strip()
        if not arg:
            self.console.print("[dim]Provide a task after --worktree[/]")
            return
        active = sum(1 for a in self._agents.values() if a.get("status") == "running")
        if active >= 3:
            self.console.print("[red]Max 3 concurrent agents. Wait for one to finish.[/]")
            return
        self._agent_counter += 1
        agent_id = f"agent_{self._agent_counter}"
        worktree_dir = None
        if use_worktree:
            worktree_dir = self._create_agent_worktree(agent_id)
            if not worktree_dir:
                self.console.print("[yellow]Falling back to shared workspace.[/]")
        self._agents[agent_id] = {
            "task": arg, "status": "running", "result": None,
            "worktree": worktree_dir,
        }
        wt_label = f" [dim](worktree)[/]" if worktree_dir else ""
        self.console.print(f"[green]●[/] Spawned {agent_id}: {arg[:60]}{wt_label}")

        def worker():
            try:
                wt_ctx = f"\nWorking directory: {worktree_dir}" if worktree_dir else ""
                task_prompt = f"Complete this task concisely:{wt_ctx}\n{arg}"
                # Use compact non-streaming query
                if is_claude_model(self.model) or self._is_live_claude(self.model):
                    result = self._claude_nostream(
                        task_prompt,
                        [{"role": "user", "content": arg}]
                    )
                else:
                    provider = self._get_provider_for_model(self.model)
                    if provider:
                        result = self._openai_compat_nostream(
                            task_prompt,
                            [{"role": "user", "content": arg}],
                            provider,
                        )
                    else:
                        resp = requests.post(
                            f"{OLLAMA_URL}/api/chat",
                            json={"model": self.model, "messages": [
                                {"role": "system", "content": task_prompt},
                                {"role": "user", "content": arg},
                            ], "stream": False},
                            timeout=120,
                        )
                        result = resp.json().get("message", {}).get("content", "No response")
                self._agents[agent_id]["result"] = result
                self._agents[agent_id]["status"] = "done"
            except Exception as e:
                self._agents[agent_id]["result"] = f"Error: {e}"
                self._agents[agent_id]["status"] = "error"
            finally:
                if worktree_dir:
                    # Show diff from worktree
                    try:
                        diff = subprocess.run(
                            ["git", "diff", "HEAD"],
                            capture_output=True, text=True, timeout=10, cwd=worktree_dir,
                        )
                        if diff.stdout.strip():
                            self._agents[agent_id]["worktree_diff"] = diff.stdout[:5000]
                    except Exception:
                        pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _handle_agents(self):
        """List running and completed agents."""
        if not self._agents:
            self.console.print("[dim]No agents. Use /agent <task> to spawn one.[/]")
            return
        for aid, info in self._agents.items():
            status = info["status"]
            color = {"running": "yellow", "done": "green", "error": "red"}.get(status, "dim")
            wt = " [dim](worktree)[/]" if info.get("worktree") else ""
            self.console.print(f"  [{color}]●[/] {aid} [{color}]{status}[/]{wt} — {info['task'][:50]}")
            if status in ("done", "error") and info.get("result"):
                result = info["result"]
                if len(result) > 500:
                    result = result[:500] + "..."
                self.console.print(Panel(result, title=aid, border_style=color))
            if info.get("worktree_diff"):
                self.console.print(f"  [dim]Worktree has changes. Use 'git merge kodiqa-{aid}' to merge.[/]")
        # Offer to inject completed results
        done = [(aid, info) for aid, info in self._agents.items() if info["status"] == "done" and info.get("result")]
        if done:
            self.console.print(f"\n[dim]{len(done)} completed. Results shown above.[/]")

    def _handle_team(self, arg):
        """Spawn a team: coordinator breaks task into subtasks, workers execute in parallel."""
        if not arg:
            self.console.print("[dim]Usage: /team <task description>[/]")
            self.console.print("[dim]  Coordinator splits task → workers execute → results merged[/]")
            return
        self._team_counter += 1
        team_id = f"team_{self._team_counter}"
        self._teams[team_id] = {
            "task": arg, "status": "planning", "subtasks": [], "final_result": None,
        }
        self.console.print(f"[green]●[/] Team {team_id}: {arg[:60]}")
        self.console.print(f"  [cyan]Coordinator planning...[/]")

        def team_worker():
            try:
                # Phase 1: Coordinator breaks task into subtasks
                plan_prompt = (
                    f"Break this task into 2-4 independent subtasks that can be done in parallel. "
                    f"Return ONLY a JSON array of subtask description strings, nothing else.\n\n"
                    f"Task: {arg}"
                )
                if is_claude_model(self.model) or self._is_live_claude(self.model):
                    plan_result = self._claude_nostream(plan_prompt, [{"role": "user", "content": plan_prompt}])
                else:
                    provider = self._get_provider_for_model(self.model)
                    if provider:
                        plan_result = self._openai_compat_nostream(plan_prompt, [{"role": "user", "content": plan_prompt}], provider)
                    else:
                        resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
                            "model": self.model, "messages": [{"role": "user", "content": plan_prompt}], "stream": False,
                        }, timeout=120)
                        plan_result = resp.json().get("message", {}).get("content", "[]")

                # Parse JSON subtasks from response
                import json as _json
                subtasks = []
                try:
                    # Find JSON array in response
                    match = re.search(r'\[.*\]', plan_result, re.DOTALL)
                    if match:
                        subtasks = _json.loads(match.group())
                except Exception:
                    subtasks = [arg]  # Fallback: single task

                if not subtasks:
                    subtasks = [arg]
                subtasks = subtasks[:4]  # Cap at 4

                self._teams[team_id]["status"] = "running"
                self._teams[team_id]["subtasks"] = [
                    {"task": st, "status": "pending", "result": None} for st in subtasks
                ]
                self.console.print(f"  [cyan]Team {team_id}: {len(subtasks)} subtasks planned[/]")
                for i, st in enumerate(subtasks):
                    self.console.print(f"    {i+1}. {st[:60]}")

                # Phase 2: Execute subtasks in parallel via threads
                import concurrent.futures
                def run_subtask(idx, task_desc):
                    self._teams[team_id]["subtasks"][idx]["status"] = "running"
                    try:
                        if is_claude_model(self.model) or self._is_live_claude(self.model):
                            r = self._claude_nostream(task_desc, [{"role": "user", "content": task_desc}])
                        else:
                            provider = self._get_provider_for_model(self.model)
                            if provider:
                                r = self._openai_compat_nostream(task_desc, [{"role": "user", "content": task_desc}], provider)
                            else:
                                resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
                                    "model": self.model, "messages": [{"role": "user", "content": task_desc}], "stream": False,
                                }, timeout=120)
                                r = resp.json().get("message", {}).get("content", "No result")
                        self._teams[team_id]["subtasks"][idx]["result"] = r
                        self._teams[team_id]["subtasks"][idx]["status"] = "done"
                        return r
                    except Exception as e:
                        self._teams[team_id]["subtasks"][idx]["result"] = f"Error: {e}"
                        self._teams[team_id]["subtasks"][idx]["status"] = "error"
                        return f"Error: {e}"

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(subtasks), 3)) as executor:
                    futures = {executor.submit(run_subtask, i, st): i for i, st in enumerate(subtasks)}
                    concurrent.futures.wait(futures)

                # Phase 3: Merge results
                self._teams[team_id]["status"] = "merging"
                results_text = "\n\n".join(
                    f"Subtask {i+1}: {st['task'][:80]}\nResult: {(st['result'] or 'No result')[:2000]}"
                    for i, st in enumerate(self._teams[team_id]["subtasks"])
                )
                merge_prompt = (
                    f"Merge these subtask results into a single coherent response.\n\n"
                    f"Original task: {arg}\n\n{results_text}"
                )
                if is_claude_model(self.model) or self._is_live_claude(self.model):
                    final = self._claude_nostream(merge_prompt, [{"role": "user", "content": merge_prompt}])
                else:
                    provider = self._get_provider_for_model(self.model)
                    if provider:
                        final = self._openai_compat_nostream(merge_prompt, [{"role": "user", "content": merge_prompt}], provider)
                    else:
                        resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
                            "model": self.model, "messages": [{"role": "user", "content": merge_prompt}], "stream": False,
                        }, timeout=120)
                        final = resp.json().get("message", {}).get("content", "No result")

                self._teams[team_id]["final_result"] = final
                self._teams[team_id]["status"] = "done"
                self.console.print(f"\n  [green]●[/] Team {team_id} complete!")

            except Exception as e:
                self._teams[team_id]["status"] = "error"
                self._teams[team_id]["final_result"] = f"Error: {e}"
                self.console.print(f"\n  [red]●[/] Team {team_id} error: {e}")

        t = threading.Thread(target=team_worker, daemon=True)
        t.start()

    def _handle_teams(self):
        """List all teams and their subtask status."""
        if not self._teams:
            self.console.print("[dim]No teams. Use /team <task> to spawn one.[/]")
            return
        for tid, info in self._teams.items():
            status = info["status"]
            color = {"planning": "cyan", "running": "yellow", "merging": "cyan", "done": "green", "error": "red"}.get(status, "dim")
            self.console.print(f"  [{color}]●[/] {tid} [{color}]{status}[/] — {info['task'][:50]}")
            for i, st in enumerate(info.get("subtasks", [])):
                sc = {"pending": "dim", "running": "yellow", "done": "green", "error": "red"}.get(st["status"], "dim")
                self.console.print(f"    [{sc}]●[/] Subtask {i+1}: {st['task'][:50]} [{sc}]{st['status']}[/]")
            if info.get("final_result"):
                result = info["final_result"]
                if len(result) > 500:
                    result = result[:500] + "..."
                self.console.print(Panel(result, title=f"{tid} result", border_style=color))

    def _handle_lsp(self, arg):
        """Handle /lsp command for Language Server Protocol."""
        try:
            from lsp import LSPClient
        except ImportError:
            self.console.print("[dim]LSP module not available yet.[/]")
            return
        parts = arg.strip().split() if arg else []
        sub = parts[0] if parts else ""

        if sub == "start":
            lang = parts[1] if len(parts) > 1 else self._detect_project_language()
            if not lang:
                self.console.print("[dim]Usage: /lsp start <python|typescript|go>[/]")
                return
            try:
                self._lsp_client = LSPClient()
                self._lsp_client.start(lang, self.cwd)
                self.console.print(f"[green]LSP started:[/] {lang}")
            except Exception as e:
                self.console.print(f"[red]LSP start error: {e}[/]")
                self._lsp_client = None
        elif sub == "stop":
            if self._lsp_client:
                self._lsp_client.stop()
                self._lsp_client = None
                self.console.print("[yellow]LSP stopped.[/]")
            else:
                self.console.print("[dim]No LSP server running.[/]")
        else:
            if self._lsp_client:
                self.console.print(f"[green]LSP running:[/] {self._lsp_client.language}")
            else:
                self.console.print("[dim]No LSP server. Usage: /lsp start <language>[/]")

    def _detect_project_language(self):
        """Detect primary project language from files."""
        counts = {}
        for f in os.listdir(self.cwd):
            ext = os.path.splitext(f)[1]
            if ext in (".py",):
                counts["python"] = counts.get("python", 0) + 1
            elif ext in (".ts", ".tsx", ".js", ".jsx"):
                counts["typescript"] = counts.get("typescript", 0) + 1
            elif ext in (".go",):
                counts["go"] = counts.get("go", 0) + 1
        if counts:
            return max(counts, key=counts.get)
        return None

    def _handle_voice(self, arg):
        """Handle /voice command for speech-to-text input."""
        # Check for sox
        try:
            subprocess.run(["sox", "--version"], capture_output=True, timeout=5, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.console.print("[red]sox not installed.[/] Install: [cyan]brew install sox[/]")
            return

        tmp_wav = os.path.join("/tmp", "kodiqa_voice.wav")
        self.console.print("[bold cyan]Recording...[/] (press Ctrl+C to stop, max 30s)")
        try:
            proc = subprocess.Popen(
                ["rec", "-q", tmp_wav, "trim", "0", "30"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            time.sleep(0.3)
        except Exception as e:
            self.console.print(f"[red]Recording error: {e}[/]")
            return

        if not os.path.isfile(tmp_wav):
            self.console.print("[dim]No recording captured.[/]")
            return

        # Transcribe — try OpenAI Whisper API first
        text = None
        openai_key = self.api_keys.get("openai", "")
        if openai_key:
            try:
                with open(tmp_wav, "rb") as f:
                    resp = requests.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        files={"file": ("voice.wav", f, "audio/wav")},
                        data={"model": "whisper-1"},
                        timeout=30,
                    )
                    if resp.ok:
                        text = resp.json().get("text", "")
            except Exception:
                pass

        if not text:
            self.console.print("[dim]Transcription failed. Need OpenAI API key for Whisper.[/]")
            try:
                os.remove(tmp_wav)
            except OSError:
                pass
            return

        self.console.print(f"[green]Transcribed:[/] {text}")
        try:
            os.remove(tmp_wav)
        except OSError:
            pass
        # Use as prompt
        if text.strip():
            self._chat(text.strip())

    # ── v3.0 Feature Handlers ──

    def _handle_profile(self, arg):
        profile_dir = os.path.join(KODIQA_DIR, "profiles")
        os.makedirs(profile_dir, exist_ok=True)
        parts = arg.split(None, 1) if arg else []
        sub = parts[0] if parts else ""
        name = parts[1].strip() if len(parts) > 1 else ""
        if sub == "save":
            if not name:
                self.console.print("[dim]Usage: /profile save <name>[/]")
                return
            from config import THEMES
            theme_name = next((k for k, v in THEMES.items() if v == self.theme), "dark")
            profile = {
                "model": self.model, "permission_mode": self.permission_mode,
                "theme": theme_name, "persona": self._persona,
                "compact_mode": self.compact_mode, "batch_edits": self.batch_edits,
                "auto_commit": self.auto_commit, "lint_cmd": self.lint_cmd,
                "optimizer": self._optimizer_enabled, "notify": self._notify_enabled,
            }
            path = os.path.join(profile_dir, f"{name}.json")
            with open(path, "w") as f:
                json.dump(profile, f, indent=2)
            self.console.print(f"[green]Profile '{name}' saved.[/]")
        elif sub == "load":
            if not name:
                self.console.print("[dim]Usage: /profile load <name>[/]")
                return
            path = os.path.join(profile_dir, f"{name}.json")
            if not os.path.isfile(path):
                self.console.print(f"[red]Profile not found: {name}[/]")
                return
            with open(path, "r") as f:
                profile = json.load(f)
            self.model = profile.get("model", self.model)
            self.permission_mode = profile.get("permission_mode", "default")
            self.compact_mode = profile.get("compact_mode", True)
            self.batch_edits = profile.get("batch_edits", True)
            self.auto_commit = profile.get("auto_commit", False)
            self.lint_cmd = profile.get("lint_cmd", "")
            self._optimizer_enabled = profile.get("optimizer", False)
            self._notify_enabled = profile.get("notify", False)
            self._persona = profile.get("persona")
            from config import THEMES
            self.theme = THEMES.get(profile.get("theme", "dark"), THEMES["dark"])
            self.console.print(f"[green]Profile '{name}' loaded.[/] Model: [cyan]{self.model}[/]")
        elif sub == "list":
            try:
                profiles = [f[:-5] for f in os.listdir(profile_dir) if f.endswith(".json")]
            except OSError:
                profiles = []
            if profiles:
                self.console.print("[bold]Saved profiles:[/]")
                for p in sorted(profiles):
                    self.console.print(f"  [cyan]{p}[/]")
            else:
                self.console.print("[dim]No profiles saved. Use /profile save <name>[/]")
        elif sub == "delete":
            if not name:
                self.console.print("[dim]Usage: /profile delete <name>[/]")
                return
            path = os.path.join(profile_dir, f"{name}.json")
            if os.path.isfile(path):
                os.remove(path)
                self.console.print(f"[yellow]Profile '{name}' deleted.[/]")
            else:
                self.console.print(f"[dim]Profile not found: {name}[/]")
        else:
            self.console.print("[dim]Usage: /profile save|load|list|delete <name>[/]")

    def _handle_history(self, arg):
        history_dir = os.path.join(KODIQA_DIR, "history")
        index_file = os.path.join(history_dir, "index.json")
        if not os.path.isfile(index_file):
            self.console.print("[dim]No session history yet.[/]")
            return
        with open(index_file, "r") as f:
            index = json.load(f)
        parts = arg.split(None, 1) if arg else []
        sub = parts[0] if parts else ""
        if sub == "resume" and len(parts) > 1:
            session_id = parts[1].strip()
            session_file = os.path.join(history_dir, f"session_{session_id}.json")
            if not os.path.isfile(session_file):
                self.console.print(f"[red]Session {session_id} not found.[/]")
                return
            with open(session_file, "r") as f:
                data = json.load(f)
            self.history = data.get("history", [])
            self.console.print(f"[green]Resumed session {session_id} ({len(self.history)} messages)[/]")
        else:
            self.console.print("[bold]Recent Sessions:[/]")
            for entry in reversed(index[-20:]):
                topic = entry.get("topic", "")[:60]
                ts = entry.get("timestamp", "")[:16]
                cost = entry.get("cost", 0)
                model = entry.get("model", "?")
                self.console.print(
                    f"  [cyan]#{entry.get('id', '?')}[/] {ts} [dim]{model}[/] "
                    f"[dim]{entry.get('user_messages', 0)} msgs[/] "
                    f"{'$' + f'{cost:.3f}' if cost > 0 else ''} "
                    f"[dim]{topic}[/]"
                )
            self.console.print("\n[dim]Usage: /history resume <id>[/]")

    def _handle_watch(self, arg):
        parts = arg.split(None, 1) if arg else []
        sub = parts[0] if parts else ""
        if sub == "stop":
            name = parts[1].strip() if len(parts) > 1 else ""
            if name and name in self._watchers:
                self._watchers[name]["active"] = False
                del self._watchers[name]
                self.console.print(f"[yellow]Stopped watching: {name}[/]")
            elif not name:
                for w in self._watchers.values():
                    w["active"] = False
                self._watchers.clear()
                self.console.print("[yellow]All watchers stopped.[/]")
            return
        if sub == "list":
            if self._watchers:
                for name, w in self._watchers.items():
                    self.console.print(f"  [green]\u25cf[/] {name} \u2014 {w['path']}")
            else:
                self.console.print("[dim]No active watchers.[/]")
            return
        if not arg:
            self.console.print("[dim]Usage: /watch <path> | /watch stop | /watch list[/]")
            return
        path = os.path.abspath(os.path.expanduser(arg.strip()))
        if not os.path.exists(path):
            self.console.print(f"[red]Path not found: {arg}[/]")
            return
        name = os.path.basename(path)
        if name in self._watchers:
            self.console.print(f"[dim]Already watching: {name}[/]")
            return
        watcher = {"path": path, "active": True, "last_mtime": {}}
        self._watchers[name] = watcher
        def poll_changes():
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            watcher["last_mtime"][fp] = os.path.getmtime(fp)
                        except OSError:
                            pass
            else:
                try:
                    watcher["last_mtime"][path] = os.path.getmtime(path)
                except OSError:
                    pass
            while watcher["active"]:
                time.sleep(2)
                changed = []
                for fp, old_mtime in list(watcher["last_mtime"].items()):
                    try:
                        new_mtime = os.path.getmtime(fp)
                        if new_mtime > old_mtime:
                            watcher["last_mtime"][fp] = new_mtime
                            changed.append(fp)
                    except OSError:
                        pass
                if changed:
                    rel_paths = [os.path.relpath(p, self.cwd) for p in changed]
                    self.console.print(f"\n  [cyan]\u27f3 Files changed:[/] {', '.join(rel_paths)}")
                    # Scan for #AI: triggers in changed files
                    for fp in changed:
                        triggers = self._scan_ai_triggers(fp)
                        for line_num, instruction in triggers:
                            rel = os.path.relpath(fp, self.cwd)
                            self.console.print(f"\n  [magenta]\u26a1 AI trigger found:[/] {rel}:{line_num} — {instruction}")
                            self._ai_trigger_queue.append({
                                "file": fp, "line": line_num, "instruction": instruction,
                            })
        t = threading.Thread(target=poll_changes, daemon=True)
        t.start()
        self.console.print(f"[green]Watching:[/] {path} [dim](use /watch stop to end)[/]")

    def _scan_ai_triggers(self, filepath):
        """Scan file for #AI: or //AI: comments. Returns [(line_num, instruction)]."""
        import re
        triggers = []
        try:
            with open(filepath, 'r', errors='replace') as f:
                for i, line in enumerate(f, 1):
                    m = re.search(r'(?:#|//|/\*)\s*AI:\s*(.+?)(?:\*/)?$', line)
                    if m:
                        triggers.append((i, m.group(1).strip()))
        except Exception:
            pass
        return triggers

    def _remove_ai_trigger(self, filepath, line_num):
        """Remove the AI trigger comment from the specified line."""
        import re
        try:
            with open(filepath, 'r', errors='replace') as f:
                lines = f.readlines()
            if 1 <= line_num <= len(lines):
                line = lines[line_num - 1]
                cleaned = re.sub(r'\s*(?:#|//|/\*)\s*AI:\s*.+?(?:\*/)?\s*$', '', line)
                if cleaned.strip():
                    lines[line_num - 1] = cleaned.rstrip() + '\n'
                else:
                    lines.pop(line_num - 1)
                with open(filepath, 'w') as f:
                    f.writelines(lines)
        except Exception:
            pass

    def _handle_embed(self, arg):
        try:
            from embeddings import EmbeddingStore
        except ImportError:
            self.console.print("[red]embeddings module not found.[/]")
            return
        db_path = os.path.join(KODIQA_DIR, "embeddings.db")
        store = EmbeddingStore(db_path)
        target = os.path.abspath(arg.strip()) if arg else self.cwd
        if self.api_keys.get("openai"):
            embed_fn = lambda t: store.embed_openai(t, self.api_keys["openai"])
            self.console.print("[dim]Using OpenAI embeddings[/]")
        else:
            embed_fn = store.embed_ollama
            self.console.print("[dim]Using Ollama embeddings (nomic-embed-text)[/]")
        count = 0
        try:
            with Status("Embedding files...", console=self.console, spinner="dots") as status:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    for fname in files:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in SKIP_EXTENSIONS:
                            continue
                        fpath = os.path.join(root, fname)
                        if os.path.getsize(fpath) > MAX_FILE_SIZE:
                            continue
                        try:
                            store.index_file(fpath, embed_fn)
                            count += 1
                            status.update(f"Embedding... {count} files")
                        except Exception:
                            pass
        except Exception as e:
            self.console.print(f"[red]Embedding error: {e}[/]")
        store.close()
        self.console.print(f"[green]Embedded {count} files.[/]")

    def _handle_rag(self, query):
        try:
            from embeddings import EmbeddingStore
        except ImportError:
            self.console.print("[red]embeddings module not found.[/]")
            return
        db_path = os.path.join(KODIQA_DIR, "embeddings.db")
        if not os.path.isfile(db_path):
            self.console.print("[yellow]No embeddings yet. Run /embed first.[/]")
            return
        store = EmbeddingStore(db_path)
        try:
            if self.api_keys.get("openai"):
                query_emb = store.embed_openai(query, self.api_keys["openai"])
            else:
                query_emb = store.embed_ollama(query)
            results = store.search(query_emb, top_k=5)
        except Exception as e:
            self.console.print(f"[red]RAG error: {e}[/]")
            store.close()
            return
        store.close()
        if not results:
            self.console.print("[dim]No relevant results found.[/]")
            return
        context_parts = []
        for score, path, text, start, end in results:
            rel = os.path.relpath(path, self.cwd)
            context_parts.append(f"### {rel} (lines {start}-{end}, relevance: {score:.2f})\n```\n{text}\n```")
        context = "\n\n".join(context_parts)
        self._chat(f"Based on these relevant code sections:\n\n{context}\n\nAnswer this question: {query}")

    def _handle_test_fix(self, arg):
        """Run tests, if failures send to AI for fix, re-run. Max 3 iterations."""
        if not arg:
            self.console.print("[dim]Usage: /test-fix <test_command>[/]")
            self.console.print("[dim]  Example: /test-fix pytest -v tests/[/]")
            return
        cmd = arg.strip()
        max_iter = 3
        for i in range(max_iter):
            self.console.print(f"[cyan]Test-fix iteration {i + 1}/{max_iter}...[/]")
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd=self.cwd)
                output = (result.stdout + result.stderr).strip()
            except subprocess.TimeoutExpired:
                self.console.print("[red]Tests timed out (120s).[/]")
                return
            except Exception as e:
                self.console.print(f"[red]Error running tests: {e}[/]")
                return
            if result.returncode == 0:
                self.console.print(f"[green]All tests passing![/]")
                if output:
                    lines = output.split("\n")
                    summary = "\n".join(lines[-10:]) if len(lines) > 10 else output
                    self.console.print(Panel(summary[:1000], title="Test Output", border_style="green"))
                return
            self.console.print(f"[red]Tests failed (exit {result.returncode}).[/]")
            if i == max_iter - 1:
                self.console.print("[yellow]Max iterations reached. Tests still failing.[/]")
                if output:
                    self.console.print(Panel(output[:1000], title="Last Output", border_style="yellow"))
                return
            self._chat(
                f"Tests are failing. Fix the code to make them pass.\n\n"
                f"Command: {cmd}\nExit code: {result.returncode}\n\n"
                f"Test output:\n```\n{output[:5000]}\n```\n\n"
                f"Read the relevant source files, identify the failures, and fix them."
            )

    def _handle_debug(self, arg):
        parts = arg.split()
        script = parts[0]
        ext = os.path.splitext(script)[1]
        runners = {".py": "python", ".js": "node", ".ts": "npx tsx", ".rb": "ruby", ".go": "go run"}
        runner = runners.get(ext, "")
        cmd = f"{runner} {arg}" if runner else arg
        self.console.print(f"[cyan]Running:[/] {cmd}")
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=self.cwd)
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            self.console.print("[red]Script timed out (30s).[/]")
            return
        except Exception as e:
            self.console.print(f"[red]Error running script: {e}[/]")
            return
        if exit_code == 0:
            self.console.print(f"[green]Script ran successfully (exit 0).[/]")
            if stdout:
                self.console.print(Panel(stdout[:2000], title="Output", border_style="green"))
            return
        self.console.print(f"[red]Script failed (exit {exit_code}).[/]")
        if stderr:
            self.console.print(Panel(stderr[:1000], title="Error", border_style="red"))
        error_context = f"stdout:\n{stdout[:3000]}\n\nstderr:\n{stderr[:3000]}"
        self._chat(
            f"Debug this script. It failed with exit code {exit_code}.\n\n"
            f"Script: {script}\nCommand: {cmd}\n\n"
            f"Output:\n```\n{error_context}\n```\n\n"
            f"Please:\n1. Read the script file to understand the code\n"
            f"2. Analyze the error and identify the root cause\n"
            f"3. Suggest and implement a fix"
        )

    # ── Repo Map ──

    def _handle_map(self, arg):
        """Build and display repository map with symbol extraction."""
        from rich.status import Status
        try:
            from repomap import RepoMap
        except ImportError:
            self.console.print("[red]repomap module not found.[/]")
            return
        path = os.path.abspath(os.path.expanduser(arg.strip())) if arg else self.cwd
        with Status("Building repo map...", console=self.console, spinner="dots"):
            rmap = RepoMap(path, SKIP_DIRS, SKIP_EXTENSIONS)
            rmap.build_map()
        output = rmap.format_map()
        if not rmap._has_treesitter:
            self.console.print("[dim]Using regex extraction. Install tree-sitter for better results:[/]")
            self.console.print("[dim]  pip install tree-sitter tree-sitter-languages[/]")
        self.console.print(Panel(output[:5000], title="Repo Map", border_style="cyan"))

    # ── Diagram Detection ──

    def _render_diagrams(self, text):
        """Detect and render mermaid/matplotlib code blocks in AI response."""
        import re
        # Mermaid blocks
        mermaid_blocks = re.findall(r'```mermaid\n(.*?)```', text, re.DOTALL)
        for i, block in enumerate(mermaid_blocks):
            try:
                subprocess.run(["mmdc", "--version"], capture_output=True, timeout=5, check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                break  # mmdc not installed
            tmp_in = f"/tmp/kodiqa_mermaid_{i}.mmd"
            tmp_out = f"/tmp/kodiqa_mermaid_{i}.png"
            try:
                with open(tmp_in, "w") as f:
                    f.write(block)
                subprocess.run(
                    ["mmdc", "-i", tmp_in, "-o", tmp_out, "-t", "dark", "-b", "transparent"],
                    capture_output=True, timeout=30,
                )
                if os.path.isfile(tmp_out):
                    self.console.print(f"  [dim]Diagram saved: {tmp_out}[/]")
            except Exception:
                pass

    @staticmethod
    def _arrow_select(options, console, default=0):
        """Arrow-key selector. Returns index of chosen option. Options: list of (label, description)."""
        import tty, termios
        selected = default
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        n = len(options)
        out_fd = sys.stdout.fileno()  # write directly to fd, bypass Rich wrapper

        def _write(s):
            os.write(out_fd, s.encode())

        def render():
            for i, (label, desc) in enumerate(options):
                if i == selected:
                    line = f"    \033[1;36m❯\033[0m \033[1m{label}\033[0m"
                    if desc:
                        line += f" \033[2m{desc}\033[0m"
                else:
                    line = f"      \033[2m{label}"
                    if desc:
                        line += f" — {desc}"
                    line += "\033[0m"
                _write(f"\r\033[K{line}\n")

        try:
            render()
            tty.setcbreak(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch == "\r" or ch == "\n":  # Enter
                    break
                elif ch == "\x03":  # Ctrl+C
                    selected = len(options) - 1  # select last (usually No/cancel)
                    break
                elif ch == "\x1b":  # Escape sequence
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":  # Up
                            selected = (selected - 1) % n
                        elif ch3 == "B":  # Down
                            selected = (selected + 1) % n
                elif ch == "k":  # vim up
                    selected = (selected - 1) % n
                elif ch == "j":  # vim down
                    selected = (selected + 1) % n
                elif ch in ("1", "2", "3", "4", "5"):
                    idx = int(ch) - 1
                    if idx < n:
                        selected = idx
                        break
                # Move cursor up N lines, re-render in place
                _write(f"\033[{n}A")
                render()
        except (EOFError, OSError):
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return selected

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
            options = [
                ("Yes", ""),
                ("Yes, don't ask again", "for this action type"),
                ("No", ""),
            ]
            choice = self._arrow_select(options, self.console, default=0)
            if choice == 0:
                return True
            elif choice == 1:
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
    import argparse
    parser = argparse.ArgumentParser(description="Kodiqa — AI coding agent")
    parser.add_argument("--headless", type=str, metavar="TASK", help="Run non-interactively with given task")
    parser.add_argument("--model", type=str, metavar="MODEL", help="Model to use")
    parser.add_argument("--output", type=str, metavar="FILE", help="Output file for headless mode")
    args = parser.parse_args()

    kodiqa = Kodiqa()
    if args.model:
        kodiqa.model = kodiqa._resolve_model_name(args.model)
    if args.headless:
        kodiqa.run_headless(args.headless, args.output)
    else:
        kodiqa.run()


if __name__ == "__main__":
    main()
