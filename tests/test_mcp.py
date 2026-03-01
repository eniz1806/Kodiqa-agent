"""Tests for MCP (Model Context Protocol) client."""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from mcp import MCPServer, MCPManager


class TestMCPServer:
    def test_init(self):
        s = MCPServer("test", "echo", args=["hello"])
        assert s.name == "test"
        assert s.command == "echo"
        assert s.args == ["hello"]
        assert s.tools == []
        assert s.process is None

    def test_init_defaults(self):
        s = MCPServer("s", "cmd")
        assert s.args == []
        assert s.env is None

    def test_stop_no_process(self):
        s = MCPServer("test", "cmd")
        s.stop()  # should not raise

    def test_stop_terminates_process(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        s.stop()
        s.process.terminate.assert_called_once()
        s.process.wait.assert_called_once()

    def test_stop_kills_on_timeout(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        s.process.wait.side_effect = Exception("timeout")
        s.stop()
        s.process.kill.assert_called_once()

    def test_get_tool_schemas_empty(self):
        s = MCPServer("myserver", "cmd")
        assert s.get_tool_schemas() == []

    def test_get_tool_schemas(self):
        s = MCPServer("myserver", "cmd")
        s.tools = [
            {"name": "read", "description": "Read a file", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
            {"name": "write", "description": "Write a file"},
        ]
        schemas = s.get_tool_schemas()
        assert len(schemas) == 2
        assert schemas[0]["name"] == "mcp_myserver_read"
        assert "[MCP:myserver]" in schemas[0]["description"]
        assert schemas[0]["input_schema"]["properties"]["path"]["type"] == "string"
        # Missing inputSchema gets default
        assert schemas[1]["input_schema"] == {"type": "object", "properties": {}}

    def test_send_no_process(self):
        s = MCPServer("test", "cmd")
        result = s._send({"jsonrpc": "2.0", "method": "test"})
        assert result is None

    def test_send_dead_process(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = 1  # exited
        result = s._send({"jsonrpc": "2.0", "method": "test"})
        assert result is None

    def test_send_success(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        response = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        s.process.stdout.readline.return_value = json.dumps(response) + "\n"
        result = s._send({"jsonrpc": "2.0", "method": "test"})
        assert result == response

    def test_notify_no_process(self):
        s = MCPServer("test", "cmd")
        s._notify({"jsonrpc": "2.0", "method": "test"})  # should not raise

    def test_call_tool_success(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        resp = {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "hello world"}]
        }}
        s.process.stdout.readline.return_value = json.dumps(resp) + "\n"
        result = s.call_tool("read", {"path": "/tmp/test"})
        assert result == "hello world"

    def test_call_tool_error(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        resp = {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "not found"}}
        s.process.stdout.readline.return_value = json.dumps(resp) + "\n"
        result = s.call_tool("read", {})
        assert "MCP error" in result
        assert "not found" in result

    def test_call_tool_no_response(self):
        s = MCPServer("test", "cmd")
        s.process = MagicMock()
        s.process.poll.return_value = None
        s.process.stdout.readline.return_value = ""
        result = s.call_tool("read", {})
        assert "no response" in result

    def test_start_command_not_found(self):
        s = MCPServer("test", "nonexistent_cmd_xyz_123")
        result = s.start()
        assert result is False


class TestMCPManager:
    def test_init(self):
        m = MCPManager()
        assert m.servers == {}

    def test_get_all_tools_empty(self):
        m = MCPManager()
        assert m.get_all_tools() == []

    def test_list_servers_empty(self):
        m = MCPManager()
        assert "No MCP servers" in m.list_servers()

    def test_list_servers_with_entries(self):
        m = MCPManager()
        server = MagicMock()
        server.tools = [{"name": "read"}, {"name": "write"}]
        m.servers["test"] = server
        result = m.list_servers()
        assert "test" in result
        assert "2 tools" in result

    def test_remove_nonexistent(self):
        m = MCPManager()
        assert m.remove_server("nope") is False

    def test_remove_existing(self):
        m = MCPManager()
        server = MagicMock()
        m.servers["test"] = server
        assert m.remove_server("test") is True
        server.stop.assert_called_once()
        assert "test" not in m.servers

    def test_call_tool_invalid_name(self):
        m = MCPManager()
        result = m.call_tool("bad_name", {})
        assert "Invalid MCP tool name" in result

    def test_call_tool_unknown_server(self):
        m = MCPManager()
        result = m.call_tool("mcp_unknown_read", {})
        assert "not connected" in result

    def test_call_tool_routes_correctly(self):
        m = MCPManager()
        server = MagicMock()
        server.call_tool.return_value = "result data"
        m.servers["myserver"] = server
        result = m.call_tool("mcp_myserver_read", {})
        server.call_tool.assert_called_once_with("read", {})
        assert result == "result data"

    def test_stop_all(self):
        m = MCPManager()
        s1 = MagicMock()
        s2 = MagicMock()
        m.servers = {"a": s1, "b": s2}
        m.stop_all()
        s1.stop.assert_called_once()
        s2.stop.assert_called_once()
        assert m.servers == {}

    def test_get_all_tools_merges(self):
        m = MCPManager()
        s1 = MagicMock()
        s1.get_tool_schemas.return_value = [{"name": "mcp_a_read"}]
        s2 = MagicMock()
        s2.get_tool_schemas.return_value = [{"name": "mcp_b_write"}, {"name": "mcp_b_exec"}]
        m.servers = {"a": s1, "b": s2}
        tools = m.get_all_tools()
        assert len(tools) == 3
