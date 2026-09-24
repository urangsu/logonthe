import os
import tempfile

from app.models import CommentProcessResult, CommentSubmitState, FeedPost, FeedSourceType
from services.history import HistoryStore


SUFFIX = "좋은 하루 보내세요"


def _post(blog_id: str, log_no: str) -> FeedPost:
    return FeedPost(
        key=f"{blog_id}:{log_no}",
        source=FeedSourceType.NEIGHBOR,
        url=f"https://m.blog.naver.com/{blog_id}/{log_no}",
        blog_id=blog_id,
        log_no=log_no,
    )


def test_suffix_flag_defaults_false():
    assert CommentProcessResult().suffix_applied is False


def test_submitted_suffix_is_remembered_per_blog():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(os.path.join(tmp, "history.json"))
        post = _post("buddy_a", "1")
        result = CommentProcessResult(
            status=CommentSubmitState.SUBMITTED,
            draft_text=f"본문\n{SUFFIX}",
            submitted_text=f"본문\n{SUFFIX}",
            suffix_applied=True,
        )
        store.record_comment_checkpoint(post, result)

        assert store.has_suffix_applied_for_blog("buddy_a", SUFFIX) is True
        assert store.has_suffix_applied_for_blog("buddy_b", SUFFIX) is False


def test_legacy_submitted_text_with_same_suffix_is_detected():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(os.path.join(tmp, "history.json"))
        store.posts["buddy_a:1"] = {
            "blog_id": "buddy_a",
            "comment": {
                "status": CommentSubmitState.SUBMITTED.value,
                "submitted_text": f"예전 댓글\n{SUFFIX}",
            },
        }
        assert store.has_suffix_applied_for_blog("buddy_a", SUFFIX) is True


def test_failed_or_unconfirmed_comment_does_not_consume_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(os.path.join(tmp, "history.json"))
        store.posts["buddy_a:1"] = {
            "blog_id": "buddy_a",
            "comment": {
                "status": CommentSubmitState.FAILED.value,
                "submitted_text": f"실패 댓글\n{SUFFIX}",
                "suffix_applied": True,
            },
        }
        store.posts["buddy_a:2"] = {
            "blog_id": "buddy_a",
            "comment": {
                "status": CommentSubmitState.SUBMISSION_UNKNOWN.value,
                "submitted_text": f"미확정 댓글\n{SUFFIX}",
                "suffix_applied": True,
                "unconfirmed": True,
            },
        }
        assert store.has_suffix_applied_for_blog("buddy_a", SUFFIX) is False


def test_resolved_pre_submit_preserves_suffix_flag_and_blog_id():
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(os.path.join(tmp, "history.json"))
        store.record_pre_submit(
            "buddy_a:3",
            f"본문\n{SUFFIX}",
            url="https://m.blog.naver.com/buddy_a/3",
            blog_id="buddy_a",
            suffix_applied=True,
        )
        assert store.has_suffix_applied_for_blog("buddy_a", SUFFIX) is False

        store.resolve_unconfirmed_post("buddy_a:3", CommentSubmitState.SUBMITTED)
        assert store.has_suffix_applied_for_blog("buddy_a", SUFFIX) is True
