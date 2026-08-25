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


class ReactionType(str, Enum):
    LIKE = "like"               # 공감
    IMPRESSIVE = "impressive"   # 칭찬
    THANKS = "thanks"           # 감사
    HAHA = "haha"               # 웃김
    WOW = "wow"                 # 놀람
    SAD = "sad"                 # 슬픔
    NONE = "none"               # 리액션 없음
    UNKNOWN = "unknown"         # 판별 불가


@dataclass
class ReactionStateResult:
    reacted: bool
    reaction_type: ReactionType
    confidence: LikeConfidence
    signals: List[str] = field(default_factory=list)


# 하위 호환용 LikeStateResult
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
    Naver Blog 다중 리액션 모델 기반 Reaction/Like Transaction Service
    - 실제 data-type="like" 옵션 버튼 타겟팅 및 상태 판별
    - 기존 칭찬/감사/웃김 등 타 리액션 보존 (ALREADY_REACTED 시 클릭 금지)
    - !_on && !active 방식의 거짓 NOT_LIKED 제거, exact class token('on', 'off') 및 aria-pressed/checked 기반 판정
    """

    @classmethod
    def resolve_reaction_state(cls, page: Page) -> ReactionStateResult:
        if not page:
            return ReactionStateResult(reacted=False, reaction_type=ReactionType.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=["page_none"])

        try:
            eval_res = page.evaluate("""
                () => {
                    const options = Array.from(document.querySelectorAll("a.u_likeit_list_button[data-type], button.u_likeit_list_button[data-type], a.u_likeit_list_btn[data-type], button.u_likeit_list_btn[data-type]"));
                    
                    if (!options || options.length === 0) {
                        // 옵션을 아직 찾지 못했을 때 요약 버튼 검사
                        const summary = document.querySelector("a.u_likeit_button, button.u_likeit_button");
                        if (summary) {
                            const aria = summary.getAttribute("aria-pressed");
                            const cls = (summary.className || "").split(/\\s+/);
                            if (aria === "true" || cls.includes("_on") || cls.includes("active")) {
                                return { reacted: true, reaction_type: "like", confidence: "medium", signals: ["summary_aria_pressed_true"] };
                            }
                        }
                        return { reacted: false, reaction_type: "unknown", confidence: "unknown", signals: ["options_not_found"] };
                    }

                    let activeType = null;
                    const signals = [];
                    let allExplicitOff = true;

                    for (const opt of options) {
                        const dType = opt.getAttribute("data-type") || "unknown";
                        const ariaP = opt.getAttribute("aria-pressed");
                        const ariaC = opt.getAttribute("aria-checked");
                        const ariaS = opt.getAttribute("aria-selected");
                        const clsList = (opt.className || "").split(/\\s+/);

                        const isExplicitOn = (ariaP === "true" || ariaC === "true" || clsList.includes("on") || clsList.includes("active") || clsList.includes("_on"));
                        const isExplicitOff = (ariaP === "false" || ariaC === "false" || ariaS === "false" || clsList.includes("off"));

                        if (isExplicitOn) {
                            activeType = dType;
                            signals.push(`opt_${dType}_on`);
                        }
                        if (!isExplicitOff) {
                            allExplicitOff = false;
                        }
                    }

                    if (activeType) {
                        return { reacted: true, reaction_type: activeType, confidence: "high", signals: signals };
                    }

                    if (allExplicitOff && options.length >= 1) {
                        return { reacted: false, reaction_type: "none", confidence: "high", signals: ["all_options_explicit_off"] };
                    }

                    return { reacted: false, reaction_type: "unknown", confidence: "unknown", signals: ["ambiguous_options_state"] };
                }
            """)

            type_map = {
                "like": ReactionType.LIKE,
                "impressive": ReactionType.IMPRESSIVE,
                "thanks": ReactionType.THANKS,
                "haha": ReactionType.HAHA,
                "wow": ReactionType.WOW,
                "sad": ReactionType.SAD,
                "none": ReactionType.NONE,
                "unknown": ReactionType.UNKNOWN
            }
            cf_map = {
                "high": LikeConfidence.HIGH,
                "medium": LikeConfidence.MEDIUM,
                "low": LikeConfidence.LOW,
                "unknown": LikeConfidence.UNKNOWN
            }

            return ReactionStateResult(
                reacted=eval_res.get("reacted", False),
                reaction_type=type_map.get(eval_res.get("reaction_type"), ReactionType.UNKNOWN),
                confidence=cf_map.get(eval_res.get("confidence"), LikeConfidence.UNKNOWN),
                signals=eval_res.get("signals", [])
            )
        except Exception as e:
            return ReactionStateResult(reacted=False, reaction_type=ReactionType.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=[f"eval_error: {e}"])

    @classmethod
    def resolve_like_state(cls, page: Page, like_btn: Optional[Locator] = None) -> LikeStateResult:
        """하위 호환용 래퍼: ReactionState를 LikeStateResult로 매핑"""
        rx = cls.resolve_reaction_state(page)
        if rx.reacted:
            return LikeStateResult(state=LikeState.LIKED, confidence=rx.confidence, signals=rx.signals)
        elif rx.reaction_type == ReactionType.NONE and rx.confidence == LikeConfidence.HIGH:
            return LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH, signals=rx.signals)
        return LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, signals=rx.signals)

    @classmethod
    def execute_like_transaction(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None
    ) -> LikeProcessResult:
        """
        트랜잭션 기반 실제 공감(data-type="like") 옵션 클릭 및 검증
        """
        if LikeCircuitBreaker.is_open():
            logger.log("  ⚡ [LIKE] 서킷 브레이커가 열려 있어 공감 클릭을 건너뜁니다.", "WARNING")
            return LikeProcessResult(
                state_before=LikeState.UNKNOWN,
                action_taken=False,
                state_after=LikeState.UNKNOWN,
                eligibility_reason="circuit_breaker_open"
            )

        # 1. PRECONDITION 검사
        rx = cls.resolve_reaction_state(page)

        if rx.reacted:
            logger.log(f"  ❤️ [LIKE] 이미 리액션 완료된 글입니다 (타입={rx.reaction_type.value}, 신뢰도={rx.confidence.value}). 기존 선택을 보존합니다.")
            return LikeProcessResult(state_before=LikeState.LIKED, action_taken=False, state_after=LikeState.LIKED)

        if rx.reaction_type != ReactionType.NONE or rx.confidence != LikeConfidence.HIGH:
            logger.log(f"  ⚠️ [LIKE] 리액션 상태 확신도 부족(type={rx.reaction_type.value}, conf={rx.confidence.value})으로 취소 방지를 위해 클릭을 건너뜁니다.", "WARNING")
            return LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN, error="low_confidence_precondition")

        # 2. 실제 좋아요 옵션(data-type="like") 탐색 및 필요 시 요약 오프너 클릭
        logger.log("  🤍 [LIKE] 미공감 확인(HIGH), 실제 공감(data-type='like') 옵션을 클릭합니다.")
        try:
            like_opt = MobileDOMResolver.get_reaction_like_option(page)

            # 만약 옵션이 숨겨져 있거나 레이어가 닫혀 있다면 요약 오프너 클릭
            if not like_opt or like_opt.count() == 0 or not like_opt.is_visible():
                summary_btn = MobileDOMResolver.get_reaction_summary_button(page)
                if summary_btn and summary_btn.count() > 0:
                    summary_btn.scroll_into_view_if_needed(timeout=1500)
                    summary_btn.click(timeout=1500)
                    interruptible_wait(stop_event, 0.4)

                like_opt = MobileDOMResolver.get_reaction_like_option(page)

            if not like_opt or like_opt.count() == 0:
                logger.log("  ⚠️ [LIKE] 실제 공감 옵션(data-type='like') 엘리먼트를 찾지 못했습니다.", "WARNING")
                return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.UNKNOWN, error="like_option_not_found")

            # 3. 실제 좋아요 옵션 클릭
            like_opt.scroll_into_view_if_needed(timeout=1500)
            like_opt.click(timeout=1500)

            # 4. POSTCONDITION 검증 (최대 2.5초간 Polling)
            deadline = time.time() + 2.5
            post_rx = ReactionStateResult(reacted=False, reaction_type=ReactionType.UNKNOWN, confidence=LikeConfidence.UNKNOWN)

            while time.time() < deadline:
                if stop_event and stop_event.is_set():
                    break
                post_rx = cls.resolve_reaction_state(page)
                if post_rx.reacted and post_rx.reaction_type == ReactionType.LIKE:
                    break
                interruptible_wait(stop_event, 0.2)

            if post_rx.reacted and post_rx.reaction_type == ReactionType.LIKE:
                logger.log("  ✅ [LIKE] 공감 트랜잭션 성공: 실제 공감 옵션 활성화(LIKED) 확인 완료!")
                return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
            else:
                logger.log(f"  ❌ [LIKE] 공감 옵션 클릭 후 상태 전이 실패(after_type={post_rx.reaction_type.value}, conf={post_rx.confidence.value})", "ERROR")
                LikeCircuitBreaker.trip("like_transition_unverified")
                return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.NOT_LIKED, error="postcondition_failed")

        except Exception as e:
            logger.log(f"  ❌ [LIKE] 공감 클릭 트랜잭션 예외: {e}", "ERROR")
            LikeCircuitBreaker.trip(f"click_exception: {e}")
            return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.UNKNOWN, error=str(e))
