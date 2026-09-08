import random
import threading
import traceback
from typing import Optional, List, Set
from app.models import (
    FeedSourceType, FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, PostActionPlan
)
from app.state import StateManager, FeedState
from app.errors import (
    UserStopRequestedError, FatalSessionError, RecoverablePostError,
    PostNavigationMismatchError, PostDOMContractError, BrowserDisconnectedError
)
from app.processor import PostProcessor, StopRequestedException
from browser.session import BrowserSession, interruptible_wait
from naver.sources import NeighborFeedSource, RecommendationFeedSource, DirectUrlSource, TargetedSearchFeedSource, FeedSource
from naver.auth_guard import NaverAuthGuard
from services.config import ConfigService
from services.history import HistoryStore
from services.pacing import PacingService
from services.clipboard_bridge import ClipboardCommandBridge
from services.blog_popularity import BlogPopularityService
from services.like_transaction import LikeCircuitBreaker
import time
from naver.comment_guard import (
    ServerCommentDuplicateGuard,
    CommentPresenceState,
    CommentPresenceResult,
)
from naver.interaction import CommentInteractionService
from naver.editor_adapter import MobileDOMResolver
from services.sampling_service import SamplingHistoryManager
from services.style_service import StylePlanService
from services.gemini_extension_bridge import GeminiExtensionBridge
from src.logger import logger


class FeedController:
    """
    모바일 피드 어시스턴트 메인 컨트롤러 (V13.3):
    - Pre-flight Login Guard: 비로그인 상태 작업 방지 (남의 조회수만 올려주는 헛돌기 차단)
    - 브라우저 세션 생명주기 및 Fatal/Recoverable 분리
    - FeedSource를 통한 포스트 디스커버리 (Card Gate 위임)
    - PostProcessor를 통한 개별 포스트 공감/Gemini댓글 생성/승인 처리
    - Detail Page Close 시 1회 재생성 재시도 및 Context Close 시 즉시 Fatal 중단
    - 멱등성 및 필터 건너뜀이 max_items 목표치를 소비하지 않도록 분리
    - Gemini 연결 사전 검증 (3초 grace 및 원인별 세분화)
    - PacingService를 통한 안전한 작업 간격 및 휴지 제어
    """
    def __init__(
        self,
        config: ConfigService,
        history: HistoryStore,
        state_mgr: Optional[StateManager] = None,
        stop_event: Optional[threading.Event] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        pause_event: Optional[threading.Event] = None,
        gemini_extension_bridge: Optional[GeminiExtensionBridge] = None,
        skip_event: Optional[threading.Event] = None,
    ):
        self.config_service = config
        if hasattr(config, "load") and callable(getattr(config, "load")):
            self.config = config.load()
        else:
            self.config = config
        self.history = history
        self.state_mgr = state_mgr or StateManager()
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self.skip_event = skip_event or threading.Event()
        self.command_bridge = command_bridge or ClipboardCommandBridge()
        self.gemini_extension_bridge = gemini_extension_bridge
        self.session: Optional[BrowserSession] = None
        self.pacing = PacingService(
            self.config,
            stop_event=self.stop_event,
            state_manager=self.state_mgr,
            pause_event=self.pause_event,
            skip_event=self.skip_event,
        )
        self.sampling_manager = SamplingHistoryManager()
        self.campaign_id = self.config.get("campaign_id") or f"camp_{int(time.time())}"
        if self.config.get("campaign_id") != self.campaign_id:
            self.config["campaign_id"] = self.campaign_id
            if hasattr(self.config_service, "set"):
                try:
                    self.config_service.set("campaign_id", self.campaign_id)
                except Exception:
                    pass
        self._thread: Optional[threading.Thread] = None
        self.like_success_count = 0
        self.comment_submitted_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.consecutive_gemini_failures = 0
        self.gemini_consecutive_failure_limit = int(self.config.get("gemini_consecutive_failure_limit", 3))

    def _handle_post_result(self, result: PostProcessResult) -> None:
        """포스트 처리 결과에 따라 통계 및 Gemini 연속 실패 회로차단기를 갱신합니다."""
        raw_err = getattr(result.comment_result, "error", "")
        cmt_err = raw_err.lower() if isinstance(raw_err, str) else ""

        is_gemini_failure = False
        status = getattr(result.comment_result, "status", None)
        if cmt_err and status in (CommentSubmitState.FAILED, CommentSubmitState.SKIPPED):
            is_gemini_failure = cmt_err.startswith("gemini_failed") or cmt_err in (
                "failed", "timeout", "publish_rejected", "response_timeout", "extension_not_ready", "context_insufficient"
            )

        if result.like_result.action_taken or result.like_result.state_after == LikeState.LIKED:
            self.like_success_count += 1
        if result.comment_result.status == CommentSubmitState.SUBMITTED:
            self.comment_submitted_count += 1
            self.consecutive_gemini_failures = 0
        elif result.comment_result.status == CommentSubmitState.SUBMISSION_UNKNOWN:
            self.state_mgr.update(inc_submission_unknown=True)
        elif result.comment_result.status == CommentSubmitState.SKIPPED:
            self.skipped_count += 1
            if not is_gemini_failure:
                self.consecutive_gemini_failures = 0

        if result.like_result.error or result.comment_result.status == CommentSubmitState.FAILED:
            self.failed_count += 1

        if is_gemini_failure:
            self.consecutive_gemini_failures += 1
            logger.log(f"  ⚠️ [CONTROLLER] Gemini 실패 카운트 증가: {self.consecutive_gemini_failures}/{self.gemini_consecutive_failure_limit} (오류: {cmt_err})")
            if self.consecutive_gemini_failures >= self.gemini_consecutive_failure_limit:
                logger.log(f"🚨 [CONTROLLER] Gemini 연결/생성이 {self.consecutive_gemini_failures}회 연속 실패하여 작업을 일시 정지합니다.", "ERROR")
                if self.pause_event:
                    self.pause_event.set()
                self.state_mgr.update(
                    new_state=FeedState.PAUSED,
                    pause_reason="gemini_circuit_breaker",
                    message="Gemini 실패 (3회 연속) - 브라우저 확인 필요"
                )

    def request_skip_current_post(self):
        """현재 처리 중인 글을 건너뛰고 다음 글로 즉시 이동"""
        self.skip_event.set()
        self.pacing.interrupt()
        if self.command_bridge:
            self.command_bridge.send_skip_post()

    def run(self):
        self._run()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        if self.pause_event:
            self.pause_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        self.pacing.interrupt()
        if self.pause_event:
            self.pause_event.clear()
        if self.session:
            self.session.close(reason="user_stop")
            self.session = None

    def recover_unconfirmed_submissions(self, page=None) -> int:
        """
        미확정(SUBMISSION_UNKNOWN) 상태로 남아있는 모든 포스트에 대해
        실제 네이버 서버 댓글 목록을 조회하여 상태를 복구(SUBMITTED 확정 또는 해제)합니다.
        """
        if not hasattr(self.history, "get_unconfirmed_posts"):
            return 0
        unconfirmed = self.history.get_unconfirmed_posts()
        if not isinstance(unconfirmed, dict) or not unconfirmed:
            return 0

        logger.log(f"🔍 [RECOVERY] 미확정(SUBMISSION_UNKNOWN) 포스트 {len(unconfirmed)}건에 대해 서버 검증 복구를 시작합니다...")
        resolved_count = 0
        allocated_page = False
        check_page = page

        try:
            if not check_page:
                if not self.session:
                    return 0
                check_page = self.session.get_detail_page()
                allocated_page = True

            for post_key, post_data in list(unconfirmed.items()):
                if self.stop_event and self.stop_event.is_set():
                    break
                url = post_data.get("url")
                if not url:
                    continue
                expected_text = (post_data.get("comment", {}).get("submitted_text") or "").strip()
                logger.log(f"  🔍 [RECOVERY] 대상 확인 중: {post_key} ({url})")
                try:
                    check_page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    open_ok, open_reason = CommentInteractionService.open_comment_layer(check_page, self.stop_event)
                    if not open_ok:
                        logger.log(f"  ⚠️ [RECOVERY] 댓글 레이어 오픈 불가({open_reason}) -> 미확정 격리 유지: {post_key}")
                        continue

                    editor_context = MobileDOMResolver.get_comment_editor_context(check_page)
                    presence_frame = editor_context["frame"] if editor_context else check_page
                    pres = ServerCommentDuplicateGuard.scan_page_for_my_comment(
                        presence_frame,
                        stop_event=self.stop_event,
                        expected_text=expected_text
                    )
                    if pres.state == CommentPresenceState.PRESENT:
                        logger.log(f"  ✅ [RECOVERY] 서버 목록에서 본인 댓글이 확인되었습니다 -> SUBMITTED로 확정: {post_key}")
                        self.history.resolve_unconfirmed_post(post_key, CommentSubmitState.SUBMITTED)
                        resolved_count += 1
                    elif pres.state == CommentPresenceState.ABSENT and pres.list_complete:
                        logger.log(f"  ℹ️ [RECOVERY] 서버 목록에 댓글이 없음이 확인되었습니다 -> 미확정 해제 및 재시도 허용: {post_key}")
                        self.history.clear_unconfirmed_post(post_key)
                        resolved_count += 1
                    else:
                        logger.log(f"  ⚠️ [RECOVERY] 댓글 상태 불완전(UNKNOWN) -> 중복 방지를 위해 미확정 격리 유지: {post_key}")
                except Exception as rec_err:
                    logger.log(f"  ⚠️ [RECOVERY] {post_key} 검증 중 예외 발생: {rec_err}", "WARNING")
        finally:
            if allocated_page and check_page:
                try:
                    check_page.close()
                except Exception:
                    pass
        return resolved_count

    def pause(self):
        if self.pause_event:
            self.pause_event.set()
        self.state_mgr.update(new_state=FeedState.PAUSED, message="작업이 일시정지되었습니다.")

    def resume(self):
        if self.pause_event:
            self.pause_event.clear()
        self.state_mgr.update(new_state=FeedState.RUNNING, message="작업을 재개합니다.")

    def _run(self):
        if hasattr(self.config_service, "load") and callable(getattr(self.config_service, "load")):
            loaded = self.config_service.load()
            self.config = loaded if isinstance(loaded, dict) else self.config_service
        else:
            self.config = self.config_service

        raw_source = self.config.get("feed_source", FeedSourceType.TARGETED_SEARCH.value) if hasattr(self.config, "get") else FeedSourceType.TARGETED_SEARCH.value
        source_type_str = str(raw_source.value if hasattr(raw_source, "value") else raw_source)
        source_type = FeedSourceType(source_type_str)
        max_items = int(self.config.get("max_feed_items", 20))
        like_enabled = bool(self.config.get("like_enabled", True))
        auto_comment_submit_enabled = bool(self.config.get("auto_comment_submit_enabled", False))
        comment_enabled = bool(self.config.get("comment_enabled", True)) or auto_comment_submit_enabled
        comment_template = str(self.config.get("comment_template", ""))
        secret_comment = bool(self.config.get("secret_comment", False))
        auto_comment_chance = float(self.config.get("auto_comment_chance", 0.60))
        auto_comment_delay_min = float(self.config.get("auto_comment_delay_min", 3.0))
        auto_comment_delay_max = float(self.config.get("auto_comment_delay_max", 6.0))
        direct_urls = self.config.get("direct_urls", [])

        ai_clipboard_enabled = bool(self.config.get("ai_clipboard_enabled", True))
        ai_context_max_chars = int(self.config.get("ai_context_max_chars", 700))
        ai_prompt_style = str(self.config.get("ai_prompt_style", "warm_short"))

        gemini_browser_mode = str(self.config.get("gemini_browser_mode", "extension_existing_chrome"))
        gemini_web_enabled = bool(self.config.get("gemini_web_enabled", True))
        gemini_mode = str(self.config.get("gemini_mode", "new"))
        gemini_custom_url = str(self.config.get("gemini_custom_url", "https://gemini.google.com/app"))

        gemini_url = gemini_custom_url if (gemini_mode == "custom" and gemini_custom_url) else "https://gemini.google.com/app"

        # [RUN_CONFIG] 실행 환경 스냅샷 로깅 (이웃 새글/직접입력일 때는 탐색 카테고리/토픽필터 n/a 표시)
        discovery_cats = self.config.get("discovery_categories", ["FOOD", "CAFE", "PARENTING", "LIVING", "TRAVEL", "LIFESTYLE"])
        is_discovery_source = source_type in (FeedSourceType.TARGETED_SEARCH, FeedSourceType.RECOMMENDATION)
        if source_type == FeedSourceType.RECOMMENDATION:
            log_cats = "맛집 (fallback=푸드)"
        elif is_discovery_source:
            log_cats = ",".join(discovery_cats)
        else:
            log_cats = "n/a (neighbor mode)"
        log_topic_filter = str(self.config.get("topic_filter_enabled", True)) if is_discovery_source else "n/a (neighbor mode)"

        logger.log(
            f"[RUN_CONFIG]\n"
            f"source={source_type.value}\n"
            f"categories={log_cats}\n"
            f"max_items={max_items}\n"
            f"like_enabled={like_enabled}\n"
            f"comment_enabled={comment_enabled}\n"
            f"auto_comment_submit={auto_comment_submit_enabled} (chance={auto_comment_chance})\n"
            f"topic_filter={log_topic_filter}\n"
            f"like_threshold={self.config.get('like_count_skip_threshold', 999)}\n"
            f"visitor_threshold={self.config.get('daily_visitor_skip_threshold', 10000)}\n"
            f"gemini_enabled={gemini_web_enabled}\n"
            f"gemini_mode={gemini_browser_mode}"
        )

        if comment_enabled and gemini_web_enabled and gemini_browser_mode == "extension_existing_chrome":
            if self.gemini_extension_bridge:
                preflight = self.gemini_extension_bridge.await_ready(timeout=6.0, stop_event=self.stop_event)
                if not preflight.ready:
                    self.state_mgr.update(new_state=FeedState.ERROR, message=f"Gemini 확장 연결 실패: {preflight.status}")
                    logger.log(f"[GEMINI][PREFLIGHT] 피드 시작 차단: {preflight.status}", "ERROR")
                    return

        BlogPopularityService.clear_cache()
        LikeCircuitBreaker.reset()

        self.state_mgr.reset(total_targets=max_items)
        self.state_mgr.update(new_state=FeedState.STARTING_BROWSER, message="브라우저 세션 시작 중...")

        self.session = BrowserSession(headless=False)
        final_close_reason = "completed"

        try:
            self.session.start()

            is_logged_in, missing_cookies = NaverAuthGuard.check_login_cookies(self.session.context)
            if not is_logged_in:
                err_msg = "네이버 로그인이 필요합니다."
                self.state_mgr.update(new_state=FeedState.ERROR, message=err_msg)
                logger.log("❌ [LOGIN_REQUIRED] 네이버 로그인이 필요합니다.", "ERROR")
                return

            unconf = self.history.get_unconfirmed_posts() if hasattr(self.history, "get_unconfirmed_posts") else None
            if comment_enabled and isinstance(unconf, dict) and unconf:
                self.state_mgr.update(message="미확정 댓글 서버 상태 복구 확인 중...")
                self.recover_unconfirmed_submissions()

            feed_page = self.session.get_feed_page()
            gemini_page = self.session.get_gemini_page() if (gemini_web_enabled and gemini_browser_mode == "managed_playwright") else None
            stats_page = self.session.get_stats_page() if (like_enabled and self.config.get("daily_visitor_guard_enabled", True)) else None

            self.state_mgr.update(new_state=FeedState.OPENING_SOURCE, message=f"피드 소스({source_type.value}) 접속 중...")

            source: FeedSource
            if source_type == FeedSourceType.NEIGHBOR:
                source = NeighborFeedSource(feed_page, max_items=max_items, stop_event=self.stop_event)
            elif source_type == FeedSourceType.TARGETED_SEARCH:
                source = TargetedSearchFeedSource(
                    feed_page,
                    max_items=max_items,
                    stop_event=self.stop_event,
                    enabled_categories=discovery_cats,
                    custom_queries=self.config.get("custom_discovery_queries", []),
                    posts_per_query=int(self.config.get("posts_per_query", 3))
                )
            elif source_type == FeedSourceType.RECOMMENDATION:
                source = RecommendationFeedSource(feed_page, max_items=max_items, stop_event=self.stop_event)
            else:
                source = DirectUrlSource(direct_urls)

            source.open()

            # 2. Post Processor 준비 (체크포인트 콜백 주입)
            processor = PostProcessor(
                config=self.config,
                like_enabled=like_enabled,
                comment_enabled=comment_enabled,
                comment_template=comment_template,
                secret_comment=secret_comment,
                ai_clipboard_enabled=ai_clipboard_enabled,
                ai_context_max_chars=ai_context_max_chars,
                ai_prompt_style=ai_prompt_style,
                gemini_browser_mode=gemini_browser_mode,
                gemini_web_enabled=gemini_web_enabled,
                gemini_url=gemini_url,
                gemini_page=gemini_page,
                stats_page=stats_page,
                pacing_service=self.pacing,
                command_bridge=self.command_bridge,
                state_manager=self.state_mgr,
                stop_event=self.stop_event,
                pause_event=self.pause_event,
                gemini_extension_bridge=self.gemini_extension_bridge,
                session=self.session,
                on_like_committed=self.history.record_like_checkpoint,
                on_comment_committed=self.history.record_comment_checkpoint,
                skip_event=self.skip_event,
                auto_comment_submit_enabled=auto_comment_submit_enabled,
                auto_comment_chance=auto_comment_chance,
                auto_comment_delay_min=auto_comment_delay_min,
                auto_comment_delay_max=auto_comment_delay_max,
                history_store=self.history,
            )

            seen_candidate_keys: Set[str] = set()
            attempted_post_keys: Set[str] = set()
            like_success_count = 0
            comment_submitted_count = 0
            skipped_count = 0
            failed_count = 0
            consecutive_gemini_failures = 0
            scroll_attempts = 0
            max_candidate_scan = max_items * 5

            logger.log("==================================================")
            logger.log(f"🤖 [ASSISTANT] 피드 작업 시작 (목표: 최대 {max_items}개)")

            # 3. 디스커버리 및 처리 루프
            while (
                len(attempted_post_keys) < max_items
                and len(seen_candidate_keys) < max_candidate_scan
                and not self.stop_event.is_set()
            ):
                self.state_mgr.update(new_state=FeedState.DISCOVERING, message="피드 목록에서 게시글 탐색 중...")
                discovered = source.discover_posts()

                new_posts = [p for p in discovered if p.key not in seen_candidate_keys]

                if not new_posts:
                    if source.is_exhausted() or scroll_attempts > 6:
                        logger.log("[ASSISTANT] 더 이상 로드할 새 게시글이 없습니다.")
                        break

                    self.state_mgr.update(new_state=FeedState.LOADING_MORE, message="피드 스크롤하여 추가 글 로드 중...")
                    loaded = source.load_more()
                    scroll_attempts += 1
                    if not loaded:
                        break
                    continue

                scroll_attempts = 0

                for post in new_posts:
                    if len(attempted_post_keys) >= max_items or self.stop_event.is_set():
                        break

                    seen_candidate_keys.add(post.key)
                    self.state_mgr.update(inc_candidate=True)

                    # 컴포넌트 레벨 멱등성 검사 (Like와 Comment 독립 판단)
                    is_local_liked = (self.history.is_liked(post.key) is True)
                    is_local_commented = (self.history.is_comment_submitted(post.key) is True)
                    is_local_unconfirmed = (self.history.is_comment_unconfirmed(post.key) is True) if hasattr(self.history, "is_comment_unconfirmed") else False

                    # 등록 결과 불명(SUBMISSION_UNKNOWN) 상태인 포스트의 재확인 및 복구 절차
                    if is_local_unconfirmed and comment_enabled:
                        logger.log(f"  🔍 [RECOVERY] 이전 실행 미확정(SUBMISSION_UNKNOWN) 포스트 감지: {post.key}. 본인 댓글 존재 여부를 서버에서 재확인합니다...")
                        try:
                            check_page = self.session.get_detail_page()
                            check_page.goto(post.url, wait_until="domcontentloaded", timeout=15000)
                            open_ok, open_reason = CommentInteractionService.open_comment_layer(check_page, self.stop_event)
                            if open_ok:
                                editor_context = MobileDOMResolver.get_comment_editor_context(check_page)
                                presence_frame = editor_context["frame"] if editor_context else check_page
                                pres = ServerCommentDuplicateGuard.scan_page_for_my_comment(presence_frame, stop_event=self.stop_event)
                                if pres.state == CommentPresenceState.PRESENT:
                                    logger.log(f"  ✅ [RECOVERY] 서버 목록에서 본인 댓글이 확인되었습니다 -> SUBMITTED로 상태 확정: {post.key}")
                                    self.history.resolve_unconfirmed_post(post.key, CommentSubmitState.SUBMITTED)
                                    is_local_commented = True
                                    is_local_unconfirmed = False
                                elif pres.state == CommentPresenceState.ABSENT and pres.list_complete:
                                    logger.log(f"  ℹ️ [RECOVERY] 서버 목록에 댓글이 확실히 없음이 확인되었습니다 -> 미확정 해제 및 재시도 허용: {post.key}")
                                    self.history.clear_unconfirmed_post(post.key)
                                    is_local_unconfirmed = False
                                else:
                                    logger.log(f"  ⚠️ [RECOVERY] 댓글 상태 판정 불가(UNKNOWN) -> 중복 방지를 위해 미확정 격리를 유지합니다: {post.key}")
                            else:
                                logger.log(f"  ⚠️ [RECOVERY] 댓글 레이어 준비 실패({open_reason}) -> 미확정 격리 유지: {post.key}")
                        except Exception as rec_err:
                            logger.log(f"  ⚠️ [RECOVERY] 미확정 상태 재확인 중 예외: {rec_err}", "WARNING")

                    should_like = like_enabled and not is_local_liked
                    should_comment = comment_enabled and not is_local_commented and not is_local_unconfirmed

                    if not should_like and not should_comment:
                        if is_local_unconfirmed:
                            logger.log(f"  🛑 [IDEMPOTENT] 등록 결과 불명(SUBMISSION_UNKNOWN) 상태의 글이므로 중복 등록 방지를 위해 댓글 작성을 건너뜁니다: {post.key}")
                        else:
                            logger.log(f"  ⏭️ [IDEMPOTENT] 로컬 기록 상 이미 공감 및 댓글 완료된 글입니다: {post.key}")
                        continue

                    # 실제 처리 진입 대상 카운트 등록
                    attempted_post_keys.add(post.key)

                    sample_selected = None
                    sample_roll = None
                    if should_comment and auto_comment_submit_enabled:
                        account_id = self.config.get("my_blog_id") or "default_user"
                        sample_selected, sample_roll = self.sampling_manager.get_or_create_sample(
                            campaign_id=self.campaign_id,
                            account_id=account_id,
                            post_key=post.key,
                            chance=auto_comment_chance,
                        )
                        if sample_selected:
                            self.state_mgr.update(inc_sampled_in=True)
                        else:
                            self.state_mgr.update(inc_sampled_out=True)

                    recent_comments = self.history.get_recent_submitted_comments(5)
                    applied_preset = self.config.get("comment_style_preset", "community")
                    style_plan = StylePlanService.select_style_plan(
                        post_key=post.key,
                        recent_comments=recent_comments,
                        preset=applied_preset,
                    )
                    logger.log(f"  🎨 [STYLE] 최종 적용 프리셋: {applied_preset} | StylePlan: {style_plan}")

                    action_plan = PostActionPlan(
                        process_like=should_like,
                        process_comment=should_comment,
                        local_like_recorded=is_local_liked,
                        local_comment_recorded=is_local_commented,
                        comment_sample_selected=sample_selected,
                        comment_sample_roll=sample_roll,
                        style_plan=style_plan,
                    )

                    # 매 글 처리마다 살아있는 detail_page 획득
                    try:
                        detail_page = self.session.get_detail_page()
                    except BrowserDisconnectedError as bde:
                        logger.log(f"💥 [CONTROLLER] 세션 종료 감지: {bde}", "ERROR")
                        final_close_reason = "fatal_error"
                        raise

                    # 개별 포스트 오류 격리 (Per-Post Error Boundary) & 단일 페이지 복구 재시도
                    try:
                        result = processor.process(detail_page, post, action_plan=action_plan)
                        self.history.record_result(result)
                        self._handle_post_result(result)
                        while self.pause_event and self.pause_event.is_set() and not self.stop_event.is_set():
                            time.sleep(0.3)
                            cmd = self.command_bridge.pop_command() if self.command_bridge else None
                            if cmd:
                                self.consecutive_gemini_failures = 0
                                self.pause_event.clear()
                                break
                    except StopRequestedException:
                        final_close_reason = "user_stop"
                        raise
                    except FatalSessionError:
                        final_close_reason = "fatal_error"
                        raise
                    except RecoverablePostError as rpe:
                        if getattr(rpe, "reason", "") == "page_closed":
                            logger.log(f"  🔄 [SESSION] 상세 페이지 재생성 후 1회 재시도 ({post.key})...")
                            try:
                                detail_page = self.session.get_detail_page()
                                result = processor.process(detail_page, post, action_plan=action_plan)
                                self.history.record_result(result)
                                if result.like_result.action_taken or result.like_result.state_after == LikeState.LIKED:
                                    like_success_count += 1
                                if result.comment_result.status == CommentSubmitState.SUBMITTED:
                                    comment_submitted_count += 1
                                elif result.comment_result.status == CommentSubmitState.SKIPPED:
                                    skipped_count += 1
                                if result.like_result.error or result.comment_result.status == CommentSubmitState.FAILED:
                                    failed_count += 1
                            except (StopRequestedException, FatalSessionError):
                                raise
                            except Exception as rpe2:
                                logger.log(f"  ⚠️ [POST_RECOVERABLE] 재시도 후 글 처리 오류 격리 ({post.key}): {rpe2}", "WARNING")
                                failed_count += 1
                                failed_res = PostProcessResult(
                                    post=post,
                                    like_result=LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN, error=str(rpe2)),
                                    comment_result=CommentProcessResult(status=CommentSubmitState.FAILED, error=str(rpe2))
                                )
                                self.history.record_result(failed_res)
                        else:
                            logger.log(f"  ⚠️ [POST_RECOVERABLE] 글 처리 오류 격리 ({post.key}): {rpe}", "WARNING")
                            failed_count += 1
                            failed_res = PostProcessResult(
                                post=post,
                                like_result=LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN, error=str(rpe)),
                                comment_result=CommentProcessResult(status=CommentSubmitState.FAILED, error=str(rpe))
                            )
                            self.history.record_result(failed_res)
                            continue

                    if self.stop_event.is_set():
                        final_close_reason = "user_stop"
                        break

                    # 4. 다음 글로 넘어가기 전 Pacing 대기 및 Random Pause
                    p_res = self.pacing.wait_next_post()
                    if p_res.stopped or (self.stop_event and self.stop_event.is_set()):
                        final_close_reason = "user_stop"
                        break
                    if p_res.skipped:
                        logger.log("  ⏭️ [PACING] 사용자가 다음 글 진입 전 대기를 건너뛰었습니다.")

                    p_pause = self.pacing.maybe_pause()
                    if p_pause and (p_pause.stopped or (self.stop_event and self.stop_event.is_set())):
                        final_close_reason = "user_stop"
                        break
                    if p_pause and p_pause.skipped:
                        logger.log("  ⏭️ [PACING] 사용자가 휴식 대기를 건너뛰었습니다.")

            if self.stop_event.is_set():
                self.state_mgr.update(new_state=FeedState.STOPPED, message="사용자에 의해 작업이 중지되었습니다.")
                logger.log("⏹ [ASSISTANT] 사용자 요청으로 작업 중지 완료.", "WARNING")
                final_close_reason = "user_stop"
            else:
                self.state_mgr.update(new_state=FeedState.COMPLETED, message=f"작업 완료! (총 {len(attempted_post_keys)}개 처리)")
                logger.log(
                    f"✅ [ASSISTANT] 전체 피드 작업 완료! (진입: {len(attempted_post_keys)}개, "
                    f"공감 성공: {like_success_count}개, 댓글 등록: {comment_submitted_count}개, "
                    f"건너뜀: {skipped_count}개, 실패: {failed_count}개)"
                )
                final_close_reason = "completed"

        except StopRequestedException:
            self.state_mgr.update(new_state=FeedState.STOPPED, message="사용자에 의해 작업이 중지되었습니다.")
            logger.log("⏹ [ASSISTANT] 작업 중지 요청 처리 완료.", "WARNING")
            final_close_reason = "user_stop"
        except UserStopRequestedError:
            self.state_mgr.update(new_state=FeedState.STOPPED, message="사용자에 의해 작업이 중지되었습니다.")
            logger.log("⏹ [ASSISTANT] 작업 중지 완료.", "WARNING")
            final_close_reason = "user_stop"
        except FatalSessionError as fse:
            self.state_mgr.update(new_state=FeedState.ERROR, message=f"세션 오류: {fse}")
            logger.log(f"💥 [ASSISTANT] 치명적 세션 오류로 즉시 중단: {fse}", "ERROR")
            final_close_reason = "fatal_error"
        except Exception as e:
            self.state_mgr.update(new_state=FeedState.ERROR, message=f"예기치 않은 오류: {e}")
            logger.log(f"💥 [ASSISTANT] 예기치 않은 오류 발생: {e}\n{traceback.format_exc()}", "ERROR")
            final_close_reason = "fatal_error"
        finally:
            if self.session:
                self.session.close(reason=final_close_reason)
                self.session = None
            self.pacing.reset()


BotController = FeedController
