import time
import datetime
from typing import Dict, Any, List, Optional, Set
from playwright.sync_api import Page
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.buddy_list_collector import BuddyListCollector, BuddyInfo
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_store import EngagementAuditStore
from src.logger import logger


class EngagementAuditService:
    """
    내 블로그 이웃 전수 조사 및 무반응 이웃 추출 감사 서비스 (v7.1)
    - Step 1: 전체 이웃 목록 전수 수집 (BuddyListCollector)
    - Step 2: 최근 N개 포스트의 공감(Likers) 및 댓글(Commenters) 반응자 수집
    - Step 3: 차집합(AllBuddies - ReactedSet) 계산 및 48시간 신규 추가 유예 필터링
    - Step 4: 종합 리포트(JSON) 및 무반응자 전용 CSV(unresponsive_buddies_YYYYMMDD.csv) 생성
    """

    @staticmethod
    def is_grace_period(added_date_str: str, days: int = 2) -> bool:
        """이웃 추가일이 최근 N일(기본 2일/48시간) 이내인지 판별"""
        if not added_date_str:
            return False
        try:
            # 형식: YY.MM.DD 또는 YYYY.MM.DD
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
        logger.log(f"👥 [AUDIT] '{b_id}' 블로그 이웃 전수 및 무반응 이웃 감사 시작...")

        # [Step 1] 전체 이웃 목록 전수 수집 (BuddyListManage)
        all_buddies: Dict[str, BuddyInfo] = BuddyListCollector.collect_all_buddies(
            page=page,
            blog_id=b_id,
            stop_event=stop_event
        )

        if stop_event and stop_event.is_set():
            return {"success": False, "error": "stop_requested"}

        # [Step 2] 최근 글 최대 N개 목록 조회
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
        reacted_people_map: Dict[str, Dict[str, Any]] = {}
        all_reacted_blog_ids: Set[str] = set()

        for idx, p in enumerate(posts, 1):
            if stop_event and stop_event.is_set():
                break

            log_no = p["log_no"]
            title = p["title"]
            logger.log(f"📌 [{idx}/{len(posts)}] '{title[:30]}...' (LogNo: {log_no}) 반응자 분석 중...")

            # 2-1. 공감 참여자 수집
            likers, liker_state = ReactionParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            # 2-2. 댓글 작성자 수집
            commenters, commenter_state = CommentParticipantCollector.collect(
                page=page,
                blog_id=b_id,
                log_no=log_no,
                stop_event=stop_event
            )

            posts_report.append({
                "post_url": p["url"],
                "log_no": log_no,
                "title": title,
                "liker_count": len(likers),
                "liker_scan_state": liker_state,
                "commenter_count": len(commenters),
                "commenter_scan_state": commenter_state
            })

            # 공감자 반영
            for l in likers:
                t_id = l["blog_id"]
                all_reacted_blog_ids.add(t_id)
                if t_id not in reacted_people_map:
                    reacted_people_map[t_id] = {
                        "blog_id": t_id,
                        "nickname": l.get("nickname", t_id),
                        "profile_url": l.get("profile_url", f"https://m.blog.naver.com/{t_id}"),
                        "liked_post_count": 0,
                        "commented_post_count": 0,
                        "total_engagement_count": 0,
                        "liked_posts": [],
                        "commented_posts": [],
                        "comment_samples": []
                    }
                reacted_people_map[t_id]["liked_post_count"] += 1
                reacted_people_map[t_id]["total_engagement_count"] += 1
                if title not in reacted_people_map[t_id]["liked_posts"]:
                    reacted_people_map[t_id]["liked_posts"].append(title)

            # 댓글 작성자 반영
            for c in commenters:
                t_id = c["blog_id"]
                all_reacted_blog_ids.add(t_id)
                if t_id not in reacted_people_map:
                    reacted_people_map[t_id] = {
                        "blog_id": t_id,
                        "nickname": c.get("nickname", t_id),
                        "profile_url": c.get("profile_url", f"https://m.blog.naver.com/{t_id}"),
                        "liked_post_count": 0,
                        "commented_post_count": 0,
                        "total_engagement_count": 0,
                        "liked_posts": [],
                        "commented_posts": [],
                        "comment_samples": []
                    }
                reacted_people_map[t_id]["commented_post_count"] += 1
                reacted_people_map[t_id]["total_engagement_count"] += 1
                if title not in reacted_people_map[t_id]["commented_posts"]:
                    reacted_people_map[t_id]["commented_posts"].append(title)
                if c.get("comment_sample") and c["comment_sample"] not in reacted_people_map[t_id]["comment_samples"]:
                    reacted_people_map[t_id]["comment_samples"].append(c["comment_sample"])

        # [Step 3] 차집합 및 무반응 이웃 추출
        unresponsive_buddies: List[Dict[str, Any]] = []
        for b_id_key, b_info in all_buddies.items():
            if b_id_key not in all_reacted_blog_ids:
                is_grace = cls.is_grace_period(b_info.added_date, days=2)
                unresponsive_buddies.append({
                    "blog_id": b_id_key,
                    "nickname": b_info.nickname,
                    "blog_title": b_info.blog_title,
                    "group_name": b_info.group_name,
                    "buddy_type": b_info.buddy_type,
                    "added_date": b_info.added_date,
                    "last_post_date": b_info.last_post_date or "",
                    "is_grace_period": is_grace
                })

        # 정렬: 그룹명 순 -> 추가일 역순
        unresponsive_buddies.sort(key=lambda x: (x["group_name"], x["added_date"]), reverse=True)

        # 반응한 전체 참여자 리스트 정렬
        reacted_list = list(reacted_people_map.values())
        reacted_list.sort(
            key=lambda x: (x["total_engagement_count"], x["commented_post_count"], x["liked_post_count"]),
            reverse=True
        )

        # [Step 4] 종합 리포트 생성 및 저장
        total_buddies_count = len(all_buddies)
        unresponsive_count = len(unresponsive_buddies)
        grace_count = sum(1 for u in unresponsive_buddies if u["is_grace_period"])
        real_unresponsive_count = unresponsive_count - grace_count
        reacted_buddy_count = sum(1 for b in all_buddies if b in all_reacted_blog_ids)

        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "blog_id": b_id,
            "recent_post_count": len(posts_report),
            "total_buddies_count": total_buddies_count,
            "reacted_buddies_count": reacted_buddy_count,
            "unresponsive_buddies_count": unresponsive_count,
            "grace_period_buddies_count": grace_count,
            "real_unresponsive_count": real_unresponsive_count,
            "unique_participant_count": len(reacted_list),
            "liker_count": sum(1 for p in reacted_list if p["liked_post_count"] > 0),
            "commenter_count": sum(1 for p in reacted_list if p["commented_post_count"] > 0),
            "posts": posts_report,
            "unresponsive_buddies": unresponsive_buddies,
            "people": reacted_list
        }

        json_path, csv_path, unresp_csv_path = EngagementAuditStore.save(report)

        logger.log("==================================================")
        logger.log(f"🎉 [AUDIT] 이웃 전수 및 무반응 감사 완료!")
        logger.log(f"   👥 전체 등록 이웃: {total_buddies_count}명")
        logger.log(f"   ❤️ 최근 글에 반응한 이웃: {reacted_buddy_count}명")
        logger.log(f"   🚫 최근 글 무반응 이웃: {unresponsive_count}명 (신규 48시간 유예: {grace_count}명 / 실질 무반응: {real_unresponsive_count}명)")
        logger.log(f"   📁 무반응 이웃 목록 CSV: {unresp_csv_path}")
        logger.log(f"   📁 반응자 종합 통계 CSV: {csv_path}")

        return {
            "success": True,
            "report": report,
            "json_path": json_path,
            "csv_path": csv_path,
            "unresponsive_csv_path": unresp_csv_path
        }
