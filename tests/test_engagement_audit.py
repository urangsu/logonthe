import os
import json
import csv
import unittest
from unittest.mock import MagicMock, patch
from services.buddy_list_collector import BuddyListCollector, BuddyInfo, BuddyCollectionResult
from services.engagement_audit_service import EngagementAuditService
from services.engagement_audit_store import EngagementAuditStore
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent


class TestEngagementAuditRebuildV8(unittest.TestCase):
    def test_comment_length_limit_100_chars(self):
        """댓글 100자 이하 허용, 101자 이상 거부 (v7/v8 정책)"""
        cand_ok = CommentCandidate(
            body="남해 독일마을 플래터 구성이 알차고 맛있어 보여요! 나중에 가보고 싶네요.",
            category="FOOD",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="음식",
            template_id="v8_test"
        )
        valid, _ = PositiveSafetyValidator.validate_candidate(cand_ok)
        self.assertTrue(valid)

        cand_over = CommentCandidate(
            body="탐론 70-300mm 망원 렌즈 가성비 구성이 정말 알차 보이네요! 펜탁스 바디에 마운트한 사진 보니까 색감도 너무 예쁘게 잘 나오는 것 같아요. 다음에 출사 갈 때 저도 다뤄보고 싶네요. 사진 결과물이 아주 만족스러워 보입니다.",
            category="IT_GADGET",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="탐론",
            template_id="v8_test"
        )
        self.assertGreater(len(cand_over.body), 100)
        valid_over, reason = PositiveSafetyValidator.validate_candidate(cand_over)
        self.assertFalse(valid_over)
        self.assertIn("length_out_of_bounds", reason)

    def test_buddy_expected_194_collected_50_is_partial(self):
        """기대 이웃 수 194명 대비 50명만 수집되었을 때 COMPLETE가 아닌 PARTIAL 판정 검증"""
        page_mock = MagicMock()
        frame_mock = MagicMock()
        page_mock.frame.return_value = frame_mock

        # evaluate returns 50 items and expectedTotal=194, no next navigation
        frame_mock.evaluate.return_value = {
            "expectedTotal": 194,
            "items": [{"blog_id": f"user_{i}", "nickname": f"유저{i}", "blog_title": "", "group_name": "기본", "buddy_type": "이웃", "added_date": "26.01.01.", "last_post_date": ""} for i in range(50)],
            "nextLinks": [],
            "firstId": "user_0"
        }

        res = BuddyListCollector.collect_all_buddies(page=page_mock, blog_id="test_user")
        self.assertEqual(res.state, "partial")
        self.assertEqual(res.collected_total, 50)
        self.assertEqual(res.expected_total, 194)

    def test_master_csv_rows_equal_total_buddies_and_zero_zero_unresponsive(self):
        """Master CSV 행 수가 전체 이웃 수(194)와 정확히 일치하고 like/comment가 0인 이웃만 무반응으로 분류되는지 검증"""
        page_mock = MagicMock()

        # 3명의 이웃과 1명의 비이웃 반응자
        buddies = {
            "buddy_both": BuddyInfo("buddy_both", "양방향", "B1", "그룹A", "서로이웃", "26.08.26.", "26.01.01."),
            "buddy_like_only": BuddyInfo("buddy_like_only", "공감만", "B2", "그룹A", "이웃", "26.08.25.", "26.01.01."),
            "buddy_unresp": BuddyInfo("buddy_unresp", "무반응", "B3", "그룹B", "서로이웃", "26.07.01.", "26.01.01.")
        }
        b_result = BuddyCollectionResult(
            buddies=buddies,
            state="complete",
            expected_total=3,
            collected_total=3,
            pages_visited=1,
            page_fingerprints=["p1_both_3"]
        )

        with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=b_result), \
             patch("services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts") as mock_posts, \
             patch("services.reaction_participant_collector.ReactionParticipantCollector.collect") as mock_likers, \
             patch("services.comment_participant_collector.CommentParticipantCollector.collect") as mock_commenters:

            mock_posts.return_value = [
                {"log_no": "101", "url": "https://m.blog.naver.com/me/101", "title": "글1"},
                {"log_no": "102", "url": "https://m.blog.naver.com/me/102", "title": "글2"}
            ]

            # Post 1: buddy_both liked & commented, non_buddy_1 commented
            # Post 2: buddy_both liked, buddy_like_only liked
            mock_likers.side_effect = [
                ([{"blog_id": "buddy_both", "nickname": "양방향"}], "complete", 1),
                ([{"blog_id": "buddy_both", "nickname": "양방향"}, {"blog_id": "buddy_like_only", "nickname": "공감만"}], "complete", 2)
            ]
            mock_commenters.side_effect = [
                ([{"blog_id": "buddy_both", "nickname": "양방향", "comment_entry_count": 2}, {"blog_id": "stranger_1", "nickname": "외부인", "comment_entry_count": 1}], "complete", 2),
                ([], "complete", 0)
            ]

            audit_res = EngagementAuditService.run_audit(page=page_mock, my_blog_id="me", recent_post_count=2)
            self.assertTrue(audit_res["success"])
            self.assertEqual(audit_res["audit_state"], "complete")

            rep = audit_res["report"]
            self.assertEqual(rep["total_buddies_count"], 3)
            self.assertEqual(len(rep["master_buddies"]), 3)

            master_dict = {m["blog_id"]: m for m in rep["master_buddies"]}

            # buddy_both: 2 likes, 1 post commented (2 entries), both = True
            self.assertEqual(master_dict["buddy_both"]["like_count"], 2)
            self.assertEqual(master_dict["buddy_both"]["comment_count"], 1)
            self.assertEqual(master_dict["buddy_both"]["comment_entry_count"], 2)
            self.assertEqual(master_dict["buddy_both"]["engaged_post_count"], 2)
            self.assertTrue(master_dict["buddy_both"]["both_like_and_comment"])
            self.assertFalse(master_dict["buddy_both"]["no_reaction"])

            # buddy_like_only: 1 like, 0 comments, liked_only = True
            self.assertEqual(master_dict["buddy_like_only"]["like_count"], 1)
            self.assertEqual(master_dict["buddy_like_only"]["comment_count"], 0)
            self.assertTrue(master_dict["buddy_like_only"]["liked_only"])
            self.assertFalse(master_dict["buddy_like_only"]["no_reaction"])

            # buddy_unresp: 0 likes, 0 comments, no_reaction = True
            self.assertEqual(master_dict["buddy_unresp"]["like_count"], 0)
            self.assertEqual(master_dict["buddy_unresp"]["comment_count"], 0)
            self.assertTrue(master_dict["buddy_unresp"]["no_reaction"])

            # Unresponsive list
            self.assertEqual(len(rep["unresponsive_buddies"]), 1)
            self.assertEqual(rep["unresponsive_buddies"][0]["blog_id"], "buddy_unresp")

            # Non-buddy reactors separated
            self.assertEqual(len(rep["non_buddy_reactors"]), 1)
            self.assertEqual(rep["non_buddy_reactors"][0]["blog_id"], "stranger_1")

    def test_store_creates_3_csv_files_with_correct_headers(self):
        """Master CSV, Unresponsive CSV, Non-buddy CSV가 정확한 헤더와 행으로 생성되는지 검증"""
        test_report = {
            "generated_at": "2026-08-26 22:00:00",
            "blog_id": "test_blog",
            "audit_state": "complete",
            "recent_post_count": 2,
            "master_buddies": [
                {
                    "blog_id": "b1", "nickname": "이웃1", "blog_title": "타이틀1", "group_name": "그룹A",
                    "buddy_type": "서로이웃", "added_date": "26.08.20.", "last_post_date": "26.08.26.",
                    "like_count": 2, "comment_count": 1, "comment_entry_count": 1, "engaged_post_count": 2,
                    "liked_only": False, "commented_only": False, "both_like_and_comment": True, "no_reaction": False,
                    "is_recent_buddy": False, "scan_complete": True
                },
                {
                    "blog_id": "b2", "nickname": "이웃2", "blog_title": "타이틀2", "group_name": "그룹B",
                    "buddy_type": "이웃", "added_date": "26.08.25.", "last_post_date": "26.08.20.",
                    "like_count": 0, "comment_count": 0, "comment_entry_count": 0, "engaged_post_count": 0,
                    "liked_only": False, "commented_only": False, "both_like_and_comment": False, "no_reaction": True,
                    "is_recent_buddy": True, "scan_complete": True
                }
            ],
            "unresponsive_buddies": [
                {
                    "blog_id": "b2", "nickname": "이웃2", "blog_title": "타이틀2", "group_name": "그룹B",
                    "buddy_type": "이웃", "added_date": "26.08.25.", "last_post_date": "26.08.20.",
                    "like_count": 0, "comment_count": 0, "comment_entry_count": 0, "engaged_post_count": 0,
                    "liked_only": False, "commented_only": False, "both_like_and_comment": False, "no_reaction": True,
                    "is_recent_buddy": True, "scan_complete": True
                }
            ],
            "non_buddy_reactors": [
                {
                    "blog_id": "stranger_1", "nickname": "외부인", "profile_url": "https://m.blog.naver.com/stranger_1",
                    "like_count": 1, "comment_count": 1, "comment_entry_count": 1, "engaged_post_count": 1
                }
            ]
        }

        json_p, master_csv, unresp_csv, non_buddy_csv = EngagementAuditStore.save_v8(test_report)

        self.assertTrue(os.path.exists(json_p))
        self.assertTrue(os.path.exists(master_csv))
        self.assertTrue(os.path.exists(unresp_csv))
        self.assertTrue(os.path.exists(non_buddy_csv))

        # Check Master CSV row count & Korean headers
        with open(master_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["블로그ID"], "b1")
            self.assertEqual(rows[0]["블로그명"], "타이틀1")
            self.assertEqual(rows[0]["공감댓글모두"], "Y")
            self.assertEqual(rows[1]["무반응여부"], "Y")

        # Check Unresponsive CSV row count & Korean headers
        with open(unresp_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["블로그ID"], "b2")
            self.assertEqual(rows[0]["신규추가유예"], "Y")

        # Check Non-buddy CSV row count & Korean headers
        with open(non_buddy_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["블로그ID"], "stranger_1")


if __name__ == "__main__":
    unittest.main()
