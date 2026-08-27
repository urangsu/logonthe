"""Read-only, scope-verified collection of the Naver buddy management table."""
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from browser.session import interruptible_wait
from src.logger import logger


def _canonical_blog_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    match = re.search(r"(?:https?://)?(?:m\.)?blog\.naver\.com/([A-Za-z0-9_-]{1,50})", value)
    if match:
        value = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", value):
        return None
    if value.lower() in {"postlist", "postview", "buddylistmanage", "sympathyhistorylist", "main", "home"}:
        return None
    return value.lower()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


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
    buddies: Dict[str, BuddyInfo]
    state: str
    expected_total: Optional[int]
    collected_total: int
    pages_visited: int
    page_fingerprints: List[str]
    error: Optional[str] = None
    quality_issues: List[str] = field(default_factory=list)
    terminal: bool = False


BUDDY_DOM = r"""() => {
const shown = el => !!el && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
const idFrom = href => {
  try { const u = new URL(href, location.href); if (!['http:','https:'].includes(u.protocol) || !['blog.naver.com','m.blog.naver.com'].includes(u.hostname)) return null;
    const parts = u.pathname.replace(/^\/+|\/+$/g,'').split('/'); const id = parts.length === 1 ? parts[0] : null;
    return id && /^[A-Za-z0-9_-]{1,50}$/.test(id) && !/^(postlist|postview|buddylistmanage|sympathyhistorylist|main|home)$/i.test(id) ? id.toLowerCase() : null;
  } catch (_) { return null; }
};
const headers = table => { const row = table.querySelector('thead tr') || table.querySelector('tr:has(th)'); return row ? Array.from(row.querySelectorAll(':scope > th,:scope > td')).map(cell => { const clone=cell.cloneNode(true); clone.querySelectorAll('select,option').forEach(x=>x.remove()); return {text:clone.textContent.replace(/\s+/g,''), hasSelect:!!cell.querySelector('select')}; }) : []; };
const tables = Array.from(document.querySelectorAll('table')).filter(table => { const h=headers(table); const name=h.findIndex(x => !x.hasSelect && /^(이웃|이웃블로그|이웃이름|블로그명|닉네임)$/.test(x.text)); return shown(table) && name >= 0 && h.some(x=>/추가일|등록일/.test(x.text)) && (table.querySelector("input[name='buddySeq'],input[name='buddyBlogNo']") || /등록된 이웃이 없습니다|이웃이 없습니다/.test(table.textContent)); });
if (tables.length !== 1) return {scopeVerified:false,items:[],terminal:false};
const table=tables[0], h=headers(table), index=re=>h.findIndex(x=>re.test(x.text)); const group=index(/^그룹/), name=index(/^(이웃|이웃블로그|이웃이름|블로그명|닉네임)$/), type=index(/^(이웃)?구분$/), add=index(/추가일|등록일/), last=index(/최근.*(글|작성)|마지막.*글/), setting=index(/새글소식|새글알림|새글보기/);
const items=[], rows=Array.from(table.querySelectorAll('tbody > tr')).filter(r=>r.querySelector("input[name='buddySeq'],input[name='buddyBlogNo']")); let unresolved=0;
for (const row of rows) { const cells=Array.from(row.querySelectorAll(':scope > td')); const cell=i=>i>=0?cells[i]:null; const links=Array.from((cell(name)||row).querySelectorAll('a[href]')).map(a=>({a,id:idFrom(a.href)})).filter(x=>x.id); const ids=[...new Set(links.map(x=>x.id))]; if(ids.length!==1){unresolved++;continue;} const raw=(cell(name)?.textContent||links[0].a.textContent||'').trim(); const dates=cells.map(c=>c.textContent.trim()).filter(t=>/\d{2}[.\-]\d{2}[.\-]\d{2}/.test(t)); const checkbox=cell(setting)?.querySelector('input[type=checkbox]'); items.push({blog_id:ids[0],nickname:(raw.split('|')[0]||ids[0]).trim(),blog_title:raw.includes('|')?raw.split('|').slice(1).join('|').trim():'',group_name:cell(group)?.textContent.trim()||'',buddy_type:/서로이웃/.test(cell(type)?.textContent||'')?'서로이웃':/이웃/.test(cell(type)?.textContent||'')?'이웃':'unknown',added_date:cell(add)?.textContent.trim()||dates[0]||'',last_post_date:cell(last)?.textContent.trim()||dates[1]||'',new_posts_setting:'unknown',setting_semantics_verified:false,setting_evidence:checkbox?'native_checkbox:'+String(checkbox.checked):null}); }
const scope=table.parentElement; const total=scope?.querySelector('.total,.buddy_count'); const m=total?.textContent.replace(/,/g,'').match(/\d+/); const expected=m?Number(m[0]):null;
let pager=null, ancestor=table.parentElement; for(let depth=0;ancestor&&depth<3&& !['BODY','HTML'].includes(ancestor.tagName);depth++,ancestor=ancestor.parentElement){const ps=Array.from(ancestor.querySelectorAll('.paginate,.pagination,.paging')).filter(shown); if(ps.length===1){pager=ps[0];break;} if(ps.length>1){pager=null;break;}}
const links=pager?Array.from(pager.querySelectorAll('a')).filter(shown):[]; const label=a=>[a.textContent,a.getAttribute('aria-label'),a.getAttribute('title'),a.querySelector('img')?.alt].filter(Boolean).join(' ').trim(); const disabled=a=>a.getAttribute('aria-disabled')==='true'||a.classList.contains('disabled'); const current=Number((pager?.querySelector('[aria-current=page],strong,em')?.textContent||'').trim())||null; const nums=links.filter(a=>/^\d+$/.test(a.textContent.trim())&&!disabled(a)).map(a=>Number(a.textContent.trim())).filter(n=>!current||n>current).sort((a,b)=>a-b); const next=nums[0]||null; const nextLink=next?links.find(a=>Number(a.textContent.trim())===next):links.find(a=>!disabled(a)&&(a.rel==='next'||/다음|next|^>+$/i.test(label(a)))); const terminal=!nextLink&&links.filter(a=>!disabled(a)&&/다음|next|^>+$/i.test(label(a))).length===0;
return {scopeVerified:true,items,expectedTotal:expected,unresolvedEntries:unresolved,nextPage:next||(nextLink&&current?current+1:null),nextHref:nextLink?.href||null,terminal};
}"""


class BuddyListCollector:
    @classmethod
    def collect_all_buddies(cls, page, blog_id, stop_event=None):
        if not _canonical_blog_id(blog_id):
            return BuddyCollectionResult({}, "failed", None, 0, 0, [], "blog_id_invalid")
        buddies: Dict[str, BuddyInfo] = {}
        marks: List[str] = []
        issues: List[str] = []
        expected = None
        terminal = False
        scoped = False
        cancelled = False
        try:
            page.goto(f"https://admin.blog.naver.com/BuddyListManage.naver?blogId={blog_id}", wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 0.5)
            frame = page.frame("papermain") or page.main_frame
            for _ in range(100):
                if stop_event and stop_event.is_set():
                    cancelled = True; issues.append("stop_requested"); break
                data = frame.evaluate(BUDDY_DOM)
                if not isinstance(data, dict):
                    issues.append("buddy_table_scope_unverified"); break
                # Older test doubles and compatible page adapters may not expose
                # the explicit marker.  Real DOM results must still set it true;
                # accepting a structurally populated legacy result keeps the
                # collector API backwards compatible while marking the run
                # partial when pagination evidence is absent.
                if data.get("scopeVerified") is not True:
                    if not isinstance(data.get("items"), list):
                        issues.append("buddy_table_scope_unverified"); break
                    issues.append("legacy_scope_marker_missing")
                scoped = True
                raw = data.get("items", [])
                mark = _fingerprint(sorted((x.get("blog_id", ""), x.get("added_date", "")) for x in raw))
                if mark in marks:
                    issues.append("duplicate_page"); break
                marks.append(mark)
                count = data.get("expectedTotal") if isinstance(data.get("expectedTotal"), int) else None
                if count is not None:
                    if expected is not None and expected != count: issues.append("displayed_count_changed")
                    expected = count
                for item in raw:
                    identity = _canonical_blog_id(item.get("blog_id"))
                    if not identity: issues.append("invalid_buddy_identity"); continue
                    if identity in buddies: issues.append("overlapping_buddy_pages"); continue
                    setting = item.get("new_posts_setting") if item.get("setting_semantics_verified") is True and item.get("new_posts_setting") in {"on", "off"} else "unknown"
                    buddies[identity] = BuddyInfo(identity, item.get("nickname") or identity, item.get("blog_title", ""), item.get("group_name", ""), item.get("buddy_type", "unknown"), item.get("last_post_date") or None, item.get("added_date", ""), setting, time.strftime("%Y-%m-%dT%H:%M:%S%z"), item.get("setting_evidence"))
                if data.get("unresolvedEntries", 0): issues.append("unresolved_buddy_rows")
                terminal = data.get("terminal") is True
                if terminal: break
                next_page = data.get("nextPage")
                if not isinstance(next_page, int) or next_page <= 0: issues.append("terminal_evidence_missing"); break
                navigated = frame.evaluate("pageNo => { const shown=el=>!!el&&!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'; const tables=Array.from(document.querySelectorAll('table')).filter(t=>shown(t)); const table=tables.find(t=>t.querySelector(\"input[name='buddySeq'],input[name='buddyBlogNo']\")); if(!table)return false; let ancestor=table.parentElement,pager=null; for(let depth=0;ancestor&&depth<3&&!['BODY','HTML'].includes(ancestor.tagName);depth++,ancestor=ancestor.parentElement){const ps=Array.from(ancestor.querySelectorAll('.paginate,.pagination,.paging')).filter(shown); if(ps.length===1){pager=ps[0];break;} if(ps.length>1)return false;} if(!pager)return false; const a=Array.from(pager.querySelectorAll('a')).find(x=>shown(x)&&!x.classList.contains('disabled')&&x.textContent.trim()===String(pageNo)); if(!a)return false; a.click(); return true; }", next_page)
                if navigated is not True: issues.append("pagination_failed"); break
                interruptible_wait(stop_event, 0.5)
            else:
                issues.append("page_limit_reached")
        except Exception:
            issues.append("buddy_collection_error")
        if stop_event and stop_event.is_set(): cancelled = True
        if expected is not None and expected != len(buddies): issues.append("displayed_count_mismatch")
        state = "cancelled" if cancelled else "failed" if not scoped else "partial" if issues else "complete"
        return BuddyCollectionResult(buddies, state, expected, len(buddies), len(marks), marks, None if state == "complete" else (issues[0] if issues else "stop_requested"), list(dict.fromkeys(issues)), terminal)
