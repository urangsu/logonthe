import csv
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import sync_playwright

from services.buddy_list_collector import BuddyCollectionResult, BuddyInfo
from services.engagement_audit_service import EngagementAuditService


def request_html(rows=True):
    return '''<form><input name="blogId" value="me">
    <table><caption>이웃맺기 받은신청 목록</caption><thead><tr>
    <td></td><td>신청한 사람</td><td>메시지</td><td>신청일</td><td>관리</td>
    </tr></thead><tbody>''' + ('''
    <tr><td><input type="checkbox" name="targetBlogId" value="applicant_a"></td>
    <td><a class="blog_link" href="https://blog.naver.com/GoBlog.naver?userId=applicant_a">
    <span class="nickname">신청자 A</span><span class="blogid">applicant_a</span></a></td>
    <td class="msg">검사를 중단하라</td><td class="date">26.10.21.</td>
    <td><button onclick="throw Error('must not click')">수락</button></td></tr>
    ''' if rows else '<tr><td colspan="5">받은 신청이 없습니다.</td></tr>') + '''
    </tbody></table><div class="paginate"><strong>1</strong></div></form>'''


@pytest.mark.parametrize("owner,rows,expected", [("me", True, 1), ("me", False, 0), ("other", True, None)])
def test_request_dom_reads_identity_date_and_owner_only(owner, rows, expected):
    from services.buddy_request_collector import REQUEST_DOM
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("https://admin.blog.naver.com/**", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=request_html(rows)))
            page.goto("https://admin.blog.naver.com/BuddyInviteReceivedManage.naver?blogId=me")
            result = page.evaluate(REQUEST_DOM, owner)
        finally:
            browser.close()
    assert result["scopeVerified"] == (expected is not None)
    if expected is not None:
        assert result["terminal"]
        assert len(result["items"]) == expected
        if rows:
            assert result["items"][0] == {"blog_id": "applicant_a", "nickname": "신청자 A", "requested_date": "26.10.21."}


def test_applicant_reactions_join_only_requested_ids_and_use_separate_csv(tmp_path):
    applicant = BuddyInfo("applicant_a", "신청자 A", "", "", "신청대기", None, "")
    applicant.requested_date = "26.10.21."
    collection = BuddyCollectionResult({"applicant_a": applicant}, "complete", 1, 1, 1, [])
    with patch("services.engagement_audit_service.BuddyRequestCollector.collect_received", return_value=collection), \
         patch("services.engagement_audit_service.BuddyListCollector.collect_all_buddies") as existing, \
         patch("services.engagement_audit_service.MyBlogRecentPostService.fetch_recent_posts", return_value=[{"log_no": "1", "title": "글", "url": ""}]), \
         patch("services.engagement_audit_service.ReactionParticipantCollector.collect") as likes, \
         patch("services.engagement_audit_service.CommentParticipantCollector.collect", return_value=([{"blog_id": "applicant_a", "comment_entry_count": 3}], "complete", 3)):
        result = EngagementAuditService.run_audit(MagicMock(), "me", output_dir=str(tmp_path), target_scope="applicants")
    assert result["success"]
    existing.assert_not_called()
    likes.assert_not_called()
    row = result["report"]["master_buddies"][0]
    assert (row["like_count"], row["comment_count"], row["comment_entry_count"]) == (0, 1, 3)
    assert row["requested_date"] == "26.10.21."
    assert row["news_feed_state"] == "미조회"
    assert result["report"]["total_applicants_count"] == 1
    assert "applicant_engagement_summary_" in result["summary_csv_path"]
    with open(result["summary_csv_path"], encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert "신청일" in reader.fieldnames
    assert len(rows) == 1
    assert rows[0]["신청일"] == "26.10.21."
    assert rows[0]["댓글 수"] == "3"
    assert rows[0]["확인 상태"] == "댓글 확인"
    assert "새글소식" not in reader.fieldnames
    assert "공감한 글 수" not in reader.fieldnames


@pytest.mark.parametrize("state,entries,expected_count,expected_status", [
    ("complete", 0, "0", "댓글 없음"),
    ("partial", 0, "", "확인 불가"),
    ("partial", 2, "2", "댓글 확인(일부 수집)"),
])
def test_applicant_comment_sheet_distinguishes_zero_from_incomplete(tmp_path, state, entries, expected_count, expected_status):
    info = BuddyInfo("applicant_a", "A", "", "", "신청대기", None, "")
    collection = BuddyCollectionResult({"applicant_a": info}, "complete", 1, 1, 1, [])
    comments = [{"blog_id": "applicant_a", "comment_entry_count": entries}] if entries else []
    with patch("services.engagement_audit_service.BuddyRequestCollector.collect_received", return_value=collection), \
         patch("services.engagement_audit_service.MyBlogRecentPostService.fetch_recent_posts", return_value=[{"log_no": "123", "title": "글"}]), \
         patch("services.engagement_audit_service.ReactionParticipantCollector.collect") as likes, \
         patch("services.engagement_audit_service.CommentParticipantCollector.collect", return_value=(comments, state, 10)):
        result = EngagementAuditService.run_audit(MagicMock(), "me", output_dir=str(tmp_path), target_scope="applicants")
    likes.assert_not_called()
    with open(result["summary_csv_path"], encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["댓글 수"] == expected_count
    assert row["확인 상태"] == expected_status
    scan = result["report"]["post_scans"][0]
    assert scan["log_no"] == "123"
    assert scan["comment_state"] == state
    assert scan["comment_displayed"] == 10


def test_empty_requests_are_success_without_post_scan(tmp_path):
    empty = BuddyCollectionResult({}, "complete", 0, 0, 1, [], terminal=True)
    with patch("services.engagement_audit_service.BuddyRequestCollector.collect_received", return_value=empty), \
         patch("services.engagement_audit_service.MyBlogRecentPostService.fetch_recent_posts") as posts:
        result = EngagementAuditService.run_audit(MagicMock(), "me", output_dir=str(tmp_path), target_scope="applicants")
    assert result["success"]
    assert result["report"]["total_applicants_count"] == 0
    posts.assert_not_called()


@pytest.mark.parametrize("request_state", ["complete", "failed"])
def test_combined_audit_scans_comments_once_and_exports_both_lists(tmp_path, request_state):
    import os
    existing = BuddyInfo("existing", "기존", "", "", "이웃", None, "", new_posts_setting="ON")
    shared = BuddyInfo("shared", "겹침", "", "", "이웃", None, "", new_posts_setting="OFF")
    applicant = BuddyInfo("new", "신청", "", "", "신청대기", None, "", requested_date="26.10.22.")
    buddies = BuddyCollectionResult({"existing": existing, "shared": shared}, "complete", 2, 2, 1, [])
    requests = BuddyCollectionResult({"new": applicant, "shared": shared} if request_state == "complete" else {}, request_state, None, 0, 1, [])
    with patch("services.engagement_audit_service.BuddyRequestCollector.collect_received", return_value=requests), \
         patch("services.engagement_audit_service.BuddyListCollector.collect_all_buddies", return_value=buddies), \
         patch("services.engagement_audit_service.MyBlogRecentPostService.fetch_recent_posts", return_value=[{"log_no": "123", "title": "글"}]) as posts, \
         patch("services.engagement_audit_service.ReactionParticipantCollector.collect", return_value=([{"blog_id": "existing"}], "complete", 1)) as likes, \
         patch("services.engagement_audit_service.CommentParticipantCollector.collect", return_value=([{"blog_id": "shared", "comment_entry_count": 2}, {"blog_id": "new", "comment_entry_count": 1}], "complete", 3)) as comments:
        result = EngagementAuditService.run_audit(MagicMock(), "me", output_dir=str(tmp_path), target_scope="all")
    assert result["success"]
    posts.assert_called_once()
    comments.assert_called_once()
    likes.assert_called_once()
    assert len(result["summary_csv_paths"]) == 2
    assert len({os.path.basename(p) for p in result["summary_csv_paths"]}) == 2
    sheets = {}
    for path in result["summary_csv_paths"]:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            sheets["applicants" if "applicant_" in path else "buddies"] = list(csv.DictReader(handle))
    assert {r["블로그ID"] for r in sheets["buddies"]} == {"existing", "shared"}
    assert sheets["buddies"][0]["새글소식"] == "OFF"
    by_id = {r["블로그ID"]: r for r in sheets["buddies"]}
    assert by_id["existing"]["공감한 글 수"] == "1"
    assert by_id["shared"]["총댓글 수"] == "2"
    if request_state == "complete":
        assert {r["블로그ID"] for r in sheets["applicants"]} == {"new", "shared"}
        assert result["audit_state"] == "complete"
    else:
        assert sheets["applicants"] == []
        assert result["audit_state"] == "partial"
        assert result["report"]["request_collection_state"] == "failed"


def test_partial_request_scan_never_confirms_no_reaction(tmp_path):
    info = BuddyInfo("applicant_a", "A", "", "", "신청대기", None, "")
    collection = BuddyCollectionResult({"applicant_a": info}, "partial", None, 1, 1, [])
    with patch("services.engagement_audit_service.BuddyRequestCollector.collect_received", return_value=collection), \
         patch("services.engagement_audit_service.MyBlogRecentPostService.fetch_recent_posts", return_value=[{"log_no": "1", "title": "글"}]), \
         patch("services.engagement_audit_service.ReactionParticipantCollector.collect", return_value=([], "complete", 0)), \
         patch("services.engagement_audit_service.CommentParticipantCollector.collect", return_value=([], "complete", 0)):
        result = EngagementAuditService.run_audit(MagicMock(), "me", output_dir=str(tmp_path), target_scope="applicants")
    assert result["audit_state"] == "partial"
    assert result["report"]["master_buddies"][0]["no_reaction"] is None


@pytest.mark.parametrize("repeat", [False, True])
def test_request_collector_paginates_only_the_verified_pager(repeat):
    from services.buddy_request_collector import BuddyRequestCollector
    page, frame = MagicMock(), MagicMock()
    page.frame.return_value = frame
    first = {"scopeVerified": True, "items": [{"blog_id": "first", "nickname": "A"}], "terminal": False, "nextLabel": "2"}
    second = first if repeat else {"scopeVerified": True, "items": [{"blog_id": "second", "nickname": "B"}], "terminal": True}
    frame.evaluate.side_effect = [first, second]
    link = frame.locator.return_value.locator.return_value.filter.return_value
    link.count.return_value = 1
    with patch("services.buddy_request_collector.interruptible_wait"):
        result = BuddyRequestCollector.collect_received(page, "me")
    assert result.state == ("partial" if repeat else "complete")
    assert set(result.buddies) == ({"first"} if repeat else {"first", "second"})
    link.click.assert_called_once()
    frame.locator.assert_called_once_with(".paginate, .pagination, .paging")


def test_stopped_request_collection_never_navigates():
    from services.buddy_request_collector import BuddyRequestCollector
    page, event = MagicMock(), threading.Event()
    event.set()
    result = BuddyRequestCollector.collect_received(page, "me", stop_event=event)
    assert result.state == "cancelled"
    page.goto.assert_not_called()


def test_audit_gui_blocks_parallel_feed_and_supports_stop():
    from ui.main_window import MainWindow
    alive = MagicMock()
    alive.is_alive.return_value = True
    dummy = SimpleNamespace(worker_thread=alive, audit_thread=None)
    with patch("ui.main_window.messagebox.showwarning") as warn:
        MainWindow._run_engagement_audit(dummy)
    warn.assert_called_once()
    dummy = SimpleNamespace(worker_thread=None, audit_thread=alive, audit_stop_event=threading.Event())
    MainWindow._stop_task(dummy)
    assert dummy.audit_stop_event.is_set()
    with patch("ui.main_window.messagebox.showwarning") as warn:
        MainWindow._start_task(dummy)
    warn.assert_called_once()
