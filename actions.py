"""Kodiqa action parser and executor - 15 actions mirroring Claude Code."""

import os
import re
import fnmatch
import subprocess
from pathlib import Path

from config import (
    CONFIRM_ACTIONS, BLOCKED_COMMANDS, COMMAND_TIMEOUT,
    MAX_FILE_SIZE, SKIP_DIRS, SKIP_EXTENSIONS,
)
from memory import MemoryStore
from web import search_duckduckgo, fetch_page, format_results


def parse_actions(text):
    """Extract all [ACTION: name]...[/ACTION] blocks from model text."""
    pattern = r'\[ACTION:\s*(\w+)\](.*?)\[/ACTION\]'
    matches = re.findall(pattern, text, re.DOTALL)
    actions = []
    for name, body in matches:
        params = _parse_params(body.strip(), name)
        actions.append({"name": name, "params": params, "raw": body.strip()})
    return actions


def _parse_params(body, action_name):
    """Parse key: value pairs from action body. Handles multiline values."""
    params = {}
    # For write_file, content is everything after "content:" line
    # For edit_file, old/new are multiline
    if action_name in ("write_file", "edit_file"):
        return _parse_multiline_params(body, action_name)

    for line in body.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            params[key.strip().lower()] = val.strip()
    return params


def _parse_multiline_params(body, action_name):
    """Parse params where values can span multiple lines."""
    params = {}
    lines = body.split("\n")

    if action_name == "write_file":
        # path on first line, content is everything after "content:" line
        current_key = None
        content_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("path:") and "path" not in params:
                params["path"] = stripped.split(":", 1)[1].strip()
            elif stripped.lower().startswith("content:"):
                current_key = "content"
                # Check if content starts on same line
                rest = stripped.split(":", 1)[1]
                if rest.strip():
                    content_lines.append(rest)
            elif current_key == "content":
                content_lines.append(line)  # preserve original indentation
        if content_lines:
            params["content"] = "\n".join(content_lines).strip()

    elif action_name == "edit_file":
        current_key = None
        sections = {"path": [], "old": [], "new": []}
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("path:") and not sections["path"]:
                params["path"] = stripped.split(":", 1)[1].strip()
                current_key = None
            elif stripped.lower().startswith("old:"):
                current_key = "old"
                rest = stripped.split(":", 1)[1]
                if rest.strip():
                    sections["old"].append(rest)
            elif stripped.lower().startswith("new:"):
                current_key = "new"
                rest = stripped.split(":", 1)[1]
                if rest.strip():
                    sections["new"].append(rest)
            elif current_key in sections:
                sections[current_key].append(line)
        if sections["old"]:
            params["old"] = "\n".join(sections["old"]).strip()
        if sections["new"]:
            params["new"] = "\n".join(sections["new"]).strip()

    return params


def execute_action(action, memory, confirm_fn):
    """Execute a parsed action and return result string."""
    name = action["name"]
    p = action["params"]

    # Check if confirmation needed
    if name in CONFIRM_ACTIONS:
        desc = _describe_action(name, p)
        if not confirm_fn(desc):
            return f"[{name}] Denied by user."

    try:
        handlers = {
            "read_file": lambda: do_read_file(p.get("path", "")),
            "write_file": lambda: do_write_file(p.get("path", ""), p.get("content", "")),
            "edit_file": lambda: do_edit_file(p.get("path", ""), p.get("old", ""), p.get("new", "")),
            "list_dir": lambda: do_list_dir(p.get("path", ".")),
            "tree": lambda: do_tree(p.get("path", "."), int(p.get("depth", "3"))),
            "glob": lambda: do_glob(p.get("pattern", ""), p.get("path", ".")),
            "grep": lambda: do_grep(p.get("pattern", ""), p.get("path", ".")),
            "run_command": lambda: do_run_command(p.get("command", "")),
            "web_search": lambda: do_web_search(p.get("query", "")),
            "web_fetch": lambda: do_web_fetch(p.get("url", "")),
            "git_status": lambda: do_run_command("git status"),
            "git_diff": lambda: do_run_command(f"git diff {p.get('args', '')}".strip()),
            "git_commit": lambda: do_git_commit(p.get("message", "")),
            "memory_store": lambda: memory.store(p.get("content", ""), p.get("tags", "")),
            "memory_search": lambda: memory.search(p.get("query", "")),
        }
        handler = handlers.get(name)
        if handler:
            return handler()
        return f"Unknown action: {name}"
    except Exception as e:
        return f"[{name}] Error: {e}"


def execute_tool_call(name, params, memory, confirm_fn):
    """Execute a Claude native tool call. params is a dict from Claude's input field."""
    p = params or {}

    if name in CONFIRM_ACTIONS:
        desc = _describe_action(name, p)
        if not confirm_fn(desc):
            return f"Denied by user."

    try:
        handlers = {
            "read_file": lambda: do_read_file(p.get("path", "")),
            "write_file": lambda: do_write_file(p.get("path", ""), p.get("content", "")),
            "edit_file": lambda: do_edit_file(p.get("path", ""), p.get("old_string", ""), p.get("new_string", "")),
            "list_dir": lambda: do_list_dir(p.get("path", ".")),
            "tree": lambda: do_tree(p.get("path", "."), p.get("depth", 3)),
            "glob": lambda: do_glob(p.get("pattern", ""), p.get("path", ".")),
            "grep": lambda: do_grep(p.get("pattern", ""), p.get("path", ".")),
            "run_command": lambda: do_run_command(p.get("command", "")),
            "web_search": lambda: do_web_search(p.get("query", "")),
            "web_fetch": lambda: do_web_fetch(p.get("url", "")),
            "git_status": lambda: do_run_command("git status"),
            "git_diff": lambda: do_run_command(f"git diff {p.get('args', '')}".strip()),
            "git_commit": lambda: do_git_commit(p.get("message", "")),
            "memory_store": lambda: memory.store(p.get("content", ""), p.get("tags", "")),
            "memory_search": lambda: memory.search(p.get("query", "")),
        }
        handler = handlers.get(name)
        if handler:
            return handler()
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


def _describe_action(name, params):
    """Human-readable description for confirmation prompt."""
    if name == "write_file":
        return f"Write file: {params.get('path', '?')}"
    if name == "edit_file":
        return f"Edit file: {params.get('path', params.get('path', '?'))}"
    if name == "run_command":
        return f"Run command: {params.get('command', '?')}"
    if name == "git_commit":
        return f"Git commit: {params.get('message', '?')}"
    return f"{name}: {params}"


# === Action Handlers ===

def do_read_file(path):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        return f"File too large ({size:,} bytes). Max: {MAX_FILE_SIZE:,}"
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        numbered = []
        for i, line in enumerate(lines, 1):
            numbered.append(f"{i:>5} | {line.rstrip()}")
        return "\n".join(numbered)
    except Exception as e:
        return f"Read error: {e}"


def do_write_file(path, content):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return f"Written {len(content)} chars to {path}"


def do_edit_file(path, old_text, new_text):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    if old_text not in content:
        return f"Text not found in {path}. Make sure the old text matches exactly."
    count = content.count(old_text)
    content = content.replace(old_text, new_text, 1)
    with open(path, "w") as f:
        f.write(content)
    return f"Replaced in {path} ({count} occurrence{'s' if count > 1 else ''} found, replaced first)"


def do_list_dir(path):
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return f"Not a directory: {path}"
    entries = sorted(os.listdir(path))
    lines = []
    for e in entries:
        full = os.path.join(path, e)
        indicator = "/" if os.path.isdir(full) else ""
        size = ""
        if os.path.isfile(full):
            s = os.path.getsize(full)
            if s > 1_000_000:
                size = f" ({s / 1_000_000:.1f}MB)"
            elif s > 1000:
                size = f" ({s / 1000:.1f}KB)"
        lines.append(f"  {e}{indicator}{size}")
    return f"{path}/ ({len(entries)} items)\n" + "\n".join(lines)


def do_tree(path, depth=3):
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return f"Not a directory: {path}"
    lines = [path + "/"]
    _tree_recurse(path, "", depth, lines)
    if len(lines) > 200:
        lines = lines[:200]
        lines.append("... (truncated, too many entries)")
    return "\n".join(lines)


def _tree_recurse(dirpath, prefix, depth, lines):
    if depth <= 0:
        return
    try:
        entries = sorted(os.listdir(dirpath))
    except PermissionError:
        return
    dirs = []
    files = []
    for e in entries:
        if e.startswith(".") and e in SKIP_DIRS:
            continue
        if e in SKIP_DIRS:
            continue
        full = os.path.join(dirpath, e)
        if os.path.isdir(full):
            dirs.append(e)
        else:
            ext = os.path.splitext(e)[1].lower()
            if ext not in SKIP_EXTENSIONS:
                files.append(e)
    all_items = [(d, True) for d in dirs] + [(f, False) for f in files]
    for i, (name, is_dir) in enumerate(all_items):
        is_last = i == len(all_items) - 1
        connector = "└── " if is_last else "├── "
        suffix = "/" if is_dir else ""
        lines.append(f"{prefix}{connector}{name}{suffix}")
        if is_dir:
            ext_prefix = prefix + ("    " if is_last else "│   ")
            _tree_recurse(os.path.join(dirpath, name), ext_prefix, depth - 1, lines)


def do_glob(pattern, path):
    path = os.path.expanduser(path)
    p = Path(path)
    if not p.is_dir():
        return f"Not a directory: {path}"
    matches = sorted(str(m) for m in p.glob(pattern))
    if not matches:
        return f"No files matching '{pattern}' in {path}"
    if len(matches) > 100:
        matches = matches[:100]
        matches.append("... (100+ matches, showing first 100)")
    return "\n".join(matches)


def do_grep(pattern, path):
    path = os.path.expanduser(path)
    results = []
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Invalid regex: {e}"

    if os.path.isfile(path):
        _grep_file(path, regex, results)
    elif os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in SKIP_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                _grep_file(fpath, regex, results)
                if len(results) > 100:
                    results.append("... (100+ matches, stopping)")
                    return "\n".join(results)
    else:
        return f"Path not found: {path}"

    return "\n".join(results) if results else f"No matches for '{pattern}' in {path}"


def _grep_file(fpath, regex, results):
    try:
        with open(fpath, "r", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if regex.search(line):
                    results.append(f"{fpath}:{i}: {line.rstrip()}")
    except (PermissionError, OSError):
        pass


def do_run_command(command):
    if not command.strip():
        return "No command provided."
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return f"Blocked dangerous command: {command}"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT, cwd=os.getcwd(),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {COMMAND_TIMEOUT}s"
    except Exception as e:
        return f"Command error: {e}"


def do_web_search(query):
    if not query.strip():
        return "No search query provided."
    results = search_duckduckgo(query)
    return format_results(results)


def do_web_fetch(url):
    if not url.strip():
        return "No URL provided."
    return fetch_page(url)


def do_git_commit(message):
    if not message.strip():
        return "No commit message provided."
    try:
        # Stage all changes
        subprocess.run(["git", "add", "-A"], capture_output=True, text=True, timeout=30)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except Exception as e:
        return f"Git commit error: {e}"
