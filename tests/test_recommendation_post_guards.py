import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models import (
    CommentProcessResult, FeedPost, FeedSourceType, LikeProcessResult, LikeState,
    PostEligibilityResult, PostProcessResult, CommentSubmitState,
)
from app.processor import PostProcessor
from app.controller import FeedController
from app.run_control import RunControl, StopRequestedException
from app.state import StateManager
from services.history import HistoryStore
from services.post_eligibility import PostEligibilityService, ProfileMetricsReader, ProfileMetric, extract_daily_visitors
from services.blog_neighbor_count import extract_neighbor_count
from naver.sources import RecommendationFeedSource


def post(blog="a", number=1):
    return FeedPost(key=f"{blog}:{number}", url=f"https://m.blog.naver.com/{blog}/{number}",
                    blog_id=blog, log_no=str(number), source=FeedSourceType.RECOMMENDATION)


def cfg(**overrides):
    return {"recommendation_neighbor_count_max": 1000, "like_count_skip_threshold": 130,
            "daily_visitor_skip_threshold": 250, "topic_filter_enabled": False, **overrides}


class Page:
    def __init__(self, neighbors=1000, visitors=250, url="https://m.blog.naver.com/a", detail=False):
        self.url = url
        self.neighbors = neighbors
        self.visitors = visitors
        self.detail = detail
        self.moves = []
        self.on_goto = None

    def goto(self, url, **kwargs):
        self.moves.append(url)
        self.url = url
        if self.on_goto:
            self.on_goto()

    def evaluate(self, js):
        if "profileBlockCount" in js:
            candidates = [] if self.detail or self.neighbors is None else [
                {"text": f"{self.neighbors:,}명의 이웃", "kind": "profile_block"}]
            return {"candidates": candidates, "notPublic": self.neighbors is None and not self.detail}
        return [] if self.detail or self.visitors is None else [f"오늘 {self.visitors} 전체 999999"]

    def is_closed(self):
        return False


class TestRecommendationPostGuards(unittest.TestCase):
    def test_neighbor_compact_units_and_digit_spacing(self):
        for raw, expected in [("2, 851명의 이웃", 2851), ("이웃 1.2K명", 1200), ("이웃 1.2만명", 12000)]:
            with self.subTest(raw=raw):
                self.assertEqual(extract_neighbor_count([{"text": raw, "kind": "profile_block"}])[0], expected)

    def test_neighbor_conflict_and_hidden_values(self):
        candidates = [{"text": "2,851명의 이웃", "kind": "profile_block"},
                      {"text": "이웃 12명", "kind": "profile_block", "visible": False}]
        self.assertEqual(extract_neighbor_count(candidates)[0], 2851)
        candidates.append({"text": "이웃 30명", "kind": "profile_block"})
        self.assertEqual(extract_neighbor_count(candidates)[2], "conflicting_neighbor_counts")

    def test_today_is_separate_from_total(self):
        self.assertEqual(extract_daily_visitors(["오늘 250 전체 25,999"])[0], 250)
        self.assertEqual(extract_daily_visitors(["TOTAL 900 TODAY 251"])[0], 251)
        self.assertIsNone(extract_daily_visitors(["전체 999999"])[0])
        self.assertEqual(extract_daily_visitors(["오늘 2", "오늘 3"])[2], "conflicting_daily_visitors")
        self.assertEqual(extract_daily_visitors(["방문자 비공개"])[2], "not_public")
        self.assertNotEqual(extract_daily_visitors(["이웃 비공개"])[2], "not_public")

    def evaluate(self, neighbors=1000, likes=129, visitors=250, **overrides):
        service = PostEligibilityService(cfg(**overrides))
        detail = Page(detail=True, url=post().url)
        home = Page(neighbors, visitors)
        with patch("services.post_eligibility.MobileDOMResolver.get_like_count_text", return_value=str(likes) if likes is not None else None):
            result = service.evaluate(detail, home, post())
        return result, service, home

    def test_boundaries(self):
        self.assertTrue(self.evaluate()[0].allowed)
        for arguments, reason in [({"neighbors": 1001}, "neighbor_count_exceeded"),
                                  ({"likes": 130}, "like_count_exceeded"),
                                  ({"visitors": 251}, "daily_visitors_exceeded"),
                                  ({"neighbors": None}, "unknown_neighbor_count"),
                                  ({"likes": None}, "unknown_like_count")]:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.evaluate(**arguments)[0].reason, reason)

    def test_only_one_home_navigation_for_both_metrics(self):
        result, _, home = self.evaluate()
        self.assertTrue(result.allowed)
        self.assertEqual(len(home.moves), 1)

    def test_first_failed_guard_prevents_later_read(self):
        service = PostEligibilityService(cfg())
        service.reader.get = MagicMock(return_value={"neighbors": ProfileMetric(2851, "2,851명의 이웃")})
        with patch("services.post_eligibility.MobileDOMResolver.get_like_count_text") as likes:
            result = service.evaluate(Page(), Page(), post())
        self.assertEqual(result.reason, "neighbor_count_exceeded")
        likes.assert_not_called()
        service.reader.get.assert_called_once()

    def test_disabled_guards_never_query_profiles(self):
        result, service, home = self.evaluate(recommendation_neighbor_count_max=0,
            like_popularity_guard_enabled=False, daily_visitor_guard_enabled=False)
        self.assertTrue(result.allowed)
        self.assertEqual(home.moves, [])
        self.assertEqual(service.reader.lookup_blogs, set())

    def test_reaction_count_render_retry(self):
        service = PostEligibilityService(cfg(recommendation_neighbor_count_max=0,
                                            daily_visitor_guard_enabled=False))
        with patch("services.post_eligibility.MobileDOMResolver.get_like_count_text", side_effect=[None, "6"]) as read:
            result = service.evaluate(Page(), None, post())
        self.assertTrue(result.allowed)
        self.assertEqual(result.metrics["likes"]["value"], 6)
        self.assertEqual(read.call_count, 2)

    def test_stop_during_reaction_count_render_retry(self):
        control = RunControl()
        service = PostEligibilityService(cfg(recommendation_neighbor_count_max=0,
                                            daily_visitor_guard_enabled=False), control)
        def stop_after_read(page):
            control.request_stop("test")
            return None
        with patch("services.post_eligibility.MobileDOMResolver.get_like_count_text", side_effect=stop_after_read) as read:
            with self.assertRaises(StopRequestedException):
                service.evaluate(Page(), None, post())
        self.assertEqual(read.call_count, 1)

    def test_positive_and_private_cache(self):
        service = PostEligibilityService(cfg())
        detail, home = Page(detail=True, url=post().url), Page()
        with patch("services.post_eligibility.MobileDOMResolver.get_like_count_text", return_value="0"):
            service.evaluate(detail, home, post())
            result = service.evaluate(detail, home, post(number=2))
        self.assertEqual(home.moves, ["https://m.blog.naver.com/a"])
        self.assertTrue(result.metrics["neighbors"]["cached"])
        self.assertEqual(len(service.decisions), 2)

    def test_explicit_private_neighbor_result_is_cached(self):
        reader = ProfileMetricsReader()
        detail, home = Page(detail=True, url=post().url), Page(neighbors=None)
        self.assertEqual(reader.get(detail, home, "a", True, False)["neighbors"].error, "not_public")
        cached = reader.get(detail, home, "a", True, False)["neighbors"]
        self.assertTrue(cached.cached)
        self.assertEqual(len(home.moves), 1)

    def test_profile_owner_mismatch_not_cached(self):
        reader = ProfileMetricsReader()
        home = Page()
        home.on_goto = lambda: setattr(home, "url", "https://m.blog.naver.com/another")
        result = reader.get(Page(detail=True, url=post().url), home, "a", True, False)
        self.assertEqual(result["neighbors"].error, "profile_owner_mismatch")
        self.assertEqual(reader.cache, {})

    def test_pause_after_navigation_blocks_read_until_resume(self):
        control = RunControl()
        reader = ProfileMetricsReader(control)
        home = Page()
        home.on_goto = lambda: control.request_pause("test")
        done = threading.Event()
        reads = []
        original = home.evaluate
        home.evaluate = lambda js: (reads.append(js), original(js))[1]
        thread = threading.Thread(target=lambda: (reader.get(Page(detail=True, url=post().url), home, "a", True, False), done.set()))
        thread.start()
        try:
            self.assertTrue(control.pause_ack_event.wait(1))
            self.assertEqual(reads, [])
            self.assertFalse(done.is_set())
        finally:
            control.request_resume()
            thread.join(2)
        self.assertTrue(done.is_set())

    def test_stop_never_negative_cached(self):
        control = RunControl()
        reader = ProfileMetricsReader(control)
        control.request_stop()
        with self.assertRaises(StopRequestedException):
            reader.get(Page(), Page(), "a", True, False)
        self.assertEqual(reader.cache, {})

    def test_excluded_post_has_no_gemini_or_like_or_comment_actions(self):
        for like_enabled in (False, True):
            with self.subTest(like_enabled=like_enabled):
                processor = PostProcessor(cfg(), like_enabled=like_enabled, state_manager=StateManager())
                processor.post_eligibility_service.evaluate = MagicMock(return_value=PostEligibilityResult(False, "neighbor_count_exceeded"))
                page = MagicMock()
                with patch("app.processor.TargetPostGuard.verify"), \
                     patch("app.processor.interruptible_wait"), \
                     patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title="맛집", excerpt="본문")), \
                     patch.object(processor, "_prepare_comment_generation_context") as gemini, \
                     patch("app.processor.LikeTransactionService.execute_like_transaction") as like, \
                     patch("app.processor.CommentInteractionService.open_comment_layer") as comment:
                    result = processor.process(page, post())
                self.assertFalse(result.eligibility.allowed)
                self.assertEqual(result.comment_result.status, CommentSubmitState.SKIPPED)
                gemini.assert_not_called()
                like.assert_not_called()
                comment.assert_not_called()

    def test_history_optional_eligibility_does_not_mark_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HistoryStore(str(Path(folder) / "history.json"))
            result = PostProcessResult(post(), eligibility=PostEligibilityResult(False, "unknown_neighbor_count"))
            history.record_result(result)
            reopened = HistoryStore(history.file_path)
            self.assertFalse(reopened.is_liked(post().key))
            self.assertFalse(reopened.is_comment_submitted(post().key))
            self.assertFalse(reopened.posts[post().key]["eligibility"]["allowed"])

    def run_controller(self, target, allowed_after, batch=False, crash_after_like=False,
                       candidates=None, previous=(), liked=(), statuses=None, new_likes=False,
                       server_previous=(), load_stalled=False, blog_limit=2):
        config = cfg(feed_source="recommendation", max_feed_items=target, campaign_id="test",
                     like_enabled=new_likes, comment_enabled=True, gemini_web_enabled=False,
                     auto_comment_submit_enabled=False, comment_blog_limit=blog_limit)
        history = MagicMock()
        history.is_liked.return_value = False
        history.is_comment_submitted.return_value = False
        history.is_liked.side_effect = lambda key: key in liked
        history.is_comment_submitted.side_effect = lambda key: key in previous
        history.is_comment_unconfirmed.return_value = False
        history.get_unconfirmed_posts.return_value = {}
        history.get_recent_submitted_comments.return_value = []
        order = []
        source_ref = []
        def discover(source):
            source_ref[:] = [source]
            if candidates is not None:
                unseen = [p for p in candidates if p.key not in source.examined_keys]
                if not unseen:
                    return []
                source.examined_keys.add(unseen[0].key)
                order.append(("candidate", int(unseen[0].log_no)))
                return unseen[:1]
            if batch:
                if source.examined_keys:
                    return []
                batch_candidates = [post(f"blog{i}", i) for i in range(1, source.max_items + 1)]
                source.examined_keys.update(p.key for p in batch_candidates)
                return batch_candidates
            n = len(source.examined_keys) + 1
            if n > source.max_items:
                return []
            candidate = post(f"blog{n}", n)
            source.examined_keys.add(candidate.key)
            order.append(("candidate", n))
            return [candidate]
        with patch("app.controller.SamplingHistoryManager"), \
             patch("app.controller.BrowserSession") as session_cls, \
             patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.PostProcessor") as processor_cls, \
             patch.object(RecommendationFeedSource, "open"), \
             patch.object(RecommendationFeedSource, "load_more", return_value=load_stalled), \
             patch.object(RecommendationFeedSource, "discover_posts", discover):
            controller = FeedController(config, history, StateManager())
            controller.pacing.wait_next_post = MagicMock(return_value=MagicMock(stopped=False, skipped=False))
            controller.pacing.maybe_pause = MagicMock(return_value=None)
            processor = processor_cls.return_value
            processor.current_result = None
            processor.post_eligibility_service = PostEligibilityService(config)
            def process(page, candidate, **kwargs):
                n = int(candidate.log_no)
                order.append(("process", n))
                result = PostProcessResult(candidate, eligibility=PostEligibilityResult(n > allowed_after, "test"))
                if n > allowed_after:
                    # 허용된 글만 실제 댓글 등록이 일어난 것으로 모의 (max_items 목표 카운트 대상)
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SUBMITTED)
                plan = kwargs["action_plan"]
                if statuses is not None:
                    result.comment_result = CommentProcessResult(status=statuses.get(n, CommentSubmitState.SUBMITTED))
                if not plan.process_comment:
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SKIPPED)
                if n in server_previous:
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.SUBMITTED, already_present=True)
                if new_likes and plan.process_like:
                    result.like_result = LikeProcessResult(action_taken=True, state_after=LikeState.LIKED)
                processor.current_result = result
                processor.post_eligibility_service.decisions[candidate.key] = result.eligibility
                if crash_after_like:
                    result.like_result = LikeProcessResult(action_taken=True, state_after=LikeState.LIKED)
                    result.comment_result = CommentProcessResult(status=CommentSubmitState.FAILED)
                    raise RuntimeError("comment failed after verified like")
                return result
            processor.process.side_effect = process
            controller.run()
            session_cls.return_value.get_stats_page.assert_called_once()
            return controller, order, source_ref[0]

    def test_controller_exclusions_preserve_target_and_pacing(self):
        controller, order, source = self.run_controller(target=2, allowed_after=3)
        self.assertEqual(order, [event for n in range(1, 6) for event in (("candidate", n), ("process", n))])
        self.assertEqual(controller.pacing.wait_next_post.call_count, 5)
        self.assertIn("target_reached", controller.state_mgr.get_state().message)
        self.assertEqual(source.max_items, 50)  # max(target*5, 50): 목표는 실제 댓글/공감 성공 수

    def test_effective_action_counts_only_submitted_comment_or_new_like(self):
        keys = set()
        reg = FeedController._register_effective_action
        def res(comment=CommentSubmitState.SKIPPED, like_taken=False):
            return PostProcessResult(
                post(),
                like_result=LikeProcessResult(action_taken=like_taken, state_after=LikeState.LIKED if like_taken else LikeState.UNKNOWN),
                comment_result=CommentProcessResult(status=comment),
            )
        # 스킵/실패/결과불명/기존 공감(action 없음)은 목표를 소모하지 않는다
        for status in (CommentSubmitState.SKIPPED, CommentSubmitState.FAILED, CommentSubmitState.SUBMISSION_UNKNOWN):
            self.assertFalse(reg(res(status), post(), keys, 5))
        self.assertEqual(keys, set())
        # 댓글 등록 또는 공감 신규 성공은 카운트
        self.assertTrue(reg(res(CommentSubmitState.SUBMITTED), post("a", 1), keys, 5))
        self.assertTrue(reg(res(like_taken=True), post("b", 2), keys, 5))
        self.assertEqual(len(keys), 2)

    def test_failed_or_unknown_like_click_does_not_consume_target(self):
        for state in (LikeState.NOT_LIKED, LikeState.UNKNOWN):
            result = PostProcessResult(post(), like_result=LikeProcessResult(
                action_taken=True, state_after=state, error="postcondition_failed"))
            self.assertFalse(FeedController._register_effective_action(result, post(), set(), 5))

    def test_like_statistic_counts_only_new_verified_likes(self):
        with patch("app.controller.SamplingHistoryManager"):
            controller = FeedController(cfg(campaign_id="test"), MagicMock(), StateManager())
        for state, taken in ((LikeState.UNKNOWN, True), (LikeState.NOT_LIKED, True), (LikeState.LIKED, False)):
            controller._handle_post_result(PostProcessResult(post(), like_result=LikeProcessResult(
                action_taken=taken, state_after=state)))
        self.assertEqual(controller.like_success_count, 0)
        controller._handle_post_result(PostProcessResult(post(), like_result=LikeProcessResult(
            action_taken=True, state_after=LikeState.LIKED)))
        self.assertEqual(controller.like_success_count, 1)

    def test_effective_action_deduplicates_same_post(self):
        keys = set()
        result = PostProcessResult(post(), comment_result=CommentProcessResult(status=CommentSubmitState.SUBMITTED))
        self.assertTrue(FeedController._register_effective_action(result, post(), keys, 5))
        self.assertFalse(FeedController._register_effective_action(result, post(), keys, 5))

    def test_scan_limit_drains_collected_batch(self):
        controller, order, source = self.run_controller(target=2, allowed_after=48, batch=True)
        self.assertEqual(len(source.examined_keys), 50)
        self.assertEqual(order, [("process", i) for i in range(1, 51)])
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_verified_like_survives_comment_exception_in_target(self):
        controller, order, _ = self.run_controller(target=1, allowed_after=0, crash_after_like=True)
        self.assertEqual(order, [event for n in range(1, 4) for event in (("candidate", n), ("process", n))])
        self.assertEqual(controller.like_success_count, 3)
        self.assertEqual(controller.comment_submitted_count, 0)
        self.assertNotIn("target_reached", controller.state_mgr.get_state().message)

    def test_comment_goal_ignores_likes_and_failures(self):
        controller, order, _ = self.run_controller(
            target=2, allowed_after=0, new_likes=True,
            statuses={1: CommentSubmitState.FAILED, 2: CommentSubmitState.SKIPPED})
        self.assertEqual(order[-1], ("process", 4))
        self.assertEqual(controller.comment_submitted_count, 2)
        self.assertEqual(controller.like_success_count, 4)
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_blog_quota_limits_comments_not_likes(self):
        candidates = [post("same", i) for i in range(1, 5)] + [post("other", 5)]
        controller, order, _ = self.run_controller(3, 0, candidates=candidates, new_likes=True)
        self.assertEqual(controller.comment_submitted_count, 3)
        self.assertEqual(controller.like_success_count, 5)
        self.assertEqual(order[-1], ("process", 5))
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_controller_uses_saved_blog_limit(self):
        candidates = [post("same", 1), post("same", 2), post("other", 3)]
        controller, order, _ = self.run_controller(2, 0, candidates=candidates, new_likes=True, blog_limit=1)
        self.assertEqual(controller.comment_submitted_count, 2)
        self.assertEqual(controller.like_success_count, 3)
        self.assertEqual(order[-1], ("process", 3))

    def test_liked_only_and_previously_viewed_posts_still_get_comments(self):
        candidates = [post("a", 1), post("b", 2)]
        controller, order, _ = self.run_controller(2, 0, candidates=candidates, liked={candidates[0].key})
        self.assertEqual(controller.comment_submitted_count, 2)
        self.assertIn(("process", 1), order)
        self.assertIn(("process", 2), order)

    def test_five_previous_comments_do_not_stop_before_uncommented_posts(self):
        candidates = [post(f"a{i}", i) for i in range(1, 9)]
        previous = {candidates[i].key for i in (0, 2, 3, 5, 6)}
        controller, order, _ = self.run_controller(3, 0, candidates=candidates, previous=previous)
        self.assertEqual(controller.comment_submitted_count, 3)
        self.assertEqual(order[-1], ("process", 8))
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_server_existing_comments_do_not_consume_goal_or_blog_quota(self):
        candidates = [post("same", i) for i in range(1, 4)]
        controller, order, _ = self.run_controller(2, 0, candidates=candidates, server_previous={1})
        self.assertEqual(controller.comment_submitted_count, 2)
        self.assertEqual(order[-1], ("process", 3))
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_five_server_existing_comments_do_not_stop_discovery(self):
        candidates = [post(f"a{i}", i) for i in range(1, 7)]
        controller, order, _ = self.run_controller(1, 0, candidates=candidates, server_previous=set(range(1, 6)))
        self.assertEqual(controller.comment_submitted_count, 1)
        self.assertEqual(order[-1], ("process", 6))
        self.assertIn("target_reached", controller.state_mgr.get_state().message)

    def test_manual_skip_is_not_counted_as_failure(self):
        with patch("app.controller.SamplingHistoryManager"):
            controller = FeedController(cfg(campaign_id="test"), MagicMock(), StateManager())
        controller._handle_post_result(PostProcessResult(post(),
            like_result=LikeProcessResult(error="user_skipped"),
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")))
        self.assertEqual(controller.skipped_count, 1)
        self.assertEqual(controller.failed_count, 0)

    def test_stalled_feed_is_not_reported_as_no_new_posts(self):
        controller, _, _ = self.run_controller(52, 0, candidates=[post()], load_stalled=True)
        self.assertEqual(controller.comment_submitted_count, 1)
        self.assertIn("feed_loading_stalled", controller.state_mgr.get_state().message)

    def test_controller_stops_at_200_candidates_when_all_excluded(self):
        controller, order, source = self.run_controller(target=40, allowed_after=200)
        self.assertEqual(len(source.examined_keys), 200)
        self.assertEqual(order[-1], ("process", 200))
        self.assertEqual(controller.pacing.wait_next_post.call_count, 200)
        self.assertIn("scan_limit", controller.state_mgr.get_state().message)


if __name__ == "__main__":
    unittest.main()
