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
from src.logger import logger


class GeminiResultStatus(str, Enum):
    READY = "ready"
    COMPLETED = "completed"
    AUTH_REQUIRED = "auth_required"
    DOM_UNSUPPORTED = "dom_unsupported"
    CAPTCHA = "captcha"
    TIMEOUT = "timeout"
    FAILED = "failed"
    BUSY = "busy"


@dataclass(frozen=True)
class GeminiCommand:
    request_id: str
    post_key: str
    navigation_version: int
    prompt: str
    created_at: float
    deadline_at: float

    @classmethod
    def create(cls, post_key: str, navigation_version: int, prompt: str, request_id: Optional[str] = None):
        now = time.time()
        return cls(request_id or uuid.uuid4().hex, post_key, navigation_version, prompt, now, now + 70.0)

    def to_json(self) -> Dict[str, object]:
        return {
            "requestId": self.request_id,
            "postKey": self.post_key,
            "navigationVersion": self.navigation_version,
            "prompt": self.prompt,
            "createdAt": self.created_at,
            "deadlineAt": self.deadline_at,
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
    protocol_version: int = 0
    bridge_schema_version: int = 0
    heartbeat_age_ms: int = 0
    bridge_session_id: str = ""
    active_request_id: Optional[str] = None
    command_state: str = "idle"


import urllib.parse
from services.runtime_contract import load_runtime_contract


class GeminiExtensionBridge:
    HEARTBEAT_TTL = 45.0
    COMMAND_TTL = 90.0

    def __init__(
        self,
        token: Optional[str] = None,
        expected_extension_version: Optional[str] = None,
        expected_build_id: Optional[str] = None
    ):
        contract = load_runtime_contract()
        self.bridge_session_id = uuid.uuid4().hex
        self._condition = threading.Condition()
        self._command: Optional[GeminiCommand] = None
        self._command_state = "idle"
        self._command_claimed_by = ""
        self._active_request_id: Optional[str] = None
        self._results: Dict[str, GeminiResult] = {}
        self._heartbeat_at = 0.0
        self._ever_seen_heartbeat = False
        self._heartbeat_status = "disconnected"
        self._heartbeat_title = ""
        self._heartbeat_url = ""
        self._extension_version = ""
        self._content_build = ""
        self._protocol_version = 0
        self._bridge_schema_version = 0
        self._transport_alive = False
        self._runtime_alive = False
        self._runtime_status = "unknown"
        self._consumer_id = ""
        self._last_runtime_ping_at = 0.0
        self._last_busy_request_id: Optional[str] = None
        self._last_busy_since: Optional[float] = None
        self._last_busy_deadline_at: Optional[float] = None
        self._cancel_requests: set[str] = set()
        self._expected_extension_version = expected_extension_version or contract.extension_version
        self._expected_build_id = expected_build_id or contract.runtime_build
        self._protocol_version_expected = contract.protocol_version
        self._bridge_schema_version_expected = contract.bridge_schema_version
        self.bridge_server_started = True
        self.bridge_server_error = ""

    def record_heartbeat(
        self,
        status: str,
        title: str = "",
        url: str = "",
        extension_version: str = "",
        content_build: str = "",
        protocol_version: int = 0,
        bridge_schema_version: int = 0,
        transport_alive: bool = True,
        runtime_alive: bool = True,
        runtime_status: str = "ready",
        consumer_id: str = "",
        last_runtime_ping_at: float = 0.0,
        busy_request_id: Optional[str] = None,
        busy_since: Optional[float] = None,
        busy_deadline_at: Optional[float] = None
    ) -> None:
        with self._condition:
            self._heartbeat_at = time.time()
            self._ever_seen_heartbeat = True
            self._heartbeat_status = status
            self._heartbeat_title = title
            self._heartbeat_url = url
            self._extension_version = extension_version
            self._content_build = content_build
            self._protocol_version = int(protocol_version or 0)
            self._bridge_schema_version = int(bridge_schema_version or 0)
            self._transport_alive = bool(transport_alive)
            self._runtime_alive = bool(runtime_alive)
            self._runtime_status = str(runtime_status or status)
            self._consumer_id = str(consumer_id or "")
            self._last_runtime_ping_at = float(last_runtime_ping_at or self._heartbeat_at)
            self._last_busy_request_id = busy_request_id
            self._last_busy_since = busy_since
            self._last_busy_deadline_at = busy_deadline_at
            self._condition.notify_all()

    def cancel_command(self, request_id: Optional[str] = None) -> bool:
        with self._condition:
            target_rid = request_id or self._active_request_id
            if target_rid:
                self._cancel_requests.add(target_rid)
                if self._command and self._command.request_id == target_rid:
                    self._command_state = "cancelled"
                    self._command = None
                    self._command_claimed_by = ""
                self._results[target_rid] = GeminiResult(
                    request_id=target_rid,
                    post_key="",
                    navigation_version=0,
                    status=GeminiResultStatus.FAILED,
                    text="",
                    error="cancelled"
                )
                if self._active_request_id == target_rid:
                    self._active_request_id = None
                logger.log(f"[GEMINI][CANCEL_COMMAND] rid={target_rid}")
                self._condition.notify_all()
                return True
            return False

    def preflight(self) -> GeminiPreflight:
        with self._condition:
            if not self.bridge_server_started:
                err_lower = self.bridge_server_error.lower()
                status = "bridge_port_in_use" if "address already in use" in err_lower or "in use" in err_lower else "bridge_server_unavailable"
                return GeminiPreflight(
                    False, status, "", "", f"Gemini bridge server unavailable: {self.bridge_server_error}",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, 0,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            if not self._ever_seen_heartbeat:
                return GeminiPreflight(
                    False, "heartbeat_never_received", "", "", "Gemini extension heartbeat never received",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, 0,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            age_sec = time.time() - self._heartbeat_at
            age_ms = int(age_sec * 1000)
            fresh = age_sec <= self.HEARTBEAT_TTL

            if not fresh:
                return GeminiPreflight(
                    False, "heartbeat_stale", self._heartbeat_title, self._heartbeat_url,
                    f"Gemini heartbeat stale (age: {age_sec:.1f}s)",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            version_ok = not self._expected_extension_version or self._extension_version == self._expected_extension_version
            if not version_ok:
                return GeminiPreflight(
                    False, "extension_version_mismatch", self._heartbeat_title, self._heartbeat_url,
                    f"Extension version mismatch: expected {self._expected_extension_version}, got {self._extension_version}",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            identity_ok = (
                self._content_build == self._expected_build_id
                and self._protocol_version == self._protocol_version_expected
                and self._bridge_schema_version == self._bridge_schema_version_expected
            )
            if not identity_ok:
                return GeminiPreflight(
                    False, "extension_identity_mismatch", self._heartbeat_title, self._heartbeat_url,
                    f"Extension runtime identity mismatch: build={self._content_build}, proto={self._protocol_version}, schema={self._bridge_schema_version}",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            if self._heartbeat_status == "auth_required":
                return GeminiPreflight(
                    False, "auth_required", self._heartbeat_title, self._heartbeat_url,
                    "Gemini login required (auth_required)",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            if self._heartbeat_status == "dom_unsupported":
                return GeminiPreflight(
                    False, "dom_unsupported", self._heartbeat_title, self._heartbeat_url,
                    "Gemini DOM editor not found or unsupported",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            if self._heartbeat_status == "captcha":
                return GeminiPreflight(
                    False, "captcha", self._heartbeat_title, self._heartbeat_url,
                    "Gemini captcha detected",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            if self._heartbeat_status == "busy":
                # Classify busy into busy_active_command, busy_orphaned, busy_stale_deadline
                if self._last_busy_deadline_at and time.time() > (self._last_busy_deadline_at / 1000.0 if self._last_busy_deadline_at > 100_000_000_000 else self._last_busy_deadline_at):
                    return GeminiPreflight(
                        False, "busy_stale_deadline", self._heartbeat_title, self._heartbeat_url,
                        "Gemini busy deadline exceeded (busy_stale_deadline)",
                        self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                        self.bridge_session_id, self._active_request_id, self._command_state
                    )
                if self._last_busy_request_id:
                    if self._active_request_id and self._active_request_id == self._last_busy_request_id:
                        return GeminiPreflight(
                            False, "busy_active_command", self._heartbeat_title, self._heartbeat_url,
                            f"Gemini currently generating for active command (rid={self._active_request_id})",
                            self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                            self.bridge_session_id, self._active_request_id, self._command_state
                        )
                    else:
                        return GeminiPreflight(
                            False, "busy_orphaned", self._heartbeat_title, self._heartbeat_url,
                            f"Gemini busy with orphaned request (rid={self._last_busy_request_id})",
                            self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                            self.bridge_session_id, self._active_request_id, self._command_state
                        )
                elif not self._active_request_id:
                    return GeminiPreflight(
                        False, "busy_orphaned", self._heartbeat_title, self._heartbeat_url,
                        "Gemini busy without active Python request (busy_orphaned)",
                        self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                        self.bridge_session_id, self._active_request_id, self._command_state
                    )
                else:
                    return GeminiPreflight(
                        False, "busy", self._heartbeat_title, self._heartbeat_url,
                        "Gemini generation currently busy",
                        self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                        self.bridge_session_id, self._active_request_id, self._command_state
                    )

            if self._heartbeat_status == GeminiResultStatus.READY.value:
                return GeminiPreflight(
                    True, "ready", self._heartbeat_title, self._heartbeat_url,
                    "Gemini extension ready",
                    self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                    self.bridge_session_id, self._active_request_id, self._command_state
                )

            return GeminiPreflight(
                False, self._heartbeat_status, self._heartbeat_title, self._heartbeat_url,
                f"Gemini extension status: {self._heartbeat_status}",
                self._extension_version, self._content_build, self._protocol_version, self._bridge_schema_version, age_ms,
                self.bridge_session_id, self._active_request_id, self._command_state
            )

    def await_ready(self, timeout: float = 5.0, stop_event: Optional[threading.Event] = None) -> GeminiPreflight:
        """피드 작업 시작 시 단기 유예 시간(5초)을 두고 ready 상태를 대기하며, orphan/stale은 자동 복구"""
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            if stop_event and stop_event.is_set():
                break
            pf = self.preflight()
            if pf.ready:
                return pf
            if pf.status in ("busy_orphaned", "busy_stale_deadline"):
                logger.log(f"⚠️ [GEMINI][RECOVERY] {pf.status} 감지 -> 자동 정리 요청", "WARNING")
                self.cancel_command(self._last_busy_request_id)
            time.sleep(0.2)
        return self.preflight()

    def publish(self, command: GeminiCommand) -> None:
        with self._condition:
            self._command = command
            self._command_state = "pending"
            self._command_claimed_by = ""
            self._active_request_id = command.request_id
            self._results.pop(command.request_id, None)
            logger.log(f"[GEMINI][PUBLISH] rid={command.request_id} post={command.post_key} nav={command.navigation_version}")
            self._condition.notify_all()

    def current_command(self) -> Optional[GeminiCommand]:
        with self._condition:
            if not self._command:
                return None
            if time.time() >= self._command.deadline_at or time.time() - self._command.created_at > self.COMMAND_TTL:
                self._command_state = "expired"
                self._command = None
                return None
            return self._command if self._command_state == "pending" else None

    def wait_for_command(self, timeout: float = 15.0, stop_event: Optional[threading.Event] = None) -> Optional[GeminiCommand]:
        deadline = time.monotonic() + max(0.1, timeout)
        with self._condition:
            while time.monotonic() < deadline:
                if stop_event and stop_event.is_set():
                    return None
                cmd = self.current_command()
                if cmd:
                    return cmd
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.5))
            return self.current_command()

    def claim_command(self, request_id: str, claimant: str = "") -> bool:
        with self._condition:
            if not self._command or self._command_state != "pending":
                return False
            if time.time() >= self._command.deadline_at:
                self._command = None
                self._command_state = "expired"
                return False
            if self._command.request_id != request_id:
                return False
            self._command_state = "claimed"
            self._command_claimed_by = claimant or uuid.uuid4().hex
            command = self._command
            logger.log(
                f"[GEMINI][CLAIM] rid={request_id} post={command.post_key} "
                f"nav={command.navigation_version} claimant={self._command_claimed_by}"
            )
            return True

    def submit_result(self, result: GeminiResult) -> tuple[bool, str]:
        with self._condition:
            command = self._command
            if not command:
                return False, "no_active_command"
            if time.time() > command.deadline_at:
                self._command = None
                self._command_state = "expired"
                return False, "late_result"
            if result.request_id != command.request_id:
                return False, "request_id_mismatch"
            if result.post_key != command.post_key:
                return False, "post_key_mismatch"
            if result.navigation_version != command.navigation_version:
                return False, "navigation_version_mismatch"
            self._results[result.request_id] = result
            self._command_state = "completed" if result.status == GeminiResultStatus.COMPLETED else "failed"
            self._command = None
            self._command_claimed_by = ""
            if self._active_request_id == result.request_id:
                self._active_request_id = None
            logger.log(f"[GEMINI][RESULT] rid={result.request_id} post={result.post_key} nav={result.navigation_version} status={result.status.value}")
            self._condition.notify_all()
            return True, "accepted"

    def wait_for_result(
        self,
        command: GeminiCommand,
        timeout: Optional[float] = None,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None
    ) -> Optional[GeminiResult]:
        timeout = float(timeout) if timeout is not None else max(0.0, command.deadline_at - time.time())
        deadline_at = command.deadline_at or (time.time() + timeout)
        deadline = min(time.monotonic() + timeout, time.monotonic() + max(0.0, deadline_at - time.time()))
        with self._condition:
            while True:
                result = self._results.get(command.request_id)
                if result:
                    if self._active_request_id == command.request_id:
                        self._active_request_id = None
                    return result
                if stop_event and stop_event.is_set():
                    logger.log(f"[GEMINI][WAIT_RESULT] stop_event 감지 -> 명령 취소 전송 (rid={command.request_id})")
                    self.cancel_command(command.request_id)
                    return None
                if skip_event and skip_event.is_set():
                    logger.log(f"[GEMINI][WAIT_RESULT] skip_event 감지 -> 명령 취소 전송 (rid={command.request_id})")
                    self.cancel_command(command.request_id)
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
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
                self.send_header("Access-Control-Max-Age", "86400")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _payload(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)

                if path == "/v1/command/wait":
                    try:
                        timeout_param = float(query.get("timeout", [15.0])[0])
                    except (ValueError, IndexError):
                        timeout_param = 15.0
                    timeout_val = min(30.0, max(0.5, timeout_param))
                    cmd = bridge.wait_for_command(timeout=timeout_val)
                    return self._json(200, {"command": cmd.to_json() if cmd else None})

                if path == "/v1/command":
                    cmd = bridge.current_command()
                    return self._json(200, {"command": cmd.to_json() if cmd else None})

                if path == "/v1/status":
                    return self._json(200, asdict(bridge.preflight()))

                return self._json(404, {"error": "not_found"})

            def do_POST(self):
                payload = self._payload()
                if self.path == "/v1/heartbeat":
                    bridge.record_heartbeat(
                        status=str(payload.get("status", "failed")),
                        title=str(payload.get("title", "")),
                        url=str(payload.get("url", "")),
                        extension_version=str(payload.get("extensionVersion", "")),
                        content_build=str(payload.get("buildId", payload.get("contentBuild", ""))),
                        protocol_version=int(payload.get("protocolVersion", 0) or 0),
                        bridge_schema_version=int(payload.get("bridgeSchemaVersion", 0) or 0),
                        transport_alive=bool(payload.get("transportAlive", True)),
                        runtime_alive=bool(payload.get("runtimeAlive", True)),
                        runtime_status=str(payload.get("runtimeStatus", payload.get("status", "ready"))),
                        consumer_id=str(payload.get("consumerId", "")),
                        last_runtime_ping_at=float(payload.get("lastRuntimePingAt", 0.0) or 0.0),
                        busy_request_id=payload.get("busyRequestId"),
                        busy_since=payload.get("busySince"),
                        busy_deadline_at=payload.get("busyDeadlineAt")
                    )
                    return self._json(200, {"ok": True})
                if self.path == "/v1/claim":
                    return self._json(200, {"claimed": bridge.claim_command(str(payload.get("requestId", "")), str(payload.get("claimant", "")))})
                if self.path == "/v1/cancel":
                    cancelled = bridge.cancel_command(str(payload.get("requestId", "")))
                    return self._json(200, {"ok": True, "cancelled": cancelled})
                if self.path == "/v1/result":
                    accepted, reason = bridge.submit_result(GeminiResult.from_json(payload))
                    return self._json(200, {"ok": True, "accepted": accepted, "reason": reason})
                return self._json(404, {"error": "not_found"})

        try:
            self._server = _LoopbackHTTPServer((self.host, self.port), Handler)
            self.port = int(self._server.server_port)
            self.bridge.bridge_server_started = True
            self.bridge.bridge_server_error = ""
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

            # Execute loopback self-test on /v1/status
            self_test_status = "FAIL"
            try:
                import http.client
                conn = http.client.HTTPConnection(self.host, self.port, timeout=1.0)
                conn.request("GET", "/v1/status")
                resp = conn.getresponse()
                if resp.status == 200:
                    self_test_status = "PASS"
                conn.close()
            except Exception as st_err:
                self_test_status = f"FAIL({st_err})"

            logger.log(f"[GEMINI][BRIDGE_SERVER] bind={self.host}:{self.port} selfTest={self_test_status}")
        except Exception as e:
            self.bridge.bridge_server_started = False
            self.bridge.bridge_server_error = str(e)
            logger.log(f"[GEMINI] Bridge HTTP 서버 시작 실패 ({self.host}:{self.port}): {e}", "ERROR")
            raise

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
