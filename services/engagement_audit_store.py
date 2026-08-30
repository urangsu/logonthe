import csv
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

BUDDY_FIELDNAMES_MASTER = [
    "블로그ID", "닉네임", "블로그링크", "그룹", "이웃구분", "추가일", "내 새글보기",
    "반응한 글 수", "공감한 글 수", "댓글 단 글 수", "총댓글 수", "관찰 범위", "확인 시각", "반응 구분",
]

class EngagementAuditStore:
    """Writes one buddy-level aggregate CSV. No per-post or non-buddy reports."""

    @classmethod
    def output_path(cls, output_dir: Optional[str] = None) -> str:
        directory = output_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"buddy_engagement_summary_{time.strftime('%Y%m%d')}.csv")

    @staticmethod
    def _safe_cell(value: Any) -> Any:
        if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    @classmethod
    def format_master_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        blog_id = row.get("blog_id", "")
        return {
            "블로그ID": blog_id,
            "닉네임": row.get("nickname", ""),
            "블로그링크": row.get("blog_url", f"https://m.blog.naver.com/{blog_id}") if blog_id else "",
            "그룹": row.get("group_name") or "기본그룹",
            "이웃구분": row.get("buddy_type", "이웃"),
            "추가일": row.get("added_date", ""),
            "내 새글보기": row.get("news_feed_state", "확인 불가"),
            "반응한 글 수": row.get("engaged_post_count", 0),
            "공감한 글 수": row.get("like_count", 0),
            "댓글 단 글 수": row.get("comment_count", 0),
            "총댓글 수": row.get("comment_entry_count", 0),
            "관찰 범위": row.get("observation_scope", ""),
            "확인 시각": row.get("checked_at", ""),
            "반응 구분": row.get("reaction_category", "확인 불가"),
        }

    @classmethod
    def save_summary(cls, report: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        path = cls.output_path(output_dir)
        rows = [cls.format_master_row(item) for item in report.get("master_buddies", [])]
        fd, tmp = tempfile.mkstemp(prefix=".buddy-summary-", suffix=".csv", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=BUDDY_FIELDNAMES_MASTER)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: cls._safe_cell(row.get(key, "")) for key in BUDDY_FIELDNAMES_MASTER})
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    @classmethod
    def save_v13(cls, report: Dict[str, Any], output_dir: Optional[str] = None) -> Tuple[str, str, str, str]:
        """Compatibility adapter for older callers; only the summary CSV is produced."""
        summary = cls.save_summary(report, output_dir=output_dir)
        return "", "", summary, ""

    save_v8 = save_v13
