import threading
from typing import Optional
from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, FailureReason, FeedSourceType
)
from app.state import StateManager, FeedState
from app.errors import (
    UserStopRequestedError, RecoverablePostError, PostNavigationMismatchError,
    PostDOMContractError, CommentUnavailableError
)
from naver.target_guard import TargetPostGuard
from naver.interaction import LikeInteractionService, CommentInteractionService
from naver.editor_adapter import CommentEditorAdapter
from naver.content_extractor import ContentContextExtractor
from services.draft import DraftService
from services.contextual_draft import ContextualDraftEngine
from services.like_eligibility import LikeEligibilityService, LikeEligibility
from services.like_transaction import LikeTransactionService, LikeConfidence, LikeCircuitBreaker
from services.ai_prompt import AIPromptBuilder
from services.clipboard_bridge import ClipboardCommandBridge
from services.gemini_web import GeminiWebBridge
from services.gemini_existing_chrome import ExistingChromeGeminiBridge
from services.pacing import PacingService
from browser.session import interruptible_wait
from src.logger import logger


class StopRequestedException(UserStopRequestedError):
    pass


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
        gemini_browser_mode: str = "existing_chrome_mac",
        gemini_web_enabled: bool = True,
        gemini_url: str = "https://gemini.google.com/app",
        gemini_page: Optional[Page] = None,
        stats_page: Optional[Page] = None,
        pacing_service: Optional[PacingService] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        state_manager: Optional[StateManager] = None,
        stop_event: Optional[threading.Event] = None
    ):
        self.config = config
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

    def process(self, detail_page: Page, post: FeedPost) -> PostProcessResult:
        """
        단일 FeedPost에 대해 상세 이동 -> TargetPostGuard 검증 -> 3단계 초안 생성 -> 공감 트랜잭션 -> 초안입력 -> 사용자 승인/등록 수행
        """
        result = PostProcessResult(post=post)

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        # 1. 상세 페이지 이동 및 TargetPostGuard 엄격 검증
        if self.state_mgr:
            self.state_mgr.update(new_state=FeedState.OPENING_POST, message=f"게시글 이동: {post.title or post.key}", post=post)

        logger.log(f"--------------------------------------------------")
        logger.log(f"[POST] 상세 이동: {post.url} ({post.title or '제목 없음'})")

        try:
            detail_page.goto(post.url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(self.stop_event, 1.0)
        except Exception as e:
            logger.log(f"[POST] 상세 페이지 로드 오류: {e}", "WARNING")
            raise PostNavigationMismatchError(f"페이지 로드 실패: {e}", post_key=post.key, reason="navigation_error")

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        # TargetPostGuard: 대상 글 일치 여부 확인 (Fail-Open 원천 차단)
        TargetPostGuard.verify(detail_page, post)

        # 2. 제목/본문 맥락 추출 및 3단계 초안 생성 (댓글 기능 활성 시에만)
        draft_text = ""
        draft_source_label = ""
        suffix = DraftService.resolve_suffix(post.source, self.config)

        if self.comment_enabled:
            context = ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
            post.title = context.title or post.title
            post.excerpt = context.excerpt

            ai_prompt = ""
            if self.ai_clipboard_enabled or self.gemini_web_enabled:
                ai_prompt = AIPromptBuilder.build(post.title, post.excerpt, style=self.ai_prompt_style)

            if self.state_mgr:
                self.state_mgr.update(
                    current_post_title=post.title or "",
                    current_post_excerpt=post.excerpt or "",
                    current_ai_prompt=ai_prompt,
                    ai_clipboard_ready=bool(ai_prompt)
                )

            # [Tier 1] Gemini 자동 댓글 생성 시도
            gemini_answer = None
            if self.gemini_web_enabled and ai_prompt:
                if self.state_mgr:
                    self.state_mgr.update(message="Gemini로 자동 댓글 생성 중...")

                if self.gemini_browser_mode == "existing_chrome_mac":
                    try:
                        gemini_answer = ExistingChromeGeminiBridge.generate_comment(
                            prompt=ai_prompt,
                            stop_event=self.stop_event
                        )
                    except Exception as e:
                        logger.log(f"[GEMINI/EXTERNAL] 연동 실패: {e}", "WARNING")

                elif self.gemini_browser_mode == "managed_playwright" and self.gemini_page:
                    try:
                        gemini_answer = GeminiWebBridge.generate_comment(
                            page=self.gemini_page,
                            prompt=ai_prompt,
                            gemini_url=self.gemini_url,
                            stop_event=self.stop_event
                        )
                    except Exception as e:
                        logger.log(f"[GEMINI/MANAGED] 생성 실패: {e}", "WARNING")

                try:
                    detail_page.bring_to_front()
                except Exception:
                    pass

            # 초안 결정 (Gemini -> Human-Like ContextualDraftEngine -> Spintax)
            if gemini_answer:
                draft_text = DraftService.compose_body_and_suffix(gemini_answer, suffix)
                draft_source_label = "Gemini 생성"
            else:
                # [Tier 2] 로컬 인간형 문맥 분석 엔진 (Human-Like Composer v3.1)
                local_res = ContextualDraftEngine.generate(post.title or "", post.excerpt or "")
                if local_res and local_res.body:
                    draft_text = DraftService.compose_body_and_suffix(local_res.body, suffix)
                    intent_tag = f"/{local_res.intent.value}" if local_res.intent.value != "none" else ""
                    draft_source_label = f"로컬 분석({local_res.category}{intent_tag})"
                    logger.log(f"💡 [DRAFT] 로컬 맞춤형 초안 생성 ({local_res.category}{intent_tag} / '{local_res.subject}'): \"{local_res.body}\"")
                else:
                    # [Tier 3] Spintax Fallback
                    draft_text = DraftService.generate(self.comment_template, suffix)
                    draft_source_label = "기본 템플릿"

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        # 액션 사이 Pacing 대기
        if self.pacing:
            p_res = self.pacing.wait_action()
            if p_res.interrupted:
                raise StopRequestedException("작업 중지 요청됨")

        # 3. 공감(하트) 처리 (Re-ordered Like Pipeline: State -> Popularity Guard -> Transaction)
        if self.like_enabled:
            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.CHECKING_LIKE, message="공감 상태 및 조건 확인 중...")

            # 3-1. 공감 상태 및 신뢰도 우선 판별
            like_state_res = LikeTransactionService.resolve_like_state(detail_page)

            if like_state_res.state == LikeState.LIKED:
                logger.log("  ❤️ [LIKE] 이미 공감 완료된 글입니다. (트랜잭션 생략)")
                result.like_result = LikeProcessResult(state_before=LikeState.LIKED, action_taken=False, state_after=LikeState.LIKED)
            elif like_state_res.state != LikeState.NOT_LIKED or like_state_res.confidence != LikeConfidence.HIGH:
                logger.log(f"  ⚠️ [LIKE] 공감 상태 확신도 부족(state={like_state_res.state.value}, conf={like_state_res.confidence.value})으로 취소 방지 위해 스킵", "WARNING")
                result.like_result = LikeProcessResult(state_before=like_state_res.state, action_taken=False, state_after=like_state_res.state, error="low_confidence_skip")
            else:
                # 3-2. NOT_LIKED + HIGH인 경우에만 Popularity Guard 평가
                elig = LikeEligibilityService.evaluate(
                    detail_page=detail_page,
                    stats_page=self.stats_page,
                    post=post,
                    config=self.config,
                    stop_event=self.stop_event
                )

                if not elig.eligible:
                    result.like_result = LikeProcessResult(
                        state_before=LikeState.NOT_LIKED,
                        action_taken=False,
                        state_after=LikeState.NOT_LIKED,
                        eligibility_reason=elig.reason,
                        like_count=elig.like_count,
                        daily_visitors=elig.daily_visitors
                    )
                else:
                    # 3-3. 공감 트랜잭션 실행
                    tx_res = LikeTransactionService.execute_like_transaction(detail_page, self.stop_event)
                    tx_res.like_count = elig.like_count
                    tx_res.daily_visitors = elig.daily_visitors
                    result.like_result = tx_res

                    if tx_res.action_taken and tx_res.state_after == LikeState.LIKED:
                        if self.state_mgr:
                            self.state_mgr.update(inc_like=True)

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        # 액션 사이 Pacing 대기
        if self.pacing:
            p_res = self.pacing.wait_action()
            if p_res.interrupted:
                raise StopRequestedException("작업 중지 요청됨")

        # 4. 댓글 처리 (Human-in-the-loop with CommentEditorAdapter)
        if self.comment_enabled and draft_text:
            # TargetPostGuard 재검증 (공감 처리 중 다른 글 이동 방지)
            TargetPostGuard.verify(detail_page, post)

            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.FILLING_DRAFT, message=f"댓글 초안({draft_source_label}) 입력 중...")

            cmt_res = CommentInteractionService.prepare_comment_draft(
                detail_page, draft_text, secret_comment=self.secret_comment, stop_event=self.stop_event
            )

            if cmt_res.status == CommentSubmitState.DRAFTED:
                if self.state_mgr:
                    msg = f"댓글 확인 대기 중 ({draft_source_label} 입력됨 / 수정 후 Enter=등록 / Esc=건너뛰기)"
                    self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)

                action = CommentInteractionService.wait_for_user_action(
                    detail_page, self.stop_event, command_bridge=self.command_bridge
                )

                if action == UserAction.STOP:
                    raise StopRequestedException("사용자 작업 중지")
                elif action == UserAction.SKIP:
                    logger.log(f"  ⏭️ [COMMENT] 사용자가 해당 글을 건너뛰었습니다.")
                    cmt_res.status = CommentSubmitState.SKIPPED
                    if self.state_mgr:
                        self.state_mgr.update(new_state=FeedState.SKIPPING, inc_skip=True)
                elif action == UserAction.SUBMIT:
                    final_text = CommentInteractionService.read_final_text(detail_page)
                    cmt_res.submitted_text = final_text or draft_text

                    if self.state_mgr:
                        self.state_mgr.update(new_state=FeedState.SUBMITTING, message="댓글 등록 및 검증 중...")

                    submit_status = CommentInteractionService.submit_and_verify(
                        detail_page, cmt_res.submitted_text, self.stop_event
                    )
                    cmt_res.status = submit_status

                    if submit_status == CommentSubmitState.SUBMITTED:
                        if self.state_mgr:
                            self.state_mgr.update(inc_comment=True)

            result.comment_result = cmt_res

        if self.state_mgr:
            self.state_mgr.update(inc_processed=True)

        return result
