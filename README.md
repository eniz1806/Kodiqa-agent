<p align="center">
  <img src="assets/logo.svg" alt="Kodiqa Logo" width="700"/>
</p>

<p align="center">
  <strong>The AI coding agent that runs anywhere — free locally with Ollama, or supercharged by 7 cloud APIs. One agent, every model, zero limits.</strong>
</p>

<p align="center">
  <em>69 slash commands &bull; 26 tools &bull; RAG search &bull; custom personas &bull; plugins &bull; sub-agents &bull; LSP &bull; 5 themes</em>
</p>

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/python-3.9+-blue?logo=python&logoColor=white" alt="Python 3.9+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-purple" alt="License"/></a>
  <a href="#testing"><img src="https://img.shields.io/badge/tests-284%20passing-brightgreen" alt="Tests"/></a>
  <a href="#api-setup"><img src="https://img.shields.io/badge/providers-7-cyan" alt="7 Providers"/></a>
  <a href="#26-tools"><img src="https://img.shields.io/badge/commands-69-orange" alt="69 Commands"/></a>
</p>

---

## How Kodiqa Compares

| Feature | Kodiqa | Claude Code | Aider | Gemini CLI | OpenCode |
|---------|--------|-------------|-------|------------|----------|
| **Price** | Free (Ollama) or pay-per-token | $20/mo (Pro) or pay-per-token | Pay-per-token only | Free (Gemini Flash) | Pay-per-token only |
| **Local/offline** | Yes (Ollama) | No | No | No | Yes (Ollama) |
| **API providers** | 7 (Ollama, Claude, OpenAI, DeepSeek, Groq, Mistral, Qwen) | 1 (Claude) | 10+ (OpenAI, Claude, etc.) | 1 (Gemini) | 75+ (OpenAI, Claude, Gemini, Ollama, etc.) |
| **Tools** | 26 built-in | ~15 built-in | ~10 built-in | ~12 built-in | ~12 built-in |
| **MCP support** | Yes | Yes | No | Yes | Yes |
| **Multi-model** | Yes (consensus mode) | No | No | No | No |
| **Plan mode** | Yes | Yes | No | No | No |
| **Permission modes** | 3 (default/relaxed/auto) | 2 (normal/auto) | 1 (confirm all) | 2 (normal/sandbox) | 2 (normal/auto) |
| **Batch edit review** | Yes (per-file accept/reject) | No | No | No | No |
| **Auto model discovery** | Yes (live from APIs) | No | No | No | No |
| **Budget limit** | Yes (`/budget`) | No | No | No | No |
| **Auto-lint** | Yes (`/lint`) | No | Yes (built-in) | No | No |
| **Auto git commit** | Yes (`/autocommit`) | Yes | Yes (default) | No | No |
| **Undo** | Yes (10 levels per file) | No | Yes (git-based) | No | No |
| **Conversation branching** | Yes (`/branch`) | No | No | No | No |
| **Context management** | Auto-compact at 85% | Auto-compact | Repo map | Auto (1M context) | LSP-based |
| **Web search** | Yes (3 engines) | No | No | Yes (Google) | No |
| **Persistent memory** | Yes (SQLite) | Yes (CLAUDE.md) | No | Yes (Gemini memory) | No |
| **Tab autocomplete** | Yes | Yes | No | Yes | Yes |
| **Thinking display** | Yes (spinner + summary) | Yes | No | Yes | Yes |
| **Project indexing** | Yes (symbol extraction) | Yes | Yes (repo map) | No | Yes (LSP) |
| **Session recovery** | Yes (auto-save) | Yes | No | No | Yes (multi-session) |
| **Custom agents** | Yes (sub-agents) | No | No | No | Yes |
| **Desktop app / IDE** | No | Yes (VS Code) | No | No | Yes (VS Code, desktop) |
| **Install** | `pip install .` | `npm install -g` | `pip install` | `npm install -g` | `go install` / `npm` |
| **Language** | Python | TypeScript | Python | TypeScript | Go |
| **Tests** | 232 | Yes | Yes | Yes | Yes |
| **Open source** | Yes (AGPL-3.0) | Yes (Apache-2.0) | Yes (Apache-2.0) | Yes (Apache-2.0) | Yes (MIT) |

**Kodiqa's unique advantages**: free local models, 7 API providers, multi-model consensus, custom plugins, sub-agents, LSP integration, 5 themes, project templates, batch edit review, conversation branching, budget limits, auto-lint, and auto model discovery — features no other agent offers together.

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
- **7 API providers** — Ollama (local/free), Claude, OpenAI, DeepSeek, Groq, Mistral, Qwen
- **MCP server support** — connect external tool servers via Model Context Protocol
- **Auto model discovery** — new Claude/Qwen models appear automatically from APIs
- **Interactive pickers** — `/model` and `/key` show numbered menus, navigate with arrows
- **Tab autocomplete** — slash commands, model names, file paths (prompt_toolkit)
- **Compact streaming** — hides code output, shows progress instead (toggle with `/verbose`)
- **Stream interrupt** — press Esc or Ctrl+C to stop any response instantly
- **Stream stall indicator** — animated spinner when response pauses (so you know it's still working)
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
- **Session summary** — auto-saves context summary on quit, loaded on next start
- **Conversation recovery** — auto-saved sessions, resume on crash
- **Workspace boundary** — asks permission before accessing files outside working directory
- **Smart Ollama lifecycle** — starts on launch, stops when switching to cloud, restarts on local switch
- **Dynamic model library** — fetches available Ollama models from ollama.com with pull counts
- **Unlimited iterations** — no artificial cap, AI keeps working until the task is done
- **Live API model routing** — auto-discovered models from Claude/Qwen APIs routed to correct provider
- **Auto git commit** — toggle with `/autocommit`, auto-commits after AI edits with descriptive message
- **`.kodiqaignore`** — per-project file exclusion (like `.gitignore` for scans/searches)
- **Budget limit** — `/budget 5` sets $5 session limit, warns at 80%, blocks at 100%
- **Auto-lint** — `/lint ruff check --fix` runs linter after edits, feeds errors back to AI
- **Custom personas** — `/persona` switches AI expertise (security-expert, code-reviewer, teacher, architect, debugger)
- **RAG search** — `/embed` indexes codebase, `/rag` searches with AI-enhanced context
- **Test generation** — `/test <file>` auto-generates unit tests
- **Git diff review** — `/review-local` AI reviews staged changes
- **Interactive debugger** — `/debug <script>` runs, catches errors, debugs with AI
- **Diagram generation** — `/diagram` generates Mermaid diagrams via AI
- **File watcher** — `/watch <path>` monitors for changes
- **Config profiles** — `/profile save/load` manages settings presets
- **Multi-file refactoring** — `/refactor rename/extract` across project
- **Session history** — `/history` browses and resumes past sessions
- **Clipboard patches** — `/patch` applies diffs from clipboard
- **Changelog** — `/changelog` shows version history
- **Session stats** — `/stats` shows metrics (files, tools, time, cost)
- **232 tests** — pytest test suite, all passing

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
| `/autocommit` | Toggle auto git commit after AI edits |
| `/budget <amount>` | Set session budget limit (warns 80%, blocks 100%) |
| `/undo [path]` | Undo last edit / list undo history |
| `/diff [args]` | Show git diff (supports --staged etc.) |
| `/lint <cmd>` | Auto-lint after edits (`/lint off` to disable) |
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
| `/changelog` | Show version history |
| `/stats` | Session metrics (files, tools, time, cost) |
| `/review-local` | AI review of staged git changes |
| `/test <file>` | Generate unit tests for a file |
| `/persona <name>` | Switch AI persona (security-expert, code-reviewer, etc.) |
| `/patch` | Apply diff/patch from clipboard |
| `/profile` | Save/load config profiles |
| `/refactor` | Multi-file refactoring (rename, extract) |
| `/history` | Browse and resume past sessions |
| `/watch <path>` | Watch files for changes |
| `/embed [path]` | Index files for RAG search |
| `/rag <query>` | RAG search + AI answer |
| `/debug <script>` | Run script, catch errors, debug with AI |
| `/diagram <desc>` | Generate Mermaid diagram |
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
| `/model gpt-local` | gpt-oss | OpenAI's open model, reasoning + agentic |

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
| `/model qwen-max` / `qwen3-max` | qwen3-max | Most powerful |
| `/model qwen-coder` / `qwen3-coder` | qwen3-coder-plus | Coding |
| `/model qwen-coder-next` | qwen3-coder-next | Newest coder |
| `/model qwq` | qwq-plus | Deep reasoning |
| `/model qwen-flash` | qwen3.5-flash | Fast |
| `/model qwen-turbo` | qwen-turbo | Cheapest/fastest |
| `/model qwen-math` | qwen-math-plus | Math |
| `/model glm-5` | glm-5 | Third-party (Coding Plan) |
| `/model kimi` | kimi-k2.5 | Third-party (Coding Plan) |

**Qwen Coding Plan**: If you have a Coding Plan subscription (`sk-sp-` key), `/key qwen` auto-detects it and configures the dedicated endpoint. Supports $3/mo Lite and $15/mo Pro tiers.

### OpenAI API Models (paid, requires API key)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model gpt` | gpt-4o | General purpose flagship |
| `/model gpt-mini` | gpt-4o-mini | Fast and cheap |
| `/model o3` | o3 | Deep reasoning |
| `/model o3-mini` | o3-mini | Fast reasoning |
| `/model o4-mini` | o4-mini | Latest reasoning |

### DeepSeek API Models (paid, requires API key)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model deepseek` | deepseek-chat | V3 general purpose |
| `/model deepseek-r1` | deepseek-reasoner | R1 deep reasoning |

### Groq API Models (free tier available)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model llama` | llama-3.3-70b-versatile | Best open model |
| `/model llama-small` | llama-3.1-8b-instant | Ultra fast |
| `/model gemma` | gemma2-9b-it | Google's open model |
| `/model mixtral` | mixtral-8x7b-32768 | MoE, 32K context |

### Mistral API Models (paid, requires API key)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model mistral` | mistral-large-latest | Flagship |
| `/model mistral-small` | mistral-small-latest | Fast and cheap |
| `/model codestral` | codestral-latest | Code generation |

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

Use `/key` to add API keys interactively (shows all 6 providers), or specify directly:

| Provider | Command | Get Key |
|----------|---------|---------|
| Claude | `/key claude` | https://console.anthropic.com/settings/keys |
| OpenAI | `/key openai` | https://platform.openai.com/api-keys |
| DeepSeek | `/key deepseek` | https://platform.deepseek.com/api_keys |
| Groq | `/key groq` | https://console.groq.com/keys |
| Mistral | `/key mistral` | https://console.mistral.ai/api-keys |
| Qwen | `/key qwen` | https://bailian.console.alibabacloud.com/?apiKey=1 |

Then switch: `/model claude`, `/model gpt`, `/model deepseek`, `/model llama`, `/model mistral`, `/model qwen3.5`

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
  kodiqa.py          # Main agent (~4970 lines)
  actions.py         # 26 action handlers (~950 lines)
  tools.py           # Tool schemas (~461 lines)
  config.py          # Config, themes, provider registry (~571 lines)
  web.py             # Web search + page fetch (~194 lines)
  memory.py          # SQLite persistent memory (82 lines)
  mcp.py             # MCP client (~176 lines)
  templates.py       # 5 project templates (61 lines)
  lsp.py             # LSP client (~220 lines)
  embeddings.py      # RAG vector store (~100 lines)
  bin/kodiqa         # Global install script
  tests/             # 232 tests (pytest)
  pyproject.toml     # Package config (pip install .)
  requirements.txt   # Dependencies

~/.kodiqa/
  config.json        # User-editable config (overrides defaults)
  settings.json      # API keys, default model
  memory.db          # Persistent memories
  session.json       # Auto-saved conversation
  input_history      # prompt_toolkit FileHistory
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
- Session summary auto-saved on quit — next start has full context
- Type `quit` or `exit` (no slash needed) to exit
- Ollama starts/stops automatically — stops on cloud switch, restarts on local switch

## Testing

```bash
pytest -v          # 232 tests, all passing
```

## Requirements

- Python 3.9+
- Ollama installed (`/Applications/Ollama.app` on macOS) — or just use API models
- Models pulled automatically on first run, or `ollama pull qwen3-coder`
- (Optional) Claude API key for Claude models
- (Optional) DashScope API key for Qwen API models
