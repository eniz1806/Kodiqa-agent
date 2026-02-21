"""Kodiqa action parser and executor - 18 tools mirroring Claude Code."""

import base64
import os
import re
import subprocess
import difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import (
    CONFIRM_ACTIONS, BLOCKED_COMMANDS, COMMAND_TIMEOUT,
    MAX_FILE_SIZE, SKIP_DIRS, SKIP_EXTENSIONS,
)
from web import search_duckduckgo, fetch_page, format_results

# ── Console reference (set by kodiqa.py on startup) ──
_console = None

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
                 "web_search", "web_fetch", "memory_search"}

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
    }
    handler = handlers.get(name)
    if handler:
        return handler()
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
    path = os.path.expanduser(path)
    # Show diff if file exists
    old_content = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", errors="replace") as f:
                old_content = f.read()
        except Exception:
            pass
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
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with open(path, "r") as f:
        content = f.read()
    if old_text not in content:
        return f"Text not found in {path}. Make sure the old text matches exactly."
    count = content.count(old_text)
    new_content = content.replace(old_text, new_text, 1)
    # Show diff
    _show_diff(path, content, new_content)
    with open(path, "w") as f:
        f.write(new_content)
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
