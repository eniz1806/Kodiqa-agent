# Kodiqa - Local AI Coding Agent

Your personal Claude Code clone running 100% locally with free models — or powered by Claude/Qwen API for maximum intelligence.

## Install

```bash
git clone https://github.com/eniz1806/Kodiqa-agent.git
cd Kodiqa-agent
pip install .
```

Then run from any directory:
```
kodiqa
```

## Features

- **Claude Code-style UI** — `❯` prompt with separator line (prompt_toolkit), arrow-key navigation for all prompts
- **26 tools** — file ops, git, search, web, memory, clipboard, multi-edit, undo, diff apply
- **3 API providers** — Ollama (local/free), Claude API, Qwen API (DashScope)
- **MCP server support** — connect external tool servers via Model Context Protocol
- **Auto model discovery** — new Claude/Qwen models appear automatically from APIs
- **Interactive pickers** — `/model` and `/key` show numbered menus, navigate with arrows
- **Tab autocomplete** — slash commands, model names, file paths (prompt_toolkit)
- **Compact streaming** — hides code output, shows progress instead (toggle with `/verbose`)
- **Thinking display** — shows spinner for `<think>` reasoning blocks, line count summary
- **Multi-model consensus** — query all models, merge best answers
- **3 permission modes** — default (confirm all), relaxed (auto file ops), auto (no confirms)
- **Plan mode** — AI explores + plans, you approve, then it implements
- **Batch edit review** — queue edits, accept/reject per file with arrow keys
- **Context window management** — warns at 70%, auto-compacts at 85%, visual progress bar
- **Conversation branching** — save/switch between conversation states
- **Token tracking** — cost per response, session totals, tok/s speed
- **Prompt caching** — Claude API cache for faster + cheaper responses
- **Auto-retry** — exponential backoff on API errors (429, 5xx, timeouts)
- **Undo** — per-file undo buffer (up to 10 levels)
- **Checkpoints** — save/restore conversation state
- **Session export** — export conversation to markdown
- **Git-aware context** — auto-detects git repo, includes diff stats
- **Project indexing** — symbol extraction (def/class/function), cached
- **Shell env detection** — auto-detects OS, shell, dev tools
- **Diff preview** — colored diff before every file write/edit
- **Parallel tools** — read-only operations run concurrently
- **Conversation recovery** — auto-saved sessions, resume on crash
- **Workspace boundary** — asks permission before accessing files outside working directory
- **Ollama auto-management** — starts on launch, stops on quit
- **156 tests** — pytest test suite, all passing

## Arrow-Key UI

All interactive prompts use arrow keys — no typing letters:

```
  Allow: Write file: ~/project/app.py
    ❯ Yes
      Yes, don't ask again — for this action type
      No
```

Navigate with **↑↓ arrows** or **j/k**, press **Enter** to select, or **1/2/3** to jump.

Prompt uses a separator line (like Claude Code):
```
────────────────────────────────────────
❯ your prompt here
```

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/model <name>` | Switch model (interactive picker if no arg) |
| `/models` | List all available models (with live API discovery) |
| `/multi <models>` | Multi-model consensus mode |
| `/single` | Back to single model |
| `/scan [path]` | Scan project into context (with symbol extraction) |
| `/clear` | Clear conversation history |
| `/compact` | Summarize conversation to save context |
| `/memories` | Show stored memories |
| `/forget <id>` | Delete a memory |
| `/context` | Show project context file |
| `/key [provider]` | Add/update API key (interactive picker if no arg) |
| `/tokens` | Session token usage, cost, context bar |
| `/config` | Show config / `/config reload` to reload |
| `/export` | Export session to markdown file |
| `/checkpoint [n]` | Save conversation checkpoint |
| `/restore [n]` | Restore checkpoint (no arg = list all) |
| `/env` | Show detected shell environment |
| `/verbose` | Toggle compact/verbose streaming |
| `/mode [mode]` | Set permission mode (default/relaxed/auto) |
| `/plan` | Toggle plan mode (explore → approve → implement) |
| `/accept` | Toggle batch edit review |
| `/search <engine>` | Switch search engine (duckduckgo/google/api) |
| `/cd <path>` | Change working directory |
| `/branch` | Save/switch/list conversation branches |
| `/mcp` | Manage MCP tool servers (add/remove/list) |
| `/help` | Show help |
| `/quit` | Exit |

## Permission Modes

| Mode | Behavior |
|------|----------|
| `default` | Arrow-key confirm for all writes/commands (Yes / Don't ask again / No) |
| `relaxed` | Auto-approve file operations, only confirm commands + deletes |
| `auto` | No confirmations — everything auto-approved |

Switch with `/mode relaxed` or `/mode auto`. Default is `default`.

## Plan Mode

Activate with `/plan`. The AI will:
1. **Explore** — read files, search, analyze (no writes allowed)
2. **Present plan** — show what it intends to do
3. **You decide** — approve, revise, or reject (arrow keys)
4. **Implement** — on approval, AI executes the plan

## Batch Edit Review

When enabled (default ON, toggle with `/accept`), file edits are queued and presented for review:

```
  ? (1/3) app.py — write  +15 -3 lines
    ❯ Accept
      Reject
      Show diff
      Accept all — remaining 3 edits
      Reject all
```

Navigate with arrow keys, view diffs, accept/reject individually or in bulk.

## MCP Server Support

Connect external tool servers via the Model Context Protocol:

```
/mcp add mytools npx my-mcp-server     # connect a server
/mcp list                                # show connected servers + tools
/mcp remove mytools                      # disconnect
```

MCP tools are automatically available to the AI alongside built-in tools.

## Model Shortcuts

### Local Models (free, unlimited, requires Ollama)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model fast` | qwen3:30b-a3b | Fast answers, 30B brain at 3B speed (MoE) |
| `/model qwen` | qwen3:14b | General purpose, smart, thinking mode |
| `/model coder` | qwen3-coder | Coding agent (default without API key) |
| `/model reason` | phi4-reasoning | Deep reasoning, math, logic |
| `/model gpt` | gpt-oss | OpenAI's open model, reasoning + agentic |

### Claude API Models (paid, requires API key)

| Shortcut | Full Model | Price (in/out per MTok) |
|----------|-----------|-------------------------|
| `/model claude` / `sonnet` | claude-sonnet-4-6 | $3/$15 |
| `/model opus` | claude-opus-4-6 | $5/$25 |
| `/model haiku` | claude-haiku-4-5 | $1/$5 |
| `/model sonnet-4.5` | claude-sonnet-4-5 | $3/$15 |
| `/model opus-4.5` | claude-opus-4-5 | $5/$25 |
| `/model opus-4.1` | claude-opus-4-1 | $15/$75 |
| `/model sonnet-4` / `opus-4` | Legacy Claude 4 | varies |

### Qwen API Models (paid, Alibaba Cloud DashScope)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model qwen3.5` / `qwen-plus` | qwen3.5-plus | Newest flagship |
| `/model qwen3.5-flash` | qwen3.5-flash | Fast 3.5 |
| `/model qwen-max` | qwen3-max | Most powerful |
| `/model qwen-coder` | qwen3-coder-plus | Coding |
| `/model qwq` | qwq-plus | Deep reasoning |
| `/model qwen-long` | qwen-long-latest | 10M context |
| `/model qwen-math` | qwen-math-plus | Math |
| `/model qwen-turbo` | qwen-turbo | Cheapest/fastest |

New models are auto-discovered from the APIs — they appear in `/model` and `/models` automatically.

You can also use full model names: `/model qwen3:14b` or `/model claude-opus-4-6`

## Compact Streaming Mode

By default, Kodiqa hides code blocks during streaming and shows progress instead:

```
Kodiqa  I'll create the project structure...

  ⠋ Writing code (javascript)... 45 lines, 1,890 chars
  ╰─ code block: javascript 45 lines, 1,890 chars

Now the package.json:

  ⠋ Writing code (json)... 12 lines, 340 chars
  ╰─ code block: json 12 lines, 340 chars

  1,204 in / 847 out | 42.3 tok/s | ($0.0061 / session: $0.0183)
```

Use `/verbose` to toggle full output (see all code as it streams).

## API Setup

### Claude API
```
/key              → choose Claude, paste API key
/model claude     → use Claude Sonnet 4.6
```
Get your key: https://console.anthropic.com/settings/keys

### Qwen API (Alibaba Cloud DashScope)
```
/key              → choose Qwen, paste API key
/model qwen3.5    → use Qwen 3.5 Plus
```
Get your key: https://bailian.console.alibabacloud.com/?apiKey=1

## What You Can Ask

### File Operations
```
read the file ~/.zshrc
create a file called hello.py with a hello world program
edit main.py and change the function name from foo to bar
move config.json to config.backup.json
delete the temp file at ~/scratch.txt
```

### Multi-Edit & Undo
```
rename all occurrences of "oldName" to "newName" in utils.py
undo the last edit to main.py
```

### Search
```
find all .py files in ~/projects
search for "TODO" in my project
```

### Commands & Git
```
run npm install
show me the git status
commit these changes with message "fix login bug"
```

### Web Search
```
search the web for kotlin coroutines tutorial
fetch the content from https://some-docs-page.com
```

### Memory
```
remember that I prefer Kotlin for Android development
what do you remember about my preferences?
```

### Images & PDFs
```
look at this screenshot ~/Desktop/screenshot.png
read the PDF ~/Documents/report.pdf
```

### Clipboard
```
paste what's on my clipboard
copy this code to clipboard
```

### Project Analysis
```
/scan ~/myapp
now explain what this project does
find any bugs in this code
```

## Safety

- **Auto-approved**: reading files, listing dirs, searching, web, memory, clipboard read, undo
- **Asks permission**: writing/editing files, running commands, git commits, delete, move, clipboard write, patches
- **Workspace boundary**: asks before accessing files outside current working directory (Allow once / Allow directory / Deny)
- **Blocked**: `rm -rf /`, `sudo rm`, `mkfs`, `dd`, fork bombs, etc.
- **Permission modes**: `/mode default` (confirm all) → `/mode relaxed` (auto file ops) → `/mode auto` (no confirms)

## 26 Tools

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

## Files

```
~/LLMS/kodiqa/
  kodiqa.py          # Main agent (~3000 lines)
  actions.py         # 26 action handlers (~940 lines)
  tools.py           # Tool schemas (~460 lines)
  config.py          # Config, aliases, system prompt (~335 lines)
  web.py             # Web search + page fetch (~195 lines)
  memory.py          # SQLite persistent memory (82 lines)
  mcp.py             # MCP client (~175 lines)
  bin/kodiqa         # Global install script
  tests/             # 156 tests (pytest)
  pyproject.toml     # Package config (pip install .)
  requirements.txt   # Dependencies

~/.kodiqa/
  config.json        # User-editable config (overrides defaults)
  settings.json      # API keys, default model
  memory.db          # Persistent memories
  session.json       # Auto-saved conversation
  input_history      # prompt_toolkit history
  error.log          # Error log (capped 1MB)
  KODIQA.md          # Global context (always in system prompt)
  projects/          # Per-project context files
  checkpoints/       # Conversation checkpoints
  exports/           # Exported session markdown files
```

## Tips

- All prompts use **arrow keys** — no typing letters, just navigate and press Enter
- Default is **compact mode** — code hidden during streaming, progress shown instead
- Use `/verbose` when you want to see code as it streams
- Use `/mode relaxed` to skip file edit confirmations
- Use `/plan` for complex tasks — review the plan before implementation
- Use `/accept` to toggle batch edit review on/off
- Use `/branch save` before experimenting — switch back if it goes wrong
- Use `/mcp add` to connect external tool servers
- Use `/checkpoint` before risky operations, `/restore` to roll back
- Use `/export` to save a conversation for later reference
- Use `/tokens` to monitor API costs and context usage
- Use `/model` with no arg for interactive picker
- Use `/key` with no arg to choose provider
- Tab complete works for commands, models, and file paths
- New API models appear automatically — no code updates needed
- Memories persist forever across sessions
- Arrow keys work: up/down for history, left/right to edit
- Sessions auto-save — restart if anything goes wrong
- Ollama starts and stops automatically

## Testing

```bash
pytest -v          # 156 tests, all passing
```

## Requirements

- Python 3.9+
- Ollama installed (`/Applications/Ollama.app` on macOS)
- At least one model pulled (`ollama pull qwen3-coder`)
- (Optional) Claude API key for Claude models
- (Optional) DashScope API key for Qwen API models
