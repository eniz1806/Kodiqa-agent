"""Kodiqa configuration - models, prompts, constants."""

import json
import os

OLLAMA_URL = "http://localhost:11434"
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
    "claude": "claude-sonnet-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-20250514",
}

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

MAX_ITERATIONS = 15
MAX_FILE_SIZE = 100_000
COMMAND_TIMEOUT = 120
KODIQA_DIR = os.path.expanduser("~/.kodiqa")
MEMORY_DB = os.path.join(KODIQA_DIR, "memory.db")
CONTEXT_FILE = os.path.join(KODIQA_DIR, "KODIQA.md")
SETTINGS_FILE = os.path.join(KODIQA_DIR, "settings.json")

CONFIRM_ACTIONS = {"write_file", "edit_file", "run_command", "git_commit"}

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

### Ask User
[ACTION: ask_user]
question: What framework do you want to use?
options: React, Vue, Angular, Svelte
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

## Context
- Current directory: {cwd}
- Current model: {model}
{memories}"""
