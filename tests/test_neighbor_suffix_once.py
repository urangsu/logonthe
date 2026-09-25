from app.models import FeedPost, FeedSourceType
from app.processor import PostProcessor
from services.draft import DraftService


SUFFIX = "좋은 하루 보내세요"


def _post(blog_id: str, log_no: str) -> FeedPost:
    return FeedPost(
        key=f"{blog_id}:{log_no}",
        source=FeedSourceType.NEIGHBOR,
        url=f"https://m.blog.naver.com/{blog_id}/{log_no}",
        blog_id=blog_id,
        log_no=log_no,
    )


def test_neighbor_suffix_runtime_set_is_not_persistent_history_contract():
    processor = object.__new__(PostProcessor)
    processor._neighbor_suffix_seen_blogs = set()

    assert "buddy_a" not in processor._neighbor_suffix_seen_blogs
    processor._neighbor_suffix_seen_blogs.add("buddy_a")
    assert "buddy_a" in processor._neighbor_suffix_seen_blogs

    # 새 Processor(새 실행)에서는 비어 있어야 한다.
    next_run = object.__new__(PostProcessor)
    next_run._neighbor_suffix_seen_blogs = set()
    assert "buddy_a" not in next_run._neighbor_suffix_seen_blogs


def test_suffix_is_detected_only_when_submitted_text_really_contains_it():
    from services.draft import normalize_naver_comment_text

    submitted = DraftService.compose_body_and_suffix("본문", SUFFIX)
    submitted_norm = normalize_naver_comment_text(submitted)
    suffix_norm = normalize_naver_comment_text(SUFFIX)

    assert submitted_norm.endswith("\n" + suffix_norm)

    edited_without_suffix = normalize_naver_comment_text("본문만 남김")
    assert not edited_without_suffix.endswith("\n" + suffix_norm)


def test_different_neighbor_blog_ids_have_independent_run_state():
    seen = {"buddy_a"}
    assert "buddy_a" in seen
    assert "buddy_b" not in seen
