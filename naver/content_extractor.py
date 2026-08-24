import re
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple
from playwright.sync_api import Page, Locator
from app.models import FeedPost
from naver.resolver import MobileDOMResolver
from src.logger import logger


@dataclass
class PostContext:
    title: str = ""
    excerpt: str = ""


class ContentContextExtractor:
    """
    네이버 블로그 게시글 상세 페이지에서 제목 및 본문 핵심 텍스트를 지능적으로 추출하여
    AI 프롬프트 생성용 컨텍스트(PostContext)를 만듭니다.
    """
    BOILERPLATE_PATTERNS = [
        r"이웃추가", r"공감\s*\d*", r"댓글\s*\d*", r"공유하기", r"URL 복사", r"통계", r"본문 기타 기능",
        r"저작자 명시 필수", r"영리적 사용 불가", r"내용 변경 불가", r"태그\s*#.*", r"NAVER\s*블로그",
        r"인쇄", r"신고하기", r"블로그 앱으로 보기"
    ]

    CONTENT_SELECTORS = [
        ".se-main-container",
        ".se-viewer",
        "#postViewArea",
        ".post_ct",
        ".post_view",
        "div.post_content",
        "article",
        "div#viewTypeSelector"
    ]

    @classmethod
    def clean_text(cls, raw_text: str, max_chars: int = 700) -> str:
        if not raw_text:
            return ""

        text = raw_text
        for pattern in cls.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

        # 여러 줄바꿈 및 불필요한 공백 정리
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "..."

        return text

    @classmethod
    def extract(cls, page: Page, post: FeedPost, max_chars: int = 700) -> PostContext:
        """게시글 페이지로부터 후보 평가를 통해 정제된 제목과 본문 핵심 추출"""
        # 1. 제목 추출
        title = post.title
        if not title:
            title = MobileDOMResolver.get_post_title(page) or ""

        # 2. 본문 렌더링 대기 및 최적 후보 탐색 (최대 3.5초간 폴링)
        best_text = ""
        start_t = time.time()

        while time.time() - start_t < 3.5:
            candidates: List[Tuple[int, str]] = []

            for sel in cls.CONTENT_SELECTORS:
                try:
                    locs = page.locator(sel)
                    cnt = locs.count()
                    if cnt == 0:
                        continue

                    for idx in range(cnt):
                        el = locs.nth(idx)
                        if not el.is_visible():
                            continue

                        raw = el.inner_text().strip()
                        cleaned = cls.clean_text(raw, max_chars=max_chars)
                        length = len(cleaned)

                        if length < 20:
                            continue

                        # 점수 계산
                        score = 100 + min(length, 1000)
                        if "댓글" in raw and length < 100:
                            score -= 500
                        if ".se-main-container" in sel or ".se-viewer" in sel:
                            score += 200

                        candidates.append((score, cleaned))
                except Exception:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_text = candidates[0][1]
                if len(best_text) >= 80:
                    break

            time.sleep(0.3)

        title_res = title.strip() if title else ""
        excerpt_res = best_text.strip() if best_text else ""

        if excerpt_res:
            logger.log(f"[CONTEXT] 글 제목: '{title_res}' | 본문 추출: {len(excerpt_res)}자 ('{excerpt_res[:35]}...')")
        else:
            logger.log(f"[CONTEXT] 본문 영역 추출 부족 — 제목 기반 프롬프트로 진행: '{title_res}'", "WARNING")

        return PostContext(title=title_res, excerpt=excerpt_res)
