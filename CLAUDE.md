# Kodiqa - Local AI Coding Agent

## What This Is
A Claude Code clone that runs 100% locally using free Ollama models, with optional Claude API and Qwen API for smarter responses. Python CLI agent with multi-model consensus, 19 tools, web search, persistent memory, and full filesystem access.

## Architecture

```
kodiqa.py  (~1600 lines)  Main agent: Kodiqa class, chat loop, slash commands, Ollama + Claude + Qwen API
actions.py (659 lines)    19 action handlers: file ops, git, search, web, memory, ask_user + diff preview
tools.py   (307 lines)    Tool schemas (Claude native format, converted to OpenAI format for Qwen)
config.py  (~210 lines)   Constants, model aliases (Ollama/Claude/Qwen), system prompt, settings I/O
web.py     (190 lines)    3 search engines (DuckDuckGo, Google scrape, Google API) + page fetcher
memory.py  (82 lines)     SQLite-backed persistent memory store
```

## Triple-Mode Design
- **Ollama models**: Text-based `[ACTION: name]...[/ACTION]` blocks parsed by `actions.py:parse_actions()`
- **Claude API**: Native tool_use with Anthropic's streaming SSE format
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
- **Auto-approved** (no confirm): read_file, list_dir, tree, glob, grep, git_status, git_diff, web_search, web_fetch, memory_search, ask_user
- **Requires confirmation** (`CONFIRM_ACTIONS`): write_file, edit_file, run_command, git_commit
- **Blocked** (`BLOCKED_COMMANDS`): rm -rf /, sudo rm -rf, mkfs, dd, fork bombs, etc.

### Parallel Execution (actions.py)
Read-only tools run in `ThreadPoolExecutor(max_workers=4)`. Write/command tools run sequentially with user confirmation.

### Multi-Model Consensus (kodiqa.py)
Models queried **sequentially** (one at a time, `keep_alive: 0` to free RAM). A judge model merges responses into a consensus answer. Supports mixing Ollama + Claude + Qwen API models.

### Session & State
- `~/.kodiqa/session.json` — auto-saved conversation for crash recovery
- `~/.kodiqa/memory.db` — SQLite persistent memory (survives across sessions)
- `~/.kodiqa/settings.json` — API keys (Claude, Qwen, Google), default model
- `~/.kodiqa/input_history` — readline arrow-key history (500 entries)
- `~/.kodiqa/KODIQA.md` — global context file (always loaded into system prompt)
- `~/.kodiqa/projects/` — per-project context files

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

### Adding a New API Provider
1. Add aliases dict and API URL constant in `config.py`
2. Add `is_<provider>_model()` detection function in `config.py`
3. Add `_chat_<provider>()`, `_call_<provider>_stream()`, `_multi_query_<provider>()`, `_<provider>_nostream()` in `kodiqa.py`
4. Update `_chat()` dispatch, `_chat_multi()`, `_compact()`, `/model`, `/key`, `/help`, `_list_models()`, `_welcome()`

### Adding a New Model Alias
Add to `MODEL_ALIASES` (Ollama), `CLAUDE_ALIASES` (Claude API), or `QWEN_ALIASES` (Qwen API) in `config.py`.

### Adding a New Slash Command
Add the handler in `_handle_slash()` method in `kodiqa.py`.

## Conventions
- No tests yet — manual testing only
- No type hints — plain Python 3.9 style
- Rich library for all terminal UI (panels, markdown, prompts, status spinners)
- All file paths are expanded with `os.path.expanduser()` before use
- Action results are truncated at 20,000 chars to avoid context overflow
- Diff preview shown before every file write/edit (capped at 50 lines)
- `git_commit` does `git add -A` then commits (stages everything)
- API colors: Claude = yellow, Qwen = blue, Ollama = green, Consensus = magenta
