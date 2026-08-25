import os
import json
import time
from typing import Optional, Dict, Any
from app.models import FeedPost
from src.logger import logger

USER_LEARNING_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "user_learning_corpus.json"))


class UserLearningService:
    """
    사용자가 직접 수정한 최종 댓글 및 등록 이력을 수집/기록하는 학습 서비스:
    - 초안 대비 사용자가 수정한 내역을 JSON 데이터셋으로 영구 축적
    - 향후 알고리즘 개선 및 Few-shot 개인화 학습 데이터로 활용
    """

    @classmethod
    def record_submission(
        cls,
        post: FeedPost,
        initial_draft: str,
        final_submitted: str,
        category: str = "UNKNOWN"
    ):
        if not final_submitted or not final_submitted.strip():
            return

        initial_s = (initial_draft or "").strip()
        final_s = final_submitted.strip()
        is_edited = (initial_s != final_s)

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "post_url": post.url,
            "post_title": post.title or "",
            "category": category,
            "initial_draft": initial_s,
            "final_submitted": final_s,
            "is_user_edited": is_edited,
            "length": len(final_s)
        }

        try:
            os.makedirs(os.path.dirname(USER_LEARNING_FILE), exist_ok=True)
            existing = []
            if os.path.exists(USER_LEARNING_FILE):
                try:
                    with open(USER_LEARNING_FILE, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            existing.append(entry)

            with open(USER_LEARNING_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

            if is_edited:
                logger.log("  📝 [LEARNING] 사용자가 수정한 댓글을 학습용 데이터셋(user_learning_corpus.json)에 기록했습니다.")
            else:
                logger.log("  📝 [LEARNING] 등록된 댓글을 학습용 데이터셋(user_learning_corpus.json)에 기록했습니다.")
        except Exception as e:
            logger.log(f"  ⚠️ [LEARNING] 학습 데이터 저장 중 예외: {e}", "WARNING")
