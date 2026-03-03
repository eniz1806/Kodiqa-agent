# Changelog

All notable changes to Kodiqa are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [3.3.1] - 2026-03-03

### Fixed
- Qwen model list: removed duplicates, removed models not in Coding Plan (qwq-plus, qwen3.5-flash, qwen-turbo, qwen-math-plus)
- Added missing Coding Plan models: glm-4.7, MiniMax-M2.5
- Model pull prompt: single-letter input no longer accidentally matches model names

### Added
- GitHub Pages landing page with particle animation, glassmorphism, animated counters
- GitHub Actions workflow for automatic Pages deployment
- Open source projects section on kodiqa.com (live GitHub star counts)

## [3.3.0] - 2026-03-02

### Changed
- License switched from GPL-3.0 to AGPL-3.0 (closes SaaS loophole)
- Commercial license page for enterprise use

## [3.2.0] - 2026-03-02

### Added
- Auto lint-fix loop (`/lint auto`) — AI fixes lint errors automatically (max 3 iterations)
- Auto test-fix loop (`/test-fix`) — run tests, AI fixes failures, re-run
- Hooks system — pre/post hooks for tool execution via config.json
- Watch AI triggers — `# AI:` comments in watched files trigger AI actions
- Architect mode (`/architect`) — strong model plans, cheap model implements
- Background/headless mode (`--headless`) — run tasks non-interactively
- Worktree isolation (`/agent --worktree`) — git worktree per sub-agent
- OS-level sandboxing (`/sandbox`) — sandbox-exec (macOS), firejail/bwrap (Linux)
- Repo map (`/map`) — tree-sitter or regex symbol extraction across codebase
- Agent teams (`/team`) — coordinator splits tasks, workers execute in parallel

## [3.0.0] - 2026-03-02

### Added
- `/changelog` — view version history
- `/stats` — session metrics (files, tools, time, cost)
- `/review-local` — AI reviews staged git changes
- `/test` — auto-generate unit tests for any file
- `/persona` — switch AI personality (security-expert, code-reviewer, teacher, architect, debugger)
- `/patch` — apply diff/patch from clipboard
- `/profile` — save/load config profiles
- `/refactor` — AI-powered multi-file refactoring (rename, extract)
- `/history` — browse and resume past sessions
- `/watch` — file watcher with change notifications
- `/embed` + `/rag` — RAG search with local embeddings (Ollama/OpenAI)
- `/debug` — run script, catch errors, debug with AI
- `/diagram` — generate Mermaid diagrams via AI
- Parallel tool calls for OpenAI-compatible providers

### Fixed
- README test count and missing v2 commands

## [2.0.0] - 2025-12-15

### Added
- 15 new features: plugins, sub-agents, LSP, themes, templates, voice
- 5 UI themes (dark, light, dracula, monokai, nord)
- Stream interrupt (Esc/Ctrl+C stops streaming instantly)
- GitHub PR workflow (`/pr`, `/review`, `/issue`)
- Pinned context (`/pin`, `/unpin`)
- Command aliases (`/alias`, `/unalias`)
- Desktop notifications (`/notify`)
- Cost optimizer (`/optimizer`)
- Session sharing (`/share` — styled HTML export)
- Project templates (`/init` — 5 templates)
- Custom tool plugins (`/plugins`)
- Sub-agents (`/agent`, `/agents` — threaded background tasks)
- LSP integration (`/lsp` — Python, TypeScript, Go)
- Voice input (`/voice` — sox + Whisper)

## [1.0.0] - 2025-10-01

### Added
- Initial release
- 26 tools, 7 API providers, MCP server support
- Multi-model consensus mode
- 3 permission modes, plan mode, batch edit review
- Context window management, conversation branching
- Compact streaming, thinking display, tab autocomplete
- Persistent memory (SQLite), session recovery
