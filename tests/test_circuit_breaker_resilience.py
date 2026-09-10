import unittest
import threading
from app.controller import FeedController
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, FeedSourceType
)
from app.state import StateManager, FeedState


class TestCircuitBreakerResilience(unittest.TestCase):
    def test_failure_sequence_with_random_skip_trips_circuit_breaker(self):
        """실패 -> 랜덤 제외 -> 실패 -> 실패에서 연결 실패 누적이 유지되고 작업이 일시정지됨을 검증"""
        config = {"gemini_consecutive_failure_limit": 3}
        state_mgr = StateManager()
        pause_event = threading.Event()

        controller = FeedController(
            config=config,
            history=None,
            state_mgr=state_mgr,
            pause_event=pause_event
        )

        post1 = FeedPost(key="b1:1", blog_id="b1", log_no="1", title="글 1", url="https://m.blog.naver.com/b1/1", source=FeedSourceType.NEIGHBOR)
        post2 = FeedPost(key="b2:2", blog_id="b2", log_no="2", title="글 2", url="https://m.blog.naver.com/b2/2", source=FeedSourceType.NEIGHBOR)
        post3 = FeedPost(key="b3:3", blog_id="b3", log_no="3", title="글 3", url="https://m.blog.naver.com/b3/3", source=FeedSourceType.NEIGHBOR)
        post4 = FeedPost(key="b4:4", blog_id="b4", log_no="4", title="글 4", url="https://m.blog.naver.com/b4/4", source=FeedSourceType.NEIGHBOR)

        like_ok = LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)

        # 1. 실패 (Gemini Timeout)
        res1 = PostProcessResult(
            post=post1,
            like_result=like_ok,
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="timeout")
        )
        controller._handle_post_result(res1)
        self.assertEqual(controller.consecutive_gemini_failures, 1)
        self.assertFalse(pause_event.is_set())

        # 2. 랜덤 제외 (표본 탈락) -> 실패 카운트가 0으로 초기화되지 않고 1로 유지되어야 함!
        res2 = PostProcessResult(
            post=post2,
            like_result=like_ok,
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="random_sample_excluded")
        )
        controller._handle_post_result(res2)
        self.assertEqual(controller.consecutive_gemini_failures, 1, "랜덤 제외는 Gemini 실패 카운트를 리셋하지 않아야 함")
        self.assertFalse(pause_event.is_set())

        # 2-b. 자료 부족(context_insufficient) -> 역시 카운트 증가도 리셋도 하지 않음
        post2b = FeedPost(key="b2b:2b", blog_id="b2b", log_no="2b", title="글 2b", url="https://m.blog.naver.com/b2b/2b", source=FeedSourceType.NEIGHBOR)
        res2b = PostProcessResult(
            post=post2b,
            like_result=like_ok,
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="context_insufficient")
        )
        controller._handle_post_result(res2b)
        self.assertEqual(controller.consecutive_gemini_failures, 1, "자료 부족(context_insufficient)은 통신 실패가 아니므로 카운트를 건드리지 않아야 함")
        self.assertFalse(pause_event.is_set())

        # 3. 실패 (Gemini Publish Rejected)
        res3 = PostProcessResult(
            post=post3,
            like_result=like_ok,
            comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="publish_rejected")
        )
        controller._handle_post_result(res3)
        self.assertEqual(controller.consecutive_gemini_failures, 2)
        self.assertFalse(pause_event.is_set())

        # 4. 실패 (Gemini Response Timeout) -> 3회 누적으로 회로차단기 일시정지 트리거!
        res4 = PostProcessResult(
            post=post4,
            like_result=like_ok,
            comment_result=CommentProcessResult(status=CommentSubmitState.FAILED, error="gemini_failed:response_timeout")
        )
        controller._handle_post_result(res4)
        self.assertEqual(controller.consecutive_gemini_failures, 3)
        self.assertTrue(pause_event.is_set(), "3회 누적 실패 시 pause_event가 설정되어야 함")
        self.assertEqual(state_mgr.get_state().current_state, FeedState.PAUSED)
        self.assertEqual(state_mgr.get_state().pause_reason, "gemini_circuit_breaker")


if __name__ == "__main__":
    unittest.main()
