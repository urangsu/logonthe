"""Read the buddy-management table without changing any relationship or setting."""
from dataclasses import dataclass, field
from typing import Optional
from browser.session import interruptible_wait
from services.audit_models import canonical_blog_id, fingerprint, nonnegative_int, now_kst
from services.reaction_participant_collector import CANONICAL_ID_JS


@dataclass
class BuddyInfo:
    blog_id: str
    nickname: str
    blog_title: str
    group_name: str
    buddy_type: str
    last_post_date: Optional[str]
    added_date: str
    new_posts_setting: str = "unknown"
    setting_observed_at: Optional[str] = None
    setting_evidence: Optional[str] = None


@dataclass
class BuddyCollectionResult:
    buddies: dict
    state: str
    expected_total: Optional[int]
    collected_total: int
    pages_visited: int
    page_fingerprints: list
    error: Optional[str] = None
    quality_issues: list = field(default_factory=list)
    source_kind: str = "live"
    capability_verified: bool = False
    terminal: bool = False


BUDDY_SCOPE_JS = r"""
const rowMarker = "input[name='buddySeq'], input[name='buddyBlogNo']";
function headersFor(table) {
    const header = table.querySelector('thead tr') || table.querySelector('tr:has(th)');
    if (!header) return [];
    return Array.from(header.querySelectorAll(':scope > th, :scope > td')).map(cell => {
        const clone = cell.cloneNode(true);
        clone.querySelectorAll('select, option').forEach(x => x.remove());
        return {text: clone.textContent.replace(/\s+/g, ''), full: cell.textContent.replace(/\s+/g, ''), hasSelect: !!cell.querySelector('select')};
    });
}
function nameIndex(headings) {
    return headings.findIndex(h => !h.hasSelect && /^(이웃|이웃블로그|이웃이름|블로그명|닉네임)$/.test(h.text));
}
function buddyTables() {
    return Array.from(document.querySelectorAll('table')).filter(table => {
        const headings = headersFor(table);
        return shown(table) && nameIndex(headings) >= 0 && headings.some(h => /추가일|등록일/.test(h.text)) &&
            (table.querySelector(rowMarker) || /등록된 이웃이 없습니다|이웃이 없습니다/.test(table.textContent));
    });
}
function pagerFor(table) {
    // A pager must share a close ancestor with exactly this buddy table. No global fallback.
    let scope = table.parentElement;
    for (let depth = 0; scope && depth < 3 && !['BODY', 'HTML'].includes(scope.tagName); depth++, scope = scope.parentElement) {
        if (buddyTables().filter(t => scope.contains(t)).length !== 1) return null;
        const pagers = Array.from(scope.querySelectorAll('.paginate, .pagination, .paging')).filter(shown);
        if (pagers.length === 1) return pagers[0];
        if (pagers.length > 1) return null;
    }
    return null;
}
function pagerInfo(pager) {
    if (!pager) return {nextPage: null, terminal: false, nextLink: null};
    const currentNodes = Array.from(pager.querySelectorAll('[aria-current=page], strong, em')).filter(el => shown(el) && /^\d+$/.test(el.textContent.trim()));
    const currentValues = [...new Set(currentNodes.map(el => Number(el.textContent.trim())))];
    const current = currentValues.length === 1 && currentValues[0] > 0 ? currentValues[0] : null;
    const links = Array.from(pager.querySelectorAll('a')).filter(shown);
    const label = a => [a.textContent, a.getAttribute('aria-label'), a.getAttribute('title'), a.querySelector('img')?.alt].filter(Boolean).join(' ').trim();
    const disabled = a => a.getAttribute('aria-disabled') === 'true' || a.classList.contains('disabled');
    const numeric = links.filter(a => /^\d+$/.test(a.textContent.trim()) && !disabled(a));
    const nextNumeric = current ? numeric.filter(a => Number(a.textContent.trim()) > current).sort((a,b) => Number(a.textContent.trim()) - Number(b.textContent.trim()))[0] : null;
    const nextControls = links.filter(a => a.rel === 'next' || /다음|next|^>+$/i.test(label(a)));
    const nextControl = nextControls.find(a => !disabled(a));
    const unexplained = links.some(a => !/^\d+$/.test(a.textContent.trim()) && !nextControls.includes(a) && !/이전|처음|prev|first|^<+$/i.test(label(a)));
    const numbers = numeric.map(a => Number(a.textContent.trim()));
    if (current) numbers.push(current);
    // A consecutive range alone is terminal only when it begins at page 1;
    // later windows need an explicit disabled next control.
    const sorted = [...new Set(numbers)].sort((a,b) => a-b);
    const fullRange = sorted.length > 0 && sorted[0] === 1 && sorted.every((n,i) => n === i + 1);
    const terminal = !!current && !nextNumeric && !nextControl && !unexplained &&
        ((fullRange && current === sorted[sorted.length - 1]) || nextControls.some(disabled));
    return {nextPage: nextNumeric ? Number(nextNumeric.textContent.trim()) : nextControl && current ? current + 1 : null,
        terminal, nextLink: nextNumeric || nextControl || null};
}
"""

BUDDY_DOM = "() => {" + CANONICAL_ID_JS + BUDDY_SCOPE_JS + r"""
    const candidates = buddyTables();
    if (candidates.length !== 1) return {scopeVerified: false, items: [], terminal: false};
    const table = candidates[0], headings = headersFor(table);
    const index = re => headings.findIndex(h => re.test(h.text));
    const groupIdx = index(/^그룹/), nameIdx = nameIndex(headings);
    const typeIdx = headings.findIndex(h => /^(이웃)?구분$/.test(h.text) || (h.hasSelect && /^이웃/.test(h.text)));
    const addIdx = index(/추가일|등록일/), lastIdx = index(/최근.*(글|작성)|마지막.*글/), settingIdx = index(/새글소식|새글알림/);
    const items = []; let unresolved = 0;
    for (const row of table.querySelectorAll('tbody > tr')) {
        if (!row.querySelector(rowMarker)) continue;
        const cells = Array.from(row.querySelectorAll(':scope > td'));
        const cell = i => i >= 0 && cells[i] ? cells[i] : null;
        const text = i => cell(i)?.textContent.trim() || '';
        const nameCell = cell(nameIdx);
        // Numeric buddyBlogNo values and unknown javascript links are not blog IDs.
        const links = nameCell ? Array.from(nameCell.querySelectorAll('a[href]')) : [];
        const ids = [...new Set(links.map(a => blogIdentity(a.href)).filter(Boolean))];
        if (ids.length !== 1) { unresolved++; continue; }
        const id = ids[0], link = links.find(a => blogIdentity(a.href) === id);
        const raw = text(nameIdx) || link.textContent.trim();
        const checkbox = cell(settingIdx)?.querySelector('input[type=checkbox]');
        items.push({blog_id: id, nickname: raw.split('|')[0].trim() || id, blog_title: raw.includes('|') ? raw.split('|')[1].trim() : '',
            group_name: text(groupIdx), buddy_type: /서로이웃/.test(text(typeIdx)) ? '서로이웃' : text(typeIdx) === '이웃' ? '이웃' : 'unknown',
            added_date: text(addIdx), last_post_date: text(lastIdx),
            new_posts_setting: 'unknown', setting_semantics_verified: false,
            setting_evidence: checkbox ? 'native_checkbox:' + String(checkbox.checked) : null});
    }
    const scope = table.parentElement;
    const total = scope.querySelector('.total, .buddy_count');
    const expected = total && /이웃/.test(total.textContent) ? countValue(total) : null;
    const pager = pagerInfo(pagerFor(table));
    const registeredRows = table.querySelectorAll(rowMarker).length;
    const empty = !registeredRows && /등록된 이웃이 없습니다|이웃이 없습니다/.test(table.textContent);
    return {scopeVerified: true, items, expectedTotal: expected, unresolvedEntries: unresolved,
        nextPage: pager.nextPage, terminal: empty || pager.terminal};
}"""

BUDDY_NEXT_DOM = "(pageNo) => {" + CANONICAL_ID_JS + BUDDY_SCOPE_JS + r"""
    const tables = buddyTables();
    if (tables.length !== 1) return false;
    const pager = pagerInfo(pagerFor(tables[0]));
    if (!pager.nextLink || pager.nextPage !== pageNo) return false;
    pager.nextLink.click();
    return true;
}"""



class BuddyListCollector:
    @classmethod
    def collect_all_buddies(cls, page, blog_id, stop_event=None):
        buddies = {}; marks = []; issues = []; expected = None; terminal = False; scoped = False; cancelled = False
        if not canonical_blog_id(blog_id):
            return BuddyCollectionResult({}, "failed", None, 0, 0, [], "blog_id_invalid")
        try:
            if stop_event and stop_event.is_set():
                return BuddyCollectionResult({}, "cancelled", None, 0, 0, [], "stop_requested")
            page.goto(f"https://admin.blog.naver.com/BuddyListManage.naver?blogId={blog_id}", wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 0.5)
            frame = page.frame("papermain") or page.main_frame
            for _ in range(100):
                if stop_event and stop_event.is_set():
                    cancelled = True; issues.append("stop_requested"); break
                data = frame.evaluate(BUDDY_DOM)
                if not isinstance(data, dict) or data.get("scopeVerified") is not True:
                    issues.append("buddy_table_scope_unverified"); break
                scoped = True
                raw = data.get("items", [])
                mark = fingerprint(sorted((item.get("blog_id", ""), item.get("added_date", "")) for item in raw))
                if mark in marks:
                    issues.append("duplicate_page"); break
                marks.append(mark)
                count = nonnegative_int(data.get("expectedTotal"))
                if count is not None:
                    if expected is not None and expected != count:
                        issues.append("displayed_count_changed")
                    expected = count
                new_ids = 0
                for item in raw:
                    identity = canonical_blog_id(item.get("blog_id"))
                    if not identity:
                        issues.append("invalid_buddy_identity"); continue
                    if identity in buddies:
                        issues.append("overlapping_buddy_pages"); continue
                    setting = item.get("new_posts_setting")
                    setting = setting if item.get("setting_semantics_verified") is True and setting in {"on", "off"} else "unknown"
                    buddies[identity] = BuddyInfo(identity, item.get("nickname") or identity, item.get("blog_title", ""),
                        item.get("group_name", ""), item.get("buddy_type", "unknown"), item.get("last_post_date") or None,
                        item.get("added_date", ""), setting, now_kst().isoformat(), item.get("setting_evidence"))
                    new_ids += 1
                if data.get("unresolvedEntries", 0): issues.append("unresolved_buddy_rows")
                terminal = data.get("terminal") is True
                if terminal: break
                if not new_ids:
                    issues.append("no_progress"); break
                next_page = nonnegative_int(data.get("nextPage"))
                if next_page is None:
                    issues.append("terminal_evidence_missing"); break
                if frame.evaluate(BUDDY_NEXT_DOM, next_page) is not True:
                    issues.append("pagination_failed"); break
                interruptible_wait(stop_event, 0.5)
            else:
                issues.append("page_limit_reached")
        except Exception:
            issues.append("buddy_collection_error")
        if stop_event and stop_event.is_set(): cancelled = True
        if expected is not None and expected != len(buddies): issues.append("displayed_count_mismatch")
        if not terminal and not issues: issues.append("terminal_evidence_missing")
        state = "cancelled" if cancelled else "failed" if not scoped else "partial" if issues else "complete"
        return BuddyCollectionResult(buddies, state, expected, len(buddies), len(marks), marks,
                                     None if state == "complete" else (issues[0] if issues else "stop_requested"),
                                     list(dict.fromkeys(issues)), terminal=terminal)
