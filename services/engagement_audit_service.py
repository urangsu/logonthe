import time
from typing import Dict, Any, List, Optional
from playwright.sync_api import Page
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_store import EngagementAuditStore
from src.logger import logger


class EngagementAuditService:
    """
    내 블로그 최근 글 반응자(공감/댓글 참여자) 수집 및 통합 분석 오케스트레이터 (v7.0)
    """

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
        logger.log(f"👥 [AUDIT] 내 블로그 ('{b_id}') 최근 {recent_post_count}개 글 반응자 수집 시작...")

        # 1. 최근 글 최대 N개 목록 조회
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
        people_map = {}  # blog_id -> dict

        # 2. 각 포스트별 공감 및 댓글 참여자 수집
        for idx, p in enumerate(posts, 1):
            if stop_event and stop_event.is_set():
                logger.log("⏹ [AUDIT] 사용자에 의해 수집이 중지되었습니다.", "WARNING")
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

            # 2-3. 동일 blog_id 기준 통합 병합
            # 공감자 반영
            for l in likers:
                t_id = l["blog_id"]
                if t_id not in people_map:
                    people_map[t_id] = {
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
                people_map[t_id]["liked_post_count"] += 1
                people_map[t_id]["total_engagement_count"] += 1
                if title not in people_map[t_id]["liked_posts"]:
                    people_map[t_id]["liked_posts"].append(title)

            # 댓글 작성자 반영
            for c in commenters:
                t_id = c["blog_id"]
                if t_id not in people_map:
                    people_map[t_id] = {
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
                people_map[t_id]["commented_post_count"] += 1
                people_map[t_id]["total_engagement_count"] += 1
                if title not in people_map[t_id]["commented_posts"]:
                    people_map[t_id]["commented_posts"].append(title)
                if c.get("comment_sample") and c["comment_sample"] not in people_map[t_id]["comment_samples"]:
                    people_map[t_id]["comment_samples"].append(c["comment_sample"])

        # 3. 정렬 규칙: 1) total desc 2) commented desc 3) liked desc
        people_list = list(people_map.values())
        people_list.sort(
            key=lambda x: (x["total_engagement_count"], x["commented_post_count"], x["liked_post_count"]),
            reverse=True
        )

        # 4. 통계 산출
        unique_count = len(people_list)
        liker_count = sum(1 for p in people_list if p["liked_post_count"] > 0)
        commenter_count = sum(1 for p in people_list if p["commented_post_count"] > 0)
        both_count = sum(1 for p in people_list if p["liked_post_count"] > 0 and p["commented_post_count"] > 0)

        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "blog_id": b_id,
            "recent_post_count": len(posts_report),
            "unique_participant_count": unique_count,
            "liker_count": liker_count,
            "commenter_count": commenter_count,
            "both_count": both_count,
            "posts": posts_report,
            "people": people_list
        }

        # 5. 파일 저장
        json_path, csv_path = EngagementAuditStore.save(report)

        logger.log("==================================================")
        logger.log(f"🎉 [AUDIT] 최근 글 {len(posts_report)}개 반응자 분석 완료!")
        logger.log(f"   👥 고유 반응자: {unique_count}명")
        logger.log(f"   ❤️ 공감 참여자: {liker_count}명")
        logger.log(f"   💬 댓글 작성자: {commenter_count}명")
        logger.log(f"   🌟 공감+댓글 모두 참여: {both_count}명")
        logger.log(f"   📁 저장 위치: {csv_path}")

        return {
            "success": True,
            "report": report,
            "json_path": json_path,
            "csv_path": csv_path
        }
