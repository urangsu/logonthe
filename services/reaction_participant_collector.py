"""Read-only participant extraction. Never search global blog links for people."""
from typing import Any, Optional
from browser.session import interruptible_wait
from services.audit_models import ParticipantCollection, canonical_blog_id, fingerprint, nonnegative_int

# Reused inside scoped row extractors only. Hosts, routes and IDs are validated twice.
CANONICAL_ID_JS = r"""
function blogIdentity(href) {
    try {
        const u = new URL(href, location.href);
        if (!['http:', 'https:'].includes(u.protocol) || !['blog.naver.com', 'm.blog.naver.com'].includes(u.hostname) || u.username || u.password || u.port) return null;
        const path = u.pathname.replace(/^\/+|\/+$/g, '');
        const id = ['PostList.naver', 'PostView.naver'].includes(path) ? u.searchParams.get('blogId') : (!path.includes('/') ? path : null);
        return id && /^[A-Za-z0-9_-]{1,50}$/.test(id) ? id.toLowerCase() : null;
    } catch (_) { return null; }
}
function shown(el) { return !!el && !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden'; }
function countValue(el) {
    if (!shown(el)) return null;
    const m = el.textContent.replace(/,/g, '').match(/\d+/);
    return m ? Number(m[0]) : null;
}
"""

REACTION_DOM = "(target) => {" + CANONICAL_ID_JS + r"""
    const roots = Array.from(document.querySelectorAll('ul.list_sympathy, .u_likeit_list, .user_list')).filter(shown);
    // Generic .user_list is accepted only within a sympathy list route, never a post/menu.
    const route = new URL(location.href);
    const routeMatches = route.pathname.endsWith('/SympathyHistoryList.naver') && ['blog.naver.com', 'm.blog.naver.com'].includes(route.hostname) && target && route.searchParams.get('blogId') === target.blogId && route.searchParams.get('logNo') === String(target.logNo);
    const root = routeMatches && roots.length === 1 ? roots[0] : null;
    if (!root) return {scopeVerified: false, items: [], terminal: false};
    const rows = Array.from(root.querySelectorAll(':scope > li'));
    const items = []; let unresolved = 0;
    for (const row of rows) {
        const profiles = Array.from(row.querySelectorAll('a[href]')).map(a => ({a, id: blogIdentity(a.href)})).filter(x => x.id);
        const ids = [...new Set(profiles.map(x => x.id))];
        if (ids.length !== 1) { unresolved++; continue; }
        const found = profiles.find(x => x.id === ids[0]);
        items.push({blog_id: found.id, nickname: found.a.textContent.trim().split('\n')[0], profile_url: found.a.href});
    }
    const container = root.parentElement;
    const count = container.querySelector('.u_likeit_list_count, .sympathy_count');
    const more = container.querySelector('button.btn_more, a.btn_more, .u_likeit_list_btn_more');
    const end = container.querySelector('.list_end, .no_more, .empty_list, .u_likeit_list_end');
    const disabled = shown(more) && (more.disabled || more.getAttribute('aria-disabled') === 'true');
    const terminal = disabled || (shown(end) && /마지막|더 이상|없습니다|없어요/.test(end.textContent));
    return {scopeVerified: true, items, displayedCount: countValue(count), countUnit: 'people', unresolvedEntries: unresolved,
            terminal, hasMore: shown(more) && !disabled};
}"""


def collect_pages(page, script, more_selector, stop_event, *, kind="reaction", max_pages=20, target=None):
    """A bounded, evidence-bearing read loop; all uncertain exits stay partial."""
    people = {}; entries = {}; fingerprints = []; issues = []
    expected = None; loaded = None; terminal = False; scoped = False
    cancelled = False; count_unit = "entries" if kind == "comment" else "people"
    for _ in range(max_pages):
        if stop_event and stop_event.is_set():
            cancelled = True; issues.append("stop_requested"); break
        try:
            data = page.evaluate(script, target)
            if not isinstance(data, dict) or data.get("scopeVerified") is not True:
                issues.append("participant_scope_unverified"); break
            scoped = True
            raw = data.get("items", [])
            current = {}
            for item in raw:
                identity = canonical_blog_id(item.get("blog_id"))
                # A supplied URL must agree with the row identity, not a menu route.
                supplied_url = item.get("profile_url")
                if not identity or (supplied_url and canonical_blog_id(supplied_url) != identity):
                    issues.append("invalid_participant_identity"); continue
                value = dict(item, blog_id=identity, profile_url=f"https://m.blog.naver.com/{identity}")
                if kind == "comment":
                    count = nonnegative_int(value.get("comment_entry_count"))
                    if count is None or count == 0:
                        issues.append("comment_entry_count_unknown"); continue
                    value["comment_entry_count"] = count
                if identity in current:
                    issues.append('duplicate_participant_rows')
                current[identity] = value
            mark = fingerprint({"items": sorted(current.items()), "entries": data.get("entries", []), "loaded": data.get("totalLoadedEntries")})
            if mark in fingerprints:
                issues.append("duplicate_page"); break
            fingerprints.append(mark)
            before = set(people)
            for identity, value in current.items():
                if kind == "comment" and identity in people:
                    value["comment_entry_count"] = max(value["comment_entry_count"], people[identity]["comment_entry_count"])
                people[identity] = value
            page_count = nonnegative_int(data.get("displayedCount"))
            if page_count is not None:
                if data.get("countUnit") != count_unit:
                    issues.append("count_unit_mismatch")
                elif expected is not None and expected != page_count:
                    issues.append("displayed_count_changed")
                else:
                    expected = page_count
            if data.get("unresolvedEntries", 0):
                issues.append("unresolved_participant_rows")
            if kind == "comment":
                observed = nonnegative_int(data.get("totalLoadedEntries"))
                if observed is None:
                    issues.append("loaded_entry_count_unknown")
                else:
                    loaded = max(loaded or 0, observed)
                # Explicit entry IDs prevent double counting across append/replacement pages.
                page_entries = data.get('entries', [])
                ids = [str(entry['entry_id']) for entry in page_entries if entry.get('entry_id')]
                if observed is None or len(ids) != observed:
                    issues.append('entry_identity_coverage_incomplete')
                if len(set(ids)) != len(ids):
                    issues.append('duplicate_comment_entries')
                for entry in page_entries:
                    key = entry.get("entry_id")
                    if key:
                        if str(key) in entries and entries[str(key)] != entry:
                            issues.append('comment_identity_changed')
                            continue
                        entries[str(key)] = entry
                if len(fingerprints) > 1 and not data.get("entries"):
                    issues.append("entry_identity_unverified")
                if entries:
                    counts = {}
                    for entry in entries.values():
                        identity = canonical_blog_id(entry.get("blog_id"))
                        if identity and not entry.get("mine"):
                            counts[identity] = counts.get(identity, 0) + 1
                    for identity, value in people.items():
                        value["comment_entry_count"] = counts.get(identity, value["comment_entry_count"])
                    loaded = max(loaded or 0, len(entries))
            terminal = data.get("terminal") is True
            if terminal:
                break
            if data.get("hasMore") is not True:
                issues.append("terminal_evidence_missing"); break
            if len(fingerprints) > 1 and set(people) == before and kind != "comment":
                issues.append("no_progress"); break
            more = page.locator(more_selector).first
            if more.count() != 1 or not more.is_visible():
                issues.append("pagination_control_missing"); break
            more.click(timeout=1500)
            interruptible_wait(stop_event, 0.5)
        except Exception:
            issues.append("collection_error"); break
    else:
        issues.append("page_limit_reached")
    if stop_event and stop_event.is_set():
        cancelled = True
    observed_count = loaded if kind == "comment" else len(people)
    if expected is not None and expected != observed_count:
        issues.append("displayed_count_mismatch")
    if not terminal and not issues:
        issues.append("terminal_evidence_missing")
    state = "cancelled" if cancelled else "failed" if not scoped else "partial" if issues else "complete"
    return ParticipantCollection(list(people.values()), state, expected, count_unit, loaded, terminal,
                                 fingerprints, list(dict.fromkeys(issues)))


class ReactionParticipantCollector:
    @classmethod
    def collect(cls, page, blog_id: str, log_no: str, stop_event: Optional[Any] = None):
        if stop_event and stop_event.is_set():
            return ParticipantCollection([], "cancelled", quality_issues=["stop_requested"])
        if not canonical_blog_id(blog_id) or not str(log_no).isdigit():
            return ParticipantCollection([], "failed", quality_issues=["invalid_post_identity"])
        try:
            page.goto(f"https://m.blog.naver.com/SympathyHistoryList.naver?blogId={blog_id}&logNo={log_no}", wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 0.5)
        except Exception:
            return ParticipantCollection([], "failed", quality_issues=["navigation_failed"])
        return collect_pages(page, REACTION_DOM,
            "ul.list_sympathy + .btn_more, .u_likeit_list_btn_more, .user_list + .btn_more", stop_event, target={"blogId": blog_id, "logNo": str(log_no)})
