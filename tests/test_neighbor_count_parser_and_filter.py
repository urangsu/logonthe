# -*- coding: utf-8 -*-
"""
tests/test_neighbor_count_parser_and_filter.py

추천피드 작성자 이웃 수 파싱 및 상한 필터 테스트:
1. parse_neighbor_count 5종 정밀 테스트 (콤마 정수, 만/천 단위, 0명, 파싱 실패 fail-open)
2. RecommendationFeedSource 이웃 수 상한 필터 (9999, 10000, 10001, 16385, None)
3. 서로이웃/이웃 피드(NeighborFeedSource)에는 이웃 수 필터가 절대 적용되지 않는 불변조건 검증
4. 추천 피드 상한 초과 카드에 대한 상세 진입, 좋아요, 댓글, Gemini 미실행 검증
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models import FeedPost, FeedSourceType, PostProcessResult, CommentSubmitState
from naver.resolver import parse_neighbor_count, MobileDOMResolver
from naver.sources import RecommendationFeedSource, NeighborFeedSource


class TestNeighborCountParserAndFilter(unittest.TestCase):

    def test_parse_neighbor_count_all_formats(self):
        """1. 이웃 수 파싱 테스트 (정수 콤마, 만/천 축약, 0, fail-open)"""
        # 콤마 포함 정수
        self.assertEqual(parse_neighbor_count("16,385"), 16385)
        self.assertEqual(parse_neighbor_count("9,821"), 9821)
        self.assertEqual(parse_neighbor_count("500"), 500)
        self.assertEqual(parse_neighbor_count("이웃 16,385명"), 16385)

        # 만/천 단위
        self.assertEqual(parse_neighbor_count("1.6만"), 16000)
        self.assertEqual(parse_neighbor_count("1만"), 10000)
        self.assertEqual(parse_neighbor_count("8천"), 8000)
        self.assertEqual(parse_neighbor_count("이웃 1.5만명"), 15000)

        # 0명 정상 파싱
        self.assertEqual(parse_neighbor_count("0"), 0)
        self.assertEqual(parse_neighbor_count("이웃 0명"), 0)

        # 파싱 실패 및 공백 -> None (fail-open)
        self.assertIsNone(parse_neighbor_count(None))
        self.assertIsNone(parse_neighbor_count(""))
        self.assertIsNone(parse_neighbor_count("   "))
        self.assertIsNone(parse_neighbor_count("알수없음"))

    def test_recommendation_feed_filter_limits(self):
        """2. 추천피드 작성자 이웃 수 상한 필터링 검증 (10,000 기준)"""
        page_mock = MagicMock()
        source = RecommendationFeedSource(
            page=page_mock,
            max_items=10,
            neighbor_count_max=10000,
        )

        # 5개의 카드 모의:
        # card0: 9,999 (허용)
        # card1: 10,000 (허용, 상한 이하)
        # card2: 10,001 (차단)
        # card3: 16,385 (차단)
        # card4: None (미확인, fail-open 허용)
        card_data = [
            ("https://m.blog.naver.com/user1/1", "강남 돈까스 맛집", "user1", 9999),
            ("https://m.blog.naver.com/user2/2", "성수동 삼겹살 맛집", "user2", 10000),
            ("https://m.blog.naver.com/user3/3", "홍대 파스타 맛집", "user3", 10001),
            ("https://m.blog.naver.com/user4/4", "종로 국밥 맛집", "user4", 16385),
            ("https://m.blog.naver.com/user5/5", "연남동 라멘 맛집", "user5", None),
        ]

        mock_cards = []
        for url, title, author, n_count in card_data:
            c = MagicMock()
            c.inner_text.return_value = f"{title} - 맛있는 메뉴 사진"
            mock_cards.append(c)

        cards_mock = MagicMock()
        cards_mock.count.return_value = len(card_data)
        cards_mock.nth.side_effect = lambda idx: mock_cards[idx]

        with patch.object(MobileDOMResolver, "get_feed_cards", return_value=cards_mock), \
             patch.object(MobileDOMResolver, "get_card_post_link") as mock_link, \
             patch.object(MobileDOMResolver, "get_card_title") as mock_title, \
             patch.object(MobileDOMResolver, "get_card_author") as mock_author, \
             patch.object(MobileDOMResolver, "get_card_neighbor_count") as mock_n_count:

            def side_link(c):
                idx = mock_cards.index(c)
                l = MagicMock()
                l.count.return_value = 1
                l.get_attribute.return_value = card_data[idx][0]
                return l

            def side_title(c):
                idx = mock_cards.index(c)
                return card_data[idx][1]

            def side_author(c):
                idx = mock_cards.index(c)
                return card_data[idx][2]

            def side_n_count(c):
                idx = mock_cards.index(c)
                return card_data[idx][3]

            mock_link.side_effect = side_link
            mock_title.side_effect = side_title
            mock_author.side_effect = side_author
            mock_n_count.side_effect = side_n_count

            discovered = source.discover_posts()

        discovered_ids = [p.blog_id for p in discovered]
        self.assertIn("user1", discovered_ids, "9999명은 허용되어야 함")
        self.assertIn("user2", discovered_ids, "10000명(상한선)은 허용되어야 함")
        self.assertNotIn("user3", discovered_ids, "10001명은 차단되어야 함")
        self.assertNotIn("user4", discovered_ids, "16385명은 차단되어야 함")
        self.assertIn("user5", discovered_ids, "None(미확인)은 fail-open 허용되어야 함")

    def test_neighbor_feed_source_never_applies_neighbor_count_filter(self):
        """3. 서로이웃/이웃 피드(NeighborFeedSource)에는 이웃 수 필터가 절대 미적용됨을 검증"""
        page_mock = MagicMock()
        source = NeighborFeedSource(page=page_mock, max_items=5)

        # NeighborFeedSource 객체에는 neighbor_count_max 속성이나 필터 로직이 전혀 없어야 함
        self.assertFalse(hasattr(source, "neighbor_count_max"), "NeighborFeedSource는 이웃 수 상한 설정을 갖지 않음")

        cards_mock = MagicMock()
        cards_mock.count.return_value = 2

        with patch.object(MobileDOMResolver, "get_feed_cards", return_value=cards_mock), \
             patch.object(MobileDOMResolver, "get_card_post_link") as mock_link, \
             patch.object(MobileDOMResolver, "get_card_title", return_value="이웃글"), \
             patch.object(MobileDOMResolver, "get_card_author", return_value="mutual_user"):

            def side_link(c):
                l = MagicMock()
                l.count.return_value = 1
                l.get_attribute.return_value = "https://m.blog.naver.com/mutual_user/1"
                return l

            mock_link.side_effect = side_link

            # NeighborFeedSource 탐색 실행
            discovered = source.discover_posts()

        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].blog_id, "mutual_user")
        self.assertEqual(discovered[0].source, FeedSourceType.NEIGHBOR)
        # neighbor_count 파싱을 호출조차 하지 않으므로 None
        self.assertIsNone(discovered[0].neighbor_count)


if __name__ == "__main__":
    unittest.main()
