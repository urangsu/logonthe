"""Evidence contracts shared by collectors, immutable storage and the UI.

Live DOM capability remains unverified until a real authenticated probe is recorded.
A list without completion evidence never proves a person's absence.
"""
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

KST = dt.timezone(dt.timedelta(hours=9))
POLICY_VERSION = "naver-audit-v1.1-calendar2-mature48-min3"
AUDIT_STATES = {"complete", "partial", "failed", "cancelled"}
SOURCE_KINDS = {"live", "legacy_unverified", "fixture"}


def now_kst():
    return dt.datetime.now(KST)


def canonical_blog_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"blog.naver.com", "m.blog.naver.com"}:
            return None
        if parsed.username or parsed.password or parsed.port:
            return None
        parts = parsed.path.strip("/").split("/")
        if parsed.path in {"/PostList.naver", "/PostView.naver"}:
            ids = parse_qs(parsed.query).get("blogId", [])
            value = ids[0] if len(ids) == 1 else ""
        elif len(parts) == 1:
            value = parts[0]
        else:
            return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", value):
        return None
    # Route names without .naver are not identities either.
    if value.lower() in {"cart", "marketplace", "readhistorylist", "blogtagview", "checkin", "postlist", "postview", "buddylistmanage", "sympathyhistorylist", "intro", "prologue", "blog", "home", "main"}:
        return None
    return value.lower()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def nonnegative_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    match = re.fullmatch(r"\s*(\d{2}|\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?\s*", value)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return dt.date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None


def published_bounds(value, precision):
    """Return earliest/latest possible KST timestamps; date-only is never midnight certainty."""
    if precision == "date":
        date = parse_date(value)
        if date:
            return dt.datetime.combine(date, dt.time.min, KST), dt.datetime.combine(date, dt.time.max, KST)
    if precision in {"minute", "second"} and isinstance(value, str):
        try:
            stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
            stamp = stamp.astimezone(KST)
            return stamp, stamp + (dt.timedelta(seconds=59, microseconds=999999) if precision == "minute" else dt.timedelta())
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class AuditRun:
    run_id: str
    generated_at: str
    blog_id: str
    audit_state: str
    source_kind: str
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True)
class BuddySnapshot:
    run_id: str
    blog_id: str
    added_date: Optional[str] = None
    new_posts_setting: str = "unknown"
    setting_observed_at: Optional[str] = None


@dataclass(frozen=True)
class ReactionObservation:
    run_id: str
    log_no: str
    blog_id: str
    liked: Optional[bool]
    commented: Optional[bool]
    comment_entry_count: Optional[int] = None


@dataclass
class ParticipantCollection:
    items: list
    state: str
    displayed_count: Optional[int] = None
    count_unit: str = "people"
    observed_entry_count: Optional[int] = None
    terminal: bool = False
    page_fingerprints: list = field(default_factory=list)
    quality_issues: list = field(default_factory=list)
    source_kind: str = "live"
    capability_verified: bool = False
    observed_at: str = field(default_factory=lambda: now_kst().isoformat())

    def __iter__(self):
        # Existing callers can continue unpacking (items, state, displayed_count).
        return iter((self.items, self.state, self.displayed_count))

    def evidence(self):
        return {key: getattr(self, key) for key in (
            "state", "displayed_count", "count_unit", "observed_entry_count", "terminal",
            "page_fingerprints", "quality_issues", "source_kind", "capability_verified", "observed_at")}


class RecentPostCollection(list):
    def __init__(self, items=(), state="partial", *, quality_issues=None, source_kind="live", capability_verified=False):
        super().__init__(items)
        self.state = state
        self.quality_issues = quality_issues or []
        self.source_kind = source_kind
        self.capability_verified = capability_verified
