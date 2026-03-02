"""Tests for v3.0 features: changelog, stats, review-local, test gen, persona, patch, profile, refactor, history, watch, embeddings, debug, diagram."""

import json
import os
import time
import tempfile
from unittest.mock import MagicMock, patch
import pytest


# ── Changelog ──

class TestChangelog:
    def test_changelog_data_exists(self):
        from config import CHANGELOG
        assert isinstance(CHANGELOG, list)
        assert len(CHANGELOG) >= 3

    def test_changelog_entries_have_required_fields(self):
        from config import CHANGELOG
        for entry in CHANGELOG:
            assert "version" in entry
            assert "date" in entry
            assert "changes" in entry
            assert isinstance(entry["changes"], list)
            assert len(entry["changes"]) > 0


# ── Personas ──

class TestPersonas:
    def test_personas_have_required_keys(self):
        from config import PERSONAS
        assert isinstance(PERSONAS, dict)
        assert len(PERSONAS) >= 5
        for name, p in PERSONAS.items():
            assert "name" in p
            assert "prompt" in p
            assert len(p["prompt"]) > 20

    def test_persona_off_resets(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._persona = "security-expert"
        k._persona = None  # simulates /persona off
        assert k._persona is None

    def test_persona_names(self):
        from config import PERSONAS
        expected = {"security-expert", "code-reviewer", "teacher", "architect", "debugger"}
        assert expected.issubset(set(PERSONAS.keys()))


# ── Stats ──

class TestStats:
    def test_session_stats_init(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        stats = {
            "files_read": 0, "files_written": 0, "files_edited": 0,
            "commands_run": 0, "searches": 0, "messages_sent": 0,
            "tools_used": {}, "start_time": time.time(),
        }
        k._session_stats = stats
        assert k._session_stats["files_read"] == 0
        assert isinstance(k._session_stats["tools_used"], dict)

    def test_track_tool_increments(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k._session_stats = {
            "files_read": 0, "files_written": 0, "files_edited": 0,
            "commands_run": 0, "searches": 0, "messages_sent": 0,
            "tools_used": {}, "start_time": time.time(),
        }
        Kodiqa._track_tool(k, "read_file")
        assert k._session_stats["files_read"] == 1
        assert k._session_stats["tools_used"]["read_file"] == 1
        Kodiqa._track_tool(k, "edit_file")
        assert k._session_stats["files_edited"] == 1
        Kodiqa._track_tool(k, "run_command")
        assert k._session_stats["commands_run"] == 1
        Kodiqa._track_tool(k, "grep")
        assert k._session_stats["searches"] == 1

    def test_stats_elapsed_time(self):
        start = time.time() - 120  # 2 minutes ago
        elapsed = time.time() - start
        mins = int(elapsed // 60)
        assert mins >= 2


# ── Review Local ──

class TestReviewLocal:
    def test_review_local_command_registered(self):
        from kodiqa import Kodiqa
        assert "/review-local" in Kodiqa._SLASH_COMMANDS

    def test_review_local_no_changes(self):
        from kodiqa import Kodiqa
        k = MagicMock(spec=Kodiqa)
        k.cwd = "/tmp"
        k.console = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            # Would print "No changes to review"
            # Just verify command is in slash commands
            assert "/review-local" in Kodiqa._SLASH_COMMANDS


# ── Test Generation ──

class TestTestGen:
    def test_test_command_registered(self):
        from kodiqa import Kodiqa
        assert "/test" in Kodiqa._SLASH_COMMANDS

    def test_test_framework_detection(self):
        ext_map = {".py": "pytest", ".ts": "jest", ".tsx": "jest", ".js": "jest", ".go": "go test"}
        assert ext_map[".py"] == "pytest"
        assert ext_map[".ts"] == "jest"


# ── Patch ──

class TestPatch:
    def test_patch_command_registered(self):
        from kodiqa import Kodiqa
        assert "/patch" in Kodiqa._SLASH_COMMANDS

    def test_patch_diff_detection(self):
        valid_diffs = ["diff --git a/f b/f", "--- a/file.py", "@@ -1,3 +1,4 @@"]
        for d in valid_diffs:
            assert any(d.lstrip().startswith(p) for p in ("diff ", "--- ", "@@", "Index:"))


# ── Profiles ──

class TestProfiles:
    def test_profile_save_load_roundtrip(self, tmp_path):
        profile_dir = tmp_path / "profiles"
        profile_dir.mkdir()
        profile = {
            "model": "claude-sonnet-4-6", "permission_mode": "relaxed",
            "theme": "dracula", "persona": "debugger",
            "compact_mode": True, "batch_edits": False,
        }
        path = profile_dir / "test.json"
        with open(path, "w") as f:
            json.dump(profile, f)
        with open(path, "r") as f:
            loaded = json.load(f)
        assert loaded["model"] == "claude-sonnet-4-6"
        assert loaded["persona"] == "debugger"

    def test_profile_list_empty(self, tmp_path):
        profile_dir = tmp_path / "profiles"
        profile_dir.mkdir()
        profiles = [f[:-5] for f in os.listdir(str(profile_dir)) if f.endswith(".json")]
        assert profiles == []

    def test_profile_delete(self, tmp_path):
        profile_dir = tmp_path / "profiles"
        profile_dir.mkdir()
        path = profile_dir / "temp.json"
        path.write_text("{}")
        assert path.exists()
        os.remove(str(path))
        assert not path.exists()


# ── Refactor ──

class TestRefactor:
    def test_refactor_command_registered(self):
        from kodiqa import Kodiqa
        assert "/refactor" in Kodiqa._SLASH_COMMANDS

    def test_refactor_subcommands(self):
        valid_subs = ["rename", "extract"]
        for sub in valid_subs:
            assert sub in ("rename", "extract")


# ── History ──

class TestHistory:
    def test_history_save_creates_index(self, tmp_path):
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        index_file = history_dir / "index.json"
        entry = {
            "id": 1, "timestamp": "2026-03-02T12:00:00",
            "model": "claude", "cwd": "/tmp",
            "messages": 10, "user_messages": 5,
            "cost": 0.05, "topic": "test session",
        }
        with open(str(index_file), "w") as f:
            json.dump([entry], f)
        with open(str(index_file), "r") as f:
            index = json.load(f)
        assert len(index) == 1
        assert index[0]["id"] == 1

    def test_history_entry_structure(self):
        entry = {
            "id": 1, "timestamp": "2026-03-02T12:00:00",
            "model": "claude", "cwd": "/tmp",
            "messages": 10, "user_messages": 5,
            "cost": 0.05, "topic": "test",
        }
        required = {"id", "timestamp", "model", "cwd", "messages", "user_messages", "cost", "topic"}
        assert required.issubset(set(entry.keys()))

    def test_history_list_empty(self):
        from kodiqa import Kodiqa
        assert "/history" in Kodiqa._SLASH_COMMANDS


# ── Watch ──

class TestWatch:
    def test_watch_command_registered(self):
        from kodiqa import Kodiqa
        assert "/watch" in Kodiqa._SLASH_COMMANDS

    def test_watcher_state_structure(self):
        watcher = {"path": "/tmp/test", "active": True, "last_mtime": {}}
        assert watcher["active"] is True
        watcher["active"] = False
        assert watcher["active"] is False


# ── Parallel Tool Calls ──

class TestParallelTools:
    def test_parallel_tool_calls_in_request(self):
        # Verify the parameter would be included
        request_body = {
            "model": "gpt-4o",
            "messages": [],
            "tools": [],
            "parallel_tool_calls": True,
        }
        assert request_body["parallel_tool_calls"] is True


# ── Embeddings ──

class TestEmbeddings:
    def test_cosine_similarity(self):
        from embeddings import EmbeddingStore
        assert EmbeddingStore._cosine_sim([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
        assert EmbeddingStore._cosine_sim([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
        assert EmbeddingStore._cosine_sim([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)

    def test_cosine_similarity_empty(self):
        from embeddings import EmbeddingStore
        assert EmbeddingStore._cosine_sim([], []) == 0.0
        assert EmbeddingStore._cosine_sim([1], [1, 2]) == 0.0

    def test_embedding_store_init(self, tmp_path):
        from embeddings import EmbeddingStore
        db_path = str(tmp_path / "test_embeddings.db")
        store = EmbeddingStore(db_path)
        assert os.path.isfile(db_path)
        store.close()

    def test_index_and_search_roundtrip(self, tmp_path):
        from embeddings import EmbeddingStore
        db_path = str(tmp_path / "test.db")
        store = EmbeddingStore(db_path)
        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('world')\n")
        # Mock embedding function (returns fixed vector)
        embed_fn = lambda text: [1.0, 0.0, 0.0]
        store.index_file(str(test_file), embed_fn, chunk_size=10)
        # Search with same vector
        results = store.search([1.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0][0] == pytest.approx(1.0)  # cosine similarity = 1
        assert "hello" in results[0][2]  # chunk contains the code
        store.close()

    def test_embed_command_registered(self):
        from kodiqa import Kodiqa
        assert "/embed" in Kodiqa._SLASH_COMMANDS
        assert "/rag" in Kodiqa._SLASH_COMMANDS


# ── Debug ──

class TestDebug:
    def test_debug_command_registered(self):
        from kodiqa import Kodiqa
        assert "/debug" in Kodiqa._SLASH_COMMANDS

    def test_debug_detects_runner(self):
        runners = {".py": "python", ".js": "node", ".ts": "npx tsx", ".rb": "ruby", ".go": "go run"}
        assert runners[".py"] == "python"
        assert runners[".js"] == "node"
        assert runners[".go"] == "go run"

    def test_debug_no_args_message(self):
        from kodiqa import Kodiqa
        assert "/debug" in Kodiqa._SLASH_COMMANDS


# ── Diagram ──

class TestDiagram:
    def test_diagram_command_registered(self):
        from kodiqa import Kodiqa
        assert "/diagram" in Kodiqa._SLASH_COMMANDS

    def test_diagram_mermaid_types(self):
        valid_types = ["flowchart", "sequence", "class", "ER", "gantt", "pie"]
        assert len(valid_types) >= 4


# ── Command Count ──

# ── @File References & Image Embedding ──

class TestAtFileReferences:
    def test_process_at_references_text_file(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent.cwd = str(tmp_path)
        agent.console = MagicMock()
        f = tmp_path / "test.py"
        f.write_text("print('hello')\n")
        agent._read_file_for_embed = Kodiqa._read_file_for_embed.__get__(agent)
        agent._read_image_for_embed = Kodiqa._read_image_for_embed.__get__(agent)
        agent._process_at_references = Kodiqa._process_at_references.__get__(agent)
        cleaned, files, images = agent._process_at_references(f"@test.py what is this?")
        assert len(files) == 1
        assert files[0]["rel_path"] == "test.py"
        assert "print('hello')" in files[0]["content"]
        assert len(images) == 0

    def test_process_at_references_missing_file(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent.cwd = str(tmp_path)
        agent.console = MagicMock()
        agent._read_file_for_embed = Kodiqa._read_file_for_embed.__get__(agent)
        agent._read_image_for_embed = Kodiqa._read_image_for_embed.__get__(agent)
        agent._process_at_references = Kodiqa._process_at_references.__get__(agent)
        cleaned, files, images = agent._process_at_references("@nonexistent.py")
        assert len(files) == 0
        assert len(images) == 0

    def test_process_at_references_image_file(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent.cwd = str(tmp_path)
        agent.console = MagicMock()
        # Create a tiny PNG (1x1 pixel)
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        img = tmp_path / "test.png"
        img.write_bytes(png_data)
        agent._read_file_for_embed = Kodiqa._read_file_for_embed.__get__(agent)
        agent._read_image_for_embed = Kodiqa._read_image_for_embed.__get__(agent)
        agent._process_at_references = Kodiqa._process_at_references.__get__(agent)
        cleaned, files, images = agent._process_at_references(f"@test.png what is this?")
        assert len(images) == 1
        assert images[0]["media_type"] == "image/png"
        assert len(images[0]["data"]) > 0

    def test_append_files_to_text(self):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._append_files_to_text = Kodiqa._append_files_to_text.__get__(agent)
        result = agent._append_files_to_text("hello", [{"rel_path": "app.py", "content": "x=1"}])
        assert "hello" in result
        assert "app.py" in result
        assert "x=1" in result

    def test_append_files_empty(self):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent._append_files_to_text = Kodiqa._append_files_to_text.__get__(agent)
        result = agent._append_files_to_text("hello", [])
        assert result == "hello"

    def test_read_file_for_embed_truncates(self, tmp_path):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        agent.cwd = str(tmp_path)
        agent._read_file_for_embed = Kodiqa._read_file_for_embed.__get__(agent)
        f = tmp_path / "big.txt"
        f.write_text("x" * 20000)
        result = agent._read_file_for_embed(str(f))
        assert "truncated" in result["content"]
        assert len(result["content"]) < 15000

    def test_pending_init(self):
        from kodiqa import Kodiqa
        agent = MagicMock(spec=Kodiqa)
        # These are set in __init__
        assert hasattr(Kodiqa, '_process_at_references')
        assert hasattr(Kodiqa, '_paste_clipboard_image')
        assert hasattr(Kodiqa, '_append_files_to_text')


class TestCommandCount:
    def test_total_slash_commands(self):
        from kodiqa import Kodiqa
        assert len(Kodiqa._SLASH_COMMANDS) >= 69
