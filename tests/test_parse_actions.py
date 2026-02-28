"""Tests for action parsing (actions.py: parse_actions, _parse_params, etc.)."""

from actions import parse_actions, _parse_params, _parse_multiline_params, _parse_options


# ── parse_actions ──

class TestParseActions:
    def test_single_action(self):
        text = "Here is the result:\n[ACTION: read_file]\npath: /tmp/test.py\n[/ACTION]"
        actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0]["name"] == "read_file"
        assert actions[0]["params"]["path"] == "/tmp/test.py"

    def test_multiple_actions(self):
        text = (
            "[ACTION: read_file]\npath: /a.py\n[/ACTION]\n"
            "Some text in between.\n"
            "[ACTION: list_dir]\npath: /tmp\n[/ACTION]"
        )
        actions = parse_actions(text)
        assert len(actions) == 2
        assert actions[0]["name"] == "read_file"
        assert actions[1]["name"] == "list_dir"

    def test_no_actions(self):
        text = "Just a normal response with no actions."
        actions = parse_actions(text)
        assert actions == []

    def test_malformed_unclosed(self):
        text = "[ACTION: read_file]\npath: /a.py\nno closing tag"
        actions = parse_actions(text)
        assert actions == []

    def test_action_with_empty_body(self):
        text = "[ACTION: git_status]\n[/ACTION]"
        actions = parse_actions(text)
        assert len(actions) == 1
        assert actions[0]["name"] == "git_status"


# ── _parse_params ──

class TestParseParams:
    def test_simple_key_value(self):
        body = "path: /tmp/test.py"
        result = _parse_params(body, "read_file")
        assert result["path"] == "/tmp/test.py"

    def test_colon_in_value(self):
        body = "command: echo hello:world"
        result = _parse_params(body, "run_command")
        assert result["command"] == "echo hello:world"

    def test_whitespace_stripping(self):
        body = "  query:   hello world   "
        result = _parse_params(body, "web_search")
        assert result["query"] == "hello world"

    def test_multiple_params(self):
        body = "pattern: **/*.py\npath: /src"
        result = _parse_params(body, "glob")
        assert result["pattern"] == "**/*.py"
        assert result["path"] == "/src"

    def test_delegates_write_file(self):
        body = "path: /tmp/out.py\ncontent:\nprint('hi')"
        result = _parse_params(body, "write_file")
        assert result["path"] == "/tmp/out.py"
        assert "print" in result.get("content", "")

    def test_delegates_edit_file(self):
        body = "path: /tmp/out.py\nold: foo\nnew: bar"
        result = _parse_params(body, "edit_file")
        assert result["path"] == "/tmp/out.py"
        assert result["old"] == "foo"
        assert result["new"] == "bar"


# ── _parse_multiline_params ──

class TestParseMultilineParams:
    def test_write_file_content(self):
        body = "path: /tmp/hello.py\ncontent:\ndef hello():\n    print('hi')"
        result = _parse_multiline_params(body, "write_file")
        assert result["path"] == "/tmp/hello.py"
        assert "def hello():" in result["content"]
        assert "print('hi')" in result["content"]

    def test_write_file_content_same_line(self):
        body = "path: /tmp/x.txt\ncontent: hello world"
        result = _parse_multiline_params(body, "write_file")
        assert result["path"] == "/tmp/x.txt"
        assert result["content"] == "hello world"

    def test_edit_file_old_new(self):
        body = "path: /tmp/test.py\nold: foo_func\nnew: bar_func"
        result = _parse_multiline_params(body, "edit_file")
        assert result["path"] == "/tmp/test.py"
        assert result["old"] == "foo_func"
        assert result["new"] == "bar_func"

    def test_edit_file_multiline_old_new(self):
        body = "path: /tmp/test.py\nold:\n    def old():\n        pass\nnew:\n    def new():\n        return 42"
        result = _parse_multiline_params(body, "edit_file")
        assert "def old():" in result["old"]
        assert "def new():" in result["new"]
        assert "return 42" in result["new"]


# ── _parse_options ──

class TestParseOptions:
    def test_list_of_dicts(self):
        opts = [{"label": "React", "description": "UI lib"}, {"label": "Vue", "description": ""}]
        result = _parse_options(opts)
        assert len(result) == 2
        assert result[0]["label"] == "React"

    def test_list_of_strings(self):
        opts = ["React", "Vue", "Angular"]
        result = _parse_options(opts)
        assert len(result) == 3
        assert result[0]["label"] == "React"
        assert result[0]["description"] == ""

    def test_comma_separated_string(self):
        opts = "React, Vue, Angular"
        result = _parse_options(opts)
        assert len(result) == 3
        assert result[1]["label"] == "Vue"

    def test_empty_input(self):
        assert _parse_options([]) == []
        assert _parse_options("") == []
        assert _parse_options(None) == []
