import os
import re
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

from src.logger import logger
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.my_blog_comment_thread_collector import MyBlogCommentThreadCollector
from services.my_blog_reply_service import MyBlogReplyService

MAX_POSTS = 3
BODY_CHARS = 1600
EXPANDED_BODY_CHARS = 3200
_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")

HEADERS = ["번호", "닉네임", "댓글 작성일", "댓글", "내가 쓴 기존 답글", "추천 답글", "상태"]

STATUS_REPLIED = "답글 완료(생성 제외)"
STATUS_GENERATED = "추천 생성"
STATUS_NEED_CONTEXT = "본문 정보 부족"
STATUS_FAILED = "생성 실패"
STATUS_EXCLUDED = "제외(비밀/삭제/식별불가)"


class ReplyWorkbookService:
    """
    내 글 답글 도우미 일괄 모드:
    최근 글 최대 3개의 본문과 댓글을 수집하고, 미답글 댓글을 Gemini에 보내 추천 답글을 만든 뒤
    글 1개당 시트 1개인 엑셀(xlsx)로 저장한다.
    - 댓글 수집이 불완전(partial)해도 진행하되 시트 상단과 상태 열에 그대로 표시한다.
    - 생성되지 않은 답글은 빈칸으로 두고 사유를 상태 열에 적는다 (임의 문구로 채우지 않는다).
    """

    @staticmethod
    def sheet_title(index: int, title: str, used: set) -> str:
        base = _INVALID_SHEET_CHARS.sub(" ", title or "").strip()
        base = re.sub(r"\s+", " ", base)
        name = f"{index}. {base}"[:31].strip() or f"글{index}"
        candidate, n = name, 2
        while candidate in used:
            suffix = f" ({n})"
            candidate = name[: 31 - len(suffix)] + suffix
            n += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def row_status(node, reply_info: Optional[Dict[str, str]], generation_attempted: bool = True) -> str:
        if node.is_secret or node.is_deleted or not node.identity_valid:
            return STATUS_EXCLUDED
        if node.existing_author_reply:
            return STATUS_REPLIED
        if reply_info is None:
            return STATUS_FAILED
        if reply_info.get("status") == "NEED_MORE_CONTEXT":
            return STATUS_NEED_CONTEXT
        return STATUS_GENERATED if reply_info.get("reply") else STATUS_FAILED

    @classmethod
    def extract_post_body(cls, page: Any, blog_id: str, log_no: str) -> Dict[str, str]:
        page.goto(f"https://m.blog.naver.com/{blog_id}/{log_no}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1000)
        try:
            raw = page.evaluate(
                """() => {
                    const ct = document.querySelector(".se-main-container, #postViewArea, .post_ct, .se_component_wrap");
                    return ct ? ct.innerText : document.body.innerText;
                }"""
            )
        except Exception as exc:
            logger.log(f"[REPLY_BOOK] 본문 추출 실패: {exc}", "WARNING")
            raw = ""
        clean = re.sub(r"\s+", " ", raw or "").strip()
        return {"excerpt": clean[:BODY_CHARS], "expanded": clean[:EXPANDED_BODY_CHARS]}

    @classmethod
    def collect_post(cls, page: Any, blog_id: str, post: Dict[str, Any], stop_event: Any = None) -> Dict[str, Any]:
        body = cls.extract_post_body(page, blog_id, post["log_no"])
        thread = MyBlogCommentThreadCollector.collect_threads(
            page, blog_id, post["log_no"], stop_event=stop_event,
            expected_comment_count=post.get("comment_count"),
        )
        return {"post": post, "body": body, "thread": thread}

    @classmethod
    def build_post_sheet_data(cls, collected: Dict[str, Any], bridge: Any, stop_event: Any = None) -> Dict[str, Any]:
        post = collected["post"]
        body = collected["body"]
        thread = collected["thread"]
        roots = list(thread.roots)
        eligible = [n for n in roots if n.is_eligible_for_auto_reply]

        replies: Dict[str, Dict[str, str]] = {}
        attempted = False
        if eligible and bridge is not None and not (stop_event and stop_event.is_set()):
            attempted = True
            targets = [{"comment_no": n.comment_no, "nickname": n.nickname, "text": n.text} for n in eligible]
            replies = MyBlogReplyService.generate_replies(
                bridge,
                post_title=post.get("title", ""),
                post_excerpt=body["excerpt"],
                target_comments=targets,
                stop_event=stop_event,
                expanded_post_excerpt=body["expanded"],
            ) or {}

        rows: List[Dict[str, str]] = []
        for number, node in enumerate(roots, 1):
            info = replies.get(node.comment_no)
            status = cls.row_status(node, info, attempted)
            rows.append({
                "번호": number,
                "닉네임": node.nickname or "익명",
                "댓글 작성일": node.date,
                "댓글": node.text,
                "내가 쓴 기존 답글": node.existing_author_reply or "",
                "추천 답글": (info or {}).get("reply", "") if status == STATUS_GENERATED else "",
                "상태": status,
            })

        return {
            "title": post.get("title", ""),
            "date": post.get("date", ""),
            "url": post.get("url", ""),
            "collection_status": thread.status,
            "loaded": thread.loaded_total,
            "expected": thread.expected_total,
            "failure_reason": thread.failure_reason,
            "rows": rows,
            "generated": sum(1 for r in rows if r["상태"] == STATUS_GENERATED),
            "eligible": len(eligible),
            "excerpt": body["excerpt"],
        }

    @classmethod
    def collection_note(cls, data: Dict[str, Any]) -> str:
        status = data["collection_status"]
        expected = data["expected"] if data["expected"] is not None else "?"
        if status == "complete":
            return f"댓글 수집 완료 ({data['loaded']}/{expected})"
        if status == "partial":
            return f"댓글 일부만 수집됨 ({data['loaded']}/{expected}) - 누락된 댓글이 있을 수 있음"
        return f"댓글 수집 실패 ({data.get('failure_reason') or '원인 미확인'})"

    @classmethod
    def write_workbook(cls, sheets: List[Dict[str, Any]], blog_id: str, output_dir: Optional[str] = None) -> str:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = Workbook()
        wb.remove(wb.active)
        used: set = set()
        header_fill = PatternFill("solid", start_color="1E293B", end_color="1E293B")
        wrap = Alignment(wrap_text=True, vertical="top")
        widths = {"A": 7, "B": 16, "C": 14, "D": 50, "E": 36, "F": 50, "G": 22}

        for idx, data in enumerate(sheets, 1):
            ws = wb.create_sheet(cls.sheet_title(idx, data["title"], used))
            ws["A1"] = f"글 제목: {data['title']}"
            ws["A2"] = f"주소: {data['url']}    작성일: {data['date']}"
            ws["A3"] = cls.collection_note(data)
            ws["A4"] = f"추천 답글 {data['generated']}개 / 생성 대상 {data['eligible']}개"
            ws["A1"].font = Font(bold=True, size=12)
            if data["collection_status"] != "complete":
                ws["A3"].font = Font(bold=True, color="B91C1C")
            ws.append([])
            ws.append(HEADERS)
            header_row = ws.max_row
            for cell in ws[header_row]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = header_fill
            for row in data["rows"]:
                ws.append([_safe(row.get(h, "")) for h in HEADERS])
                for cell in ws[ws.max_row]:
                    cell.alignment = wrap
            ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
            for col, width in widths.items():
                ws.column_dimensions[col].width = width

        directory = output_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"reply_suggestions_{blog_id}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx")
        fd, tmp = tempfile.mkstemp(prefix=".reply-book-", suffix=".xlsx", dir=directory)
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    @classmethod
    def run(
        cls,
        page: Any,
        bridge: Any,
        blog_id: str,
        max_posts: int = MAX_POSTS,
        stop_event: Any = None,
        output_dir: Optional[str] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        def note(message: str) -> None:
            logger.log(f"[REPLY_BOOK] {message}")
            if progress:
                progress(message)

        b_id = (blog_id or "").strip()
        if not b_id:
            return {"success": False, "error": "empty_blog_id"}
        max_posts = max(1, min(int(max_posts or MAX_POSTS), MAX_POSTS))

        posts = MyBlogRecentPostService.fetch_recent_posts_api(b_id, max_count=max_posts)[:max_posts]
        if not posts:
            return {"success": False, "error": "no_recent_posts_found"}

        sheets: List[Dict[str, Any]] = []
        for idx, post in enumerate(posts, 1):
            if stop_event and stop_event.is_set():
                break
            note(f"({idx}/{len(posts)}) '{post['title'][:25]}' 본문/댓글 수집 중")
            collected = cls.collect_post(page, b_id, post, stop_event)
            note(f"({idx}/{len(posts)}) 답글 생성 중 (미답글 대상 확인)")
            sheets.append(cls.build_post_sheet_data(collected, bridge, stop_event))

        if not sheets:
            return {"success": False, "error": "stop_requested"}

        path = cls.write_workbook(sheets, b_id, output_dir)
        complete = all(s["collection_status"] == "complete" for s in sheets)
        return {
            "success": True,
            "path": path,
            "collection_state": "complete" if complete else "partial",
            "sheets": [
                {k: s[k] for k in ("title", "collection_status", "loaded", "expected", "generated", "eligible")}
                for s in sheets
            ],
        }


def _safe(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value
