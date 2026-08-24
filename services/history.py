import os
import json
from datetime import datetime
from typing import Dict, Any, Optional
from app.models import FeedPost, PostProcessResult, CommentSubmitState, LikeState

DEFAULT_HISTORY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history.json"))


class HistoryStore:
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

            # Schema v1 (URL array) -> v2 마이그레이션
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
        except Exception:
            self.posts = {}

    def save(self):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        payload = {
            "schema_version": 2,
            "updated_at": datetime.now().isoformat(),
            "posts": self.posts
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_processed(self, key: str) -> bool:
        """해당 post key가 이미 기록되었는지 확인"""
        return key in self.posts

    def is_comment_submitted(self, key: str) -> bool:
        """해당 post key에 이미 댓글이 성공적으로 등록되었는지 확인"""
        item = self.posts.get(key)
        if not item:
            return False
        return item.get("comment", {}).get("status") == CommentSubmitState.SUBMITTED.value

    def record_result(self, result: PostProcessResult):
        """작업 완료된 PostProcessResult를 구조화하여 기록"""
        post = result.post
        now_str = datetime.now().isoformat()

        record = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "blog_id": post.blog_id,
            "log_no": post.log_no,
            "title": post.title,
            "author": post.author,
            "updated_at": now_str,
            "like": {
                "state_before": result.like_result.state_before.value,
                "action": "clicked" if result.like_result.action_taken else "none",
                "state_after": result.like_result.state_after.value,
                "error": result.like_result.error
            },
            "comment": {
                "status": result.comment_result.status.value,
                "draft": result.comment_result.draft_text,
                "submitted_text": result.comment_result.submitted_text,
                "error": result.comment_result.error
            }
        }
        self.posts[post.key] = record
        self.save()

    def mark_skipped(self, post: FeedPost, reason: str = "user_skip"):
        """건너뛴 게시글 상태 기록"""
        self.posts[post.key] = {
            "key": post.key,
            "source": post.source.value,
            "url": post.url,
            "title": post.title,
            "author": post.author,
            "updated_at": datetime.now().isoformat(),
            "comment": {
                "status": CommentSubmitState.SKIPPED.value,
                "reason": reason
            }
        }
        self.save()
