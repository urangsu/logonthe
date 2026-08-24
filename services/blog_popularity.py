import re
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any
from playwright.sync_api import Page
from naver.count_parser import parse_compact_count
from src.logger import logger


@dataclass
class DailyVisitorResult:
    value: Optional[int] = None
    raw_text: Optional[str] = None
    source: Optional[str] = None
    confidence: str = "unknown"  # "high", "medium", "unknown"
    error: Optional[str] = None


class BlogPopularityService:
    """
    네이버 블로그의 당일 일 방문자 수(Today Visitors)를 안전하게 조회하고 세션 캐싱합니다.
    (누적/전체 방문자와 엄격히 구분)
    """
    _cache: Dict[str, DailyVisitorResult] = {}

    @classmethod
    def clear_cache(cls):
        cls._cache.clear()

    @classmethod
    def get_daily_visitors(
        cls,
        stats_page: Optional[Page],
        blog_id: str,
        stop_event: Optional[threading.Event] = None
    ) -> DailyVisitorResult:
        if not blog_id:
            return DailyVisitorResult(error="empty_blog_id", confidence="unknown")

        # 1. 세션 인메모리 캐시 확인
        if blog_id in cls._cache:
            cached = cls._cache[blog_id]
            logger.log(f"[POPULARITY] 캐시된 블로그 방문자 조회: {blog_id} -> {cached.value if cached.value is not None else '확인불가'}")
            return cached

        if not stats_page:
            return DailyVisitorResult(error="no_stats_page", confidence="unknown")

        if stop_event and stop_event.is_set():
            return DailyVisitorResult(error="stopped", confidence="unknown")

        blog_home_url = f"https://m.blog.naver.com/{blog_id}"
        logger.log(f"[POPULARITY] 블로그 일 방문자 수 확인 중: {blog_id} ({blog_home_url})")

        try:
            stats_page.goto(blog_home_url, wait_until="domcontentloaded", timeout=12000)

            # 방문자 영역 DOM 탐색
            # 모바일 블로그 홈의 헤더 프로필 / 방문자 통계 위젯
            visitor_js = """
            (() => {
                const textNodes = [];
                // 1. '오늘', 'TODAY' 키워드를 포함하는 텍스트 요소 검색
                const allElements = document.querySelectorAll('div, span, em, p, li, strong, a');
                for (const el of allElements) {
                    const txt = (el.innerText || '').trim();
                    if (!txt) continue;
                    // '오늘 1,234' 또는 'TODAY 123' 패턴
                    if (txt.includes('오늘') || txt.toUpperCase().includes('TODAY')) {
                        // 자식 요소가 너무 많지 않은 단일 라벨/컨테이너 우선
                        if (el.children.length <= 3 && txt.length < 50) {
                            textNodes.push(txt);
                        }
                    }
                }
                return textNodes;
            })()
            """
            candidates = stats_page.evaluate(visitor_js)

            # 후보 분석
            daily_val = None
            daily_raw = None

            for cand in candidates:
                # "전체 12,000"과 같이 누적 방문자가 같이 섞여 있는 경우 "오늘" 부분만 분리
                lines = cand.split("\n")
                for line in lines:
                    line_s = line.strip()
                    if ("오늘" in line_s or "TODAY" in line_s.upper()) and not ("전체" in line_s or "누적" in line_s):
                        parsed = parse_compact_count(line_s)
                        if parsed is not None:
                            daily_val = parsed
                            daily_raw = line_s
                            break
                    elif "오늘" in line_s and ("전체" in line_s or "누적" in line_s):
                        # "오늘 3,241 전체 120,000" 형태
                        m = re.search(r"오늘\s*([0-9,천만KMkm\.]+)", line_s)
                        if m:
                            parsed = parse_compact_count(m.group(1))
                            if parsed is not None:
                                daily_val = parsed
                                daily_raw = m.group(0)
                                break
                if daily_val is not None:
                    break

            if daily_val is not None:
                res = DailyVisitorResult(
                    value=daily_val,
                    raw_text=daily_raw,
                    source=blog_home_url,
                    confidence="high"
                )
                logger.log(f"  📊 [POPULARITY] 블로그 '{blog_id}' 일 방문자 확인 성공: {daily_val:,}명 ('{daily_raw}')")
            else:
                res = DailyVisitorResult(
                    value=None,
                    raw_text=None,
                    source=blog_home_url,
                    confidence="unknown",
                    error="not_public_or_not_found"
                )
                logger.log(f"  ℹ️ [POPULARITY] 블로그 '{blog_id}' 일 방문자 비공개 또는 미표시", "WARNING")

            cls._cache[blog_id] = res
            return res

        except Exception as e:
            res = DailyVisitorResult(error=str(e), confidence="unknown")
            cls._cache[blog_id] = res
            logger.log(f"  [POPULARITY] 방문자 조회 실패: {e}", "WARNING")
            return res
