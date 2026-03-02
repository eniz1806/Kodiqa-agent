# Contributing to Kodiqa

Thanks for your interest in contributing! Kodiqa is an open-source AI coding agent and welcomes contributions of all kinds.

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/eniz1806/Kodiqa-agent.git
   cd Kodiqa-agent
   ```

2. Create a virtual environment and install:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Run the agent:
   ```bash
   kodiqa
   ```

## Running Tests

```bash
pytest -v
```

All 284 tests run in ~0.4s and require no API keys or Ollama.

## Code Style

- Python 3.9+ compatible, no type hints
- Rich for all terminal output (panels, markdown, prompts, spinners)
- prompt_toolkit for interactive input
- All file paths expanded with `os.path.expanduser()` before use
- Action results truncated at 20,000 chars to avoid context overflow

## Architecture

See [CLAUDE.md](CLAUDE.md) for full architecture docs. Key files:

| File | Purpose |
|------|---------|
| `kodiqa.py` | Main agent class, chat loops, UI, slash commands |
| `actions.py` | 26 tool handlers, dispatch, edit queue |
| `tools.py` | Tool schemas (Claude format, auto-converted for OpenAI) |
| `config.py` | Constants, provider registry, themes, system prompt |
| `web.py` | Web search engines + page fetcher |
| `memory.py` | SQLite persistent memory |
| `mcp.py` | MCP client (stdio JSON-RPC) |

## Adding a New Tool

1. Add handler `do_<name>()` in `actions.py`
2. Register in `_dispatch()` handler map in `actions.py`
3. Add schema to `CLAUDE_TOOLS` in `tools.py`
4. Add `[ACTION: name]` docs to `SYSTEM_PROMPT` in `config.py` (for Ollama)
5. If it needs confirmation, add to `CONFIRM_ACTIONS` in `config.py`
6. If read-only, add to `read_only` set in `execute_tools_parallel()` in `actions.py`
7. Add label to `_tool_label()` in `kodiqa.py`
8. Add description to `_describe_action()` in `actions.py`

## Adding a New API Provider

1. Add entry to `OPENAI_COMPAT_PROVIDERS` in `config.py`
2. The generic `_chat_openai_compat(provider)` handles everything automatically
3. (Optional) Add provider-specific context limit in `_context_limit()` in `kodiqa.py`

## Adding a Slash Command

Add the handler in `_handle_slash()` in `kodiqa.py`. Update `/help` text and add to `_SLASH_COMMANDS`.

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes
3. Ensure all tests pass: `pytest -v`
4. Submit a PR with a clear description
5. PRs are reviewed before merging

## Reporting Bugs

Use the [Bug Report](https://github.com/eniz1806/Kodiqa-agent/issues/new?template=bug_report.yml) template.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 License.
