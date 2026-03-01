"""Kodiqa MCP (Model Context Protocol) client — connects to external tool servers."""

import json
import subprocess
import threading
import logging

_logger = logging.getLogger("kodiqa")


class MCPServer:
    """A connection to an MCP server process (stdio transport)."""

    def __init__(self, name, command, args=None, env=None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.process = None
        self.tools = []
        self._id = 0
        self._lock = threading.Lock()

    def start(self):
        """Start the MCP server process."""
        try:
            cmd = [self.command] + self.args
            self.process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=self.env,
            )
            # Initialize with MCP protocol
            resp = self._send({"jsonrpc": "2.0", "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kodiqa", "version": "1.0"},
            }})
            if resp and "result" in resp:
                # List tools
                tools_resp = self._send({"jsonrpc": "2.0", "method": "tools/list", "params": {}})
                if tools_resp and "result" in tools_resp:
                    self.tools = tools_resp["result"].get("tools", [])
                # Send initialized notification
                self._notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
                return True
        except FileNotFoundError:
            _logger.warning(f"MCP server '{self.name}': command not found: {self.command}")
        except Exception as e:
            _logger.warning(f"MCP server '{self.name}' failed to start: {e}")
        return False

    def call_tool(self, tool_name, arguments):
        """Call a tool on this MCP server."""
        resp = self._send({"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": tool_name,
            "arguments": arguments,
        }})
        if resp and "result" in resp:
            content = resp["result"].get("content", [])
            texts = []
            for block in content:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return "\n".join(texts) if texts else str(resp["result"])
        if resp and "error" in resp:
            return f"MCP error: {resp['error'].get('message', str(resp['error']))}"
        return "MCP: no response"

    def _send(self, message):
        """Send a JSON-RPC message and read the response."""
        if not self.process or self.process.poll() is not None:
            return None
        with self._lock:
            self._id += 1
            message["id"] = self._id
            try:
                line = json.dumps(message) + "\n"
                self.process.stdin.write(line)
                self.process.stdin.flush()
                resp_line = self.process.stdout.readline()
                if resp_line:
                    return json.loads(resp_line)
            except Exception as e:
                _logger.warning(f"MCP '{self.name}' communication error: {e}")
        return None

    def _notify(self, message):
        """Send a notification (no response expected)."""
        if not self.process or self.process.poll() is not None:
            return
        try:
            line = json.dumps(message) + "\n"
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except Exception:
            pass

    def stop(self):
        """Stop the MCP server process."""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()

    def get_tool_schemas(self):
        """Convert MCP tools to Claude-compatible tool schemas."""
        schemas = []
        for tool in self.tools:
            schemas.append({
                "name": f"mcp_{self.name}_{tool['name']}",
                "description": f"[MCP:{self.name}] {tool.get('description', tool['name'])}",
                "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
            })
        return schemas


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self.servers = {}  # {name: MCPServer}

    def add_server(self, name, command, args=None, env=None):
        """Add and start an MCP server."""
        if name in self.servers:
            self.servers[name].stop()
        server = MCPServer(name, command, args, env)
        if server.start():
            self.servers[name] = server
            return server.tools
        return None

    def remove_server(self, name):
        """Stop and remove an MCP server."""
        if name in self.servers:
            self.servers[name].stop()
            del self.servers[name]
            return True
        return False

    def call_tool(self, full_tool_name, arguments):
        """Call a tool by its full name (mcp_servername_toolname)."""
        # Parse: mcp_servername_toolname
        parts = full_tool_name.split("_", 2)
        if len(parts) < 3 or parts[0] != "mcp":
            return f"Invalid MCP tool name: {full_tool_name}"
        server_name = parts[1]
        tool_name = parts[2]
        if server_name not in self.servers:
            return f"MCP server '{server_name}' not connected"
        return self.servers[server_name].call_tool(tool_name, arguments)

    def get_all_tools(self):
        """Get all tool schemas from all connected servers."""
        tools = []
        for server in self.servers.values():
            tools.extend(server.get_tool_schemas())
        return tools

    def list_servers(self):
        """List connected servers with their tools."""
        if not self.servers:
            return "No MCP servers connected."
        lines = []
        for name, server in self.servers.items():
            tool_names = [t["name"] for t in server.tools]
            lines.append(f"  {name}: {len(server.tools)} tools ({', '.join(tool_names[:5])})")
        return "\n".join(lines)

    def stop_all(self):
        """Stop all servers."""
        for server in self.servers.values():
            server.stop()
        self.servers.clear()
