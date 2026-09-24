import copy
import threading
from enum import Enum, auto
from typing import Optional, Callable, List
from dataclasses import dataclass
from app.models import FeedPost


class FeedState(Enum):
    IDLE = auto()
    STARTING_BROWSER = auto()
    OPENING_SOURCE = auto()
    DISCOVERING = auto()
    OPENING_POST = auto()
    CHECKING_LIKE = auto()
    LIKING = auto()
    OPENING_COMMENT = auto()
    FILLING_DRAFT = auto()
    WAITING_USER = auto()
    # Gemini 전송 파이프라인 단계 (계획서 §8)
    PREPARING_CONTEXT = auto()   # 본문 추출·맥락 선별 중
    WRITING_PROMPT = auto()      # 에디터에 입력 중
    VERIFYING_INPUT = auto()     # 입력 내용 검증 중
    WAITING_SEND_READY = auto()  # 전송 버튼 준비 대기
    AWAITING_COMMIT = auto()     # 클릭 후 turn 상관관계 확인 중
    WAITING_RESPONSE = auto()    # Gemini 응답 대기 중
    QUALITY_CHECK = auto()       # 생성 결과 품질 검사 중
    SUBMITTING = auto()
    VERIFYING = auto()
    RECORDING = auto()
    SKIPPING = auto()
    LOADING_MORE = auto()
    PACING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    COMPLETED = auto()
    ERROR = auto()



@dataclass
class BotRuntimeState:
    current_state: FeedState = FeedState.IDLE
    current_post: Optional[FeedPost] = None
    processed_count: int = 0
    total_target_count: int = 0
    likes_count: int = 0
    comments_count: int = 0
    skipped_count: int = 0
    message: str = "대기 중"
    pause_reason: Optional[str] = None

    # Granular sampling & pipeline metrics
    candidates_count: int = 0
    sampled_in_count: int = 0
    sampled_out_count: int = 0
    generated_success_count: int = 0
    generated_fail_count: int = 0
    submission_unknown_count: int = 0

    # AI Context & Prompt state
    current_post_title: str = ""
    current_post_excerpt: str = ""
    current_ai_prompt: str = ""
    ai_clipboard_ready: bool = False

    # Gemini 파이프라인 진단 (계획서 §8)
    gemini_phase: str = ""           # 현재 단계 코드 (VERIFYING_INPUT 등)
    gemini_failure_count: int = 0    # 현재 연속 실패 횟수
    gemini_failure_code: str = ""    # 마지막 실패 코드


def make_gemini_phase_message(phase: str, failure_code: str = "", fail_count: int = 0, max_fail: int = 3) -> str:
    """
    계획서 §8: Gemini 파이프라인 단계·실패 코드를 UI 메시지로 변환한다.
    입력 확인 실패 / 전송 미확인 / 응답 대기 / 3회 실패 일시정지를 구분한다.
    """
    if phase == "VERIFYING_INPUT":
        if failure_code == "prompt_exact_readback_failed" and fail_count > 0:
            return f"Gemini 입력 확인 실패. 아직 전송하지 않았습니다. 연속 실패 {fail_count}/{max_fail}."
        return "Gemini 입력 내용 확인 중..."
    if phase == "WAITING_SEND_READY":
        return "Gemini 전송 버튼 준비 대기 중..."
    if phase == "AWAITING_COMMIT":
        if failure_code in ("send_state_lost", "prompt_editor_changed_before_send"):
            return "Gemini 전송 여부를 확인하지 못했습니다. 자동 재전송하지 않습니다."
        return "Gemini 전송 확인 중..."
    if phase == "WAITING_RESPONSE":
        return "Gemini 응답 대기 중..."
    if phase == "QUALITY_CHECK":
        return "생성 결과 품질 검사 중..."
    if phase == "PAUSED" and failure_code == "prompt_exact_readback_failed":
        return f"입력 확인이 {max_fail}회 연속 실패해 일시정지했습니다."
    return ""


class StateManager:
    """스레드 안전한 런타임 상태 관리자"""

    def __init__(self):
        self.state = BotRuntimeState()
        self._lock = threading.RLock()
        self._listeners: List[Callable[[BotRuntimeState], None]] = []

    def register_listener(self, listener: Callable[[BotRuntimeState], None]):
        with self._lock:
            self._listeners.append(listener)

    def get_snapshot(self) -> BotRuntimeState:
        with self._lock:
            return copy.deepcopy(self.state)

    def get_state(self) -> BotRuntimeState:
        return self.get_snapshot()

    def update(
        self,
        new_state: Optional[FeedState] = None,
        message: Optional[str] = None,
        post: Optional[FeedPost] = None,
        inc_like: bool = False,
        inc_comment: bool = False,
        inc_skip: bool = False,
        inc_processed: bool = False,
        inc_candidate: bool = False,
        inc_sampled_in: bool = False,
        inc_sampled_out: bool = False,
        inc_gen_success: bool = False,
        inc_gen_fail: bool = False,
        inc_submission_unknown: bool = False,
        total_targets: Optional[int] = None,
        current_post_title: Optional[str] = None,
        current_post_excerpt: Optional[str] = None,
        current_ai_prompt: Optional[str] = None,
        ai_clipboard_ready: Optional[bool] = None,
        pause_reason: Optional[str] = None,
        clear_pause_reason: bool = False,
        gemini_phase: Optional[str] = None,
        gemini_failure_count: Optional[int] = None,
        gemini_failure_code: Optional[str] = None,
    ):
        with self._lock:
            if new_state is not None:
                self.state.current_state = new_state
                if new_state != FeedState.PAUSED and pause_reason is None:
                    self.state.pause_reason = None
            if clear_pause_reason:
                self.state.pause_reason = None
            elif pause_reason is not None:
                self.state.pause_reason = pause_reason
            if message is not None:
                self.state.message = message
            if post is not None:
                self.state.current_post = post
            if inc_like:
                self.state.likes_count += 1
            if inc_comment:
                self.state.comments_count += 1
            if inc_skip:
                self.state.skipped_count += 1
            if inc_processed:
                self.state.processed_count += 1
            if inc_candidate:
                self.state.candidates_count += 1
            if inc_sampled_in:
                self.state.sampled_in_count += 1
            if inc_sampled_out:
                self.state.sampled_out_count += 1
            if inc_gen_success:
                self.state.generated_success_count += 1
            if inc_gen_fail:
                self.state.generated_fail_count += 1
            if inc_submission_unknown:
                self.state.submission_unknown_count += 1
            if total_targets is not None:
                self.state.total_target_count = total_targets
            if current_post_title is not None:
                self.state.current_post_title = current_post_title
            if current_post_excerpt is not None:
                self.state.current_post_excerpt = current_post_excerpt
            if current_ai_prompt is not None:
                self.state.current_ai_prompt = current_ai_prompt
            if ai_clipboard_ready is not None:
                self.state.ai_clipboard_ready = ai_clipboard_ready
            if gemini_phase is not None:
                self.state.gemini_phase = gemini_phase
            if gemini_failure_count is not None:
                self.state.gemini_failure_count = gemini_failure_count
            if gemini_failure_code is not None:
                self.state.gemini_failure_code = gemini_failure_code

            snapshot = copy.deepcopy(self.state)
            listeners_copy = list(self._listeners)

        for cb in listeners_copy:
            try:
                cb(snapshot)
            except Exception:
                pass

    def reset(self, total_targets: int = 0):
        with self._lock:
            self.state = BotRuntimeState(
                current_state=FeedState.IDLE,
                total_target_count=total_targets,
                message="작업 대기"
            )
            snapshot = copy.deepcopy(self.state)
            listeners_copy = list(self._listeners)

        for cb in listeners_copy:
            try:
                cb(snapshot)
            except Exception:
                pass
