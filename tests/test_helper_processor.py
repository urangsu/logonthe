"""Worker + injected panel integration using only routed offline pages."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from app.models import FeedPost, FeedSourceType, PostActionPlan
from naver.content_extractor import PostContext
from services.helper_processor import ManualHelperProcessor
from tests.test_helper_browser import HTML, URL, TEXT, BODY


class HelperProcessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        if not Path(cls.playwright.chromium.executable_path).exists():
            cls.playwright.stop()
            raise unittest.SkipTest("Chromium fixture runtime not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HTML))
        self.stop = threading.Event()
        self.processor = ManualHelperProcessor({}, stop_event=self.stop, drafts_path=self.tmp.name)
        self.post = FeedPost("owner:123", FeedSourceType.DIRECT, URL, "owner", "123", "현재 글")

    def panel(self, selector):
        return self.page.locator("#naver-assistant-helper").locator(selector)

    def run_steps(self, steps):
        actions = iter(steps)
        original_wait = self.page.wait_for_timeout
        calls = 0
        def step(ms):
            nonlocal calls
            calls += 1
            self.assertLess(calls, 12, "Helper did not wait for the expected explicit action")
            action = next(actions, None)
            if action:
                action()
            original_wait(10)
        with patch.object(self.page, "wait_for_timeout", side_effect=step), patch(
                "services.helper_processor.ContentContextExtractor.extract", return_value=PostContext("현재 글", BODY)):
            return self.processor.process(self.page, self.post, PostActionPlan(False, False, True, True))

    def test_full_worker_manual_flow_with_flags_false(self):
        result = self.run_steps([
            lambda: self.panel("#answer").fill(TEXT),
            lambda: self.panel("#insert").click(),
            lambda: self.page.locator(".u_cbox_btn_upload").click(),
            lambda: self.panel("#next").click(),
        ])
        self.assertEqual(result.comment_result.status.value, "submitted")
        self.assertEqual(result.comment_result.submitted_text, TEXT)
        self.assertEqual(self.page.evaluate("[submitClicks,likeClicks]"), [1, 0])
        self.assertEqual(self.processor.drafts.load(self.post.key)["status"], "submitted")

    def test_stop_preserves_answer_and_native_editor_edits(self):
        edited = "네이버 입력창에서 마지막으로 직접 고친 내용입니다."
        result = self.run_steps([
            lambda: self.panel("#answer").fill(TEXT),
            lambda: self.panel("#insert").click(),
            lambda: self.page.locator("#naverComment__write_textarea").fill(edited),
            lambda: self.stop.set(),
        ])
        stored = self.processor.drafts.load(self.post.key)
        self.assertEqual(stored["answer"], TEXT)
        self.assertEqual(stored["nativeText"], edited)
        self.assertEqual(result.comment_result.draft_text, edited)
        self.assertEqual(self.page.evaluate("submitClicks"), 0)
        self.assertEqual(result.comment_result.status.value, "drafted")

    def test_navigation_locks_insertion_and_keeps_original_draft(self):
        def assert_locked_then_next():
            self.assertTrue(self.panel("#insert").is_disabled())
            self.assertEqual(self.panel("#answer").input_value(), "")
            self.assertEqual(self.panel("#title").inner_text(), "제목 확인 필요")
            self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), "")
            self.panel("#next").click()
        result = self.run_steps([
            lambda: self.panel("#answer").fill(TEXT),
            lambda: self.page.goto("https://m.blog.naver.com/other/456"),
            assert_locked_then_next,
        ])
        self.assertEqual(result.comment_result.status.value, "drafted")
        self.assertEqual(self.processor.drafts.load(self.post.key)["answer"], TEXT)
        self.assertEqual(self.page.evaluate("[submitClicks,likeClicks]"), [0, 0])

    def test_unknown_survives_stop_and_restore_cannot_reinsert(self):
        result = self.run_steps([
            lambda: self.panel("#answer").fill(TEXT),
            lambda: self.panel("#insert").click(),
            lambda: self.page.evaluate("window.autoObserve=false"),
            lambda: self.page.locator(".u_cbox_btn_upload").click(),
            lambda: self.panel("#next").click(),
        ])
        self.assertEqual(result.comment_result.status.value, "unknown")
        self.assertIsNone(result.comment_result.submitted_text)
        def inspect_restore():
            self.assertTrue(self.panel("#insert").is_disabled())
            self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), "")
            self.panel("#next").click()
        restored = self.run_steps([inspect_restore])
        self.assertEqual(restored.comment_result.status.value, "unknown")
        self.assertEqual(self.page.evaluate("submitClicks"), 0)


if __name__ == "__main__":
    unittest.main()
