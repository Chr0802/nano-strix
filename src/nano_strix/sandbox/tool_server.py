"""Lightweight HTTP tool server running inside Docker sandbox."""

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class ToolHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            args = json.loads(body)
        except json.JSONDecodeError:
            args = {}

        if self.path == "/tools/terminal_execute":
            result = self._handle_terminal(args)
        elif self.path == "/tools/file_read":
            result = self._handle_file_read(args)
        elif self.path == "/tools/scanner/semgrep":
            result = self._handle_semgrep(args)
        elif self.path == "/tools/scanner/bandit":
            result = self._handle_bandit(args)
        else:
            result = {"error": f"Unknown tool: {self.path}"}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_terminal(self, args: dict) -> dict:
        command = args.get("command", "")
        timeout = args.get("timeout", 30)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd="/workspace/source",
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Command timed out",
            }

    def _handle_file_read(self, args: dict) -> dict:
        path = args.get("path", "")
        full_path = Path("/workspace/source") / path
        try:
            content = full_path.read_text(errors="replace")
            return {"content": content, "size": len(content)}
        except Exception as e:
            return {"error": str(e)}

    def _handle_semgrep(self, args: dict) -> dict:
        target = args.get("target", "/workspace/source")
        try:
            proc = subprocess.run(
                ["semgrep", "--config", "auto", "--json", target],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except FileNotFoundError:
            return {"error": "semgrep not installed in sandbox"}
        except subprocess.TimeoutExpired:
            return {"error": "semgrep timed out"}

    def _handle_bandit(self, args: dict) -> dict:
        target = args.get("target", "/workspace/source")
        try:
            proc = subprocess.run(
                ["bandit", "-r", "-f", "json", target],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except FileNotFoundError:
            return {"error": "bandit not installed in sandbox"}
        except subprocess.TimeoutExpired:
            return {"error": "bandit timed out"}

    def log_message(self, format, *args):
        pass  # Suppress HTTP request logging


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("0.0.0.0", port), ToolHandler)
    print(f"Tool server listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
