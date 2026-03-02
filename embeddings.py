"""Kodiqa embeddings — SQLite-backed vector store for RAG search."""

import json
import math
import os
import sqlite3

import requests

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                embedding TEXT,
                updated_at TEXT
            )
        """)
        self.conn.commit()

    def embed_ollama(self, text, model="nomic-embed-text"):
        resp = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": model, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [])

    def embed_openai(self, text, api_key):
        resp = requests.post(
            OPENAI_EMBED_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "text-embedding-3-small", "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def index_file(self, file_path, embed_fn, chunk_size=50):
        """Split file into chunks and embed each."""
        with open(file_path, "r", errors="replace") as f:
            lines = f.readlines()
        self.conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            chunk_text = "".join(chunk_lines)
            if not chunk_text.strip():
                continue
            embedding = embed_fn(chunk_text)
            self.conn.execute(
                "INSERT INTO chunks (file_path, chunk_text, start_line, end_line, embedding, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (file_path, chunk_text, i + 1, min(i + chunk_size, len(lines)), json.dumps(embedding)),
            )
        self.conn.commit()

    def search(self, query_embedding, top_k=5):
        """Find top-k most similar chunks by cosine similarity."""
        rows = self.conn.execute(
            "SELECT id, file_path, chunk_text, start_line, end_line, embedding FROM chunks"
        ).fetchall()
        scored = []
        for row in rows:
            stored = json.loads(row[5])
            score = self._cosine_sim(query_embedding, stored)
            scored.append((score, row[1], row[2], row[3], row[4]))
        scored.sort(reverse=True)
        return scored[:top_k]

    @staticmethod
    def _cosine_sim(a, b):
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def close(self):
        self.conn.close()
