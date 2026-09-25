import os
import json
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState
)
from src.logger import logger

DEFAULT_HISTORY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history.json"))


class HistoryPersistenceError(Exception):
    """히스토리 파일 디스크 저장 실패 시 발생하는 예외"""
    pass


class HistoryStore:
    """
    히스토리 저장소 (Monotonic Merge & Atomic File I/O)
    - 성공 상태(SUBMITTED, LIKED)를 하위 상태나 실패로 다운그레이드하지 않는 단조 병합(Monotonic Merge) 보장
    - 액션별(Like, Comment) 독립 병합 처리
    - 원자적 파일 교체(Atomic Write)로 프로세스 비정상 종료 시 데이터 손상 방지
    """

    def __init__(self, file_path: str = DEFAULT_HISTORY_PATH):
        self.file_path = file_path
        self.posts: Dict[str, Any] = {}
        self.load()

    def load(self):
        if not os.path.exists(self.file_path):
            self.posts = {}
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                self.posts = {}
                for url in data:
                    self.posts[url] = {
                        "url": url,
                        "comment": {"status": "submitted", "legacy": True}
                    }
                self.save()
            elif isinstance(data, dict):
                self.posts = data.get("posts", {})
        except Exception as e:
            logger.log(f"[HISTORY] 히스토리 로드 중 예외: {e}", "WARNING")
            self.posts = {}

    def save(self, raise_on_error: bool = False):
        target_dir = os.path.dirname(self.file_path)
        os.makedirs(target_dir, exist_ok=True)

        payload = {
            "schema_version": 2,
            "updated_at": datetime.now().isoformat(),
            "posts": self.posts
        }

        temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="history_", suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.file_path)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.log(f"[HISTORY] 히스토리 원자적 저장 실패: {e}", "WARNING")
            if raise_on_error:
                raise HistoryPersistenceError(f"히스토리 원자적 저장 실패: {e}") from e

    def is_processed(self, key: str) -> bool:
        return key in self.posts

    def get_comment_status(self, key: str) -> Optional[str]:
        item = self.posts.get(key)
        if not item:
            return None
        return item.get("comment", {}).get("status")

    def is_comment_unconfirmed(self, key: str) -> bool:
        return self.get_comment_status(key) == CommentSubmitState.SUBMISSION_UNKNOWN.value

    def is_comment_submitted(self, key: str) -> bool:
        return self.get_comment_status(key) == CommentSubmitState.SUBMITTED.value

    def record_pre_submit(self, post_key: str, text: str, url: Optional[str] = None):
        """클릭 직전 프로세스 비정상 종료를 대비한 사전 미확정 상태 영속 기록 (실패 시 예외 발생)"""
        existing = self.posts.get(post_key, {})
        comment_data = existing.get("comment", {})
        
        # 이미 제출되었거나 이미 미확정 상태면 덮어쓰지 않음
        if comment_data.get("status") in [CommentSubmitState.SUBMITTED.value, CommentSubmitState.SUBMISSION_UNKNOWN.value]:
            return

        if url:
            existing["url"] = url
        elif not existing.get("url") and ":" in post_key:
            parts = post_key.split(":", 1)
            existing["url"] = f"https://m.blog.naver.com/{parts[0]}/{parts[1]}"

        comment_data.update({
            "status": CommentSubmitState.SUBMISSION_UNKNOWN.value,
            "submitted_text": text,
            "attempted_at": datetime.now().isoformat(),
            "unconfirmed": True,
        })
        existing["comment"] = comment_data
        existing["updated_at"] = datetime.now().isoformat()
        self.posts[post_key] = existing
        self.save(raise_on_error=True)

    def get_unconfirmed_posts(self) -> Dict[str, Any]:
        """미확정(SUBMISSION_UNKNOWN) 상태의 포스트 목록 반환"""
        unconfirmed = {}
        for k, v in self.posts.items():
            cmt = v.get("comment", {})
            if cmt.get("status") == CommentSubmitState.SUBMISSION_UNKNOWN.value or cmt.get("unconfirmed"):
                unconfirmed[k] = v
        return unconfirmed

    def resolve_unconfirmed_post(self, post_key: str, status: CommentSubmitState):
        """미확정 포스트의 최종 등록 확인 후 상태 확정 (SUBMITTED 또는 SKIPPED/FAILED)"""
        if post_key in self.posts:
            cmt = self.posts[post_key].get("comment", {})
            cmt["status"] = status.value
            cmt["unconfirmed"] = (status == CommentSubmitState.SUBMISSION_UNKNOWN)
            cmt["resolved_at"] = datetime.now().isoformat()
            self.posts[post_key]["comment"] = cmt
            self.posts[post_key]["updated_at"] = datetime.now().isoformat()
            self.save()

    def clear_unconfirmed_post(self, post_key: str):
        """사용자 확인 또는 미등록 확정에 따른 미확정 플래그 해제"""
        if post_key in self.posts:
            cmt = self.posts[post_key].get("comment", {})
            if cmt.get("status") == CommentSubmitState.SUBMISSION_UNKNOWN.value:
                del self.posts[post_key]["comment"]
                self.save()

    def get_recent_submitted_comments(self, limit: int = 10) -> list[str]:
        """
        최근 실제 등록 확인(SUBMITTED)된 최종 댓글 텍스트 목록을 최신순(내림차순)으로 반환합니다.
        - 사용자가 수정한 경우 최종 문장(submitted_text)을 우선 사용
        - 등록 실패(FAILED), 건너뜀(SKIPPED), 결과 미확정(SUBMISSION_UNKNOWN) 초안은 엄격히 배제
        - 정렬 순서: updated_at 내림차순 (최신 등록순)
        """
        results = []
        # Sort posts by updated_at / resolved_at descending (chronological descending)
        sorted_posts = sorted(
            self.posts.values(),
            key=lambda x: str(x.get("updated_at") or x.get("comment", {}).get("resolved_at") or x.get("comment", {}).get("attempted_at") or ""),
            reverse=True
        )
        for p in sorted_posts:
            cmt = p.get("comment", {})
            if cmt.get("status") == CommentSubmitState.SUBMITTED.value and not cmt.get("unconfirmed"):
                text = cmt.get("submitted_text") or cmt.get("draft")
                if text and text.strip():
                    results.append(text.strip())
            if len(results) >= limit:
                break
        return results

    def is_liked(self, key: str) -> bool:
        item = self.posts.get(key)
        if not item:
            return False
        return item.get("like", {}).get("state_after") == LikeState.LIKED.value

    def record_like_checkpoint(self, post: FeedPost, like_result: LikeProcessResult):
        """서버 공감 확정 즉시 체크포인트 기록 (후속 단계 오류 시에도 공감 멱등성 보존)"""
        now_str = datetime.now().isoformat()
        existing = self.posts.get(post.key, {})
        existing_like = existing.get("like", {})
        final_like_state = LikeState.LIKED.value if (like_result.state_after == LikeState.LIKED or existing_like.get("state_after") == LikeState.LIKED.value) else like_result.state_after.value

        like_record = {
            "state_before": like_result.state_before.value,
            "action": "clicked" if like_result.action_taken else existing_like.get("action", "none"),
            "state_after": final_like_state,
            "error": like_result.error or existing_like.get("error")
        }

        self.posts[post.key] = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "blog_id": post.blog_id,
            "log_no": post.log_no,
            "title": post.title or existing.get("title"),
            "author": post.author or existing.get("author"),
            "updated_at": now_str,
            "like": like_record,
            "comment": existing.get("comment", {"status": "none"})
        }
        self.save()

    def record_comment_checkpoint(self, post: FeedPost, comment_result: CommentProcessResult):
        """서버 댓글 확정 즉시 체크포인트 기록"""
        now_str = datetime.now().isoformat()
        existing = self.posts.get(post.key, {})
        existing_comment = existing.get("comment", {})
        existing_status = existing_comment.get("status")

        final_comment_status = comment_result.status.value
        final_submitted_text = comment_result.submitted_text or existing_comment.get("submitted_text")
        if existing_status == CommentSubmitState.SUBMITTED.value:
            final_comment_status = CommentSubmitState.SUBMITTED.value
            final_submitted_text = existing_comment.get("submitted_text") or comment_result.submitted_text

        comment_record = {
            "status": final_comment_status,
            "draft": comment_result.draft_text or existing_comment.get("draft"),
            "submitted_text": final_submitted_text,
            "error": comment_result.error or existing_comment.get("error")
        }

        self.posts[post.key] = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "blog_id": post.blog_id,
            "log_no": post.log_no,
            "title": post.title or existing.get("title"),
            "author": post.author or existing.get("author"),
            "updated_at": now_str,
            "like": existing.get("like", {"state_after": "unknown"}),
            "comment": comment_record
        }
        self.save()

    def record_result(self, result: PostProcessResult):
        """단조 병합(Monotonic Merge)을 적용하여 이전 성공 기록을 덮어쓰지 않음"""
        post = result.post
        now_str = datetime.now().isoformat()

        existing = self.posts.get(post.key, {})

        # 1. Like 상태 병합
        existing_like = existing.get("like", {})
        new_like_after = result.like_result.state_after.value
        # 이전이 이미 LIKED인 경우 다운그레이드 방지
        final_like_state = LikeState.LIKED.value if existing_like.get("state_after") == LikeState.LIKED.value else new_like_after

        like_record = {
            "state_before": result.like_result.state_before.value,
            "action": "clicked" if result.like_result.action_taken else existing_like.get("action", "none"),
            "state_after": final_like_state,
            "error": result.like_result.error or existing_like.get("error")
        }

        # 2. Comment 상태 병합 (SUBMITTED > DRAFTED > SKIPPED > NONE)
        existing_comment = existing.get("comment", {})
        existing_status = existing_comment.get("status")
        new_status = result.comment_result.status.value

        final_comment_status = new_status
        final_submitted_text = result.comment_result.submitted_text or existing_comment.get("submitted_text")

        if existing_status == CommentSubmitState.SUBMITTED.value:
            final_comment_status = CommentSubmitState.SUBMITTED.value
            final_submitted_text = existing_comment.get("submitted_text") or result.comment_result.submitted_text
        elif existing_status == CommentSubmitState.SUBMISSION_UNKNOWN.value and new_status != CommentSubmitState.SUBMITTED.value:
            # 미확정(SUBMISSION_UNKNOWN) 상태는 명시적인 SUBMITTED 확정 전에는 FAILED/SKIPPED로 덮어쓰지 않음
            final_comment_status = CommentSubmitState.SUBMISSION_UNKNOWN.value
            final_submitted_text = existing_comment.get("submitted_text") or result.comment_result.submitted_text

        comment_record = {
            "status": final_comment_status,
            "draft": result.comment_result.draft_text or existing_comment.get("draft"),
            "submitted_text": final_submitted_text,
            "error": result.comment_result.error or existing_comment.get("error")
        }

        record = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "blog_id": post.blog_id,
            "log_no": post.log_no,
            "title": post.title or existing.get("title"),
            "author": post.author or existing.get("author"),
            "updated_at": now_str,
            "like": like_record,
            "comment": comment_record
        }

        self.posts[post.key] = record
        self.save()

    def mark_skipped(self, post: FeedPost, reason: str = "user_skip"):
        """기존 SUBMITTED 상태를 파괴하지 않고 안전하게 스킵 기록 병합"""
        existing = self.posts.get(post.key, {})
        now_str = datetime.now().isoformat()

        # 이미 댓글이 등록된 글이면 status 유지
        existing_comment = existing.get("comment", {})
        if existing_comment.get("status") == CommentSubmitState.SUBMITTED.value:
            return

        comment_record = {
            "status": CommentSubmitState.SKIPPED.value,
            "reason": reason
        }

        record = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "title": post.title or existing.get("title"),
            "author": post.author or existing.get("author"),
            "updated_at": now_str,
            "like": existing.get("like", {"state_after": LikeState.UNKNOWN.value}),
            "comment": comment_record
        }

        self.posts[post.key] = record
        self.save()
