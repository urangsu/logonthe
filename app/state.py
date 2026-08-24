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
    SUBMITTING = auto()
    VERIFYING = auto()
    RECORDING = auto()
    SKIPPING = auto()
    LOADING_MORE = auto()
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


class StateManager:
    def __init__(self):
        self.state = BotRuntimeState()
        self._listeners: List[Callable[[BotRuntimeState], None]] = []

    def register_listener(self, listener: Callable[[BotRuntimeState], None]):
        self._listeners.append(listener)

    def update(
        self,
        new_state: Optional[FeedState] = None,
        message: Optional[str] = None,
        post: Optional[FeedPost] = None,
        inc_like: bool = False,
        inc_comment: bool = False,
        inc_skip: bool = False,
        inc_processed: bool = False,
        total_targets: Optional[int] = None
    ):
        if new_state is not None:
            self.state.current_state = new_state
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
        if total_targets is not None:
            self.state.total_target_count = total_targets

        for cb in self._listeners:
            try:
                cb(self.state)
            except Exception:
                pass

    def reset(self, total_targets: int = 0):
        self.state = BotRuntimeState(
            current_state=FeedState.IDLE,
            total_target_count=total_targets,
            message="작업 대기"
        )
        for cb in self._listeners:
            try:
                cb(self.state)
            except Exception:
                pass
