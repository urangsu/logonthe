import threading
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
        state_mgr: StateManager,
        stop_event: threading.Event,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        pause_event: Optional[threading.Event] = None,
        gemini_extension_bridge: Optional[GeminiExtensionBridge] = None,
    ):
        self.config = config
        self.history = history
        self.state_mgr = state_mgr
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.command_bridge = command_bridge
        self.gemini_extension_bridge = gemini_extension_bridge
        self.session: Optional[BrowserSession] = None
        self.pacing = PacingService(config=config, stop_event=stop_event, state_manager=state_mgr, pause_event=pause_event)

    def run(self):
        source_type_str = self.config.get("feed_source", FeedSourceType.TARGETED_SEARCH.value)
        source_type = FeedSourceType(source_type_str)
        max_items = int(self.config.get("max_feed_items", 20))
        like_enabled = bool(self.config.get("like_enabled", True))
        comment_enabled = bool(self.config.get("comment_enabled", True))
        comment_template = str(self.config.get("comment_template", ""))
        secret_comment = bool(self.config.get("secret_comment", False))
        direct_urls = self.config.get("direct_urls", [])

        ai_clipboard_enabled = bool(self.config.get("ai_clipboard_enabled", True))
        ai_context_max_chars = int(self.config.get("ai_context_max_chars", 700))
        ai_prompt_style = str(self.config.get("ai_prompt_style", "warm_short"))

        # Gemini Bridge 설정
        gemini_browser_mode = str(self.config.get("gemini_browser_mode", "extension_existing_chrome"))
        gemini_web_enabled = bool(self.config.get("gemini_web_enabled", True))
        gemini_mode = str(self.config.get("gemini_mode", "new"))
        gemini_custom_url = str(self.config.get("gemini_custom_url", "https://gemini.google.com/app"))

        gemini_url = gemini_custom_url if (gemini_mode == "custom" and gemini_custom_url) else "https://gemini.google.com/app"

        # [RUN_CONFIG] 실행 환경 스냅샷 로깅
        discovery_cats = self.config.get("discovery_categories", ["FOOD", "CAFE", "PARENTING", "LIVING", "TRAVEL", "LIFESTYLE"])
        logger.log(
            f"[RUN_CONFIG]\n"
            f"source={source_type.value}\n"
            f"categories={','.join(discovery_cats)}\n"
            f"max_items={max_items}\n"
            f"like_enabled={like_enabled}\n"
            f"comment_enabled={comment_enabled}\n"
            f"topic_filter={self.config.get('topic_filter_enabled', True)}\n"
            f"like_threshold={self.config.get('like_count_skip_threshold', 999)}\n"
            f"visitor_threshold={self.config.get('daily_visitor_skip_threshold', 10000)}\n"
            f"gemini_enabled={gemini_web_enabled}\n"
            f"gemini_mode={gemini_browser_mode}"
        )

        if comment_enabled and gemini_web_enabled and gemini_browser_mode == "extension_existing_chrome":
            if self.gemini_extension_bridge:
                preflight = self.gemini_extension_bridge.await_ready(timeout=3.0, stop_event=self.stop_event)
                if not preflight.ready:
                    self.state_mgr.update(new_state=FeedState.ERROR, message=f"Gemini 확장 연결 실패: {preflight.status}")
                    logger.log(
                        f"[GEMINI][PREFLIGHT] 피드 시작 차단: {preflight.status} "
                        f"(heartbeatAgeMs={preflight.heartbeat_age_ms}, version={preflight.extension_version}, build={preflight.content_build})",
                        "ERROR"
                    )
                    return
                else:
                    logger.log(
                        f"[GEMINI][PREFLIGHT] status=ready heartbeatAgeMs={preflight.heartbeat_age_ms} "
                        f"version={preflight.extension_version} runtimeBuild={preflight.content_build} "
                        f"protocol={preflight.protocol_version} schema={preflight.bridge_schema_version}"
                    )

        # 세션 초기화 (캐시 및 서킷 브레이커 리셋)
        BlogPopularityService.clear_cache()
        LikeCircuitBreaker.reset()

        self.state_mgr.reset(total_targets=max_items)
        self.state_mgr.update(new_state=FeedState.STARTING_BROWSER, message="브라우저 세션 시작 중...")

        self.session = BrowserSession(headless=False)
        final_close_reason = "completed"

        try:
            self.session.start()

            # Pre-flight Login Guard: 비로그인 상태 검사 (헛돌기 방지)
            is_logged_in, missing_cookies = NaverAuthGuard.check_login_cookies(self.session.context)
            if not is_logged_in:
                err_msg = "네이버 로그인이 필요합니다. [🌐 로그인 창 열기] 버튼을 눌러 로그인해 주세요."
                self.state_mgr.update(new_state=FeedState.ERROR, message=err_msg)
                logger.log("==================================================", "ERROR")
                logger.log("❌ [LOGIN_REQUIRED] 프로그램 브라우저에 네이버 로그인이 되어있지 않습니다!", "ERROR")
                logger.log("💡 [조치 방법] 메인 화면 우측 하단의 [🌐 로그인 창 열기] 버튼을 클릭하여 네이버에 로그인하신 후 다시 [피드 작업 시작]을 눌러주세요.", "WARNING")
                logger.log("🚫 비로그인 상태에서 타인의 조회수만 올려주는 헛돌기를 방지하기 위해 작업을 안전하게 중단합니다.", "WARNING")
                logger.log("==================================================", "ERROR")
                final_close_reason = "login_required"
                return

            feed_page = self.session.get_feed_page()

            # managed_playwright 모드일 때만 Playwright 내부 gemini_page 준비
            gemini_page = self.session.get_gemini_page() if (gemini_web_enabled and gemini_browser_mode == "managed_playwright") else None

            # 일 방문자 통계 가드 활성화 시 stats_page 준비
            stats_page = None
            if like_enabled and self.config.get("daily_visitor_guard_enabled", True):
                stats_page = self.session.get_stats_page()

            # 1. Feed Source 초기화
            self.state_mgr.update(new_state=FeedState.OPENING_SOURCE, message=f"피드 소스({source_type.value}) 접속 중...")

            source: FeedSource
            if source_type == FeedSourceType.NEIGHBOR:
                source = NeighborFeedSource(feed_page, max_items=max_items, stop_event=self.stop_event)
            elif source_type == FeedSourceType.TARGETED_SEARCH:
                custom_queries = self.config.get("custom_discovery_queries", [])
                posts_per_q = int(self.config.get("posts_per_query", 3))
                source = TargetedSearchFeedSource(
                    feed_page,
                    max_items=max_items,
                    stop_event=self.stop_event,
                    enabled_categories=discovery_cats,
                    custom_queries=custom_queries,
                    posts_per_query=posts_per_q
                )
            elif source_type == FeedSourceType.RECOMMENDATION:
                source = RecommendationFeedSource(feed_page, max_items=max_items, stop_event=self.stop_event)
            else:
                source = DirectUrlSource(direct_urls)

            source.open()

            # 2. PostProcessor 초기화
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
            )

            seen_candidate_keys: Set[str] = set()
            attempted_post_keys: Set[str] = set()
            completed_count = 0
            failed_count = 0
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

                    # 컴포넌트 레벨 멱등성 검사 (Like와 Comment 독립 판단)
                    is_local_liked = self.history.is_liked(post.key)
                    is_local_commented = self.history.is_comment_submitted(post.key)

                    should_like = like_enabled and not is_local_liked
                    should_comment = comment_enabled and not is_local_commented

                    if not should_like and not should_comment:
                        logger.log(f"  ⏭️ [IDEMPOTENT] 로컬 기록 상 이미 공감 및 댓글 완료된 글입니다: {post.key}")
                        continue

                    # 실제 처리 진입 대상 카운트 등록
                    attempted_post_keys.add(post.key)

                    action_plan = PostActionPlan(
                        process_like=should_like,
                        process_comment=should_comment,
                        local_like_recorded=is_local_liked,
                        local_comment_recorded=is_local_commented
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
                        completed_count += 1
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
                                completed_count += 1
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
                    if p_res.interrupted or self.stop_event.is_set():
                        final_close_reason = "user_stop"
                        break

                    p_pause = self.pacing.maybe_pause()
                    if p_pause and p_pause.interrupted or self.stop_event.is_set():
                        final_close_reason = "user_stop"
                        break

            if self.stop_event.is_set():
                self.state_mgr.update(new_state=FeedState.STOPPED, message="사용자에 의해 작업이 중지되었습니다.")
                logger.log("⏹ [ASSISTANT] 사용자 요청으로 작업 중지 완료.", "WARNING")
                final_close_reason = "user_stop"
            else:
                self.state_mgr.update(new_state=FeedState.COMPLETED, message=f"작업 완료! (총 {len(attempted_post_keys)}개 처리)")
                logger.log(f"✅ [ASSISTANT] 전체 피드 작업 완료! (총 {len(attempted_post_keys)}개 진입, 성공: {completed_count}개, 실패: {failed_count}개)")
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
            logger.log(f"💥 [ASSISTANT] 예기치 않은 오류 발생: {e}", "ERROR")
            final_close_reason = "fatal_error"
        finally:
            if self.session:
                self.session.close(reason=final_close_reason)
                self.session = None
            self.pacing.reset()
