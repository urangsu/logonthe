from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.my_blog_comment_thread_collector import BlogCommentNode, CommentThreadCollectionResult
from services.reply_workbook_service import (
    ReplyWorkbookService,
    STATUS_EXCLUDED,
    STATUS_FAILED,
    STATUS_GENERATED,
    STATUS_NEED_CONTEXT,
    STATUS_REPLIED,
)


def node(no, text="좋은 글이네요", **kw):
    return BlogCommentNode(comment_no=no, nickname=f"닉{no}", text=text, date="2026. 10. 27.", **kw)


def thread(roots, status="complete", loaded=None, expected=None):
    return CommentThreadCollectionResult(
        status=status, roots=roots, loaded_total=loaded if loaded is not None else len(roots),
        expected_total=expected, unresolved_identity_count=0, unresolved_parent_count=0, more_clicks=0,
    )


def collected(title, roots, **kw):
    return {
        "post": {"log_no": "1", "title": title, "date": "2026. 10. 27.", "url": "https://m.blog.naver.com/me/1"},
        "body": {"excerpt": "본문", "expanded": "본문 확장"},
        "thread": thread(roots, **kw),
    }


def test_row_status_classification():
    replied = node("1")
    replied.existing_author_reply = "감사합니다"
    assert ReplyWorkbookService.row_status(node("2", is_secret=True), None) == STATUS_EXCLUDED
    assert ReplyWorkbookService.row_status(node("3", identity_valid=False), None) == STATUS_EXCLUDED
    assert ReplyWorkbookService.row_status(replied, None) == STATUS_REPLIED
    assert ReplyWorkbookService.row_status(node("4"), None) == STATUS_FAILED
    assert ReplyWorkbookService.row_status(node("5"), {"status": "NEED_MORE_CONTEXT", "reply": ""}) == STATUS_NEED_CONTEXT
    assert ReplyWorkbookService.row_status(node("6"), {"status": "REPLY", "reply": "ㅎㅎ"}) == STATUS_GENERATED
    assert ReplyWorkbookService.row_status(node("7"), {"status": "REPLY", "reply": ""}) == STATUS_FAILED


def test_only_unanswered_comments_are_sent_and_failures_stay_blank():
    replied = node("1")
    replied.existing_author_reply = "이미 답함"
    roots = [replied, node("2"), node("3", is_secret=True), node("4")]
    bridge = object()
    with patch("services.reply_workbook_service.MyBlogReplyService.generate_replies",
               return_value={"2": {"status": "REPLY", "reply": "방문 감사해요"}}) as gen:
        data = ReplyWorkbookService.build_post_sheet_data(collected("글A", roots), bridge)
    sent_ids = [t["comment_no"] for t in gen.call_args.kwargs["target_comments"]]
    assert sent_ids == ["2", "4"]
    by_no = {r["번호"]: r for r in data["rows"]}
    assert by_no[1]["상태"] == STATUS_REPLIED and by_no[1]["추천 답글"] == ""
    assert by_no[2]["상태"] == STATUS_GENERATED and by_no[2]["추천 답글"] == "방문 감사해요"
    assert by_no[3]["상태"] == STATUS_EXCLUDED
    assert by_no[4]["상태"] == STATUS_FAILED and by_no[4]["추천 답글"] == ""  # 지어내지 않음
    assert data["generated"] == 1 and data["eligible"] == 2


def test_no_bridge_call_when_nothing_to_answer():
    done = node("1")
    done.existing_author_reply = "답함"
    with patch("services.reply_workbook_service.MyBlogReplyService.generate_replies") as gen:
        ReplyWorkbookService.build_post_sheet_data(collected("글", [done]), object())
    gen.assert_not_called()


def test_sheet_titles_are_valid_and_unique():
    used = set()
    a = ReplyWorkbookService.sheet_title(1, "광양/순천: 맛집? [후기] " + "가" * 50, used)
    b = ReplyWorkbookService.sheet_title(1, "광양/순천: 맛집? [후기] " + "가" * 50, used)
    assert len(a) <= 31 and len(b) <= 31 and a != b
    assert not any(ch in a for ch in "\\/*?:[]")


def test_workbook_has_one_sheet_per_post_and_flags_partial(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    ok = ReplyWorkbookService.build_post_sheet_data(
        collected("첫 글", [node("1")], status="complete", expected=1), None)
    partial = ReplyWorkbookService.build_post_sheet_data(
        collected("둘째 글", [node("2")], status="partial", loaded=1, expected=5), None)
    path = ReplyWorkbookService.write_workbook([ok, partial], "me", str(tmp_path))
    wb = openpyxl.load_workbook(path)
    assert len(wb.sheetnames) == 2
    assert "완료" in wb[wb.sheetnames[0]]["A3"].value
    assert "일부만 수집됨 (1/5)" in wb[wb.sheetnames[1]]["A3"].value
    header_row = next(r for r in wb[wb.sheetnames[0]].iter_rows() if r[0].value == "번호")
    assert [c.value for c in header_row][:4] == ["번호", "닉네임", "댓글 작성일", "댓글"]


def test_formula_like_comment_is_escaped(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    data = ReplyWorkbookService.build_post_sheet_data(
        collected("글", [node("1", text='=HYPERLINK("x")')]), None)
    path = ReplyWorkbookService.write_workbook([data], "me", str(tmp_path))
    ws = openpyxl.load_workbook(path).active
    values = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(v.startswith("'=HYPERLINK") for v in values)
    assert not any(v.startswith("=") for v in values)


def test_run_limits_to_three_posts_and_reports_collection_state(tmp_path):
    posts = [{"log_no": str(i), "title": f"글{i}", "date": "", "url": "u", "comment_count": 1} for i in range(5)]
    page = MagicMock()
    with patch("services.reply_workbook_service.MyBlogRecentPostService.fetch_recent_posts_api", return_value=posts), \
         patch.object(ReplyWorkbookService, "collect_post",
                      side_effect=lambda p, b, post, s=None: collected(post["title"], [node("1")], status="partial", loaded=1, expected=3)), \
         patch("services.reply_workbook_service.MyBlogReplyService.generate_replies",
               return_value={"1": {"status": "REPLY", "reply": "감사해요"}}):
        result = ReplyWorkbookService.run(page, object(), "me", max_posts=10, output_dir=str(tmp_path))
    assert result["success"]
    assert len(result["sheets"]) == 3
    assert result["collection_state"] == "partial"


def test_run_fails_closed_without_posts(tmp_path):
    with patch("services.reply_workbook_service.MyBlogRecentPostService.fetch_recent_posts_api", return_value=[]):
        result = ReplyWorkbookService.run(MagicMock(), object(), "me", output_dir=str(tmp_path))
    assert result == {"success": False, "error": "no_recent_posts_found"}
    assert ReplyWorkbookService.run(MagicMock(), object(), "  ")["error"] == "empty_blog_id"
