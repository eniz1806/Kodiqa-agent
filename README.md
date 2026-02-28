# Kodiqa - Local AI Coding Agent

Your personal Claude Code clone running 100% locally with free models — or powered by Claude/Qwen API for maximum intelligence.

## Start

```
kodiqa
```

Or if alias isn't loaded yet:
```
source ~/LLMS/kodiqa/venv/bin/activate && python ~/LLMS/kodiqa/kodiqa.py
```

## Features

- **26 tools** — file ops, git, search, web, memory, clipboard, multi-edit, undo, diff apply
- **3 API providers** — Ollama (local/free), Claude API, Qwen API (DashScope)
- **Compact streaming** — hides code output, shows progress instead (toggle with `/verbose`)
- **Multi-model consensus** — query all models, merge best answers
- **3-choice confirmation** — Yes / Yes don't ask again / No
- **Token tracking** — cost per response, session totals, tok/s speed
- **Prompt caching** — Claude API cache for faster + cheaper responses
- **Auto-retry** — exponential backoff on API errors (429, 5xx, timeouts)
- **Undo** — per-file undo buffer (up to 10 levels)
- **Checkpoints** — save/restore conversation state
- **Session export** — export conversation to markdown
- **Shell env detection** — auto-detects OS, shell, dev tools
- **User-editable config** — `~/.kodiqa/config.json` overrides defaults
- **Diff preview** — colored diff before every file write/edit
- **Parallel tools** — read-only operations run concurrently
- **Conversation recovery** — auto-saved sessions, resume on crash
- **Ollama auto-management** — starts on launch, stops on quit

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/model <name>` | Switch model (see shortcuts below) |
| `/models` | List all available models |
| `/multi <models>` | Multi-model consensus mode |
| `/single` | Back to single model |
| `/scan [path]` | Scan project into context (with progress) |
| `/clear` | Clear conversation history |
| `/compact` | Summarize conversation to save context |
| `/memories` | Show stored memories |
| `/forget <id>` | Delete a memory |
| `/context` | Show project context file |
| `/key [provider]` | Add/update API key (Claude or Qwen) |
| `/tokens` | Session token usage, cost, context estimate |
| `/config` | Show config / `/config reload` to reload |
| `/export` | Export session to markdown file |
| `/checkpoint [n]` | Save conversation checkpoint |
| `/restore [n]` | Restore checkpoint (no arg = list all) |
| `/env` | Show detected shell environment |
| `/verbose` | Toggle compact/verbose streaming |
| `/search <engine>` | Switch search engine (duckduckgo/google/api) |
| `/cd <path>` | Change working directory |
| `/help` | Show help |
| `/quit` | Exit |

## Model Shortcuts

### Local Models (free, unlimited, requires Ollama)

| Shortcut | Full Model | Size | Best For |
|----------|-----------|------|----------|
| `/model fast` | qwen3:30b-a3b | ~3GB | Fast answers, 30B brain at 3B speed (MoE) |
| `/model qwen` | qwen3:14b | ~9GB | General purpose, smart, thinking mode |
| `/model coder` | qwen3-coder | ~3GB | Coding agent (default without API key, MoE) |
| `/model reason` | phi4-reasoning | ~9GB | Deep reasoning, math, logic |
| `/model gpt` | gpt-oss | ~12GB | OpenAI's open model, reasoning + agentic |

### Claude API Models (paid, requires API key)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model claude` | claude-sonnet-4 | Best balance of smart + fast (default with key) |
| `/model sonnet` | claude-sonnet-4 | Same as claude |
| `/model haiku` | claude-haiku-4.5 | Fast + cheap, good for simple tasks |
| `/model opus` | claude-opus-4 | Smartest, best for complex coding |

### Qwen API Models (paid, Alibaba Cloud DashScope)

| Shortcut | Full Model | Context | Best For |
|----------|-----------|---------|----------|
| `/model qwen-api` | qwen-plus | 1M tokens | Smart + affordable |
| `/model qwen-max` | qwen-max | 262K tokens | Most powerful |
| `/model qwen-coder-api` | qwen3-coder-plus | 1M tokens | Code + tool calling |
| `/model qwen-flash-api` | qwen-flash | 1M tokens | Ultra cheap ($0.05/M input) |

You can also use full model names: `/model qwen3:14b`

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
/key              → add or update Claude API key
/model claude     → prompts for key if not set
```
Get your key: https://console.anthropic.com/settings/keys

### Qwen API (Alibaba Cloud DashScope)
```
/key qwen         → add or update Qwen API key
/model qwen-api   → prompts for key if not set
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
- **Blocked**: `rm -rf /`, `sudo rm`, `mkfs`, `dd`, fork bombs, etc.
- **3-choice confirm**: Yes / Yes don't ask again (per action type) / No

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
  kodiqa.py          # Main agent (~2200 lines)
  actions.py         # 26 action handlers (~860 lines)
  tools.py           # Tool schemas (~480 lines)
  config.py          # Config, aliases, system prompt (~280 lines)
  web.py             # Web search + page fetch (~195 lines)
  memory.py          # SQLite persistent memory (82 lines)
  requirements.txt   # Dependencies
  venv/              # Python virtual environment

~/.kodiqa/
  config.json        # User-editable config (overrides defaults)
  settings.json      # API keys, default model
  memory.db          # Persistent memories
  session.json       # Auto-saved conversation
  input_history      # Readline history (500 entries)
  error.log          # Error log (capped 1MB)
  KODIQA.md          # Global context (always in system prompt)
  projects/          # Per-project context files
  checkpoints/       # Conversation checkpoints
  exports/           # Exported session markdown files
```

## Tips

- Default is **compact mode** — code hidden during streaming, progress shown instead
- Use `/verbose` when you want to see code as it streams
- Use `/checkpoint` before risky operations, `/restore` to roll back
- Use `/export` to save a conversation for later reference
- Use `/tokens` to monitor API costs
- Use `/model qwen-flash-api` for ultra-cheap API queries
- Use `/scan` before asking about a project
- Use `/compact` when conversation gets long
- Memories persist forever across sessions
- Arrow keys work: up/down for history, left/right to edit
- Sessions auto-save — restart if anything goes wrong
- Ollama starts and stops automatically

## Requirements

- Python 3.9+
- Ollama installed (`/Applications/Ollama.app` on macOS)
- At least one model pulled (`ollama pull qwen3-coder`)
- (Optional) Claude API key for Claude models
- (Optional) DashScope API key for Qwen API models
