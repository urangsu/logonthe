"""
Single source of authority for bot execution control.
Manages thread-safe state machine, pause/resume acknowledgements,
side-effect checkpoints, and skip reservation.
"""
from enum import Enum
import threading
import time
from typing import Callable, Optional, Set

from browser.session import WaitInterruptionReason
from app.state import FeedState


class RunControlState(str, Enum):
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUME_REQUESTED = "resume_requested"
    STOPPED = "stopped"


from app.errors import UserStopRequestedError


class StopRequestedException(UserStopRequestedError):
    """Raised when stop_event is detected at a checkpoint."""
    pass


class RunControl:
    """
    Unified execution controller ensuring:
    1. Single authority for stop, pause, resume, and skip.
    2. Explicit pause acknowledgement (PAUSE_REQUESTED -> PAUSED).
    3. Checkpoint enforcement at all side-effect boundaries.
    4. Active-post skip reservation (does not leak to next post).
    5. Preserves pause checks even if pacing is 0 or disabled.
    """

    def __init__(
        self,
        stop_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None,
        state_manager: Optional[object] = None,
    ):
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)

        self.stop_event = stop_event or threading.Event()
        # Backwards compatible pause_event: set when paused or pause_requested
        self.pause_event = pause_event or threading.Event()
        self.skip_event = skip_event or threading.Event()

        self.pause_requested_event = threading.Event()
        self.pause_ack_event = threading.Event()

        self.state: RunControlState = RunControlState.RUNNING
        self.pause_reason: Optional[str] = None
        self.active_post_key: Optional[str] = None
        self.pending_skip_post_key: Optional[str] = None
        self.consumed_skip_post_keys: Set[str] = set()
        self.current_stage: str = ""
        self.state_manager = state_manager
        self.on_state_change: Optional[Callable[[RunControlState, str], None]] = None

    def set_active_post(self, post_key: Optional[str]) -> None:
        with self._lock:
            self.active_post_key = post_key

    def request_pause(self, reason: str = "user_manual_pause") -> None:
        with self._lock:
            if self.state in (RunControlState.STOPPED, RunControlState.PAUSED, RunControlState.PAUSE_REQUESTED):
                return
            self.state = RunControlState.PAUSE_REQUESTED
            self.pause_reason = reason
            self.pause_requested_event.set()
            self.pause_event.set()

            if self.state_manager and hasattr(self.state_manager, "update"):
                try:
                    self.state_manager.update(message="일시정지 요청 중...", pause_reason=reason)
                except Exception:
                    pass

            if self.on_state_change:
                self.on_state_change(RunControlState.PAUSE_REQUESTED, reason)

    def request_resume(self) -> None:
        with self._lock:
            if self.state not in (RunControlState.PAUSED, RunControlState.PAUSE_REQUESTED):
                return
            self.state = RunControlState.RUNNING
            self.pause_reason = None
            self.pause_requested_event.clear()
            self.pause_ack_event.clear()
            self.pause_event.clear()
            self._cv.notify_all()

            if self.state_manager and hasattr(self.state_manager, "update"):
                try:
                    self.state_manager.update(message="작업 재개됨", clear_pause_reason=True)
                except Exception:
                    pass

            if self.on_state_change:
                self.on_state_change(RunControlState.RUNNING, "")

    def request_stop(self, reason: str = "user_stop") -> None:
        with self._lock:
            self.state = RunControlState.STOPPED
            self.stop_event.set()
            self.pause_requested_event.clear()
            self.pause_ack_event.clear()
            self.pause_event.clear()
            self._cv.notify_all()

    def request_skip(self, target_post_key: Optional[str] = None) -> bool:
        """
        Reserve a skip command bound strictly to target_post_key or active_post_key.
        Multiple skip clicks on the same post coalesce into one.
        If no post is currently active, skip is ignored.
        """
        with self._lock:
            key = target_post_key or self.active_post_key
            if not key:
                return False
            self.pending_skip_post_key = key
            self.skip_event.set()
            return True

    def consume_pending_skip(self, post_key: str) -> bool:
        with self._lock:
            if self.pending_skip_post_key and self.pending_skip_post_key == post_key:
                self.pending_skip_post_key = None
                self.consumed_skip_post_keys.add(post_key)
                self.skip_event.clear()
                return True
            return False

    def is_skip_pending_for(self, post_key: str) -> bool:
        with self._lock:
            return self.pending_skip_post_key == post_key

    def safe_clear_skips(self) -> None:
        """
        Clears skip_event only if no active post has a pending skip reservation.
        Prevents processor teardown from wiping out a user's skip queued during pause.
        """
        with self._lock:
            if not self.pending_skip_post_key:
                self.skip_event.clear()

    def checkpoint(self, stage: str = "") -> None:
        """
        Side-effect boundary checkpoint.
        If pause was requested, transitions to PAUSED, sets pause_ack_event,
        and blocks until resumed or stopped.
        """
        with self._lock:
            self.current_stage = stage
            if self.stop_event.is_set():
                self.state = RunControlState.STOPPED
                raise StopRequestedException("작업 중지 요청됨")

            if self.state == RunControlState.PAUSE_REQUESTED or self.pause_requested_event.is_set():
                self.state = RunControlState.PAUSED
                self.pause_ack_event.set()
                self.pause_requested_event.clear()

                if self.state_manager and hasattr(self.state_manager, "update"):
                    try:
                        self.state_manager.update(
                            new_state=FeedState.PAUSED,
                            message="작업 일시정지됨 (재개 대기 중)",
                            pause_reason=self.pause_reason,
                        )
                    except Exception:
                        pass

                if self.on_state_change:
                    self.on_state_change(RunControlState.PAUSED, self.pause_reason or "")

                while self.state == RunControlState.PAUSED and not self.stop_event.is_set():
                    self._cv.wait(timeout=0.05)

                if self.stop_event.is_set():
                    self.state = RunControlState.STOPPED
                    raise StopRequestedException("작업 중지 요청됨")

    def interruptible_wait(
        self,
        seconds: float,
        step: float = 0.05,
        stage: str = "",
    ) -> WaitInterruptionReason:
        """
        Waits for given seconds while checking stop, pause, and skip signals.
        Always runs checkpoint at start, guaranteeing pause check even when seconds <= 0.
        """
        self.checkpoint(stage)

        if self.stop_event.is_set():
            return WaitInterruptionReason.STOPPED
        if self.skip_event.is_set():
            return WaitInterruptionReason.SKIPPED

        if seconds <= 0:
            return WaitInterruptionReason.COMPLETED

        elapsed = 0.0
        while elapsed < seconds:
            self.checkpoint(stage)
            if self.stop_event.is_set():
                return WaitInterruptionReason.STOPPED
            if self.skip_event.is_set():
                return WaitInterruptionReason.SKIPPED

            sleep_dur = min(step, seconds - elapsed)
            time.sleep(sleep_dur)
            elapsed += sleep_dur

        self.checkpoint(stage)
        if self.stop_event.is_set():
            return WaitInterruptionReason.STOPPED
        if self.skip_event.is_set():
            return WaitInterruptionReason.SKIPPED

        return WaitInterruptionReason.COMPLETED

    def is_paused(self) -> bool:
        with self._lock:
            return self.state in (RunControlState.PAUSED, RunControlState.PAUSE_REQUESTED)

    def is_stopped(self) -> bool:
        return self.stop_event.is_set() or self.state == RunControlState.STOPPED
