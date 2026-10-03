# -*- coding: utf-8 -*-
"""
tests/test_neighbor_count_parser_and_filter.py

추천피드 이웃수 필터 v2 (프로필 페이지 조회 기반):
1. parse_neighbor_count 단위 테스트
2. extract_neighbor_count / DOM fixture (hash 변경, semantic fallback, 오탐 방지)
3. BlogNeighborCountService 캐시 / 실패 분류
4. RecommendationFeedSource 통합 (허용/차단/UNKNOWN, circuit breaker, 재검사 금지)
5. NeighborFeedSource 에서는 서비스 호출 0회
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models import FeedSourceType
from naver.resolver import parse_neighbor_count, MobileDOMResolver
from naver.sources import RecommendationFeedSource, NeighborFeedSource
from services import blog_neighbor_count as bnc
from services.blog_neighbor_count import (
    BlogNeighborCountService,
    NeighborCountResult,
    extract_neighbor_count,
)


def _cand(text, kind="generic"):
    return {"text": text, "kind": kind, "cls": ""}


class TestParser(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_neighbor_count("16,385"), 16385)
        self.assertEqual(parse_neighbor_count("1.6만"), 16000)
        self.assertEqual(parse_neighbor_count("8천"), 8000)
        self.assertEqual(parse_neighbor_count("이웃 0명"), 0)
        self.assertIsNone(parse_neighbor_count("알수없음"))


class TestExtract(unittest.TestCase):
    def test_profile_block(self):
        self.assertEqual(extract_neighbor_count([_cand("이웃 16,385 명", "profile_block")])[0], 16385)

    def test_hash_changed_class_irrelevant(self):
        self.assertEqual(extract_neighbor_count([_cand("이웃 984 명", "profile_block")])[0], 984)

    def test_semantic_fallback(self):
        self.assertEqual(extract_neighbor_count([_cand("이웃 2,340명")])[0], 2340)

    def test_compact_units(self):
        self.assertEqual(extract_neighbor_count([_cand("이웃 1.6만명")])[0], 16000)

    def test_no_false_positive_without_label(self):
        # '이웃' label 이 없는 숫자는 후보로 들어오지도 않지만, 들어와도 사용 금지
        for txt in ("오늘 1,234명", "공감 500", "댓글 22", "게시글 100"):
            self.assertIsNone(extract_neighbor_count([_cand(txt)])[0])

    def test_mutual_neighbor_count_not_used(self):
        self.assertIsNone(extract_neighbor_count([_cand("서로이웃 12명")])[0])

    def test_profile_block_wins_over_generic(self):
        value, _, _ = extract_neighbor_count([_cand("이웃 5명"), _cand("이웃 873 명", "profile_block")])
        self.assertEqual(value, 873)

    def test_error_codes(self):
        self.assertEqual(extract_neighbor_count([])[2], bnc.ERR_LABEL_NOT_FOUND)
        self.assertEqual(extract_neighbor_count([_cand("이웃 추가")])[2], bnc.ERR_PARSE_FAILED)

    def test_live_blog_home_format_number_first(self):
        """실전 로그에서 확인된 '1,186명의 이웃' 형식 (buddy__ span)"""
        live = [
            _cand("JOOTALK 일상·생각 ㆍ 1,186명의 이웃"),
            _cand("1,186명의 이웃", "profile_block"),
            _cand("이웃추가 안부글이웃목록앱알림 비활성화됨, 앱알림 활성화하기 공유하기"),
            _cand("이웃추가"),
            _cand("이웃목록"),
        ]
        self.assertEqual(extract_neighbor_count(live)[0], 1186)
        # generic 후보만 있어도 동일하게 추출
        self.assertEqual(extract_neighbor_count(live[:1] + live[2:])[0], 1186)

    def test_live_profile_block_format_label_first(self):
        """사용자가 확인한 '이웃 613명' ProfileBlock 형식 (innerText 공백 정규화 후)"""
        self.assertEqual(extract_neighbor_count([_cand("이웃 613 명", "profile_block")])[0], 613)

    def test_number_first_buttons_not_counted(self):
        for txt in ("이웃추가", "이웃목록", "3명의 이웃추가", "12명의 서로이웃"):
            self.assertIsNone(extract_neighbor_count([_cand(txt)])[0], txt)


class _StubPage:
    def __init__(self, data=None, url="https://m.blog.naver.com/x", goto_error=None):
        self.data = data or {"candidates": [], "profileBlockCount": 0, "title": ""}
        self.url = url
        self.goto_calls = 0
        self.goto_error = goto_error

    def goto(self, *a, **k):
        self.goto_calls += 1
        if self.goto_error:
            raise self.goto_error

    def wait_for_function(self, *a, **k):
        return None

    def evaluate(self, js):
        return self.data


class TestService(unittest.TestCase):
    def setUp(self):
        BlogNeighborCountService.clear_cache()
        p = patch.object(bnc, "interruptible_wait", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def test_cache_prevents_second_goto(self):
        page = _StubPage({"candidates": [_cand("이웃 873명", "profile_block")], "profileBlockCount": 1, "title": ""})
        r1 = BlogNeighborCountService.get_neighbor_count(page, "a")
        r2 = BlogNeighborCountService.get_neighbor_count(page, "a")
        self.assertEqual((r1.value, r2.value), (873, 873))
        self.assertEqual(page.goto_calls, 1)

    def test_unknown_is_negative_cached(self):
        page = _StubPage()
        r1 = BlogNeighborCountService.get_neighbor_count(page, "b")
        BlogNeighborCountService.get_neighbor_count(page, "b")
        self.assertIsNone(r1.value)
        self.assertEqual(r1.error, bnc.ERR_PROFILE_BLOCK_NOT_FOUND)
        self.assertEqual(page.goto_calls, 1)

    def test_navigation_failure_retried_once(self):
        page = _StubPage(goto_error=RuntimeError("boom"))
        r = BlogNeighborCountService.get_neighbor_count(page, "c")
        self.assertEqual(r.error, bnc.ERR_NAVIGATION_FAILED)
        self.assertEqual(page.goto_calls, 2)

    def test_login_redirect(self):
        page = _StubPage(url="https://nid.naver.com/nidlogin.login")
        self.assertEqual(BlogNeighborCountService.get_neighbor_count(page, "d").error, bnc.ERR_LOGIN_REDIRECT)

    def test_not_public(self):
        page = _StubPage({"candidates": [], "profileBlockCount": 1, "title": "", "notPublic": True})
        self.assertEqual(BlogNeighborCountService.get_neighbor_count(page, "e").error, bnc.ERR_NOT_PUBLIC)


def _build_source(counts, max_n=1000, resolver=None):
    """counts: {blog_id: int|None}"""
    ids = list(counts.keys())
    cards = []
    for bid in ids:
        c = MagicMock()
        c.inner_text.return_value = "성수동 맛집 후기 - 메뉴 사진"
        cards.append(c)
    cards_mock = MagicMock()
    cards_mock.count.return_value = len(ids)
    cards_mock.nth.side_effect = lambda i: cards[i]

    calls = []

    def default_resolver(blog_id):
        calls.append(blog_id)
        v = counts[blog_id]
        return NeighborCountResult(v, None, "t", "high" if v is not None else "unknown",
                                   None if v is not None else "profile_block_not_found")

    source = RecommendationFeedSource(
        page=MagicMock(), max_items=50, neighbor_count_max=max_n,
        neighbor_count_resolver=resolver or default_resolver,
    )

    def link(c):
        i = cards.index(c)
        lnk = MagicMock()
        lnk.count.return_value = 1
        lnk.get_attribute.return_value = f"https://m.blog.naver.com/{ids[i]}/{1000 + i}"
        return lnk

    patches = [
        patch.object(MobileDOMResolver, "get_feed_cards", return_value=cards_mock),
        patch.object(MobileDOMResolver, "get_card_post_link", side_effect=link),
        patch.object(MobileDOMResolver, "get_card_title", side_effect=lambda c: "성수동 맛집 후기"),
        patch.object(MobileDOMResolver, "get_card_author", side_effect=lambda c: "author"),
    ]
    return source, patches, calls


def _discover(source, patches):
    for p in patches:
        p.start()
    try:
        return source.discover_posts()
    finally:
        for p in patches:
            p.stop()


class TestRecommendationIntegration(unittest.TestCase):
    def test_allow_block_unknown(self):
        source, patches, calls = _build_source(
            {"blog1": 250, "blog2": 1000, "blog3": 1001, "blog4": 16385, "blog5": None}
        )
        ids = [p.blog_id for p in _discover(source, patches)]
        self.assertEqual(ids, ["blog1", "blog2"])
        st = source.neighbor_stats
        self.assertEqual((st["lookups"], st["known"], st["allowed"], st["blocked"], st["unknown"]), (5, 4, 2, 2, 1))
        self.assertFalse(source.resolver_broken)

    def test_circuit_breaker_after_five_unknown(self):
        source, patches, calls = _build_source({f"b{i}": None for i in range(9)})
        self.assertEqual(_discover(source, patches), [])
        self.assertTrue(source.resolver_broken)
        self.assertEqual(len(calls), 5)  # 5회 이후 더 조회하지 않는다

    def test_no_breaker_when_some_known(self):
        counts = {"a": None, "b": None, "c": None, "d": None, "e": 10, "f": None}
        source, patches, _ = _build_source(counts)
        _discover(source, patches)
        self.assertFalse(source.resolver_broken)

    def test_examined_cards_not_rechecked(self):
        source, patches, calls = _build_source({"x": 5000, "y": 10})
        _discover(source, patches)
        _discover(source, patches)
        self.assertEqual(calls, ["x", "y"])

    def test_filter_off_never_calls_resolver(self):
        source, patches, calls = _build_source({"x": 5000}, max_n=0)
        ids = [p.blog_id for p in _discover(source, patches)]
        self.assertEqual(ids, ["x"])
        self.assertEqual(calls, [])


class TestNeighborSourceUnaffected(unittest.TestCase):
    def test_neighbor_source_never_calls_service(self):
        source = NeighborFeedSource(page=MagicMock(), max_items=5)
        self.assertFalse(hasattr(source, "neighbor_count_resolver"))
        cards_mock = MagicMock()
        cards_mock.count.return_value = 1
        with patch.object(MobileDOMResolver, "get_feed_cards", return_value=cards_mock), \
             patch.object(MobileDOMResolver, "get_card_post_link") as mock_link, \
             patch.object(MobileDOMResolver, "get_card_title", return_value="t"), \
             patch.object(MobileDOMResolver, "get_card_author", return_value="a"), \
             patch.object(BlogNeighborCountService, "get_neighbor_count") as svc:
            lnk = MagicMock()
            lnk.count.return_value = 1
            lnk.get_attribute.return_value = "https://m.blog.naver.com/mutual_user/1"
            mock_link.return_value = lnk
            discovered = source.discover_posts()
        self.assertEqual(discovered[0].source, FeedSourceType.NEIGHBOR)
        svc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
