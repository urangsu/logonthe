import time
import datetime
from typing import Dict, Any, List, Optional, Set, Literal
from playwright.sync_api import Page
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.buddy_list_collector import BuddyListCollector, BuddyInfo, BuddyCollectionResult
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_store import EngagementAuditStore
from src.logger import logger


class EngagementAuditService:
    """
    내 블로그 전체 이웃 기준 감사 및 무반응 이웃 식별 엔진 (v8.0 Master Rebuild)
    - 전체 이웃 전수(N명)를 베이스라인으로 100% 포괄하는 Master Join
    - 최근 실제 게시글 5개 대상 공감글수(0~5), 댓글글수(0~5), 댓글개수 전수 합산
    - no_reaction = (like_count == 0 and comment_count == 0)
    - 비이웃 반응자(non_buddy_reactors) 분리
    """

    @staticmethod
    def is_grace_period(added_date_str: str, days: int = 2) -> bool:
        """이웃 추가일이 최근 N일(기본 2일/48시간) 이내인지 판별"""
        if not added_date_str:
            return False
        try:
            cleaned = added_date_str.strip().replace(".", "-").rstrip("-")
            parts = [int(p) for p in cleaned.split("-")]
            if len(parts) == 3:
                year = 2000 + parts[0] if parts[0] < 100 else parts[0]
                added_dt = datetime.date(year, parts[1], parts[2])
                today = datetime.date.today()
                return (today - added_dt).days <= days
        except Exception:
            pass
        return False

    @classmethod
    def run_audit(
        cls,
        page: Page,
        my_blog_id: str,
        recent_post_count: int = 5,
        stop_event: Optional[Any] = None
    ) -> Dict[str, Any]:
        if not my_blog_id or not my_blog_id.strip():
            logger.log("❌ [AUDIT] 내 블로그 ID가 설정되지 않았습니다.", "ERROR")
            return {"success": False, "error": "my_blog_id_empty"}

        b_id = my_blog_id.strip()
        logger.log("==================================================")
        logger.log(f"👥 [AUDIT] '{b_id}' 블로그 전체 이웃 기준 감사 시작...")

        # [Step 1] 전체 이웃 목록 전수 수집 (BuddyListManage)
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

        posts_report = []
        all_post_scans_complete = (buddy_result.state == "complete")

        # Master Buddy Table 초기화 (전원 0으로 세팅)
        master_buddies_map: Dict[str, Dict[str, Any]] = {}
        for b_id_key, b_info in all_buddies.items():
            master_buddies_map[b_id_key] = {
                "blog_id": b_id_key,
                "nickname": b_info.nickname,
                "blog_title": b_info.blog_title,
                "group_name": b_info.group_name,
                "buddy_type": b_info.buddy_type,
                "added_date": b_info.added_date,
                "last_post_date": b_info.last_post_date or "",
                "like_count": 0,
                "comment_count": 0,
                "comment_entry_count": 0,
                "engaged_post_count": 0,
                "liked_only": False,
                "commented_only": False,
                "both_like_and_comment": False,
                "no_reaction": True,
                "is_recent_buddy": cls.is_grace_period(b_info.added_date, days=2),
                "reaction_status": "무반응",
                "is_participated": "미참여",
                "post_reactions": {1: "-", 2: "-", 3: "-", 4: "-", 5: "-"},
                "scan_complete": True
            }

        # 비이웃 반응자 맵
        non_buddy_map: Dict[str, Dict[str, Any]] = {}

        # [Step 3] 최근 N개 포스트별 반응자 수집 및 Master Join
        for idx, p in enumerate(posts, 1):
            if stop_event and stop_event.is_set():
                break

            log_no = p["log_no"]
            title = p["title"]
            logger.log(f"📌 [{idx}/{len(posts)}] '{title[:30]}...' (LogNo: {log_no}) 반응자 분석 중...")

            # 3-1. 공감 참여자
            likers, liker_state, liker_disp = ReactionParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            # 3-2. 댓글 작성자
            commenters, commenter_state, cmt_disp = CommentParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            if liker_state != "complete" or commenter_state != "complete":
                all_post_scans_complete = False

            posts_report.append({
                "post_url": p["url"],
                "log_no": log_no,
                "title": title,
                "liker_count": len(likers),
                "liker_displayed": liker_disp,
                "liker_scan_state": liker_state,
                "commenter_count": len(commenters),
                "commenter_displayed": cmt_disp,
                "commenter_scan_state": commenter_state
            })

            # 포스트별 참여자 세트
            post_liker_ids = {l["blog_id"]: l for l in likers}
            post_commenter_ids = {c["blog_id"]: c for c in commenters}
            post_all_reactors = set(post_liker_ids.keys()) | set(post_commenter_ids.keys())

            # 이웃 반응 집계
            for r_id in post_all_reactors:
                has_like = r_id in post_liker_ids
                has_comment = r_id in post_commenter_ids
                cmt_entries = post_commenter_ids[r_id]["comment_entry_count"] if has_comment else 0

                # 포스트별 반응 유형
                if has_like and has_comment:
                    r_type = "공감+댓글"
                elif has_like:
                    r_type = "공감"
                elif has_comment:
                    r_type = "댓글"
                else:
                    r_type = "-"

                if r_id in master_buddies_map:
                    mb = master_buddies_map[r_id]
                    if has_like:
                        mb["like_count"] += 1
                    if has_comment:
                        mb["comment_count"] += 1
                        mb["comment_entry_count"] += cmt_entries
                    mb["engaged_post_count"] += 1
                    mb["post_reactions"][idx] = r_type
                else:
                    # 비이웃 반응자 집계
                    if r_id not in non_buddy_map:
                        nick = post_commenter_ids.get(r_id, {}).get("nickname") or post_liker_ids.get(r_id, {}).get("nickname") or r_id
                        non_buddy_map[r_id] = {
                            "blog_id": r_id,
                            "nickname": nick,
                            "profile_url": f"https://m.blog.naver.com/{r_id}",
                            "like_count": 0,
                            "comment_count": 0,
                            "comment_entry_count": 0,
                            "engaged_post_count": 0,
                            "reaction_status": "미분류",
                            "is_participated": "참여",
                            "post_reactions": {1: "-", 2: "-", 3: "-", 4: "-", 5: "-"}
                        }
                    nb = non_buddy_map[r_id]
                    if has_like:
                        nb["like_count"] += 1
                    if has_comment:
                        nb["comment_count"] += 1
                        nb["comment_entry_count"] += cmt_entries
                    nb["engaged_post_count"] += 1
                    nb["post_reactions"][idx] = r_type

        # [Step 4] Master Table Flag 계산 및 무반응자 추출
        master_rows: List[Dict[str, Any]] = []
        unresponsive_rows: List[Dict[str, Any]] = []

        for b_id_key, row in master_buddies_map.items():
            l_cnt = row["like_count"]
            c_cnt = row["comment_count"]

            row["liked_only"] = (l_cnt > 0 and c_cnt == 0)
            row["commented_only"] = (c_cnt > 0 and l_cnt == 0)
            row["both_like_and_comment"] = (l_cnt > 0 and c_cnt > 0)
            row["no_reaction"] = (l_cnt == 0 and c_cnt == 0)
            row["scan_complete"] = all_post_scans_complete

            # 명확한 한글 분류 상태값 지정
            if row["both_like_and_comment"]:
                row["reaction_status"] = "공감+댓글"
                row["is_participated"] = "참여"
            elif row["commented_only"]:
                row["reaction_status"] = "댓글만"
                row["is_participated"] = "참여"
            elif row["liked_only"]:
                row["reaction_status"] = "공감만"
                row["is_participated"] = "참여"
            elif row["is_recent_buddy"]:
                row["reaction_status"] = "신규유예"
                row["is_participated"] = "미참여"
            else:
                row["reaction_status"] = "무반응"
                row["is_participated"] = "미참여"

            master_rows.append(row)
            if row["no_reaction"]:
                unresponsive_rows.append(row)

        # 비이웃 반응자 상태값 지정
        for nb_id, nb_row in non_buddy_map.items():
            nl = nb_row["like_count"]
            nc = nb_row["comment_count"]
            if nl > 0 and nc > 0:
                nb_row["reaction_status"] = "공감+댓글"
            elif nc > 0:
                nb_row["reaction_status"] = "댓글만"
            elif nl > 0:
                nb_row["reaction_status"] = "공감만"
            else:
                nb_row["reaction_status"] = "기타"

        # 정렬: 
        # Master: 1) engaged_post_count desc, 2) group_name, 3) nickname
        master_rows.sort(key=lambda x: (x["engaged_post_count"], x["comment_count"], x["like_count"]), reverse=True)
        # Unresponsive: 1) group_name, 2) added_date desc
        unresponsive_rows.sort(key=lambda x: (x["group_name"], x["added_date"]), reverse=True)

        # Non-buddy list
        non_buddies_list = list(non_buddy_map.values())
        non_buddies_list.sort(key=lambda x: (x["engaged_post_count"], x["comment_count"]), reverse=True)

        # [Step 5] 통계 산출
        total_buddies = len(master_rows)
        unresponsive_count = len(unresponsive_rows)
        reacted_buddies_count = total_buddies - unresponsive_count
        both_count = sum(1 for r in master_rows if r["both_like_and_comment"])
        liked_only_count = sum(1 for r in master_rows if r["liked_only"])
        commented_only_count = sum(1 for r in master_rows if r["commented_only"])
        grace_count = sum(1 for r in unresponsive_rows if r["is_recent_buddy"])
        real_unresponsive_count = unresponsive_count - grace_count

        audit_state: Literal["complete", "partial", "failed"] = "complete" if all_post_scans_complete else "partial"

        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "blog_id": b_id,
            "audit_state": audit_state,
            "recent_post_count": len(posts_report),
            "total_buddies_count": total_buddies,
            "expected_buddies_count": buddy_result.expected_total,
            "reacted_buddies_count": reacted_buddies_count,
            "unresponsive_buddies_count": unresponsive_count,
            "grace_period_buddies_count": grace_count,
            "real_unresponsive_count": real_unresponsive_count,
            "both_like_and_comment_count": both_count,
            "liked_only_count": liked_only_count,
            "commented_only_count": commented_only_count,
            "non_buddy_reactors_count": len(non_buddies_list),
            "posts": posts_report,
            "master_buddies": master_rows,
            "unresponsive_buddies": unresponsive_rows,
            "non_buddy_reactors": non_buddies_list
        }

        # [Step 6] 산출물 3개 CSV 및 JSON 저장
        json_path, master_csv_path, unresp_csv_path, non_buddy_csv_path = EngagementAuditStore.save_v8(report)

        logger.log("==================================================")
        logger.log(f"🎉 [AUDIT] 전체 이웃 {total_buddies}명 기준 무반응 감사 완료! (상태: {audit_state.upper()})")
        logger.log(f"   👥 전체 등록 이웃 (Master): {total_buddies}명")
        logger.log(f"   ❤️ 최근 글에 반응한 이웃: {reacted_buddies_count}명 (공감+댓글 모두: {both_count}명, 공감만: {liked_only_count}명, 댓글만: {commented_only_count}명)")
        logger.log(f"   🚫 최근 글 무반응 이웃: {unresponsive_count}명 (48시간 신규 유예: {grace_count}명 / 실질 무반응: {real_unresponsive_count}명)")
        logger.log(f"   🌐 비이웃 참여자: {len(non_buddies_list)}명")
        logger.log(f"   📁 Master 이웃 감사 CSV: {master_csv_path}")
        logger.log(f"   📁 무반응 이웃 전용 CSV: {unresp_csv_path}")
        logger.log(f"   📁 비이웃 반응자 CSV: {non_buddy_csv_path}")

        return {
            "success": True,
            "audit_state": audit_state,
            "report": report,
            "json_path": json_path,
            "master_csv_path": master_csv_path,
            "unresponsive_csv_path": unresp_csv_path,
            "non_buddy_csv_path": non_buddy_csv_path
        }
