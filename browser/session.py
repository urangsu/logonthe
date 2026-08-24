import os
import time
import threading
from typing import Optional, Dict, Any, List
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page
from src.logger import logger

USER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile"))
LOCK_FILE = os.path.join(USER_DATA_DIR, ".profile_lock")


class ProfileLockManager:
    @staticmethod
    def is_locked(profile_dir: str) -> bool:
        lock_path = os.path.join(profile_dir, ".profile_lock")
        if not os.path.exists(lock_path):
            return False
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            pid = int(content)
            # macOS / Unix 프로세스 생존 확인
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                os.remove(lock_path)
                return False
        except Exception:
            return False

    @staticmethod
    def acquire(profile_dir: str) -> bool:
        if ProfileLockManager.is_locked(profile_dir):
            return False
        os.makedirs(profile_dir, exist_ok=True)
        lock_path = os.path.join(profile_dir, ".profile_lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True

    @staticmethod
    def release(profile_dir: str):
        lock_path = os.path.join(profile_dir, ".profile_lock")
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception:
                pass


def interruptible_wait(stop_event: Optional[threading.Event], seconds: float, step: float = 0.1) -> bool:
    """stop_event 신호를 즉각 감지하며 주어진 시간만큼 대기 (중지 요청 시 True 반환)"""
    if seconds <= 0:
        return bool(stop_event and stop_event.is_set())
    elapsed = 0.0
    while elapsed < seconds:
        if stop_event and stop_event.is_set():
            return True
        sleep_dur = min(step, seconds - elapsed)
        time.sleep(sleep_dur)
        elapsed += sleep_dur
    return bool(stop_event and stop_event.is_set())


class BrowserSession:
    """
    Playwright 브라우저 세션 생명주기 관리자
    - feed_page (피드 목록 순회)
    - detail_page (게시글 상세 및 댓글/공감 상호작용)
    - gemini_page (Gemini 웹 자동화 탭)
    - stats_page (블로그 방문자 수 및 통계 확인용 유휴 탭)
    """
    def __init__(
        self,
        headless: bool = False,
        user_data_dir: str = USER_DATA_DIR,
        viewport_width: int = 430,
        viewport_height: int = 900,
        cdp_url: Optional[str] = None
    ):
        self.headless = headless
        self.user_data_dir = os.path.abspath(user_data_dir)
        self.viewport = {"width": viewport_width, "height": viewport_height}
        self.cdp_url = cdp_url

        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.feed_page: Optional[Page] = None
        self.detail_page: Optional[Page] = None
        self.gemini_page: Optional[Page] = None
        self.stats_page: Optional[Page] = None

    def start(self) -> BrowserContext:
        if self.cdp_url:
            logger.log(f"[SESSION] 기존 실행 중인 크롬(CDP: {self.cdp_url})에 연결 중...")
            try:
                self.playwright = sync_playwright().start()
                browser = self.playwright.chromium.connect_over_cdp(self.cdp_url)
                if browser.contexts:
                    self.context = browser.contexts[0]
                else:
                    self.context = browser.new_context()

                self._init_pages_from_context()
                return self.context
            except Exception as e:
                logger.log(f"[SESSION] CDP 연결 실패, 영구 프로필 모드로 전환합니다: {e}", "WARNING")

        if not ProfileLockManager.acquire(self.user_data_dir):
            raise RuntimeError(
                f"프로필 디렉토리({self.user_data_dir})가 이미 다른 작업에서 사용 중입니다.\n"
                f"기존 브라우저 창을 닫아주시거나 락을 초기화해 주세요."
            )

        os.makedirs(self.user_data_dir, exist_ok=True)
        logger.log(f"[SESSION] 브라우저 세션 시작 중... (프로필: {self.user_data_dir})")

        try:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                viewport=self.viewport,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                args=[
                    "--disable-infobars",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
            )

            self._init_pages_from_context()
            return self.context
        except Exception as e:
            self.close()
            raise e

    def _init_pages_from_context(self):
        """컨텍스트 내에 기존 열려 있는 탭들을 지능적으로 분석하여 feed_page, gemini_page 등에 매핑"""
        if not self.context:
            return

        pages = [p for p in self.context.pages if not p.is_closed()]

        for p in pages:
            try:
                if "gemini.google.com" in (p.url or ""):
                    logger.log("[SESSION] 기존 브라우저에 열려 있는 Gemini 탭을 감지하여 연동합니다.")
                    self.gemini_page = p
                    break
            except Exception:
                pass

        remaining = [p for p in pages if p != self.gemini_page]
        if remaining:
            self.feed_page = remaining[0]
            if len(remaining) > 1:
                self.detail_page = remaining[1]
            else:
                self.detail_page = self.context.new_page()
        else:
            self.feed_page = self.context.new_page()
            self.detail_page = self.context.new_page()

    def get_feed_page(self) -> Page:
        if not self.feed_page or self.feed_page.is_closed():
            if self.context and not self.context.pages:
                self.feed_page = self.context.new_page()
            elif self.context:
                self.feed_page = self.context.pages[0]
        return self.feed_page

    def get_detail_page(self) -> Page:
        if not self.detail_page or self.detail_page.is_closed():
            if self.context:
                self.detail_page = self.context.new_page()
        return self.detail_page

    def get_gemini_page(self) -> Page:
        if self.gemini_page and not self.gemini_page.is_closed():
            return self.gemini_page

        if self.context:
            for p in self.context.pages:
                try:
                    if not p.is_closed() and "gemini.google.com" in (p.url or ""):
                        logger.log("[SESSION] 열려 있는 Gemini 탭을 발견하여 재사용합니다.")
                        self.gemini_page = p
                        return self.gemini_page
                except Exception:
                    pass

            for p in self.context.pages:
                try:
                    if not p.is_closed() and p != self.feed_page and p != self.detail_page and p != self.stats_page:
                        self.gemini_page = p
                        return self.gemini_page
                except Exception:
                    pass

            self.gemini_page = self.context.new_page()
            return self.gemini_page

        raise RuntimeError("브라우저 세션 컨텍스트가 초기화되지 않았습니다.")

    def get_stats_page(self) -> Page:
        """블로그 방문자 수 및 프로필 통계 조회 전용 탭"""
        if self.stats_page and not self.stats_page.is_closed():
            return self.stats_page

        if self.context:
            self.stats_page = self.context.new_page()
            return self.stats_page

        raise RuntimeError("브라우저 세션 컨텍스트가 초기화되지 않았습니다.")

    def close(self):
        try:
            if self.stats_page and not self.stats_page.is_closed():
                self.stats_page.close()
        except Exception:
            pass

        try:
            if self.context:
                self.context.close()
                self.context = None
        except Exception:
            pass

        try:
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
        except Exception:
            pass

        ProfileLockManager.release(self.user_data_dir)
        logger.log("[SESSION] 브라우저 세션이 정상 종료되었습니다.")
