import os
import csv
import json
import time
import tempfile
from typing import Dict, Any, List, Tuple, Optional

AUDIT_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.json"))

BUDDY_FIELDNAMES_V13 = [
    "블로그ID",
    "닉네임",
    "블로그링크",
    "이웃구분",
    "그룹",
    "추가일",
    "내 새글보기",
    "공감한 글 수",
    "댓글 단 글 수",
    "총댓글 수",
    "반응한 글 수",
    "관찰 범위",
    "확인 시각",
    "반응 구분",
]


class EngagementAuditStore:
    """
    내 블로그 전체 이웃 기준 감사 및 무반응 이웃 감사 리포트 저장소 (V13.1)
    - 1. Master 이웃 감사 CSV: data/buddy_engagement_audit_YYYYMMDD.csv
    - 2. 무반응 이웃 전용 CSV: data/unresponsive_buddies_YYYYMMDD.csv
    - 3. 종합 감사 JSON: data/my_blog_engagement_audit.json
    - (비이웃 CSV 및 글별 상세 열은 사용자 요구에 따라 제거됨)
    """

    @classmethod
    def get_file_paths(cls) -> Tuple[str, str, str]:
        date_str = time.strftime("%Y%m%d")
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(data_dir, exist_ok=True)

        json_path = os.path.join(data_dir, "my_blog_engagement_audit.json")
        master_csv = os.path.join(data_dir, f"buddy_engagement_audit_{date_str}.csv")
        unresp_csv = os.path.join(data_dir, f"unresponsive_buddies_{date_str}.csv")

        return json_path, master_csv, unresp_csv

    @staticmethod
    def _safe_cell(value: Any) -> Any:
        """Keep spreadsheet formula-like external text literal."""
        if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    @classmethod
    def _atomic_json(cls, path: str, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".audit-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @classmethod
    def _atomic_csv(cls, path: str, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".audit-", suffix=".csv", dir=directory)
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
    def format_buddy_row(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        b_id = row.get("blog_id", "")
        blog_url = row.get("blog_url", f"https://m.blog.naver.com/{b_id}") if b_id else ""
        return {
            "블로그ID": b_id,
            "닉네임": row.get("nickname", ""),
            "블로그링크": blog_url,
            "이웃구분": row.get("buddy_type", "이웃"),
            "그룹": row.get("group_name", ""),
            "추가일": row.get("added_date", ""),
            "내 새글보기": row.get("news_feed_state", "확인 불가"),
            "공감한 글 수": row.get("like_count", 0),
            "댓글 단 글 수": row.get("comment_count", 0),
            "총댓글 수": row.get("comment_entry_count", 0),
            "반응한 글 수": row.get("engaged_post_count", 0),
            "관찰 범위": row.get("observation_scope", "최근 5개 글"),
            "확인 시각": row.get("checked_at", ""),
            "반응 구분": row.get("reaction_category", "확인 불가"),
        }

    @classmethod
    def save_v13(cls, report: Dict[str, Any]) -> Tuple[str, str, str]:
        json_path, master_csv, unresp_csv = cls.get_file_paths()

        # 1. 종합 JSON 저장
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        cls._atomic_json(json_path, report)

        # 2. Master CSV 저장 (전체 이웃)
        master_list = report.get("master_buddies", [])
        master_rows = [cls.format_buddy_row(b) for b in master_list]
        cls._atomic_csv(master_csv, BUDDY_FIELDNAMES_V13, master_rows)

        # 3. 무반응 이웃 전용 CSV 저장
        unresp_list = report.get("unresponsive_buddies", [])
        unresp_rows = [cls.format_buddy_row(u) for u in unresp_list]
        cls._atomic_csv(unresp_csv, BUDDY_FIELDNAMES_V13, unresp_rows)

        return json_path, master_csv, unresp_csv

    @classmethod
    def save_v8(cls, report: Dict[str, Any]) -> Tuple[str, str, str, Optional[str]]:
        """Backward-compatible adapter for save_v8 callers."""
        json_path, master_csv, unresp_csv = cls.save_v13(report)
        return json_path, master_csv, unresp_csv, None
