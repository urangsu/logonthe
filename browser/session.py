import os
import sys
import time
import threading
from typing import Optional
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page
from src.logger import logger

USER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "user_profile"))


def interruptible_wait(stop_event: Optional[threading.Event], seconds: float, step: float = 0.05) -> bool:
    """
    지정된 초 동안 step 간격으로 stop_event를 폴링하며 대기합니다.
    사용자가 중지 요청을 한 경우 즉시 True를 반환합니다.
    """
    if stop_event and stop_event.is_set():
        return True

    elapsed = 0.0
    while elapsed < seconds:
        if stop_event and stop_event.is_set():
            return True
        sleep_time = min(step, seconds - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time

    return stop_event is not None and stop_event.is_set()


class ProfileLockManager:
    """동일한 Chrome Profile 디렉토리를 2개 이상의 브라우저 인스턴스가 동시 점유하지 못하도록 관리"""
    _locks = {}
    _mutex = threading.Lock()

    @classmethod
    def acquire(cls, profile_path: str) -> bool:
        with cls._mutex:
            abs_path = os.path.abspath(profile_path)
            if cls._locks.get(abs_path, False):
                return False
            cls._locks[abs_path] = True
            return True

    @classmethod
    def release(cls, profile_path: str):
        with cls._mutex:
            abs_path = os.path.abspath(profile_path)
            cls._locks[abs_path] = False

    @classmethod
    def is_locked(cls, profile_path: str) -> bool:
        with cls._mutex:
            return cls._locks.get(os.path.abspath(profile_path), False)


class BrowserSession:
    """
    단일 지속적 BrowserContext 기반의 세션 관리자
    - feed_page: 네이버 피드 목록 탐색 및 스크롤 전용
    - detail_page: 네이버 개별 게시글 상세 진입 및 공감/댓글 처리 전용
    - gemini_page: Google Gemini 웹 자동화 전용 탭 (기존 탭 지능형 탐색 및 재사용)
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

    def start(self) -> BrowserContext:
        # 1. CDP 원격 디버깅 연결 시도 (사용자가 실행 중인 실제 크롬에 직접 연결할 경우)
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

        # 2. 영구 프로필 모드 (Persistent Context)
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

        # 기존 탭 중 Gemini 탭이 있는지 확인
        for p in pages:
            try:
                if "gemini.google.com" in (p.url or ""):
                    logger.log("[SESSION] 기존 브라우저에 열려 있는 Gemini 탭을 감지하여 연동합니다.")
                    self.gemini_page = p
                    break
            except Exception:
                pass

        # feed_page 매핑 (Gemini 탭이 아닌 첫 번째 탭)
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
        """기존에 열려 있는 Gemini 탭을 최우선 재사용하고, 없으면 신규 탭 생성"""
        if self.gemini_page and not self.gemini_page.is_closed():
            return self.gemini_page

        if self.context:
            # 1. 컨텍스트 내 열린 탭 전체 검색
            for p in self.context.pages:
                try:
                    if not p.is_closed() and "gemini.google.com" in (p.url or ""):
                        logger.log("[SESSION] 열려 있는 Gemini 탭을 발견하여 재사용합니다.")
                        self.gemini_page = p
                        return self.gemini_page
                except Exception:
                    pass

            # 2. feed/detail이 아닌 유휴 탭이 이미 열려 있는지 검색
            for p in self.context.pages:
                try:
                    if not p.is_closed() and p != self.feed_page and p != self.detail_page:
                        self.gemini_page = p
                        return self.gemini_page
                except Exception:
                    pass

            # 3. 없으면 신규 탭 생성
            self.gemini_page = self.context.new_page()

        return self.gemini_page

    def is_connected(self) -> bool:
        if not self.context or not self.context.browser:
            return self.context is not None
        return self.context.browser.is_connected()

    def close(self):
        try:
            if self.gemini_page and not self.gemini_page.is_closed():
                self.gemini_page.close()
        except Exception:
            pass

        try:
            if self.detail_page and not self.detail_page.is_closed():
                self.detail_page.close()
        except Exception:
            pass

        try:
            if self.feed_page and not self.feed_page.is_closed():
                self.feed_page.close()
        except Exception:
            pass

        try:
            if self.context:
                self.context.close()
        except Exception:
            pass

        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

        self.feed_page = None
        self.detail_page = None
        self.gemini_page = None
        self.context = None
        self.playwright = None

        ProfileLockManager.release(self.user_data_dir)
        logger.log("[SESSION] 브라우저 세션 자원 정리 완료.")
