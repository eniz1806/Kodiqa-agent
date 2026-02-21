"""Kodiqa persistent memory - SQLite storage."""

import os
import sqlite3
from datetime import datetime

from config import MEMORY_DB, KODIQA_DIR


class MemoryStore:
    def __init__(self):
        os.makedirs(KODIQA_DIR, exist_ok=True)
        self.conn = sqlite3.connect(MEMORY_DB)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def store(self, content, tags=""):
        self.conn.execute(
            "INSERT INTO memories (content, tags, created_at) VALUES (?, ?, ?)",
            (content.strip(), tags.strip(), datetime.now().isoformat()),
        )
        self.conn.commit()
        return "Memory stored."

    def search(self, query):
        words = query.strip().split()
        if not words:
            return self.list_all()
        conditions = " AND ".join(["(content LIKE ? OR tags LIKE ?)"] * len(words))
        params = []
        for w in words:
            params.extend([f"%{w}%", f"%{w}%"])
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE {conditions} ORDER BY id DESC LIMIT 20",
            params,
        ).fetchall()
        return self._format(rows)

    def list_all(self):
        rows = self.conn.execute(
            "SELECT * FROM memories ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return self._format(rows)

    def delete(self, memory_id):
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        if cur.rowcount:
            return f"Memory #{memory_id} deleted."
        return f"Memory #{memory_id} not found."

    def get_context(self):
        rows = self.conn.execute(
            "SELECT * FROM memories ORDER BY id DESC LIMIT 20"
        ).fetchall()
        if not rows:
            return ""
        lines = ["## Your Memories"]
        for r in rows:
            tags = f" [{r['tags']}]" if r["tags"] else ""
            lines.append(f"- #{r['id']}: {r['content']}{tags}")
        return "\n".join(lines)

    def _format(self, rows):
        if not rows:
            return "No memories found."
        lines = []
        for r in rows:
            tags = f" [{r['tags']}]" if r["tags"] else ""
            lines.append(f"#{r['id']} ({r['created_at'][:10]}): {r['content']}{tags}")
        return "\n".join(lines)

    def close(self):
        self.conn.close()
