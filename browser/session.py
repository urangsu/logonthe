import os
import time
import subprocess
import threading
import unicodedata
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page
from app.errors import BrowserDisconnectedError
from src.logger import logger

USER_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile"))
LOCK_FILE = os.path.join(USER_DATA_DIR, ".profile_lock")


@dataclass
class ProfileStatus:
    is_busy: bool
    live_app_pid: Optional[int] = None
    live_chromium_pid: Optional[int] = None
    stale_app_lock: bool = False
    stale_singleton_lock: bool = False


class ProfileLockManager:
    """
    브라우저 프로필 및 Chromium Singleton Lock 관리자
    - 앱 레벨 .profile_lock 및 Chromium 레벨 SingletonLock/Socket/Cookie 동시 관리
    - macOS 프로세스 테이블(ps -axo) 검색을 통한 실제 실행 중인 Chromium 탐지 (NFC/NFD 정규화 지원)
    - Live 프로세스가 없을 때만 Stale 락 안전 정리
    """

    @classmethod
    def get_live_chromium_pid(cls, profile_dir: str) -> Optional[int]:
        """지정된 user-data-dir을 물고 실행 중인 실제 Chromium 프로세스 PID 조회"""
        norm_target = unicodedata.normalize("NFC", os.path.abspath(profile_dir))
        try:
            out = subprocess.check_output(["ps", "-axo", "pid=,command="], stderr=subprocess.DEVNULL).decode("utf-8")
            for line in out.splitlines():
                line_str = line.strip()
                if not line_str:
                    continue
                parts = line_str.split(None, 1)
                if len(parts) < 2:
                    continue
                pid_str, cmd = parts[0], parts[1]
                norm_cmd = unicodedata.normalize("NFC", cmd)

                if f"--user-data-dir={norm_target}" in norm_cmd or f"--user-data-dir {norm_target}" in norm_cmd:
                    try:
                        return int(pid_str)
                    except ValueError:
                        pass
        except Exception:
            pass
        return None

    @classmethod
    def inspect(cls, profile_dir: str) -> ProfileStatus:
        lock_path = os.path.join(profile_dir, ".profile_lock")
        live_app_pid = None
        stale_app_lock = False

        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                pid = int(content)
                try:
                    os.kill(pid, 0)
                    live_app_pid = pid
                except OSError:
                    stale_app_lock = True
            except Exception:
                stale_app_lock = True

        live_chromium_pid = cls.get_live_chromium_pid(profile_dir)

        singleton_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
        has_singleton = any(os.path.lexists(os.path.join(profile_dir, f)) for f in singleton_files)
        stale_singleton = has_singleton and (live_chromium_pid is None)

        is_busy = (live_app_pid is not None) or (live_chromium_pid is not None)

        return ProfileStatus(
            is_busy=is_busy,
            live_app_pid=live_app_pid,
            live_chromium_pid=live_chromium_pid,
            stale_app_lock=stale_app_lock,
            stale_singleton_lock=stale_singleton
        )

    @classmethod
    def is_locked(cls, profile_dir: str) -> bool:
        status = cls.inspect(profile_dir)
        return status.is_busy

    @classmethod
    def acquire(cls, profile_dir: str) -> bool:
        status = cls.inspect(profile_dir)
        if status.is_busy:
            return False

        # Stale 락 안전 정리
        cls.cleanup_stale_locks(profile_dir)

        os.makedirs(profile_dir, exist_ok=True)
        lock_path = os.path.join(profile_dir, ".profile_lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True

    @classmethod
    def cleanup_stale_locks(cls, profile_dir: str) -> bool:
        """Live 프로세스가 없는 경우에만 stale 락 파일들 안전 삭제"""
        status = cls.inspect(profile_dir)
        if status.is_busy:
            return False

        # 1. 앱 레벨 락 파일 삭제
        lock_path = os.path.join(profile_dir, ".profile_lock")
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception:
                pass

        # 2. Chromium Singleton 파일 삭제
        singleton_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
        for sf in singleton_files:
            p = os.path.join(profile_dir, sf)
            if os.path.lexists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        return True

    @classmethod
    def release(cls, profile_dir: str):
        """현재 프로세스의 락 해제"""
        lock_path = os.path.join(profile_dir, ".profile_lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if int(content) == os.getpid():
                    os.remove(lock_path)
            except Exception:
                pass


def ensure_page_alive(page: Optional[Page]):
    """페이지 생존 여부 검사 (Playwright closed state를 FatalSessionError로 전환)"""
    if page is None:
        raise BrowserDisconnectedError("Target page is None")
    try:
        if hasattr(page, "is_closed") and page.is_closed() is True:
            raise BrowserDisconnectedError("Target page, context or browser has been closed")
    except Exception as e:
        if isinstance(e, BrowserDisconnectedError):
            raise


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
            status = ProfileLockManager.inspect(self.user_data_dir)
            holder = f"앱 PID {status.live_app_pid}" if status.live_app_pid else f"Chromium PID {status.live_chromium_pid}"
            raise RuntimeError(
                f"프로필 디렉토리({self.user_data_dir})가 이미 사용 중입니다 ({holder}).\n"
                f"실행 중인 브라우저 창을 닫거나 종료 후 다시 시도해 주세요."
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

        # Chromium 프로세스 및 Singleton 파일 정리 대기
        deadline = time.time() + 3.0
        while time.time() < deadline:
            live_pid = ProfileLockManager.get_live_chromium_pid(self.user_data_dir)
            if live_pid is None:
                break
            time.sleep(0.2)

        ProfileLockManager.release(self.user_data_dir)
        logger.log("[SESSION] 브라우저 세션이 정상 종료되었습니다.")
