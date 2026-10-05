from unittest.mock import MagicMock, patch

import pytest

from app.feed_comment_policy import FeedCommentPolicy
from app.controller import FeedController
from app.models import CommentProcessResult, CommentSubmitState, FeedPost, FeedSourceType, PostProcessResult
from app.run_control import StopRequestedException
from app.state import StateManager
from naver.sources import NeighborFeedSource, RecommendationFeedSource


def post(blog="neighbor", number=1):
    return FeedPost(key=f"{blog}:{number}", blog_id=blog, source=FeedSourceType.NEIGHBOR,
                    url=f"https://m.blog.naver.com/{blog}/{number}")


def result(blog, number, status=CommentSubmitState.SUBMITTED, already_present=False):
    return PostProcessResult(post(blog, number), comment_result=CommentProcessResult(
        status=status, already_present=already_present))


def test_quota_is_run_local_case_insensitive_and_counts_distinct_posts():
    policy = FeedCommentPolicy()
    first = result("Neighbor", 1)
    policy.record_result(first)
    policy.record_result(first)
    assert policy.can_comment(post("neighbor", 2))
    policy.record_result(result("neighbor", 2))
    assert not policy.can_comment(post("NEIGHBOR", 3))
    assert policy.can_comment(post("another", 3))
    assert FeedCommentPolicy().can_comment(post("neighbor", 3))


def test_configured_blog_limit_replaces_default():
    policy = FeedCommentPolicy(blog_limit=1)
    policy.record_result(result("neighbor", 1))
    assert not policy.can_comment(post("neighbor", 2))
    assert FeedCommentPolicy(blog_limit="bad").blog_limit == 2


def test_failed_skipped_and_existing_comments_do_not_consume_quota():
    policy = FeedCommentPolicy()
    for number, status in enumerate((CommentSubmitState.FAILED, CommentSubmitState.SKIPPED, CommentSubmitState.NONE), 1):
        policy.record_result(result("neighbor", number, status))
    policy.record_result(result("neighbor", 4, already_present=True))
    assert policy.can_comment(post())
    assert len(policy.comment_slots["neighbor"]) == 0
    assert len(policy.previous_comment_keys) == 1


def test_unknown_reserves_slot_without_becoming_new_or_previous_comment():
    policy = FeedCommentPolicy()
    policy.record_result(result("neighbor", 1, CommentSubmitState.SUBMISSION_UNKNOWN))
    policy.record_result(result("neighbor", 2))
    assert not policy.can_comment(post("neighbor", 3))
    assert len(policy.new_comment_keys) == 1
    assert not policy.previous_comment_keys


@pytest.mark.parametrize("status", [CommentSubmitState.SUBMITTED, CommentSubmitState.SUBMISSION_UNKNOWN])
def test_comment_committed_before_later_exception_is_preserved(status):
    current = result("neighbor", 1, status)
    comment = FeedController._preserve_comment_result(current, current.post, RuntimeError("late error"))
    assert comment is current.comment_result
    other = FeedController._preserve_comment_result(current, post("neighbor", 2), RuntimeError("next post"))
    assert other.status == CommentSubmitState.FAILED


def test_previous_comments_are_statistics_only_and_deduplicated():
    policy = FeedCommentPolicy()
    policy.record_result(result("new", 1))
    assert not policy.observe_previous(post("new", 1))
    for number in range(1, 11):
        assert policy.observe_previous(post("old", number))
        assert not policy.observe_previous(post("old", number))
    assert len(policy.previous_comment_keys) == 10


def test_controller_connects_neighbor_source_to_the_same_run_control():
    config = {"feed_source": "neighbor", "neighbor_mutual_only": False,
              "max_feed_items": 52, "gemini_web_enabled": False, "campaign_id": "test"}
    history = MagicMock()
    history.get_unconfirmed_posts.return_value = {}
    with patch("app.controller.SamplingHistoryManager"), \
         patch("app.controller.BrowserSession"), \
         patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
         patch("app.controller.PostProcessor"), \
         patch("app.controller.NeighborFeedSource") as source:
        source.return_value.discover_posts.return_value = []
        source.return_value.is_exhausted.return_value = True
        controller = FeedController(config, history, StateManager())
        controller.run()
        assert source.call_args.kwargs["run_control"] is controller.run_control
        assert source.call_args.kwargs["pause_event"] is controller.pause_event
        assert source.call_args.kwargs["stop_event"] is controller.stop_event


@pytest.mark.parametrize("source_type", [NeighborFeedSource, RecommendationFeedSource])
def test_feed_load_waits_for_dom_growth_without_using_mouse_position(source_type):
    page = MagicMock()
    page.evaluate.side_effect = [
        {"cards": 4, "tail": "old", "top": 0, "height": 1000},
        {"cards": 4, "tail": "old"},
        {"cards": 8, "tail": "new"},
    ]
    control = MagicMock()
    source = source_type(page, run_control=control)
    assert source.load_more()
    assert page.evaluate.call_count == 3
    assert control.interruptible_wait.call_count == 2
    assert "document.scrollingElement" in page.evaluate.call_args_list[0].args[0]
    page.mouse.wheel.assert_not_called()


@pytest.mark.parametrize("source_type", [NeighborFeedSource, RecommendationFeedSource])
def test_feed_load_stop_during_render_is_not_swallowed(source_type):
    page = MagicMock()
    control = MagicMock()
    control.interruptible_wait.side_effect = StopRequestedException()
    source = source_type(page, run_control=control)
    with pytest.raises(StopRequestedException):
        source.load_more()
    assert page.evaluate.call_count == 1


@pytest.mark.parametrize("source_type", [NeighborFeedSource, RecommendationFeedSource])
def test_pause_checkpoint_blocks_scroll_even_with_zero_wait(source_type):
    page = MagicMock()
    control = MagicMock()
    control.checkpoint.side_effect = StopRequestedException()
    source = source_type(page, run_control=control)
    with pytest.raises(StopRequestedException):
        source.load_more()
    page.evaluate.assert_not_called()
