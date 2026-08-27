"""Comment authors are read from author headers, never comment-body blog links."""
from browser.session import interruptible_wait
from services.audit_models import ParticipantCollection, canonical_blog_id
from services.reaction_participant_collector import CANONICAL_ID_JS, collect_pages

COMMENT_DOM = "(target) => {" + CANONICAL_ID_JS + r"""
    const route = new URL(location.href), parts = route.pathname.split('/').filter(Boolean);
    const owner = parts[0] === 'PostView.naver' ? route.searchParams.get('blogId') : parts[0];
    const logNo = parts[0] === 'PostView.naver' ? route.searchParams.get('logNo') : parts.length === 2 ? parts[1] : null;
    if (!target || owner !== target.blogId || logNo !== String(target.logNo) || !['blog.naver.com', 'm.blog.naver.com'].includes(route.hostname)) return {scopeVerified: false, items: []};
    const roots = Array.from(document.querySelectorAll('#cbox_module, .u_cbox')).filter(shown);
    const root = roots.find(r => !roots.some(other => other !== r && other.contains(r)));
    if (!root) return {scopeVerified: false, items: []};
    const rows = Array.from(root.querySelectorAll('li.u_cbox_comment'));
    const users = {}; const entries = []; let unresolved = 0;
    for (const row of rows) {
        const header = row.querySelector('.u_cbox_info');
        const links = header ? Array.from(header.querySelectorAll('a.u_cbox_name, .u_cbox_nick a, a.u_cbox_thumb')) : [];
        const ids = [...new Set(links.map(a => blogIdentity(a.href)).filter(Boolean))];
        let info = {}; try { info = JSON.parse(row.getAttribute('data-info') || '{}'); } catch (_) {}
        const mine = row.classList.contains('u_cbox_type_mine') || info.mine === true;
        const id = ids.length === 1 ? ids[0] : null;
        const entryId = row.getAttribute('data-comment-no') || info.commentNo || row.id;
        if (!id && !mine) unresolved++;
        if (entryId) entries.push({entry_id: String(entryId), blog_id: id, mine});
        if (mine || !id) continue;
        if (!users[id]) users[id] = {blog_id: id, nickname: (header.querySelector('.u_cbox_nick')?.textContent || id).trim(), comment_entry_count: 0};
        users[id].comment_entry_count++;
    }
    const more = root.querySelector('a.u_cbox_btn_more, button.u_cbox_btn_more');
    const disabled = shown(more) && (more.disabled || more.getAttribute('aria-disabled') === 'true');
    const end = root.querySelector('.u_cbox_no_comment, .u_cbox_end, .u_cbox_list_end');
    const terminal = disabled || (shown(end) && /댓글이 없습니다|등록된 댓글이 없습니다|마지막|더 이상/.test(end.textContent));
    return {scopeVerified: true, items: Object.values(users), entries, totalLoadedEntries: rows.length,
            displayedCount: countValue(root.querySelector('.u_cbox_count')), countUnit: 'entries',
            unresolvedEntries: unresolved, terminal, hasMore: shown(more) && !disabled};
}"""


class CommentParticipantCollector:
    @classmethod
    def collect(cls, page, blog_id, log_no, stop_event=None):
        if stop_event and stop_event.is_set():
            return ParticipantCollection([], "cancelled", count_unit="entries", quality_issues=["stop_requested"])
        if not canonical_blog_id(blog_id) or not str(log_no).isdigit():
            return ParticipantCollection([], "failed", count_unit="entries", quality_issues=["invalid_post_identity"])
        try:
            page.goto(f"https://m.blog.naver.com/{blog_id}/{log_no}", wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 0.5)
            button = page.locator("button[data-click-area$='.re'], button.Interact__comment_btn--Wbuoq").first
            if button.count() and button.is_visible():
                button.click(timeout=1500)
                interruptible_wait(stop_event, 0.5)
        except Exception:
            return ParticipantCollection([], "failed", count_unit="entries", quality_issues=["comment_layer_unavailable"])
        return collect_pages(page, COMMENT_DOM,
            "#cbox_module a.u_cbox_btn_more, #cbox_module button.u_cbox_btn_more, .u_cbox a.u_cbox_btn_more", stop_event, kind="comment", target={"blogId": blog_id, "logNo": str(log_no)})
