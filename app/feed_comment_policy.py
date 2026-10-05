from collections import defaultdict

from app.models import CommentSubmitState, FeedPost, PostProcessResult


class FeedCommentPolicy:
    """Run-local comment limits; likes and prior visits never consume a slot."""

    BLOG_LIMIT = 2

    def __init__(self, blog_limit=2):
        try:
            self.blog_limit = max(1, min(100, int(blog_limit)))
        except (TypeError, ValueError, OverflowError):
            self.blog_limit = self.BLOG_LIMIT
        self.comment_slots = defaultdict(set)
        self.new_comment_keys = set()
        self.previous_comment_keys = set()

    @staticmethod
    def blog_id(post: FeedPost) -> str:
        return (post.blog_id or post.key.split(":", 1)[0]).strip().lower()

    def can_comment(self, post: FeedPost) -> bool:
        return len(self.comment_slots[self.blog_id(post)]) < self.blog_limit

    def observe_previous(self, post: FeedPost) -> bool:
        if post.key in self.new_comment_keys or post.key in self.previous_comment_keys:
            return False
        self.previous_comment_keys.add(post.key)
        return True

    def record_result(self, result: PostProcessResult):
        comment = result.comment_result
        if comment.status == CommentSubmitState.SUBMITTED and comment.already_present:
            self.observe_previous(result.post)
        elif comment.status in (CommentSubmitState.SUBMITTED, CommentSubmitState.SUBMISSION_UNKNOWN):
            # Unknown submissions reserve a slot until reconciled, preventing a possible third comment.
            self.comment_slots[self.blog_id(result.post)].add(result.post.key)
            if comment.status == CommentSubmitState.SUBMITTED:
                self.new_comment_keys.add(result.post.key)
