import unittest
from unittest.mock import MagicMock, patch
import threading

from app.feed_queue import FeedQueue
from app.models import (
    FeedPost, FeedSourceType, LikeState, CommentSubmitState,
    PostProcessResult, LikeProcessResult, CommentProcessResult
)
from app.controller import FeedController
from services.relationship_resolver import RelationshipType, RelationshipResolution


class TestFeedQueueAndSweepStreak(unittest.TestCase):
    def test_feed_queue_fifo_and_scroll_invariants(self):
        q = FeedQueue()
        p1 = FeedPost(key="p1", url="https://blog.naver.com/u/1", source=FeedSourceType.NEIGHBOR)
        p2 = FeedPost(key="p2", url="https://blog.naver.com/u/2", source=FeedSourceType.NEIGHBOR)
        p3 = FeedPost(key="p3", url="https://blog.naver.com/u/3", source=FeedSourceType.NEIGHBOR)

        # Initially empty and no active post -> can scroll
        self.assertTrue(q.is_empty())
        self.assertTrue(q.can_scroll())

        # Push items
        added = q.push([p1, p2, p3])
        self.assertEqual(added, 3)
        self.assertEqual(q.size(), 3)
        # Queue is not empty -> cannot scroll
        self.assertFalse(q.can_scroll())

        # Duplicate push is ignored
        added_dup = q.push([p1, p2])
        self.assertEqual(added_dup, 0)
        self.assertEqual(q.size(), 3)

        # FIFO order: p1 first
        popped1 = q.pop()
        self.assertEqual(popped1.key, "p1")
        q.set_active(popped1.key)
        self.assertTrue(q.has_active())
        self.assertFalse(q.can_scroll())

        # Pop remaining
        q.pop()
        q.pop()
        self.assertTrue(q.is_empty())
        # Empty, BUT active post still set -> cannot scroll!
        self.assertFalse(q.can_scroll())

        # Clear active post -> now can scroll!
        q.set_active(None)
        self.assertFalse(q.has_active())
        self.assertTrue(q.can_scroll())

    def test_sweep_streak_4_consecutive_already_liked_terminates(self):
        """
        4 consecutive posts with state_before == LikeState.LIKED terminates immediately
        with SWEEP_CONSECUTIVE_ALREADY_LIKED_4 without popping the 5th post or scrolling.
        """
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "feed_source": "neighbor",
            "feed_source_type": "neighbor",
            "neighbor_like_sweep_mode": True,
            "neighbor_mutual_only": True,
            "sweep_stop_consecutive_liked": 4,
            "max_feed_items": 20,
            "my_blog_id": "test_user",
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()

        controller = FeedController(
            config=config_mock,
            history=history_mock,
            state_mgr=state_mgr_mock,
        )

        posts = [
            FeedPost(key=f"post_{i}", url=f"https://blog.naver.com/u/{i}", blog_id="mutual_friend", source=FeedSourceType.NEIGHBOR)
            for i in range(1, 10)
        ]

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = posts
        source_mock.is_exhausted.return_value = False

        resolver_mock = MagicMock()
        resolver_mock.is_execution_blocked.return_value = (False, "")
        resolver_mock.get_mutual_blog_ids.return_value = {"mutual_friend"}
        resolver_mock.get_summary.return_value = {"total": 1, "mutual": 1, "non_mutual": 0, "unknown": 0}
        resolver_mock.resolve.return_value = RelationshipResolution(
            blog_id="mutual_friend",
            rel_type=RelationshipType.MUTUAL,
            evidence="buddy_table_direct_text"
        )

        processor_mock = MagicMock()
        # All posts return state_before == LIKED (already liked)
        def mock_process(detail_page, post, action_plan=None):
            return PostProcessResult(
                post=post,
                like_result=LikeProcessResult(
                    state_before=LikeState.LIKED,
                    action_taken=False,
                    state_after=LikeState.LIKED,
                ),
                comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED),
            )
        processor_mock.process.side_effect = mock_process

        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.BuddyListCollector.collect_all_buddies", return_value=MagicMock()), \
             patch("app.controller.NeighborFeedSource", return_value=source_mock), \
             patch("app.controller.RelationshipResolver", return_value=resolver_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # Exactly 4 posts processed
        self.assertEqual(processor_mock.process.call_count, 4)
        # Load more / scroll was never called because 4-streak terminated immediately
        self.assertEqual(source_mock.load_more.call_count, 0)
        # State manager updated with SWEEP_CONSECUTIVE_ALREADY_LIKED_4
        last_state_msg = [c.kwargs.get("message") for c in state_mgr_mock.update.call_args_list if "message" in c.kwargs]
        self.assertTrue(any("SWEEP_CONSECUTIVE_ALREADY_LIKED_4" in str(m) for m in last_state_msg))

    def test_sweep_streak_resets_on_not_liked_observation(self):
        """
        Observation of state_before == NOT_LIKED immediately sets streak to 0,
        regardless of whether the like action was successful, skipped, or failed.
        """
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "feed_source": "neighbor",
            "feed_source_type": "neighbor",
            "neighbor_like_sweep_mode": True,
            "neighbor_mutual_only": True,
            "sweep_stop_consecutive_liked": 4,
            "max_feed_items": 10,
            "my_blog_id": "test_user",
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()
        controller = FeedController(config=config_mock, history=history_mock, state_mgr=state_mgr_mock)

        # 3 LIKED posts, then 1 NOT_LIKED post (resets streak), then 4 LIKED posts (total 8 processed)
        posts = [
            FeedPost(key=f"p_{i}", url=f"https://blog.naver.com/u/{i}", blog_id="friend", source=FeedSourceType.NEIGHBOR)
            for i in range(1, 12)
        ]

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = posts
        source_mock.is_exhausted.return_value = False

        resolver_mock = MagicMock()
        resolver_mock.is_execution_blocked.return_value = (False, "")
        resolver_mock.get_mutual_blog_ids.return_value = {"friend"}
        resolver_mock.get_summary.return_value = {"total": 1, "mutual": 1, "non_mutual": 0, "unknown": 0}
        resolver_mock.resolve.return_value = RelationshipResolution(
            blog_id="friend",
            rel_type=RelationshipType.MUTUAL,
            evidence="direct"
        )

        call_idx = 0
        def mock_process(detail_page, post, action_plan=None):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 4:
                # 4th post observed as NOT_LIKED (new post!)
                return PostProcessResult(
                    post=post,
                    like_result=LikeProcessResult(
                        state_before=LikeState.NOT_LIKED,
                        action_taken=True,
                        state_after=LikeState.LIKED,
                    ),
                    comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED),
                )
            else:
                # All other posts already liked
                return PostProcessResult(
                    post=post,
                    like_result=LikeProcessResult(
                        state_before=LikeState.LIKED,
                        action_taken=False,
                        state_after=LikeState.LIKED,
                    ),
                    comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED),
                )

        processor_mock = MagicMock()
        processor_mock.process.side_effect = mock_process
        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.BuddyListCollector.collect_all_buddies", return_value=MagicMock()), \
             patch("app.controller.NeighborFeedSource", return_value=source_mock), \
             patch("app.controller.RelationshipResolver", return_value=resolver_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # Streak progression:
        # p1: LIKED (streak=1)
        # p2: LIKED (streak=2)
        # p3: LIKED (streak=3)
        # p4: NOT_LIKED (streak resets to 0!)
        # p5: LIKED (streak=1)
        # p6: LIKED (streak=2)
        # p7: LIKED (streak=3)
        # p8: LIKED (streak=4 -> EXIT!)
        self.assertEqual(processor_mock.process.call_count, 8)

    def test_sweep_streak_non_mutual_does_not_change_streak(self):
        """
        Confirmed NON_MUTUAL neighbor does not alter sweep streak (neither incremented nor reset).
        """
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "feed_source": "neighbor",
            "feed_source_type": "neighbor",
            "neighbor_like_sweep_mode": True,
            "neighbor_mutual_only": True,
            "sweep_stop_consecutive_liked": 4,
            "max_feed_items": 10,
            "my_blog_id": "test_user",
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()
        controller = FeedController(config=config_mock, history=history_mock, state_mgr=state_mgr_mock)

        # 2 LIKED posts, 1 NON_MUTUAL post (skipped), 2 LIKED posts -> total 4 LIKED processed -> terminates!
        posts = [
            FeedPost(key="p1", url="https://blog.naver.com/u/1", blog_id="mutual_1", source=FeedSourceType.NEIGHBOR),
            FeedPost(key="p2", url="https://blog.naver.com/u/2", blog_id="mutual_1", source=FeedSourceType.NEIGHBOR),
            FeedPost(key="p3", url="https://blog.naver.com/u/3", blog_id="stranger_1", source=FeedSourceType.NEIGHBOR),  # non-mutual
            FeedPost(key="p4", url="https://blog.naver.com/u/4", blog_id="mutual_1", source=FeedSourceType.NEIGHBOR),
            FeedPost(key="p5", url="https://blog.naver.com/u/5", blog_id="mutual_1", source=FeedSourceType.NEIGHBOR),
            FeedPost(key="p6", url="https://blog.naver.com/u/6", blog_id="mutual_1", source=FeedSourceType.NEIGHBOR),
        ]

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = posts
        source_mock.is_exhausted.return_value = False

        resolver_mock = MagicMock()
        resolver_mock.is_execution_blocked.return_value = (False, "")
        resolver_mock.get_mutual_blog_ids.return_value = {"mutual_1"}
        resolver_mock.get_summary.return_value = {"total": 2, "mutual": 1, "non_mutual": 1, "unknown": 0}

        def mock_resolve(blog_id):
            if blog_id == "mutual_1":
                return RelationshipResolution(blog_id="mutual_1", rel_type=RelationshipType.MUTUAL, evidence="text")
            return RelationshipResolution(blog_id="stranger_1", rel_type=RelationshipType.NON_MUTUAL, evidence="text")

        resolver_mock.resolve.side_effect = mock_resolve

        processor_mock = MagicMock()
        processor_mock.process.return_value = PostProcessResult(
            post=posts[0],
            like_result=LikeProcessResult(
                state_before=LikeState.LIKED,
                action_taken=False,
                state_after=LikeState.LIKED,
            ),
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED),
        )

        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.BuddyListCollector.collect_all_buddies", return_value=MagicMock()), \
             patch("app.controller.NeighborFeedSource", return_value=source_mock), \
             patch("app.controller.RelationshipResolver", return_value=resolver_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # p1 (liked, streak=1)
        # p2 (liked, streak=2)
        # p3 (stranger skipped, streak remains 2)
        # p4 (liked, streak=3)
        # p5 (liked, streak=4 -> terminates!)
        # p6 should never be processed
        self.assertEqual(processor_mock.process.call_count, 4)


if __name__ == "__main__":
    unittest.main()
