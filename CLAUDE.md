# Kodiqa - Local AI Coding Agent

## What This Is
An open-source AI coding agent that runs anywhere — free locally with Ollama, or supercharged by 7 cloud APIs (Claude, OpenAI, DeepSeek, Groq, Mistral, Qwen). Python CLI agent with multi-model consensus, 26 tools, MCP server support, auto model discovery, 3 permission modes, plan mode, batch edit review, tab autocomplete, context window management, conversation branching, thinking display, web search, persistent memory, compact streaming, custom plugins, sub-agents, LSP integration, 5 themes, and full filesystem access.

## Architecture

```
kodiqa.py    (~4252 lines)  Main agent: Kodiqa class, StreamWriter, KodiqaCompleter, prompt_toolkit UI, chat loops, slash commands, modes, MCP, branching, auto-discovery, workspace boundary, auto-commit, budget, lint, plugins, sub-agents, LSP, voice, themes
actions.py   (~950 lines)   26 action handlers: file ops, git, search, web, memory, clipboard, multi_edit, edit queue + diff preview
tools.py     (~461 lines)   Tool schemas (Claude native format, converted to OpenAI format for OpenAI-compat providers)
config.py    (~489 lines)   Constants, provider registry, model aliases, themes, system prompt, config, .kodiqaignore
web.py       (~194 lines)   3 search engines (DuckDuckGo, Google scrape, Google API) + page fetcher
memory.py    (82 lines)     SQLite-backed persistent memory store
mcp.py       (~176 lines)   MCP client: MCPServer (stdio JSON-RPC transport) + MCPManager (multi-server)
templates.py (61 lines)     5 project templates for /init command
lsp.py       (~220 lines)   LSP client for Language Server Protocol integration
```

## Test Suite

```
tests/           196 tests, all passing (~0.24s)
  conftest.py          Shared fixtures (sample_file, sample_tree, memory_store)
  test_parse_actions   Action parsing (~18 tests)
  test_config          Config functions + provider registry (~22 tests)
  test_file_ops        File operations (~15 tests)
  test_search_ops      Search operations (~13 tests)
  test_edit_queue      Edit queue + undo (~10 tests)
  test_memory          Memory store (~8 tests)
  test_dispatch        Dispatch + execution (~14 tests)
  test_web             Web functions (~7 tests)
  test_stream_writer   StreamWriter (~5 tests)
  test_mcp             MCP client (~27 tests)
  test_new_features    Thinking, branching, slash commands (~16 tests)
  test_features_v2     Themes, pin, alias, optimizer, templates, LSP, plugins, agents (~31 tests)
```

## Dual-Mode Design
- **Ollama models**: Text-based `[ACTION: name]...[/ACTION]` blocks parsed by `actions.py:parse_actions()`
- **Claude API**: Native tool_use with Anthropic's streaming SSE format + prompt caching
- **OpenAI-compat providers** (OpenAI, DeepSeek, Groq, Mistral, Qwen): Generic `_chat_openai_compat(provider)` with OpenAI-format tool calling, parameterized by provider registry
- All modes share the same `_dispatch()` handler in `actions.py`

## API Providers

| Provider | Config Key | Detection | Endpoint | Color |
|----------|-----------|-----------|----------|-------|
| Ollama | — | default | `localhost:11434` | green |
| Claude | `claude_api_key` | `is_claude_model()` | `api.anthropic.com` | yellow |
| OpenAI | `openai_api_key` | `get_openai_provider()` | `api.openai.com` | white |
| DeepSeek | `deepseek_api_key` | `get_openai_provider()` | `api.deepseek.com` | cyan |
| Groq | `groq_api_key` | `get_openai_provider()` | `api.groq.com` | red |
| Mistral | `mistral_api_key` | `get_openai_provider()` | `api.mistral.ai` | magenta |
| Qwen | `qwen_api_key` | `get_openai_provider()` | `dashscope-intl.aliyuncs.com` | blue |

### Provider Registry (config.py: `OPENAI_COMPAT_PROVIDERS`)
All 5 OpenAI-compatible providers share one implementation via a registry dict:
```python
OPENAI_COMPAT_PROVIDERS = {
    "openai": {"url": ..., "models_url": ..., "key_setting": ..., "color": ..., "label": ..., "aliases": {...}},
    "deepseek": {...},
    "groq": {...},
    "mistral": {...},
    "qwen": {...},
}
```
- `get_openai_provider(model_name)` — returns provider name or None
- `is_openai_compat_model(model_name)` — returns bool
- `is_qwen_api_model()` — backward-compat thin wrapper

### Provider Routing (kodiqa.py)
```python
def _dispatch_chat(self, user_msg):
    if is_claude_model(self.model) or self._is_live_claude(self.model):
        self._chat_claude(user_msg)
    else:
        provider = self._get_provider_for_model(self.model)
        if provider:
            self._chat_openai_compat(user_msg, provider)
        else:
            self._chat_ollama(user_msg)
```
- `_get_provider_for_model()` checks aliases + live cached API models
- `self.api_keys = {}` dict stores all provider keys from settings

## API Models

### Claude API (11 aliases)
| Alias | Model ID | Price (in/out per MTok) |
|-------|----------|-------------------------|
| `claude` / `sonnet` | claude-sonnet-4-6 | $3/$15 |
| `opus` | claude-opus-4-6 | $5/$25 |
| `haiku` | claude-haiku-4-5-20251001 | $1/$5 |
| `sonnet-4.5` | claude-sonnet-4-5-20250929 | $3/$15 |
| `opus-4.5` | claude-opus-4-5-20251101 | $5/$25 |
| `opus-4.1` | claude-opus-4-1-20250805 | $15/$75 |
| `sonnet-4` | claude-sonnet-4-20250514 | $3/$15 |
| `opus-4` | claude-opus-4-20250514 | $15/$75 |

### OpenAI API (6 aliases)
| Alias | Model ID |
|-------|----------|
| `gpt` / `gpt4` | gpt-4o |
| `gpt-mini` | gpt-4o-mini |
| `o3` / `o3-mini` / `o4-mini` | reasoning models |

### DeepSeek API (2 aliases)
| Alias | Model ID |
|-------|----------|
| `deepseek` | deepseek-chat |
| `deepseek-r1` | deepseek-reasoner |

### Groq API (4 aliases)
| Alias | Model ID |
|-------|----------|
| `llama` | llama-3.3-70b-versatile |
| `llama-small` | llama-3.1-8b-instant |
| `gemma` | gemma2-9b-it |
| `mixtral` | mixtral-8x7b-32768 |

### Mistral API (3 aliases)
| Alias | Model ID |
|-------|----------|
| `mistral` | mistral-large-latest |
| `mistral-small` | mistral-small-latest |
| `codestral` | codestral-latest |

### Qwen API (13 aliases)
| Alias | Model ID | Best For |
|-------|----------|----------|
| `qwen3.5` / `qwen-plus` | qwen3.5-plus-2026-02-15 | Newest flagship |
| `qwen3.5-flash` | qwen3.5-flash | Fast 3.5 |
| `qwen-max` | qwen3-max | Most powerful |
| `qwen-coder` | qwen3-coder-plus | Coding |
| `qwq` | qwq-plus | Deep reasoning |
| `qwen-long` | qwen-long-latest | 10M context |
| `qwen-math` | qwen-math-plus | Math |
| `qwen-turbo` | qwen-turbo | Cheapest/fastest |

### Auto Model Discovery
- `_fetch_api_models()` fetches live model lists from all API providers with keys set
- `_fetch_ollama_library()` scrapes ollama.com/library for available models (name, desc, pulls)
- Cached for 10 minutes to avoid repeated API calls
- New models appear automatically in `/model` picker and `/models` list
- Live models shown as "(live)" — usable by full model ID

### Ollama Lifecycle (kodiqa.py)
- `_ensure_ollama()` — starts Ollama if not running, tracks `_ollama_started_by_us`
- `_stop_ollama()` — stops only if we started it
- `_check_updates()` — checks installed models for updates, fetches available models from ollama.com
- Always starts Ollama on launch + checks updates
- Stops Ollama when switching to cloud model
- Restarts + checks updates when switching back to local model
- Stops on quit if we started it
- Welcome detects missing local models, guides user to pull or add API key
- Checks model exists before chatting, guides user if missing

### Session Summary (kodiqa.py: `_save_session_summary`)
- Auto-generates bullet-point summary on quit (2+ user messages required)
- Saves as `## Last Session` in project context file (`~/.kodiqa/projects/<project>.md`)
- Replaces previous summary each session, preserves manual notes
- Auto-loaded into system prompt on next start for continuity
- Works with all providers

### Prompt UI (kodiqa.py: prompt_toolkit + `_arrow_select`)
- Claude Code-style `❯` prompt with separator line, powered by `prompt_toolkit`
- `PromptSession` with `FileHistory`, `KodiqaCompleter`, styled prompt
- `KodiqaCompleter(Completer)` — tab completion for slash commands, model aliases (all providers), modes, file paths
- `_arrow_select(options, console, default)` — reusable arrow-key selector using `tty.setcbreak` + `os.write` to fd (cursor-up redraw)
- Supports ↑↓ arrow keys, j/k vim keys, number shortcuts, Enter to confirm
- Uses save/restore cursor (`\033[s`/`\033[u`) for in-place redraw
- Used in: permission confirm, edit review, plan approval

### Global Install
- `bin/kodiqa` — shell script that runs venv Python directly
- `pyproject.toml` — pip-installable package with `kodiqa` entry point
- Install: `pip install .` or `pip install -e .` (editable)
- Current version: v2.0.0

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

### Plan Mode (kodiqa.py: `self.plan_mode`)
- Activated with `/plan`
- Two-phase flow: AI explores + plans (read-only, no writes), user approves/revises/rejects, then AI implements

### Batch Edit Review (actions.py + kodiqa.py)
- Toggle with `/accept` (default ON: `self.batch_edits = True`)
- When enabled, file edits queue instead of applying immediately
- After AI finishes, interactive review: accept/reject per file, diff view, bulk accept/reject

### MCP Server Support (mcp.py + kodiqa.py)
- `MCPServer` class: stdio JSON-RPC transport, initialize/tools/list/tools/call
- `MCPManager` class: manages multiple servers, routes tool calls
- `/mcp add <name> <command>` — connect a server
- `/mcp remove <name>` — disconnect
- `/mcp list` — show connected servers + tools
- MCP tools automatically available to Claude and OpenAI-compat providers (merged via `_get_all_tools()`)
- MCP tool calls routed in both chat loops (split from regular tools)

### Tab Autocomplete (kodiqa.py: KodiqaCompleter)
- prompt_toolkit `Completer` subclass for slash commands, model aliases (all 7 providers), modes, search engines, file paths
- Context-aware: `/model` completes model names, `/cd` completes paths, `/mode` completes mode names

### Context Window Management (kodiqa.py)
- `_context_limit()`: 200K for Claude, 128K for OpenAI/Mistral, 64K for DeepSeek, 32K for Groq, 1M for Qwen, config-based for Ollama
- `_auto_compact_if_needed()`: warns at 70%, auto-compacts at 85%
- `/tokens` shows visual progress bar with percentage

### Conversation Branching (kodiqa.py)
- `/branch save <name>` — save current conversation state
- `/branch switch <name>` — switch to a saved branch (auto-saves current as `_previous`)
- `/branch delete <name>` — remove a branch
- `/branch list` — show all branches

### Thinking Display (kodiqa.py: StreamWriter)
- Detects `<think>...</think>` blocks from reasoning models
- Shows spinner during thinking, summary line count after
- Hidden in compact mode, passed through in verbose mode

### Compact Streaming Mode (kodiqa.py: StreamWriter)
- Default ON (`self.compact_mode = True`), toggle with `/verbose`
- Hides code blocks + ACTION blocks + think blocks with progress indicators

### Interactive Model Picker
- `/model` with no arg shows numbered list of all models (local + all API providers + live API)
- Pick by number or name
- Current model marked with arrow

### Interactive Key Picker
- `/key` with no arg shows all 6 providers (Claude + 5 OpenAI-compat) with status
- Shows set/not set for each provider

### Workspace Boundary Protection (kodiqa.py: `_check_workspace_boundary`)
- Checks if tool accesses files outside the current working directory
- Arrow-key prompt: Allow once / Allow directory / Deny
- Remembers allowed directories for session via `self._allowed_dirs`
- Applies to file ops, search, git tools — any tool with path parameters

### Session & State
- `~/.kodiqa/session.json` — auto-saved conversation for crash recovery
- `~/.kodiqa/memory.db` — SQLite persistent memory (survives across sessions)
- `~/.kodiqa/settings.json` — API keys (Claude, OpenAI, DeepSeek, Groq, Mistral, Qwen, Google), default model
- `~/.kodiqa/config.json` — user-editable config (overrides defaults)
- `~/.kodiqa/input_history` — prompt_toolkit FileHistory
- `~/.kodiqa/KODIQA.md` — global context file (always loaded into system prompt)
- `~/.kodiqa/projects/` — per-project context files
- `~/.kodiqa/checkpoints/` — conversation checkpoints (JSON)
- `~/.kodiqa/exports/` — exported session markdown files
- `~/.kodiqa/error.log` — error log

### Slash Commands (49 total)
| Command | Description |
|---------|-------------|
| `/model <name>` | Switch model (interactive picker if no arg) |
| `/multi <models>` | Multi-model consensus mode |
| `/single` | Back to single model |
| `/models` | List all available models (with live API discovery) |
| `/scan [path]` | Scan project into context (with progress + symbol extraction) |
| `/clear` | Clear conversation |
| `/compact` | Summarize conversation to save context |
| `/memories` | Show stored memories |
| `/forget <id>` | Delete a memory |
| `/context` | Show project context file |
| `/key [provider]` | Add/update API key (interactive picker if no arg) |
| `/tokens` | Session token usage + cost + visual progress bar |
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
| `/branch` | Save/switch/list conversation branches |
| `/mcp` | Manage MCP tool servers (add/remove/list) |
| `/autocommit` | Toggle auto git commit after AI edits |
| `/budget <amount>` | Set session budget limit (warns 80%, blocks 100%) |
| `/undo [path]` | Undo last edit / list undo history |
| `/diff [args]` | Show git diff (supports --staged etc.) |
| `/lint <cmd>` | Set auto-lint command after edits (/lint off to disable) |
| `/pin <path>` | Pin file to always include in context |
| `/unpin <path>` | Remove pinned file |
| `/alias <name> <cmd>` | Create command alias |
| `/unalias <name>` | Remove command alias |
| `/notify` | Toggle desktop notifications for long tasks |
| `/optimizer` | Toggle cost optimizer tips |
| `/theme <name>` | Switch UI theme (dark/light/dracula/monokai/nord) |
| `/share` | Export session as styled HTML |
| `/pr [title]` | Create GitHub PR via gh CLI |
| `/review [number]` | Review PR diff via gh CLI |
| `/issue [number]` | View GitHub issue via gh CLI |
| `/init [template]` | Scaffold project from template |
| `/plugins` | List/reload custom tool plugins |
| `/agent <task>` | Spawn sub-agent for background task |
| `/agents` | List running/completed sub-agents |
| `/lsp [start\|stop]` | Start/stop Language Server Protocol |
| `/voice` | Voice input via sox + Whisper |
| `/help` | Show help |
| `/quit` | Exit |

## Development

### Run
```bash
source ~/LLMS/kodiqa/venv/bin/activate && python ~/LLMS/kodiqa/kodiqa.py
```

### Test
```bash
source ~/LLMS/kodiqa/venv/bin/activate && pytest -v
```

### Dependencies
- Python 3.9+, rich, beautifulsoup4, requests, prompt_toolkit, pytest (dev)
- Ollama installed at `/Applications/Ollama.app`
- Virtual environment at `./venv/`
- Current version: v2.0.0

### Adding a New Tool
1. Add the handler function `do_<name>()` in `actions.py`
2. Register it in `_dispatch()` handler map in `actions.py`
3. Add it to `CLAUDE_TOOLS` list in `tools.py` (used by both Claude and OpenAI-compat)
4. Add `[ACTION: name]` docs to `SYSTEM_PROMPT` in `config.py` (for Ollama text mode)
5. If it needs confirmation, add to `CONFIRM_ACTIONS` in `config.py`
6. If it's read-only, add to `read_only` set in `execute_tools_parallel()` in `actions.py`
7. Add label to `_tool_label()` in `kodiqa.py`
8. Add description to `_describe_action()` in `actions.py`

### Adding a New API Provider
1. Add entry to `OPENAI_COMPAT_PROVIDERS` in `config.py` with url, models_url, key_setting, key_prefix, color, label, aliases
2. That's it — the generic `_chat_openai_compat(provider)` handles everything automatically
3. (Optional) Add provider-specific context limit in `_context_limit()` in `kodiqa.py`

### Adding a New Model Alias
Add to `MODEL_ALIASES` (Ollama), `CLAUDE_ALIASES` (Claude API), or the provider's `aliases` dict in `OPENAI_COMPAT_PROVIDERS` (config.py). Or just use full model name — live API models are auto-discovered.

### Adding a New Slash Command
Add the handler in `_handle_slash()` method in `kodiqa.py`. Update `/help` text. Add to `_SLASH_COMMANDS` list.

## Conventions
- 165 tests via pytest (`tests/` directory)
- No type hints — plain Python 3.9 style
- Rich library for all terminal UI (panels, markdown, prompts, status spinners)
- All file paths are expanded with `os.path.expanduser()` before use
- Action results are truncated at 20,000 chars to avoid context overflow
- Diff preview shown before every file write/edit (capped at 50 lines)
- `git_commit` does `git add -A` then commits (stages everything)
- API colors: Claude=yellow, OpenAI=white, DeepSeek=cyan, Groq=red, Mistral=magenta, Qwen=blue, Ollama=green, Consensus=magenta
- Per-file undo buffer: `deque(maxlen=10)` storing content before each edit/write
- Shell env detection at startup (OS, shell, Python, git, node, cargo, go, java, docker)
- prompt_toolkit for `❯` prompt with separator line, tab completion, file history, bottom padding
- Arrow-key selector for all interactive prompts (tty/termios raw input, save/restore cursor)
- Stream interrupt: Esc or Ctrl+C stops any streaming response instantly (all 3 providers)
- pip-installable via `pyproject.toml` with `kodiqa` console script entry point
- Workspace boundary protection: asks before accessing files outside cwd
