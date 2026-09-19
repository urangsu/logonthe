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
    deadline_at_ms: Optional[int] = None
    created_at_ms: Optional[int] = None
    generation_deadline_at: Optional[float] = None
    generation_deadline_at_ms: Optional[int] = None
    acceptance_deadline_at: Optional[float] = None
    acceptance_deadline_at_ms: Optional[int] = None
    timeout_seconds: float = 55.0
    delivery_reserve_seconds: float = 9.0

    DELIVERY_RESERVE_SECONDS: float = 9.0

    @classmethod
    def create(cls, post_key: str, navigation_version: int, prompt: str, request_id: Optional[str] = None, timeout_seconds: float = 55.0, delivery_reserve_seconds: Optional[float] = None):
        now = time.time()
        timeout = max(10.0, float(timeout_seconds or 55.0))
        delivery_reserve = float(delivery_reserve_seconds) if delivery_reserve_seconds is not None else cls.DELIVERY_RESERVE_SECONDS
        generation_deadline = now + timeout
        acceptance_deadline = generation_deadline + delivery_reserve
        return cls(
            request_id=request_id or uuid.uuid4().hex,
            post_key=post_key,
            navigation_version=navigation_version,
            prompt=prompt,
            created_at=now,
            deadline_at=generation_deadline,
            deadline_at_ms=int(generation_deadline * 1000),
            created_at_ms=int(now * 1000),
            generation_deadline_at=generation_deadline,
            generation_deadline_at_ms=int(generation_deadline * 1000),
            acceptance_deadline_at=acceptance_deadline,
            acceptance_deadline_at_ms=int(acceptance_deadline * 1000),
            timeout_seconds=timeout,
            delivery_reserve_seconds=delivery_reserve,
        )

    def to_json(self) -> Dict[str, object]:
        now = self.created_at
        deadline = self.deadline_at
        d_ms = self.deadline_at_ms if self.deadline_at_ms is not None else (int(deadline) if deadline > 1e11 else int(deadline * 1000))
        c_ms = self.created_at_ms if self.created_at_ms is not None else (int(now) if now > 1e11 else int(now * 1000))
        gen_at = self.generation_deadline_at if self.generation_deadline_at is not None else (self.created_at + self.timeout_seconds)
        gen_ms = self.generation_deadline_at_ms if self.generation_deadline_at_ms is not None else int(gen_at * 1000)
        acc_at = self.acceptance_deadline_at if self.acceptance_deadline_at is not None else deadline
        acc_ms = self.acceptance_deadline_at_ms if self.acceptance_deadline_at_ms is not None else d_ms
        return {
            "requestId": self.request_id,
            "postKey": self.post_key,
            "navigationVersion": self.navigation_version,
            "prompt": self.prompt,
            "createdAt": self.created_at,
            "deadlineAt": self.deadline_at,
            "deadlineAtMs": d_ms,
            "createdAtMs": c_ms,
            "generationDeadlineAt": gen_at,
            "generationDeadlineAtMs": gen_ms,
            "acceptanceDeadlineAt": acc_at,
            "acceptanceDeadlineAtMs": acc_ms,
            "overallDeadlineAtMs": acc_ms,
            "timeoutSeconds": self.timeout_seconds,
            "deliveryReserveSeconds": self.delivery_reserve_seconds,
        }


@dataclass(frozen=True)
class GeminiResult:
    request_id: str
    post_key: str
    navigation_version: int
    status: GeminiResultStatus
    text: str = ""
    error: str = ""
    delivery_id: Optional[str] = None

    @classmethod
    def from_json(cls, payload: Dict[str, object]):
        req_id = str(payload.get("requestId", "") or "").strip()
        post_k = str(payload.get("postKey", "") or "").strip()
        if not req_id:
            raise ValueError("missing_request_id")
        if not post_k:
            raise ValueError("missing_post_key")

        nav_v = payload.get("navigationVersion", None)
        try:
            nav_int = int(nav_v)
        except (ValueError, TypeError):
            raise ValueError("invalid_navigation_version")
        if nav_int <= 0:
            raise ValueError("invalid_navigation_version")

        raw_status = str(payload.get("status", "") or "").strip().lower()
        if not raw_status:
            raise ValueError("missing_result_status")
        try:
            status_enum = GeminiResultStatus(raw_status)
        except ValueError as exc:
            raise ValueError(f"invalid_result_status:{raw_status}") from exc

        text_val = str(payload.get("text", "") or "")
        err_val = str(payload.get("error", "") or "")
        delivery_id = str(payload.get("deliveryId", "") or "").strip() or None
        return cls(
            req_id,
            post_k,
            nav_int,
            status_enum,
            text_val,
            err_val,
            delivery_id,
        )

    def to_json(self) -> Dict[str, object]:
        data = {
            "requestId": self.request_id,
            "postKey": self.post_key,
            "navigationVersion": self.navigation_version,
            "status": self.status.value,
            "text": self.text,
            "error": self.error,
        }
        if self.delivery_id:
            data["deliveryId"] = self.delivery_id
        return data


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
    cancelled_request_ids: Optional[List[str]] = None

    def to_json(self) -> Dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "title": self.title,
            "url": self.url,
            "message": self.message,
            "extensionVersion": self.extension_version,
            "contentBuild": self.content_build,
            "protocolVersion": self.protocol_version,
            "bridgeSchemaVersion": self.bridge_schema_version,
            "heartbeatAgeMs": self.heartbeat_age_ms,
            "bridgeSessionId": self.bridge_session_id,
            "activeRequestId": self.active_request_id,
            "commandState": self.command_state,
            "cancelledRequestIds": list(self.cancelled_request_ids) if self.cancelled_request_ids else [],
        }


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
        self._last_completed_request_id: Optional[str] = None
        self._last_completed_at: float = 0.0
        self._cancel_requests: set[str] = set()
        self._stop_event: Optional[threading.Event] = None
        self._skip_event: Optional[threading.Event] = None
        self._expected_extension_version = expected_extension_version or contract.extension_version
        self._expected_build_id = expected_build_id or contract.runtime_build
        self._protocol_version_expected = contract.protocol_version
        self._bridge_schema_version_expected = contract.bridge_schema_version
        self.bridge_server_started = True
        self.bridge_server_error = ""

    def record_heartbeat(
        self,
        status: object,
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
        if isinstance(status, dict):
            payload = status
            status = str(payload.get("status", "failed"))
            title = str(payload.get("title", ""))
            url = str(payload.get("url", ""))
            extension_version = str(payload.get("extensionVersion", ""))
            content_build = str(payload.get("buildId", payload.get("contentBuild", "")))
            protocol_version = int(payload.get("protocolVersion", 0) or 0)
            bridge_schema_version = int(payload.get("bridgeSchemaVersion", 0) or 0)
            transport_alive = bool(payload.get("transportAlive", True))
            runtime_alive = bool(payload.get("runtimeAlive", True))
            runtime_status = str(payload.get("runtimeStatus", payload.get("status", "ready")))
            consumer_id = str(payload.get("consumerId", ""))
            last_runtime_ping_at = float(payload.get("lastRuntimePingAt", 0.0) or 0.0)
            busy_request_id = payload.get("busyRequestId")
            busy_since = payload.get("busySince")
            busy_deadline_at = payload.get("busyDeadlineAt")

        with self._condition:
            now = time.time()
            if status == "busy" and busy_request_id and busy_request_id == self._last_completed_request_id:
                if (now - self._last_completed_at) < 2.5:
                    status = "settling"
                    busy_request_id = None
            elif status == "busy" and not busy_request_id and self._last_completed_request_id:
                if (now - self._last_completed_at) < 2.5:
                    status = "settling"

            self._heartbeat_at = now
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

    def get_result(self, request_id: str) -> Optional[GeminiResult]:
        with self._condition:
            return self._results.get(request_id)

    def cancel_command(self, request_id: Optional[str] = None) -> bool:
        with self._condition:
            target_rid = request_id or self._active_request_id
            if target_rid:
                self._cancel_requests.add(target_rid)
                if len(self._cancel_requests) > 100:
                    oldest = next(iter(self._cancel_requests))
                    self._cancel_requests.discard(oldest)
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

    def is_cancelled(self, request_id: Optional[str]) -> bool:
        if not request_id:
            return False
        with self._condition:
            return request_id in self._cancel_requests

    def is_command_cancelled(self, request_id: Optional[str]) -> bool:
        return self.is_cancelled(request_id)

    def cancel_status(self, request_id: str) -> dict:
        return {
            "requestId": request_id,
            "cancelled": self.is_cancelled(request_id),
        }

    def cancelled_request_ids(self) -> List[str]:
        with self._condition:
            return list(self._cancel_requests)

    def preflight_status(self) -> dict:
        pf = self.preflight()
        data = pf.to_json()
        data["cancelled_request_ids"] = list(self._cancel_requests)
        data["reason"] = pf.status
        return data

    def set_control_events(self, stop_event: Optional[threading.Event] = None, skip_event: Optional[threading.Event] = None) -> None:
        with self._condition:
            self._stop_event = stop_event
            self._skip_event = skip_event

    def _build_preflight(self, ready: bool, status: str, title: str = "", url: str = "", message: str = "", age_ms: int = 0) -> GeminiPreflight:
        return GeminiPreflight(
            ready=ready,
            status=status,
            title=title,
            url=url,
            message=message,
            extension_version=self._extension_version,
            content_build=self._content_build,
            protocol_version=self._protocol_version,
            bridge_schema_version=self._bridge_schema_version,
            heartbeat_age_ms=age_ms,
            bridge_session_id=self.bridge_session_id,
            active_request_id=self._active_request_id,
            command_state=self._command_state,
            cancelled_request_ids=list(self._cancel_requests),
        )

    def preflight(self) -> GeminiPreflight:
        with self._condition:
            if not self.bridge_server_started:
                err_lower = self.bridge_server_error.lower()
                status = "bridge_port_in_use" if "address already in use" in err_lower or "in use" in err_lower else "bridge_server_unavailable"
                return self._build_preflight(False, status, message=f"Gemini bridge server unavailable: {self.bridge_server_error}")

            if not self._ever_seen_heartbeat:
                return self._build_preflight(False, "heartbeat_never_received", message="Gemini extension heartbeat never received")

            age_sec = time.time() - self._heartbeat_at
            age_ms = int(age_sec * 1000)
            fresh = age_sec <= self.HEARTBEAT_TTL

            if not fresh:
                return self._build_preflight(
                    False, "heartbeat_stale", self._heartbeat_title, self._heartbeat_url,
                    f"Gemini heartbeat stale (age: {age_sec:.1f}s)", age_ms
                )

            version_ok = not self._expected_extension_version or self._extension_version == self._expected_extension_version
            if not version_ok:
                return self._build_preflight(
                    False, "extension_version_mismatch", self._heartbeat_title, self._heartbeat_url,
                    f"Extension version mismatch: expected {self._expected_extension_version}, got {self._extension_version}", age_ms
                )

            identity_ok = (
                self._content_build == self._expected_build_id
                and self._protocol_version == self._protocol_version_expected
                and self._bridge_schema_version == self._bridge_schema_version_expected
            )
            if not identity_ok:
                return self._build_preflight(
                    False, "extension_identity_mismatch", self._heartbeat_title, self._heartbeat_url,
                    f"Extension runtime identity mismatch: build={self._content_build}, proto={self._protocol_version}, schema={self._bridge_schema_version}", age_ms
                )

            if self._heartbeat_status == "auth_required":
                return self._build_preflight(
                    False, "auth_required", self._heartbeat_title, self._heartbeat_url,
                    "Gemini login required (auth_required)", age_ms
                )

            if self._heartbeat_status == "dom_unsupported":
                return self._build_preflight(
                    False, "dom_unsupported", self._heartbeat_title, self._heartbeat_url,
                    "Gemini DOM editor not found or unsupported", age_ms
                )

            if self._heartbeat_status == "captcha":
                return self._build_preflight(
                    False, "captcha", self._heartbeat_title, self._heartbeat_url,
                    "Gemini captcha detected", age_ms
                )

            if self._heartbeat_status == "settling":
                return self._build_preflight(
                    False, "settling", self._heartbeat_title, self._heartbeat_url,
                    "Gemini runtime settling after completed command", age_ms
                )

            if self._heartbeat_status == "busy":
                now_t = time.time()
                if self._last_completed_request_id and (now_t - self._last_completed_at) < 2.5:
                    if not self._last_busy_request_id or self._last_busy_request_id == self._last_completed_request_id:
                        return self._build_preflight(
                            False, "settling", self._heartbeat_title, self._heartbeat_url,
                            f"Gemini runtime settling after completed request (rid={self._last_completed_request_id})", age_ms
                        )

                if self._last_busy_deadline_at and time.time() > (self._last_busy_deadline_at / 1000.0 if self._last_busy_deadline_at > 100_000_000_000 else self._last_busy_deadline_at):
                    return self._build_preflight(
                        False, "busy_stale_deadline", self._heartbeat_title, self._heartbeat_url,
                        "Gemini busy deadline exceeded (busy_stale_deadline)", age_ms
                    )
                if self._last_busy_request_id:
                    if self._active_request_id and self._active_request_id == self._last_busy_request_id:
                        return self._build_preflight(
                            False, "busy_active_command", self._heartbeat_title, self._heartbeat_url,
                            f"Gemini currently generating for active command (rid={self._active_request_id})", age_ms
                        )
                    else:
                        return self._build_preflight(
                            False, "busy_orphaned", self._heartbeat_title, self._heartbeat_url,
                            f"Gemini busy with orphaned request (rid={self._last_busy_request_id})", age_ms
                        )
                elif not self._active_request_id:
                    return self._build_preflight(
                        False, "busy_orphaned", self._heartbeat_title, self._heartbeat_url,
                        "Gemini busy without active Python request (busy_orphaned)", age_ms
                    )
                else:
                    return self._build_preflight(
                        False, "busy", self._heartbeat_title, self._heartbeat_url,
                        "Gemini generation currently busy", age_ms
                    )

            if self._heartbeat_status == GeminiResultStatus.READY.value:
                return self._build_preflight(
                    True, "ready", self._heartbeat_title, self._heartbeat_url,
                    "Gemini extension ready", age_ms
                )

            return self._build_preflight(
                False, self._heartbeat_status, self._heartbeat_title, self._heartbeat_url,
                f"Gemini extension status: {self._heartbeat_status}", age_ms
            )

    def await_ready(
        self,
        timeout: float = 5.0,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None,
    ) -> GeminiPreflight:
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            if stop_event and stop_event.is_set():
                break
            if skip_event and skip_event.is_set():
                break
            pf = self.preflight()
            if pf.ready:
                return pf
            if pf.status == "settling":
                with self._condition:
                    self._condition.wait(timeout=0.2)
                continue
            if pf.status in ("busy_orphaned", "busy_stale_deadline"):
                logger.log(f"⚠️ [GEMINI][RECOVERY] {pf.status} 감지 -> 자동 정리 요청", "WARNING")
                self.cancel_command(self._last_busy_request_id)
            with self._condition:
                self._condition.wait(timeout=0.2)
        return self.preflight()

    def publish(
        self,
        command: GeminiCommand,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None,
    ) -> bool:
        with self._condition:
            s_evt = stop_event or self._stop_event
            k_evt = skip_event or self._skip_event
            if (s_evt and s_evt.is_set()) or (k_evt and k_evt.is_set()):
                logger.log(f"[GEMINI][PUBLISH_REJECTED] Stop or skip event set before publish (rid={command.request_id})", "WARNING")
                return False
            if command.request_id in self._cancel_requests:
                logger.log(f"[GEMINI][PUBLISH_REJECTED] Command was already cancelled (rid={command.request_id})", "WARNING")
                return False
            now = time.time()
            if self._command is not None:
                if self._command_state == "pending":
                    active_until = self._command.generation_deadline_at or self._command.deadline_at
                elif self._command_state == "claimed":
                    active_until = self._command.acceptance_deadline_at or self._command.deadline_at
                else:
                    active_until = 0

                if self._command_state in ("pending", "claimed") and now < active_until:
                    logger.log(f"[GEMINI][PUBLISH_REJECTED] Active command still running (rid={self._command.request_id}, state={self._command_state})", "WARNING")
                    return False
            if self._ever_seen_heartbeat and self._heartbeat_status in ("busy", "auth_required", "dom_unsupported", "captcha"):
                now_t = time.time()
                is_settling = bool(self._last_completed_request_id and (now_t - self._last_completed_at) < 2.5)
                if not is_settling:
                    logger.log(f"[GEMINI][PUBLISH_REJECTED] Extension runtime is not ready (status={self._heartbeat_status})", "WARNING")
                    return False
            self._command = command
            self._command_state = "pending"
            self._command_claimed_by = ""
            self._active_request_id = command.request_id
            self._results.pop(command.request_id, None)
            logger.log(f"[GEMINI][PUBLISH] rid={command.request_id} post={command.post_key} nav={command.navigation_version}")
            self._condition.notify_all()
            return True

    def current_command(self) -> Optional[GeminiCommand]:
        with self._condition:
            if not self._command:
                return None
            now = time.time()
            if self._command_state == "pending":
                active_until = self._command.generation_deadline_at or self._command.deadline_at
            elif self._command_state == "claimed":
                active_until = self._command.acceptance_deadline_at or self._command.deadline_at
            else:
                active_until = 0

            if (active_until and now >= active_until) or (now - self._command.created_at > self.COMMAND_TTL):
                self._command_state = "expired"
                self._command = None
                return None
            return self._command if self._command_state == "pending" else None

    def wait_for_command(
        self,
        timeout: float = 15.0,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None,
    ) -> Optional[GeminiCommand]:
        deadline = time.monotonic() + max(0.1, timeout)
        s_evt = stop_event or self._stop_event
        k_evt = skip_event or self._skip_event
        with self._condition:
            while time.monotonic() < deadline:
                if (s_evt and s_evt.is_set()) or (k_evt and k_evt.is_set()):
                    return None
                cmd = self.current_command()
                if cmd:
                    if (s_evt and s_evt.is_set()) or (k_evt and k_evt.is_set()):
                        return None
                    return cmd
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.5))
            if (s_evt and s_evt.is_set()) or (k_evt and k_evt.is_set()):
                return None
            return self.current_command()

    def claim_command(self, request_id: str, claimant: str = "") -> bool:
        with self._condition:
            if (self._stop_event and self._stop_event.is_set()) or (self._skip_event and self._skip_event.is_set()):
                logger.log(f"[GEMINI][CLAIM_REJECTED] Stop or skip event set before claim (rid={request_id})", "WARNING")
                return False
            if request_id in self._cancel_requests:
                logger.log(f"[GEMINI][CLAIM_REJECTED] Command was cancelled (rid={request_id})", "WARNING")
                return False
            if not self._command or self._command_state != "pending":
                return False
            if time.time() >= (self._command.acceptance_deadline_at or self._command.deadline_at):
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

    @staticmethod
    def _same_result_payload(left: GeminiResult, right: GeminiResult) -> bool:
        return (
            left.request_id == right.request_id
            and left.post_key == right.post_key
            and left.navigation_version == right.navigation_version
            and left.status == right.status
            and left.text == right.text
            and left.error == right.error
        )

    def submit_result(self, result: GeminiResult) -> tuple[bool, str]:
        with self._condition:
            if not result.request_id:
                return False, "missing_request_id"

            if result.request_id in self._cancel_requests:
                return False, "request_cancelled"

            cached_res = self._results.get(result.request_id)
            if cached_res:
                if self._same_result_payload(cached_res, result):
                    return True, "already_accepted"
                return False, "duplicate_result_conflict"

            if not result.post_key:
                return False, "missing_post_key"
            if result.navigation_version <= 0:
                return False, "invalid_navigation_version"

            command = self._command
            if not command:
                return False, "no_active_command"
            if not command.post_key or command.navigation_version <= 0:
                return False, "active_command_identity_invalid"
            acc_deadline = getattr(command, "acceptance_deadline_at", None) or command.deadline_at
            if time.time() > acc_deadline:
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
            if len(self._results) > 100:
                oldest_key = next(iter(self._results))
                self._results.pop(oldest_key, None)
            self._command_state = "completed" if result.status == GeminiResultStatus.COMPLETED else "failed"
            self._command = None
            self._command_claimed_by = ""
            if self._active_request_id == result.request_id:
                self._active_request_id = None
            self._last_completed_request_id = result.request_id
            self._last_completed_at = time.time()
            status_val = result.status.value if hasattr(result.status, "value") else str(result.status)
            if status_val == GeminiResultStatus.COMPLETED.value:
                self._heartbeat_status = "settling"
            else:
                self._heartbeat_status = "recovering"
                logger.log(f"[GEMINI_GENERATION_FAILED] rid={result.request_id} reason={result.error or status_val}")
            self._last_busy_request_id = None
            logger.log(f"[GEMINI][RESULT_ACCEPTED] rid={result.request_id} post={result.post_key} nav={result.navigation_version} status={status_val}")
            self._condition.notify_all()
            return True, "accepted"

    def wait_for_result(
        self,
        command: GeminiCommand,
        timeout: Optional[float] = None,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None
    ) -> Optional[GeminiResult]:
        acc_deadline = getattr(command, "acceptance_deadline_at", None) or command.deadline_at
        timeout = float(timeout) if timeout is not None else max(0.0, acc_deadline - time.time())
        deadline_at = acc_deadline or (time.time() + timeout)
        deadline = min(time.monotonic() + timeout, time.monotonic() + max(0.0, deadline_at - time.time()))
        with self._condition:
            while True:
                s_evt = stop_event or self._stop_event
                k_evt = skip_event or self._skip_event
                if s_evt and s_evt.is_set():
                    logger.log(f"[GEMINI][WAIT_RESULT] stop_event 감지 -> 명령 취소 전송 (rid={command.request_id})")
                    self.cancel_command(command.request_id)
                    return None
                if k_evt and k_evt.is_set():
                    logger.log(f"[GEMINI][WAIT_RESULT] skip_event 감지 -> 명령 취소 전송 (rid={command.request_id})")
                    self.cancel_command(command.request_id)
                    return None
                result = self._results.get(command.request_id)
                if result:
                    if self._active_request_id == command.request_id:
                        self._active_request_id = None
                    logger.log(f"[GEMINI][RESULT_CONSUMED] rid={command.request_id} post={result.post_key} nav={result.navigation_version}")
                    return result
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
                try:
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Access-Control-Request-Private-Network")
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_OPTIONS(self):
                try:
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Access-Control-Request-Private-Network")
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Access-Control-Max-Age", "86400")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    return

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
                    return self._json(200, bridge.preflight().to_json())

                if path == "/v1/cancel":
                    rid = query.get("requestId", [None])[0]
                    return self._json(200, {
                        "ok": True,
                        "cancelled": bridge.is_cancelled(rid) if rid else bool(bridge.cancelled_request_ids()),
                        "cancelledRequestIds": bridge.cancelled_request_ids(),
                    })

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
                    target_rid = str(payload.get("requestId", "")).strip() or None
                    cancelled = bridge.cancel_command(target_rid)
                    return self._json(200, {
                        "ok": True,
                        "cancelled": cancelled,
                        "cancelledRequestIds": bridge.cancelled_request_ids(),
                    })
                if self.path == "/v1/result":
                    try:
                        rid = str(payload.get("requestId", "") or "").strip()
                        post_k = str(payload.get("postKey", "") or "").strip()
                        nav_v = payload.get("navigationVersion", None)
                        status_v = str(payload.get("status", "") or "").strip()
                        text_len = len(str(payload.get("text", "") or ""))
                        logger.log(
                            f"[GEMINI][RESULT_INCOMING] rid={rid} post={post_k} nav={nav_v} status={status_v} chars={text_len}"
                        )
                        res_obj = GeminiResult.from_json(payload)
                        accepted, reason = bridge.submit_result(res_obj)
                        if accepted:
                            logger.log(f"[GEMINI][RESULT_DELIVERY_ACCEPTED] rid={rid} reason={reason}")
                        else:
                            logger.log(
                                f"[GEMINI][RESULT_REJECTED] rid={rid} reason={reason}", "WARNING"
                            )
                        return self._json(200, {
                            "ok": accepted,
                            "received": True,
                            "accepted": accepted,
                            "reason": reason,
                        })
                    except ValueError as err:
                        logger.log(f"[GEMINI][RESULT_PARSE_REJECTED] err={err}", "WARNING")
                        return self._json(400, {
                            "ok": False,
                            "received": True,
                            "accepted": False,
                            "reason": str(err),
                        })
                    except Exception as err:
                        logger.log(f"[GEMINI][RESULT_PARSE_ERROR] err={err}", "ERROR")
                        return self._json(500, {"ok": False, "received": False, "accepted": False, "error": str(err)})

                if self.path == "/v1/event":
                    ev_type = str(payload.get("type", ""))
                    if ev_type in ("FRESH_CHAT_READY", "FRESH_CHAT_RUNTIME_READY", "FRESH_CHAT_EXEC_READY"):
                        inst = payload.get("instance")
                        epoch = payload.get("epoch")
                        tab = payload.get("tab")
                        epoch_key = f"{inst}:{epoch}"
                        seen_epochs = getattr(bridge, "_seen_fresh_chat_epochs", None)
                        if seen_epochs is None:
                            seen_epochs = set()
                            bridge._seen_fresh_chat_epochs = seen_epochs
                        if epoch_key not in seen_epochs:
                            seen_epochs.add(epoch_key)
                            if len(seen_epochs) > 50:
                                seen_epochs.pop()
                            logger.log(
                                f"[GEMINI][FRESH_CHAT_READY] tab={tab} "
                                f"instance={inst} epoch={epoch} source={ev_type}"
                            )
                    elif ev_type == "USER_TURN_CONFIRMED":
                        logger.log(
                            f"[GEMINI][USER_TURN_CONFIRMED] rid={payload.get('rid')} "
                            f"userUniqueTurns={payload.get('userUniqueTurns')}"
                        )
                    elif ev_type == "RESPONSE_TURN_BOUND":
                        logger.log(
                            f"[GEMINI][RESPONSE_TURN_BOUND] responseUniqueTurns={payload.get('responseUniqueTurns')} "
                            f"visibleTextCandidates={payload.get('visibleTextCandidates')}"
                        )
                    elif ev_type == "TEXT_NONEMPTY":
                        logger.log(
                            f"[GEMINI][TEXT_NONEMPTY] chars={payload.get('chars')}"
                        )
                    elif ev_type == "TEXT_STABLE":
                        logger.log(
                            f"[GEMINI][TEXT_STABLE] stableMs={payload.get('stableMs')}"
                        )
                    elif ev_type == "STREAMING_STALE_SUSPECTED":
                        logger.log(
                            f"[GEMINI][STREAMING_STALE_SUSPECTED] rid={payload.get('rid')} "
                            f"stableMs={payload.get('stableMs')} chars={payload.get('chars')}"
                        )
                    return self._json(200, {"ok": True})
                if self.path == "/v1/diag":
                    rid = str(payload.get("rid", ""))
                    elapsed = payload.get("elapsedMs", 0)
                    elapsed_s = f"{elapsed / 1000.0:.1f}s" if isinstance(elapsed, (int, float)) else str(elapsed)
                    fresh = payload.get("freshChatVerified", False)
                    confirmed = payload.get("sendConfirmed", False)
                    u_matches = payload.get("userSelectorMatches", payload.get("userQueryCount", 0))
                    u_turns = payload.get("userUniqueTurns", u_matches)
                    r_matches = payload.get("responseSelectorMatches", payload.get("responseSelectorCount", 0))
                    r_turns = payload.get("responseUniqueTurns", r_matches)
                    v_cands = payload.get("visibleTextCandidates", payload.get("visibleResponseCount", 0))
                    r_bound = payload.get("responseBound", payload.get("bound_response", False))
                    t_len = payload.get("responseTextLength", 0)
                    mut_age = payload.get("lastMutationAgeMs", 0)
                    mut_age_s = f"{mut_age / 1000.0:.1f}s" if isinstance(mut_age, (int, float)) else str(mut_age)
                    evidence = payload.get("generationEvidence", "")
                    build = payload.get("runtimeBuild", "")
                    exclude_reasons = payload.get("candidateExcludeReasons", "")
                    diag_log = (
                        f"[GEMINI][WAIT_DIAG] rid={rid} elapsed={elapsed_s} freshChatVerified={fresh} "
                        f"sendConfirmed={confirmed} userSelectorMatches={u_matches} userUniqueTurns={u_turns} "
                        f"responseSelectorMatches={r_matches} responseUniqueTurns={r_turns} "
                        f"visibleTextCandidates={v_cands} bound={r_bound} textLen={t_len} "
                        f"lastMutationAge={mut_age_s} evidence={evidence} build={build}"
                    )
                    if exclude_reasons:
                        diag_log += f" candidateExcludeReasons={exclude_reasons}"
                    logger.log(diag_log)
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "not_found"})

        try:
            self._server = _LoopbackHTTPServer((self.host, self.port), Handler)
            self.port = int(self._server.server_port)
            self.bridge.bridge_server_started = True
            self.bridge.bridge_server_error = ""
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

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
