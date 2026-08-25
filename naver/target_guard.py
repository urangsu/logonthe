from typing import Optional
from playwright.sync_api import Page
from app.models import FeedPost
from app.errors import PostNavigationMismatchError
from naver.url_utils import parse_blog_post_url, build_post_key
from src.logger import logger


class TargetPostGuard:
    """
    네비게이션 Fail-Open 방지 가드:
    현재 Playwright Page가 실제로 열고 있는 URL의 canonical (blog_id, log_no)가
    처리하고자 하는 expected_post.key와 정확히 일치하는지 검증합니다.
    """

    @classmethod
    def verify(cls, page: Optional[Page], expected_post: FeedPost) -> None:
        """
        현재 페이지 URL을 검증하며, 불일치하거나 유효하지 않은 경우 PostNavigationMismatchError를 발생시킵니다.
        """
        if not page:
            raise PostNavigationMismatchError(
                f"Page 객체가 존재하지 않습니다. (expected: {expected_post.key})",
                post_key=expected_post.key,
                reason="page_none"
            )

        current_url = page.url or ""
        parsed = parse_blog_post_url(current_url)

        if not parsed:
            raise PostNavigationMismatchError(
                f"현재 열린 페이지가 블로그 게시글 URL이 아닙니다. URL: '{current_url}' (expected: {expected_post.key})",
                post_key=expected_post.key,
                reason="invalid_url"
            )

        blog_id, log_no = parsed
        actual_key = build_post_key(blog_id, log_no)

        if actual_key != expected_post.key:
            raise PostNavigationMismatchError(
                f"❌ [TARGET_GUARD] 대상 글 불일치 감지! 현재 페이지: '{actual_key}', 목표 글: '{expected_post.key}'. 잘못된 대상 조작을 차단합니다.",
                post_key=expected_post.key,
                reason="target_mismatch"
            )
