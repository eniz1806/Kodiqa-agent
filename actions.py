"""Kodiqa action parser and executor - 20 tools mirroring Claude Code."""

import base64
import os
import re
import subprocess
import difflib
import logging
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import (
    CONFIRM_ACTIONS, BLOCKED_COMMANDS, COMMAND_TIMEOUT,
    MAX_FILE_SIZE, SKIP_DIRS, SKIP_EXTENSIONS,
)
from web import web_search, fetch_page, format_results

# ── Console reference (set by kodiqa.py on startup) ──
_console = None
_logger = logging.getLogger("kodiqa")

# Per-file undo stack: {filepath: deque([(old_content), ...], maxlen=N)}
_undo_buffer = defaultdict(lambda: deque(maxlen=10))

# ── Hooks ──
_hooks = {}

def set_hooks(hooks_dict):
    """Set tool hooks from config."""
    global _hooks
    _hooks = hooks_dict if isinstance(hooks_dict, dict) else {}

def _run_hook(hook_cmd, params):
    """Run a hook command with {param} substitution. Returns True if success."""
    try:
        cmd = hook_cmd
        for k, v in params.items():
            cmd = cmd.replace(f"{{{k}}}", str(v))
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception:
        return True  # Don't block on hook failure

# ── Sandbox ──
_sandbox_enabled = False

def set_sandbox(enabled):
    """Enable/disable OS-level sandboxing for run_command."""
    global _sandbox_enabled
    _sandbox_enabled = enabled

def _sandbox_wrap(cmd, cwd):
    """Wrap command in OS-level sandbox restricting writes to cwd + /tmp."""
    import platform
    import shutil
    system = platform.system()
    if system == "Darwin":
        # macOS sandbox-exec
        profile = (
            "(version 1)(allow default)"
            f"(deny file-write* (require-not (subpath \"{cwd}\"))"
            f" (require-not (subpath \"/tmp\"))"
            f" (require-not (subpath \"/private/tmp\")))"
        )
        return f"sandbox-exec -p '{profile}' /bin/sh -c {_shell_quote(cmd)}"
    elif system == "Linux":
        if shutil.which("firejail"):
            return f"firejail --noprofile --whitelist={cwd} --whitelist=/tmp -- {cmd}"
        elif shutil.which("bwrap"):
            return f"bwrap --ro-bind / / --bind {cwd} {cwd} --bind /tmp /tmp --dev /dev --proc /proc -- {cmd}"
    return cmd  # No sandbox tool, run as-is

def _shell_quote(s):
    """Quote a string for shell usage."""
    return "'" + s.replace("'", "'\\''") + "'"

# Edit queue for batch review mode
_edit_queue = []  # list of {"path": ..., "old": ..., "new": ..., "type": "write"|"edit"|...}
_batch_mode = False


def set_batch_mode(enabled):
    global _batch_mode
    _batch_mode = enabled


def get_edit_queue():
    return list(_edit_queue)


def clear_edit_queue():
    _edit_queue.clear()


def apply_queued_edit(index):
    """Apply a single queued edit by index."""
    if index < 0 or index >= len(_edit_queue):
        return "Invalid edit index"
    entry = _edit_queue[index]
    path = entry.get("path", "")
    if not path:
        return "Error: empty file path — skipped"
    # Save to undo buffer
    old = entry.get("old_content", "")
    _undo_buffer[os.path.abspath(path)].append(old if old else None)
    # Write the new content
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(entry["new_content"])
    return f"Applied: {path}"


def reject_queued_edit(index):
    """Reject (skip) a queued edit."""
    if index < 0 or index >= len(_edit_queue):
        return "Invalid edit index"
    return f"Rejected: {_edit_queue[index]['path']}"


def set_console(console):
    global _console
    _console = console


def _show_diff(path, old_content, new_content):
    """Show colored diff before applying changes."""
    if _console is None:
        return
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{os.path.basename(path)}", tofile=f"b/{os.path.basename(path)}", lineterm="")
    diff_lines = list(diff)
    if not diff_lines:
        return
    for line in diff_lines[:50]:  # cap at 50 lines
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            _console.print(f"[bold]{line}[/]")
        elif line.startswith("@@"):
            _console.print(f"[cyan]{line}[/]")
        elif line.startswith("+"):
            _console.print(f"[green]{line}[/]")
        elif line.startswith("-"):
            _console.print(f"[red]{line}[/]")
        else:
            _console.print(f"[dim]{line}[/]")
    if len(diff_lines) > 50:
        _console.print(f"[dim]... ({len(diff_lines) - 50} more diff lines)[/]")


# ── Text-based action parsing (for Ollama models) ──

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
    params = {}
    if action_name in ("write_file", "edit_file"):
        return _parse_multiline_params(body, action_name)
    for line in body.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            params[key.strip().lower()] = val.strip()
    return params


def _parse_multiline_params(body, action_name):
    params = {}
    lines = body.split("\n")
    if action_name == "write_file":
        current_key = None
        content_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("path:") and "path" not in params:
                params["path"] = stripped.split(":", 1)[1].strip()
            elif stripped.lower().startswith("content:"):
                current_key = "content"
                rest = stripped.split(":", 1)[1]
                if rest.strip():
                    content_lines.append(rest)
            elif current_key == "content":
                content_lines.append(line)
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


# ── Execution (shared by both text-based and native tool calls) ──

def execute_action(action, memory, confirm_fn):
    """Execute a text-based action (Ollama mode)."""
    name = action["name"]
    p = action["params"]
    if name in CONFIRM_ACTIONS:
        desc = _describe_action(name, p)
        if not confirm_fn(desc):
            return f"[{name}] Denied by user."
    try:
        return _dispatch(name, p, memory)
    except Exception as e:
        return f"[{name}] Error: {e}"


def execute_tool_call(name, params, memory, confirm_fn):
    """Execute a Claude native tool call."""
    p = params or {}
    if name in CONFIRM_ACTIONS:
        desc = _describe_action(name, p)
        if not confirm_fn(desc):
            return "Denied by user."
    try:
        return _dispatch(name, p, memory)
    except Exception as e:
        return f"Error: {e}"


def execute_tools_parallel(tool_calls, memory, confirm_fn):
    """Execute multiple tool calls in parallel where safe. Returns list of (id, result)."""
    # Separate into safe-to-parallel (read-only) and sequential (needs confirm or writes)
    read_only = {"read_file", "list_dir", "tree", "glob", "grep", "git_status", "git_diff",
                 "web_search", "web_fetch", "memory_search", "ask_user", "clipboard_read"}

    parallel_batch = []
    sequential_batch = []
    for tc in tool_calls:
        if tc["name"] in read_only:
            parallel_batch.append(tc)
        else:
            sequential_batch.append(tc)

    results = {}

    # Run read-only tools in parallel
    if parallel_batch:
        with ThreadPoolExecutor(max_workers=min(4, len(parallel_batch))) as executor:
            futures = {}
            for tc in parallel_batch:
                f = executor.submit(_dispatch, tc["name"], tc.get("input", {}), memory)
                futures[f] = tc["id"]
            for future in as_completed(futures):
                tc_id = futures[future]
                try:
                    result = future.result()
                    if len(result) > 20000:
                        result = result[:20000] + "\n... (truncated)"
                    results[tc_id] = result
                except Exception as e:
                    results[tc_id] = f"Error: {e}"

    # Run write/command tools sequentially (they need confirmation)
    for tc in sequential_batch:
        name = tc["name"]
        p = tc.get("input", {})
        if name in CONFIRM_ACTIONS:
            desc = _describe_action(name, p)
            if not confirm_fn(desc):
                results[tc["id"]] = "Denied by user."
                continue
        try:
            result = _dispatch(name, p, memory)
            if len(result) > 20000:
                result = result[:20000] + "\n... (truncated)"
            results[tc["id"]] = result
        except Exception as e:
            results[tc["id"]] = f"Error: {e}"

    # Return in original order
    return [(tc["id"], results.get(tc["id"], "Error: no result")) for tc in tool_calls]


def _dispatch(name, p, memory):
    """Central dispatch for all tool/action names."""
    handlers = {
        "read_file": lambda: do_read_file(p.get("path", "")),
        "write_file": lambda: do_write_file(p.get("path", ""), p.get("content", "")),
        "edit_file": lambda: do_edit_file(
            p.get("path", ""),
            p.get("old_string", p.get("old", "")),
            p.get("new_string", p.get("new", "")),
        ),
        "list_dir": lambda: do_list_dir(p.get("path", ".")),
        "tree": lambda: do_tree(p.get("path", "."), int(p.get("depth", 3))),
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
        "read_image": lambda: do_read_image(p.get("path", "")),
        "read_pdf": lambda: do_read_pdf(p.get("path", "")),
        "undo_edit": lambda: do_undo_edit(p.get("path", "")),
        "search_replace_all": lambda: do_edit_file_all(
            p.get("path", ""),
            p.get("old_string", p.get("old", "")),
            p.get("new_string", p.get("new", "")),
        ),
        "ask_user": lambda: do_ask_user(
            p.get("question", ""),
            _parse_options(p.get("options", [])),
            p.get("header", ""),
            p.get("multi_select", False),
        ),
        "create_directory": lambda: do_create_directory(p.get("path", "")),
        "move_file": lambda: do_move_file(p.get("source", ""), p.get("destination", "")),
        "delete_file": lambda: do_delete_file(p.get("path", "")),
        "multi_edit": lambda: do_multi_edit(p.get("path", ""), p.get("edits", [])),
        "clipboard_read": lambda: do_clipboard_read(),
        "clipboard_write": lambda: do_clipboard_write(p.get("content", "")),
        "diff_apply": lambda: do_diff_apply(p.get("path", ""), p.get("patch", "")),
    }
    handler = handlers.get(name)
    if handler:
        # Pre-hook
        pre_hook = _hooks.get(f"pre_{name}")
        if pre_hook:
            if not _run_hook(pre_hook, p):
                return f"Pre-hook failed for {name}"
        result = handler()
        # Post-hook
        post_hook = _hooks.get(f"post_{name}")
        if post_hook:
            _run_hook(post_hook, p)
        return result
    return f"Unknown tool: {name}"


def _describe_action(name, params):
    if name == "write_file":
        return f"Write file: {params.get('path', '?')}"
    if name == "edit_file":
        return f"Edit file: {params.get('path', '?')}"
    if name == "run_command":
        return f"Run command: {params.get('command', '?')}"
    if name == "git_commit":
        return f"Git commit: {params.get('message', '?')}"
    if name == "create_directory":
        return f"Create directory: {params.get('path', '?')}"
    if name == "move_file":
        return f"Move: {params.get('source', '?')} → {params.get('destination', '?')}"
    if name == "delete_file":
        return f"Delete file: {params.get('path', '?')}"
    if name == "multi_edit":
        return f"Multi-edit: {params.get('path', '?')} ({len(params.get('edits', []))} edits)"
    if name == "clipboard_write":
        return f"Copy to clipboard: {len(params.get('content', ''))} chars"
    if name == "diff_apply":
        return f"Apply patch: {params.get('path', '?')}"
    if name == "search_replace_all":
        return f"Replace all in: {params.get('path', '?')}"
    return f"{name}: {params}"


# ── Action Handlers ──

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
    if not path or not path.strip():
        return "Error: file path is required — cannot write to an empty path."
    path = os.path.expanduser(path)
    if not content:
        return "Error: content is required — cannot write empty content."
    old_content = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", errors="replace") as f:
                old_content = f.read()
        except Exception:
            pass
    # Batch mode: queue the edit for review instead of applying
    if _batch_mode:
        _edit_queue.append({
            "path": path,
            "type": "write",
            "old_content": old_content if old_content else "",
            "new_content": content,
            "description": f"Write {len(content)} chars to {path}" + (" (new file)" if not old_content else ""),
        })
        return f"[queued] Write to {path} ({len(content)} chars)"
    # Save to undo buffer before writing
    _undo_buffer[os.path.abspath(path)].append(old_content if old_content else None)
    if old_content:
        _show_diff(path, old_content, content)
    else:
        if _console:
            _console.print(f"  [green]+ new file: {path} ({len(content)} chars)[/]")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return f"Written {len(content)} chars to {path}"


def do_edit_file(path, old_text, new_text):
    if not path or not path.strip():
        return "Error: file path is required — cannot edit an empty path."
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    if old_text not in content:
        return f"Text not found in {path}. Make sure the old text matches exactly."
    new_content = content.replace(old_text, new_text, 1)
    count = content.count(old_text)
    # Batch mode: queue the edit for review instead of applying
    if _batch_mode:
        _edit_queue.append({
            "path": path,
            "type": "edit",
            "old_content": content,
            "new_content": new_content,
            "description": f"Edit {path} ({count} occurrence{'s' if count > 1 else ''})",
        })
        return f"[queued] Edit {path}"
    # Save to undo buffer before editing
    _undo_buffer[os.path.abspath(path)].append(content)
    _show_diff(path, content, new_content)
    with open(path, "w") as f:
        f.write(new_content)
    return f"Replaced in {path} ({count} occurrence{'s' if count > 1 else ''} found, replaced first)"


def do_undo_edit(path):
    """Restore the previous version of a file."""
    path = os.path.expanduser(path)
    abs_path = os.path.abspath(path)
    if abs_path not in _undo_buffer or not _undo_buffer[abs_path]:
        return f"No undo history for {path}"
    previous = _undo_buffer[abs_path].pop()
    if previous is None:
        # File was newly created — undo means delete it
        try:
            os.remove(path)
            return f"Undone: removed newly created file {path}"
        except Exception as e:
            return f"Undo error: {e}"
    # Show diff and restore
    current = ""
    if os.path.isfile(path):
        with open(path, "r", errors="replace") as f:
            current = f.read()
    _show_diff(path, current, previous)
    with open(path, "w") as f:
        f.write(previous)
    remaining = len(_undo_buffer[abs_path])
    return f"Undone edit to {path} (restored previous version, {remaining} more undo(s) available)"


def do_edit_file_all(path, old_text, new_text):
    """Replace ALL occurrences of old_text with new_text in a file."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    if old_text not in content:
        return f"Text not found in {path}. Make sure the old text matches exactly."
    count = content.count(old_text)
    new_content = content.replace(old_text, new_text)
    # Batch mode
    if _batch_mode:
        _edit_queue.append({
            "path": path, "type": "replace_all",
            "old_content": content, "new_content": new_content,
            "description": f"Replace all ({count}x) in {path}",
        })
        return f"[queued] Replace all in {path}"
    _undo_buffer[os.path.abspath(path)].append(content)
    _show_diff(path, content, new_content)
    with open(path, "w") as f:
        f.write(new_content)
    return f"Replaced {count} occurrence(s) in {path}"


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
        if e in SKIP_DIRS or (e.startswith(".") and e in SKIP_DIRS):
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
    run_cmd = command
    if _sandbox_enabled:
        run_cmd = _sandbox_wrap(command, os.getcwd())
    try:
        result = subprocess.run(
            run_cmd, shell=True, capture_output=True, text=True,
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
    results = web_search(query)
    return format_results(results)


def do_web_fetch(url):
    if not url.strip():
        return "No URL provided."
    return fetch_page(url)


def do_git_commit(message):
    if not message.strip():
        return "No commit message provided."
    # Run pre-commit hooks if configured
    hook_result = _run_pre_commit_hooks()
    if hook_result:
        return f"Pre-commit hook failed:\n{hook_result}"
    try:
        subprocess.run(["git", "add", "-A"], capture_output=True, text=True, timeout=30)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except Exception as e:
        return f"Git commit error: {e}"


def _run_pre_commit_hooks():
    """Run pre-commit hooks if .pre-commit-config.yaml exists or git hooks are set up."""
    # Check for git's own pre-commit hook
    git_hook = os.path.join(".git", "hooks", "pre-commit")
    if os.path.isfile(git_hook) and os.access(git_hook, os.X_OK):
        try:
            result = subprocess.run(
                [git_hook], capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return result.stdout + result.stderr
        except Exception as e:
            return str(e)
    return None


def do_read_image(path):
    """Read an image file and return base64 for Claude vision."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    ext = os.path.splitext(path)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext)
    if not media_type:
        return f"Unsupported image format: {ext}. Supported: png, jpg, gif, webp"
    size = os.path.getsize(path)
    if size > 5_000_000:  # 5MB limit
        return f"Image too large ({size / 1_000_000:.1f}MB). Max: 5MB"
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"__IMAGE__:{media_type}:{data}"
    except Exception as e:
        return f"Read error: {e}"


def do_read_pdf(path):
    """Extract text from a PDF file."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    # Try using python's built-in or pdfplumber
    try:
        import subprocess
        # Try pdftotext (usually available on macOS via poppler or xcode)
        result = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
            if len(text) > MAX_FILE_SIZE:
                text = text[:MAX_FILE_SIZE] + "\n... (truncated)"
            return text
    except FileNotFoundError:
        pass
    # Fallback: try with python
    try:
        # Simple binary scan for text
        with open(path, "rb") as f:
            raw = f.read()
        # Extract text between stream/endstream (very basic)
        import re
        text_parts = []
        for match in re.finditer(rb'\((.*?)\)', raw):
            try:
                text_parts.append(match.group(1).decode("utf-8", errors="ignore"))
            except Exception:
                pass
        if text_parts:
            text = " ".join(text_parts)
            if len(text) > MAX_FILE_SIZE:
                text = text[:MAX_FILE_SIZE] + "\n... (truncated)"
            return text
    except Exception:
        pass
    return f"Could not extract text from {path}. Install pdftotext: brew install poppler"


def _parse_options(options):
    """Parse options from either structured (Claude) or comma-separated string (Ollama)."""
    if isinstance(options, list):
        # Claude sends list of {label, description} objects
        parsed = []
        for opt in options:
            if isinstance(opt, dict):
                parsed.append(opt)
            elif isinstance(opt, str):
                parsed.append({"label": opt, "description": ""})
        return parsed
    if isinstance(options, str) and options.strip():
        return [{"label": o.strip(), "description": ""} for o in options.split(",") if o.strip()]
    return []


def do_ask_user(question, options=None, header=None, multi_select=False):
    """Ask the user a structured question with rich UI."""
    if not question.strip():
        return "No question provided."
    if _console is None:
        return "Could not ask user (no console available)."

    from rich.panel import Panel

    # Build the question panel
    content_lines = []
    if header:
        content_lines.append(f"[dim]{header}[/]")
    content_lines.append(f"[bold]{question}[/]")

    if options and len(options) > 0:
        content_lines.append("")
        for i, opt in enumerate(options, 1):
            label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
            desc = opt.get("description", "") if isinstance(opt, dict) else ""
            if desc:
                content_lines.append(f"  [cyan bold]{i}.[/] [bold]{label}[/]")
                content_lines.append(f"     [dim]{desc}[/]")
            else:
                content_lines.append(f"  [cyan bold]{i}.[/] [bold]{label}[/]")
        content_lines.append(f"  [dim]{len(options) + 1}. Other (type your own answer)[/]")

        if multi_select:
            content_lines.append("")
            content_lines.append("[dim italic]You can select multiple: e.g. 1,3 or 1 2 3[/]")

    _console.print(Panel(
        "\n".join(content_lines),
        border_style="yellow",
        title="[bold yellow]Question[/]",
        padding=(1, 2),
    ))

    # Use raw input() — Rich Prompt.ask can fail when terminal state is altered
    # by stream interrupt monitor or stall indicator threads
    try:
        if options and len(options) > 0:
            _console.print("[bold yellow]Your choice: [/]", end="")
            choice = input().strip()

            if multi_select:
                # Parse multiple selections: "1,3" or "1 2 3" or "1, 3"
                parts = [p.strip() for p in choice.replace(",", " ").split() if p.strip()]
                selected = []
                custom = []
                for part in parts:
                    try:
                        idx = int(part)
                        if 1 <= idx <= len(options):
                            label = options[idx - 1].get("label", str(options[idx - 1]))
                            selected.append(label)
                    except ValueError:
                        custom.append(part)
                if selected:
                    for s in selected:
                        _console.print(f"  [green]+ {s}[/]")
                    result = "User selected: " + ", ".join(selected)
                    if custom:
                        result += f" (and typed: {' '.join(custom)})"
                    return result
                return f"User answered: {choice}"
            else:
                # Single select
                try:
                    idx = int(choice)
                    if 1 <= idx <= len(options):
                        label = options[idx - 1].get("label", str(options[idx - 1]))
                        _console.print(f"  [green]Selected: {label}[/]")
                        return f"User selected: {label}"
                except ValueError:
                    pass
                return f"User answered: {choice}"
        else:
            _console.print("[bold yellow]Your answer: [/]", end="")
            answer = input().strip()
            return f"User answered: {answer}"
    except (EOFError, KeyboardInterrupt):
        _console.print("\n[dim]Cancelled.[/]")
        return "User cancelled the question."


# ── File Management Tools ──

def do_create_directory(path):
    """Create a directory (and parents)."""
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        return f"Directory already exists: {path}"
    try:
        os.makedirs(path, exist_ok=True)
        return f"Created directory: {path}"
    except Exception as e:
        return f"Error creating directory: {e}"


def do_move_file(source, destination):
    """Move or rename a file/directory."""
    import shutil
    source = os.path.expanduser(source)
    destination = os.path.expanduser(destination)
    if not os.path.exists(source):
        return f"Source not found: {source}"
    try:
        shutil.move(source, destination)
        return f"Moved {source} → {destination}"
    except Exception as e:
        return f"Move error: {e}"


def do_delete_file(path):
    """Delete a file."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Not found: {path}"
    if os.path.isdir(path):
        return f"Use run_command to delete directories: {path}"
    try:
        os.remove(path)
        return f"Deleted: {path}"
    except Exception as e:
        return f"Delete error: {e}"


def do_multi_edit(path, edits):
    """Apply multiple edits to a single file in one pass."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    # Save to undo buffer
    _undo_buffer[os.path.abspath(path)].append(content)
    if not isinstance(edits, list):
        return "edits must be a list of {old_string, new_string} objects"
    applied = 0
    new_content = content
    for edit in edits:
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        if old and old in new_content:
            new_content = new_content.replace(old, new, 1)
            applied += 1
    if applied == 0:
        return f"No edits matched in {path}"
    _show_diff(path, content, new_content)
    with open(path, "w") as f:
        f.write(new_content)
    return f"Applied {applied}/{len(edits)} edits to {path}"


def do_clipboard_read():
    """Read system clipboard contents."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        content = result.stdout
        if not content:
            return "Clipboard is empty."
        if len(content) > 50000:
            content = content[:50000] + "\n... (truncated)"
        return content
    except FileNotFoundError:
        return "Clipboard not available (pbpaste not found)."
    except Exception as e:
        return f"Clipboard error: {e}"


def do_clipboard_write(content):
    """Write text to system clipboard."""
    try:
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, timeout=5)
        proc.communicate(input=content.encode("utf-8"))
        return f"Copied {len(content)} chars to clipboard."
    except FileNotFoundError:
        return "Clipboard not available (pbcopy not found)."
    except Exception as e:
        return f"Clipboard error: {e}"


def do_diff_apply(path, patch):
    """Apply a unified diff/patch to a file."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    _undo_buffer[os.path.abspath(path)].append(content)
    # Parse unified diff and apply
    lines = content.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    result_lines = list(lines)
    offset = 0
    for line in patch_lines:
        line_stripped = line.rstrip("\n")
        if line_stripped.startswith("@@"):
            import re as _re
            m = _re.search(r"@@ -(\d+)", line_stripped)
            if m:
                offset = int(m.group(1)) - 1
    # Simple approach: just apply via subprocess patch if available
    try:
        proc = subprocess.run(
            ["patch", path],
            input=patch, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            with open(path, "r") as f:
                new_content = f.read()
            _show_diff(path, content, new_content)
            return f"Patch applied to {path}"
        return f"Patch failed: {proc.stderr}"
    except FileNotFoundError:
        return "patch command not found. Install with: brew install gpatch"
    except Exception as e:
        return f"Patch error: {e}"
