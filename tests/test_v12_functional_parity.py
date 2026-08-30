import threading
import csv
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.controller import FeedController
from app.models import FeedPost, FeedSourceType, PostProcessResult
from app.state import StateManager
from app.errors import RecoverablePostError
from naver.content_extractor import ContentContextExtractor
from services.config import ConfigService
from services.engagement_audit_store import EngagementAuditStore
from services.engagement_audit_service import EngagementAuditService
from services.buddy_list_collector import BUDDY_DOM


class V12FunctionalParityTests(unittest.TestCase):
    def test_recoverable_post_failure_continues_to_next_post(self):
        first = FeedPost("one:1", FeedSourceType.DIRECT, "https://m.blog.naver.com/one/1")
        second = FeedPost("two:2", FeedSourceType.DIRECT, "https://m.blog.naver.com/two/2")
        config = {"feed_source": "direct", "direct_urls": [first.url, second.url], "max_feed_items": 2,
                  "like_enabled": True, "comment_enabled": True, "assistant_mode": False}
        history = MagicMock(); history.is_liked.return_value = False; history.is_comment_submitted.return_value = False
        with patch("app.controller.BrowserSession") as session_type, \
             patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.DirectUrlSource") as source_type, \
             patch("app.controller.PostProcessor") as processor_type:
            source_type.return_value.discover_posts.return_value = [first, second]
            processor_type.return_value.process.side_effect = [RecoverablePostError("recoverable"), PostProcessResult(second)]
            controller = FeedController(config, history, StateManager(), threading.Event())
            controller.run()
            self.assertEqual(processor_type.return_value.process.call_count, 2)

    def test_body_cleaning_removes_navigation_and_comment_boilerplate(self):
        raw = "본문의 실제 내용입니다. 이웃추가 공감 12 댓글 3 공유하기 본문 끝"
        cleaned = ContentContextExtractor.clean_text(raw)
        self.assertIn("본문의 실제 내용", cleaned)
        self.assertNotIn("이웃추가", cleaned)
        self.assertNotIn("공감 12", cleaned)

    def test_config_migrates_retired_assistant_mode_without_overwriting_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/config.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 2, "assistant_mode": False,
                           "like_enabled": False, "comment_enabled": True}, handle)
            cfg = ConfigService(path)
            self.assertEqual(cfg.get("workflow_mode"), "assisted_auto")
            self.assertFalse(cfg.get("like_enabled"))
            self.assertTrue(cfg.get("comment_enabled"))

    def test_partial_audit_does_not_confirm_zero_reaction(self):
        # This fixture exercises the V12/V13 rule directly at the report boundary:
        # a partial scan must retain unknown, not classify a buddy as silent.
        from services.buddy_list_collector import BuddyInfo, BuddyCollectionResult
        with patch("services.buddy_list_collector.BuddyListCollector.collect_all_buddies") as collect, \
             patch("services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts", return_value=[{"log_no": "1", "url": "u", "title": "t"}]), \
             patch("services.reaction_participant_collector.ReactionParticipantCollector.collect", return_value=([], "partial", None)), \
             patch("services.comment_participant_collector.CommentParticipantCollector.collect", return_value=([], "complete", 0)), \
             patch("services.engagement_audit_store.EngagementAuditStore.save_v13", return_value=("j", "x", "m", "u")):
            collect.return_value = BuddyCollectionResult(
                {"b": BuddyInfo("b", "N", "", "", "이웃", None, "26.08.01.")},
                "complete", 1, 1, 1, ["p"])
            result = EngagementAuditService.run_audit(MagicMock(), "me", 1)
            row = result["report"]["master_buddies"][0]
            self.assertIsNone(row["no_reaction"])
            self.assertEqual(result["report"]["unresponsive_buddies"], [])

    def test_csv_output_is_atomic_formula_safe(self):
        report = {"master_buddies": [{"blog_id": "=HYPERLINK(\"x\")", "nickname": "N", "no_reaction": None}],
                  "unresponsive_buddies": []}
        with tempfile.TemporaryDirectory() as tmp, patch.object(EngagementAuditStore, "get_file_paths", return_value=(f"{tmp}/r.json", f"{tmp}/x.xlsx", f"{tmp}/m.csv", f"{tmp}/u.csv")):
            _, _, master, _ = EngagementAuditStore.save_v13(report)
            with open(master, encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["블로그ID"], "'=HYPERLINK(\"x\")")
            self.assertFalse(any(name.endswith(".tmp") for name in __import__("os").listdir(tmp)))

    def test_buddy_dom_scopes_rows_and_ignores_navigation_links(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright unavailable")
        html = """<a href='https://blog.naver.com/menu'>메뉴</a>
        <div class='scope'><div class='total'>등록된 이웃 1명</div>
        <table><thead><tr><th>선택</th><th>닉네임</th><th>추가일</th></tr></thead>
        <tbody><tr><td><input name='buddyBlogNo' value='1'></td><td><a href='https://blog.naver.com/real_buddy'>실제 이웃</a></td><td>26.08.01.</td></tr></tbody></table>
        <div class='pagination'><strong>1</strong></div></div>"""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            data = page.evaluate(BUDDY_DOM)
            browser.close()
        self.assertTrue(data["scopeVerified"])
        self.assertEqual([item["blog_id"] for item in data["items"]], ["real_buddy"])
        self.assertEqual(data["expectedTotal"], 1)


if __name__ == "__main__":
    unittest.main()
