from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.run_control import RunControl, RunControlState, StopRequestedException
from services.buddy_list_collector import BUDDY_DOM, BuddyCollectionResult
from services.relationship_resolver import RelationshipResolver
from ui.main_window import MainWindow


def test_gui_restart_creates_new_control_and_events():
    window = SimpleNamespace(state_mgr=None, after=lambda *args: None)
    window._sync_run_control_ui = lambda *args: None
    with patch("app.run_control.logger.log"):
        MainWindow._prepare_feed_run_control(window)
        old = window.run_control
        old.set_active_post("previous:1")
        old.request_skip()
        old.request_stop()
        MainWindow._prepare_feed_run_control(window)
        new = window.run_control
        assert new is not old
        assert new.stop_event is not old.stop_event
        assert new.state == RunControlState.RUNNING
        assert not new.skip_event.is_set()
        assert old.stop_event.is_set()
        new.request_pause("gemini_circuit_breaker")
        assert new.state == RunControlState.PAUSE_REQUESTED


def test_clearing_stop_event_cannot_revive_stopped_control():
    with patch("app.run_control.logger.log"):
        control = RunControl()
        control.request_stop()
        control.stop_event.clear()
        with pytest.raises(StopRequestedException):
            control.checkpoint("before_next_post")


@pytest.mark.parametrize("state,complete,terminal,reason", [
    ("partial", False, False, "relationship_collection_incomplete"),
    ("complete", False, True, "relationship_collection_incomplete"),
    ("complete", True, False, "relationship_collection_incomplete"),
])
def test_partial_collection_is_not_usable(state, complete, terminal, reason):
    collection = BuddyCollectionResult(
        {}, state, 250, 200, 8, [], terminal=terminal,
        collection_complete=complete, relationship_complete=True)
    assert RelationshipResolver(collection).is_execution_blocked() == (True, reason)


@pytest.mark.parametrize("evidence,terminal", [
    ("", False),
    ('<span class="total">1</span>', True),
    ('<span class="total">250</span>', False),
    ('<div class="paginate"><strong>1</strong></div>', True),
])
def test_buddy_list_end_requires_positive_evidence(evidence, terminal):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f'''<section>{evidence}<table>
              <thead><tr><th>이웃</th><th>구분</th><th>추가일</th></tr></thead>
              <tbody><tr><td><input name="buddySeq" value="1">
              <a href="https://blog.naver.com/example">example</a></td>
              <td>서로이웃</td><td>2026.10.12</td></tr></tbody>
            </table></section>''')
            data = page.evaluate(BUDDY_DOM)
            assert data["scopeVerified"]
            assert len(data["items"]) == 1
            assert data["terminal"] is terminal
        finally:
            browser.close()
