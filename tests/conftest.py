"""Shared fixtures for Kodiqa tests."""

import os
import sqlite3
import pytest


@pytest.fixture
def sample_file(tmp_path):
    """Create a single file for read/write/edit tests."""
    p = tmp_path / "test.py"
    p.write_text("line one\nline two\nline three\n")
    return p


@pytest.fixture
def sample_tree(tmp_path):
    """Create a sample directory tree for search/tree tests."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    print('hello')\n")
    (tmp_path / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    (tmp_path / "README.md").write_text("# Project\nSome docs\n")
    (tmp_path / "config.json").write_text('{"key": "value"}\n')
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "deep.py").write_text("# deep\n")
    # Add a skip dir to test filtering
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {}")
    return tmp_path


@pytest.fixture
def memory_store():
    """In-memory SQLite MemoryStore (no disk I/O)."""
    from memory import MemoryStore
    store = MemoryStore.__new__(MemoryStore)
    store.conn = sqlite3.connect(":memory:")
    store.conn.row_factory = sqlite3.Row
    store.conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    store.conn.commit()
    return store


@pytest.fixture(autouse=True)
def reset_actions_state():
    """Reset module-level state in actions.py between tests."""
    import actions
    actions._undo_buffer.clear()
    actions._edit_queue.clear()
    actions._batch_mode = False
    yield
    actions._undo_buffer.clear()
    actions._edit_queue.clear()
    actions._batch_mode = False
