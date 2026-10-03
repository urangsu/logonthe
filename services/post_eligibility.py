"""Recommendation guards, evaluated only for the currently opened post."""
import re
import time
from dataclasses import dataclass, replace
from urllib.parse import urlparse, parse_qs

from app.models import PostEligibilityResult
from app.errors import BrowserDisconnectedError, FatalSessionError, classify_playwright_failure, BrowserFailureKind
from app.run_control import StopRequestedException
from browser.session import WaitInterruptionReason
from naver.count_parser import parse_compact_count
from naver.resolver import MobileDOMResolver
from services.blog_neighbor_count import extract_neighbor_count, _EXTRACT_JS
from src.logger import logger


_VISITORS_JS = r"""() => {
    const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
    const roots = Array.from(document.querySelectorAll("[class*='ProfileBlock'], [class*='profile'], [class*='visitor'], [class*='Visitor'], [class*='visit__'], [class*='today__'], [class*='stat'], header"));
    const text = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
    const inArticle = e => e.closest('article, .se-main-container, .post_ct, #postViewArea');
    const candidates = new Set();
    const add = e => {
        if (!visible(e) || inArticle(e) || e.querySelector('article, .se-main-container, .post_ct, #postViewArea')) return;
        const t = text(e);
        if (t.length < 120 && /오늘|TODAY|비공개/i.test(t)) candidates.add(t);
    };
    roots.forEach(e => [e, ...e.querySelectorAll('span, em, p, strong, div, dl, dt, dd, li, a')].forEach(add));
    // Class hashes vary; recover small semantic counters without scanning article prose.
    const counterOnly = /^(?:오늘|TODAY)\s*[:：]?\s*[0-9][0-9,\s]*(?:\.[0-9]+)?\s*(?:천|만|[kKmM])?\s*(?:명)?$/i;
    document.querySelectorAll('div, span, em, p, li, strong, a, dl, dt, dd').forEach(e => {
        if (!visible(e) || inArticle(e)) return;
        const t = text(e);
        if (counterOnly.test(t) || (t.length < 120 && /오늘|TODAY/i.test(t) && /전체|누적|TOTAL/i.test(t))) add(e);
        if (/^(?:오늘|TODAY)\s*[:：]?$/i.test(t) && e.parentElement) add(e.parentElement);
        if (/^(?:방문자|오늘|TODAY)\s*비공개$/i.test(t)) add(e);
    });
    return [...candidates];
}"""


def extract_daily_visitors(candidates):
    values = {}
    for text in candidates:
        for match in re.finditer(r"(?:오늘|TODAY)\s*[:：]?\s*([0-9](?:[0-9,]|\s+(?=[0-9]))*(?:\.[0-9]+)?\s*(?:천|만|[kKmM])?)", text, re.I):
            value = parse_compact_count(re.sub(r"\s+", "", match.group(1)))
            if value is not None:
                values[value] = match.group(0)
    if len(values) > 1:
        return None, None, "conflicting_daily_visitors"
    if values:
        value, raw = next(iter(values.items()))
        return value, raw, None
    for text in candidates:
        if re.search(r"(?:방문자|오늘|TODAY)[^\n]{0,20}비공개|비공개[^\n]{0,20}(?:방문자|오늘|TODAY)", text, re.I):
            return None, text, "not_public"
    label_candidates = [text for text in candidates if re.search(r"오늘|TODAY", text, re.I)]
    if label_candidates:
        return None, label_candidates[0][:120], "daily_visitor_number_parse_failed"
    return None, None, "daily_visitor_region_not_found"


def page_blog_id(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"m.blog.naver.com", "blog.naver.com"}:
        return None
    query_id = parse_qs(parsed.query).get("blogId", [None])[0]
    path_id = parsed.path.strip('/').split('/')[0]
    return (query_id or path_id).lower()


@dataclass(frozen=True)
class ProfileMetric:
    value: int | None = None
    raw: str | None = None
    source: str = "profile_page"
    error: str | None = None
    cached: bool = False


class ProfileMetricsReader:
    def __init__(self, run_control=None):
        self.control = run_control
        self.cache = {}
        self.lookup_blogs = set()
        self.navigation_count = 0
        self.last_home_blog = None

    def checkpoint(self, stage):
        if self.control:
            self.control.checkpoint(stage)
            if self.control.skip_event.is_set():
                return False
        return True

    def _read(self, page, blog_id, need_neighbor, need_visitors, detail=False):
        if not self.checkpoint("profile_read"):
            return {}
        if page_blog_id(page.url) != blog_id.lower():
            return {k: ProfileMetric(error="profile_owner_mismatch") for k in ("neighbors", "visitors")}
        out = {}
        if need_neighbor:
            data = page.evaluate(_EXTRACT_JS) or {}
            if not self.checkpoint("after_profile_neighbor_read"):
                return {}
            owner = page_blog_id(data.get("ownerUrl", ""))
            if owner and owner != blog_id.lower():
                return {k: ProfileMetric(error="profile_owner_mismatch") for k in ("neighbors", "visitors")}
            candidates = data.get("candidates", [])
            # Article text is never a fallback source for the author's profile.
            if detail:
                candidates = [c for c in candidates if c.get("kind") == "profile_block"]
            value, raw, error = extract_neighbor_count(candidates)
            if value is None and data.get("profileNotPublic" if detail else "notPublic"):
                error = "not_public"
            out["neighbors"] = ProfileMetric(value, raw, "detail_profile" if detail else "profile_page", error)
        if need_visitors:
            value, raw, error = extract_daily_visitors(page.evaluate(_VISITORS_JS) or [])
            if not self.checkpoint("after_profile_visitor_read"):
                return {}
            out["visitors"] = ProfileMetric(value, raw, "detail_profile" if detail else "profile_page", error)
        return out

    def get(self, detail_page, stats_page, blog_id, need_neighbor, need_visitors):
        required = [k for k, needed in (("neighbors", need_neighbor), ("visitors", need_visitors)) if needed]
        if not required:
            return {}
        known = self.cache.get(blog_id, {})
        out = {k: replace(known[k], cached=True) for k in required if k in known}
        missing = [k for k in required if k not in out]
        if not missing:
            return out
        if not self.checkpoint("before_detail_profile_read"):
            return out
        self.lookup_blogs.add(blog_id)
        detail = self._read(detail_page, blog_id, "neighbors" in missing, "visitors" in missing, detail=True)
        for key, metric in detail.items():
            if metric.value is not None or metric.error == "not_public":
                out[key] = metric
        missing = [k for k in missing if k not in out]
        if missing and stats_page is None:
            out.update({k: ProfileMetric(error="no_profile_page") for k in missing})
        elif missing:
            for attempt in range(2):
                if not self.checkpoint("before_profile_navigation"):
                    return out
                try:
                    if attempt > 0 or self.last_home_blog != blog_id or page_blog_id(stats_page.url) != blog_id.lower():
                        self.navigation_count += 1
                        stats_page.goto(f"https://m.blog.naver.com/{blog_id}", wait_until="domcontentloaded", timeout=12000)
                    if not self.checkpoint("after_profile_navigation"):
                        return out
                    if "nid.naver.com" in stats_page.url or "/login" in stats_page.url:
                        raise FatalSessionError("profile_login_redirect")
                    if page_blog_id(stats_page.url) != blog_id.lower():
                        out.update({k: ProfileMetric(error="profile_owner_mismatch") for k in missing})
                        break
                    self.last_home_blog = blog_id
                    deadline = time.monotonic() + 4.0
                    while True:
                        metrics = self._read(stats_page, blog_id, "neighbors" in missing, "visitors" in missing)
                        if not self.checkpoint("profile_render_wait"):
                            return out
                        ready = all(k in metrics and (metrics[k].value is not None or metrics[k].error in {
                            "not_public", "profile_owner_mismatch", "conflicting_neighbor_counts", "conflicting_daily_visitors"
                        }) for k in missing)
                        if ready or time.monotonic() >= deadline:
                            break
                        if self.control:
                            reason = self.control.interruptible_wait(0.1, stage="profile_render_wait")
                            if reason == WaitInterruptionReason.SKIPPED:
                                return out
                        else:
                            time.sleep(0.1)
                    out.update(metrics)
                    if ready or attempt == 1:
                        break
                except (StopRequestedException, FatalSessionError):
                    raise
                except Exception as exc:
                    kind = classify_playwright_failure(exc, page=stats_page)
                    if kind in {BrowserFailureKind.PAGE_CLOSED, BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED}:
                        raise BrowserDisconnectedError(str(exc)) from exc
                    out.update({k: ProfileMetric(error="profile_navigation_failed") for k in missing})
                if not self.checkpoint("before_profile_retry"):
                    return out
        for key, metric in out.items():
            if metric.value is not None or metric.error == "not_public":
                self.cache.setdefault(blog_id, {})[key] = metric
        return out


class PostEligibilityService:
    def __init__(self, config, run_control=None):
        self.config = config
        self.reader = ProfileMetricsReader(run_control)
        self.decisions = {}

    def evaluate(self, detail_page, stats_page, post):
        cfg = self.config
        neighbor_max = int(cfg.get("recommendation_neighbor_count_max") or 0)
        need_visitors = bool(cfg.get("daily_visitor_guard_enabled", True))
        metrics = {}
        profile = None

        def finish(allowed, reason):
            result = PostEligibilityResult(allowed, reason, metrics)
            self.decisions[post.key] = result
            logger.log(f"[POST_ELIGIBILITY] post={post.key} allowed={allowed} reason={reason} metrics={metrics}")
            return result

        def save_profile(key):
            metric = (profile or {}).get(key, ProfileMetric(error="user_skipped"))
            metrics[key] = vars(metric)
            return metric.value

        if neighbor_max > 0:
            profile = self.reader.get(detail_page, stats_page, post.blog_id, True, False)
            count = save_profile("neighbors")
            if count is None:
                return finish(False, "unknown_neighbor_count")
            if count > neighbor_max:
                return finish(False, "neighbor_count_exceeded")
        if cfg.get("like_popularity_guard_enabled", True):
            deadline = time.monotonic() + 2.0
            while True:
                if not self.reader.checkpoint("before_post_like_count_read"):
                    return finish(False, "user_skipped")
                raw = MobileDOMResolver.get_like_count_text(detail_page)
                count = parse_compact_count(re.sub(r"\s+", "", raw)) if raw else None
                if not self.reader.checkpoint("after_post_like_count_read"):
                    return finish(False, "user_skipped")
                if count is not None or time.monotonic() >= deadline:
                    break
                if self.reader.control:
                    reason = self.reader.control.interruptible_wait(0.1, stage="post_like_count_render_wait")
                    if reason == WaitInterruptionReason.SKIPPED:
                        return finish(False, "user_skipped")
                else:
                    time.sleep(0.1)
            metrics["likes"] = {"value": count, "raw": raw, "source": "detail_page", "cached": False}
            if count is None:
                return finish(False, "unknown_like_count")
            if count >= int(cfg.get("like_count_skip_threshold", 999)):
                return finish(False, "like_count_exceeded")
        if need_visitors:
            profile = self.reader.get(detail_page, stats_page, post.blog_id, False, True)
            visitors = save_profile("visitors")
            if visitors is None:
                return finish(False, "unknown_daily_visitors")
            if visitors > int(cfg.get("daily_visitor_skip_threshold", 10000)):
                return finish(False, "daily_visitors_exceeded")
        if not self.reader.checkpoint("after_post_eligibility"):
            return finish(False, "user_skipped")
        return finish(True, "eligible")
