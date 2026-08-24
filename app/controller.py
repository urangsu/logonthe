import threading
from typing import Optional, List
from app.models import FeedSourceType, FeedPost, PostProcessResult
from app.state import StateManager, FeedState
from app.processor import PostProcessor, StopRequestedException
from browser.session import BrowserSession, interruptible_wait
from naver.sources import NeighborFeedSource, RecommendationFeedSource, DirectUrlSource, FeedSource
from services.config import ConfigService
from services.history import HistoryStore
from services.pacing import PacingService
from services.clipboard_bridge import ClipboardCommandBridge
from services.blog_popularity import BlogPopularityService
from src.logger import logger


class FeedController:
    """
    모바일 피드 어시스턴트 메인 컨트롤러:
    - 브라우저 세션 생명주기 관리
    - FeedSource를 통한 포스트 디스커버리
    - PostProcessor를 통한 개별 포스트 공감/Gemini댓글 생성/승인 처리
    - PacingService를 통한 안전한 작업 간격 및 휴지 제어
    - History 저장 및 UI State 업데이트
    """
    def __init__(
        self,
        config: ConfigService,
        history: HistoryStore,
        state_mgr: StateManager,
        stop_event: threading.Event,
        command_bridge: Optional[ClipboardCommandBridge] = None
    ):
        self.config = config
        self.history = history
        self.state_mgr = state_mgr
        self.stop_event = stop_event
        self.command_bridge = command_bridge
        self.session: Optional[BrowserSession] = None
        self.pacing = PacingService(config=config, stop_event=stop_event, state_manager=state_mgr)

    def run(self):
        source_type_str = self.config.get("feed_source", FeedSourceType.NEIGHBOR.value)
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
        gemini_browser_mode = str(self.config.get("gemini_browser_mode", "existing_chrome_mac"))
        gemini_web_enabled = bool(self.config.get("gemini_web_enabled", True))
        gemini_mode = str(self.config.get("gemini_mode", "new"))
        gemini_custom_url = str(self.config.get("gemini_custom_url", "https://gemini.google.com/app/0a1545681329aa0a?hl=ko"))

        gemini_url = gemini_custom_url if (gemini_mode == "custom" and gemini_custom_url) else "https://gemini.google.com/app"

        # 세션 캐시 초기화
        BlogPopularityService.clear_cache()

        self.state_mgr.reset(total_targets=max_items)
        self.state_mgr.update(new_state=FeedState.STARTING_BROWSER, message="브라우저 세션 시작 중...")

        self.session = BrowserSession(headless=False)

        try:
            self.session.start()
            feed_page = self.session.get_feed_page()
            detail_page = self.session.get_detail_page()

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
                stop_event=self.stop_event
            )

            processed_keys = set()
            scroll_attempts = 0

            logger.log("==================================================")
            logger.log(f"🤖 [ASSISTANT] 피드 작업 시작 (목표: 최대 {max_items}개)")

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

                    # 이미 댓글 작성 완료된 글인지 확인
                    if comment_enabled and self.history.is_comment_submitted(post.key):
                        logger.log(f"  ⏭️ [HISTORY] 이미 댓글 작성 완료된 글입니다 (건너뜀): {post.key}")
                        continue

                    # 개별 포스트 처리
                    result = processor.process(detail_page, post)
                    self.history.record_result(result)

                    if self.stop_event.is_set():
                        break

                    # 4. 다음 글로 넘어가기 전 Pacing 대기 및 Random Pause
                    p_res = self.pacing.wait_next_post()
                    if p_res.interrupted or self.stop_event.is_set():
                        break

                    p_pause = self.pacing.maybe_pause()
                    if p_pause and p_pause.interrupted or self.stop_event.is_set():
                        break

            if self.stop_event.is_set():
                self.state_mgr.update(new_state=FeedState.STOPPED, message="사용자에 의해 작업이 중지되었습니다.")
                logger.log("⏹ [ASSISTANT] 사용자 요청으로 작업 중지 완료.", "WARNING")
            else:
                self.state_mgr.update(new_state=FeedState.COMPLETED, message=f"작업 완료! (총 {len(processed_keys)}개 처리)")
                logger.log(f"✅ [ASSISTANT] 전체 피드 작업 완료! (총 {len(processed_keys)}개 처리 완료)")

        except StopRequestedException:
            self.state_mgr.update(new_state=FeedState.STOPPED, message="작업 중지됨.")
            logger.log("⏹ [ASSISTANT] 작업 중지 요청 수신.", "WARNING")
        except Exception as e:
            self.state_mgr.update(new_state=FeedState.ERROR, message=f"오류 발생: {e}")
            logger.log(f"❌ [ASSISTANT] 작업 중 오류 발생: {e}", "ERROR")
        finally:
            if self.session:
                self.session.close()
                self.session = None
