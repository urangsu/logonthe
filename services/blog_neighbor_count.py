import random
import re
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from playwright.sync_api import Page

from naver.count_parser import parse_compact_count
from services.pacing import interruptible_wait
from src.logger import logger


@dataclass(frozen=True)
class NeighborCountResult:
    value: Optional[int]
    raw_text: Optional[str]
    source: str
    confidence: str
    error: Optional[str] = None


# 세분화된 오류 코드 (UNKNOWN 하나로 뭉뚱그리지 않는다)
ERR_EMPTY_BLOG_ID = "empty_blog_id"
ERR_NO_PROFILE_PAGE = "no_profile_page"
ERR_STOPPED = "stopped"
ERR_NAVIGATION_FAILED = "navigation_failed"
ERR_LOGIN_REDIRECT = "login_redirect"
ERR_TIMEOUT = "timeout"
ERR_PROFILE_BLOCK_NOT_FOUND = "profile_block_not_found"
ERR_LABEL_NOT_FOUND = "neighbor_label_not_found"
ERR_PARSE_FAILED = "neighbor_number_parse_failed"
ERR_NOT_PUBLIC = "not_public"
ERR_CONFLICT = "conflicting_neighbor_counts"

# 일시적 오류만 1회 재시도 대상 (구조적 오류는 재시도해도 동일)
_TRANSIENT_ERRORS = frozenset({ERR_NAVIGATION_FAILED, ERR_TIMEOUT})

# 형식 A: '이웃 613명' (ProfileBlock 계열)  /  형식 B: '1,186명의 이웃' (블로그 홈 buddy 영역)
# '서로이웃 N명'은 이웃수가 아니므로 negative lookbehind 로 제외한다.
# 반드시 '이웃' label + 숫자 + '명'이 같은 semantic group 안에 있어야 한다.
_NUM = r"([0-9](?:[0-9,]|\s+(?=[0-9]))*(?:\.[0-9]+)?\s*(?:만|천|[kKmM])?)"
_NEIGHBOR_RES = (
    re.compile(r"(?<!서로)이웃\s*" + _NUM + r"\s*명"),
    re.compile(_NUM + r"\s*명\s*의\s*(?<!서로)이웃(?!\s*(?:추가|목록))"),
)

# 렌더 완료 대기: 단순 '이웃' 포함이 아니라 '이웃 + 숫자'(양방향)가 나타날 때까지 (SPA hydration 대응)
_READY_JS = r"""
() => {
    const t = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ');
    return /[0-9]\s*명\s*의\s*이웃/.test(t) || /(^|[^서로])이웃\s*[0-9]/.test(t) || /이웃[^\n]{0,20}비공개/.test(t);
}
"""

_EXTRACT_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    const push = (el, kind) => {
        if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') return;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text || text.length > 40 || !text.includes('이웃')) return;
        const key = kind + '|' + text;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ text: text, cls: String(el.className || ''), kind: kind });
    };
    // 1순위: 알려진 이웃수 영역 (hash 비의존, 부분 class 매칭, .first 금지)
    document.querySelectorAll("span[class*='ProfileBlock__text'], span[class*='buddy__']").forEach(el => push(el, 'profile_block'));
    // 2순위: 일반 작은 텍스트 노드 (class 변경 대비)
    document.querySelectorAll('span, div, p, em, strong, a, li').forEach(el => push(el, 'generic'));
    const bodyText = (document.body && document.body.innerText) || '';
    const profileText = Array.from(document.querySelectorAll("[class*='ProfileBlock'], [class*='profile'], [class*='buddy__']"))
        .filter(el => el.getClientRects().length).map(el => el.innerText || '').join('\n');
    return {
        candidates: out,
        profileBlockCount: document.querySelectorAll("[class*='ProfileBlock']").length,
        title: document.title || '',
        notPublic: /이웃[^\n]{0,20}비공개|비공개[^\n]{0,20}이웃/.test(bodyText),
        profileNotPublic: /이웃[^\n]{0,20}비공개|비공개[^\n]{0,20}이웃/.test(profileText),
        ownerUrl: document.querySelector('link[rel="canonical"]')?.href || document.querySelector('meta[property="og:url"]')?.content || '',
    };
}
"""


def extract_neighbor_count(
    candidates: Iterable[Dict],
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    후보 텍스트에서 '이웃 N명' 의미 그룹만 이웃수로 인정한다. (순수 함수 — fixture 테스트 용이)
    returns (value, raw_text, error)

    - profile_block 후보를 generic 후보보다 우선한다.
    - '이웃' label 이 없는 숫자(방문자/공감/댓글/게시글 등)는 절대 사용하지 않는다.
    - 같은 우선순위의 수치가 충돌하면 UNKNOWN으로 판정한다.
    """
    cands: List[Dict] = list(candidates or [])
    if not cands:
        return None, None, ERR_LABEL_NOT_FOUND
    ordered = sorted(cands, key=lambda c: 0 if c.get("kind") == "profile_block" else 1)
    for kind in ("profile_block", "generic"):
        values = {}
        for cand in ordered:
            if cand.get("visible") is False or (cand.get("kind") == "profile_block") != (kind == "profile_block"):
                continue
            text = re.sub(r"\s+", " ", str(cand.get("text", ""))).strip()
            for rx in _NEIGHBOR_RES:
                for m in rx.finditer(text):
                    parsed = parse_compact_count(re.sub(r"\s+", "", m.group(1)))
                    if parsed is not None:
                        values[parsed] = text
        if len(values) > 1:
            return None, None, ERR_CONFLICT
        if values:
            value, text = next(iter(values.items()))
            return value, text, None
    return None, None, ERR_PARSE_FAILED


class BlogNeighborCountService:
    """
    블로그 프로필(모바일 블로그 홈)에서 '이웃 N명'을 조회하고 세션 캐싱한다.
    추천피드 카드 DOM에는 의존하지 않는다. (BlogPopularityService 와 동일한 stats_page 패턴)

    - 세션 메모리 캐시만 사용 (정상 수치와 명시적 비공개만 캐시).
    - 일시적 오류(navigation/timeout)는 1회 재시도, 구조적 오류는 즉시 확정.
    - 단일 controller thread 에서 순차 사용을 전제하되, 캐시는 lock 으로 보호한다.
    """

    SOURCE = "mobile_blog_home"
    _cache: Dict[str, NeighborCountResult] = {}
    _lock = threading.Lock()
    _diag_logged: bool = False

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()
            cls._diag_logged = False

    @classmethod
    def peek_cache(cls, blog_id: str) -> Optional[NeighborCountResult]:
        with cls._lock:
            return cls._cache.get(blog_id)

    @classmethod
    def _fail(cls, error: str) -> NeighborCountResult:
        return NeighborCountResult(None, None, cls.SOURCE, "unknown", error)

    @classmethod
    def get_neighbor_count(
        cls,
        profile_page: Optional[Page],
        blog_id: str,
        stop_event: Optional[threading.Event] = None,
    ) -> NeighborCountResult:
        blog_id = (blog_id or "").strip()
        if not blog_id:
            return cls._fail(ERR_EMPTY_BLOG_ID)

        cached = cls.peek_cache(blog_id)
        if cached is not None:
            logger.log(
                f"[NEIGHBOR_PROFILE] blog={blog_id} "
                f"count={cached.value if cached.value is not None else 'None'} cache=true"
            )
            return cached

        if profile_page is None:
            return cls._fail(ERR_NO_PROFILE_PAGE)
        if stop_event and stop_event.is_set():
            return cls._fail(ERR_STOPPED)

        res = cls._lookup_once(profile_page, blog_id, stop_event)
        if res.error in _TRANSIENT_ERRORS and not (stop_event and stop_event.is_set()):
            logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} transient error={res.error} -> retry 1회", "WARNING")
            res = cls._lookup_once(profile_page, blog_id, stop_event)

        # 중단으로 인한 미완료는 캐시하지 않는다 (재실행/재개 시 재시도 가능)
        if res.error == ERR_STOPPED:
            return res

        if res.value is not None or res.error == ERR_NOT_PUBLIC:
            with cls._lock:
                cls._cache[blog_id] = res
        return res

    @classmethod
    def _lookup_once(
        cls,
        page: Page,
        blog_id: str,
        stop_event: Optional[threading.Event],
    ) -> NeighborCountResult:
        # 블로그별 별도 navigation 이므로 짧게 pacing (0.3~0.8s, interruptible)
        interruptible_wait(stop_event, random.uniform(0.3, 0.8))
        if stop_event and stop_event.is_set():
            return cls._fail(ERR_STOPPED)

        url = f"https://m.blog.naver.com/{blog_id}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
        except Exception as e:
            logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} count=None error={ERR_NAVIGATION_FAILED} ({e}) cache=false", "WARNING")
            return cls._fail(ERR_NAVIGATION_FAILED)

        try:
            cur_url = page.url or ""
        except Exception:
            cur_url = ""
        if "nid.naver.com" in cur_url or "/login" in cur_url:
            logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} count=None error={ERR_LOGIN_REDIRECT} cache=false", "WARNING")
            return cls._fail(ERR_LOGIN_REDIRECT)

        # '이웃 + 숫자' 가 렌더될 때까지 대기. 실패해도 추출 단계에서 최종 판정한다.
        try:
            page.wait_for_function(_READY_JS, timeout=4000)
        except Exception:
            pass

        try:
            data = page.evaluate(_EXTRACT_JS) or {}
        except Exception as e:
            logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} count=None error={ERR_TIMEOUT} ({e}) cache=false", "WARNING")
            return cls._fail(ERR_TIMEOUT)

        value, raw, err = extract_neighbor_count(data.get("candidates", []))
        if value is not None:
            logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} count={value} source={cls.SOURCE} cache=false")
            return NeighborCountResult(value, raw, cls.SOURCE, "high")

        if data.get("notPublic"):
            err = ERR_NOT_PUBLIC
        elif err == ERR_LABEL_NOT_FOUND and not data.get("profileBlockCount"):
            err = ERR_PROFILE_BLOCK_NOT_FOUND
        logger.log(f"[NEIGHBOR_PROFILE] blog={blog_id} count=None error={err} cache=false", "WARNING")

        with cls._lock:
            first_diag = not cls._diag_logged
            cls._diag_logged = True
        if first_diag:
            cls._log_diagnostics(page, blog_id, url, data)
        return cls._fail(err)

    @classmethod
    def _log_diagnostics(cls, page: Page, blog_id: str, url: str, data: Dict) -> None:
        """최초 1회만 제한된 진단 정보를 남긴다 (전체 HTML 출력 금지, outerHTML 2000자 제한)."""
        texts = [str(c.get("text", ""))[:40] for c in (data.get("candidates") or [])[:8]]
        logger.log(
            f"[NEIGHBOR_PROFILE_DIAG] blog={blog_id} url={url} title={data.get('title', '')!r} "
            f"profileBlockCount={data.get('profileBlockCount')} neighborTextCandidates={texts}",
            "WARNING",
        )
        try:
            snippet = page.evaluate(
                """() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const hit = all.find(el => el.children.length <= 6 && (el.innerText || '').includes('이웃')
                        && (el.innerText || '').length < 200);
                    const el = hit || document.querySelector("[class*='ProfileBlock']") || document.body;
                    return el ? el.outerHTML.slice(0, 2000) : '';
                }"""
            )
            # logger 는 DEBUG 레벨을 파일에 쓰지 않으므로 INFO 로 1회만 기록
            logger.log(f"[NEIGHBOR_PROFILE_DIAG] outerHTML(<=2000)={snippet}")
        except Exception:
            pass
