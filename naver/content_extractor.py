import re
from dataclasses import dataclass
from typing import Optional
from playwright.sync_api import Page
from app.models import FeedPost
from naver.resolver import MobileDOMResolver


@dataclass
class PostContext:
    title: str = ""
    excerpt: str = ""


class ContentContextExtractor:
    """
    네이버 블로그 게시글 상세 페이지에서 제목 및 본문 핵심 텍스트를 추출하여
    AI 프롬프트 생성용 컨텍스트(PostContext)를 만듭니다.
    """
    BOILERPLATE_PATTERNS = [
        r"이웃추가", r"공감\s*\d*", r"댓글\s*\d*", r"공유하기", r"URL 복사", r"통계", r"본문 기타 기능",
        r"저작자 명시 필수", r"영리적 사용 불가", r"내용 변경 불가", r"태그\s*#.*", r"NAVER\s*블로그"
    ]

    @classmethod
    def clean_text(cls, raw_text: str, max_chars: int = 700) -> str:
        if not raw_text:
            return ""

        text = raw_text
        for pattern in cls.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

        # 여러 줄바꿈 및 공백 압축
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "..."

        return text

    @classmethod
    def extract(cls, page: Page, post: FeedPost, max_chars: int = 700) -> PostContext:
        """게시글 페이지로부터 정제된 제목과 본문 일부 추출"""
        # 1. 제목 결정 (기존 FeedPost에 있으면 우선 사용, 없으면 DOM에서 추출)
        title = post.title
        if not title:
            title = MobileDOMResolver.get_post_title(page) or ""

        # 2. 본문 영역 텍스트 추출
        excerpt = ""
        try:
            content_loc = MobileDOMResolver.get_post_content_locator(page)
            if content_loc.count() > 0:
                raw_content = content_loc.inner_text().strip()
                excerpt = cls.clean_text(raw_content, max_chars=max_chars)
        except Exception:
            excerpt = ""

        return PostContext(title=title.strip() if title else "", excerpt=excerpt)
