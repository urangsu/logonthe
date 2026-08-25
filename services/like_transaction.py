import time
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from playwright.sync_api import Page, Locator
from app.models import LikeState, LikeProcessResult
from naver.resolver import MobileDOMResolver
from browser.session import interruptible_wait
from src.logger import logger


class LikeConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class LikeStateResult:
    state: LikeState
    confidence: LikeConfidence
    signals: List[str] = field(default_factory=list)


class LikeCircuitBreaker:
    """공감 클릭 실패나 상태 불일치 시 세션의 추가 공감 클릭을 안전하게 차단하는 서킷 브레이커"""
    _is_open: bool = False
    _open_reason: Optional[str] = None

    @classmethod
    def is_open(cls) -> bool:
        return cls._is_open

    @classmethod
    def trip(cls, reason: str) -> None:
        cls._is_open = True
        cls._open_reason = reason
        logger.log(f"⚡ [LIKE_CIRCUIT] 공감 서킷 브레이커가 작동(OPEN)되었습니다: {reason}. 세션 동안 추가 공감 클릭을 중단합니다.", "WARNING")

    @classmethod
    def reset(cls) -> None:
        cls._is_open = False
        cls._open_reason = None


class LikeTransactionService:
    """
    Like Transaction & Fail-Closed State Evaluator
    - PRECONDITION: NOT_LIKED + HIGH CONFIDENCE
    - ACTION: click()
    - POSTCONDITION: LIKED + HIGH CONFIDENCE (최대 2.5초 Polling)
    - 실패 시: Circuit Breaker 작동
    """

    @classmethod
    def resolve_like_state(cls, page: Page, like_btn: Optional[Locator] = None) -> LikeStateResult:
        if not page:
            return LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=["page_none"])

        btn = like_btn or MobileDOMResolver.get_like_button(page)
        if not btn or btn.count() == 0:
            return LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=["btn_not_found"])

        try:
            eval_res = btn.evaluate("""
                el => {
                    const outer = el.outerHTML || '';
                    const inner = el.innerHTML || '';
                    const cls = el.className || '';
                    const ariaPressed = el.getAttribute('aria-pressed');
                    const signals = [];

                    // 1. 확실한 LIKED 시그니처
                    if (outer.includes('__reaction__like') || inner.includes('__reaction__like')) {
                        signals.push('reaction_like_dom');
                    }
                    if (ariaPressed === 'true') {
                        signals.push('aria_pressed_true');
                    }
                    if (cls.includes('_on') || cls.includes(' active')) {
                        signals.push('class_on_active');
                    }

                    // 카운트 색상 검사
                    const countEl = el.querySelector('._count, .u_likeit_text, .num, span[class*="count"]');
                    if (countEl) {
                        const style = window.getComputedStyle(countEl);
                        const color = style.color || '';
                        if (color.includes('3, 199, 90') || color.includes('03C75A') || color.includes('03c75a')) {
                            signals.push('color_naver_green');
                        }
                    }

                    if (signals.length >= 2) {
                        return { state: 'liked', confidence: 'high', signals: signals };
                    } else if (signals.length === 1) {
                        return { state: 'liked', confidence: 'medium', signals: signals };
                    }

                    // 2. 확실한 NOT_LIKED 시그니처
                    const notLikedSignals = [];
                    if (outer.includes('__reaction__zeroface') || inner.includes('__reaction__zeroface')) {
                        notLikedSignals.push('reaction_zeroface_dom');
                    }
                    if (ariaPressed === 'false') {
                        notLikedSignals.push('aria_pressed_false');
                    }
                    if (cls.includes('off') || (!cls.includes('_on') && !cls.includes('active'))) {
                        notLikedSignals.push('class_off_inactive');
                    }

                    if (notLikedSignals.length >= 2) {
                        return { state: 'not_liked', confidence: 'high', signals: notLikedSignals };
                    } else if (notLikedSignals.length === 1) {
                        return { state: 'not_liked', confidence: 'medium', signals: notLikedSignals };
                    }

                    return { state: 'unknown', confidence: 'unknown', signals: ['no_clear_signature'] };
                }
            """)

            st_map = {"liked": LikeState.LIKED, "not_liked": LikeState.NOT_LIKED, "unknown": LikeState.UNKNOWN}
            cf_map = {"high": LikeConfidence.HIGH, "medium": LikeConfidence.MEDIUM, "low": LikeConfidence.LOW, "unknown": LikeConfidence.UNKNOWN}

            return LikeStateResult(
                state=st_map.get(eval_res.get("state"), LikeState.UNKNOWN),
                confidence=cf_map.get(eval_res.get("confidence"), LikeConfidence.UNKNOWN),
                signals=eval_res.get("signals", [])
            )
        except Exception as e:
            return LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=[f"eval_error: {e}"])

    @classmethod
    def execute_like_transaction(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None
    ) -> LikeProcessResult:
        """
        트랜잭션 기반 공감 처리 (Fail-Closed)
        """
        if LikeCircuitBreaker.is_open():
            logger.log("  ⚡ [LIKE] 서킷 브레이커가 열려 있어 공감 클릭을 건너뜁니다.", "WARNING")
            return LikeProcessResult(
                state_before=LikeState.UNKNOWN,
                action_taken=False,
                state_after=LikeState.UNKNOWN,
                eligibility_reason="circuit_breaker_open"
            )

        btn = MobileDOMResolver.get_like_button(page)
        if not btn or btn.count() == 0:
            return LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN, error="btn_not_found")

        # PRECONDITION 검사
        pre = cls.resolve_like_state(page, btn)
        if pre.state == LikeState.LIKED:
            logger.log("  ❤️ [LIKE] 이미 공감 완료된 글입니다. (트랜잭션 성공/생략)")
            return LikeProcessResult(state_before=LikeState.LIKED, action_taken=False, state_after=LikeState.LIKED)

        if pre.state != LikeState.NOT_LIKED or pre.confidence != LikeConfidence.HIGH:
            logger.log(f"  ⚠️ [LIKE] 공감 상태 확신도 부족(state={pre.state.value}, conf={pre.confidence.value})으로 취소 방지를 위해 클릭을 건너뜁니다.", "WARNING")
            return LikeProcessResult(state_before=pre.state, action_taken=False, state_after=pre.state, error="low_confidence_precondition")

        # ACTION
        logger.log("  🤍 [LIKE] 미공감 확인(HIGH), 공감(하트)을 클릭합니다.")
        try:
            btn.scroll_into_view_if_needed(timeout=1500)
            btn.click(timeout=1500)

            # POSTCONDITION (최대 2.5초간 LIKED 전이 Polling 검증)
            deadline = time.time() + 2.5
            post = LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.UNKNOWN)

            while time.time() < deadline:
                if stop_event and stop_event.is_set():
                    break
                fresh_btn = MobileDOMResolver.get_like_button(page)
                post = cls.resolve_like_state(page, fresh_btn)
                if post.state == LikeState.LIKED and post.confidence in (LikeConfidence.HIGH, LikeConfidence.MEDIUM):
                    break
                interruptible_wait(stop_event, 0.2)

            if post.state == LikeState.LIKED:
                logger.log("  ✅ [LIKE] 공감 트랜잭션 성공: 상태 전환(LIKED) 확인 완료!")
                return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
            else:
                logger.log(f"  ❌ [LIKE] 공감 클릭 후 상태 전이 실패(after={post.state.value}, conf={post.confidence.value})", "ERROR")
                LikeCircuitBreaker.trip("like_transition_unverified")
                return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=post.state, error="postcondition_failed")
        except Exception as e:
            logger.log(f"  ❌ [LIKE] 공감 클릭 트랜잭션 오류: {e}", "ERROR")
            LikeCircuitBreaker.trip(f"click_exception: {e}")
            return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.UNKNOWN, error=str(e))
