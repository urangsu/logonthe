"""Bounded public post-list reads with explicit publication precision."""
import datetime as dt
import re
from browser.session import interruptible_wait
from services.audit_models import KST, RecentPostCollection, canonical_blog_id, fingerprint, parse_date
from services.reaction_participant_collector import CANONICAL_ID_JS

POSTS_DOM = "(arg) => {" + CANONICAL_ID_JS + r"""
    const roots = Array.from(document.querySelectorAll('.list_post, .post_list, #postList')).filter(shown);
    const root = roots.find(r => !roots.some(other => other !== r && other.contains(r)));
    if (!root) return {scopeVerified: false, items: []};
    const cards = Array.from(root.querySelectorAll(':scope > li, :scope > .post_item, :scope > .item'));
    const items = [];
    for (const card of cards) {
        if (card.matches('.notice, .pinned') || card.querySelector('.ico_notice, .notice_badge, .pin_badge')) continue;
        const links = Array.from(card.querySelectorAll('a[href]'));
        for (const link of links) {
            let u; try { u = new URL(link.href); } catch (_) { continue; }
            if (!['blog.naver.com', 'm.blog.naver.com'].includes(u.hostname)) continue;
            const parts = u.pathname.split('/').filter(Boolean);
            const owner = parts[0] === 'PostView.naver' ? u.searchParams.get('blogId') : parts[0];
            const log = parts[0] === 'PostView.naver' ? u.searchParams.get('logNo') : parts.length === 2 ? parts[1] : null;
            if (owner !== arg.blogId || !/^\d+$/.test(log || '')) continue;
            const date = card.querySelector('time[datetime], .date, .post_date');
            items.push({log_no: log, url: 'https://m.blog.naver.com/' + owner + '/' + log,
                title: (card.querySelector('.title, .tit, h3, .post_title')?.textContent || link.textContent || '').trim(),
                published_raw: date ? (date.getAttribute('datetime') || date.textContent.trim()) : null});
            break;
        }
    }
    const scope = root.parentElement;
    const more = scope.querySelector('.btn_more, .more_btn');
    const end = scope.querySelector('.list_end, .no_more, .empty_list');
    return {scopeVerified: true, items, hasMore: shown(more) && !more.disabled && more.getAttribute('aria-disabled') !== 'true',
        terminal: shown(end) && /마지막|더 이상|없습니다|없어요/.test(end.textContent)};
}"""


def normalize_publication(raw):
    date = parse_date(raw)
    if date:
        return date.isoformat(), "date"
    if isinstance(raw, str):
        text = raw.strip()
        try:
            stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                precision = "second" if re.search(r"T\d{2}:\d{2}:\d{2}", text) else "minute"
                return stamp.astimezone(KST).isoformat(), precision
        except ValueError:
            pass
        match = re.fullmatch(r"(\d{4})[.]\s*(\d{1,2})[.]\s*(\d{1,2})[.]\s*(\d{1,2}):(\d{2})", text)
        if match:
            try:
                return dt.datetime(*map(int, match.groups()), tzinfo=KST).isoformat(), "minute"
            except ValueError:
                pass
    return None, "unknown"


class MyBlogRecentPostService:
    @classmethod
    def fetch_recent_posts(cls, page, blog_id, max_count=5, stop_event=None):
        if max_count not in {5, 10, 20}:
            return RecentPostCollection([], "failed", quality_issues=["unsupported_post_count"])
        if not canonical_blog_id(blog_id):
            return RecentPostCollection([], "failed", quality_issues=["invalid_blog_id"])
        items = {}; marks = []; issues = []; terminal = False; scoped = False; cancelled = False
        try:
            if stop_event and stop_event.is_set(): return RecentPostCollection([], "cancelled")
            page.goto(f"https://m.blog.naver.com/PostList.naver?blogId={blog_id}", wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 0.5)
            for _ in range(20):
                if stop_event and stop_event.is_set(): cancelled = True; break
                data = page.evaluate(POSTS_DOM, {"blogId": blog_id})
                if not isinstance(data, dict) or data.get("scopeVerified") is not True:
                    issues.append("post_list_scope_unverified"); break
                scoped = True
                raw = data.get("items", [])
                mark = fingerprint([p.get("log_no") for p in raw])
                if mark in marks: issues.append("duplicate_page"); break
                marks.append(mark); before = len(items)
                for item in raw:
                    log = str(item.get("log_no", ""))
                    if not log.isdigit() or log in items: continue
                    published, precision = normalize_publication(item.get("published_raw"))
                    items[log] = {"log_no": log, "url": f"https://m.blog.naver.com/{blog_id}/{log}", "title": item.get("title", ""),
                                  "published_at": published, "published_at_precision": precision}
                    if len(items) >= max_count: break
                terminal = data.get("terminal") is True
                if len(items) >= max_count or terminal: break
                if len(items) == before: issues.append("no_progress"); break
                if data.get("hasMore") is not True: issues.append("terminal_evidence_missing"); break
                more = page.locator('.list_post + .btn_more, .post_list + .btn_more, #postList + .btn_more').first
                if more.count() != 1 or not more.is_visible(): issues.append("pagination_control_missing"); break
                more.click(timeout=1500); interruptible_wait(stop_event, 0.5)
            else: issues.append("page_limit_reached")
        except Exception: issues.append("post_list_collection_error")
        if stop_event and stop_event.is_set(): cancelled = True
        state = "cancelled" if cancelled else "failed" if not scoped else "partial" if issues or (len(items) < max_count and not terminal) else "complete"
        return RecentPostCollection(items.values(), state, quality_issues=issues)
