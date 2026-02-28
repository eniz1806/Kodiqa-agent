"""Tests for web.py functions."""

import pytest
from unittest.mock import patch, MagicMock
from web import format_results, search_duckduckgo, fetch_page


class TestFormatResults:
    def test_normal_results(self):
        results = [
            {"title": "Python Docs", "url": "https://docs.python.org", "snippet": "Official docs"},
            {"title": "Tutorial", "url": "https://example.com", "snippet": "A tutorial"},
        ]
        output = format_results(results)
        assert "Python Docs" in output
        assert "https://docs.python.org" in output
        assert "Official docs" in output
        assert "Tutorial" in output

    def test_empty_results(self):
        assert format_results([]) == "No results found."

    def test_missing_fields(self):
        results = [{"title": "No URL", "url": "", "snippet": ""}]
        output = format_results(results)
        assert "No URL" in output

    def test_includes_engine_tag(self):
        output = format_results([{"title": "Test", "url": "http://x", "snippet": "s"}])
        assert "Search results" in output


class TestSearchDuckDuckGo:
    @patch("web.requests.post")
    def test_happy_path(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.text = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com">Example</a>
            <a class="result__snippet">A snippet</a>
        </div>
        </body></html>
        """
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        results = search_duckduckgo("test query")
        assert len(results) >= 1
        assert results[0]["title"] == "Example"

    @patch("web.requests.post")
    def test_error_returns_error_result(self, mock_post):
        mock_post.side_effect = Exception("Network error")
        results = search_duckduckgo("test")
        assert len(results) == 1
        assert "Error" in results[0]["title"]


class TestFetchPage:
    @patch("web.requests.get")
    def test_extracts_text(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Hello World</p><script>evil()</script></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        result = fetch_page("https://example.com")
        assert "Hello World" in result
        assert "evil" not in result

    @patch("web.requests.get")
    def test_truncates_long_content(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = f"<html><body><p>{'x' * 10000}</p></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        result = fetch_page("https://example.com", max_chars=100)
        assert "truncated" in result
        assert len(result) < 200

    @patch("web.requests.get")
    def test_error_returns_message(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        result = fetch_page("https://example.com")
        assert "Fetch error" in result
