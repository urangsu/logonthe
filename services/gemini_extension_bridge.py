from __future__ import annotations

import json
import socketserver
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional


class GeminiResultStatus(str, Enum):
    READY = "ready"
    COMPLETED = "completed"
    AUTH_REQUIRED = "auth_required"
    DOM_UNSUPPORTED = "dom_unsupported"
    CAPTCHA = "captcha"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class GeminiCommand:
    request_id: str
    post_key: str
    navigation_version: int
    prompt: str
    created_at: float

    @classmethod
    def create(cls, post_key: str, navigation_version: int, prompt: str, request_id: Optional[str] = None):
        return cls(request_id or uuid.uuid4().hex, post_key, navigation_version, prompt, time.time())

    def to_json(self) -> Dict[str, object]:
        return {
            "requestId": self.request_id,
            "postKey": self.post_key,
            "navigationVersion": self.navigation_version,
            "prompt": self.prompt,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class GeminiResult:
    request_id: str
    post_key: str
    navigation_version: int
    status: GeminiResultStatus
    text: str = ""
    error: str = ""

    @classmethod
    def from_json(cls, payload: Dict[str, object]):
        return cls(
            str(payload.get("requestId", "")),
            str(payload.get("postKey", "")),
            int(payload.get("navigationVersion", 0)),
            GeminiResultStatus(str(payload.get("status", "failed"))),
            str(payload.get("text", "")),
            str(payload.get("error", "")),
        )


@dataclass(frozen=True)
class GeminiPreflight:
    ready: bool
    status: str
    title: str = ""
    url: str = ""
    message: str = ""
    extension_version: str = ""
    content_build: str = ""


class GeminiExtensionBridge:
    HEARTBEAT_TTL = 3.0

    COMMAND_TTL = 90.0

    def __init__(self, token: Optional[str] = None, expected_extension_version: Optional[str] = None):
        # Kept for compatibility with preview callers; loopback binding is the
        # only access control in the simplified connection flow.
        self._condition = threading.Condition()
        self._command: Optional[GeminiCommand] = None
        self._command_state = "idle"
        self._command_claimed_by = ""
        self._results: Dict[str, GeminiResult] = {}
        self._heartbeat_at = 0.0
        self._heartbeat_status = "disconnected"
        self._heartbeat_title = ""
        self._heartbeat_url = ""
        self._extension_version = ""
        self._content_build = ""
        self._expected_extension_version = expected_extension_version

    def record_heartbeat(self, status: str, title: str = "", url: str = "", extension_version: str = "", content_build: str = "") -> None:
        with self._condition:
            self._heartbeat_at = time.time()
            self._heartbeat_status = status
            self._heartbeat_title = title
            self._heartbeat_url = url
            self._extension_version = extension_version
            self._content_build = content_build
            self._condition.notify_all()

    def preflight(self) -> GeminiPreflight:
        fresh = time.time() - self._heartbeat_at <= self.HEARTBEAT_TTL
        version_ok = not self._expected_extension_version or self._extension_version == self._expected_extension_version
        ready = fresh and self._heartbeat_status == GeminiResultStatus.READY.value and version_ok
        status = self._heartbeat_status if fresh else "disconnected"
        if fresh and not version_ok:
            status = "extension_version_mismatch"
        message = "Gemini extension ready" if ready else f"Gemini extension not ready: {status}"
        return GeminiPreflight(ready, status, self._heartbeat_title, self._heartbeat_url, message, self._extension_version, self._content_build)

    def publish(self, command: GeminiCommand) -> None:
        with self._condition:
            self._command = command
            self._command_state = "pending"
            self._command_claimed_by = ""
            self._results.pop(command.request_id, None)
            self._condition.notify_all()

    def current_command(self) -> Optional[GeminiCommand]:
        with self._condition:
            if not self._command:
                return None
            if time.time() - self._command.created_at > self.COMMAND_TTL:
                self._command_state = "expired"
                self._command = None
                return None
            return self._command if self._command_state == "pending" else None

    def claim_command(self, request_id: str, claimant: str = "") -> bool:
        with self._condition:
            if not self._command or self._command_state != "pending":
                return False
            if self._command.request_id != request_id:
                return False
            self._command_state = "claimed"
            self._command_claimed_by = claimant or uuid.uuid4().hex
            return True

    def submit_result(self, result: GeminiResult) -> None:
        with self._condition:
            command = self._command
            if not command:
                return
            if (
                result.request_id != command.request_id
                or result.post_key != command.post_key
                or result.navigation_version != command.navigation_version
            ):
                return
            self._results[result.request_id] = result
            self._command_state = "completed" if result.status == GeminiResultStatus.COMPLETED else "failed"
            self._command = None
            self._command_claimed_by = ""
            self._condition.notify_all()

    def wait_for_result(self, command: GeminiCommand, timeout: float, stop_event=None) -> Optional[GeminiResult]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                result = self._results.get(command.request_id)
                if result:
                    return result
                if stop_event and stop_event.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(min(0.2, remaining))


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """HTTP server without HTTPServer's blocking reverse-DNS lookup."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class GeminiBridgeHTTPServer:
    def __init__(self, bridge: GeminiExtensionBridge, host: str = "127.0.0.1", port: int = 43127):
        self.bridge = bridge
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self) -> None:
        bridge = self.bridge

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _json(self, status, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _payload(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                if self.path == "/v1/command":
                    cmd = bridge.current_command()
                    return self._json(200, {"command": cmd.to_json() if cmd else None})
                if self.path == "/v1/status":
                    return self._json(200, asdict(bridge.preflight()))
                return self._json(404, {"error": "not_found"})

            def do_POST(self):
                payload = self._payload()
                if self.path == "/v1/heartbeat":
                    bridge.record_heartbeat(str(payload.get("status", "failed")), str(payload.get("title", "")), str(payload.get("url", "")), str(payload.get("extensionVersion", "")), str(payload.get("contentBuild", "")))
                    return self._json(200, {"ok": True})
                if self.path == "/v1/claim":
                    return self._json(200, {"claimed": bridge.claim_command(str(payload.get("requestId", "")), str(payload.get("claimant", "")))})
                if self.path == "/v1/result":
                    bridge.submit_result(GeminiResult.from_json(payload))
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "not_found"})

        self._server = _LoopbackHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
