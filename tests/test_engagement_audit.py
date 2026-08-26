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

    def test_audit_deduplication_and_merge_by_blog_id(self):
        """동일한 blog_id에 대해 공감과 댓글이 합산되어 하나의 행으로 통합되는지 검증"""
        page_mock = MagicMock()

        # Mock Recent posts
        with patch("services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts") as mock_posts, \
             patch("services.reaction_participant_collector.ReactionParticipantCollector.collect") as mock_likers, \
             patch("services.comment_participant_collector.CommentParticipantCollector.collect") as mock_commenters:

            mock_posts.return_value = [
                {"log_no": "111", "url": "https://m.blog.naver.com/test_me/111", "title": "첫번째 글"},
                {"log_no": "222", "url": "https://m.blog.naver.com/test_me/222", "title": "두번째 글"}
            ]

            # Post 1: user_a liked & commented, user_b liked
            # Post 2: user_a liked, user_c commented
            mock_likers.side_effect = [
                ([{"blog_id": "user_a", "nickname": "사용자A"}, {"blog_id": "user_b", "nickname": "사용자B"}], "complete"),
                ([{"blog_id": "user_a", "nickname": "사용자A"}], "complete")
            ]
            mock_commenters.side_effect = [
                ([{"blog_id": "user_a", "nickname": "사용자A", "comment_sample": "잘봤어요!"}], "complete"),
                ([{"blog_id": "user_c", "nickname": "사용자C", "comment_sample": "멋지네요"}], "complete")
            ]

            res = EngagementAuditService.run_audit(
                page=page_mock,
                my_blog_id="test_me",
                recent_post_count=2
            )

            self.assertTrue(res["success"])
            rep = res["report"]
            self.assertEqual(rep["recent_post_count"], 2)
            self.assertEqual(rep["unique_participant_count"], 3)  # user_a, user_b, user_c
            self.assertEqual(rep["liker_count"], 2)               # user_a, user_b
            self.assertEqual(rep["commenter_count"], 2)           # user_a, user_c
            self.assertEqual(rep["both_count"], 1)                # user_a

            people = {p["blog_id"]: p for p in rep["people"]}
            self.assertEqual(people["user_a"]["liked_post_count"], 2)
            self.assertEqual(people["user_a"]["commented_post_count"], 1)
            self.assertEqual(people["user_a"]["total_engagement_count"], 3)
            self.assertEqual(people["user_b"]["total_engagement_count"], 1)
            self.assertEqual(people["user_c"]["total_engagement_count"], 1)

    def test_engagement_audit_store_creates_valid_csv(self):
        """JSON 및 CSV 파일이 올바르게 생성되는지 검증"""
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
            ]
        }
        json_p, csv_p = EngagementAuditStore.save(test_report)
        self.assertTrue(os.path.exists(json_p))
        self.assertTrue(os.path.exists(csv_p))

        with open(csv_p, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["blog_id"], "friend1")
            self.assertEqual(rows[0]["total_engagement_count"], "3")
            self.assertEqual(rows[0]["is_liker"], "Y")
            self.assertEqual(rows[0]["is_commenter"], "Y")


if __name__ == "__main__":
    unittest.main()
