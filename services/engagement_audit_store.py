import os
import csv
import json
import time
from typing import Dict, Any, List

AUDIT_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.json"))
AUDIT_CSV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "my_blog_engagement_audit.csv"))


class EngagementAuditStore:
    """
    내 블로그 반응자 분석 데이터셋 저장 서비스 (JSON 및 CSV)
    """

    @classmethod
    def save(cls, report: Dict[str, Any]) -> Tuple_Path:
        os.makedirs(os.path.dirname(AUDIT_JSON_PATH), exist_ok=True)

        # 1. JSON 저장
        with open(AUDIT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 2. CSV 저장
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

        return AUDIT_JSON_PATH, AUDIT_CSV_PATH


class Tuple_Path(tuple):
    pass
