import threading
from typing import Optional
from app.models import (
    FeedSourceType, FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, PostActionPlan
)
from app.state import StateManager, FeedState
from app.errors import (
    UserStopRequestedError, FatalSessionError, RecoverablePostError,
    PostNavigationMismatchError, PostDOMContractError
)
from services.helper_processor import ManualHelperProcessor
from app.processor import PostProcessor, StopRequestedException
from browser.session import BrowserSession
from naver.sources import NeighborFeedSource, RecommendationFeedSource, DirectUrlSource, TargetedSearchFeedSource, FeedSource
from naver.auth_guard import NaverAuthGuard
from services.config import ConfigService
from services.history import HistoryStore
from services.pacing import PacingService
from services.clipboard_bridge import ClipboardCommandBridge
from services.blog_popularity import BlogPopularityService
from services.like_transaction import LikeCircuitBreaker
from src.logger import logger


class FeedController:
    """
    모바일 피드 어시스턴트 메인 컨트롤러:
    - Pre-flight Login Guard: 비로그인 상태 작업 방지 (남의 조회수만 올려주는 헛돌기 차단)
    - 브라우저 세션 생명주기 관리
    - FeedSource를 통한 포스트 디스커버리
    - 기본은 ManualHelperProcessor, 설정에서 기존 PostProcessor 자동화 모드 선택 가능
    - Per-Post Error Boundary: 개별 글 오류(RecoverablePostError) 격리 및 세션 보호
    - 컴포넌트 레벨 멱등성(PostActionPlan을 통해 Like, Comment 독립 전달)
    - 수동 모드의 다음 글은 브라우저 도우미에서 사용자가 직접 선택
    - History 저장 및 UI State 업데이트
    """
    def __init__(
        self,
        config: ConfigService,
        history: HistoryStore,
        state_mgr: StateManager,
        stop_event: threading.Event,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        pause_event: Optional[threading.Event] = None
    ):
        self.config = config
        self.history = history
        self.state_mgr = state_mgr
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.command_bridge = command_bridge
        self.session: Optional[BrowserSession] = None
        self.pacing = PacingService(config=config, stop_event=stop_event, state_manager=state_mgr, pause_event=pause_event)

    def run(self):
        source_type_str = self.config.get("feed_source", FeedSourceType.NEIGHBOR.value)
        source_type = FeedSourceType(source_type_str)
        max_items = int(self.config.get("max_feed_items", 20))
        like_enabled = bool(self.config.get("like_enabled", True))
        comment_enabled = bool(self.config.get("comment_enabled", True))
        direct_urls = self.config.get("direct_urls", [])

        # 세션 초기화 (캐시 및 서킷 브레이커 리셋)
        BlogPopularityService.clear_cache()
        LikeCircuitBreaker.reset()

        self.state_mgr.reset(total_targets=max_items)
        self.state_mgr.update(new_state=FeedState.STARTING_BROWSER, message="브라우저 세션 시작 중...")

        self.session = BrowserSession(headless=False)

        try:
            self.session.start()

            # Pre-flight Login Guard: 비로그인 상태 검사 (헛돌기 방지)
            is_logged_in, missing_cookies = NaverAuthGuard.check_login_cookies(self.session.context)
            if not is_logged_in:
                err_msg = "네이버 로그인이 필요합니다. [로그인 창 열기] 버튼을 눌러 로그인해 주세요."
                self.state_mgr.update(new_state=FeedState.ERROR, message=err_msg)
                logger.log("==================================================", "ERROR")
                logger.log("[LOGIN_REQUIRED] 프로그램 브라우저에 네이버 로그인이 되어있지 않습니다!", "ERROR")
                logger.log("[조치 방법] 메인 화면 우측 하단의 [로그인 창 열기] 버튼을 클릭하여 네이버에 로그인하신 후 다시 [피드 작업 시작]을 눌러주세요.", "WARNING")
                logger.log("비로그인 상태에서 타인의 조회수만 올려주는 헛돌기를 방지하기 위해 작업을 안전하게 중단합니다.", "WARNING")
                logger.log("==================================================", "ERROR")
                return

            feed_page = self.session.get_feed_page()
            detail_page = self.session.get_detail_page()

            # 1. Feed Source 초기화
            self.state_mgr.update(new_state=FeedState.OPENING_SOURCE, message=f"피드 소스({source_type.value}) 접속 중...")
            
            source: FeedSource
            if source_type == FeedSourceType.NEIGHBOR:
                source = NeighborFeedSource(feed_page, max_items=max_items, stop_event=self.stop_event)
            elif source_type == FeedSourceType.TARGETED_SEARCH:
                discovery_cats = self.config.get("discovery_categories", ["FOOD", "CAFE", "PARENTING", "LIVING", "TRAVEL", "LIFESTYLE"])
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

            # The safe manual helper remains the default. Explicitly selecting
            # assistant_mode=false restores the prior automated Gemini/comment
            # flow, including the existing pacing service and guards.
            assistant_mode = bool(self.config.get("assistant_mode", True))
            if assistant_mode:
                processor = ManualHelperProcessor(
                    config=self.config, command_bridge=self.command_bridge,
                    state_manager=self.state_mgr, stop_event=self.stop_event,
                    pause_event=self.pause_event,
                )
            else:
                gemini_browser_mode = str(self.config.get("gemini_browser_mode", "existing_chrome_mac"))
                gemini_web_enabled = bool(self.config.get("gemini_web_enabled", True))
                gemini_mode = str(self.config.get("gemini_mode", "new"))
                gemini_custom_url = str(self.config.get("gemini_custom_url", "https://gemini.google.com/app"))
                gemini_url = gemini_custom_url if gemini_mode == "custom" and gemini_custom_url else "https://gemini.google.com/app"
                gemini_page = self.session.get_gemini_page() if gemini_web_enabled and gemini_browser_mode == "managed_playwright" else None
                stats_page = self.session.get_stats_page() if like_enabled and self.config.get("daily_visitor_guard_enabled", True) else None
                processor = PostProcessor(
                    config=self.config,
                    like_enabled=like_enabled,
                    comment_enabled=comment_enabled,
                    comment_template=str(self.config.get("comment_template", "")),
                    secret_comment=bool(self.config.get("secret_comment", False)),
                    ai_clipboard_enabled=bool(self.config.get("ai_clipboard_enabled", True)),
                    ai_context_max_chars=int(self.config.get("ai_context_max_chars", 700)),
                    ai_prompt_style=str(self.config.get("ai_prompt_style", "warm_short")),
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
                )

            processed_keys = set()
            scroll_attempts = 0

            logger.log("==================================================")
            logger.log(f"[ASSISTANT] 피드 작업 시작 (목표: 최대 {max_items}개)")

            # 3. 디스커버리 및 처리 루프
            while len(processed_keys) < max_items and not self.stop_event.is_set():
                self.state_mgr.update(new_state=FeedState.DISCOVERING, message="피드 목록에서 게시글 탐색 중...")
                discovered = source.discover_posts()

                new_posts = [p for p in discovered if p.key not in processed_keys]

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
                    if len(processed_keys) >= max_items or self.stop_event.is_set():
                        break

                    processed_keys.add(post.key)

                    # 컴포넌트 레벨 멱등성 검사 (Like와 Comment 독립 판단)
                    is_local_liked = self.history.is_liked(post.key)
                    is_local_commented = self.history.is_comment_submitted(post.key)

                    should_like = like_enabled and not is_local_liked
                    should_comment = comment_enabled and not is_local_commented

                    if not assistant_mode and not should_like and not should_comment:
                        logger.log(f"[IDEMPOTENT] 이미 공감·댓글 처리된 글을 건너뜁니다: {post.key}")
                        continue

                    # Every discovered post opens the manual helper, even when both
                    # legacy automation flags are false or local history already exists.
                    action_plan = PostActionPlan(
                        process_like=should_like,
                        process_comment=should_comment,
                        local_like_recorded=is_local_liked,
                        local_comment_recorded=is_local_commented
                    )

                    # 개별 포스트 오류 격리 (Per-Post Error Boundary)
                    try:
                        result = processor.process(detail_page, post, action_plan=action_plan)
                        self.history.record_result(result)
                    except UserStopRequestedError:
                        raise
                    except FatalSessionError:
                        raise
                    except RecoverablePostError as rpe:
                        logger.log(f"  [POST_RECOVERABLE] 글 처리 오류 격리 ({post.key}): {rpe}", "WARNING")
                        failed_res = PostProcessResult(
                            post=post,
                            like_result=LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN, error=str(rpe)),
                            comment_result=CommentProcessResult(status=CommentSubmitState.FAILED, error=str(rpe))
                        )
                        self.history.record_result(failed_res)
                        self.stop_event.set()
                        break

                    if self.stop_event.is_set():
                        break

                    # Automated mode keeps the prior per-post pacing and
                    # random pause behavior. Manual mode waits for explicit
                    # next/skip controls in the browser panel.
                    if not assistant_mode:
                        p_res = self.pacing.wait_next_post()
                        if p_res.interrupted or self.stop_event.is_set():
                            break
                        p_pause = self.pacing.maybe_pause()
                        if p_pause and p_pause.interrupted or self.stop_event.is_set():
                            break

            if self.stop_event.is_set():
                self.state_mgr.update(new_state=FeedState.STOPPED, message="작업을 중지했습니다. 수정한 초안은 이 기기에 보존됩니다.")
                logger.log("[ASSISTANT] 사용자 요청으로 작업 중지 완료.", "WARNING")
            else:
                self.state_mgr.update(new_state=FeedState.COMPLETED, message=f"작업 완료! (총 {len(processed_keys)}개 처리)")
                logger.log(f"[ASSISTANT] 전체 피드 작업 완료! (총 {len(processed_keys)}개 처리 완료)")

        except UserStopRequestedError:
            self.state_mgr.update(new_state=FeedState.STOPPED, message="작업을 중지했습니다. 수정한 초안은 이 기기에 보존됩니다.")
            logger.log("[ASSISTANT] 작업 중지 요청 수신.", "WARNING")
        except Exception as e:
            # Page/provider exception bodies can contain URLs, account data or
            # response payloads. Keep the operator-facing error actionable but
            # bounded; the detailed local log remains available for diagnosis.
            code = type(e).__name__
            self.state_mgr.update(new_state=FeedState.ERROR, message=f"작업 중단: {code}. 로그인·프로필 사용 상태를 확인하세요.")
            logger.log(f"[ASSISTANT] 작업 중 오류 발생 ({code})", "ERROR")
        finally:
            if self.session:
                self.session.close()
                self.session = None
