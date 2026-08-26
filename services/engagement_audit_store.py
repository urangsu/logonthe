import os
import csv
import json
import time
from typing import Dict, Any, List, Tuple

AUDIT_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.json"))
AUDIT_CSV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.csv"))


class EngagementAuditStore:
    """
    내 블로그 반응자 및 무반응 이웃 감사 리포트 저장소 (JSON 및 CSV)
    """

    @classmethod
    def get_unresponsive_csv_path(cls) -> str:
        date_str = time.strftime("%Y%m%d")
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", f"unresponsive_buddies_{date_str}.csv"))

    @classmethod
    def save(cls, report: Dict[str, Any]) -> Tuple[str, str, str]:
        os.makedirs(os.path.dirname(AUDIT_JSON_PATH), exist_ok=True)
        unresp_csv_path = cls.get_unresponsive_csv_path()

        # 1. 종합 JSON 저장
        with open(AUDIT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 2. 반응 참여자 CSV 저장
        people = report.get("people", [])
        fieldnames = [
            "blog_id",
            "nickname",
            "profile_url",
            "liked_post_count",
            "commented_post_count",
            "total_engagement_count",
            "liked_post_titles",
            "commented_post_titles",
            "latest_engagement_at",
            "comment_samples",
            "is_liker",
            "is_commenter"
        ]

        with open(AUDIT_CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in people:
                writer.writerow({
                    "blog_id": p.get("blog_id", ""),
                    "nickname": p.get("nickname", ""),
                    "profile_url": p.get("profile_url", ""),
                    "liked_post_count": p.get("liked_post_count", 0),
                    "commented_post_count": p.get("commented_post_count", 0),
                    "total_engagement_count": p.get("total_engagement_count", 0),
                    "liked_post_titles": " | ".join(p.get("liked_posts", [])),
                    "commented_post_titles": " | ".join(p.get("commented_posts", [])),
                    "latest_engagement_at": report.get("generated_at", ""),
                    "comment_samples": " || ".join(p.get("comment_samples", [])),
                    "is_liker": "Y" if p.get("liked_post_count", 0) > 0 else "N",
                    "is_commenter": "Y" if p.get("commented_post_count", 0) > 0 else "N"
                })

        # 3. 무반응 이웃 전용 CSV 저장
        unresponsive = report.get("unresponsive_buddies", [])
        unresp_fieldnames = [
            "blog_id",
            "nickname",
            "blog_title",
            "group_name",
            "buddy_type",
            "added_date",
            "last_post_date",
            "is_grace_period"
        ]

        with open(unresp_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=unresp_fieldnames)
            writer.writeheader()
            for u in unresponsive:
                writer.writerow({
                    "blog_id": u.get("blog_id", ""),
                    "nickname": u.get("nickname", ""),
                    "blog_title": u.get("blog_title", ""),
                    "group_name": u.get("group_name", ""),
                    "buddy_type": u.get("buddy_type", "이웃"),
                    "added_date": u.get("added_date", ""),
                    "last_post_date": u.get("last_post_date", ""),
                    "is_grace_period": "Y" if u.get("is_grace_period") else "N"
                })

        return AUDIT_JSON_PATH, AUDIT_CSV_PATH, unresp_csv_path
