import unittest
from unittest.mock import MagicMock
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState, LikeConfidence


class TestCommentPresenceGuard(unittest.TestCase):
    def test_mine_regex_matching(self):
        """data-info 문자열 정규식 파싱 검증"""
        self.assertTrue(ServerCommentDuplicateGuard.MINE_REGEX.search("commentNo:'123',mine:true,deleted:false"))
        self.assertTrue(ServerCommentDuplicateGuard.MINE_REGEX.search("mine: true, other: 1"))
        self.assertTrue(ServerCommentDuplicateGuard.MINE_REGEX.search("{ mine : true }"))
        self.assertFalse(ServerCommentDuplicateGuard.MINE_REGEX.search("commentNo:'123',mine:false,deleted:false"))
        self.assertFalse(ServerCommentDuplicateGuard.MINE_REGEX.search("vitaminE:true"))

    def test_strong_signal_data_info_mine_true(self):
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"totalCount": 5, "hasEditor": True},
            {
                "foundMine": True,
                "foundCommentNo": "902937659083587593",
                "foundText": "좋은 글이네요 :)",
                "evidence": ["data_info_mine_true"],
                "loadedCount": 5,
                "hasMore": False
            }
        ]
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock)
        self.assertEqual(res.state, CommentPresenceState.PRESENT)
        self.assertEqual(res.confidence, LikeConfidence.HIGH)
        self.assertEqual(res.comment_no, "902937659083587593")

    def test_total_count_zero_and_editor_ready_is_absent_high(self):
        page_mock = MagicMock()
        page_mock.evaluate.return_value = {"totalCount": 0, "hasEditor": True}
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock)
        self.assertEqual(res.state, CommentPresenceState.ABSENT)
        self.assertEqual(res.confidence, LikeConfidence.HIGH)

    def test_full_list_no_mine_is_absent_high(self):
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"totalCount": 3, "hasEditor": True},
            {
                "foundMine": False,
                "foundCommentNo": None,
                "foundText": None,
                "evidence": [],
                "loadedCount": 3,
                "hasMore": False
            }
        ]
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock)
        self.assertEqual(res.state, CommentPresenceState.ABSENT)
        self.assertEqual(res.confidence, LikeConfidence.HIGH)

    def test_partial_list_unconfirmed_is_unknown(self):
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"totalCount": 50, "hasEditor": True},
            # attempt 0
            {"foundMine": False, "foundCommentNo": None, "foundText": None, "evidence": [], "loadedCount": 10, "hasMore": True},
            # attempt 1
            {"foundMine": False, "foundCommentNo": None, "foundText": None, "evidence": [], "loadedCount": 20, "hasMore": True},
            # attempt 2
            {"foundMine": False, "foundCommentNo": None, "foundText": None, "evidence": [], "loadedCount": 30, "hasMore": True},
            # attempt 3
            {"foundMine": False, "foundCommentNo": None, "foundText": None, "evidence": [], "loadedCount": 40, "hasMore": True},
        ]
        page_mock.locator.return_value.first.count.return_value = 1
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock, max_scroll_attempts=3)
        self.assertEqual(res.state, CommentPresenceState.UNKNOWN)
        self.assertFalse(res.list_complete)


if __name__ == "__main__":
    unittest.main()
