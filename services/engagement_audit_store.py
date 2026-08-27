"""Per-run exports; legacy files and same-day reports are never overwritten."""
import csv
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

AUDIT_JSON_PATH = str(Path(__file__).resolve().parent.parent / "data" / "my_blog_engagement_audit.json")
DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "audit_exports"


def csv_cell(value):
    if value is None: return ""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


class EngagementAuditStore:
    @classmethod
    def get_file_paths(cls, directory=None, run_id=None):
        identity = run_id or str(uuid4())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity): raise ValueError("invalid export run_id")
        root = Path(directory or DEFAULT_EXPORT_DIR) / identity
        return tuple(str(root / name) for name in ("report.json", "master.csv", "unresponsive.csv", "non_buddy.csv"))

    @classmethod
    def save_v8(cls, report, directory=None):
        paths = cls.get_file_paths(directory, report.get("run_id"))
        target = Path(paths[0]).parent
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): raise FileExistsError("immutable audit export already exists")
        staging = Path(tempfile.mkdtemp(prefix=".audit-export-", dir=target.parent))
        try:
            with (staging / "report.json").open("x", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
            count = min(20, max(5, report.get("requested_post_count", report.get("recent_post_count", 5)) or 5))
            mapping = {"블로그ID": "blog_id", "닉네임": "nickname", "블로그명": "blog_title", "그룹명": "group_name", "이웃구분": "buddy_type",
                "반응상태": "reaction_status", "참여여부": "is_participated", "이웃추가일": "added_date", "최근글작성일": "last_post_date",
                "새글소식설정": "new_posts_setting", "설정관측시각": "setting_observed_at", "공감한글수": "like_count", "댓글단글수": "comment_count",
                "총댓글개수": "comment_entry_count", "반응한글수": "engaged_post_count", "관측공감글수하한": "observed_like_count",
                "관측댓글글수하한": "observed_comment_count", "관측댓글개수하한": "observed_comment_entry_count", "유효글수": "eligible_post_count"}
            fields = list(mapping) + [f"글{i}_반응" for i in range(1, count + 1)] + ["신규유예여부", "검사완료여부", "공감만참여", "댓글만참여", "공감댓글모두", "무반응여부", "제외사유", "실행ID", "출처", "감사상태"]
            for name, key in (("master.csv", "master_buddies"), ("unresponsive.csv", "unresponsive_buddies"), ("non_buddy.csv", "non_buddy_reactors")):
                with (staging / name).open("x", newline="", encoding="utf-8-sig") as file:
                    writer = csv.DictWriter(file, fieldnames=fields)
                    writer.writeheader()
                    for row in report.get(key, []):
                        formatted = {header: row.get(source) for header, source in mapping.items()}
                        formatted["반응상태"] = row.get("reaction_status", "확인불가")
                        formatted["참여여부"] = row.get("is_participated", "확인불가")
                        reactions = row.get("post_reactions", {})
                        formatted.update({f"글{i}_반응": reactions.get(str(i), reactions.get(i, "확인불가")) for i in range(1, count + 1)})
                        def flag(value): return "O" if value is True else "X" if value is False else "확인불가"
                        formatted.update({"신규유예여부": "유예대상" if row.get("is_recent_buddy") is True else "유예아님" if row.get("is_recent_buddy") is False else "확인불가",
                            "검사완료여부": flag(row.get("scan_complete")), "공감만참여": flag(row.get("liked_only")), "댓글만참여": flag(row.get("commented_only")),
                            "공감댓글모두": flag(row.get("both_like_and_comment")), "무반응여부": flag(row.get("no_reaction")), "제외사유": "; ".join(row.get("exclusion_reasons", [])),
                            "실행ID": report.get("run_id"), "출처": report.get("source_kind", "legacy_unverified"), "감사상태": report.get("audit_state", "partial")})
                        writer.writerow({header: csv_cell(value) for header, value in formatted.items()})
            # Same-filesystem rename publishes a whole run; no consumer sees half-written exports.
            if target.exists(): raise FileExistsError("immutable audit export already exists")
            os.rename(staging, target)
        finally:
            if staging.exists(): shutil.rmtree(staging)
        return paths
