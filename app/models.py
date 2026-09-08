from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FeedSourceType(str, Enum):
    NEIGHBOR = "neighbor"
    TARGETED_SEARCH = "targeted_search"
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
    SUBMISSION_UNKNOWN = "submission_unknown"


class UserAction(str, Enum):
    SUBMIT = "submit"
    NATIVE_SUBMIT = "native_submit"
    AUTO_SUBMIT = "auto_submit"
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
    COMMENT_INPUT_FAILED = "comment_input_failed"
    COMMENT_SUBMIT_FAILED = "comment_submit_failed"
    DAILY_LIMIT_REACHED = "daily_limit_reached"
    UNKNOWN_ERROR = "unknown_error"
    BROWSER_DISCONNECTED = "browser_disconnected"


class WorkerCommandType(str, Enum):
    APPLY_CLIPBOARD_COMMENT = "apply_clipboard_comment"
    GEMINI_RETRY = "gemini_retry"
    GEMINI_SKIP_POST = "gemini_skip_post"
    GEMINI_USE_LOCAL_ONCE = "gemini_use_local_once"
    SKIP_POST = "skip_post"


@dataclass
class WorkerCommand:
    kind: WorkerCommandType
    text: str = ""
    post_key: str = ""


@dataclass
class StylePlan:
    reaction_type: str = "detail_empathy"  # "short_impression", "detail_empathy", "light_curiosity"
    length_band: str = "standard"  # "concise", "standard"
    intensity: str = "crisp"  # "crisp", "playful"
    ending_family: str = "~네요"  # "~네요", "~겠어요", "~보여요"
    emphasis: str = "none"  # "none", "once"

    def to_dict(self) -> dict:
        return {
            "reaction_type": self.reaction_type,
            "length_band": self.length_band,
            "intensity": self.intensity,
            "ending_family": self.ending_family,
            "emphasis": self.emphasis,
        }


@dataclass
class PostActionPlan:
    """개별 글에 대한 컴포넌트 레벨 멱등성 실행 계획"""
    process_like: bool = True
    process_comment: bool = True
    local_like_recorded: bool = False
    local_comment_recorded: bool = False
    comment_sample_selected: Optional[bool] = None
    comment_sample_roll: Optional[float] = None
    style_plan: Optional[StylePlan] = None


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
