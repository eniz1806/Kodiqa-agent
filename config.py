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
    "gpt": "gpt-oss",
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
QWEN_ALIASES = {
    # Flagship
    "qwen-max": "qwen3-max",
    "qwen-plus": "qwen3.5-plus-2026-02-15",
    "qwen-api": "qwen3.5-plus-2026-02-15",
    "qwen3.5": "qwen3.5-plus-2026-02-15",
    "qwen3.5-plus": "qwen3.5-plus-2026-02-15",
    "qwen3.5-flash": "qwen3.5-flash",
    # Coding
    "qwen-coder": "qwen3-coder-plus",
    "qwen-coder-flash": "qwen3-coder-flash",
    # Reasoning
    "qwq": "qwq-plus",
    # Fast / cheap
    "qwen-flash": "qwen-flash",
    "qwen-turbo": "qwen-turbo",
    # Long context (10M)
    "qwen-long": "qwen-long-latest",
    # Open-source via API
    "qwen3.5-os": "qwen3.5-27b",
    # Math
    "qwen-math": "qwen-math-plus",
}

QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

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

DEFAULTS = {
    "max_iterations": MAX_ITERATIONS,
    "max_file_size": MAX_FILE_SIZE,
    "command_timeout": COMMAND_TIMEOUT,
    "auto_compact_threshold": 80000,
    "undo_buffer_size": 10,
    "blocked_commands": BLOCKED_COMMANDS,
    "skip_dirs": list(SKIP_DIRS),
    "skip_extensions": list(SKIP_EXTENSIONS),
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


def is_qwen_api_model(model_name):
    """Check if a model name is a Qwen API model (not local Ollama Qwen)."""
    if model_name in QWEN_ALIASES or model_name in QWEN_ALIASES.values():
        return True
    # Local Ollama models have colons (e.g. qwen3-coder:latest) or are in MODEL_ALIASES
    if ":" in model_name or model_name in MODEL_ALIASES or model_name in MODEL_ALIASES.values():
        return False
    # API model names: contain version dates or API-specific suffixes
    api_markers = ("-plus", "-flash", "-turbo", "-latest", "-max", "-long", "-math")
    if any(m in model_name for m in api_markers):
        return True
    # Date-stamped models (e.g. qwen3-max-2025-09-23)
    import re
    if re.search(r"\d{4}-\d{2}-\d{2}", model_name):
        return True
    return False


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
