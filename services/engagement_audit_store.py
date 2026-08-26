import os
import csv
import json
import time
from typing import Dict, Any, List, Tuple

AUDIT_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.json"))


class EngagementAuditStore:
    """
    내 블로그 전체 이웃 기준 감사 및 무반응 이웃 감사 리포트 저장소 (v8.0)
    - 1. Master 이웃 감사 CSV: data/buddy_engagement_audit_YYYYMMDD.csv
    - 2. 무반응 이웃 전용 CSV: data/unresponsive_buddies_YYYYMMDD.csv
    - 3. 비이웃 반응자 CSV: data/non_buddy_reactors_YYYYMMDD.csv
    - 4. 종합 감사 JSON: data/my_blog_engagement_audit.json
    """

    @classmethod
    def get_file_paths(cls) -> Tuple[str, str, str, str]:
        date_str = time.strftime("%Y%m%d")
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(data_dir, exist_ok=True)

        json_path = os.path.join(data_dir, "my_blog_engagement_audit.json")
        master_csv = os.path.join(data_dir, f"buddy_engagement_audit_{date_str}.csv")
        unresp_csv = os.path.join(data_dir, f"unresponsive_buddies_{date_str}.csv")
        non_buddy_csv = os.path.join(data_dir, f"non_buddy_reactors_{date_str}.csv")

        return json_path, master_csv, unresp_csv, non_buddy_csv

    @classmethod
    def save_v8(cls, report: Dict[str, Any]) -> Tuple[str, str, str, str]:
        json_path, master_csv, unresp_csv, non_buddy_csv = cls.get_file_paths()

        # 1. 종합 JSON 저장
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 공통 Master/Unresponsive 필드
        buddy_fieldnames = [
            "blog_id",
            "nickname",
            "blog_title",
            "group_name",
            "buddy_type",
            "added_date",
            "last_post_date",
            "like_count",
            "comment_count",
            "comment_entry_count",
            "engaged_post_count",
            "liked_only",
            "commented_only",
            "both_like_and_comment",
            "no_reaction",
            "is_recent_buddy",
            "scan_complete"
        ]

        def format_buddy_row(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "blog_id": row.get("blog_id", ""),
                "nickname": row.get("nickname", ""),
                "blog_title": row.get("blog_title", ""),
                "group_name": row.get("group_name", ""),
                "buddy_type": row.get("buddy_type", "이웃"),
                "added_date": row.get("added_date", ""),
                "last_post_date": row.get("last_post_date", ""),
                "like_count": row.get("like_count", 0),
                "comment_count": row.get("comment_count", 0),
                "comment_entry_count": row.get("comment_entry_count", 0),
                "engaged_post_count": row.get("engaged_post_count", 0),
                "liked_only": "Y" if row.get("liked_only") else "N",
                "commented_only": "Y" if row.get("commented_only") else "N",
                "both_like_and_comment": "Y" if row.get("both_like_and_comment") else "N",
                "no_reaction": "Y" if row.get("no_reaction") else "N",
                "is_recent_buddy": "Y" if row.get("is_recent_buddy") else "N",
                "scan_complete": "Y" if row.get("scan_complete") else "N"
            }

        # 2. Master CSV 저장 (전체 이웃)
        master_list = report.get("master_buddies", [])
        with open(master_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=buddy_fieldnames)
            writer.writeheader()
            for b in master_list:
                writer.writerow(format_buddy_row(b))

        # 3. 무반응 이웃 전용 CSV 저장
        unresp_list = report.get("unresponsive_buddies", [])
        with open(unresp_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=buddy_fieldnames)
            writer.writeheader()
            for u in unresp_list:
                writer.writerow(format_buddy_row(u))

        # 4. 비이웃 반응자 CSV 저장
        non_buddies = report.get("non_buddy_reactors", [])
        non_buddy_fieldnames = [
            "blog_id",
            "nickname",
            "profile_url",
            "like_count",
            "comment_count",
            "comment_entry_count",
            "engaged_post_count"
        ]
        with open(non_buddy_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=non_buddy_fieldnames)
            writer.writeheader()
            for nb in non_buddies:
                writer.writerow({
                    "blog_id": nb.get("blog_id", ""),
                    "nickname": nb.get("nickname", ""),
                    "profile_url": nb.get("profile_url", ""),
                    "like_count": nb.get("like_count", 0),
                    "comment_count": nb.get("comment_count", 0),
                    "comment_entry_count": nb.get("comment_entry_count", 0),
                    "engaged_post_count": nb.get("engaged_post_count", 0)
                })

        return json_path, master_csv, unresp_csv, non_buddy_csv

    # 하위 호환 save
    @classmethod
    def save(cls, report: Dict[str, Any]):
        return cls.save_v8(report)
