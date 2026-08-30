import time
from datetime import date, timedelta
from typing import Dict, Any, List, Optional, Tuple, Literal
from playwright.sync_api import Page

from src.logger import logger
from services.buddy_list_collector import BuddyListCollector, BuddyCollectionResult, BuddyInfo
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_store import EngagementAuditStore


class EngagementAuditService:
    """
    내 블로그 전체 이웃 기준 감사 및 무반응 이웃 감사 서비스 (V13.1)
    - 이웃별 관리에 직접 필요한 최소 항목만 집계하고 CSV 하나로 저장
    - 비이웃 반응자는 집계 및 저장하지 않고 즉시 스킵
    """

    @classmethod
    def is_grace_period(cls, added_date_str: str, days: int = 2) -> bool:
        """한국 달력 날짜 기준으로 추가일과 그 이후 N일을 유예한다."""
        if not added_date_str:
            return False
        clean = added_date_str.strip().rstrip(".")
        parts = clean.split(".")
        if len(parts) != 3:
            return False

        try:
            y = int(parts[0])
            m = int(parts[1])
            d = int(parts[2])
            if y < 100:
                y += 2000

            added_day = date(y, m, d)
            today = date.today()
            return added_day <= today <= added_day + timedelta(days=days)
        except Exception:
            return False

    @classmethod
    def run_audit(
        cls,
        page: Page,
        my_blog_id: str,
        recent_post_count: int = 5,
        stop_event: Optional[Any] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        내 블로그 전체 이웃 기준 감사 실행
        """
        b_id = my_blog_id.strip() if my_blog_id else ""
        if not b_id:
            logger.log("❌ [AUDIT] 블로그 ID가 지정되지 않았습니다.", "ERROR")
            return {"success": False, "error": "empty_blog_id"}

        logger.log(f"👥 [AUDIT] '{b_id}' 블로그 전체 이웃 기준 감사 시작...")

        # [Step 1] 전체 이웃 목록 전수 수집
        buddy_result: BuddyCollectionResult = BuddyListCollector.collect_all_buddies(
            page=page,
            blog_id=b_id,
            stop_event=stop_event
        )

        if stop_event and stop_event.is_set():
            return {"success": False, "error": "stop_requested"}

        if buddy_result.state == "failed" or not buddy_result.buddies:
            logger.log("❌ [AUDIT] 이웃 목록 수집에 실패하여 감사를 중단합니다.", "ERROR")
            return {"success": False, "error": "buddy_collection_failed"}

        all_buddies = buddy_result.buddies
        logger.log(f"📌 [AUDIT] 베이스라인 이웃: 총 {len(all_buddies)}명 (수집 상태: {buddy_result.state})")

        # [Step 2] 최근 일반 공개 포스트 최대 N개 목록 조회
        posts = MyBlogRecentPostService.fetch_recent_posts(
            page=page,
            blog_id=b_id,
            max_count=recent_post_count,
            stop_event=stop_event
        )

        if not posts:
            logger.log("⚠️ [AUDIT] 최근 공개 글을 찾지 못했습니다.", "WARNING")
            return {"success": False, "error": "no_recent_posts_found"}

        all_post_scans_complete = (buddy_result.state == "complete")
        checked_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        scope_str = f"최근 {len(posts)}개 글"

        # Master Buddy Table 초기화
        master_buddies_map: Dict[str, Dict[str, Any]] = {}
        for b_id_key, b_info in all_buddies.items():
            master_buddies_map[b_id_key] = {
                "blog_id": b_id_key,
                "nickname": b_info.nickname,
                "blog_title": b_info.blog_title,
                "blog_url": f"https://m.blog.naver.com/{b_id_key}",
                "group_name": b_info.group_name,
                "buddy_type": b_info.buddy_type,
                "added_date": b_info.added_date,
                "last_post_date": b_info.last_post_date or "",
                "news_feed_state": getattr(b_info, "news_feed_state", "확인 불가"),
                "like_count": 0,
                "comment_count": 0,
                "comment_entry_count": 0,
                "engaged_post_count": 0,
                "observation_scope": scope_str,
                "checked_at": checked_time_str,
                "liked_only": False,
                "commented_only": False,
                "both_like_and_comment": False,
                "no_reaction": True,
                "is_recent_buddy": cls.is_grace_period(b_info.added_date, days=2),
                "reaction_category": "확인 불가",
                "scan_complete": True
            }

        # [Step 3] 최근 N개 포스트별 반응자 수집 및 Master Join
        for idx, p in enumerate(posts, 1):
            if stop_event and stop_event.is_set():
                break

            log_no = p["log_no"]
            title = p["title"]
            logger.log(f"📌 [{idx}/{len(posts)}] '{title[:30]}...' (LogNo: {log_no}) 반응자 분석 중...")

            # 3-1. 공감 참여자
            likers, liker_state, _ = ReactionParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            # 3-2. 댓글 작성자
            commenters, commenter_state, _ = CommentParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            if liker_state != "complete" or commenter_state != "complete":
                all_post_scans_complete = False

            post_liker_ids = {l["blog_id"]: l for l in likers}
            post_commenter_ids = {c["blog_id"]: c for c in commenters}
            post_all_reactors = set(post_liker_ids.keys()) | set(post_commenter_ids.keys())

            # 이웃 반응 집계 (비이웃은 즉시 스킵)
            for r_id in post_all_reactors:
                if r_id not in master_buddies_map:
                    continue

                has_like = r_id in post_liker_ids
                has_comment = r_id in post_commenter_ids
                cmt_entries = post_commenter_ids[r_id]["comment_entry_count"] if has_comment else 0

                mb = master_buddies_map[r_id]
                if has_like:
                    mb["like_count"] += 1
                if has_comment:
                    mb["comment_count"] += 1
                    mb["comment_entry_count"] += cmt_entries
                mb["engaged_post_count"] += 1

        # [Step 4] Master Table Flag 계산 및 무반응자 추출
        master_rows: List[Dict[str, Any]] = []
        unresponsive_rows: List[Dict[str, Any]] = []

        for b_id_key, row in master_buddies_map.items():
            l_cnt = row["like_count"]
            c_cnt = row["comment_count"]
            engaged = row["engaged_post_count"]

            row["liked_only"] = (l_cnt > 0 and c_cnt == 0)
            row["commented_only"] = (c_cnt > 0 and l_cnt == 0)
            row["both_like_and_comment"] = (l_cnt > 0 and c_cnt > 0)
            row["scan_complete"] = all_post_scans_complete

            if engaged > 0:
                row["no_reaction"] = False
                row["reaction_category"] = "반응 확인"
            elif all_post_scans_complete:
                row["no_reaction"] = True
                row["reaction_category"] = "무반응"
            else:
                row["no_reaction"] = None
                row["reaction_category"] = "확인 불가"

            master_rows.append(row)
            if row["no_reaction"] is True:
                unresponsive_rows.append(row)

        # 정렬: 
        # Master: 1) engaged_post_count desc, 2) group_name, 3) nickname
        master_rows.sort(key=lambda x: (x["engaged_post_count"], x["comment_count"], x["like_count"]), reverse=True)
        # Unresponsive: 1) group_name, 2) added_date desc
        unresponsive_rows.sort(key=lambda x: (x["group_name"], x["added_date"]), reverse=True)

        # [Step 5] 통계 산출
        total_buddies = len(master_rows)
        unresponsive_count = len(unresponsive_rows)
        reacted_buddies_count = sum(1 for r in master_rows if r["engaged_post_count"] > 0)
        both_count = sum(1 for r in master_rows if r["both_like_and_comment"])
        liked_only_count = sum(1 for r in master_rows if r["liked_only"])
        commented_only_count = sum(1 for r in master_rows if r["commented_only"])
        grace_count = sum(1 for r in unresponsive_rows if r["is_recent_buddy"])
        real_unresponsive_count = unresponsive_count - grace_count

        audit_state: Literal["complete", "partial", "failed"] = "complete" if all_post_scans_complete else "partial"

        report = {
            "generated_at": checked_time_str,
            "blog_id": b_id,
            "audit_state": audit_state,
            "recent_post_count": len(posts),
            "total_buddies_count": total_buddies,
            "expected_buddies_count": buddy_result.expected_total,
            "reacted_buddies_count": reacted_buddies_count,
            "unresponsive_buddies_count": unresponsive_count,
            "grace_period_buddies_count": grace_count,
            "real_unresponsive_count": real_unresponsive_count,
            "both_like_and_comment_count": both_count,
            "liked_only_count": liked_only_count,
            "commented_only_count": commented_only_count,
            "master_buddies": master_rows,
            "unresponsive_buddies": unresponsive_rows,
        }

        # [Step 6] 이웃별 누적 집계 CSV 하나만 원자적으로 저장
        summary_csv = EngagementAuditStore.save_summary(report, output_dir=output_dir)

        logger.log(f"==================================================")
        logger.log(f"🎉 [AUDIT] 전체 이웃 {total_buddies}명 기준 무반응 감사 완료! (상태: {audit_state.upper()})")
        logger.log(f"   👥 전체 등록 이웃 (Master): {total_buddies}명")
        logger.log(f"   ❤️ 최근 글에 반응한 이웃: {reacted_buddies_count}명 (공감+댓글 모두: {both_count}명, 공감만: {liked_only_count}명, 댓글만: {commented_only_count}명)")
        logger.log(f"   🚫 최근 글 무반응 이웃: {unresponsive_count}명 (추가일 기준 참고: {grace_count}명 / 확인된 무반응: {real_unresponsive_count}명)")
        logger.log(f"   이웃별 누적 반응 CSV: {summary_csv}")

        return {
            "success": True,
            "audit_state": audit_state,
            "report": report,
            "summary_csv_path": summary_csv,
        }
