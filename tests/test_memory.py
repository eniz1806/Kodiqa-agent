"""Tests for memory.py MemoryStore."""

import pytest


class TestMemoryStore:
    def test_store_and_list(self, memory_store):
        memory_store.store("Python is great", "language")
        result = memory_store.list_all()
        assert "Python is great" in result
        assert "language" in result

    def test_search_by_keyword(self, memory_store):
        memory_store.store("User prefers dark mode", "ui")
        memory_store.store("Project uses React", "framework")
        result = memory_store.search("dark mode")
        assert "dark mode" in result
        assert "React" not in result

    def test_search_by_tag(self, memory_store):
        memory_store.store("Use pytest for testing", "tools")
        memory_store.store("Prefer vim keybindings", "editor")
        result = memory_store.search("tools")
        assert "pytest" in result

    def test_search_empty_returns_all(self, memory_store):
        memory_store.store("memory one")
        memory_store.store("memory two")
        result = memory_store.search("")
        assert "memory one" in result
        assert "memory two" in result

    def test_delete_existing(self, memory_store):
        memory_store.store("to be deleted")
        # Get the ID
        rows = memory_store.conn.execute("SELECT id FROM memories").fetchall()
        mid = rows[0]["id"]
        result = memory_store.delete(mid)
        assert "deleted" in result.lower()
        assert memory_store.list_all() == "No memories found."

    def test_delete_nonexistent(self, memory_store):
        result = memory_store.delete(9999)
        assert "not found" in result.lower()

    def test_get_context_with_memories(self, memory_store):
        memory_store.store("Use TypeScript", "lang")
        result = memory_store.get_context()
        assert "Your Memories" in result
        assert "Use TypeScript" in result
        assert "[lang]" in result

    def test_get_context_empty(self, memory_store):
        result = memory_store.get_context()
        assert result == ""
