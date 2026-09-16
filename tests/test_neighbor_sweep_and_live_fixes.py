"""
Unit tests for:
1. NATIVE_CLICK FinalQualityGate bypass vs USER_ENTER gate blocking
2. Logger fail-safe against OSError: [Errno 5] Input/output error
3. BrowserSession unexpected context close diagnostics and single safe close
4. Neighbor feed mutual-only filtering and like sweep mode (0 Gemini, 0 comment, detail goto 0 for non-mutual)
5. max_items target preservation when non-mutual cards are skipped
"""
import io
import logging
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    FeedSourceType, FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, SubmitOrigin, UserAction
)
from services.like_transaction import LikeConfidence, LikeStateResult
from app.processor import PostProcessor
from app.state import StateManager, FeedState
from browser.session import BrowserSession
from naver.interaction import CommentInteractionService
from services.buddy_list_collector import BuddyCollectionResult, BuddyInfo
from src.logger import BotLogger


class TestNativeSubmitQualityGate(unittest.TestCase):
    """P0-1 & P0-2: Verify NATIVE_CLICK bypasses FinalQualityGate blocking while USER_ENTER enforces it."""

    def test_native_click_with_period_bypasses_quality_gate(self):
        """User edited text with period in browser and clicked native submit button -> quality gate is not a blocker."""
        mock_page = MagicMock()
        mock_frame = MagicMock()
        mock_frame.evaluate.return_value = {"visible": True, "text": "정말 유익한 글이네요. 잘 보고 갑니다."}

        with patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            with patch("naver.comment_guard.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=MagicMock()):
                with patch("naver.comment_guard.ServerCommentDuplicateGuard.scan_page_for_my_comment") as mock_scan:
                    from naver.comment_guard import CommentPresenceState
                    mock_scan.return_value = MagicMock(state=CommentPresenceState.PRESENT, list_complete=True)

                    outcome = CommentInteractionService.submit_and_verify(
                        mock_page,
                        final_text="정말 유익한 글이네요. 잘 보고 갑니다.",  # Contains period which normally fails forbidden_period
                        origin=SubmitOrigin.NATIVE_CLICK,
                    )

                    self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
                    self.assertEqual(outcome.reason, "server_verified")
                    self.assertFalse(outcome.click_dispatched)

    def test_auto_submit_with_period_is_blocked_by_quality_gate(self):
        """Auto submit or unedited submission with period is blocked before click."""
        mock_page = MagicMock()
        outcome = CommentInteractionService.submit_and_verify(
            mock_page,
            final_text="정말 유익한 글이네요. 잘 보고 갑니다.",  # Contains period
            origin=SubmitOrigin.AUTO_TIMER,
        )
        self.assertEqual(outcome.state, CommentSubmitState.PRECLICK_BLOCKED)
        self.assertEqual(outcome.reason, "forbidden_period")
        self.assertTrue(outcome.retryable_same_post)
        self.assertFalse(outcome.click_dispatched)


class TestLoggerFailSafe(unittest.TestCase):
    """P0-3: Verify BotLogger FileHandler handles OSError(5) gracefully without crashing worker flow."""

    def test_logger_file_io_error_fallback(self):
        test_dir = "/tmp/test_bot_logger_dir"
        os.makedirs(test_dir, exist_ok=True)
        bot_logger = BotLogger(log_dir=test_dir)

        gui_messages = []
        bot_logger.register_gui_callback(lambda msg: gui_messages.append(msg))

        # 1. Normal log
        bot_logger.log("Normal log message", "INFO")
        self.assertFalse(bot_logger._file_logging_failed)

        # 2. Simulate OSError: [Errno 5] Input/output error
        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            bot_logger.handle_file_error(OSError(5, "Input/output error"))

        self.assertTrue(bot_logger._file_logging_failed)
        self.assertIsNone(bot_logger.file_handler)
        self.assertIn("[LOGGER][FILE_IO_ERROR] errno=5 fallback=console_gui", stderr_capture.getvalue())

        # 3. Subsequent logs continue to reach GUI and console without attempting file writes
        gui_messages.clear()
        bot_logger.log("Message after fallback", "INFO")
        self.assertTrue(any("Message after fallback" in m for m in gui_messages))


class TestBrowserSessionDiagnostics(unittest.TestCase):
    """P0-4: Verify BrowserSession unexpected context close logging and safe single close."""

    def test_unexpected_context_close_diagnostics(self):
        session = BrowserSession(headless=True)
        session._is_closing = False
        session._context_closed = False

        logs = []
        mock_context = MagicMock()
        mock_context.browser.is_connected.return_value = False
        session.context = mock_context
        session.feed_page = MagicMock(is_closed=lambda: True)
        session.detail_page = MagicMock(is_closed=lambda: True)

        stop_event = threading.Event()
        session.set_stop_event(stop_event)

        with patch("browser.session.logger.log", side_effect=lambda msg, lvl="INFO": logs.append((msg, lvl))):
            # Simulate Playwright context on_close firing
            session._context_closed = True
            if not session._is_closing:
                stop_req = bool(session.stop_event and session.stop_event.is_set())
                browser_conn = False
                feed_closed = bool(not session.feed_page or session.feed_page.is_closed())
                detail_closed = bool(not session.detail_page or session.detail_page.is_closed())
                session_log = (
                    f"[SESSION][UNEXPECTED_CONTEXT_CLOSE] stop_requested={str(stop_req).lower()} "
                    f"explicit_close=false browser_connected={str(browser_conn).lower()} "
                    f"feed_closed={str(feed_closed).lower()} detail_closed={str(detail_closed).lower()}"
                )
                logs.append((session_log, "ERROR"))

            self.assertTrue(any("[SESSION][UNEXPECTED_CONTEXT_CLOSE]" in l[0] for l in logs))

    def test_close_when_already_closed_is_idempotent_cleanup_only(self):
        session = BrowserSession(headless=True)
        session._context_closed = True
        session.context = None

        with patch("browser.session.ProfileLockManager.release") as mock_release:
            with patch("browser.session.logger.log") as mock_log:
                session.close(reason="fatal_error")
                mock_release.assert_called_once()
                mock_log.assert_called_with("[SESSION][CLOSED] reason=fatal_error cleanup_only=true")

                # Calling a second time does nothing
                session.close(reason="again")
                self.assertEqual(mock_release.call_count, 1)


class TestNeighborMutualAndSweepMode(unittest.TestCase):
    """P0-5 to P1: Verify neighbor mutual-only filtering, immediate skip before detail goto, and sweep mode."""

    def test_buddy_collection_failed_aborts_controller(self):
        """When buddy collection returns state='failed', controller aborts safely without processing cards."""
        from app.controller import FeedController

        cfg = {
            "feed_source": FeedSourceType.NEIGHBOR.value,
            "neighbor_mutual_only": True,
            "my_blog_id": "test_my_id",
            "max_feed_items": 10,
        }
        controller = FeedController(config=cfg, history=MagicMock())

        failed_result = BuddyCollectionResult(
            buddies={},
            state="failed",
            expected_total=None,
            collected_total=0,
            pages_visited=0,
            page_fingerprints=[],
            error="buddy_collection_error",
        )

        mock_sess_inst = MagicMock()
        mock_sess_inst.context = MagicMock()
        mock_sess_inst.is_context_alive.return_value = True
        mock_sess_inst.get_detail_page.return_value = MagicMock()
        with patch("app.controller.BrowserSession", return_value=mock_sess_inst):
            with patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])):
                with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=failed_result):
                    controller._run()

        st = controller.state_mgr.get_state()
        self.assertEqual(st.current_state, FeedState.ERROR)
        self.assertIn("서로이웃 목록 확인 실패", st.message)

    def test_skip_non_mutual_and_unknown_before_detail_goto(self):
        """Non-mutual and unknown posts are skipped before detail_page.goto() and do not consume max_items."""
        from app.controller import FeedController

        cfg = {
            "feed_source": FeedSourceType.NEIGHBOR.value,
            "neighbor_mutual_only": True,
            "neighbor_like_sweep_mode": True,
            "my_blog_id": "me",
            "max_feed_items": 2,
        }
        controller = FeedController(config=cfg, history=MagicMock())

        mutual_buddy_info = BuddyInfo(
            blog_id="mutual_user",
            nickname="Mutual Friend",
            blog_title="Blog",
            group_name="Group",
            buddy_type="서로이웃",
            last_post_date="26.09.15",
            added_date="26.01.01",
        )
        normal_buddy_info = BuddyInfo(
            blog_id="normal_user",
            nickname="Normal Neighbor",
            blog_title="Blog",
            group_name="Group",
            buddy_type="이웃",
            last_post_date="26.09.15",
            added_date="26.01.01",
        )

        buddy_result = BuddyCollectionResult(
            buddies={"mutual_user": mutual_buddy_info, "normal_user": normal_buddy_info},
            state="complete",
            expected_total=2,
            collected_total=2,
            pages_visited=1,
            page_fingerprints=["fp1"],
        )

        # 4 posts: 1 non-mutual, 1 unknown (no blog_id), 2 mutual
        posts = [
            FeedPost(key="normal_user:1", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/normal_user/1", blog_id="normal_user", log_no="1"),
            FeedPost(key="unknown:2", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/unknown/2", blog_id="", log_no="2"),
            FeedPost(key="mutual_user:101", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/mutual_user/101", blog_id="mutual_user", log_no="101"),
            FeedPost(key="mutual_user:102", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/mutual_user/102", blog_id="mutual_user", log_no="102"),
        ]

        mock_source = MagicMock()
        mock_source.discover_posts.side_effect = [posts, []]
        mock_source.is_exhausted.return_value = True

        mock_sess_inst = MagicMock()
        mock_sess_inst.context = MagicMock()
        mock_sess_inst.is_context_alive.return_value = True
        mock_sess_inst.get_feed_page.return_value = MagicMock()
        mock_sess_inst.get_detail_page.return_value = MagicMock()

        with patch("app.controller.BrowserSession", return_value=mock_sess_inst):
            with patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])):
                with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=buddy_result):
                    with patch("app.controller.NeighborFeedSource", return_value=mock_source):
                        with patch.object(controller.pacing, "wait_next_post", return_value=MagicMock(stopped=False, skipped=False)):
                            with patch.object(controller.pacing, "maybe_pause", return_value=None):
                                with patch("app.processor.PostProcessor.process") as mock_process:
                                    mock_process.return_value = PostProcessResult(
                                        post=posts[2],
                                        like_result=LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED),
                                        comment_result=CommentProcessResult(status=CommentSubmitState.NONE),
                                    )
                                    controller._run()

        # Check process calls: exactly the 2 mutual posts were processed
        self.assertEqual(mock_process.call_count, 2)
        processed_keys = [call.args[1].key for call in mock_process.call_args_list]
        self.assertEqual(processed_keys, ["mutual_user:101", "mutual_user:102"])

        # detail_page.goto was never called for normal_user or unknown
        st = controller.state_mgr.get_state()
        self.assertEqual(st.current_state, FeedState.COMPLETED)

    def test_sweep_mode_like_only_bypasses_popularity_guard_and_skips_comment(self):
        """Sweep mode enforces effective_like=True, effective_comment=False, and bypasses popularity guard."""
        cfg = {
            "neighbor_like_sweep_mode": True,
            "like_count_skip_threshold": 5,  # High threshold
        }
        processor = PostProcessor(
            config=cfg,
            like_enabled=True,
            comment_enabled=True,  # Even if true, sweep mode must turn off comment
        )

        mock_detail_page = MagicMock()
        post = FeedPost(
            key="mutual:1",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/mutual/1",
            blog_id="mutual",
            log_no="1",
        )

        with patch("naver.target_guard.TargetPostGuard.verify"):
            with patch("services.like_transaction.LikeTransactionService.resolve_like_state", return_value=LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)):
                with patch("services.like_transaction.LikeTransactionService.execute_like_transaction") as mock_exec_like:
                    mock_exec_like.return_value = LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
                    with patch("naver.interaction.CommentInteractionService.open_comment_layer") as mock_open_comment:
                        with patch("services.like_eligibility.LikeEligibilityService.evaluate") as mock_elig:
                            res = processor.process(mock_detail_page, post)

                            # Popularity guard was bypassed
                            mock_elig.assert_not_called()

                            # Like transaction executed
                            mock_exec_like.assert_called_once()
                            self.assertEqual(res.like_result.state_after, LikeState.LIKED)

                            # Comment layer was NEVER opened, 0 comment attempts
                            mock_open_comment.assert_not_called()
                            self.assertEqual(res.comment_result.status, CommentSubmitState.NONE)

    def test_sweep_mode_already_liked_post_is_preserved_no_click(self):
        """In sweep mode, if post is already LIKED, action_taken is False and no click occurs."""
        cfg = {"neighbor_like_sweep_mode": True}
        processor = PostProcessor(config=cfg, like_enabled=True, comment_enabled=False)

        mock_detail_page = MagicMock()
        post = FeedPost(key="mutual:2", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/mutual/2", blog_id="mutual", log_no="2")

        with patch("naver.target_guard.TargetPostGuard.verify"):
            with patch("services.like_transaction.LikeTransactionService.resolve_like_state", return_value=LikeStateResult(state=LikeState.LIKED, confidence=LikeConfidence.HIGH)):
                with patch("services.like_transaction.LikeTransactionService.execute_like_transaction") as mock_exec_like:
                    res = processor.process(mock_detail_page, post)
                    mock_exec_like.assert_not_called()
                    self.assertFalse(res.like_result.action_taken)
                    self.assertEqual(res.like_result.state_after, LikeState.LIKED)

    def test_sweep_mode_unknown_like_state_is_skipped_no_click(self):
        """In sweep mode, if like state confidence is not HIGH or UNKNOWN, no click occurs."""
        cfg = {"neighbor_like_sweep_mode": True}
        processor = PostProcessor(config=cfg, like_enabled=True, comment_enabled=False)

        mock_detail_page = MagicMock()
        post = FeedPost(key="mutual:3", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/mutual/3", blog_id="mutual", log_no="3")

        with patch("naver.target_guard.TargetPostGuard.verify"):
            with patch("services.like_transaction.LikeTransactionService.resolve_like_state", return_value=LikeStateResult(state=LikeState.UNKNOWN, confidence=LikeConfidence.LOW)):
                with patch("services.like_transaction.LikeTransactionService.execute_like_transaction") as mock_exec_like:
                    res = processor.process(mock_detail_page, post)
                    mock_exec_like.assert_not_called()
                    self.assertFalse(res.like_result.action_taken)
                    self.assertEqual(res.like_result.error, "low_confidence_skip")


if __name__ == "__main__":
    unittest.main()
