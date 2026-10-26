from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import sync_playwright

from services.comment_participant_collector import CommentParticipantCollector
from services.reaction_participant_collector import ReactionParticipantCollector


@pytest.mark.parametrize("collector", [ReactionParticipantCollector, CommentParticipantCollector])
def test_participant_identity_handles_goblog_links_and_rejects_foreign_host(collector):
    html = '''<span class="u_cbox_count">2</span><span class="count">2</span><ul class="list_sympathy">
    <li class="u_cbox_comment"><div class="u_cbox_info"><a href="https://blog.naver.com/GoBlog.naver?userId=Applicant_A">A</a></div></li>
    <li class="u_cbox_comment"><div class="u_cbox_info"><a href="https://m.blog.naver.com/member_b">B</a></div></li>
    <li class="u_cbox_comment"><div class="u_cbox_info"><a href="https://example.org/?blogId=forged">X</a></div></li>
    </ul>'''
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            dom = browser.new_page()
            dom.set_content(html)
            page = MagicMock()
            page.locator.return_value.first.count.return_value = 0
            page.evaluate.side_effect = dom.evaluate
            module = collector.__module__
            with patch(module + ".interruptible_wait"):
                items, state, count = collector.collect(page, "me", "1")
        finally:
            browser.close()
    assert {item["blog_id"] for item in items} == {"applicant_a", "member_b"}
    if collector is CommentParticipantCollector:
        assert state == "partial"  # The third author's identity could not be matched.


@pytest.mark.parametrize("collector", [ReactionParticipantCollector, CommentParticipantCollector])
def test_missing_count_is_partial_not_confirmed_zero(collector):
    page = MagicMock()
    page.locator.return_value.first.count.return_value = 0
    page.evaluate.return_value = {"items": [], "displayedCount": None, "totalLoadedEntries": 0}
    with patch(collector.__module__ + ".interruptible_wait"):
        _, state, _ = collector.collect(page, "me", "1")
    assert state == "partial"
