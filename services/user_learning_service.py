import os
import json
import time
import hashlib
import tempfile
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
        category: str = "UNKNOWN",
        anchor: str = "",
        evidence_span: str = "",
        source: str = "unknown",
        rejection_reason: str = "",
    ):
        if not final_submitted or not final_submitted.strip():
            return
        cls.record_decision(
            post=post,
            initial_draft=initial_draft,
            final_submitted=final_submitted,
            category=category,
            anchor=anchor,
            evidence_span=evidence_span,
            source=source,
            decision="edited" if (initial_draft or "").strip() != final_submitted.strip() else "adopted",
            rejection_reason=rejection_reason,
        )

    @classmethod
    def infer_anchor(cls, post: FeedPost, comment: str) -> tuple[str, str]:
        """Infer only a comment token that is visibly grounded in title/body."""
        from services.comments.entities import extract_entity_tokens

        context = f"{post.title or ''} {post.excerpt or ''}"
        for token in sorted(set(extract_entity_tokens(comment or "")), key=len, reverse=True):
            index = context.find(token)
            if index >= 0:
                start = max(0, index - 30)
                end = min(len(context), index + len(token) + 30)
                return token, context[start:end].strip()
        return "", ""

    @classmethod
    def record_decision(
        cls,
        post: FeedPost,
        initial_draft: str,
        final_submitted: str = "",
        category: str = "UNKNOWN",
        anchor: str = "",
        evidence_span: str = "",
        source: str = "unknown",
        decision: str = "skipped",
        rejection_reason: str = "",
    ):
        if decision not in {"adopted", "edited", "skipped", "rejected"}:
            raise ValueError(f"unsupported learning decision: {decision}")

        initial_s = (initial_draft or "").strip()
        final_s = (final_submitted or "").strip()
        is_edited = (initial_s != final_s)

        if not anchor:
            anchor, evidence_span = cls.infer_anchor(post, final_s or initial_s)

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "post_key_hash": hashlib.sha256((post.key or post.url).encode("utf-8")).hexdigest(),
            "category": category,
            "anchor": anchor,
            "evidence_span": evidence_span[:160],
            "source": source,
            "decision": decision,
            "initial_draft": initial_s,
            "final_submitted": final_s,
            "is_user_edited": is_edited,
            "length": len(final_s),
            "rejection_reason": rejection_reason,
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

            fd, tmp = tempfile.mkstemp(prefix=".learning-", suffix=".json", dir=os.path.dirname(USER_LEARNING_FILE))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, USER_LEARNING_FILE)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if decision == "skipped":
                logger.log("  📝 [LEARNING] 사용자가 건너뛴 초안과 사유를 기록했습니다.")
            elif is_edited:
                logger.log("  📝 [LEARNING] 사용자가 수정한 댓글을 학습용 데이터셋(user_learning_corpus.json)에 기록했습니다.")
            else:
                logger.log("  📝 [LEARNING] 등록된 댓글을 학습용 데이터셋(user_learning_corpus.json)에 기록했습니다.")
        except Exception as e:
            logger.log(f"  ⚠️ [LEARNING] 학습 데이터 저장 중 예외: {e}", "WARNING")
