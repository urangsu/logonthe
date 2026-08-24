import os
import random
import threading
import urllib.request
from typing import Optional, Tuple
from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page
from src.logger import logger

USER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "user_profile"))
DEBUG_PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "cdp_profile"))


def is_cdp_ready(cdp_url: str = "http://127.0.0.1:9222", timeout: float = 0.8) -> bool:
    """9222 포트의 Chrome CDP 엔드포인트(/json/version)가 준비되었는지 사전 검증"""
    try:
        url = f"{cdp_url.rstrip('/')}/json/version"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


class ProfileLockManager:
    """애플리케이션 내 동일 user_data_dir 동시 실행 방지 락 매니저"""
    _lock = threading.Lock()
    _active_profile_users = set()

    @classmethod
    def acquire(cls, profile_dir: str) -> bool:
        with cls._lock:
            if profile_dir in cls._active_profile_users:
                return False
            cls._active_profile_users.add(profile_dir)
            return True

    @classmethod
    def release(cls, profile_dir: str):
        with cls._lock:
            cls._active_profile_users.discard(profile_dir)

    @classmethod
    def is_locked(cls, profile_dir: str) -> bool:
        with cls._lock:
            return profile_dir in cls._active_profile_users


class BrowserManager:
    """
    각 Worker Thread 전용 브라우저 생명주기 관리자.
    (동일 스레드 내에서 생성 -> 사용 -> 종료 완결)
    """
    def __init__(self, user_data_dir: str = USER_DATA_DIR, headless: bool = False, use_cdp: bool = False, cdp_url: str = "http://127.0.0.1:9222"):
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.use_cdp = use_cdp
        self.cdp_url = cdp_url

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._owns_profile_lock = False

    def start(self) -> BrowserContext:
        """스레드별 Playwright 인스턴스를 시작하고 BrowserContext를 반환합니다."""
        self._playwright = sync_playwright().start()

        if self.use_cdp:
            if not is_cdp_ready(self.cdp_url):
                self.close()
                raise ConnectionRefusedError(
                    f"크롬 디버깅 포트({self.cdp_url})가 열려있지 않습니다.\n"
                    "'9222 포트로 크롬 브라우저 열기'를 먼저 실행하거나 '오토봇 전용 세션 브라우저' 모드를 사용해 주세요."
                )

            try:
                logger.log(f"이미 켜져 있는 크롬 브라우저({self.cdp_url}) 연결 중...")
                self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url, timeout=3000)
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = self._browser.new_context()

                logger.log("✅ 크롬 브라우저(포트 9222)에 성공적으로 연결되었습니다!")
                return self._context
            except Exception as e:
                logger.log(f"⚠️ 기존 크롬(포트 9222) 연결 실패: {e}", "WARNING")
                self.close()
                raise e

        # Persistent Context 모드: 프로필 단일 소유권 확인
        if not ProfileLockManager.acquire(self.user_data_dir):
            self.close()
            raise RuntimeError(
                f"프로필 디렉토리({self.user_data_dir})가 이미 다른 브라우저 작업에서 사용 중입니다.\n"
                "기존에 열려 있는 로그인 창 또는 자동화 브라우저를 먼저 닫아주세요."
            )

        self._owns_profile_lock = True
        os.makedirs(self.user_data_dir, exist_ok=True)
        logger.log(f"전용 브라우저 세션 시작 중... (프로필: {self.user_data_dir})")

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 850},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage"
                ],
                locale="ko-KR",
                timezone_id="Asia/Seoul"
            )

            init_script = "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            for page in self._context.pages:
                page.add_init_script(init_script)

            self._context.on("page", lambda p: p.add_init_script(init_script))
            return self._context

        except Exception as e:
            logger.log(f"브라우저 실행 중 오류: {e}", "ERROR")
            self.close()
            raise e

    def find_or_create_page(self, url_keyword: str = "blog.naver.com") -> Page:
        if not self._context:
            raise RuntimeError("Browser context가 초기화되지 않았습니다.")

        for p in self._context.pages:
            try:
                if url_keyword in p.url:
                    logger.log(f"🔗 이미 열려 있는 블로그 탭을 사용합니다: {p.url}")
                    p.bring_to_front()
                    return p
            except Exception:
                continue

        if self._context.pages:
            p = self._context.pages[-1]
            p.bring_to_front()
            return p

        return self._context.new_page()

    def close(self):
        """Worker Thread 내에서 자원을 완전 정리하고 프로필 락을 해제합니다."""
        try:
            if self._context and not self.use_cdp:
                self._context.close()
        except Exception:
            pass

        try:
            if self._browser and self.use_cdp:
                self._browser.close()
        except Exception:
            pass

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

        if self._owns_profile_lock:
            ProfileLockManager.release(self.user_data_dir)
            self._owns_profile_lock = False

        self._context = None
        self._browser = None
        self._playwright = None
        logger.log("브라우저 자원 정리 완료.")


def interruptible_wait(stop_event: Optional[threading.Event], seconds: float) -> bool:
    """중지 신호를 감지하는 대기 함수."""
    if stop_event is None:
        import time
        time.sleep(seconds)
        return False
    return stop_event.wait(timeout=seconds)


def random_sleep(min_sec: float = 0.0, max_sec: float = 2.0, stop_event: Optional[threading.Event] = None) -> float:
    """0 ~ 2초 사이 무작위 난수 지연 (stop_event 즉시 감지)"""
    delay = random.uniform(min_sec, max_sec)
    interruptible_wait(stop_event, delay)
    return delay
