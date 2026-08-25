from typing import Tuple, List, Optional
from playwright.sync_api import BrowserContext, Page
from src.logger import logger


class NaverAuthGuard:
    """
    네이버 로그인 세션 유효성 검증 가드 (Pre-flight Login Guard)
    - data/browser_profile 내 NID_AUT / NID_SES 쿠키 존재 여부 검사
    - 모바일 블로그 피드(FeedList.naver) 접속 시 로그인 상태 검증
    - 로그아웃 상태에서 타인 글에 무의미한 조회수만 올려주는 동작 원천 차단
    """

    @classmethod
    def check_login_cookies(cls, context: BrowserContext) -> Tuple[bool, List[str]]:
        if not context:
            return False, ["context_none"]

        try:
            cookies = context.cookies(["https://naver.com", "https://blog.naver.com", "https://nid.naver.com"])
            cookie_names = set(c.get("name") for c in cookies)

            has_nid_aut = "NID_AUT" in cookie_names
            has_nid_ses = "NID_SES" in cookie_names

            if has_nid_aut and has_nid_ses:
                return True, ["NID_AUT", "NID_SES"]

            missing = []
            if not has_nid_aut:
                missing.append("NID_AUT_missing")
            if not has_nid_ses:
                missing.append("NID_SES_missing")
            return False, missing
        except Exception as e:
            return False, [f"cookie_check_error: {e}"]

    @classmethod
    def verify_login_state(cls, page: Page) -> Tuple[bool, str]:
        """실제 모바일 블로그 피드 페이지를 통한 실시간 로그인 상태 검증"""
        if not page:
            return False, "page_none"

        try:
            # 피드 리스트 페이지 확인
            current_url = page.url or ""
            if "nidlogin.login" in current_url:
                return False, "redirected_to_nidlogin"

            # DOM 상 로그인 버튼 또는 게스트 가이드 존재 여부
            logged_out = page.evaluate("""
                () => {
                    const loginBtn = document.querySelector("a[href*='nidlogin.login'], a.btn_login, button.btn_login, .u_cbox_type_logged_out");
                    if (loginBtn) return true;
                    // 네이버 모바일 상단 바에 로그인 링크가 노출되어 있는지
                    const topLogin = document.querySelector(".gnb_btn_login, .btn_gnb_login");
                    if (topLogin) return true;
                    return false;
                }
            """)
            if logged_out:
                return False, "login_button_detected"

            return True, "logged_in"
        except Exception as e:
            return False, f"eval_error: {e}"
