#!/usr/bin/env python3
"""Kodiqa - Local AI coding agent. Claude native tools + Ollama text-based actions."""

import json
import os
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

from config import (
    OLLAMA_URL, DEFAULT_MODEL, MODEL_ALIASES, CLAUDE_ALIASES,
    CLAUDE_API_URL, CONTEXT_FILE, KODIQA_DIR,
    MAX_ITERATIONS, SYSTEM_PROMPT, SKIP_DIRS, SKIP_EXTENSIONS, MAX_FILE_SIZE,
    load_settings, save_settings, is_claude_model,
)
from memory import MemoryStore
from actions import parse_actions, execute_action, execute_tool_call, execute_tools_parallel, set_console
from tools import CLAUDE_TOOLS

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


class Kodiqa:
    def __init__(self):
        self.console = Console()
        set_console(self.console)  # share console with actions.py for diff display
        self.memory = MemoryStore()
        self.history = []
        self.cwd = os.getcwd()
        self.settings = load_settings()
        self.claude_key = self.settings.get("claude_api_key", "")
        self.session_file = os.path.join(KODIQA_DIR, "session.json")
        self.multi_models = self._discover_models()  # default: multi-model mode
        if self.claude_key:
            self.model = self.settings.get("default_model", "claude-sonnet-4-20250514")
        else:
            self.model = self.settings.get("default_model", DEFAULT_MODEL)

    def _discover_models(self):
        """Auto-discover all installed Ollama models for multi-mode default."""
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return models if models else []
        except Exception:
            return []

    def run(self):
        self._first_run_setup()
        self._detect_git()
        self._load_session()
        self._welcome()
        self._check_updates()
        try:
            while True:
                try:
                    user_input = Prompt.ask("\n[bold cyan]You[/]")
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
        self.git_info = info

    def _git_context(self):
        """Format git info for system prompt."""
        if not self.git_info:
            return ""
        g = self.git_info
        lines = [f"## Git Repository"]
        lines.append(f"- Branch: {g['branch']}")
        if g["changed_files"]:
            lines.append(f"- Uncommitted changes: {g['changed_files']} files")
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
        provider = "[yellow]Claude API[/]" if is_claude_model(self.model) else "[green]Local/Ollama[/]"
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
        self.console.print("\n[dim]Goodbye! Session saved.[/]")

    def _check_updates(self):
        """Check for model updates and new models on startup."""
        try:
            # Get installed models
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            resp.raise_for_status()
            installed = {m["name"]: m for m in resp.json().get("models", [])}
        except Exception:
            return  # Ollama not running, skip silently

        if not installed:
            return

        # 1. Check installed models for updates
        updates_available = []
        for model_name in list(installed.keys()):
            try:
                # Pull with dry-run style: check manifest
                resp = requests.post(
                    f"{OLLAMA_URL}/api/show",
                    json={"name": model_name},
                    timeout=5,
                )
            except Exception:
                continue

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
        for model, desc in new_models[:6]:  # Show max 6
            self.console.print(f"  [dim]•[/] [cyan]{model}[/] — {desc}")
        if len(new_models) > 6:
            self.console.print(f"  [dim]... and {len(new_models) - 6} more[/]")

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
            to_pull = [m for m, _ in new_models[:6]]
        else:
            # Parse numbers or model names
            parts = answer.replace(",", " ").split()
            for part in parts:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(new_models[:6]):
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
                    ["ollama", "pull", model],
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
            self.console.print(Panel(
                "[bold]/model <name>[/]  - Switch model\n"
                "  [dim]Local: fast, qwen, coder, reason, gpt[/]\n"
                f"  [dim]Claude: claude, sonnet, haiku, opus ({claude_status})[/]\n"
                "[bold]/multi <models>[/] - Multi-model mode (e.g. /multi coder qwen reason)\n"
                "[bold]/single[/]        - Back to single model mode\n"
                "[bold]/models[/]       - List all available models\n"
                "[bold]/scan[/] [path]   - Scan project into context\n"
                "[bold]/clear[/]         - Clear conversation\n"
                "[bold]/memories[/]      - Show stored memories\n"
                "[bold]/forget <id>[/]   - Delete a memory\n"
                "[bold]/compact[/]       - Summarize conversation to save context\n"
                "[bold]/context[/]       - Show project context file\n"
                "[bold]/key[/]           - Add/update Claude API key\n"
                "[bold]/cd <path>[/]     - Change working directory\n"
                "[bold]/quit[/]          - Exit",
                title="Commands", border_style="blue",
            ))
        elif command == "/model":
            if not arg:
                provider = "Claude API" if is_claude_model(self.model) else "Local/Ollama"
                self.console.print(f"Current model: [cyan]{self.model}[/] ({provider})")
                self.console.print(f"Local aliases: {', '.join(MODEL_ALIASES.keys())}")
                if self.claude_key:
                    self.console.print(f"Claude aliases: {', '.join(CLAUDE_ALIASES.keys())}")
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
            elif arg in MODEL_ALIASES:
                new_model = MODEL_ALIASES[arg]
            else:
                new_model = arg
            self.model = new_model
            self.multi_models = []  # switch to single mode
            provider = "[yellow]Claude API[/]" if is_claude_model(self.model) else "[green]Local[/]"
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
            self._setup_api_key()
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
        else:
            self.console.print(f"[red]Unknown command: {command}. Type /help[/]")

    def _setup_api_key(self):
        if self.claude_key:
            masked = self.claude_key[:10] + "..." + self.claude_key[-4:]
            self.console.print(f"Current key: [dim]{masked}[/]")
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

    def _list_models(self):
        lines = []
        if self.claude_key:
            lines.append("[bold yellow]Claude API Models:[/]")
            for alias, model in CLAUDE_ALIASES.items():
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
        self.console.print(f"[dim]Scanning {path}...[/]")
        files_content = []
        total_chars = 0
        file_count = 0
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
                    files_content.append(f"### {rel}\n```\n{content}\n```")
                    total_chars += len(content)
                    file_count += 1
                    if total_chars > 500_000:
                        files_content.append("... (stopped, context limit)")
                        break
                except (PermissionError, OSError):
                    continue
            if total_chars > 500_000:
                break
        if not files_content:
            self.console.print("[yellow]No readable files found.[/]")
            return
        scan_text = f"Project scan of {path} ({file_count} files):\n\n" + "\n\n".join(files_content)
        self.history.append({"role": "user", "content": f"[Project scan of {path}]"})
        self.history.append({"role": "assistant", "content": scan_text})
        self.console.print(f"[green]Scanned {file_count} files ({total_chars:,} chars) into context.[/]")

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
        """Rough token estimate: ~4 chars per token."""
        total = sum(len(m.get("content", "")) for m in self.history if isinstance(m.get("content"), str))
        # Also count tool results in content blocks
        for m in self.history:
            if isinstance(m.get("content"), list):
                for block in m["content"]:
                    if isinstance(block, dict):
                        total += len(str(block.get("content", "")))
        return total // 4

    def _auto_compact_if_needed(self):
        """Auto-compact when context exceeds ~80K tokens."""
        tokens = self._estimate_tokens()
        if tokens > 80000:
            self.console.print(f"[dim]Context large (~{tokens:,} tokens). Auto-compacting...[/]")
            self._compact()

    # ── Main chat dispatch ──

    def _chat(self, user_msg):
        self._auto_compact_if_needed()
        if self.multi_models:
            self._chat_multi(user_msg)
        elif is_claude_model(self.model):
            self._chat_claude(user_msg)
        else:
            self._chat_ollama(user_msg)

    # ── Multi-model chat ──

    def _chat_multi(self, user_msg):
        """Send message to all selected models and show each response."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        memories_ctx = self.memory.get_context()
        context_file_ctx = self._load_context_file()

        def query_model(model_name):
            """Query a single model (no streaming, no tools - just chat)."""
            if is_claude_model(model_name):
                return self._multi_query_claude(model_name, user_msg, memories_ctx, context_file_ctx)
            else:
                return self._multi_query_ollama(model_name, user_msg, memories_ctx, context_file_ctx)

        self.console.print(f"\n[dim]Querying {len(self.multi_models)} models...[/]\n")

        # Run all models in parallel
        results = {}
        with ThreadPoolExecutor(max_workers=len(self.multi_models)) as executor:
            futures = {executor.submit(query_model, m): m for m in self.multi_models}
            for future in as_completed(futures):
                model_name = futures[future]
                try:
                    results[model_name] = future.result()
                except Exception as e:
                    results[model_name] = f"Error: {e}"

        # Display individual results
        for model_name in self.multi_models:
            response = results.get(model_name, "No response")
            is_claude = is_claude_model(model_name)
            color = "yellow" if is_claude else "green"
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
                json={"model": model_name, "messages": messages, "stream": False},
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
            messages = [{"role": "system", "content": system_prompt}] + self.history

            assistant_text = self._stream_ollama(messages)
            if assistant_text is None:
                return
            self.history.append({"role": "assistant", "content": assistant_text})

            actions = parse_actions(assistant_text)
            if not actions:
                self._save_session()
                break
            results = []
            for action in actions:
                action_label = _tool_label(action['name'], action.get('params', {}))
                with Status(f"  [yellow]●[/] {action_label}", console=self.console, spinner="dots"):
                    result = execute_action(action, self.memory, self._confirm)
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results.append(f"[Result of {action['name']}]\n{result}")
                self.console.print(f"  [green]●[/] {action_label}")
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

            # Execute tools - parallel for read-only, sequential for writes
            if len(tool_calls) > 1:
                with Status(f"  [yellow]●[/] Running {len(tool_calls)} tools...", console=self.console, spinner="dots"):
                    results_list = execute_tools_parallel(tool_calls, self.memory, self._confirm)
                for tc_id, result in results_list:
                    tc_name = next((tc["name"] for tc in tool_calls if tc["id"] == tc_id), "?")
                    tc_input = next((tc.get("input", {}) for tc in tool_calls if tc["id"] == tc_id), {})
                    self.console.print(f"  [green]●[/] {_tool_label(tc_name, tc_input)}")
            else:
                results_list = []
                tc = tool_calls[0]
                label = _tool_label(tc['name'], tc.get('input', {}))
                with Status(f"  [yellow]●[/] {label}", console=self.console, spinner="dots"):
                    result = execute_tool_call(tc["name"], tc["input"], self.memory, self._confirm)
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results_list.append((tc["id"], result))
                self.console.print(f"  [green]●[/] {label}")

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
        """Stream Claude API with native tool_use support. Returns parsed response."""
        if not self.claude_key:
            self.console.print("[red]No Claude API key. Use /key to add one.[/]")
            return None

        try:
            resp = requests.post(
                CLAUDE_API_URL,
                headers={
                    "x-api-key": self.claude_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 8192,
                    "system": system_prompt,
                    "messages": messages,
                    "tools": CLAUDE_TOOLS,
                    "stream": True,
                },
                stream=True,
                timeout=300,
            )
            if resp.status_code == 401:
                self.console.print("[red]Invalid Claude API key. Use /key to update it.[/]")
                return None
            if resp.status_code == 429:
                self.console.print("[red]Claude rate limit hit. Wait a moment and try again.[/]")
                return None
            if resp.status_code >= 400:
                self.console.print(f"[red]Claude API error {resp.status_code}: {resp.text[:200]}[/]")
                return None
            resp.raise_for_status()
        except requests.ConnectionError:
            self.console.print("[red]Can't connect to Claude API. Check your internet.[/]")
            return None
        except Exception as e:
            self.console.print(f"[red]Claude error: {e}[/]")
            return None

        # Parse streaming response
        self.console.print()

        full_text = []
        tool_calls = []
        current_tool = None
        current_tool_json = []
        stop_reason = "end_turn"
        first_token = True
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

                if event_type == "content_block_start":
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
                            sys.stdout.write(token)
                            sys.stdout.flush()
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

                elif event_type == "error":
                    thinking_status.stop()
                    err = event.get("error", {})
                    self.console.print(f"\n[red]Claude error: {err.get('message', 'Unknown')}[/]")

        except KeyboardInterrupt:
            thinking_status.stop()
            self.console.print("\n[dim](interrupted)[/]")

        if first_token:
            thinking_status.stop()
        self.console.print()
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

    # ── Ollama streaming ──

    def _stream_ollama(self, messages):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": self.model, "messages": messages, "stream": True},
                stream=True, timeout=300,
            )
            resp.raise_for_status()
        except requests.ConnectionError:
            self.console.print("[red]Can't connect to Ollama. Is it running?[/]")
            return None
        except Exception as e:
            self.console.print(f"[red]Ollama error: {e}[/]")
            return None

        self.console.print()
        full_text = []
        first_token = True
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
                    sys.stdout.write(token)
                    sys.stdout.flush()
        except KeyboardInterrupt:
            thinking_status.stop()
            self.console.print("\n[dim](interrupted)[/]")
        if first_token:
            thinking_status.stop()
        self.console.print()
        return "".join(full_text)

    # ── Shared ──

    def _confirm(self, description):
        self.console.print()
        try:
            answer = Prompt.ask(f"  [bold yellow]Allow:[/] {description}", choices=["y", "n"], default="y")
            return answer.lower() == "y"
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
        "ask_user": lambda: f"Ask user",
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
