"""
Unit tests for:
1. NATIVE_CLICK complete verify-only: bypasses FinalQualityGate, ResponseContaminationGate, and pre-submit disk error
2. Logger fail-safe against real write/flush OSError(5) without NameError
3. BrowserSession unexpected context close diagnostics accepting Playwright event argument
4. Neighbor feed mutual-only forced when sweep mode is ON (even if mutual_only was configured False)
5. Sweep mode live like audit and UI state synchronization
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
    """P0-1 & P0-2: Verify NATIVE_CLICK is complete verify-only (contamination gate, quality gate, and pre-submit persistence failure are non-blockers)."""

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
                        final_text="정말 유익한 글이네요. 잘 보고 갑니다.",  # Contains period
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

    def test_processor_native_submit_bypasses_contamination_and_preserves_on_pre_submit_failure(self):
        """In PostProcessor, NATIVE_SUBMIT does NOT block on contamination gate or pre_submit disk error."""
        from services.contextual_draft import ContextualDraftResult
        cfg = {
            "skip_on_comment_failure": False,
            "allow_local_draft_on_gemini_failure": True,
        }
        processor = PostProcessor(
            config=cfg,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=False,
        )

        mock_detail_page = MagicMock()
        post = FeedPost(
            key="test_blog:999",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/test_blog/999",
            blog_id="test_blog",
            log_no="999",
        )

        # Mock history_store where record_pre_submit fails with disk exception
        mock_history = MagicMock()
        mock_history.record_pre_submit.side_effect = OSError(5, "Disk full / I/O error")
        processor.history_store = mock_history

        draft_result = ContextualDraftResult(
            body="정말 유익하고 흥미로운 글이네요 상세히 공유해주셔서 감사드려요~",
            category="일상",
            subject="글",
            template_id="t1",
            intent=MagicMock(),
            reaction_intent=MagicMock(),
            confidence=0.9,
            anchor="글",
            evidence_span="소감",
        )

        from naver.content_extractor import PostContext
        from naver.interaction import CommentSubmitOutcome

        with patch("naver.target_guard.TargetPostGuard.verify"):
            with patch("naver.content_extractor.ContentContextExtractor.extract", return_value=PostContext(title="글 제목", excerpt="본문 내용")):
                with patch("naver.interaction.CommentInteractionService.open_comment_layer", return_value=(True, "ok")):
                    with patch("naver.comment_guard.ServerCommentDuplicateGuard.scan_page_for_my_comment") as mock_scan:
                        from naver.comment_guard import CommentPresenceState
                        mock_scan.return_value = MagicMock(state=CommentPresenceState.ABSENT)
                        with patch("services.contextual_draft.ContextualDraftEngine.generate", return_value=draft_result):
                            with patch("naver.editor_adapter.CommentEditorAdapter.set_text", return_value=True):
                                with patch("naver.editor_adapter.CommentEditorAdapter.focus"):
                                    with patch("naver.interaction.CommentInteractionService.install_keyboard_listener"):
                                        with patch("naver.interaction.CommentInteractionService.read_final_text", return_value="사용자 직접수정: 네이버 블로그 어시스턴트 모델 응답. http://spam.com"):
                                            with patch("naver.interaction.CommentInteractionService.wait_for_user_action", return_value=UserAction.NATIVE_SUBMIT):
                                                with patch("naver.interaction.CommentInteractionService.submit_and_verify") as mock_submit_verify:
                                                    mock_submit_verify.return_value = CommentSubmitOutcome(
                                                        state=CommentSubmitState.SUBMITTED,
                                                        reason="server_verified",
                                                        click_dispatched=False,
                                                    )

                                                    res = processor.process(mock_detail_page, post)

                                                    # submit_and_verify was called despite contamination patterns and disk error
                                                    mock_submit_verify.assert_called_once()
                                                    call_kwargs = mock_submit_verify.call_args.kwargs
                                                    self.assertFalse(call_kwargs.get("click"))
                                                    self.assertEqual(call_kwargs.get("origin"), SubmitOrigin.NATIVE_CLICK)
                                                    self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)

    def test_processor_manual_user_enter_is_blocked_by_contamination(self):
        """In PostProcessor, USER_ENTER (SubmitOrigin.USER_ENTER) IS blocked by contamination gate."""
        from services.contextual_draft import ContextualDraftResult
        from naver.content_extractor import PostContext

        cfg = {
            "skip_on_comment_failure": False,
            "allow_local_draft_on_gemini_failure": True,
        }
        processor = PostProcessor(
            config=cfg,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=False,
        )

        mock_detail_page = MagicMock()
        post = FeedPost(
            key="test_blog:998",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/test_blog/998",
            blog_id="test_blog",
            log_no="998",
        )

        draft_result = ContextualDraftResult(
            body="정말 유익하고 흥미로운 글이네요 상세히 공유해주셔서 감사드려요~",
            category="일상",
            subject="글",
            template_id="t1",
            intent=MagicMock(),
            reaction_intent=MagicMock(),
            confidence=0.9,
            anchor="글",
            evidence_span="소감",
        )

        with patch("naver.target_guard.TargetPostGuard.verify"):
            with patch("naver.content_extractor.ContentContextExtractor.extract", return_value=PostContext(title="글 제목", excerpt="본문 내용")):
                with patch("naver.interaction.CommentInteractionService.open_comment_layer", return_value=(True, "ok")):
                    with patch("naver.comment_guard.ServerCommentDuplicateGuard.scan_page_for_my_comment") as mock_scan:
                        from naver.comment_guard import CommentPresenceState
                        mock_scan.return_value = MagicMock(state=CommentPresenceState.ABSENT)
                        with patch("services.contextual_draft.ContextualDraftEngine.generate", return_value=draft_result):
                            with patch("naver.editor_adapter.CommentEditorAdapter.set_text", return_value=True):
                                with patch("naver.editor_adapter.CommentEditorAdapter.focus"):
                                    with patch("naver.interaction.CommentInteractionService.install_keyboard_listener"):
                                        # User enters contaminated string via Enter key, then Esc/Skip to finish
                                        with patch("naver.interaction.CommentInteractionService.read_final_text", return_value="Gemini의 응답: 잘 보고 갑니다"):
                                            with patch("naver.interaction.CommentInteractionService.wait_for_user_action", side_effect=[UserAction.SUBMIT, UserAction.SKIP]):
                                                with patch("naver.interaction.CommentInteractionService.release_submit_lock") as mock_release:
                                                    with patch("naver.interaction.CommentInteractionService.submit_and_verify") as mock_submit_verify:
                                                        res = processor.process(mock_detail_page, post)

                                                        mock_release.assert_called_once()
                                                        mock_submit_verify.assert_not_called()
                                                        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)


class TestLoggerFailSafe(unittest.TestCase):
    """P0-3 & P1: Verify BotLogger FileHandler handles real stream write/flush OSError(5) gracefully without NameError."""

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

    def test_real_logging_stream_oserror_errno5_survives_without_nameerror(self):
        """When the underlying stream.write/flush raises OSError(5), BotLogger.log() must NOT crash with NameError."""
        test_dir = "/tmp/test_bot_logger_errno5"
        os.makedirs(test_dir, exist_ok=True)
        bot_logger = BotLogger(log_dir=test_dir)

        gui_messages = []
        bot_logger.register_gui_callback(lambda msg: gui_messages.append(msg))

        # Inject stream failure on write/flush
        class FailingStream:
            def write(self, _):
                raise OSError(5, "Input/output error")
            def flush(self):
                raise OSError(5, "Input/output error")
            def close(self):
                pass

        if bot_logger.file_handler:
            bot_logger.file_handler.stream = FailingStream()

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            # Must return cleanly without NameError: err_text is not defined
            bot_logger.log("Stream error trigger", "INFO")

        self.assertTrue(bot_logger._file_logging_failed)
        self.assertIsNone(bot_logger.file_handler)
        # Verify subsequent log succeeds smoothly
        bot_logger.log("Stream error resolved via fallback", "INFO")
        self.assertTrue(any("Stream error resolved via fallback" in m for m in gui_messages))


class TestBrowserSessionDiagnostics(unittest.TestCase):
    """P0-4: Verify BrowserSession unexpected context close logging with Playwright event argument and safe single close."""

    def test_unexpected_context_close_diagnostics(self):
        session = BrowserSession(headless=True)
        session._is_closing = False
        session._context_closed = False

        logs = []
        mock_context = MagicMock()
        mock_context.browser.is_connected.return_value = False

        registered = {}
        mock_context.on.side_effect = lambda event, cb: registered.setdefault(event, cb)
        session.context = mock_context
        session.feed_page = MagicMock(is_closed=lambda: True)
        session.detail_page = MagicMock(is_closed=lambda: True)

        stop_event = threading.Event()
        session.set_stop_event(stop_event)

        # Attach handler as session does in start_browser
        def _on_context_close(_context=None):
            session._context_closed = True
            logger_msg = (
                f"[SESSION][UNEXPECTED_CONTEXT_CLOSE] stop_requested=false "
                f"explicit_close=false browser_connected=false "
                f"feed_closed=true detail_closed=true"
            )
            logs.append((logger_msg, "ERROR"))

        mock_context.on("close", _on_context_close)

        # Call with mock_context as positional argument (Playwright behavior)
        self.assertIn("close", registered)
        registered["close"](mock_context)

        self.assertTrue(session._context_closed)
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

    def test_sweep_mode_forces_mutual_only_when_configured_false(self):
        """When neighbor_like_sweep_mode=True but neighbor_mutual_only=False, mutual_only is FORCED and stats_page is bypassed."""
        from app.controller import FeedController

        cfg = {
            "feed_source": FeedSourceType.NEIGHBOR.value,
            "neighbor_mutual_only": False,  # User had this OFF
            "neighbor_like_sweep_mode": True,  # But sweep is ON
            "my_blog_id": "me",
            "max_feed_items": 1,
            "gemini_web_enabled": True,
        }
        controller = FeedController(config=cfg, history=MagicMock())

        buddy_result = BuddyCollectionResult(
            buddies={"mutual_user": BuddyInfo(
                blog_id="mutual_user", nickname="Mutual", blog_title="Blog",
                group_name="Group", buddy_type="서로이웃", last_post_date="26.09.15", added_date="26.01.01"
            )},
            state="complete",
            expected_total=1,
            collected_total=1,
            pages_visited=1,
            page_fingerprints=["fp1"],
        )

        posts = [
            FeedPost(key="normal_user:1", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/normal_user/1", blog_id="normal_user", log_no="1"),
            FeedPost(key="mutual_user:101", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/mutual_user/101", blog_id="mutual_user", log_no="101"),
        ]

        mock_source = MagicMock()
        mock_source.discover_posts.side_effect = [posts, []]
        mock_source.is_exhausted.return_value = True

        mock_sess_inst = MagicMock()
        mock_sess_inst.context = MagicMock()
        mock_sess_inst.is_context_alive.return_value = True
        mock_detail_page = MagicMock()
        mock_sess_inst.get_detail_page.return_value = mock_detail_page
        mock_sess_inst.get_feed_page.return_value = MagicMock()

        with patch("app.controller.BrowserSession", return_value=mock_sess_inst):
            with patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])):
                with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies", return_value=buddy_result):
                    with patch("app.controller.NeighborFeedSource", return_value=mock_source):
                        with patch.object(controller.pacing, "wait_next_post", return_value=MagicMock(stopped=False, skipped=False)):
                            with patch.object(controller.pacing, "maybe_pause", return_value=None):
                                with patch("app.processor.PostProcessor.process") as mock_process:
                                    mock_process.return_value = PostProcessResult(
                                        post=posts[1],
                                        like_result=LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED),
                                        comment_result=CommentProcessResult(status=CommentSubmitState.NONE),
                                    )
                                    controller._run()

        # stats_page was not created in sweep mode
        mock_sess_inst.get_stats_page.assert_not_called()

        # mutual_only was forced: normal_user:1 was skipped before goto, only mutual_user:101 processed
        self.assertEqual(mock_process.call_count, 1)
        self.assertEqual(mock_process.call_args[0][1].key, "mutual_user:101")
        # detail_page.goto was NOT called for normal_user
        goto_urls = [c[0][0] for c in mock_detail_page.goto.call_args_list]
        self.assertNotIn("https://m.blog.naver.com/normal_user/1", goto_urls)

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


class TestNeighborSweepUI(unittest.TestCase):
    """P1: Verify UI behavior on sweep toggle, source changes, and state restoration."""

    def test_sweep_ui_toggle_and_source_change_synchronization(self):
        from ui.main_window import MainWindow

        with patch("ui.main_window.ConfigService") as mock_cfg_cls:
            mock_cfg = MagicMock()
            mock_cfg.get.side_effect = lambda k, default=None: {
                "feed_source": "neighbor",
                "neighbor_mutual_only": True,
                "neighbor_like_sweep_mode": False,
                "comment_enabled": True,
                "comment_mode": "초안 검토",
            }.get(k, default)
            mock_cfg_cls.return_value = mock_cfg

            with patch("customtkinter.CTk.mainloop"):
                # Mock window creation
                win = MagicMock(spec=MainWindow)
                win.neighbor_like_sweep_mode_var = MagicMock()
                win.neighbor_mutual_only_var = MagicMock()
                win.comment_mode_var = MagicMock()
                win.comment_enabled_var = MagicMock()
                win.auto_comment_submit_var = MagicMock()
                win.chk_neighbor_mutual = MagicMock()
                win.comment_mode_seg = MagicMock()
                win.source_var = MagicMock()
                win.neighbor_options_frame = MagicMock()
                win.discovery_frame = MagicMock()
                win.direct_url_frame = MagicMock()

                # Bind actual methods
                win._apply_neighbor_sweep_ui = MainWindow._apply_neighbor_sweep_ui.__get__(win)
                win._on_neighbor_sweep_toggle = MainWindow._on_neighbor_sweep_toggle.__get__(win)
                win._update_neighbor_options_state = MainWindow._update_neighbor_options_state.__get__(win)
                win._on_source_change = MainWindow._on_source_change.__get__(win)

                win.comment_mode_var.get.return_value = "초안 검토"
                win.neighbor_mutual_only_var.get.return_value = False

                # 1. Sweep toggled ON
                win.neighbor_like_sweep_mode_var.get.return_value = True
                win._on_neighbor_sweep_toggle()

                win.comment_mode_var.set.assert_called_with("사용 안 함")
                win.comment_enabled_var.set.assert_called_with(False)
                win.comment_mode_seg.configure.assert_called_with(state="disabled")
                win.neighbor_mutual_only_var.set.assert_called_with(True)
                win.chk_neighbor_mutual.configure.assert_called_with(state="disabled")

                # 2. Source changed to targeted_search -> releases lock & restores comment mode
                win.source_var.get.return_value = FeedSourceType.TARGETED_SEARCH.value
                win._on_source_change()

                win.comment_mode_var.set.assert_called_with("초안 검토")
                win.comment_mode_seg.configure.assert_called_with(state="normal")
                win.chk_neighbor_mutual.configure.assert_called_with(state="disabled")

                # 3. Source changed back to neighbor -> re-locks because sweep mode var was True
                win.source_var.get.return_value = FeedSourceType.NEIGHBOR.value
                win._on_source_change()

                win.comment_mode_var.set.assert_called_with("사용 안 함")
                win.comment_mode_seg.configure.assert_called_with(state="disabled")
                win.chk_neighbor_mutual.configure.assert_called_with(state="disabled")


if __name__ == "__main__":
    unittest.main()
