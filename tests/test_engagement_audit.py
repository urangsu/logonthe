import os
import json
import csv
import unittest
from unittest.mock import MagicMock, patch
from services.engagement_audit_service import EngagementAuditService
from services.engagement_audit_store import EngagementAuditStore, AUDIT_JSON_PATH, AUDIT_CSV_PATH
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent


class TestEngagementAuditV7(unittest.TestCase):
    def test_comment_length_limit_100_chars(self):
        """댓글 100자 이하 허용, 101자 이상 거부 (v7 정책)"""
        cand_99 = CommentCandidate(
            body="이것은 정확히 백 자 이하의 정상적인 댓글입니다. 글 내용이 너무 유익하고 알차네요 ㅎㅎ 다음에 꼭 방문해보고 싶습니다!",
            category="FOOD",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="음식",
            template_id="v7_test"
        )
        self.assertLessEqual(len(cand_99.body), 100)
        valid, _ = PositiveSafetyValidator.validate_candidate(cand_99)
        self.assertTrue(valid)

        cand_101 = CommentCandidate(
            body="탐론 70-300mm 망원 렌즈 가성비 구성이 정말 알차 보이네요! 펜탁스 바디에 마운트한 사진 보니까 색감도 너무 예쁘게 잘 나오는 것 같아요 ㅎㅎ 다음에 출사 갈 때 저도 꼭 써보고 싶네요!",
            category="IT_GADGET",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="탐론",
            template_id="v7_test"
        )
        self.assertGreater(len(cand_101.body), 100)
        valid_over, reason = PositiveSafetyValidator.validate_candidate(cand_101)
        self.assertFalse(valid_over)
        self.assertIn("length_out_of_bounds", reason)

    def test_audit_deduplication_and_unresponsive_buddies(self):
        """이웃 전수와 반응자 간의 차집합(무반응 이웃) 및 유예 필터링 검증"""
        page_mock = MagicMock()
        from services.buddy_list_collector import BuddyInfo

        with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies") as mock_buddies, \
             patch("services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts") as mock_posts, \
             patch("services.reaction_participant_collector.ReactionParticipantCollector.collect") as mock_likers, \
             patch("services.comment_participant_collector.CommentParticipantCollector.collect") as mock_commenters:

            # 4명의 등록 이웃
            mock_buddies.return_value = {
                "user_a": BuddyInfo("user_a", "사용자A", "A블로그", "친한이웃", "서로이웃", "26.08.26.", "26.01.01."),
                "user_b": BuddyInfo("user_b", "사용자B", "B블로그", "기본", "이웃", "26.08.20.", "26.08.26."), # 신규 (유예 대상)
                "user_unresp1": BuddyInfo("user_unresp1", "무반응1", "U1블로그", "안한놈", "서로이웃", "26.07.01.", "26.05.01."),
                "user_unresp2": BuddyInfo("user_unresp2", "무반응2", "U2블로그", "사기꾼", "서로이웃", "26.06.01.", "26.04.01.")
            }

            mock_posts.return_value = [
                {"log_no": "111", "url": "https://m.blog.naver.com/test_me/111", "title": "첫번째 글"},
                {"log_no": "222", "url": "https://m.blog.naver.com/test_me/222", "title": "두번째 글"}
            ]

            # user_a는 좋아요와 댓글 남김, user_b는 댓글 남김 (반응자)
            # user_unresp1, user_unresp2는 반응 없음 (무반응)
            mock_likers.side_effect = [
                ([{"blog_id": "user_a", "nickname": "사용자A"}], "complete"),
                ([], "complete")
            ]
            mock_commenters.side_effect = [
                ([{"blog_id": "user_a", "nickname": "사용자A", "comment_sample": "좋아요"}], "complete"),
                ([{"blog_id": "user_b", "nickname": "사용자B", "comment_sample": "멋져요"}], "complete")
            ]

            res = EngagementAuditService.run_audit(
                page=page_mock,
                my_blog_id="test_me",
                recent_post_count=2
            )

            self.assertTrue(res["success"])
            rep = res["report"]
            self.assertEqual(rep["total_buddies_count"], 4)
            self.assertEqual(rep["reacted_buddies_count"], 2)      # user_a, user_b
            self.assertEqual(rep["unresponsive_buddies_count"], 2) # user_unresp1, user_unresp2

            unresp_ids = {u["blog_id"] for u in rep["unresponsive_buddies"]}
            self.assertEqual(unresp_ids, {"user_unresp1", "user_unresp2"})

    def test_engagement_audit_store_creates_valid_csv(self):
        """JSON 및 무반응자 CSV 파일이 올바르게 생성되는지 검증"""
        test_report = {
            "generated_at": "2026-08-26 20:00:00",
            "blog_id": "test_blog",
            "recent_post_count": 1,
            "people": [
                {
                    "blog_id": "friend1",
                    "nickname": "친한이웃",
                    "profile_url": "https://m.blog.naver.com/friend1",
                    "liked_post_count": 2,
                    "commented_post_count": 1,
                    "total_engagement_count": 3,
                    "liked_posts": ["글1", "글2"],
                    "commented_posts": ["글1"],
                    "comment_samples": ["좋은 글이에요"]
                }
            ],
            "unresponsive_buddies": [
                {
                    "blog_id": "ghost1",
                    "nickname": "유령이웃",
                    "blog_title": "유령의집",
                    "group_name": "안한놈",
                    "buddy_type": "서로이웃",
                    "added_date": "26.01.01.",
                    "last_post_date": "26.02.01.",
                    "is_grace_period": False
                }
            ]
        }
        json_p, csv_p, unresp_csv_p = EngagementAuditStore.save(test_report)
        self.assertTrue(os.path.exists(json_p))
        self.assertTrue(os.path.exists(csv_p))
        self.assertTrue(os.path.exists(unresp_csv_p))

        with open(unresp_csv_p, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["blog_id"], "ghost1")
            self.assertEqual(rows[0]["group_name"], "안한놈")
            self.assertEqual(rows[0]["is_grace_period"], "N")


if __name__ == "__main__":
    unittest.main()
