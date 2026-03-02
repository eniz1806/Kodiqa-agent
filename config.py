"""Kodiqa configuration - models, prompts, constants."""

import json
import os

OLLAMA_URL = "http://localhost:11434"
OLLAMA_BIN = "/Applications/Ollama.app/Contents/Resources/ollama"
DEFAULT_MODEL = "qwen3-coder"

# Local Ollama models
MODEL_ALIASES = {
    "fast": "qwen3:30b-a3b",
    "qwen": "qwen3:14b",
    "coder": "qwen3-coder",
    "reason": "phi4-reasoning",
    "gpt-local": "gpt-oss",
}

# Claude API models
CLAUDE_ALIASES = {
    # Latest (4.6)
    "claude": "claude-sonnet-4-6",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    # Explicit version aliases
    "opus-4.6": "claude-opus-4-6",
    "sonnet-4.6": "claude-sonnet-4-6",
    "haiku-4.5": "claude-haiku-4-5-20251001",
    # Legacy
    "sonnet-4.5": "claude-sonnet-4-5-20250929",
    "opus-4.5": "claude-opus-4-5-20251101",
    "opus-4.1": "claude-opus-4-1-20250805",
    "sonnet-4": "claude-sonnet-4-20250514",
    "opus-4": "claude-opus-4-20250514",
}

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# Qwen API models (Alibaba Cloud DashScope - OpenAI-compatible)
# Coding Plan (sk-sp- keys) — $3/mo Lite, $15/mo Pro — dedicated endpoint
QWEN_CODING_PLAN_MODELS = {
    "qwen3.5-plus", "qwen3-coder-plus", "qwen3-coder-next",
    "qwen3-max-2026-01-23", "glm-4.7", "glm-5", "MiniMax-M2.5", "kimi-k2.5",
}
QWEN_ALIASES = {
    # Flagship
    "qwen-max": "qwen3-max",
    "qwen3-max": "qwen3-max",
    "qwen-plus": "qwen3.5-plus",
    "qwen-api": "qwen3.5-plus",
    "qwen3.5": "qwen3.5-plus",
    "qwen3.5-plus": "qwen3.5-plus",
    # Coding
    "qwen-coder": "qwen3-coder-plus",
    "qwen-coder-next": "qwen3-coder-next",
    "qwen3-coder": "qwen3-coder-plus",
    # Reasoning
    "qwq": "qwq-plus",
    # Fast / cheap
    "qwen-flash": "qwen3.5-flash",
    "qwen3.5-flash": "qwen3.5-flash",
    "qwen-turbo": "qwen-turbo",
    # Math
    "qwen-math": "qwen-math-plus",
    # Third-party (Coding Plan only)
    "glm-5": "glm-5",
    "kimi": "kimi-k2.5",
}

QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_URLS = {
    "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    "china": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "coding-intl": "https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions",
    "coding-china": "https://coding.dashscope.aliyuncs.com/v1/chat/completions",
}

# All OpenAI-compatible API providers (shared streaming/tool-calling implementation)
OPENAI_COMPAT_PROVIDERS = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "key_setting": "openai_api_key",
        "key_prefix": "sk-",
        "color": "white",
        "label": "OpenAI",
        "aliases": {
            "gpt": "gpt-4o",
            "gpt4": "gpt-4o",
            "gpt-mini": "gpt-4o-mini",
            "o3": "o3",
            "o3-mini": "o3-mini",
            "o4-mini": "o4-mini",
        },
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "models_url": "https://api.deepseek.com/v1/models",
        "key_setting": "deepseek_api_key",
        "key_prefix": "sk-",
        "color": "cyan",
        "label": "DeepSeek",
        "aliases": {
            "deepseek": "deepseek-chat",
            "deepseek-v3": "deepseek-chat",
            "deepseek-r1": "deepseek-reasoner",
        },
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "key_setting": "groq_api_key",
        "key_prefix": "gsk_",
        "color": "red",
        "label": "Groq",
        "aliases": {
            "llama": "llama-3.3-70b-versatile",
            "llama-small": "llama-3.1-8b-instant",
            "gemma": "gemma2-9b-it",
            "mixtral": "mixtral-8x7b-32768",
        },
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "models_url": "https://api.mistral.ai/v1/models",
        "key_setting": "mistral_api_key",
        "key_prefix": "",
        "color": "magenta",
        "label": "Mistral",
        "aliases": {
            "mistral": "mistral-large-latest",
            "mistral-large": "mistral-large-latest",
            "mistral-small": "mistral-small-latest",
            "codestral": "codestral-latest",
        },
    },
    "qwen": {
        "url": QWEN_API_URL,
        "models_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
        "key_setting": "qwen_api_key",
        "key_prefix": "sk-",
        "color": "blue",
        "label": "Qwen",
        "aliases": QWEN_ALIASES,
    },
}

KODIQA_DIR = os.path.expanduser("~/.kodiqa")
MEMORY_DB = os.path.join(KODIQA_DIR, "memory.db")
CONTEXT_FILE = os.path.join(KODIQA_DIR, "KODIQA.md")
SETTINGS_FILE = os.path.join(KODIQA_DIR, "settings.json")
CONFIG_FILE = os.path.join(KODIQA_DIR, "config.json")

# Defaults — all overridable via ~/.kodiqa/config.json
MAX_ITERATIONS = 15
MAX_FILE_SIZE = 100_000
COMMAND_TIMEOUT = 120

CONFIRM_ACTIONS = {"write_file", "edit_file", "run_command", "git_commit", "search_replace_all",
                    "move_file", "delete_file", "multi_edit", "clipboard_write", "diff_apply"}

BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "sudo rm -rf",
    "mkfs.", "dd if=", ":(){:|:&};:",
    "chmod -R 777 /", "chown -R",
    "> /dev/sda", "mv / ",
]

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "env", ".env", "dist", "build", ".idea", ".vscode",
    ".gradle", "target", ".dart_tool", ".pub-cache",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".zip", ".tar", ".gz", ".bz2", ".jar", ".war",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".pdf", ".doc",
    ".class", ".o", ".a", ".wasm", ".lock",
}

# ── Themes ──
THEMES = {
    "dark": {
        "prompt": "#af5fff",
        "ai_name": "green",
        "accent": "cyan",
        "cost": "dim",
        "border": "blue",
        "error": "red",
        "warning": "yellow",
        "success": "green",
        "tool": "yellow",
        "tool_done": "green",
    },
    "light": {
        "prompt": "#6200ea",
        "ai_name": "#1b5e20",
        "accent": "#006064",
        "cost": "#757575",
        "border": "#1565c0",
        "error": "#c62828",
        "warning": "#e65100",
        "success": "#2e7d32",
        "tool": "#e65100",
        "tool_done": "#2e7d32",
    },
    "dracula": {
        "prompt": "#bd93f9",
        "ai_name": "#50fa7b",
        "accent": "#8be9fd",
        "cost": "#6272a4",
        "border": "#bd93f9",
        "error": "#ff5555",
        "warning": "#f1fa8c",
        "success": "#50fa7b",
        "tool": "#ffb86c",
        "tool_done": "#50fa7b",
    },
    "monokai": {
        "prompt": "#ae81ff",
        "ai_name": "#a6e22e",
        "accent": "#66d9ef",
        "cost": "#75715e",
        "border": "#ae81ff",
        "error": "#f92672",
        "warning": "#e6db74",
        "success": "#a6e22e",
        "tool": "#fd971f",
        "tool_done": "#a6e22e",
    },
    "nord": {
        "prompt": "#b48ead",
        "ai_name": "#a3be8c",
        "accent": "#88c0d0",
        "cost": "#4c566a",
        "border": "#81a1c1",
        "error": "#bf616a",
        "warning": "#ebcb8b",
        "success": "#a3be8c",
        "tool": "#d08770",
        "tool_done": "#a3be8c",
    },
}

# ── Personas ──
PERSONAS = {
    "security-expert": {
        "name": "Security Expert",
        "prompt": "You are a security-focused expert. Always analyze code for vulnerabilities (XSS, SQLi, CSRF, etc.), suggest secure alternatives, and flag potential security risks before implementing features.",
    },
    "code-reviewer": {
        "name": "Code Reviewer",
        "prompt": "You are a meticulous code reviewer. Focus on code quality, performance, maintainability, and best practices. Point out potential bugs, suggest improvements, and ensure consistent style.",
    },
    "teacher": {
        "name": "Teacher",
        "prompt": "You are a patient coding teacher. Explain concepts thoroughly, provide examples, and break down complex topics. Ask questions to check understanding. Prefer educational explanations over just writing code.",
    },
    "architect": {
        "name": "Software Architect",
        "prompt": "You are a software architect. Focus on system design, patterns, scalability, and separation of concerns. Suggest architectural improvements and explain trade-offs of different approaches.",
    },
    "debugger": {
        "name": "Debugger",
        "prompt": "You are an expert debugger. When investigating issues, be methodical: reproduce, isolate, diagnose root cause, and fix. Always check edge cases and add regression tests.",
    },
}

# ── Changelog ──
# Canonical changelog is CHANGELOG.md — this list powers the /changelog command
CHANGELOG = [
    {"version": "v3.2.0", "date": "2026-03-02", "changes": [
        "Auto lint-fix loop (/lint auto) — AI fixes lint errors automatically (max 3 iterations)",
        "Auto test-fix loop (/test-fix) — run tests, AI fixes failures, re-run",
        "Hooks system — pre/post hooks for tool execution via config.json",
        "Watch AI triggers — # AI: comments in watched files trigger AI actions",
        "Architect mode (/architect) — strong model plans, cheap model implements",
        "Background/headless mode (--headless) — run tasks non-interactively",
        "Worktree isolation (/agent --worktree) — git worktree per sub-agent",
        "OS-level sandboxing (/sandbox) — sandbox-exec (macOS), firejail/bwrap (Linux)",
        "Repo map (/map) — tree-sitter or regex symbol extraction across codebase",
        "Agent teams (/team) — coordinator splits tasks, workers execute in parallel",
    ]},
    {"version": "v3.0.0", "date": "2026-03-02", "changes": [
        "Added /changelog — view version history",
        "Added /stats — session metrics (files, tools, time, cost)",
        "Added /review-local — AI reviews staged git changes",
        "Added /test — auto-generate unit tests for any file",
        "Added /persona — switch AI personality (security-expert, code-reviewer, teacher, architect, debugger)",
        "Added /patch — apply diff/patch from clipboard",
        "Added /profile — save/load config profiles",
        "Added /refactor — AI-powered multi-file refactoring (rename, extract)",
        "Added /history — browse and resume past sessions",
        "Added /watch — file watcher with change notifications",
        "Added /embed + /rag — RAG search with local embeddings (Ollama/OpenAI)",
        "Added /debug — run script, catch errors, debug with AI",
        "Added /diagram — generate Mermaid diagrams via AI",
        "Enabled parallel tool calls for OpenAI-compatible providers",
        "Fixed README test count and missing v2 commands",
    ]},
    {"version": "v2.0.0", "date": "2025-12-15", "changes": [
        "15 new features: plugins, sub-agents, LSP, themes, templates, voice",
        "5 UI themes (dark, light, dracula, monokai, nord)",
        "Stream interrupt (Esc/Ctrl+C stops streaming instantly)",
        "GitHub PR workflow (/pr, /review, /issue)",
        "Pinned context (/pin, /unpin)",
        "Command aliases (/alias, /unalias)",
        "Desktop notifications (/notify)",
        "Cost optimizer (/optimizer)",
        "Session sharing (/share — styled HTML export)",
        "Project templates (/init — 5 templates)",
        "Custom tool plugins (/plugins)",
        "Sub-agents (/agent, /agents — threaded background tasks)",
        "LSP integration (/lsp — Python, TypeScript, Go)",
        "Voice input (/voice — sox + Whisper)",
    ]},
    {"version": "v1.0.0", "date": "2025-10-01", "changes": [
        "Initial release",
        "26 tools, 7 API providers, MCP server support",
        "Multi-model consensus mode",
        "3 permission modes, plan mode, batch edit review",
        "Context window management, conversation branching",
        "Compact streaming, thinking display, tab autocomplete",
        "Persistent memory (SQLite), session recovery",
    ]},
]

DEFAULTS = {
    "max_iterations": MAX_ITERATIONS,
    "max_file_size": MAX_FILE_SIZE,
    "command_timeout": COMMAND_TIMEOUT,
    "auto_compact_threshold": 80000,
    "undo_buffer_size": 10,
    "blocked_commands": BLOCKED_COMMANDS,
    "skip_dirs": list(SKIP_DIRS),
    "skip_extensions": list(SKIP_EXTENSIONS),
    "hooks": {},
}


def load_kodiqaignore(cwd):
    """Read .kodiqaignore from cwd, return (extra_dirs, extra_extensions) to skip."""
    ignore_file = os.path.join(cwd, ".kodiqaignore")
    extra_dirs = set()
    extra_exts = set()
    if not os.path.isfile(ignore_file):
        return extra_dirs, extra_exts
    try:
        with open(ignore_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("*."):
                    extra_exts.add("." + line[2:])  # *.log -> .log
                else:
                    extra_dirs.add(line.rstrip("/"))
    except Exception:
        pass
    return extra_dirs, extra_exts


def load_config():
    """Load user config from ~/.kodiqa/config.json, merged with defaults."""
    config = dict(DEFAULTS)
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                user_config = json.load(f)
            config.update(user_config)
        except Exception:
            pass
    return config


def save_default_config():
    """Write default config.json if it doesn't exist (template for user)."""
    if not os.path.isfile(CONFIG_FILE):
        os.makedirs(KODIQA_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULTS, f, indent=2, default=list)


def load_settings():
    """Load settings from ~/.kodiqa/settings.json."""
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(settings):
    """Save settings to ~/.kodiqa/settings.json."""
    os.makedirs(KODIQA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def is_claude_model(model_name):
    """Check if a model name is a Claude API model."""
    return model_name.startswith("claude-") or model_name in CLAUDE_ALIASES


def get_openai_provider(model_name):
    """Return provider name (openai/deepseek/groq/mistral/qwen) if model belongs to an OpenAI-compat provider, else None."""
    for prov_name, prov in OPENAI_COMPAT_PROVIDERS.items():
        if model_name in prov["aliases"] or model_name in prov["aliases"].values():
            return prov_name
    return None


def is_openai_compat_model(model_name):
    """Check if model is any OpenAI-compatible API model."""
    return get_openai_provider(model_name) is not None


def is_qwen_api_model(model_name):
    """Check if a model name is a Qwen API model (backward compat)."""
    return get_openai_provider(model_name) == "qwen"


SYSTEM_PROMPT = """You are Kodiqa, a powerful AI coding assistant. You help users with software engineering, research, and general tasks.

## Your Capabilities
You have actions you can use to interact with the filesystem, run commands, search the web, and manage memory. To use an action, write it in this exact format in your response:

[ACTION: action_name]
param1: value1
param2: value2
[/ACTION]

## Available Actions

### File Operations
[ACTION: read_file]
path: /absolute/path/to/file
[/ACTION]

[ACTION: write_file]
path: /absolute/path/to/file
content:
file content here (everything after "content:" line)
[/ACTION]

[ACTION: edit_file]
path: /absolute/path/to/file
old: exact text to find
new: replacement text
[/ACTION]

[ACTION: list_dir]
path: /absolute/path/to/directory
[/ACTION]

[ACTION: tree]
path: /absolute/path
depth: 3
[/ACTION]

### Search
[ACTION: glob]
pattern: **/*.py
path: /absolute/path
[/ACTION]

[ACTION: grep]
pattern: regex pattern
path: /absolute/path/to/search
[/ACTION]

### Commands
[ACTION: run_command]
command: the shell command to run
[/ACTION]

### Web
[ACTION: web_search]
query: search terms here
[/ACTION]

[ACTION: web_fetch]
url: https://example.com
[/ACTION]

### Git
[ACTION: git_status]
[/ACTION]

[ACTION: git_diff]
args: --staged
[/ACTION]

[ACTION: git_commit]
message: commit message here
[/ACTION]

### Memory
[ACTION: memory_store]
content: what to remember
tags: optional tags
[/ACTION]

[ACTION: memory_search]
query: search terms
[/ACTION]

### Undo
[ACTION: undo_edit]
path: /absolute/path/to/file
[/ACTION]

### Replace All (replaces every occurrence, not just the first)
[ACTION: search_replace_all]
path: /absolute/path/to/file
old: text to find everywhere
new: replacement text
[/ACTION]

### File Management
[ACTION: create_directory]
path: /absolute/path/to/new/dir
[/ACTION]

[ACTION: move_file]
source: /absolute/path/to/source
destination: /absolute/path/to/destination
[/ACTION]

[ACTION: delete_file]
path: /absolute/path/to/file
[/ACTION]

### Multi-Edit (apply multiple edits to one file at once)
[ACTION: multi_edit]
path: /absolute/path/to/file
edits: [{{"old_string": "find this", "new_string": "replace with"}}, ...]
[/ACTION]

### Clipboard
[ACTION: clipboard_read]
[/ACTION]

[ACTION: clipboard_write]
content: text to copy
[/ACTION]

### Patch
[ACTION: diff_apply]
path: /absolute/path/to/file
patch: unified diff content
[/ACTION]

### Ask User (use this to clarify before assuming)
[ACTION: ask_user]
question: Which framework should we use?
header: Framework
options: React, Vue, Angular, Svelte
[/ACTION]

You can also ask open-ended questions without options:
[ACTION: ask_user]
question: What should the function be called?
[/ACTION]

## Rules
1. Always read a file before editing it
2. Use glob/grep to find files before assuming paths
3. Explain what you're doing before and after actions
4. You can use multiple actions in one response
5. After seeing action results, you can use more actions to continue working
6. For write_file and edit_file: the content/old/new values are everything on the lines after the key until the next key or [/ACTION]
7. Be concise but thorough in explanations
8. When asked to investigate or scan a project, actually READ the key files (pubspec.yaml, build.gradle, package.json, main entry points) - do not guess from directory names alone
9. Use ask_user when requirements are unclear or there are multiple valid approaches - don't assume, ask first

## Context
- Current directory: {cwd}
- Current model: {model}
{memories}"""
