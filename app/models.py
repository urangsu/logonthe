from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FeedSourceType(str, Enum):
    NEIGHBOR = "neighbor"
    RECOMMENDATION = "recommendation"
    DIRECT = "direct"


class LikeState(str, Enum):
    LIKED = "liked"
    NOT_LIKED = "not_liked"
    UNKNOWN = "unknown"


class CommentSubmitState(str, Enum):
    NONE = "none"
    DRAFTED = "drafted"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNKNOWN = "unknown"


class UserAction(str, Enum):
    SUBMIT = "submit"
    SKIP = "skip"
    STOP = "stop"


class FailureReason(str, Enum):
    LOGIN_REQUIRED = "login_required"
    NAVIGATION_FAILED = "navigation_failed"
    POST_URL_INVALID = "post_url_invalid"
    POST_UNAVAILABLE = "post_unavailable"
    LIKE_BUTTON_NOT_FOUND = "like_button_not_found"
    LIKE_STATE_UNKNOWN = "like_state_unknown"
    LIKE_POPULARITY_SKIP = "like_popularity_skip"
    COMMENT_DISABLED = "comment_disabled"
    COMMENT_BUTTON_NOT_FOUND = "comment_button_not_found"
    COMMENT_EDITOR_NOT_FOUND = "comment_editor_not_found"
    COMMENT_SUBMIT_NOT_FOUND = "comment_submit_not_found"
    COMMENT_SUBMIT_UNVERIFIED = "comment_submit_unverified"
    BROWSER_DISCONNECTED = "browser_disconnected"


class WorkerCommandType(str, Enum):
    APPLY_CLIPBOARD_COMMENT = "apply_clipboard_comment"


@dataclass
class WorkerCommand:
    kind: WorkerCommandType
    text: str = ""


@dataclass
class FeedPost:
    key: str  # Canonical identifier e.g. "blogId:logNo"
    source: FeedSourceType
    url: str  # Canonical mobile post URL
    blog_id: Optional[str] = None
    log_no: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    excerpt: Optional[str] = None


@dataclass
class LikeProcessResult:
    state_before: LikeState = LikeState.UNKNOWN
    action_taken: bool = False
    state_after: LikeState = LikeState.UNKNOWN
    eligibility_reason: Optional[str] = None
    like_count: Optional[int] = None
    daily_visitors: Optional[int] = None
    error: Optional[str] = None


@dataclass
class CommentProcessResult:
    status: CommentSubmitState = CommentSubmitState.NONE
    draft_text: Optional[str] = None
    submitted_text: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PostProcessResult:
    post: FeedPost
    like_result: LikeProcessResult = field(default_factory=LikeProcessResult)
    comment_result: CommentProcessResult = field(default_factory=CommentProcessResult)
    success: bool = True
    failure_reason: Optional[FailureReason] = None
