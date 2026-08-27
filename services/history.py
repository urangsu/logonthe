import os
import json
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional
from app.models import FeedPost, PostProcessResult, CommentSubmitState, LikeState
from src.logger import logger

DEFAULT_HISTORY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history.json"))


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
                # Read-only migration; retain the original until an actual result is saved.
            elif isinstance(data, dict):
                self.posts = data.get("posts", {})
        except Exception as e:
            logger.log(f"[HISTORY] 히스토리 로드 중 예외: {e}", "WARNING")
            self.posts = {}

    def save(self):
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

    def is_processed(self, key: str) -> bool:
        return key in self.posts

    def is_comment_submitted(self, key: str) -> bool:
        item = self.posts.get(key)
        if not item:
            return False
        return item.get("comment", {}).get("status") == CommentSubmitState.SUBMITTED.value

    def is_liked(self, key: str) -> bool:
        item = self.posts.get(key)
        if not item:
            return False
        return item.get("like", {}).get("state_after") == LikeState.LIKED.value

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
