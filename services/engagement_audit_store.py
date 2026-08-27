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

        # 공통 Master/Unresponsive 필드 (한글 헤더 및 명확한 참여/무반응 분류)
        buddy_fieldnames = [
            "블로그ID",
            "닉네임",
            "블로그명",
            "그룹명",
            "이웃구분",
            "반응상태",
            "참여여부",
            "이웃추가일",
            "최근글작성일",
            "공감한글수",
            "댓글단글수",
            "총댓글개수",
            "반응한글수",
            "글1_반응",
            "글2_반응",
            "글3_반응",
            "글4_반응",
            "글5_반응",
            "신규유예여부",
            "검사완료여부",
            "공감만참여",
            "댓글만참여",
            "공감댓글모두",
            "무반응여부"
        ]

        def format_buddy_row(row: Dict[str, Any]) -> Dict[str, Any]:
            post_r = row.get("post_reactions", {})
            r_status = row.get("reaction_status")
            if not r_status:
                if row.get("both_like_and_comment"):
                    r_status = "공감+댓글"
                elif row.get("commented_only"):
                    r_status = "댓글만"
                elif row.get("liked_only"):
                    r_status = "공감만"
                elif row.get("is_recent_buddy"):
                    r_status = "신규유예"
                else:
                    r_status = "무반응"

            is_part = "참여" if row.get("engaged_post_count", 0) > 0 or r_status in ("공감+댓글", "댓글만", "공감만") else "미참여"

            return {
                "블로그ID": row.get("blog_id", ""),
                "닉네임": row.get("nickname", ""),
                "블로그명": row.get("blog_title", ""),
                "그룹명": row.get("group_name", ""),
                "이웃구분": row.get("buddy_type", "이웃"),
                "반응상태": r_status,
                "참여여부": is_part,
                "이웃추가일": row.get("added_date", ""),
                "최근글작성일": row.get("last_post_date", ""),
                "공감한글수": row.get("like_count", 0),
                "댓글단글수": row.get("comment_count", 0),
                "총댓글개수": row.get("comment_entry_count", 0),
                "반응한글수": row.get("engaged_post_count", 0),
                "글1_반응": post_r.get(1, "-"),
                "글2_반응": post_r.get(2, "-"),
                "글3_반응": post_r.get(3, "-"),
                "글4_반응": post_r.get(4, "-"),
                "글5_반응": post_r.get(5, "-"),
                "신규유예여부": "유예대상" if row.get("is_recent_buddy") else "일반",
                "검사완료여부": "완료" if row.get("scan_complete") else "일부",
                "공감만참여": "O" if row.get("liked_only") else "X",
                "댓글만참여": "O" if row.get("commented_only") else "X",
                "공감댓글모두": "O" if row.get("both_like_and_comment") else "X",
                "무반응여부": "O" if row.get("no_reaction") else "X"
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
            "블로그ID",
            "닉네임",
            "반응상태",
            "참여여부",
            "공감한글수",
            "댓글단글수",
            "총댓글개수",
            "반응한글수",
            "글1_반응",
            "글2_반응",
            "글3_반응",
            "글4_반응",
            "글5_반응",
            "프로필URL"
        ]
        with open(non_buddy_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=non_buddy_fieldnames)
            writer.writeheader()
            for nb in non_buddies:
                nb_post_r = nb.get("post_reactions", {})
                writer.writerow({
                    "블로그ID": nb.get("blog_id", ""),
                    "닉네임": nb.get("nickname", ""),
                    "반응상태": nb.get("reaction_status", "참여"),
                    "참여여부": "참여",
                    "공감한글수": nb.get("like_count", 0),
                    "댓글단글수": nb.get("comment_count", 0),
                    "총댓글개수": nb.get("comment_entry_count", 0),
                    "반응한글수": nb.get("engaged_post_count", 0),
                    "글1_반응": nb_post_r.get(1, "-"),
                    "글2_반응": nb_post_r.get(2, "-"),
                    "글3_반응": nb_post_r.get(3, "-"),
                    "글4_반응": nb_post_r.get(4, "-"),
                    "글5_반응": nb_post_r.get(5, "-"),
                    "프로필URL": nb.get("profile_url", "")
                })

        return json_path, master_csv, unresp_csv, non_buddy_csv

    # 하위 호환 save
    @classmethod
    def save(cls, report: Dict[str, Any]):
        return cls.save_v8(report)
