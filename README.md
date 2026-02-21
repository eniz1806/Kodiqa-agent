# Kodiqa - Local AI Coding Agent

Your personal Claude Code clone running 100% locally with free models.

## Start

```
kodiqa
```

Or if alias isn't loaded yet:
```
source ~/LLMS/kodiqa/venv/bin/activate && python ~/LLMS/kodiqa/kodiqa.py
```

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/model <name>` | Switch AI model (see model list below) |
| `/model` | Show current model |
| `/models` | List all installed Ollama models |
| `/scan [path]` | Scan a project folder - reads all files into context so the AI understands the project |
| `/clear` | Clear conversation history (start fresh) |
| `/memories` | Show everything Kodiqa remembers about you |
| `/forget <id>` | Delete a specific memory by its ID number |
| `/compact` | Summarize conversation to save context window (use when chat gets long) |
| `/context` | Show project context file |
| `/key` | Add/update/remove Claude API key |
| `/cd <path>` | Change working directory |
| `/help` | Show help |
| `/quit` | Exit (or press Ctrl+C) |

## Model Shortcuts

### Local Models (free, unlimited, requires Ollama)

| Shortcut | Full Model | Size | Best For |
|----------|-----------|------|----------|
| `/model fast` | qwen3:30b-a3b | ~3GB | Fast answers, 30B brain at 3B speed (MoE) |
| `/model qwen` | qwen3:14b | ~9GB | General purpose, smart, thinking mode |
| `/model coder` | qwen3-coder | ~3GB | Coding agent (default without API key, MoE) |
| `/model reason` | phi4-reasoning | ~9GB | Deep reasoning, math, logic - beats 70B models |
| `/model gpt` | gpt-oss | ~12GB | OpenAI's open model, reasoning + agentic |

### Claude API Models (paid, much smarter, requires API key)

| Shortcut | Full Model | Best For |
|----------|-----------|----------|
| `/model claude` | claude-sonnet-4 | Best balance of smart + fast (default with API key) |
| `/model sonnet` | claude-sonnet-4 | Same as claude |
| `/model haiku` | claude-haiku-4.5 | Fast + cheap, good for simple tasks |
| `/model opus` | claude-opus-4 | Smartest, best for complex coding |

You can also use the full model name: `/model qwen2.5-coder:14b`

## Claude API Setup

On first run, Kodiqa asks if you want to add a Claude API key. You can also do it anytime:

```
/key              → add or update your API key
/key              → type "remove" to delete it and go back to local models
/model claude     → switch to Claude
/model coder      → switch back to local
```

Get your API key at: https://console.anthropic.com/settings/keys

## What You Can Ask (Natural Language)

Kodiqa understands natural requests. Just type what you want:

### File Operations
```
read the file ~/.zshrc
create a file called hello.py with a hello world program
edit main.py and change the function name from foo to bar
show me what's in the ~/projects folder
show me the project structure of ~/myapp
```

### Search
```
find all .py files in ~/projects
search for "TODO" in my project
find files named "config" in ~/myapp
```

### Run Commands
```
run ls -la
run python hello.py
run npm install
run git log --oneline -10
```

### Git
```
show me the git status
show me what changed (git diff)
commit these changes with message "fix login bug"
```

### Web Search & Research
```
search the web for kotlin coroutines tutorial
search for flutter state management best practices
look up how to use python dataclasses
fetch the content from https://some-docs-page.com
```

### Memory
```
remember that I prefer Kotlin for Android development
remember my project uses Flutter 3.19
what do you remember about my preferences?
what do you know about my projects?
```

### Project Analysis
```
/scan ~/myapp
now explain what this project does
find any bugs in this code
suggest improvements
```

### Code Generation
```
write a Python script that reads a CSV and converts it to JSON
create a REST API endpoint in Flask for user login
write unit tests for the calculator module
refactor this function to be more readable
```

## Safety

- **Auto-approved** (no confirmation needed): reading files, listing directories, searching, web search, memory
- **Asks permission first** (y/n prompt): writing files, editing files, running commands, git commits
- **Blocked**: dangerous commands like `rm -rf /`, `sudo rm`, etc.

### Image & PDF
```
look at this screenshot ~/Desktop/screenshot.png
read the PDF ~/Documents/report.pdf
```

## Advanced Features

### Diff Preview
Before any file edit or write, Kodiqa shows a colored diff so you can see exactly what changes before approving.

### Parallel Tool Execution
When Claude needs multiple read-only operations (reading files, searching, etc.), they run in parallel for faster results.

### Conversation Recovery
Sessions auto-save to `~/.kodiqa/session.json`. If Kodiqa crashes or you close the terminal, next launch offers to resume where you left off.

### Pre-commit Hooks
If your project has git pre-commit hooks set up, Kodiqa runs them automatically before committing.

### Git Awareness
On startup and when changing directories, Kodiqa detects your git branch, recent commits, and changed files - injected into the AI's context automatically.

### Auto-compact
When conversation gets too long (~80K tokens), Kodiqa automatically summarizes it to keep things fast.

## Files

```
~/LLMS/kodiqa/
  kodiqa.py          # Main agent (~500 lines)
  actions.py         # 17 action handlers with diff view (~450 lines)
  tools.py           # Claude native tool schemas (~260 lines)
  memory.py          # Persistent memory - SQLite
  web.py             # DuckDuckGo search + page fetch
  config.py          # Models, prompts, settings
  requirements.txt   # Dependencies
  venv/              # Python virtual environment

~/.kodiqa/
  memory.db          # Your memories (persists across sessions)
  settings.json      # API keys, default model
  session.json       # Auto-saved conversation for recovery
  KODIQA.md          # Global context (always loaded)
  projects/          # Per-project context files (not in your repos)
```

## Tips

- Use `/model llama` for quick questions (faster, saves battery)
- Use `/model coder` for coding tasks (default with local models)
- Use `/model claude` for complex work (requires API key, best quality)
- Use `/model deepseek` for math/logic/reasoning problems
- Use `/scan` before asking about a project so the AI understands the code
- Use `/compact` when the conversation gets long and responses slow down
- Memories persist forever - Kodiqa remembers across sessions
- You can run any shell command through Kodiqa (it will ask permission first)
- Sessions auto-save - just restart if anything goes wrong

## Requirements

- Python 3.9+
- Ollama running for local models (`ollama serve`)
- At least one model pulled (`ollama pull qwen2.5-coder:14b`)
- (Optional) Claude API key for smart mode
