import csv
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

BUDDY_FIELDNAMES_MASTER = [
    "블로그ID", "닉네임", "블로그링크", "그룹", "이웃구분", "추가일", "새글소식",
    "반응한 글 수", "공감한 글 수", "댓글 단 글 수", "총댓글 수", "관찰 범위", "확인 시각", "반응 구분",
    "판정", "수집상태",
]

# 의미 토큰 색상 (배경, 글자). 어두운 배경 + 흰색 굵은 글자로 가독성 확보.
HIGHLIGHT_FILLS = {
    "blue": ("1D4ED8", "FFFFFF"),
    "red": ("B91C1C", "FFFFFF"),
}

class EngagementAuditStore:
    """Writes one buddy-level aggregate CSV. No per-post or non-buddy reports."""

    @classmethod
    def output_path(cls, output_dir: Optional[str] = None, target_scope: str = "buddies") -> str:
        directory = output_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(directory, exist_ok=True)
        prefix = "applicant" if target_scope == "applicants" else "buddy"
        return os.path.join(directory, f"{prefix}_engagement_summary_{time.strftime('%Y%m%d')}.csv")

    @staticmethod
    def _safe_cell(value: Any) -> Any:
        if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    @classmethod
    def format_master_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        blog_id = row.get("blog_id", "")
        news_feed = (
            row.get("새글소식")
            or row.get("news_feed_state")
            or row.get("new_posts_setting")
            or row.get("내 새글보기")
            or "확인 불가"
        )
        if isinstance(news_feed, str):
            news_feed_upper = news_feed.strip().upper()
            if news_feed_upper in {"ON", "OFF"}:
                news_feed = news_feed_upper
        return {
            "블로그ID": blog_id,
            "닉네임": row.get("nickname", ""),
            "블로그링크": row.get("blog_url", f"https://m.blog.naver.com/{blog_id}") if blog_id else "",
            "그룹": row.get("group_name") or "기본그룹",
            "이웃구분": row.get("buddy_type", "이웃"),
            "추가일": row.get("added_date", ""),
            "신청일": row.get("requested_date", ""),
            "새글소식": news_feed,
            "내 새글보기": news_feed,
            "반응한 글 수": row.get("engaged_post_count", 0),
            "공감한 글 수": row.get("like_count", 0),
            "댓글 단 글 수": row.get("comment_count", 0),
            "총댓글 수": row.get("comment_entry_count", 0),
            "관찰 범위": row.get("observation_scope", ""),
            "확인 시각": row.get("checked_at", ""),
            "반응 구분": row.get("reaction_category", "확인 불가"),
            "판정": row.get("highlight_label", ""),
            "수집상태": "완료" if row.get("scan_complete") is True else "일부" if row.get("scan_complete") is False else "확인 불가",
        }

    @classmethod
    def xlsx_path_for(cls, csv_path: str) -> str:
        return os.path.splitext(csv_path)[0] + ".xlsx"

    @classmethod
    def _save_xlsx(cls, csv_path: str, fieldnames: List[str], rows: List[Dict[str, Any]], colors: List[str]) -> Optional[str]:
        """CSV와 같은 내용을 XLSX로 저장하며 highlight 행을 색칠한다. openpyxl이 없으면 건너뛴다."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except Exception:
            return None
        wb = Workbook()
        ws = wb.active
        ws.title = "이웃 반응"
        ws.append(fieldnames)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row, color in zip(rows, colors):
            ws.append([cls._safe_cell(row.get(key, "")) for key in fieldnames])
            spec = HIGHLIGHT_FILLS.get(color)
            if spec:
                fill = PatternFill("solid", start_color=spec[0], end_color=spec[0])
                font = Font(bold=True, color=spec[1])
                for cell in ws[ws.max_row]:
                    cell.fill = fill
                    cell.font = font
        ws.freeze_panes = "A2"
        for idx, name in enumerate(fieldnames, 1):
            ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = max(10, min(40, len(name) * 2 + 4))
        path = cls.xlsx_path_for(csv_path)
        fd, tmp = tempfile.mkstemp(prefix=".buddy-summary-", suffix=".xlsx", dir=os.path.dirname(path))
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    @classmethod
    def save_summary(cls, report: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        applicants = report.get("target_scope") == "applicants"
        path = cls.output_path(output_dir, report.get("target_scope", "buddies"))
        fieldnames = ["신청일" if key == "추가일" and applicants else key for key in BUDDY_FIELDNAMES_MASTER]
        rows = [cls.format_master_row(item) for item in report.get("master_buddies", [])]
        colors = [item.get("highlight", "") for item in report.get("master_buddies", [])]
        if applicants or report.get("comments_only"):
            fieldnames = ["닉네임", "블로그ID", "댓글 수", "확인 상태", "관찰 범위", "신청일", "블로그링크", "확인 시각"]
            if not applicants:
                fieldnames = ["닉네임", "블로그ID", "댓글 수", "확인 상태", "새글소식", "관찰 범위", "추가일", "블로그링크", "확인 시각"]
            rows = []
            for item in report.get("master_buddies", []):
                row = cls.format_master_row(item)
                count = item.get("comment_entry_count", 0)
                complete = item.get("scan_complete") is True
                row["댓글 수"] = count if count or complete else ""
                row["확인 상태"] = (
                    ("댓글 확인" if complete else "댓글 확인(일부 수집)")
                    if count else ("댓글 없음" if complete else "확인 불가")
                )
                rows.append(row)
        cls._write_csv(path, fieldnames, rows)
        cls._save_xlsx(path, fieldnames, rows, colors)
        return path

    @classmethod
    def _write_csv(cls, path: str, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".buddy-summary-", suffix=".csv", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: cls._safe_cell(row.get(key, "")) for key in fieldnames})
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @classmethod
    def save_v13(cls, report: Dict[str, Any], output_dir: Optional[str] = None) -> Tuple[str, str, str, str]:
        """Compatibility adapter for older callers; only the summary CSV is produced."""
        summary = cls.save_summary(report, output_dir=output_dir)
        return "", "", summary, ""

    save_v8 = save_v13
