"""
Unit tests for Step 1:
- Relationship parsing from cell text, img alt/title, aria-label, and fallback columns
- Fail-closed quality contract on unknown relationships (246 unknown -> blocked)
- Genuine zero mutual detection (246 non-mutual -> NO_ELIGIBLE_MUTUAL_NEIGHBORS -> immediate safe completion)
- Missing feed post ID treated as UNKNOWN (not non-mutual)
"""
import unittest
from unittest.mock import MagicMock, patch

from app.models import FeedSourceType, FeedPost
from app.state import FeedState
from services.buddy_list_collector import (
    BuddyCollectionResult,
    BuddyInfo,
    BuddyListCollector,
)
from services.relationship_resolver import (
    RelationshipResolver,
    RelationshipType,
)


class TestRelationshipResolverAndQualityContract(unittest.TestCase):
    def test_246_unknown_blocks_execution(self):
        """246 total with all unknown relationships triggers fail-closed execution block."""
        buddies = {
            f"user_{i}": BuddyInfo(
                blog_id=f"user_{i}",
                nickname=f"User {i}",
                blog_title="",
                group_name="기본",
                buddy_type="unknown",
                last_post_date=None,
                added_date="26.01.01",
                buddy_type_evidence="unresolved",
            )
            for i in range(246)
        }
        res = BuddyCollectionResult(
            buddies=buddies,
            state="partial",
            expected_total=246,
            collected_total=246,
            pages_visited=9,
            page_fingerprints=["fp"],
            error=None,
            quality_issues=["relationship_parse_incomplete"],
            terminal=True,
            collection_complete=True,
            relationship_complete=False,
            mutual_count=0,
            non_mutual_count=0,
            unknown_count=246,
        )
        resolver = RelationshipResolver(res)
        blocked, reason = resolver.is_execution_blocked()
        self.assertTrue(blocked)
        self.assertEqual(reason, "relationship_parse_incomplete")

    def test_genuine_zero_mutual_is_not_blocked_but_zero_eligible(self):
        """246 total with all verified NON_MUTUAL (0 mutual, 0 unknown) has complete parsing."""
        buddies = {
            f"user_{i}": BuddyInfo(
                blog_id=f"user_{i}",
                nickname=f"User {i}",
                blog_title="",
                group_name="기본",
                buddy_type="이웃",
                last_post_date=None,
                added_date="26.01.01",
                buddy_type_evidence="text:이웃",
            )
            for i in range(246)
        }
        res = BuddyCollectionResult(
            buddies=buddies,
            state="complete",
            expected_total=246,
            collected_total=246,
            pages_visited=9,
            page_fingerprints=["fp"],
            terminal=True,
            collection_complete=True,
            relationship_complete=True,
            mutual_count=0,
            non_mutual_count=246,
            unknown_count=0,
        )
        resolver = RelationshipResolver(res)
        blocked, reason = resolver.is_execution_blocked()
        self.assertFalse(blocked)
        self.assertEqual(len(resolver.get_mutual_blog_ids()), 0)

    def test_post_not_in_buddy_list_resolved_as_unknown_fail_closed(self):
        """A post whose blog_id is not in the collected buddy list is UNKNOWN, not assumed NON_MUTUAL."""
        buddies = {
            "known_mutual": BuddyInfo("known_mutual", "KM", "", "", "서로이웃", None, "26.01.01"),
            "known_neighbor": BuddyInfo("known_neighbor", "KN", "", "", "이웃", None, "26.01.01"),
        }
        res = BuddyCollectionResult(
            buddies=buddies,
            state="complete",
            expected_total=2,
            collected_total=2,
            pages_visited=1,
            page_fingerprints=["fp"],
            terminal=True,
            collection_complete=True,
            relationship_complete=True,
            mutual_count=1,
            non_mutual_count=1,
            unknown_count=0,
        )
        resolver = RelationshipResolver(res)

        km = resolver.resolve("known_mutual")
        self.assertEqual(km.rel_type, RelationshipType.MUTUAL)

        kn = resolver.resolve("known_neighbor")
        self.assertEqual(kn.rel_type, RelationshipType.NON_MUTUAL)

        unknown_stranger = resolver.resolve("stranger_blog")
        self.assertEqual(unknown_stranger.rel_type, RelationshipType.UNKNOWN)
        self.assertEqual(unknown_stranger.evidence, "not_in_collected_buddy_list")

    def test_controller_zero_eligible_mutual_exits_immediately_no_feed_call(self):
        """Controller stops with COMPLETED (NO_ELIGIBLE_MUTUAL_NEIGHBORS) when verified mutual is 0."""
        from app.controller import FeedController

        cfg = {
            "feed_source": FeedSourceType.NEIGHBOR.value,
            "neighbor_mutual_only": True,
            "my_blog_id": "test_my_id",
            "max_feed_items": 10,
        }
        controller = FeedController(config=cfg, history=MagicMock())

        buddies = {
            f"user_{i}": BuddyInfo(f"user_{i}", f"U{i}", "", "", "이웃", None, "26.01.01", buddy_type_evidence="text:이웃")
            for i in range(5)
        }
        res = BuddyCollectionResult(
            buddies=buddies,
            state="complete",
            expected_total=5,
            collected_total=5,
            pages_visited=1,
            page_fingerprints=["fp"],
            terminal=True,
            collection_complete=True,
            relationship_complete=True,
            mutual_count=0,
            non_mutual_count=5,
            unknown_count=0,
        )

        mock_sess = MagicMock()
        mock_sess.context = MagicMock()
        mock_sess.is_context_alive.return_value = True
        mock_sess.get_detail_page.return_value = MagicMock()
        mock_sess.get_feed_page.return_value = MagicMock()

        with patch("app.controller.BrowserSession", return_value=mock_sess):
            with patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])):
                with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=res):
                    with patch("app.controller.NeighborFeedSource") as mock_source_cls:
                        mock_src = mock_source_cls.return_value
                        controller._run()
                        # Feed discover_posts was NEVER called because mutual is 0
                        mock_src.discover_posts.assert_not_called()

        st = controller.state_mgr.get_state()
        self.assertEqual(st.current_state, FeedState.COMPLETED)
        self.assertIn("NO_ELIGIBLE_MUTUAL_NEIGHBORS", st.message)

    def test_controller_aborts_on_relationship_parse_incomplete(self):
        """Controller stops with ERROR when relationship parsing has unknown items."""
        from app.controller import FeedController

        cfg = {
            "feed_source": FeedSourceType.NEIGHBOR.value,
            "neighbor_mutual_only": True,
            "my_blog_id": "test_my_id",
            "max_feed_items": 10,
        }
        controller = FeedController(config=cfg, history=MagicMock())

        buddies = {
            "u1": BuddyInfo("u1", "U1", "", "", "unknown", None, "26.01.01", buddy_type_evidence="unresolved")
        }
        res = BuddyCollectionResult(
            buddies=buddies,
            state="partial",
            expected_total=1,
            collected_total=1,
            pages_visited=1,
            page_fingerprints=["fp"],
            terminal=True,
            collection_complete=True,
            relationship_complete=False,
            mutual_count=0,
            non_mutual_count=0,
            unknown_count=1,
            quality_issues=["relationship_parse_incomplete"],
        )

        mock_sess = MagicMock()
        mock_sess.context = MagicMock()
        mock_sess.is_context_alive.return_value = True
        mock_sess.get_detail_page.return_value = MagicMock()

        with patch("app.controller.BrowserSession", return_value=mock_sess):
            with patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])):
                with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=res):
                    controller._run()

        st = controller.state_mgr.get_state()
        self.assertEqual(st.current_state, FeedState.ERROR)
        self.assertIn("서로이웃 목록 확인 실패", st.message)


if __name__ == "__main__":
    unittest.main()
