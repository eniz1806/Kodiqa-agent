# Kodiqa - Local AI Coding Agent

## What This Is
A Claude Code clone that runs 100% locally using free Ollama models, with optional Claude API and Qwen API for smarter responses. Python CLI agent with multi-model consensus, 26 tools, 3 permission modes, plan mode, batch edit review, web search, persistent memory, compact streaming, and full filesystem access.

## Architecture

```
kodiqa.py  (~2580 lines)  Main agent: Kodiqa class, StreamWriter, chat loops, slash commands, modes, Ollama + Claude + Qwen API
actions.py (~940 lines)   26 action handlers: file ops, git, search, web, memory, clipboard, multi_edit, edit queue + diff preview
tools.py   (~460 lines)   Tool schemas (Claude native format, converted to OpenAI format for Qwen)
config.py  (~290 lines)   Constants, model aliases (Ollama/Claude/Qwen), system prompt, user-editable config
web.py     (~195 lines)   3 search engines (DuckDuckGo, Google scrape, Google API) + page fetcher
memory.py  (82 lines)     SQLite-backed persistent memory store
```

## Triple-Mode Design
- **Ollama models**: Text-based `[ACTION: name]...[/ACTION]` blocks parsed by `actions.py:parse_actions()`
- **Claude API**: Native tool_use with Anthropic's streaming SSE format + prompt caching
- **Qwen API**: OpenAI-compatible tool calling (DashScope endpoint, same tool schemas converted via `_get_qwen_tools()`)
- All three modes share the same `_dispatch()` handler in `actions.py`

## API Providers

| Provider | Config Key | Aliases Dict | Detection | Endpoint |
|----------|-----------|-------------|-----------|----------|
| Ollama | — | `MODEL_ALIASES` | default | `localhost:11434` |
| Claude | `claude_api_key` | `CLAUDE_ALIASES` | `is_claude_model()` | `api.anthropic.com` |
| Qwen | `qwen_api_key` | `QWEN_ALIASES` | `is_qwen_api_model()` | `dashscope-intl.aliyuncs.com` |

## Key Patterns

### Tool Safety Tiers (config.py)
- **Auto-approved** (no confirm): read_file, list_dir, tree, glob, grep, git_status, git_diff, web_search, web_fetch, memory_search, ask_user, clipboard_read, create_directory, read_image, read_pdf, undo_edit
- **Requires confirmation** (`CONFIRM_ACTIONS`): write_file, edit_file, run_command, git_commit, search_replace_all, move_file, delete_file, multi_edit, clipboard_write, diff_apply
- **Blocked** (`BLOCKED_COMMANDS`): rm -rf /, sudo rm -rf, mkfs, dd, fork bombs, etc.

### 26 Tools
| Category | Tools |
|----------|-------|
| File ops | read_file, write_file, edit_file, multi_edit, search_replace_all, create_directory, move_file, delete_file, undo_edit |
| Search | glob, grep, list_dir, tree |
| Commands | run_command |
| Git | git_status, git_diff, git_commit |
| Web | web_search, web_fetch |
| Media | read_image, read_pdf |
| Memory | memory_store, memory_search |
| Clipboard | clipboard_read, clipboard_write |
| Patch | diff_apply |
| UX | ask_user |

### Permission Modes (kodiqa.py: `self.permission_mode`)
- **default** — 3-choice confirmation for all write/command actions (Yes / Yes don't ask again / No)
- **relaxed** — auto-approve file ops, only confirm commands (run_command, git_commit, delete_file)
- **auto** — no confirmations, everything auto-approved
- Toggle with `/mode [default|relaxed|auto]`
- Implemented in `_confirm()` method

### Plan Mode (kodiqa.py: `self.plan_mode`)
- Activated with `/plan`
- Two-phase flow: AI explores + plans (read-only, no writes), user approves/revises/rejects, then AI implements
- Plan prefix injected into user message in `_chat()`
- `_show_plan_approval()` presents approve/revise/reject panel
- State: `self.plan_mode`, `self._pending_plan`, `self._plan_request`

### Batch Edit Review (actions.py + kodiqa.py)
- Toggle with `/accept` (default ON: `self.batch_edits = True`)
- When enabled, `set_batch_mode(True)` in actions.py causes file edits to queue instead of apply
- Edit queue: `_edit_queue` list, manipulated via `set_batch_mode()`, `get_edit_queue()`, `apply_queued_edit()`, `reject_queued_edit()`, `clear_edit_queue()`
- After AI finishes, `_review_edit_queue()` shows interactive review:
  - Per-file summary with accept (a) / reject (r) / diff (d) / next (n) / prev (p) controls
  - Accept All (A) / Reject All (R) / quit (q)
- Wired into all 3 chat loops (Ollama, Claude, Qwen)

### Compact Streaming Mode (kodiqa.py: StreamWriter)
- Default ON (`self.compact_mode = True`), toggle with `/verbose`
- `StreamWriter` class intercepts streaming output token-by-token
- Detects ` ``` ` code fences and `[ACTION:]` blocks in real-time
- **Text/explanations** — shown normally as they stream
- **Code blocks** — hidden, replaced with live spinner showing line count + char count
- **ACTION blocks** — hidden with progress indicator (Ollama text mode)

### Parallel Execution (actions.py)
Read-only tools run in `ThreadPoolExecutor(max_workers=4)`. Write/command tools run sequentially with user confirmation.

### Multi-Model Consensus (kodiqa.py)
Models queried **sequentially** (one at a time, `keep_alive: 0` to free RAM). A judge model merges responses into a consensus answer. Supports mixing Ollama + Claude + Qwen API models.

### Error Handling & Retry
- `_retry_api_call(fn, max_retries=3, backoff_base=2)` — retries on 429, 5xx, ConnectionError
- Error logging to `~/.kodiqa/error.log` (capped at 1MB)
- Graceful degradation with provider switch suggestions

### Token Usage & Cost Tracking
- `COST_TABLE` maps model IDs to (input, output) pricing per 1M tokens
- `_display_token_usage()` shows tokens, tok/s, and cost after each response
- `self.session_tokens` tracks cumulative input/output/cache/cost
- `/tokens` shows session totals + context estimate

### Prompt Caching (Claude)
- `anthropic-beta: prompt-caching-2024-07-31` header
- System prompt + last tool definition marked with `cache_control: {"type": "ephemeral"}`
- Cache hits visible in token usage display

### Session & State
- `~/.kodiqa/session.json` — auto-saved conversation for crash recovery
- `~/.kodiqa/memory.db` — SQLite persistent memory (survives across sessions)
- `~/.kodiqa/settings.json` — API keys (Claude, Qwen, Google), default model
- `~/.kodiqa/config.json` — user-editable config (overrides defaults)
- `~/.kodiqa/input_history` — readline arrow-key history (500 entries)
- `~/.kodiqa/KODIQA.md` — global context file (always loaded into system prompt)
- `~/.kodiqa/projects/` — per-project context files
- `~/.kodiqa/checkpoints/` — conversation checkpoints (JSON)
- `~/.kodiqa/exports/` — exported session markdown files
- `~/.kodiqa/error.log` — error log

### Slash Commands (27 total)
| Command | Description |
|---------|-------------|
| `/model <name>` | Switch model (local: fast, qwen, coder, reason, gpt; Claude: claude, sonnet, haiku, opus; Qwen: qwen-api, qwen-max, qwen-coder-api, qwen-flash-api) |
| `/multi <models>` | Multi-model consensus mode |
| `/single` | Back to single model |
| `/models` | List all available models |
| `/scan [path]` | Scan project into context (with progress) |
| `/clear` | Clear conversation |
| `/compact` | Summarize conversation to save context |
| `/memories` | Show stored memories |
| `/forget <id>` | Delete a memory |
| `/context` | Show project context file |
| `/key [provider]` | Add/update API key |
| `/tokens` | Session token usage + cost |
| `/config` | Show/reload config |
| `/export` | Export session to markdown |
| `/checkpoint [name]` | Save conversation checkpoint |
| `/restore [name]` | Restore from checkpoint |
| `/env` | Show shell environment |
| `/verbose` | Toggle compact/verbose streaming |
| `/mode [mode]` | Set permission mode (default/relaxed/auto) |
| `/plan` | Toggle plan mode |
| `/accept` | Toggle batch edit review |
| `/search` | Switch search engine |
| `/cd <path>` | Change working directory |
| `/help` | Show help |
| `/quit` | Exit |

## Development

### Run
```bash
source ~/LLMS/kodiqa/venv/bin/activate && python ~/LLMS/kodiqa/kodiqa.py
```

### Dependencies
- Python 3.9+, rich, beautifulsoup4, requests
- Ollama installed at `/Applications/Ollama.app`
- Virtual environment at `./venv/`

### Adding a New Tool
1. Add the handler function `do_<name>()` in `actions.py`
2. Register it in `_dispatch()` handler map in `actions.py`
3. Add it to `CLAUDE_TOOLS` list in `tools.py` (used by both Claude and Qwen)
4. Add `[ACTION: name]` docs to `SYSTEM_PROMPT` in `config.py` (for Ollama text mode)
5. If it needs confirmation, add to `CONFIRM_ACTIONS` in `config.py`
6. If it's read-only, add to `read_only` set in `execute_tools_parallel()` in `actions.py`
7. Add label to `_tool_label()` in `kodiqa.py`
8. Add description to `_describe_action()` in `actions.py`

### Adding a New API Provider
1. Add aliases dict and API URL constant in `config.py`
2. Add `is_<provider>_model()` detection function in `config.py`
3. Add `_chat_<provider>()`, `_call_<provider>_stream()`, `_multi_query_<provider>()`, `_<provider>_nostream()` in `kodiqa.py`
4. Update `_chat()` dispatch, `_chat_multi()`, `_compact()`, `/model`, `/key`, `/help`, `_list_models()`, `_welcome()`

### Adding a New Model Alias
Add to `MODEL_ALIASES` (Ollama), `CLAUDE_ALIASES` (Claude API), or `QWEN_ALIASES` (Qwen API) in `config.py`.

### Adding a New Slash Command
Add the handler in `_handle_slash()` method in `kodiqa.py`. Update `/help` text.

## Conventions
- No tests yet — manual testing only
- No type hints — plain Python 3.9 style
- Rich library for all terminal UI (panels, markdown, prompts, status spinners)
- All file paths are expanded with `os.path.expanduser()` before use
- Action results are truncated at 20,000 chars to avoid context overflow
- Diff preview shown before every file write/edit (capped at 50 lines)
- `git_commit` does `git add -A` then commits (stages everything)
- API colors: Claude = yellow, Qwen = blue, Ollama = green, Consensus = magenta
- Per-file undo buffer: `deque(maxlen=10)` storing content before each edit/write
- Shell env detection at startup (OS, shell, Python, git, node, cargo, go, java, docker)
