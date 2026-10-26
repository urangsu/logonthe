"""Read pending incoming requests without accepting, rejecting or changing settings."""
import re

from browser.session import interruptible_wait
from services.buddy_list_collector import BuddyCollectionResult, BuddyInfo, _canonical_blog_id, _fingerprint


REQUEST_DOM = r"""owner => {
  const shown = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const route = new URL(document.URL);
  if (route.hostname!=='admin.blog.naver.com' || route.pathname!=='/BuddyInviteReceivedManage.naver' ||
      route.searchParams.get('blogId')?.toLowerCase()!==owner.toLowerCase()) return {scopeVerified:false,items:[]};
  const tables = Array.from(document.querySelectorAll('table')).filter(t => shown(t) &&
    /받은신청/.test(t.querySelector('caption')?.textContent.replace(/\s/g,'') || '') &&
    /신청한\s*사람/.test(t.textContent) && /신청일/.test(t.textContent));
  if (tables.length !== 1) return {scopeVerified:false,items:[]};
  const table=tables[0], items=[];
  let unresolved=0;
  for (const row of table.querySelectorAll('tbody tr')) {
    const input=row.querySelector('input[name="targetBlogId"]');
    if (!input) continue;
    const id=(input.value || '').trim().toLowerCase();
    const link=row.querySelector('a.blog_link');
    let linked=null;
    try {
      const url=new URL(link?.getAttribute('href'), location.href);
      if (['http:','https:'].includes(url.protocol) && ['blog.naver.com','m.blog.naver.com'].includes(url.hostname)) {
        linked=url.pathname==='/GoBlog.naver' ? url.searchParams.get('userId') : url.pathname.split('/').filter(Boolean)[0];
      }
    } catch (_) {}
    if (!/^[a-z0-9_-]{1,50}$/.test(id) || !linked || linked.toLowerCase()!==id) {unresolved++;continue;}
    items.push({blog_id:id,nickname:(link.querySelector('.nickname')?.textContent || id).trim(),
      requested_date:(row.querySelector('.date')?.textContent || '').trim()});
  }
  const pagers=Array.from(table.parentElement.querySelectorAll('.paginate,.pagination,.paging')).filter(shown);
  const pager=pagers.length===1 ? pagers[0] : null;
  const current=Number((pager?.querySelector('strong,em,[aria-current="page"]')?.textContent || '').trim()) || null;
  const links=pager ? Array.from(pager.querySelectorAll('a')).filter(a=>shown(a) && !a.classList.contains('disabled') && a.getAttribute('aria-disabled')!=='true') : [];
  const forward=links.filter(a=>/^\d+$/.test(a.textContent.trim()) && current && Number(a.textContent.trim())>current)
    .sort((a,b)=>Number(a.textContent)-Number(b.textContent));
  const next=forward[0] || links.find(a=>/다음|next|^>+$/i.test([a.textContent,a.title,a.querySelector('img')?.alt].join(' ')));
  const empty=/받은\s*신청이?\s*없|신청\s*내역이?\s*없|신청한\s*이웃이?\s*없/.test(table.textContent);
  return {scopeVerified:true,items,unresolvedEntries:unresolved,nextLabel:next?.textContent.trim() || '',
    nextTitle:next?.title || '', nextIndex:next ? Array.from(pager.querySelectorAll('a')).indexOf(next) : null,
    terminal:!next && (!!pager && !!current || items.length===0 && empty)};
}"""


class BuddyRequestCollector:
    @classmethod
    def collect_received(cls, page, blog_id, stop_event=None):
        owner = _canonical_blog_id(blog_id)
        if not owner:
            return BuddyCollectionResult({}, "failed", None, 0, 0, [], "blog_id_invalid")
        members, fingerprints, issues = {}, [], []
        terminal = False
        cancelled = bool(stop_event and stop_event.is_set())
        if cancelled:
            return BuddyCollectionResult({}, "cancelled", None, 0, 0, [], "stop_requested")
        scoped = False
        try:
            page.goto(f"https://admin.blog.naver.com/BuddyInviteReceivedManage.naver?blogId={owner}",
                      wait_until="domcontentloaded", timeout=25000)
            frame = page.frame("papermain") or page.main_frame
            for _ in range(100):
                interruptible_wait(stop_event, 0.5)
                if stop_event and stop_event.is_set():
                    cancelled = True
                    break
                data = frame.evaluate(REQUEST_DOM, owner)
                if not isinstance(data, dict) or data.get("scopeVerified") is not True:
                    issues.append("request_table_scope_unverified")
                    break
                scoped = True
                raw = data.get("items", [])
                fingerprint = _fingerprint(raw)
                if fingerprint in fingerprints:
                    issues.append("duplicate_request_page")
                    break
                fingerprints.append(fingerprint)
                for item in raw:
                    identity = _canonical_blog_id(item.get("blog_id"))
                    if not identity:
                        issues.append("invalid_request_identity")
                        continue
                    if identity in members:
                        issues.append("overlapping_request_pages")
                        continue
                    members[identity] = BuddyInfo(
                        identity, item.get("nickname") or identity, "", "", "신청대기", None, "",
                        requested_date=item.get("requested_date") or None,
                    )
                if data.get("unresolvedEntries"):
                    issues.append("unresolved_request_rows")
                terminal = data.get("terminal") is True
                if terminal:
                    break
                label = data.get("nextLabel", "")
                title = data.get("nextTitle", "")
                next_index = data.get("nextIndex")
                if not label and not title and not isinstance(next_index, int):
                    issues.append("request_terminal_evidence_missing")
                    break
                if stop_event and stop_event.is_set():
                    cancelled = True
                    break
                pager = frame.locator(".paginate, .pagination, .paging")
                if isinstance(next_index, int) and 0 <= next_index < pager.locator("a").count():
                    link = pager.locator("a").nth(next_index)
                elif label:
                    link = pager.locator("a").filter(has_text=re.compile(r"^\s*" + re.escape(label) + r"\s*$"))
                else:
                    matches = [a for a in pager.locator("a[title]").all() if a.get_attribute("title") == title]
                    link = matches[0] if len(matches) == 1 else None
                if link is None or link.count() != 1:
                    issues.append("request_pagination_ambiguous")
                    break
                link.click(timeout=3000)
            else:
                issues.append("request_page_limit_reached")
        except Exception:
            issues.append("request_collection_error")
        cancelled = cancelled or bool(stop_event and stop_event.is_set())
        if cancelled:
            issues.append("stop_requested")
        complete = scoped and terminal and not issues
        state = "cancelled" if cancelled else "complete" if complete else "partial" if members else "failed"
        return BuddyCollectionResult(
            members, state, len(members) if complete else None, len(members), len(fingerprints), fingerprints,
            None if complete else (issues[0] if issues else "request_collection_incomplete"),
            list(dict.fromkeys(issues)), terminal, collection_complete=complete,
        )
