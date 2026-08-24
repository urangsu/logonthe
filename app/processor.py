import threading
from typing import Optional
from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, FailureReason
)
from app.state import StateManager, FeedState
from naver.interaction import LikeInteractionService, CommentInteractionService
from naver.content_extractor import ContentContextExtractor
from services.draft import DraftService
from services.ai_prompt import AIPromptBuilder
from services.clipboard_bridge import ClipboardCommandBridge
from services.pacing import PacingService
from browser.session import interruptible_wait
from src.logger import logger


class StopRequestedException(Exception):
    pass


class PostProcessor:
    def __init__(
        self,
        like_enabled: bool = True,
        comment_enabled: bool = True,
        comment_template: str = "",
        fixed_suffix: str = "",
        secret_comment: bool = False,
        ai_clipboard_enabled: bool = True,
        ai_context_max_chars: int = 700,
        ai_prompt_style: str = "natural",
        pacing_service: Optional[PacingService] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        state_manager: Optional[StateManager] = None,
        stop_event: Optional[threading.Event] = None
    ):
        self.like_enabled = like_enabled
        self.comment_enabled = comment_enabled
        self.comment_template = comment_template
        self.fixed_suffix = fixed_suffix
        self.secret_comment = secret_comment
        self.ai_clipboard_enabled = ai_clipboard_enabled
        self.ai_context_max_chars = ai_context_max_chars
        self.ai_prompt_style = ai_prompt_style
        self.pacing = pacing_service
        self.command_bridge = command_bridge
        self.state_mgr = state_manager
        self.stop_event = stop_event

    def process(self, detail_page: Page, post: FeedPost) -> PostProcessResult:
        """단일 FeedPost에 대해 상세 이동 -> 맥락추출/AI프롬프트 -> 공감 확인/처리 -> 댓글 초안입력 -> 사용자 승인/등록 수행"""
        result = PostProcessResult(post=post)

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException()

        # 1. 상세 페이지 이동
        if self.state_mgr:
            self.state_mgr.update(new_state=FeedState.OPENING_POST, message=f"게시글 이동: {post.title or post.key}", post=post)

        logger.log(f"--------------------------------------------------")
        logger.log(f"[POST] 상세 이동: {post.url} ({post.title or '제목 없음'})")

        try:
            detail_page.goto(post.url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(self.stop_event, 1.0)
        except Exception as e:
            logger.log(f"[POST] 상세 페이지 로드 안내: {e}", "WARNING")

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException()

        # 2. 제목/본문 맥락 추출 및 Gemini 프롬프트 준비
        context = ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
        post.title = context.title or post.title
        post.excerpt = context.excerpt

        ai_prompt = ""
        if self.ai_clipboard_enabled:
            ai_prompt = AIPromptBuilder.build(post.title, post.excerpt, style=self.ai_prompt_style)

        if self.state_mgr:
            self.state_mgr.update(
                current_post_title=post.title or "",
                current_post_excerpt=post.excerpt or "",
                current_ai_prompt=ai_prompt,
                ai_clipboard_ready=bool(ai_prompt)
            )

        # 액션 사이 Pacing 대기
        if self.pacing:
            p_res = self.pacing.wait_action()
            if p_res.interrupted:
                raise StopRequestedException()

        # 3. 공감(하트) 처리
        if self.like_enabled:
            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.CHECKING_LIKE, message="공감 상태 확인 중...")

            like_res = LikeInteractionService.safe_process_like(detail_page, self.stop_event)
            result.like_result = like_res

            if like_res.action_taken and like_res.state_after == LikeState.LIKED:
                if self.state_mgr:
                    self.state_mgr.update(inc_like=True)

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException()

        # 액션 사이 Pacing 대기
        if self.pacing:
            p_res = self.pacing.wait_action()
            if p_res.interrupted:
                raise StopRequestedException()

        # 4. 댓글 처리 (Human-in-the-loop)
        if self.comment_enabled:
            draft_text = DraftService.generate(self.comment_template, self.fixed_suffix)

            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.FILLING_DRAFT, message="댓글 초안 입력 중...")

            cmt_res = CommentInteractionService.prepare_comment_draft(
                detail_page, draft_text, secret_comment=self.secret_comment, stop_event=self.stop_event
            )

            if cmt_res.status == CommentSubmitState.DRAFTED:
                if self.state_mgr:
                    msg = "댓글 입력 대기 중 (Enter=등록 / Shift+Enter=줄바꿈 / Esc=건너뛰기)"
                    if self.ai_clipboard_enabled:
                        msg = "댓글 확인 대기 중 (AI 프롬프트 복사/클립보드 적용 가능 / Enter=등록)"
                    self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)

                action = CommentInteractionService.wait_for_user_action(
                    detail_page, self.stop_event, command_bridge=self.command_bridge
                )

                if action == UserAction.STOP:
                    raise StopRequestedException()
                elif action == UserAction.SKIP:
                    logger.log(f"  ⏭️ [COMMENT] 사용자가 해당 글을 건너뛰었습니다 (Esc).")
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
