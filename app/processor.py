import random
import re
import threading
import time
import uuid
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, List, Dict
from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, FailureReason, FeedSourceType, PostActionPlan,
    WorkerCommandType, SubmitOrigin, CommentSubmitOutcome
)
from app.state import StateManager, FeedState
from app.errors import (
    UserStopRequestedError, RecoverablePostError, PostNavigationMismatchError,
    PostDOMContractError, CommentUnavailableError, BrowserDisconnectedError,
    BrowserFailureKind, classify_playwright_failure
)
from naver.target_guard import TargetPostGuard
from naver.resolver import MobileDOMResolver
from naver.interaction import LikeInteractionService, CommentInteractionService
from naver.editor_adapter import CommentEditorAdapter
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState
from naver.content_extractor import ContentContextExtractor
from services.draft import DraftService
from services.contextual_draft import ContextualDraftEngine
from services.like_eligibility import LikeEligibilityService, LikeEligibility
from services.like_transaction import LikeTransactionService, LikeConfidence, LikeCircuitBreaker
from services.food_comment_focus import FoodCommentFocus
from services.ai_prompt import AIPromptBuilder
from services.clipboard_bridge import ClipboardCommandBridge
from services.gemini_web import GeminiWebBridge
from services.gemini_existing_chrome import ExistingChromeGeminiBridge
from services.gemini_extension_bridge import GeminiCommand, GeminiExtensionBridge, GeminiResultStatus
from services.pacing import PacingService
from browser.session import interruptible_wait
from app.run_control import RunControl, StopRequestedException
from src.logger import logger


def build_quality_rewrite_feedback(gate_res: Any) -> str:
    """품질 검사(FinalQualityGate) 탈락 결과에 대한 1회 자동 재작성 피드백 생성"""
    code = getattr(gate_res, "code", "")
    matched = getattr(gate_res, "matched", None) or ""
    reason = getattr(gate_res, "reason", "") or ""

    if code == "laughter_or_emoticon":
        return "초성 웃음(ㅎㅎ, ㅋㅋ 등)이나 문자 이모티콘(:), ^^ 등)을 사용하지 말고 자연스러운 문장으로 작성해 주세요."
    elif code == "emoji":
        return "그림 이모지나 특수 기호를 사용하지 말고 텍스트로만 자연스럽게 작성해 주세요."
    elif code == "formal_register":
        hint = f"('{matched}')" if matched else ""
        return f"격식체/문어체{hint}를 쓰지 말고 친근하고 담백한 대화체(~네요, ~겠어요 등)로 작성해 주세요."
    elif code == "banned_macro":
        hint = f"('{matched}')" if matched else ""
        return f"상투적인 매크로 표현{hint}을 사용하지 말고 본문 내용에 구체적으로 호응하는 문장으로 작성해 주세요."
    elif code == "fake_experience":
        hint = f"('{matched}')" if matched else ""
        return f"가보지 않은 곳을 가본 척하거나 직접 해본 척하는 표현{hint} 대신, 포스팅을 본 소감으로 작성해 주세요."
    elif code == "absolute_or_pressure":
        hint = f"('{matched}')" if matched else ""
        return f"강요하거나 단정적인 어휘{hint} 대신 부드럽고 가벼운 공감 어조로 작성해 주세요."
    elif code == "forbidden_period":
        return "문장 끝이나 중간에 마침표(.)를 사용하지 말고 물결(~)이나 여운을 주는 어미(~네요)로 마무리해 주세요."
    elif code == "excessive_tilde":
        return "물결표(~)는 문장 전체에서 최대 1개 이하로만 절제해서 사용해 주세요."
    elif code == "excessive_slang":
        return "과도한 감탄사/신조어 대신 자연스럽고 담백한 어조로 작성해 주세요."
    elif code == "length_below_minimum":
        return "글자 수가 너무 짧습니다. 20자 이상 40자 내외로 자연스러운 1~2문장으로 작성해 주세요."
    elif code == "length_exceeded":
        return "글자 수가 너무 깁니다. 40자 내외의 간결한 1~2문장으로 압축하여 작성해 주세요."
    elif code == "rude_slang":
        return "속어/은어를 배제하고 예의 바르고 따뜻한 이웃 댓글로 작성해 주세요."
    elif code == "semantic_mismatch":
        return "본문의 핵심 주제/맥락에 부합하는 자연스러운 반응으로 작성해 주세요."
    else:
        cause = reason or code or "품질 기준 미달"
        return f"품질 기준 위반({cause})을 배제하고 담백하고 자연스러운 이웃 댓글로 다시 작성해 주세요."


@dataclass
class GenerationContext:
    """
    포스트별 AI 댓글 생성 상태 및 재작성 컨텍스트를 일관되게 관리하는 객체.
    - 최초 분석 시점부터 핵심 앵커와 보조 앵커를 항상 확보
    - 본문 확장(NEED_MORE_CONTEXT) 시 update_excerpt()를 통해 앵커를 완전 갱신
    - 모든 재작성/재시도 경로가 동일한 컨텍스트와 인자를 사용하도록 단일화
    - 시도 횟수 상한(최대 3회: 최초 1회 + 재작성 최대 2회)으로 무한 루프 차단
    """
    title: str
    excerpt: str
    preset: str
    style: str
    content_focus: str
    verified_anchors: List[str]
    secondary_anchors: List[str]
    style_plan: Optional[Any] = None
    recent_comments: List[str] = field(default_factory=list)
    corpus_examples: List[str] = field(default_factory=list)
    style_profile: Optional[Any] = None
    corpus_stats: Dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 0
    max_attempts: int = 3
    rewrite_reasons: List[str] = field(default_factory=list)
    style_policy: Optional[Any] = None

    def update_excerpt(self, new_excerpt: str) -> None:
        from services.food_comment_focus import FoodCommentFocus
        from services.user_learning_service import UserLearningService
        from services.comments.policy import CommentStylePolicy
        self.excerpt = new_excerpt
        info = FoodCommentFocus.analyze(self.title or "", self.excerpt or "")
        self.content_focus = info.get("focus", "GENERAL")
        self.verified_anchors = info.get("food_anchors", [])
        self.secondary_anchors = info.get("secondary_anchors", [])
        examples, stats, profile = UserLearningService.get_learning_context(
            category=self.content_focus,
            anchors=self.verified_anchors,
            limit=3,
        )
        self.corpus_examples = examples
        self.corpus_stats = stats
        self.style_profile = profile
        self.style_policy = CommentStylePolicy.from_context(
            preset=self.preset,
            style_profile=self.style_profile,
            action_plan=None,
        )

    def build_prompt(
        self,
        rewrite_feedback: Optional[str] = None,
        recent_repeats: Optional[str] = None,
        recent_comments: Optional[List[str]] = None,
        request_id: Optional[str] = None,
        previous_draft: Optional[str] = None,
    ) -> str:
        self.attempt_count += 1
        rid = request_id or uuid.uuid4().hex
        if rewrite_feedback:
            self.rewrite_reasons.append(rewrite_feedback)
        comments_to_use = self.recent_comments if recent_comments is None else recent_comments
        return AIPromptBuilder.build(
            title=self.title,
            excerpt=self.excerpt,
            style=self.style,
            preset=self.preset,
            request_id=rid,
            content_focus=self.content_focus,
            verified_anchors=self.verified_anchors,
            secondary_anchors=self.secondary_anchors,
            style_plan=self.style_plan,
            recent_comments=comments_to_use,
            recent_repeats=recent_repeats,
            rewrite_feedback=rewrite_feedback,
            corpus_examples=self.corpus_examples,
            style_profile=self.style_profile,
            corpus_stats=self.corpus_stats,
            style_policy=self.style_policy,
            previous_draft=previous_draft,
        )


class PostProcessor:
    def __init__(
        self,
        config,
        like_enabled: bool = True,
        comment_enabled: bool = True,
        comment_template: str = "",
        secret_comment: bool = False,
        ai_clipboard_enabled: bool = True,
        ai_context_max_chars: int = 700,
        ai_prompt_style: str = "warm_short",
        gemini_browser_mode: str = "extension_existing_chrome",
        gemini_web_enabled: bool = True,
        gemini_url: str = "https://gemini.google.com/app",
        gemini_page: Optional[Page] = None,
        stats_page: Optional[Page] = None,
        pacing_service: Optional[PacingService] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        state_manager: Optional[StateManager] = None,
        stop_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        gemini_extension_bridge: Optional[GeminiExtensionBridge] = None,
        session: Optional[Any] = None,
        on_like_committed: Optional[Callable[[FeedPost, LikeProcessResult], None]] = None,
        on_comment_committed: Optional[Callable[[FeedPost, CommentProcessResult], None]] = None,
        skip_event: Optional[threading.Event] = None,
        auto_comment_submit_enabled: Optional[bool] = None,
        auto_comment_chance: Optional[float] = None,
        auto_comment_delay_min: Optional[float] = None,
        auto_comment_delay_max: Optional[float] = None,
        history_store: Optional[Any] = None,
        run_control: Optional[RunControl] = None,
    ):
        self.config = config
        self.session = session
        self.history_store = history_store
        self.like_enabled = like_enabled
        self.comment_enabled = comment_enabled
        self.comment_template = comment_template
        self.secret_comment = secret_comment
        self.ai_clipboard_enabled = ai_clipboard_enabled
        self.ai_context_max_chars = ai_context_max_chars
        self.ai_prompt_style = ai_prompt_style
        self.gemini_browser_mode = gemini_browser_mode
        self.gemini_web_enabled = gemini_web_enabled
        self.gemini_url = gemini_url
        self.gemini_page = gemini_page
        self.stats_page = stats_page
        self.pacing = pacing_service
        self.command_bridge = command_bridge
        self.state_mgr = state_manager
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.skip_event = skip_event
        if run_control is not None:
            self.run_control = run_control
            self.stop_event = run_control.stop_event
            self.pause_event = run_control.pause_event
            self.skip_event = run_control.skip_event
        else:
            self.run_control = RunControl(
                stop_event=self.stop_event,
                pause_event=self.pause_event,
                skip_event=self.skip_event,
                state_manager=self.state_mgr,
            )
            self.stop_event = self.run_control.stop_event
            self.pause_event = self.run_control.pause_event
            self.skip_event = self.run_control.skip_event

        if self.pacing and hasattr(self.pacing, "run_control") and not getattr(self.pacing, "run_control", None):
            self.pacing.run_control = self.run_control

        self.gemini_extension_bridge = gemini_extension_bridge
        if self.gemini_extension_bridge and hasattr(self.gemini_extension_bridge, "set_control_events"):
            self.gemini_extension_bridge.set_control_events(self.stop_event, self.skip_event)
        self.on_like_committed = on_like_committed
        self.on_comment_committed = on_comment_committed
        self.navigation_version = 0

        # Random Auto Comment Submit settings
        cfg_dict = config if isinstance(config, dict) else (config.data if hasattr(config, "data") else {})
        if auto_comment_submit_enabled is not None:
            self.auto_comment_submit_enabled = bool(auto_comment_submit_enabled)
        else:
            self.auto_comment_submit_enabled = bool(cfg_dict.get("auto_comment_submit_enabled", False))

        if auto_comment_chance is not None:
            self.auto_comment_chance = float(auto_comment_chance)
        else:
            self.auto_comment_chance = float(cfg_dict.get("auto_comment_chance", 0.60))

        if auto_comment_delay_min is not None:
            self.auto_comment_delay_min = float(auto_comment_delay_min)
        else:
            self.auto_comment_delay_min = float(cfg_dict.get("auto_comment_delay_min", 3.0))

        if auto_comment_delay_max is not None:
            self.auto_comment_delay_max = float(auto_comment_delay_max)
        else:
            self.auto_comment_delay_max = float(cfg_dict.get("auto_comment_delay_max", 6.0))

        # P1-1 Invariant: if auto_comment_submit_enabled is active, comment pipeline is always enabled
        self.comment_enabled = bool(comment_enabled or self.auto_comment_submit_enabled)
        self._processed_post_keys: set[str] = set()

    def _prepare_comment_generation_context(
        self,
        post: FeedPost,
        action_plan: Optional[PostActionPlan] = None,
        preset: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Tuple[GenerationContext, str, str, str, str]:
        preset = preset or self.config.get("comment_style_preset", "community")
        suffix = DraftService.resolve_suffix(post.source, self.config)

        from services.food_comment_focus import FoodCommentFocus
        food_focus_info = FoodCommentFocus.analyze(post.title or "", post.excerpt or "")
        content_focus = food_focus_info["focus"]
        food_anchors = food_focus_info["food_anchors"]
        sec_anchors = food_focus_info.get("secondary_anchors", [])
        if content_focus != "GENERAL":
            logger.log(f"[FOOD_FOCUS] focus={content_focus} anchors={food_anchors[:3]} secondary={sec_anchors[:3]}")

        recent_submits = []
        if self.history_store and hasattr(self.history_store, "get_recent_submitted_comments"):
            recent_submits = self.history_store.get_recent_submitted_comments(limit=5)

        from services.user_learning_service import UserLearningService
        corpus_examples, corpus_stats, style_profile = UserLearningService.get_learning_context(
            category=content_focus,
            anchors=food_anchors,
            limit=2,
        )

        from services.comments.policy import CommentStylePolicy
        style_policy = CommentStylePolicy.from_context(
            preset=preset,
            config=self.config,
            style_profile=style_profile,
            action_plan=action_plan,
        )

        style_plan = (action_plan.style_plan if action_plan else None)
        gen_ctx = GenerationContext(
            title=post.title or "",
            excerpt=post.excerpt or "",
            preset=preset,
            style=self.ai_prompt_style,
            content_focus=content_focus,
            verified_anchors=food_anchors,
            secondary_anchors=sec_anchors,
            style_plan=style_plan,
            recent_comments=recent_submits,
            corpus_examples=corpus_examples,
            style_profile=style_profile,
            corpus_stats=corpus_stats,
            max_attempts=3,
            style_policy=style_policy,
        )

        req_id = request_id or uuid.uuid4().hex
        ai_prompt = ""
        if self.ai_clipboard_enabled or self.gemini_web_enabled:
            ai_prompt = gen_ctx.build_prompt(request_id=req_id)
            prompt_ver = getattr(AIPromptBuilder, "PROMPT_VERSION", "3.1.0-grounded-human")
            stats = gen_ctx.corpus_stats or {}
            logger.log(
                f"  📝 [PROMPT] version={prompt_ver} "
                f"raw_corpus_count={stats.get('total_raw', 0)} "
                f"cleaned_corpus_count={stats.get('cleaned', 0)} "
                f"user_edit_count={stats.get('user_edits', 0)} "
                f"referenced_examples={stats.get('referenced', len(gen_ctx.corpus_examples))} "
                f"recent_history_count={len(gen_ctx.recent_comments)}"
            )

        if self.state_mgr:
            self.state_mgr.update(
                current_post_title=post.title or "",
                current_post_excerpt=post.excerpt or "",
                current_ai_prompt=ai_prompt,
                ai_clipboard_ready=bool(ai_prompt)
            )

        return gen_ctx, req_id, ai_prompt, suffix, preset

    def process(
        self,
        detail_page: Page,
        post: FeedPost,
        action_plan: Optional[PostActionPlan] = None
    ) -> PostProcessResult:
        """
        단일 FeedPost에 대해:
        1. 상세 이동 -> TargetPostGuard 검증
        2. Like 처리 (action_plan.process_like가 True일 때)
        3. 댓글 처리: 댓글창 오픈 -> ServerCommentDuplicateGuard 확인 -> (부재 시에만) Gemini/로컬 초안 생성 -> 검토 및 등록
        처리 글 수(processed_count)는 모든 조기 반환과 예외를 포함해 정확히 1만 증가 (단일 지점 갱신)
        """
        post_key = post.key or post.url
        try:
            return self._process_internal(detail_page, post, action_plan=action_plan)
        finally:
            if hasattr(self, "run_control") and self.run_control:
                self.run_control.set_active_post(None)
                self.run_control.safe_clear_skips()
            if self.state_mgr and post_key not in self._processed_post_keys:
                self._processed_post_keys.add(post_key)
                self.state_mgr.update(inc_processed=True)

    def _process_internal(
        self,
        detail_page: Page,
        post: FeedPost,
        action_plan: Optional[PostActionPlan] = None
    ) -> PostProcessResult:
        result = PostProcessResult(post=post)
        self.navigation_version += 1
        navigation_version = self.navigation_version
        post_key = post.key or post.url

        if hasattr(self, "run_control") and self.run_control:
            self.run_control.set_active_post(post_key)
            if self.run_control.consume_pending_skip(post_key):
                logger.log(f"  ⏭️ [RUN_CONTROL] 이전 일시정지 중 예약된 스킵 실행: {post_key}")
                result.like_result.error = "user_skipped"
                result.comment_result.status = CommentSubmitState.SKIPPED
                result.comment_result.error = "user_skipped"
                if self.state_mgr:
                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                return result

            if not self.run_control.is_skip_pending_for(post_key):
                if self.skip_event:
                    self.skip_event.clear()
                if self.command_bridge:
                    self.command_bridge.clear_skips()
        else:
            if self.skip_event:
                self.skip_event.clear()
            if self.command_bridge:
                self.command_bridge.clear_skips()

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        effective_like = self.like_enabled and (action_plan.process_like if action_plan else True)
        effective_comment = self.comment_enabled and (action_plan.process_comment if action_plan else True)

        is_sweep_mode = bool(self.config.get("neighbor_like_sweep_mode", False) and getattr(post, "source", None) == FeedSourceType.NEIGHBOR)
        if is_sweep_mode:
            effective_like = True
            effective_comment = False

        if action_plan and action_plan.comment_sample_selected is False:
            result.comment_result = CommentProcessResult(
                status=CommentSubmitState.SKIPPED,
                error="random_chance_skipped",
            )
            if self.state_mgr:
                if post_key not in self._processed_post_keys:
                    self._processed_post_keys.add(post_key)
                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True, inc_processed=True)
                else:
                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
            if not effective_like:
                return result
            effective_comment = False

        # 1. 상세 페이지 이동 및 TargetPostGuard 엄격 검증
        if self.state_mgr:
            self.state_mgr.update(new_state=FeedState.OPENING_POST, message=f"게시글 이동: {post.title or post.key}", post=post)

        logger.log(f"--------------------------------------------------")
        logger.log(f"[POST] 상세 이동: {post.url} ({post.title or '제목 없음'})")

        if hasattr(self, "run_control") and self.run_control:
            self.run_control.checkpoint("before_detail_goto")

        try:
            detail_page.goto(post.url, wait_until="domcontentloaded", timeout=25000)
            if hasattr(self, "run_control") and self.run_control:
                self.run_control.checkpoint("after_detail_goto")
            if self.pacing:
                settle = self.pacing.wait_page_settle()
                if settle.stopped or (self.stop_event and self.stop_event.is_set()):
                    raise StopRequestedException("작업 중지 요청됨")
                if settle.skipped or (self.skip_event and self.skip_event.is_set()):
                    logger.log("  ⏭️ [USER] 페이지 진입 대기 중 다음 글로 건너뛰기 요청됨.")
                    result.like_result.error = "user_skipped"
                    result.comment_result.status = CommentSubmitState.SKIPPED
                    result.comment_result.error = "user_skipped"
                    if self.state_mgr:
                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                    return result
            else:
                interruptible_wait(self.stop_event, 1.0, skip_event=self.skip_event)
                if self.skip_event and self.skip_event.is_set():
                    logger.log("  ⏭️ [USER] 페이지 진입 대기 중 다음 글로 건너뛰기 요청됨.")
                    result.like_result.error = "user_skipped"
                    result.comment_result.status = CommentSubmitState.SKIPPED
                    result.comment_result.error = "user_skipped"
                    if self.state_mgr:
                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                    return result
        except StopRequestedException:
            raise
        except Exception as e:
            if self.session is not None and hasattr(self.session, "classify_failure"):
                failure_kind = self.session.classify_failure(e, page=detail_page)
            else:
                context = getattr(detail_page, "context", None)
                failure_kind = classify_playwright_failure(e, page=detail_page, context=context)

            if failure_kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                logger.log(f"💥 [POST] 브라우저/컨텍스트 종료 감지 ({failure_kind.value}): {e}", "ERROR")
                raise BrowserDisconnectedError(f"브라우저 또는 컨텍스트가 종료되었습니다: {e}")
            elif failure_kind == BrowserFailureKind.PAGE_CLOSED:
                logger.log(f"⚠️ [POST] 상세 페이지 종료 감지: {e}", "WARNING")
                raise PostNavigationMismatchError(f"페이지 종료 감지: {e}", post_key=post.key, reason="page_closed")
            else:
                logger.log(f"[POST] 상세 페이지 로드 오류: {e}", "WARNING")
                raise PostNavigationMismatchError(f"페이지 로드 실패: {e}", post_key=post.key, reason="navigation_error")

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        self._context_retry_done = False
        self._food_retry_done = False
        self._draft_rewrite_done = False
        self._contamination_retry_done = False
        self._food_anchor_retry_done = False
        self._quality_body_retry_done = False
        self._gemini_auto_repair_done = False
        # TargetPostGuard: 대상 글 일치 여부 확인 (Fail-Open 원천 차단)
        TargetPostGuard.verify(detail_page, post)

        # 추천/관심주제는 실제 본문을 한 번 더 확인한 뒤 어떤 상호작용도 수행한다.
        detail_context = None
        if post.source in {FeedSourceType.RECOMMENDATION, FeedSourceType.TARGETED_SEARCH} and self.config.get("topic_filter_enabled", True):
            detail_context = ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
            from naver.discovery.topic_filter import DiscoveryTopicFilter
            topic_decision = DiscoveryTopicFilter.evaluate(
                detail_context.title or post.title or "",
                detail_context.excerpt,
                stage="detail",
            )
            if not topic_decision.allowed:
                topic_reason = topic_decision.blocked_category or topic_decision.reason_code or "unknown"
                logger.log(
                    f"  [TOPIC_FILTER] detail/{topic_reason} "
                    f"evidence={list(topic_decision.evidence)} title=\"{detail_context.title or post.title or ''}\""
                )
                result.like_result.error = f"topic_blocked:{topic_reason}"
                result.comment_result.status = CommentSubmitState.SKIPPED
                result.comment_result.error = f"topic_blocked:{topic_reason}"
                return result

        # [사전 전송 준비] 좋아요 누르기 전에 댓글 생성부터 제미나이에 사전 전송하여 지체 시간 최소화
        early_gemini_command: Optional[GeminiCommand] = None

        def _cancel_early_command(reason: str) -> bool:
            nonlocal early_gemini_command

            cmd = early_gemini_command
            if cmd is None or self.gemini_extension_bridge is None:
                return False

            cancelled = self.gemini_extension_bridge.cancel_command(cmd.request_id)

            if cancelled:
                logger.log(
                    f"[GEMINI][EARLY_CANCEL] rid={cmd.request_id} reason={reason}"
                )
                early_gemini_command = None

            return cancelled

        try:
            early_gen_ctx: Optional[GenerationContext] = None
            early_suffix: Optional[str] = None
            early_preset: Optional[str] = None
            early_request_id: Optional[str] = None
            early_ai_prompt: str = ""
            sample_selected: bool = True

            if effective_comment and self.auto_comment_submit_enabled:
                if action_plan and action_plan.comment_sample_selected is not None:
                    sample_selected = action_plan.comment_sample_selected
                    roll = action_plan.comment_sample_roll if action_plan.comment_sample_roll is not None else 0.0
                else:
                    roll = random.random()
                    sample_selected = roll <= self.auto_comment_chance

                if not sample_selected:
                    logger.log(
                        f"  🎲 [COMMENT] 이번 글은 랜덤 작성 비율({int(self.auto_comment_chance * 100)}%, roll={roll:.2f})에 따라 댓글 작성을 건너뜁니다 (댓글창 미오픈)."
                    )
                    result.comment_result = CommentProcessResult(
                        status=CommentSubmitState.SKIPPED,
                        error="random_chance_skipped",
                    )
                    effective_comment = False
                else:
                    logger.log(
                        f"  🎲 [COMMENT] 랜덤 댓글 작성 대상 선정 ({int(self.auto_comment_chance * 100)}%, roll={roll:.2f}) - Gemini 댓글 생성 및 자동 등록을 진행합니다."
                    )

            if effective_comment and effective_like:
                if detail_context is None:
                    detail_context = ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
                    post.title = detail_context.title or post.title
                    post.excerpt = detail_context.excerpt
                if (post.excerpt or "").strip():
                    if self.gemini_web_enabled and self.gemini_browser_mode == "extension_existing_chrome" and self.gemini_extension_bridge:
                        if not (self.stop_event and self.stop_event.is_set()) and not (self.skip_event and self.skip_event.is_set()):
                            preflight = self.gemini_extension_bridge.await_ready(
                                timeout=3.0,
                                stop_event=self.stop_event,
                                skip_event=self.skip_event
                            )
                            if preflight and preflight.ready:
                                early_gen_ctx, early_request_id, early_ai_prompt, early_suffix, early_preset = self._prepare_comment_generation_context(
                                    post=post, action_plan=action_plan
                                )
                                if early_ai_prompt:
                                    gemini_timeout = float(self.config.get("gemini_response_timeout", 55.0))
                                    early_cmd = GeminiCommand.create(
                                        post_key=post.key,
                                        navigation_version=navigation_version,
                                        prompt=early_ai_prompt,
                                        request_id=early_request_id,
                                        timeout_seconds=gemini_timeout,
                                    )
                                    if hasattr(self, "run_control") and self.run_control:
                                        self.run_control.checkpoint("before_gemini_publish")
                                    if self.gemini_extension_bridge.publish(early_cmd, stop_event=self.stop_event, skip_event=self.skip_event):
                                        early_gemini_command = early_cmd
                                        logger.log(f"  🚀 [GEMINI] 좋아요 누르기 전 댓글 생성 요청 사전 전송 완료 (rid={early_request_id}) - 공감/페이싱과 병행 생성")
                                        if self.state_mgr:
                                            self.state_mgr.update(message="Gemini 댓글 생성 중 (공감 병행)...")

            # 2. 공감(하트) 처리 (Re-ordered Like Pipeline: State -> Popularity Guard -> Transaction)
            if effective_like:
                # Keep the configured human pacing before the first mutating
                # action as well as between actions.  This is intentionally
                # cancellable and never bypasses the state/confidence checks below.
                if self.pacing:
                    p_res = self.pacing.wait_pre_like()
                    if p_res.stopped or (self.stop_event and self.stop_event.is_set()):
                        _cancel_early_command("pre_like_pacing_stopped")
                        raise StopRequestedException("작업 중지 요청됨")
                    if p_res.skipped or (self.skip_event and self.skip_event.is_set()):
                        logger.log("  ⏭️ [USER] 공감 대기 중 다음 글로 건너뛰기 요청됨 (스킵).")
                        _cancel_early_command("pre_like_pacing_skipped")
                        result.like_result.error = "user_skipped"
                        result.comment_result.status = CommentSubmitState.SKIPPED
                        result.comment_result.error = "user_skipped"
                        if self.state_mgr:
                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                        return result
                if self.state_mgr:
                    self.state_mgr.update(new_state=FeedState.CHECKING_LIKE, message="공감 상태 및 조건 확인 중...")

                # 2-1. 공감 상태 및 신뢰도 우선 판별
                like_state_res = LikeTransactionService.resolve_like_state(detail_page)

                if like_state_res.state == LikeState.LIKED:
                    logger.log("  ❤️ [LIKE] 이미 공감(리액션) 완료된 글입니다. (트랜잭션 생략)")
                    result.like_result = LikeProcessResult(state_before=LikeState.LIKED, action_taken=False, state_after=LikeState.LIKED)
                elif like_state_res.state != LikeState.NOT_LIKED or like_state_res.confidence != LikeConfidence.HIGH:
                    logger.log(f"  ⚠️ [LIKE] 리액션 상태 확신도 부족(state={like_state_res.state.value}, conf={like_state_res.confidence.value})으로 취소 방지 위해 스킵", "WARNING")
                    result.like_result = LikeProcessResult(state_before=like_state_res.state, action_taken=False, state_after=like_state_res.state, error="low_confidence_skip")
                else:
                    # 2-2. NOT_LIKED + HIGH인 경우에만 Popularity Guard 평가 (공감 훑기 모드에서는 가드 면제)
                    if is_sweep_mode:
                        elig_ok = True
                        elig_like_cnt = None
                        elig_daily_vis = None
                    else:
                        elig = LikeEligibilityService.evaluate(
                            detail_page=detail_page,
                            stats_page=self.stats_page,
                            post=post,
                            config=self.config,
                            stop_event=self.stop_event
                        )
                        elig_ok = elig.eligible
                        elig_like_cnt = elig.like_count
                        elig_daily_vis = elig.daily_visitors

                    if not elig_ok:
                        result.like_result = LikeProcessResult(
                            state_before=LikeState.NOT_LIKED,
                            action_taken=False,
                            state_after=LikeState.NOT_LIKED,
                            eligibility_reason=elig.reason,
                            like_count=elig.like_count,
                            daily_visitors=elig.daily_visitors
                        )
                    else:
                        # 2-3. 실제 공감 트랜잭션 실행 (2-Path 및 UI Settle 적용)
                        if hasattr(self, "run_control") and self.run_control:
                            self.run_control.checkpoint("before_like_click")
                        tx_res = LikeTransactionService.execute_like_transaction(detail_page, self.stop_event, post=post)
                        if hasattr(self, "run_control") and self.run_control:
                            self.run_control.checkpoint("after_like_click")
                        tx_res.like_count = elig_like_cnt
                        tx_res.daily_visitors = elig_daily_vis
                        result.like_result = tx_res

                        if tx_res.action_taken and tx_res.state_after == LikeState.LIKED:
                            if self.state_mgr:
                                self.state_mgr.update(inc_like=True)
                            if self.on_like_committed:
                                try:
                                    self.on_like_committed(post, tx_res)
                                except Exception as cp_err:
                                    logger.log(f"  ⚠️ [CHECKPOINT] Like checkpoint 기록 실패: {cp_err}", "WARNING")

            if self.stop_event and self.stop_event.is_set():
                _cancel_early_command("stop_before_post_like")
                raise StopRequestedException("작업 중지 요청됨")

            # 액션 사이 Pacing 대기
            if self.pacing:
                p_res = self.pacing.wait_post_like() if effective_like else self.pacing.wait_action()
                if p_res.stopped or (self.stop_event and self.stop_event.is_set()):
                    _cancel_early_command("post_like_pacing_stopped")
                    raise StopRequestedException("작업 중지 요청됨")
                if p_res.skipped or (self.skip_event and self.skip_event.is_set()):
                    logger.log("  ⏭️ [USER] 공감 후 대기 중 다음 글로 건너뛰기 요청됨 (댓글 단계 스킵).")
                    _cancel_early_command("post_like_pacing_skipped")
                    result.comment_result.status = CommentSubmitState.SKIPPED
                    result.comment_result.error = "user_skipped"
                    return result

            # 3. 댓글 처리 (댓글창 오픈 -> 서버 중복 확인 -> [사전 전송 결과 수신 또는 생성] -> 입력 -> 승인)
            if effective_comment:
                if self.skip_event and self.skip_event.is_set():
                    logger.log("  ⏭️ [USER] 댓글 단계 진입 전 다음 글로 건너뛰기 요청됨 (스킵).")
                    _cancel_early_command("skip_before_comment")
                    result.comment_result.status = CommentSubmitState.SKIPPED
                    result.comment_result.error = "user_skipped"
                    if self.state_mgr:
                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                    return result

                TargetPostGuard.verify(detail_page, post)

                if self.state_mgr:
                    self.state_mgr.update(new_state=FeedState.OPENING_COMMENT, message="댓글 레이어 열기 및 서버 중복 확인 중...")

                # 3-1. 댓글 레이어 오픈 Polling
                if hasattr(self, "run_control") and self.run_control:
                    self.run_control.checkpoint("before_comment_open")
                open_ok, open_reason = CommentInteractionService.open_comment_layer(
                    detail_page, stop_event=self.stop_event, skip_event=self.skip_event
                )
                if hasattr(self, "run_control") and self.run_control:
                    self.run_control.checkpoint("after_comment_open")
                if not open_ok:
                    _cancel_early_command(f"comment_open_fail:{open_reason}")
                    if open_reason == "user_skipped":
                        logger.log("  ⏭️ [USER] 댓글 레이어 준비 중 다음 글로 건너뛰기 요청됨 (스킵).")
                        result.comment_result.status = CommentSubmitState.SKIPPED
                        result.comment_result.error = "user_skipped"
                        if self.state_mgr:
                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                        return result
                    elif open_reason in ("login_required", "comment_login_required"):
                        logger.log("  ⚠️ [COMMENT] 로그인이 필요한 게시글입니다.", "ERROR")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="login_required")
                    elif open_reason == "comment_disabled":
                        logger.log("  ⚠️ [COMMENT] 작성자가 댓글을 닫아둔 게시글입니다 (비활성화).", "WARNING")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="comment_disabled")
                    else:
                        logger.log(f"  ⚠️ [COMMENT] 댓글 레이어 준비 실패 ({open_reason}).", "WARNING")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error=open_reason)
                    if self.config.get("skip_on_comment_failure", True):
                        logger.log(f"  ⏭️ [COMMENT] 댓글창 열기 불가({open_reason}) -> 다음 글로 건너뜁니다.")
                        result.comment_result.status = CommentSubmitState.SKIPPED
                        if self.state_mgr:
                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                        return result
                else:
                    from services.user_learning_service import UserLearningService

                    # 3-2. 서버 사이드 중복 댓글 스캔 (Gemini 호출 전 반드시 선행)
                    comment_context = MobileDOMResolver.get_comment_editor_context(detail_page)
                    presence_frame = comment_context["frame"] if comment_context else detail_page
                    presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(presence_frame, stop_event=self.stop_event)
                    if presence.state == CommentPresenceState.PRESENT:
                        _cancel_early_command("server_comment_duplicate_present")
                        logger.log("  🛑 [COMMENT] 서버 댓글 목록에 이미 내 댓글이 존재합니다! (AI 호출/입력 0, 동기화 완료)")
                        result.comment_result = CommentProcessResult(
                            status=CommentSubmitState.SUBMITTED,
                            submitted_text=presence.comment_text or "서버 감지 기존 등록 댓글"
                        )
                        if self.on_comment_committed:
                            try:
                                self.on_comment_committed(post, result.comment_result)
                            except Exception as cp_err:
                                logger.log(f"  ⚠️ [CHECKPOINT] Comment checkpoint 기록 실패: {cp_err}", "WARNING")
                        return result
                    elif presence.state == CommentPresenceState.UNKNOWN:
                        _cancel_early_command("server_comment_duplicate_unknown")
                        logger.log("  ⚠️ [COMMENT] 댓글 목록이 불완전하여 중복 방지를 위해 안전하게 작성을 스킵합니다.", "WARNING")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="server_duplicate_check_unknown")
                        return result
                    else:
                        # 3-3. 내 댓글이 없는 것이 확실한 경우(ABSENT HIGH)에만 초안 생성 및 주입
                        context = detail_context or ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
                        post.title = context.title or post.title
                        post.excerpt = context.excerpt
                        if not post.excerpt.strip():
                            _cancel_early_command("content_extraction_insufficient")
                            # Never manufacture a plausible reply from a title
                            # when the locked/changed page yielded no body.
                            logger.log("  ⚠️ [COMMENT] 본문을 확인하지 못해 제목만으로 댓글을 만들지 않았습니다. 페이지를 확인하거나 발췌문을 보완해 주세요.", "WARNING")
                            result.comment_result = CommentProcessResult(
                                status=CommentSubmitState.FAILED,
                                error="content_extraction_insufficient"
                            )
                            if self.state_mgr:
                                self.state_mgr.update(current_post_title=post.title or "", current_post_excerpt="")
                            return result

                        if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                            _cancel_early_command("stop_or_skip_before_draft")
                            if self.stop_event and self.stop_event.is_set():
                                logger.log("  ⏹️ [USER] Gemini 생성 시작 전 정지 요청 감지 -> 즉시 중단합니다.")
                                raise StopRequestedException("User stopped before Gemini generation")
                            else:
                                logger.log("  ⏭️ [USER] Gemini 생성 시작 전 스킵 요청 감지 -> Gemini 발행 없이 건너뜁니다.")
                                result.comment_result = CommentProcessResult(
                                    status=CommentSubmitState.SKIPPED,
                                    error="user_skipped",
                                )
                                if self.state_mgr:
                                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                return result

                        if early_gen_ctx is not None:
                            gen_ctx = early_gen_ctx
                            request_id = early_request_id
                            ai_prompt = early_ai_prompt
                            suffix = early_suffix
                            preset = early_preset
                        else:
                            gen_ctx, request_id, ai_prompt, suffix, preset = self._prepare_comment_generation_context(
                                post=post, action_plan=action_plan
                            )
                        content_focus = gen_ctx.content_focus

                        draft_text = ""
                        draft_source_label = ""
                        detected_category = "UNKNOWN"
                        local_res = None
                        food_anchor_fail_closed = False

                        # [Tier 1] Gemini 자동 댓글 생성
                        gemini_answer = None
                        use_local_requested = False
                        if self.gemini_web_enabled and ai_prompt:
                            if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                _cancel_early_command("stop_or_skip_before_gemini_loop")
                                if self.stop_event and self.stop_event.is_set():
                                    logger.log("  ⏹️ [USER] Gemini 생성 시작 전 정지 요청 감지 -> 즉시 중단합니다.")
                                    raise StopRequestedException("User stopped before Gemini generation")
                                else:
                                    logger.log("  ⏭️ [USER] Gemini 생성 시작 전 스킵 요청 감지 -> Gemini 발행 없이 건너뜁니다.")
                                    result.comment_result = CommentProcessResult(
                                        status=CommentSubmitState.SKIPPED,
                                        error="user_skipped",
                                    )
                                    if self.state_mgr:
                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                    return result

                            if self.state_mgr:
                                self.state_mgr.update(message="Gemini로 자동 댓글 생성 중...")

                            if self.gemini_browser_mode == "extension_existing_chrome":
                                while not gemini_answer and not use_local_requested:
                                    if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                        _cancel_early_command("stop_or_skip_in_gemini_loop")
                                        if self.stop_event and self.stop_event.is_set():
                                            logger.log("  ⏹️ [USER] Gemini 루프 시작 전 정지 요청 감지 -> 중단합니다.")
                                            raise StopRequestedException("User stopped before Gemini loop")
                                        else:
                                            logger.log("  ⏭️ [USER] Gemini 루프 시작 전 스킵 요청 감지 -> Gemini 발행 없이 건너뜁니다.")
                                            result.comment_result = CommentProcessResult(
                                                status=CommentSubmitState.SKIPPED,
                                                error="user_skipped",
                                            )
                                            if self.state_mgr:
                                                self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                            return result

                                    failure = "invalid_response"
                                    command = None

                                    if early_gemini_command is not None:
                                        command = early_gemini_command
                                        logger.log(f"  ⚡ [GEMINI] 좋아요 누르기 전 사전 전송한 명령(rid={command.request_id}) 결과를 바로 대기합니다.")
                                    else:
                                        preflight = (
                                            self.gemini_extension_bridge.await_ready(
                                                timeout=5.0,
                                                stop_event=self.stop_event,
                                                skip_event=self.skip_event
                                            )
                                            if self.gemini_extension_bridge
                                            else None
                                        )
                                        if not (preflight and preflight.ready):
                                            failure = preflight.status if preflight else "bridge_not_started"
                                            logger.log(f"[GEMINI/EXTENSION] 연결 준비 안 됨: {failure}", "ERROR")
                                        else:
                                            if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                if self.stop_event and self.stop_event.is_set():
                                                    raise StopRequestedException("User stopped before command creation")
                                                else:
                                                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                    return result

                                            gemini_timeout = float(self.config.get("gemini_response_timeout", 55.0))
                                            command = GeminiCommand.create(
                                                post_key=post.key,
                                                navigation_version=navigation_version,
                                                prompt=ai_prompt,
                                                request_id=request_id,
                                                timeout_seconds=gemini_timeout,
                                            )
                                            if hasattr(self, "run_control") and self.run_control:
                                                self.run_control.checkpoint("before_gemini_publish")
                                            if not self.gemini_extension_bridge.publish(command, stop_event=self.stop_event, skip_event=self.skip_event):
                                                if self.skip_event and self.skip_event.is_set():
                                                    logger.log("  ⏭️ [USER] Gemini 발행 시점에 스킵 감지 -> 즉시 건너뜁니다.")
                                                    result.comment_result = CommentProcessResult(
                                                        status=CommentSubmitState.SKIPPED,
                                                        error="user_skipped",
                                                    )
                                                    if self.state_mgr:
                                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                    return result
                                                if self.stop_event and self.stop_event.is_set():
                                                    logger.log("  ⏹️ [USER] Gemini 발행 시점에 정지 감지 -> 즉시 중단합니다.")
                                                    raise StopRequestedException("User stopped during publish")
                                                failure = "publish_rejected"
                                                logger.log(f"[GEMINI/EXTENSION] 명령 발행 거부: {failure}", "ERROR")
                                                break

                                    if command is not None:
                                        extension_result = self.gemini_extension_bridge.wait_for_result(
                                            command,
                                            stop_event=self.stop_event,
                                            skip_event=self.skip_event,
                                        )
                                        if extension_result is not None:
                                            early_gemini_command = None
                                        else:
                                            _cancel_early_command("wait_result_timeout")

                                        if hasattr(self, "run_control") and self.run_control:
                                            self.run_control.checkpoint("after_gemini_publish")
                                        if self.skip_event and self.skip_event.is_set():
                                            logger.log("  ⏭️ [USER] Gemini 생성 중 다음 글로 바로 넘어가기 요청됨 (스킵).")
                                            result.comment_result = CommentProcessResult(
                                                status=CommentSubmitState.SKIPPED,
                                                error="user_skipped_during_gemini_generation",
                                            )
                                            if self.state_mgr:
                                                self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                            return result

                                        if extension_result and extension_result.status == GeminiResultStatus.COMPLETED:
                                            logger.log(
                                                f"[GEMINI][CORRELATED] rid={request_id} post={post.key} nav={navigation_version}"
                                            )
                                            raw_result_text = (extension_result.text or "").strip()
                                            if raw_result_text == "NEED_MORE_CONTEXT":
                                                if not getattr(self, "_context_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                    setattr(self, "_context_retry_done", True)
                                                    logger.log("  ℹ️ [GEMINI] 'NEED_MORE_CONTEXT' 수신 -> 본문 1800자 재추출 및 앵커 재분석 후 1회 retry 시도")
                                                    context = ContentContextExtractor.extract(detail_page, post, max_chars=1800)
                                                    new_excerpt = (context.excerpt or "").strip()
                                                    prev_excerpt = (post.excerpt or "").strip()
                                                    if not new_excerpt or new_excerpt == prev_excerpt:
                                                        logger.log("  ⏭️ [COMMENT] 본문 1800자 재수집 후에도 신규 근거 없음 -> context_insufficient로 안전하게 스킵")
                                                        result.comment_result = CommentProcessResult(
                                                            status=CommentSubmitState.SKIPPED,
                                                            error="context_insufficient"
                                                        )
                                                        if self.state_mgr:
                                                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                        return result

                                                    post.excerpt = new_excerpt
                                                    gen_ctx.update_excerpt(new_excerpt)
                                                    if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                        if self.stop_event and self.stop_event.is_set():
                                                            raise StopRequestedException("User stopped before Gemini retry")
                                                        result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                        if self.state_mgr:
                                                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                        return result
                                                    request_id = uuid.uuid4().hex
                                                    ai_prompt = gen_ctx.build_prompt(request_id=request_id)
                                                    if self.state_mgr:
                                                        self.state_mgr.update(
                                                            current_post_excerpt=post.excerpt or "",
                                                            current_ai_prompt=ai_prompt,
                                                            message="본문 확장 후 Gemini 댓글 재생성 중..."
                                                        )
                                                    continue
                                                else:
                                                    logger.log("  ⏭️ [COMMENT] 본문 1800자 재수집 후에도 컨텍스트 부족(NEED_MORE_CONTEXT) 또는 한도 초과 -> context_insufficient로 안전하게 스킵")
                                                    result.comment_result = CommentProcessResult(
                                                        status=CommentSubmitState.SKIPPED,
                                                        error="context_insufficient"
                                                    )
                                                    if self.state_mgr:
                                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                    return result

                                            gemini_answer = DraftService.clean_ai_response(
                                                extension_result.text,
                                                expected_request_id=request_id,
                                            )
                                            from services.comments.community_rhythm import (
                                                ResponseContaminationGate,
                                                FinalQualityGate,
                                                CommentDraftInspector,
                                            )

                                            # P0-3: Response Contamination Gate
                                            contam_check = ResponseContaminationGate.validate(extension_result.text or "")
                                            if not contam_check.is_contaminated and gemini_answer:
                                                contam_check = ResponseContaminationGate.validate(gemini_answer)

                                            if contam_check.is_contaminated:
                                                logger.log(
                                                    f"❌ [GEMINI/EXTENSION] UI 상태/생각 오염 텍스트 감지: "
                                                    f"[{contam_check.code}] pattern={contam_check.matched_pattern!r} reason={contam_check.reason}",
                                                    "ERROR",
                                                )
                                                if not getattr(self, "_contamination_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                    self._contamination_retry_done = True
                                                    logger.log("⚠️ [GEMINI/EXTENSION] UI 오염으로 인해 1회 재생성을 시도합니다.", "WARNING")
                                                    if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                        if self.stop_event and self.stop_event.is_set():
                                                            raise StopRequestedException("User stopped before Gemini retry")
                                                        result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                        if self.state_mgr:
                                                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                        return result
                                                    request_id = uuid.uuid4().hex
                                                    ai_prompt = gen_ctx.build_prompt(request_id=request_id)
                                                    gemini_answer = None
                                                    continue
                                                else:
                                                    logger.log("❌ [GEMINI/EXTENSION] UI 오염 재생성 후에도 오염 지속 -> fail-closed 스킵 처리", "ERROR")
                                                    result.comment_result = CommentProcessResult(
                                                        status=CommentSubmitState.SKIPPED if self.config.get("skip_on_comment_failure", True) else CommentSubmitState.FAILED,
                                                        error="response_ui_contamination",
                                                    )
                                                    if self.state_mgr:
                                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                    return result

                                            if gemini_answer:
                                                # Step 0: 5단계 초안 검사 (사람 말투, 사실성 검사, 중복 마무리, 설명조 등)
                                                inspection = CommentDraftInspector.inspect(
                                                    gemini_answer,
                                                    recent_comments=gen_ctx.recent_comments,
                                                    preset=preset,
                                                    excerpt=gen_ctx.excerpt,
                                                )
                                                if not inspection.passed and inspection.code != "need_more_context":
                                                    if not getattr(self, "_draft_rewrite_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                        self._draft_rewrite_done = True
                                                        logger.log(
                                                            f"⚠️ [DRAFT_INSPECT] 초안 검사 미통과 (stage {inspection.stage}: {inspection.code}) "
                                                            f"-> 1회 재작성 피드백 반영: {inspection.feedback}",
                                                            "WARNING",
                                                        )
                                                        if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                            if self.stop_event and self.stop_event.is_set():
                                                                raise StopRequestedException("User stopped before Gemini retry")
                                                            result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                            if self.state_mgr:
                                                                self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                            return result
                                                        request_id = uuid.uuid4().hex
                                                        ai_prompt = gen_ctx.build_prompt(
                                                            rewrite_feedback=inspection.feedback,
                                                            recent_repeats=inspection.matched,
                                                            recent_comments=gen_ctx.recent_comments,
                                                            request_id=request_id,
                                                        )
                                                        if self.state_mgr:
                                                            self.state_mgr.update(
                                                                current_ai_prompt=ai_prompt,
                                                                message="말투 개선 1회 재작성 중..."
                                                            )
                                                        gemini_answer = None
                                                        continue

                                                # Step 1: Body validation
                                                body_gate = FinalQualityGate.validate_final_text(
                                                    gemini_answer, preset=preset, source="gemini_body",
                                                    style_profile=getattr(gen_ctx, "style_profile", None),
                                                    style_policy=getattr(gen_ctx, "style_policy", None),
                                                    excerpt=gen_ctx.excerpt,
                                                )
                                                if not body_gate.valid:
                                                    if not getattr(self, "_quality_body_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                        self._quality_body_retry_done = True
                                                        feedback = build_quality_rewrite_feedback(body_gate)
                                                        logger.log(
                                                            f"🔄 [GEMINI/EXTENSION] 본문 품질 탈락({body_gate.code}) -> 1회 자동 재작성 피드백 반영: {feedback}",
                                                            "WARNING",
                                                        )
                                                        if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                            if self.stop_event and self.stop_event.is_set():
                                                                raise StopRequestedException("User stopped before Gemini retry")
                                                            result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                            if self.state_mgr:
                                                                self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                            return result
                                                        request_id = uuid.uuid4().hex
                                                        ai_prompt = gen_ctx.build_prompt(
                                                            rewrite_feedback=feedback,
                                                            recent_comments=gen_ctx.recent_comments,
                                                            request_id=request_id,
                                                            previous_draft=gemini_answer,
                                                        )
                                                        if self.state_mgr:
                                                            self.state_mgr.update(
                                                                current_ai_prompt=ai_prompt,
                                                                message=f"품질 보정 1회 재작성 중 ({body_gate.code})...",
                                                            )
                                                        gemini_answer = None
                                                        continue
                                                    failure = f"quality_body:{body_gate.code}"
                                                    logger.log(
                                                        f"⚠️ [GEMINI/EXTENSION] 응답 수신 완료되었으나 본문 품질 검사에서 제외됨: "
                                                        f"[{failure}] matched={body_gate.matched!r} length={body_gate.length} source=gemini_body",
                                                        "WARNING",
                                                    )
                                                    gemini_answer = None
                                                else:
                                                    # Step 2: Combined / Suffix validation
                                                    candidate_with_suffix = DraftService.compose_body_and_suffix(gemini_answer, suffix)
                                                    combined_gate = FinalQualityGate.validate_final_text(
                                                        candidate_with_suffix, preset=preset, source="gemini_suffix",
                                                        style_profile=getattr(gen_ctx, "style_profile", None),
                                                        style_policy=getattr(gen_ctx, "style_policy", None),
                                                        excerpt=gen_ctx.excerpt,
                                                    )
                                                    if not combined_gate.valid:
                                                        if not getattr(self, "_quality_body_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                            self._quality_body_retry_done = True
                                                            feedback = build_quality_rewrite_feedback(combined_gate)
                                                            logger.log(
                                                                f"🔄 [GEMINI/EXTENSION] 접미사 결합 품질 탈락({combined_gate.code}) -> 1회 자동 재작성 피드백 반영: {feedback}",
                                                                "WARNING",
                                                            )
                                                            if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                                if self.stop_event and self.stop_event.is_set():
                                                                    raise StopRequestedException("User stopped before Gemini retry")
                                                                result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                                if self.state_mgr:
                                                                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                                return result
                                                            request_id = uuid.uuid4().hex
                                                            ai_prompt = gen_ctx.build_prompt(
                                                                rewrite_feedback=feedback,
                                                                recent_comments=gen_ctx.recent_comments,
                                                                request_id=request_id,
                                                                previous_draft=candidate_with_suffix,
                                                            )
                                                            if self.state_mgr:
                                                                self.state_mgr.update(
                                                                    current_ai_prompt=ai_prompt,
                                                                    message=f"품질 보정 1회 재작성 중 ({combined_gate.code})...",
                                                                )
                                                            gemini_answer = None
                                                            continue
                                                        failure = f"quality_suffix:{combined_gate.code}"
                                                        logger.log(
                                                            f"⚠️ [GEMINI/EXTENSION] 응답 수신 완료되었으나 접미사 결합 품질 검사에서 제외됨: "
                                                            f"[{failure}] matched={combined_gate.matched!r} length={combined_gate.length} source=gemini_suffix",
                                                            "WARNING",
                                                        )
                                                        gemini_answer = None
                                                    else:
                                                        logger.log(
                                                            f"✅ [GEMINI/EXTENSION] 품질 검사 통과: "
                                                            f"length={combined_gate.length} source=gemini"
                                                        )
                                                        # P0-4: Semantic anchor selection & connection
                                                        selected_anchor = "none"
                                                        matched_food = [d for d in gen_ctx.verified_anchors if d in gemini_answer]
                                                        if matched_food:
                                                            selected_anchor = matched_food[0]
                                                        elif gen_ctx.secondary_anchors:
                                                            matched_sec = [s for s in gen_ctx.secondary_anchors if s in gemini_answer]
                                                            if matched_sec:
                                                                selected_anchor = f"secondary:{matched_sec[0]}"

                                                        # Semantic connection to food/dining context (식사 디테일 및 본문 실제 사실 연결 검증)
                                                        if selected_anchor == "none" and gen_ctx.verified_anchors:
                                                            dining_context_signals = (
                                                                "웨이팅", "대기", "오픈런", "가격", "리필", "점심", "저녁", "주문",
                                                                "반찬", "스프", "고기", "국물", "소스", "디저트", "커피"
                                                            )
                                                            excerpt_text = (gen_ctx.excerpt or "")
                                                            matched_signals = []
                                                            for sig in dining_context_signals:
                                                                if sig in gemini_answer and sig in excerpt_text:
                                                                    matched_signals.append(sig)

                                                            # '양', '밥', '줄' 등 단문자 신호는 "다양한", "줄어든" 오탐 방지를 위한 경계 및 식사 문맥 검증
                                                            short_context_checks = [
                                                                ("양", r"(?<![다모영])양(?:이|도|은|\s*많|\s*적|푸짐)", r"(?<![다모영])양(?:이|도|은|\s*많|\s*적|푸짐)"),
                                                                ("밥", r"(?<![가-힣])(?:공기)?밥(?:이|도|은|이랑|\s*무한)", r"(?<![가-힣])(?:공기)?밥"),
                                                                ("줄", r"줄\s*(?:서|대기|라인)", r"줄\s*(?:서|대기|라인)"),
                                                            ]
                                                            for label, ans_pat, exc_pat in short_context_checks:
                                                                if re.search(ans_pat, gemini_answer) and re.search(exc_pat, excerpt_text):
                                                                    matched_signals.append(label)

                                                            if matched_signals:
                                                                selected_anchor = f"dining_context:{matched_signals[0]}"

                                                        if content_focus != "GENERAL":
                                                            logger.log(f"[FOOD_COMMENT] focus={content_focus} selected_anchor={selected_anchor}")

                                                        # P0-4: Food/Cafe anchor fail-closed check
                                                        is_food_or_cafe = content_focus in ("FOOD_RESTAURANT", "FOOD_PRODUCT", "CAFE_DESSERT")
                                                        if is_food_or_cafe and gen_ctx.verified_anchors and selected_anchor == "none":
                                                            if not getattr(self, "_food_anchor_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                                self._food_anchor_retry_done = True
                                                                logger.log("⚠️ [FOOD_COMMENT] 맛집/카페 글에 검증 앵커가 누락되어 1회 재생성을 시도합니다 (anchor_missing)", "WARNING")
                                                                if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                                    if self.stop_event and self.stop_event.is_set():
                                                                        raise StopRequestedException("User stopped before Gemini retry")
                                                                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                                    if self.state_mgr:
                                                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                                    return result
                                                                request_id = uuid.uuid4().hex
                                                                anchors_hint = ", ".join(gen_ctx.verified_anchors[:2])
                                                                ai_prompt = gen_ctx.build_prompt(
                                                                    rewrite_feedback=f"핵심 소재({anchors_hint})와 직접 연결된 본문의 구체적 사실 하나에 조금 더 가벼운 블로그 이웃 말투로 반응해 주세요. 단어 자체를 억지로 넣을 필요는 없습니다.",
                                                                    request_id=request_id,
                                                                )
                                                                gemini_answer = None
                                                                continue
                                                            else:
                                                                food_anchor_fail_closed = True
                                                                logger.log(
                                                                    "⚠️ [FOOD_COMMENT] 앵커 누락 재시도 후에도 selected_anchor=none -> fail-closed (자동등록 해제)",
                                                                    "WARNING",
                                                                )

                                                        # Check if food anchors were available but Gemini only commented on secondary place anchors
                                                        if (
                                                            FoodCommentFocus.analyze(gen_ctx.title, gen_ctx.excerpt).get("has_food_details")
                                                            and not matched_food
                                                            and any(sec in gemini_answer for sec in ("주차", "위치", "인테리어", "매장", "공간", "접근성"))
                                                        ):
                                                            if not getattr(self, "_food_retry_done", False) and gen_ctx.attempt_count < gen_ctx.max_attempts:
                                                                self._food_retry_done = True
                                                                logger.log("⚠️ [FOOD_FOCUS] 음식 정보가 본문에 있음에도 장소 정보에만 반응하여 1회 재시도합니다 (food_focus_missed)", "WARNING")
                                                                if (self.stop_event and self.stop_event.is_set()) or (self.skip_event and self.skip_event.is_set()):
                                                                    if self.stop_event and self.stop_event.is_set():
                                                                        raise StopRequestedException("User stopped before Gemini retry")
                                                                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
                                                                    if self.state_mgr:
                                                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                                                    return result
                                                                request_id = uuid.uuid4().hex
                                                                ai_prompt = gen_ctx.build_prompt(
                                                                    rewrite_feedback="장소/시설 언급 대신 본문에 나온 구체적인 음식/메뉴 특징에 반응해 주세요.",
                                                                    request_id=request_id,
                                                                )
                                                                gemini_answer = None
                                                                continue
                                            else:
                                                failure = "empty_cleaned_response"
                                                logger.log("⚠️ [GEMINI/EXTENSION] 정제 후 응답 본문이 비어있음", "WARNING")
                                        else:
                                            failure = extension_result.status.value if extension_result else "timeout"
                                            failure_detail = extension_result.error if extension_result else "response_timeout"
                                            logger.log(f"[GEMINI/EXTENSION] 생성 실패: {failure} / {failure_detail}", "ERROR")
                                    else:
                                        failure = preflight.status if preflight else "bridge_not_started"
                                        logger.log(f"[GEMINI/EXTENSION] 연결 준비 안 됨: {failure}", "ERROR")

                                    if gemini_answer:
                                        break
                                    if self.stop_event and self.stop_event.is_set():
                                        raise StopRequestedException("사용자 작업 중지")

                                    if self.config.get("skip_on_comment_failure", True):
                                        logger.log(f"  ⏭️ [COMMENT] Gemini 댓글 생성 실패({failure}) -> 설정에 따라 다음 글로 자동 건너뜁니다.")
                                        result.comment_result = CommentProcessResult(
                                            status=CommentSubmitState.SKIPPED,
                                            error=f"gemini_failed:{failure}",
                                        )
                                        if self.state_mgr:
                                            self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                        return result

                                    if self.pause_event is not None:
                                        self.pause_event.set()
                                    else:
                                        result.comment_result = CommentProcessResult(
                                            status=CommentSubmitState.FAILED,
                                            error=f"gemini_failed:{failure}",
                                        )
                                        return result

                                    is_quality_pause = ("quality_" in failure)
                                    pause_reason = "gemini_quality_gate" if is_quality_pause else "gemini_generation_failure"
                                    if is_quality_pause:
                                        msg = f"Gemini 품질 검사 제외 ({failure}) - 아래 [재시도], [로컬초안], [스킵] 또는 [▶️ 작업 재개]를 누르세요"
                                    else:
                                        msg = f"Gemini 생성 실패 ({failure}) - 브라우저 확인 후 아래 [재시도], [로컬초안], [스킵]을 누르세요"
                                    if self.state_mgr:
                                        self.state_mgr.update(
                                            new_state=FeedState.PAUSED,
                                            message=msg,
                                            pause_reason=pause_reason,
                                        )
                                    while self.pause_event is not None and self.pause_event.is_set():
                                        if self.stop_event and self.stop_event.is_set():
                                            raise StopRequestedException("사용자 작업 중지")
                                        cmd = self.command_bridge.pop_command() if self.command_bridge else None
                                        if cmd and cmd.kind in (WorkerCommandType.GEMINI_SKIP_POST, WorkerCommandType.SKIP_POST):
                                            result.comment_result = CommentProcessResult(
                                                status=CommentSubmitState.SKIPPED,
                                                error=f"gemini_failed:{failure}" if failure else "user_skipped",
                                            )
                                            self.pause_event.clear()
                                            if self.state_mgr:
                                                self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True, clear_pause_reason=True)
                                            return result
                                        if cmd and cmd.kind == WorkerCommandType.GEMINI_USE_LOCAL_ONCE:
                                            use_local_requested = True
                                            self.pause_event.clear()
                                            if self.state_mgr:
                                                self.state_mgr.update(clear_pause_reason=True)
                                            break
                                        if cmd and cmd.kind == WorkerCommandType.GEMINI_RETRY:
                                            self.pause_event.clear()
                                            if self.state_mgr:
                                                self.state_mgr.update(clear_pause_reason=True)
                                            break
                                        time.sleep(0.2)
                                    if self.state_mgr:
                                        self.state_mgr.update(clear_pause_reason=True)
                                    if use_local_requested:
                                        break
                                    request_id = uuid.uuid4().hex
                                    ai_prompt = gen_ctx.build_prompt(request_id=request_id)

                            elif self.gemini_browser_mode == "existing_chrome_mac":
                                try:
                                    gemini_answer = ExistingChromeGeminiBridge.generate_comment(
                                        prompt=ai_prompt,
                                        stop_event=self.stop_event,
                                        preset=preset,
                                        request_id=request_id,
                                    )
                                except Exception as e:
                                    logger.log(f"[GEMINI/EXTERNAL] 연동 실패: {e}", "WARNING")

                            elif self.gemini_browser_mode == "managed_playwright" and self.gemini_page:
                                try:
                                    gemini_answer = GeminiWebBridge.generate_comment(
                                        page=self.gemini_page,
                                        prompt=ai_prompt,
                                        gemini_url=self.gemini_url,
                                        stop_event=self.stop_event,
                                        preset=preset,
                                        request_id=request_id,
                                    )
                                except Exception as e:
                                    logger.log(f"[GEMINI/MANAGED] 생성 실패: {e}", "WARNING")

                            try:
                                detail_page.bring_to_front()
                            except Exception:
                                pass

                        from services.comments.community_rhythm import FinalQualityGate

                        if gemini_answer:
                            logger.log(f"[GEMINI_GENERATION_SUCCESS] rid={request_id} post={post.key} nav={navigation_version}")
                            cand_composed = DraftService.compose_body_and_suffix(gemini_answer, suffix)
                            gate_res = FinalQualityGate.validate_final_text(
                                cand_composed, preset=preset, source="gemini",
                                style_profile=getattr(gen_ctx, "style_profile", None) if "gen_ctx" in locals() else None,
                                style_policy=getattr(gen_ctx, "style_policy", None) if "gen_ctx" in locals() else None,
                                excerpt=getattr(gen_ctx, "excerpt", None) if "gen_ctx" in locals() else None,
                            )
                            if gate_res.valid:
                                draft_text = cand_composed
                                draft_source_label = "Gemini 생성"
                                if self.state_mgr:
                                    self.state_mgr.update(inc_gen_success=True)
                            elif gate_res.code in FinalQualityGate.AUTO_REPAIRABLE_CODES and not getattr(self, "_gemini_auto_repair_done", False):
                                # 1-time mechanical auto-repair for minor style violations (laughter/tilde/slang)
                                self._gemini_auto_repair_done = True
                                repaired_text, repaired_gate = FinalQualityGate.auto_repair(
                                    cand_composed, gate_res, preset=preset, source="gemini"
                                )
                                if repaired_text and repaired_gate.valid:
                                    draft_text = repaired_text
                                    draft_source_label = "Gemini 생성 (자동수정)"
                                    logger.log(
                                        f"[GEMINI][AUTO_REPAIRED] code={gate_res.code} matched={gate_res.matched!r} "
                                        f"original_chars={len(cand_composed)} repaired_chars={len(repaired_text)}"
                                    )
                                    if self.state_mgr:
                                        self.state_mgr.update(inc_gen_success=True)
                                else:
                                    logger.log(
                                        f"[GEMINI] 자동 수정 시도 후에도 품질 게이트 미통과 ([{gate_res.code}] {gate_res.reason}).",
                                        "ERROR",
                                    )
                            else:
                                logger.log(f"[GEMINI] 생성된 텍스트가 품질 게이트를 통과하지 못했습니다 ([{gate_res.code}] {gate_res.reason}).", "ERROR")

                        if not draft_text and (use_local_requested or not self.gemini_web_enabled or self.config.get("allow_local_draft_on_gemini_failure", False)):
                            # 로컬 엔진은 사용자가 Gemini를 끄거나 명시적으로 허용한 경우에만 사용한다.
                            local_res = ContextualDraftEngine.generate(post.title or "", post.excerpt or "", preset=preset)
                            if local_res and local_res.body:
                                cand_composed = DraftService.compose_body_and_suffix(local_res.body, suffix)
                                gate_res = FinalQualityGate.validate_final_text(
                                    cand_composed, preset=preset, source="local",
                                    style_profile=getattr(gen_ctx, "style_profile", None),
                                    style_policy=getattr(gen_ctx, "style_policy", None),
                                    excerpt=gen_ctx.excerpt,
                                )
                                if gate_res.valid:
                                    draft_text = cand_composed
                                    detected_category = local_res.category
                                    draft_source_label = f"로컬 분석({local_res.category})"
                                    logger.log(f"💡 [DRAFT] 로컬 맞춤형 초안 생성 ({local_res.category} / '{local_res.anchor}'): \"{local_res.body}\"")

                        # Section 29: No generic fallback when all candidates fail
                        if not draft_text:
                            logger.log("  ⚠️ [COMMENT] 유효한 앵커 기반 댓글 초안을 생성하지 못했습니다. (작성 스킵)", "WARNING")
                            result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="no_valid_draft_candidate")
                            return result

                        # 에디터 주입 전 Gate 재검증
                        from services.comments.community_rhythm import ResponseContaminationGate
                        contam_pre_gate = ResponseContaminationGate.validate(draft_text)
                        if contam_pre_gate.is_contaminated:
                            logger.log(f"  ❌ [COMMENT] 에디터 주입 전 UI 오염 차단: [{contam_pre_gate.code}] {contam_pre_gate.reason}", "ERROR")
                            result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error=contam_pre_gate.code)
                            if self.config.get("skip_on_comment_failure", True):
                                logger.log("  ⏭️ [COMMENT] UI 오염 초안 주입 차단 -> 다음 글로 건너뜁니다.")
                                result.comment_result.status = CommentSubmitState.SKIPPED
                                if self.state_mgr:
                                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                            return result

                        pre_inject_gate = FinalQualityGate.validate_final_text(
                            draft_text, preset=preset, source="editor_injection",
                            style_profile=getattr(gen_ctx, "style_profile", None),
                            style_policy=getattr(gen_ctx, "style_policy", None),
                            excerpt=gen_ctx.excerpt,
                        )
                        if not pre_inject_gate.valid:
                            logger.log(f"  ❌ [COMMENT] 에디터 주입 전 품질 게이트 실패: [{pre_inject_gate.code}] {pre_inject_gate.reason}", "ERROR")
                            result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error=pre_inject_gate.code)
                            if self.config.get("skip_on_comment_failure", True):
                                logger.log("  ⏭️ [COMMENT] 초안 품질 게이트 실패 -> 다음 글로 건너뜁니다.")
                                result.comment_result.status = CommentSubmitState.SKIPPED
                                if self.state_mgr:
                                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                            return result

                        # 에디터에 주입 및 Read-back 검증
                        set_ok = CommentEditorAdapter.set_text(detail_page, draft_text)
                        if not set_ok:
                            logger.log("  ❌ [COMMENT] 에디터 초안 주입 및 Read-back 검증 실패", "ERROR")
                            result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="editor_set_text_failed")
                            if self.config.get("skip_on_comment_failure", True):
                                logger.log("  ⏭️ [COMMENT] 에디터 주입 실패 -> 다음 글로 건너뜁니다.")
                                result.comment_result.status = CommentSubmitState.SKIPPED
                                if self.state_mgr:
                                    self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                            return result
                        else:
                            logger.log(f"[COMMENT][DRAFT_READY] source={draft_source_label} chars={len(draft_text)}")
                            # 비밀댓글 설정
                            if self.secret_comment:
                                secret_chk = MobileDOMResolver.get_secret_comment_checkbox(detail_page)
                                if secret_chk and secret_chk.count() > 0:
                                    try:
                                        secret_chk.click(timeout=1000)
                                        logger.log("  🔒 [COMMENT] 비밀댓글 설정 완료")
                                    except Exception:
                                        pass
                            CommentInteractionService.install_keyboard_listener(detail_page, post_key=post.key)
                            CommentEditorAdapter.focus(detail_page)

                            cmt_res = CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)

                            auto_submit_timeout = None
                            if self.auto_comment_submit_enabled:
                                if food_anchor_fail_closed:
                                    logger.log("  ✍️ [COMMENT][AUTO_SUBMIT_DISARMED] reason=food_anchor_missing_fail_closed (verified anchors exist but selected_anchor=none)", "WARNING")
                                    msg = f"댓글 확인 대기 중 ({draft_source_label} 입력됨 / 음식 앵커 미확인으로 자동 등록 해제 / Enter=등록 / Esc=건너뛰기)"
                                else:
                                    auto_submit_timeout = random.uniform(
                                        min(self.auto_comment_delay_min, self.auto_comment_delay_max),
                                        max(self.auto_comment_delay_min, self.auto_comment_delay_max)
                                    )
                                    msg = f"댓글 자동 등록 대기 중 ({draft_source_label} 입력됨 / {auto_submit_timeout:.1f}초 후 자동 등록 / Esc=건너뛰기)"
                            else:
                                msg = f"댓글 확인 대기 중 ({draft_source_label} 입력됨 / 수정 후 Enter=등록 / Esc=건너뛰기)"

                            if self.state_mgr:
                                self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)
                            logger.log(
                                f"[COMMENT][WAITING_USER] post={post.key} source={draft_source_label} chars={len(draft_text)}"
                                + (f" auto_submit_in={auto_submit_timeout:.1f}s" if auto_submit_timeout else "")
                            )

                            while True:
                                action = CommentInteractionService.wait_for_user_action(
                                    detail_page,
                                    self.stop_event,
                                    command_bridge=self.command_bridge,
                                    preset=preset,
                                    skip_event=self.skip_event,
                                    post_key=post.key,
                                    timeout_seconds=auto_submit_timeout,
                                    state_mgr=self.state_mgr,
                                    pause_event=self.pause_event,
                                    run_control=self.run_control,
                                )

                                if action == UserAction.STOP:
                                    raise StopRequestedException("사용자 작업 중지")
                                elif action == UserAction.SKIP:
                                    logger.log(f"  ⏭️ [COMMENT] 사용자가 해당 글을 건너뛰었습니다.")
                                    cmt_res.status = CommentSubmitState.SKIPPED
                                    cmt_res.error = "user_skipped"
                                    UserLearningService.record_decision(
                                        post=post,
                                        initial_draft=draft_text,
                                        category=detected_category,
                                        anchor=(local_res.anchor if local_res else ""),
                                        evidence_span=(local_res.evidence_span if local_res else ""),
                                        source=("gemini" if draft_source_label == "Gemini 생성" else "local"),
                                        decision="skipped",
                                        rejection_reason="user_skip",
                                    )
                                    if self.state_mgr:
                                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                                    break
                                elif action in (UserAction.SUBMIT, UserAction.NATIVE_SUBMIT, UserAction.AUTO_SUBMIT):
                                    logger.log(f"[COMMENT][SUBMIT_REQUESTED] post={post.key} action={action.value}")
                                    final_text = CommentInteractionService.read_final_text(detail_page)
                                    submitted_cand = final_text or draft_text
                                    is_edited = bool(final_text and final_text.strip() != draft_text.strip())
                                    sub_source = "user_edit" if is_edited else "user_submission"

                                    origin = SubmitOrigin.USER_ENTER if action == UserAction.SUBMIT else (
                                        SubmitOrigin.NATIVE_CLICK if action == UserAction.NATIVE_SUBMIT else SubmitOrigin.AUTO_TIMER
                                    )

                                    # 등록 직전 최종 read-back 텍스트 Gate 검증 (NATIVE_CLICK은 이미 클릭되었으므로 블로커로 사용하지 않음)
                                    if origin != SubmitOrigin.NATIVE_CLICK:
                                        from services.comments.community_rhythm import ResponseContaminationGate
                                        contam_sub_gate = ResponseContaminationGate.validate(submitted_cand)
                                        if contam_sub_gate.is_contaminated:
                                            logger.log(f"  ❌ [COMMENT] 등록 직전 댓글 UI 오염 통과 실패: [{contam_sub_gate.code}] {contam_sub_gate.reason} - 등록 보류", "WARNING")
                                            CommentInteractionService.release_submit_lock(detail_page, source=origin.value)
                                            auto_submit_timeout = None
                                            msg = f"댓글 UI 오염({contam_sub_gate.code}) / 수정 후 Enter=등록 / Esc=건너뛰기"
                                            if self.state_mgr:
                                                self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)
                                            continue

                                        final_gate = FinalQualityGate.validate_final_text(
                                            submitted_cand, preset=preset, source=sub_source,
                                            style_profile=getattr(gen_ctx, "style_profile", None),
                                            style_policy=getattr(gen_ctx, "style_policy", None),
                                            excerpt=gen_ctx.excerpt,
                                        )
                                        if not final_gate.valid:
                                            logger.log(f"  ❌ [COMMENT] 등록 직전 댓글 품질 게이트 통과 실패: [{final_gate.code}] {final_gate.reason} (매칭: {final_gate.matched}) - 등록 보류", "WARNING")
                                            CommentInteractionService.release_submit_lock(detail_page, source=origin.value)
                                            auto_submit_timeout = None
                                            msg = f"댓글 품질 요건 미충족({final_gate.code}) / 수정 후 Enter=등록 / Esc=건너뛰기"
                                            if self.state_mgr:
                                                self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)
                                            logger.log(f"[COMMENT][MANUAL_SUBMIT_PRECHECK_FAILED] reason={final_gate.code} retryable=true")
                                            continue
                                    else:
                                        logger.log(
                                            "  ℹ️ [COMMENT][NATIVE_SUBMIT_VERIFY_ONLY] native click already occurred; contamination/quality gates are not used as blockers"
                                        )

                                    cmt_res.submitted_text = submitted_cand

                                    if self.state_mgr:
                                        self.state_mgr.update(new_state=FeedState.SUBMITTING, message="댓글 등록 및 검증 중...")

                                    if self.history_store and hasattr(self.history_store, "record_pre_submit"):
                                        if origin != SubmitOrigin.NATIVE_CLICK:
                                            try:
                                                self.history_store.record_pre_submit(post.key, cmt_res.submitted_text, url=post.url)
                                            except Exception as e:
                                                logger.log(f"❌ [HISTORY] pre_submit 영속 저장 실패 -> 중복 등록 방지를 위해 제출을 중단합니다: {e}", "ERROR")
                                                cmt_res.status = CommentSubmitState.FAILED
                                                cmt_res.error = "pre_submit_persistence_failed"
                                                break
                                        else:
                                            try:
                                                self.history_store.record_pre_submit(post.key, cmt_res.submitted_text, url=post.url)
                                            except Exception as e:
                                                logger.log(f"⚠️ [HISTORY] pre_submit 기록 실패 (네이티브 클릭 후, 서버 검증 계속 진행): {e}", "WARNING")

                                    if hasattr(self, "run_control") and self.run_control:
                                        self.run_control.checkpoint("before_comment_submit")
                                    outcome = CommentInteractionService.submit_and_verify(
                                        detail_page,
                                        cmt_res.submitted_text,
                                        self.stop_event,
                                        preset=preset,
                                        click=(origin != SubmitOrigin.NATIVE_CLICK),
                                        origin=origin,
                                    )
                                    if hasattr(self, "run_control") and self.run_control:
                                        self.run_control.checkpoint("after_comment_submit")
                                    status = outcome.state if hasattr(outcome, "state") else outcome
                                    cmt_res.status = status

                                    if status == CommentSubmitState.SUBMITTED:
                                        # [사용자 피드백 기록] 초안 대비 사용자 최종 수정 및 등록 댓글을 학습용 코퍼스에 저장
                                        UserLearningService.record_submission(
                                            post=post,
                                            initial_draft=draft_text,
                                            final_submitted=cmt_res.submitted_text,
                                            category=detected_category,
                                            anchor=(local_res.anchor if 'local_res' in locals() and local_res else ""),
                                            source=("gemini" if draft_source_label == "Gemini 생성" else "local"),
                                            decision_origin=("auto_submit" if action == UserAction.AUTO_SUBMIT else "user"),
                                        )
                                        if self.state_mgr:
                                            self.state_mgr.update(inc_comment=True)
                                        if self.on_comment_committed:
                                            try:
                                                self.on_comment_committed(post, cmt_res)
                                            except Exception as cp_err:
                                                logger.log(f"  ⚠️ [CHECKPOINT] Comment checkpoint 기록 실패: {cp_err}", "WARNING")
                                        break
                                    elif status in (CommentSubmitState.REVIEW_REQUIRED, CommentSubmitState.PRECLICK_BLOCKED):
                                        CommentInteractionService.release_submit_lock(detail_page, source=origin.value)
                                        auto_submit_timeout = None
                                        msg = "댓글은 등록되지 않았습니다 / 수정 후 Enter=등록 / Esc=건너뛰기"
                                        if self.state_mgr:
                                            self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)
                                        logger.log(f"[COMMENT][MANUAL_SUBMIT_PRECHECK_FAILED] reason={getattr(outcome, 'reason', status)} retryable=true")
                                        continue
                                    elif status == CommentSubmitState.SUBMISSION_UNKNOWN:
                                        if self.state_mgr:
                                            self.state_mgr.update(message="댓글 등록 여부를 확인할 수 없습니다 / 중복 방지를 위해 자동 재등록하지 않습니다")
                                        break
                                    else:
                                        break

                            result.comment_result = cmt_res

            return result
        finally:
            _cancel_early_command("scope_exit_cleanup")
