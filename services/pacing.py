import random
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple
from browser.session import interruptible_wait
from src.logger import logger


class PacingKind(str, Enum):
    ACTION = "action"
    NEXT_POST = "next_post"
    PAUSE = "pause"


@dataclass
class PacingResult:
    kind: PacingKind
    seconds: float
    interrupted: bool = False


class PacingService:
    """
    모바일 피드 어시스턴트 작업 속도 조절(Pacing) 서비스
    - 모든 랜덤 시간 계산 및 interruptible 대기를 단일 모듈에서 총괄
    """
    def __init__(self, config, stop_event: Optional[threading.Event] = None, state_manager = None):
        self.config = config
        self.stop_event = stop_event
        self.state_manager = state_manager

    def _range(self, min_key: str, max_key: str, default_min: float = 1.0, default_max: float = 2.5) -> Tuple[float, float]:
        low = float(self.config.get(min_key, default_min))
        high = float(self.config.get(max_key, default_max))
        if high < low:
            low, high = high, low
        return low, high

    def wait_action(self) -> PacingResult:
        """글 진입 후 공감 전, 공감 후 댓글 열기 전 등 짧은 UI 동작 사이 대기"""
        if not self.config.get("pacing_enabled", True):
            return PacingResult(PacingKind.ACTION, 0.0)

        low, high = self._range("action_delay_min", "action_delay_max", 1.0, 2.5)
        if high <= 0:
            return PacingResult(PacingKind.ACTION, 0.0)

        seconds = round(random.uniform(low, high), 2)
        interrupted = interruptible_wait(self.stop_event, seconds)
        return PacingResult(PacingKind.ACTION, seconds, interrupted)

    def wait_next_post(self) -> PacingResult:
        """한 글 처리가 완료된 후 다음 글로 이동하기 전 대기"""
        if not self.config.get("pacing_enabled", True):
            return PacingResult(PacingKind.NEXT_POST, 0.0)

        low, high = self._range("next_post_delay_min", "next_post_delay_max", 2.0, 5.0)
        if high <= 0:
            return PacingResult(PacingKind.NEXT_POST, 0.0)

        seconds = round(random.uniform(low, high), 2)
        if self.state_manager:
            from app.state import FeedState
            self.state_manager.update(new_state=FeedState.PACING, message=f"다음 글로 이동 전 대기 중... ({seconds:.1f}초)")

        logger.log(f"[PACING] 다음 글 진입 전 {seconds:.1f}초 대기...")
        interrupted = interruptible_wait(self.stop_event, seconds)
        return PacingResult(PacingKind.NEXT_POST, seconds, interrupted)

    def maybe_pause(self) -> Optional[PacingResult]:
        """일정 확률로 발생하는 안전한 긴 휴지(Pause)"""
        if not self.config.get("pacing_enabled", True) or not self.config.get("random_pause_enabled", True):
            return None

        chance = float(self.config.get("random_pause_chance", 0.10))
        chance = max(0.0, min(1.0, chance))

        if random.random() >= chance:
            return None

        low, high = self._range("random_pause_min", "random_pause_max", 8.0, 20.0)
        if high <= 0:
            return None

        seconds = round(random.uniform(low, high), 1)
        if self.state_manager:
            from app.state import FeedState
            self.state_manager.update(new_state=FeedState.PAUSED, message=f"잠시 쉬는 중... ({seconds:.1f}초)")

        logger.log(f"☕ [PAUSE] 작업 간격 조정을 위해 {seconds:.1f}초 동안 잠시 대기합니다.")
        interrupted = interruptible_wait(self.stop_event, seconds)
        return PacingResult(PacingKind.PAUSE, seconds, interrupted)
