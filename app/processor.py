import threading
from typing import Optional
from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, FailureReason
)
from app.state import StateManager, FeedState
from naver.interaction import LikeInteractionService, CommentInteractionService
from services.draft import DraftService
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
        state_manager: Optional[StateManager] = None,
        stop_event: Optional[threading.Event] = None
    ):
        self.like_enabled = like_enabled
        self.comment_enabled = comment_enabled
        self.comment_template = comment_template
        self.fixed_suffix = fixed_suffix
        self.secret_comment = secret_comment
        self.state_mgr = state_manager
        self.stop_event = stop_event

    def process(self, detail_page: Page, post: FeedPost) -> PostProcessResult:
        """단일 FeedPost에 대해 상세 이동 -> 공감 확인/처리 -> 댓글 초안입력 -> 사용자 승인/등록 수행"""
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
            interruptible_wait(self.stop_event, 1.2)
        except Exception as e:
            logger.log(f"[POST] 상세 페이지 로드 안내: {e}", "WARNING")

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException()

        # 2. 공감(하트) 처리
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

        # 3. 댓글 처리 (Human-in-the-loop)
        if self.comment_enabled:
            draft_text = DraftService.generate(self.comment_template, self.fixed_suffix)

            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.FILLING_DRAFT, message="댓글 초안 입력 중...")

            cmt_res = CommentInteractionService.prepare_comment_draft(
                detail_page, draft_text, secret_comment=self.secret_comment, stop_event=self.stop_event
            )

            if cmt_res.status == CommentSubmitState.DRAFTED:
                if self.state_mgr:
                    self.state_mgr.update(
                        new_state=FeedState.WAITING_USER,
                        message="사용자 승인 대기 중 (Enter=등록 / Shift+Enter=줄바꿈 / Esc=건너뛰기)"
                    )

                action = CommentInteractionService.wait_for_user_action(detail_page, self.stop_event)

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
