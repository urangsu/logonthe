import threading
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from playwright.sync_api import Page
from app.models import FeedPost
from naver.resolver import MobileDOMResolver
from naver.count_parser import parse_compact_count
from naver.url_utils import parse_blog_post_url
from services.blog_popularity import BlogPopularityService
from src.logger import logger


class LikeEligibility(str, Enum):
    ELIGIBLE = "eligible"
    SKIP_LIKE_COUNT = "skip_like_count"
    SKIP_DAILY_VISITORS = "skip_daily_visitors"
    UNKNOWN_LIKE_COUNT = "unknown_like_count"
    UNKNOWN_DAILY_VISITORS = "unknown_daily_visitors"


@dataclass
class LikeEligibilityResult:
    eligible: bool
    status: LikeEligibility
    like_count: Optional[int] = None
    like_count_raw: Optional[str] = None
    daily_visitors: Optional[int] = None
    daily_visitors_raw: Optional[str] = None
    reason: Optional[str] = None


class LikeEligibilityService:
    """
    게시글 공감수(999+ 가드) 및 블로그 일 방문자 수(10,000+ 가드)를 검사하여
    공감 클릭 적격성(Eligibility)을 판정합니다.
    """

    @classmethod
    def evaluate(
        cls,
        detail_page: Page,
        stats_page: Optional[Page],
        post: FeedPost,
        config,
        stop_event: Optional[threading.Event] = None
    ) -> LikeEligibilityResult:
        # 1. 공감수(Like Count) Guard 검사 (비용이 적으므로 우선 수행)
        like_guard_enabled = config.get("like_popularity_guard_enabled", True)
        like_threshold = int(config.get("like_count_skip_threshold", 999))

        like_count_val = None
        like_count_raw = None

        if like_guard_enabled:
            like_count_raw = MobileDOMResolver.get_like_count_text(detail_page)
            like_count_val = parse_compact_count(like_count_raw)

            if like_count_val is not None and like_count_val >= like_threshold:
                reason = f"🚫 [LIKE] 공감수 {like_count_val:,}개 (raw: '{like_count_raw}') — 기준({like_threshold:,}개 이상)에 따라 공감 제외"
                logger.log(reason, "WARNING")
                return LikeEligibilityResult(
                    eligible=False,
                    status=LikeEligibility.SKIP_LIKE_COUNT,
                    like_count=like_count_val,
                    like_count_raw=like_count_raw,
                    reason=reason
                )

        # 2. 일 방문자 수(Daily Visitor) Guard 검사
        visitor_guard_enabled = config.get("daily_visitor_guard_enabled", True)
        visitor_threshold = int(config.get("daily_visitor_skip_threshold", 10000))
        unknown_policy = config.get("daily_visitor_unknown_policy", "skip_like")

        daily_visitors_val = None
        daily_visitors_raw = None

        if visitor_guard_enabled:
            # 블로그 ID 파싱
            blog_id = None
            if post.url:
                parsed_res = parse_blog_post_url(post.url)
                if parsed_res:
                    blog_id = parsed_res[0]
            if not blog_id and post.key and ":" in post.key:
                blog_id = post.key.split(":", 1)[0]

            if blog_id:
                vis_res = BlogPopularityService.get_daily_visitors(stats_page, blog_id, stop_event)
                daily_visitors_val = vis_res.value
                daily_visitors_raw = vis_res.raw_text

                if daily_visitors_val is not None:
                    # > 10,000 기준
                    if daily_visitors_val > visitor_threshold:
                        reason = f"🚫 [LIKE] 블로그 '{blog_id}' 일 방문자 {daily_visitors_val:,}명 — 기준({visitor_threshold:,}명 초과)에 따라 공감 제외"
                        logger.log(reason, "WARNING")
                        return LikeEligibilityResult(
                            eligible=False,
                            status=LikeEligibility.SKIP_DAILY_VISITORS,
                            like_count=like_count_val,
                            like_count_raw=like_count_raw,
                            daily_visitors=daily_visitors_val,
                            daily_visitors_raw=daily_visitors_raw,
                            reason=reason
                        )
                else:
                    # 방문자 수 확인 불가
                    if unknown_policy == "skip_like":
                        reason = f"ℹ️ [LIKE] 블로그 '{blog_id}' 일 방문자 수 확인 불가 — 정책({unknown_policy})에 따라 공감 건너뜀"
                        logger.log(reason, "WARNING")
                        return LikeEligibilityResult(
                            eligible=False,
                            status=LikeEligibility.UNKNOWN_DAILY_VISITORS,
                            like_count=like_count_val,
                            like_count_raw=like_count_raw,
                            reason=reason
                        )

        # 모든 가드 통과 -> 공감 적격
        return LikeEligibilityResult(
            eligible=True,
            status=LikeEligibility.ELIGIBLE,
            like_count=like_count_val,
            like_count_raw=like_count_raw,
            daily_visitors=daily_visitors_val,
            daily_visitors_raw=daily_visitors_raw
        )
