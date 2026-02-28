"""Tests for StreamWriter class (kodiqa.py)."""

import sys
from io import StringIO
from unittest.mock import MagicMock, patch
import pytest


class TestStreamWriter:
    @pytest.fixture
    def mock_console(self):
        return MagicMock()

    @pytest.fixture
    def capture_stdout(self):
        """Capture stdout writes."""
        buf = StringIO()
        return buf

    def _make_writer(self, console, compact=True):
        from kodiqa import StreamWriter
        return StreamWriter(console, compact=compact)

    def test_text_passes_through_compact(self, mock_console, capture_stdout):
        writer = self._make_writer(mock_console, compact=True)
        with patch.object(sys, "stdout", capture_stdout):
            writer.write("Hello ")
            writer.write("world\n")
            writer.flush_pending()
        assert "Hello" in capture_stdout.getvalue()
        assert "world" in capture_stdout.getvalue()

    def test_code_fence_suppressed_compact(self, mock_console, capture_stdout):
        writer = self._make_writer(mock_console, compact=True)
        with patch.object(sys, "stdout", capture_stdout):
            writer.write("Before code\n")
            writer.write("```python\n")
            writer.write("def foo():\n")
            writer.write("    return 42\n")
            writer.write("```\n")
            writer.write("After code\n")
            writer.flush_pending()
        output = capture_stdout.getvalue()
        assert "Before code" in output
        assert "After code" in output
        assert "def foo():" not in output
        assert "return 42" not in output

    def test_verbose_passes_everything(self, mock_console, capture_stdout):
        writer = self._make_writer(mock_console, compact=False)
        with patch.object(sys, "stdout", capture_stdout):
            writer.write("text\n")
            writer.write("```python\n")
            writer.write("code\n")
            writer.write("```\n")
            writer.flush_pending()
        output = capture_stdout.getvalue()
        assert "text" in output
        assert "code" in output

    def test_action_block_suppressed(self, mock_console, capture_stdout):
        writer = self._make_writer(mock_console, compact=True)
        with patch.object(sys, "stdout", capture_stdout):
            writer.write("Explaining\n")
            writer.write("[ACTION: read_file]\n")
            writer.write("path: /tmp/test.py\n")
            writer.write("[/ACTION]\n")
            writer.write("Done\n")
            writer.flush_pending()
        output = capture_stdout.getvalue()
        assert "Explaining" in output
        assert "Done" in output
        assert "path: /tmp/test.py" not in output

    def test_fence_counter_tracks(self, mock_console, capture_stdout):
        writer = self._make_writer(mock_console, compact=True)
        with patch.object(sys, "stdout", capture_stdout):
            writer.write("```js\n")
            writer.write("line1\n")
            writer.write("line2\n")
            writer.write("line3\n")
            writer.write("```\n")
            writer.flush_pending()
        assert writer._in_fence is False  # fence should be closed
        # The console should have printed the summary with line count
        mock_console.print.assert_called()
        summary_call = str(mock_console.print.call_args_list[-1])
        assert "3 lines" in summary_call
