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
    단일 지속적 BrowserContext 기반의 모바일 세션 관리자
    - feed_page: 피드 목록 탐색 및 스크롤 전용
    - detail_page: 개별 게시글 상세 진입 및 공감/댓글 처리 전용
    """
    def __init__(
        self,
        headless: bool = False,
        user_data_dir: str = USER_DATA_DIR,
        viewport_width: int = 430,
        viewport_height: int = 900
    ):
        self.headless = headless
        self.user_data_dir = os.path.abspath(user_data_dir)
        self.viewport = {"width": viewport_width, "height": viewport_height}

        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.feed_page: Optional[Page] = None
        self.detail_page: Optional[Page] = None

    def start(self) -> BrowserContext:
        if not ProfileLockManager.acquire(self.user_data_dir):
            raise RuntimeError(
                f"프로필 디렉토리({self.user_data_dir})가 이미 다른 작업에서 사용 중입니다.\n"
                f"기존 브라우저 창을 닫아주시거나 락을 초기화해 주세요."
            )

        os.makedirs(self.user_data_dir, exist_ok=True)
        logger.log(f"[SESSION] 모바일 세션 브라우저 시작 중... (프로필: {self.user_data_dir})")

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

            # 첫 번째 페이지를 feed_page로 지정
            if self.context.pages:
                self.feed_page = self.context.pages[0]
            else:
                self.feed_page = self.context.new_page()

            # 두 번째 페이지를 detail_page로 생성
            self.detail_page = self.context.new_page()

            return self.context
        except Exception as e:
            self.close()
            raise e

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

    def is_connected(self) -> bool:
        if not self.context or not self.context.browser:
            return self.context is not None
        return self.context.browser.is_connected()

    def close(self):
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
        self.context = None
        self.playwright = None

        ProfileLockManager.release(self.user_data_dir)
        logger.log("[SESSION] 브라우저 세션 자원 정리 완료.")
