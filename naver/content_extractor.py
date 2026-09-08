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
        ".se-main-container", ".se-viewer", ".se_component_wrap", ".se_component",
        "#postViewArea", "#postViewArea .se-viewer", ".post_ct", ".post_view",
        "div.post_content", ".view_content", ".blog2_container", "article", "div#viewTypeSelector"
    ]

    @classmethod
    def _collect_candidates(cls, page: Page, max_chars: int) -> List[Tuple[int, str]]:
        candidates: List[Tuple[int, str]] = []
        try:
            frames = list(page.frames)
        except Exception:
            frames = [page]
        for frame in frames:
            for sel in cls.CONTENT_SELECTORS:
                try:
                    locs = frame.locator(sel)
                    for idx in range(min(locs.count(), 8)):
                        el = locs.nth(idx)
                        if not el.is_visible():
                            continue
                        raw = el.inner_text().strip()
                        cleaned = cls.clean_text(raw, max_chars=max_chars)
                        if len(cleaned) < 20:
                            continue
                        score = 100 + min(len(cleaned), 1000)
                        if "댓글" in raw and len(cleaned) < 100:
                            score -= 500
                        if any(token in sel for token in ("se-main-container", "se-viewer", "postViewArea", "post_ct", "post_content")):
                            score += 250
                        if frame != page.main_frame:
                            score += 40
                        candidates.append((score, cleaned))
                except Exception:
                    continue
            try:
                body = frame.locator("body").first
                if body.count() and body.is_visible():
                    cleaned = cls.clean_text(body.inner_text().strip(), max_chars=max_chars)
                    if len(cleaned) >= 40:
                        candidates.append((120 + min(len(cleaned), 500), cleaned))
            except Exception:
                pass
        return candidates

    GREETING_PATTERNS = [
        r"^안녕하세요", r"^반갑습니다", r"^오늘도\s*좋은\s*하루",
        r"^다들\s*잘\s*지내셨나요", r"^오랜만에\s*포스팅", r"^서이추\s*환영",
        r"^영업시간\s*:", r"^주소\s*:", r"^전화번호\s*:", r"^연락처\s*:", r"^오시는\s*길\s*:",
        r"^위치\s*:", r"^주차\s*:"
    ]

    FACTUAL_PATTERNS = [
        r"\d+(?:,\d+)?\s*원", r"\d+\s*g", r"\d+\s*ml", r"\d+\s*개", r"\d+\s*인분",
        r"식감", r"맛있", r"단면", r"부드럽", r"바삭", r"달콤", r"고소", r"담백", r"촉촉", r"매콤",
        r"비주얼", r"재료", r"소스", r"조합", r"메뉴", r"주문", r"가격", r"사이즈", r"웨이팅",
        r"느껴", r"생각보다", r"직접", r"분위기", r"창가", r"햇빛", r"인테리어"
    ]

    @classmethod
    def clean_text(cls, raw_text: str, max_chars: int = 700) -> str:
        if not raw_text:
            return ""

        text = raw_text
        for pattern in cls.BOILERPLATE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

        # 문단 단위로 분리하여 보일러플레이트 제거 및 정제
        raw_paras = [p.strip() for p in re.split(r"[\r\n]+", text) if p.strip()]
        scored_paras: List[Tuple[int, int, str]] = []  # (score, original_index, text)

        for idx, p in enumerate(raw_paras):
            # 너무 짧거나 단순 기호 문단 제외
            cleaned_p = re.sub(r"\s+", " ", p).strip()
            if len(cleaned_p) < 8:
                continue

            score = 10
            # 인사말/안내문 감점
            for g_pat in cls.GREETING_PATTERNS:
                if re.search(g_pat, cleaned_p, re.IGNORECASE):
                    score -= 40
                    break

            # 구체적 사실/음식/수치/특징 가점
            for f_pat in cls.FACTUAL_PATTERNS:
                if re.search(f_pat, cleaned_p, re.IGNORECASE):
                    score += 25

            # 적절한 문장 길이 가점
            if 30 <= len(cleaned_p) <= 200:
                score += 15

            scored_paras.append((score, idx, cleaned_p))

        if not scored_paras:
            # 폴백: 단순 공백 정돈
            flat = re.sub(r"\s+", " ", text).strip()
            return flat[:max_chars].rsplit(" ", 1)[0] + "..." if len(flat) > max_chars else flat

        total_full_len = sum(len(p[2]) + 1 for p in scored_paras)
        if total_full_len <= max_chars:
            return "\n".join(p[2] for p in scored_paras)

        # max_chars 초과 시: 고득점 핵심 문단 우선 선별하되, 원문 순서(문맥 흐름)를 보존하여 조립
        sorted_by_score = sorted(scored_paras, key=lambda x: x[0], reverse=True)
        selected_indices = set()
        accumulated_len = 0

        # 1) 첫 도입 문단(배경 맥락) 1개 우선 포함 검토 (인사말이 아닌 경우)
        if scored_paras and scored_paras[0][0] >= 0:
            selected_indices.add(scored_paras[0][1])
            accumulated_len += len(scored_paras[0][2]) + 1

        # 2) 고득점 사실 문단 우선 추가
        for score, orig_idx, p_text in sorted_by_score:
            if orig_idx in selected_indices:
                continue
            if accumulated_len + len(p_text) + 1 > max_chars:
                # 앞 문단이 비어있는 경우 일부라도 수용
                if not selected_indices:
                    selected_indices.add(orig_idx)
                break
            selected_indices.add(orig_idx)
            accumulated_len += len(p_text) + 1

        # 3) 원문 등장 순서대로 정렬하여 문맥 보존 조립
        ordered_selected = [p for p in scored_paras if p[1] in selected_indices]
        ordered_selected.sort(key=lambda x: x[1])

        result_text = "\n".join(p[2] for p in ordered_selected).strip()
        if len(result_text) > max_chars:
            result_text = result_text[:max_chars].rsplit(" ", 1)[0] + "..."

        return result_text

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
            candidates = cls._collect_candidates(page, max_chars)

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
