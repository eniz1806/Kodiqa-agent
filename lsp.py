"""LSP client for Language Server Protocol integration."""

import json
import os
import subprocess
import threading


# Language server commands
LSP_SERVERS = {
    "python": {
        "cmd": ["pylsp"],
        "fallback": ["pyright-langserver", "--stdio"],
        "extensions": [".py"],
    },
    "typescript": {
        "cmd": ["typescript-language-server", "--stdio"],
        "fallback": None,
        "extensions": [".ts", ".tsx", ".js", ".jsx"],
    },
    "go": {
        "cmd": ["gopls"],
        "fallback": None,
        "extensions": [".go"],
    },
}


class LSPClient:
    """Minimal LSP client for diagnostics, definitions, and references."""

    def __init__(self):
        self.process = None
        self.language = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._root_uri = None

    def start(self, language, workspace_path):
        """Start LSP server for given language."""
        if language not in LSP_SERVERS:
            raise ValueError(f"Unsupported language: {language}. Use: {', '.join(LSP_SERVERS)}")

        server = LSP_SERVERS[language]
        cmd = server["cmd"]

        # Check if server command exists
        try:
            subprocess.run([cmd[0], "--help"], capture_output=True, timeout=5)
        except FileNotFoundError:
            if server["fallback"]:
                cmd = server["fallback"]
                try:
                    subprocess.run([cmd[0], "--help"], capture_output=True, timeout=5)
                except FileNotFoundError:
                    raise FileNotFoundError(
                        f"LSP server not found: {server['cmd'][0]}. "
                        f"Install: pip install python-lsp-server (for Python)"
                    )
            else:
                raise FileNotFoundError(f"LSP server not found: {cmd[0]}")

        self.language = language
        self._root_uri = f"file://{os.path.abspath(workspace_path)}"

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
        )

        # Initialize
        self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": self._root_uri,
            "capabilities": {},
        })
        self._send_notification("initialized", {})

    def stop(self):
        """Stop LSP server."""
        if self.process:
            try:
                self._send_request("shutdown", None)
                self._send_notification("exit", None)
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self.language = None

    def diagnostics(self, file_path):
        """Get diagnostics for a file."""
        if not self.process:
            return "LSP not running."
        uri = f"file://{os.path.abspath(file_path)}"
        try:
            with open(file_path, "r") as f:
                text = f.read()
        except Exception as e:
            return f"Error reading file: {e}"

        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self.language,
                "version": 1,
                "text": text,
            },
        })
        # Wait briefly for diagnostics
        import time
        time.sleep(1)
        return "Diagnostics requested. Check LSP server output."

    def definition(self, file_path, line, col):
        """Go to definition."""
        if not self.process:
            return "LSP not running."
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })
        return json.dumps(result, indent=2) if result else "No definition found."

    def references(self, file_path, line, col):
        """Find references."""
        if not self.process:
            return "LSP not running."
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": True},
        })
        return json.dumps(result, indent=2) if result else "No references found."

    def hover(self, file_path, line, col):
        """Get hover info."""
        if not self.process:
            return "LSP not running."
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })
        if result and "contents" in result:
            contents = result["contents"]
            if isinstance(contents, dict):
                return contents.get("value", str(contents))
            return str(contents)
        return "No hover info."

    def _send_request(self, method, params):
        """Send JSON-RPC request and return result."""
        with self._lock:
            self._msg_id += 1
            msg = {"jsonrpc": "2.0", "id": self._msg_id, "method": method}
            if params is not None:
                msg["params"] = params
            self._write(msg)
            return self._read_response(self._msg_id)

    def _send_notification(self, method, params):
        """Send JSON-RPC notification (no response expected)."""
        with self._lock:
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            self._write(msg)

    def _write(self, msg):
        """Write LSP message to stdin."""
        if not self.process or not self.process.stdin:
            return
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        try:
            self.process.stdin.write(header + body)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_response(self, msg_id, timeout=5):
        """Read LSP response for given message ID."""
        if not self.process or not self.process.stdout:
            return None
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # Read header
                header = b""
                while b"\r\n\r\n" not in header:
                    byte = self.process.stdout.read(1)
                    if not byte:
                        return None
                    header += byte
                # Parse content length
                for line in header.decode("utf-8").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":")[1].strip())
                        break
                else:
                    continue
                # Read body
                body = self.process.stdout.read(length)
                resp = json.loads(body.decode("utf-8"))
                if resp.get("id") == msg_id:
                    return resp.get("result")
            except Exception:
                return None
        return None
