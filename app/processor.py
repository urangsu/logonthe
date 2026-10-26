import threading
from typing import Optional
from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, FailureReason, FeedSourceType, PostActionPlan
)
from app.state import StateManager, FeedState
from app.errors import (
    UserStopRequestedError, RecoverablePostError, PostNavigationMismatchError,
    PostDOMContractError, CommentUnavailableError
)
from naver.target_guard import TargetPostGuard
from naver.interaction import LikeInteractionService, CommentInteractionService
from naver.editor_adapter import CommentEditorAdapter
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState
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
        stop_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None
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
        self.pause_event = pause_event

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
        """
        result = PostProcessResult(post=post)

        if self.stop_event and self.stop_event.is_set():
            raise StopRequestedException("작업 중지 요청됨")

        effective_like = self.like_enabled and (action_plan.process_like if action_plan else True)
        effective_comment = self.comment_enabled and (action_plan.process_comment if action_plan else True)

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

        # 2. 공감(하트) 처리 (Re-ordered Like Pipeline: State -> Popularity Guard -> Transaction)
        if effective_like:
            # Keep the configured human pacing before the first mutating
            # action as well as between actions.  This is intentionally
            # cancellable and never bypasses the state/confidence checks below.
            if self.pacing:
                p_res = self.pacing.wait_action()
                if p_res.interrupted:
                    raise StopRequestedException("작업 중지 요청됨")
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
                # 2-2. NOT_LIKED + HIGH인 경우에만 Popularity Guard 평가
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
                    # 2-3. 실제 공감 트랜잭션 실행 (2-Path 및 UI Settle 적용)
                    tx_res = LikeTransactionService.execute_like_transaction(detail_page, self.stop_event, post=post)
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

        # 3. 댓글 처리 (댓글창 오픈 -> 서버 중복 확인 -> 초안 생성 -> 입력 -> 승인)
        if effective_comment:
            TargetPostGuard.verify(detail_page, post)

            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.OPENING_COMMENT, message="댓글 레이어 열기 및 서버 중복 확인 중...")

            # 3-1. 댓글 레이어 오픈 Polling
            open_ok, open_reason = CommentInteractionService.open_comment_layer(detail_page, self.stop_event)
            if not open_ok:
                if open_reason == "login_required":
                    logger.log("  ⚠️ [COMMENT] 로그인이 필요한 게시글입니다.", "ERROR")
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="login_required")
                elif open_reason == "comment_disabled":
                    logger.log("  ⚠️ [COMMENT] 작성자가 댓글을 닫아둔 게시글입니다 (비활성화).", "WARNING")
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="comment_disabled")
                else:
                    logger.log(f"  ⚠️ [COMMENT] 댓글 레이어 준비 타임아웃 ({open_reason}).", "WARNING")
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error=open_reason)
            else:
                # [코퍼스 자동 수집] 방문한 글의 기존 우수 댓글을 학습 코퍼스에 비식별화하여 자동 누적
                from services.visited_comment_collector import VisitedCommentCollector
                from services.user_learning_service import UserLearningService
                VisitedCommentCollector.collect_from_page(detail_page, post)

                # 3-2. 서버 사이드 중복 댓글 스캔 (Gemini 호출 전 반드시 선행)
                presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(detail_page, stop_event=self.stop_event)
                if presence.state == CommentPresenceState.PRESENT:
                    logger.log("  🛑 [COMMENT] 서버 댓글 목록에 이미 내 댓글이 존재합니다! (AI 호출/입력 0, 동기화 완료)")
                    result.comment_result = CommentProcessResult(
                        status=CommentSubmitState.SUBMITTED,
                        submitted_text=presence.comment_text or "서버 감지 기존 등록 댓글"
                    )
                elif presence.state == CommentPresenceState.UNKNOWN:
                    logger.log("  ⚠️ [COMMENT] 댓글 목록이 불완전하여 중복 방지를 위해 안전하게 작성을 스킵합니다.", "WARNING")
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED, error="server_duplicate_check_unknown")
                else:
                    # 3-3. 내 댓글이 없는 것이 확실한 경우(ABSENT HIGH)에만 초안 생성 및 주입
                    context = ContentContextExtractor.extract(detail_page, post, max_chars=self.ai_context_max_chars)
                    post.title = context.title or post.title
                    post.excerpt = context.excerpt
                    if not post.excerpt.strip():
                        # Never manufacture a plausible reply from a title
                        # when the locked/changed page yielded no body.
                        logger.log("  ⚠️ [COMMENT] 본문을 확인하지 못해 제목만으로 댓글을 만들지 않았습니다. 페이지를 확인하거나 발췌문을 보완해 주세요.", "WARNING")
                        result.comment_result = CommentProcessResult(
                            status=CommentSubmitState.FAILED,
                            error="content_extraction_insufficient"
                        )
                        if self.state_mgr:
                            self.state_mgr.update(current_post_title=post.title or "", current_post_excerpt="")
                        # Like, if any, remains recorded; this post is
                        # recoverable and the controller may continue.
                        return result
                    preset = self.config.get("comment_style_preset", "community")
                    suffix = DraftService.resolve_suffix(post.source, self.config)

                    ai_prompt = ""
                    if self.ai_clipboard_enabled or self.gemini_web_enabled:
                        ai_prompt = AIPromptBuilder.build(post.title, post.excerpt, style=self.ai_prompt_style, preset=preset)

                    if self.state_mgr:
                        self.state_mgr.update(
                            current_post_title=post.title or "",
                            current_post_excerpt=post.excerpt or "",
                            current_ai_prompt=ai_prompt,
                            ai_clipboard_ready=bool(ai_prompt)
                        )

                    draft_text = ""
                    draft_source_label = ""
                    detected_category = "UNKNOWN"

                    # [Tier 1] Gemini 자동 댓글 생성
                    gemini_answer = None
                    if self.gemini_web_enabled and ai_prompt:
                        if self.state_mgr:
                            self.state_mgr.update(message="Gemini로 자동 댓글 생성 중...")

                        if self.gemini_browser_mode == "existing_chrome_mac":
                            try:
                                gemini_answer = ExistingChromeGeminiBridge.generate_comment(
                                    prompt=ai_prompt,
                                    stop_event=self.stop_event,
                                    preset=preset
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
                                    preset=preset
                                )
                            except Exception as e:
                                logger.log(f"[GEMINI/MANAGED] 생성 실패: {e}", "WARNING")

                        try:
                            detail_page.bring_to_front()
                        except Exception:
                            pass

                    from services.comments.community_rhythm import FinalQualityGate

                    if gemini_answer:
                        cand_composed = DraftService.compose_body_and_suffix(gemini_answer, suffix)
                        gate_res = FinalQualityGate.validate_final_text(cand_composed, preset=preset, source="gemini")
                        if gate_res.valid:
                            draft_text = cand_composed
                            draft_source_label = "Gemini 생성"
                        else:
                            logger.log(f"⚠️ [GEMINI] 생성된 텍스트가 품질 게이트를 통과하지 못했습니다 ([{gate_res.code}] {gate_res.reason} / 매칭: {gate_res.matched}). 로컬 엔진으로 전환합니다.", "WARNING")

                    if not draft_text:
                        # [Tier 2] 로컬 V4.1 인간형 엔진
                        local_res = ContextualDraftEngine.generate(post.title or "", post.excerpt or "", preset=preset)
                        if local_res and local_res.body:
                            cand_composed = DraftService.compose_body_and_suffix(local_res.body, suffix)
                            gate_res = FinalQualityGate.validate_final_text(cand_composed, preset=preset, source="local")
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
                    pre_inject_gate = FinalQualityGate.validate_final_text(draft_text, preset=preset, source="editor_injection")
                    if not pre_inject_gate.valid:
                        logger.log(f"  ❌ [COMMENT] 에디터 주입 전 품질 게이트 실패: [{pre_inject_gate.code}] {pre_inject_gate.reason}", "ERROR")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error=pre_inject_gate.code)
                        return result

                    # 에디터에 주입 및 Read-back 검증
                    set_ok = CommentEditorAdapter.set_text(detail_page, draft_text)
                    if not set_ok:
                        logger.log("  ❌ [COMMENT] 에디터 초안 주입 및 Read-back 검증 실패", "ERROR")
                        result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED, error="editor_set_text_failed")
                    else:
                        # 비밀댓글 설정
                        if self.secret_comment:
                            secret_chk = MobileDOMResolver.get_secret_comment_checkbox(detail_page)
                            if secret_chk and secret_chk.count() > 0:
                                try:
                                    secret_chk.click(timeout=1000)
                                    logger.log("  🔒 [COMMENT] 비밀댓글 설정 완료")
                                except Exception:
                                    pass

                        CommentInteractionService.install_keyboard_listener(detail_page)
                        CommentEditorAdapter.focus(detail_page)

                        cmt_res = CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)

                        if self.state_mgr:
                            msg = f"댓글 확인 대기 중 ({draft_source_label} 입력됨 / 수정 후 Enter=등록 / Esc=건너뛰기)"
                            self.state_mgr.update(new_state=FeedState.WAITING_USER, message=msg)

                        action = CommentInteractionService.wait_for_user_action(
                            detail_page, self.stop_event, command_bridge=self.command_bridge, preset=preset
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
                            submitted_cand = final_text or draft_text

                            # 등록 직전 최종 read-back 텍스트 Gate 검증
                            final_gate = FinalQualityGate.validate_final_text(submitted_cand, preset=preset, source="user_submission")
                            if not final_gate.valid:
                                logger.log(f"  ❌ [COMMENT] 등록 직전 댓글 품질 게이트 통과 실패: [{final_gate.code}] {final_gate.reason} (매칭: {final_gate.matched}) - 등록 취소", "ERROR")
                                cmt_res.status = CommentSubmitState.FAILED
                                cmt_res.error = final_gate.code
                                result.comment_result = cmt_res
                                return result

                            cmt_res.submitted_text = submitted_cand

                            if self.state_mgr:
                                self.state_mgr.update(new_state=FeedState.SUBMITTING, message="댓글 등록 및 검증 중...")

                            submit_status = CommentInteractionService.submit_and_verify(
                                detail_page, cmt_res.submitted_text, self.stop_event, preset=preset
                            )
                            cmt_res.status = submit_status

                            if submit_status == CommentSubmitState.SUBMITTED:
                                # [사용자 피드백 기록] 초안 대비 사용자 최종 수정 및 등록 댓글을 학습용 코퍼스에 저장
                                UserLearningService.record_submission(
                                    post=post,
                                    initial_draft=draft_text,
                                    final_submitted=cmt_res.submitted_text,
                                    category=detected_category
                                )
                                if self.state_mgr:
                                    self.state_mgr.update(inc_comment=True)

                        result.comment_result = cmt_res

        if self.state_mgr:
            self.state_mgr.update(inc_processed=True)

        return result
