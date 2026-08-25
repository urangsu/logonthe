# NAVER FEED ASSISTANT
# RUNTIME FIX v4 — LIKE VISIBILITY / COMMENT JS / PROFILE LIFECYCLE

기준: GitHub `urangsu/logonthe` / `main`
목표: 2026-08-25 실제 실행 로그에서 확인된 런타임 결함을 한 번에 제거한다.

---

## 0. 이번 로그에서 확인된 실제 결함

1. Runtime 공감수 threshold가 300으로 남아 있음.
2. 실제 `data-type=like` option은 DOM에 있으나 hidden 상태인데 `scroll_into_view_if_needed()`를 호출함.
3. summary click 직후 reaction state를 재확인하지 않아, summary click이 이미 공감을 적용했어도 실패로 처리할 수 있음.
4. 모든 Like click 예외에서 circuit breaker를 열어버림.
5. 브라우저 JS `querySelectorAll()` 안에 Playwright-only `:has-text()` selector를 사용함.
6. 로그인 요구 selector가 hidden/template element까지 오탐할 수 있음.
7. 앱 `.profile_lock`과 Chromium `SingletonLock`을 구분하지 못함.
8. “락 초기화”가 live process 확인 없이 앱 lock만 삭제함.
9. 로그인 브라우저를 사용자가 닫은 뒤 closed context에서 cookie check가 실행될 여지가 있음.
10. 댓글 엔진이 `UNKNOWN_TOPIC / 속초` 같은 품질 회귀를 여전히 보임.

---

# 1. Like Transaction을 2-path 동작으로 변경

현재 코드의 핵심 문제:

```python
if like_opt hidden:
    summary.click()
    wait(0.4)

like_opt = resolve()
like_opt.scroll_into_view_if_needed()
```

`like_opt`가 여전히 hidden이면 scroll timeout.

더 중요한 점:
summary click 자체가 reaction을 적용했을 수도 있는데 이를 확인하지 않음.

---

## 2. 올바른 Like transaction

```text
PRE reaction state
↓
summary button resolve
↓
summary를 viewport에 안전하게 위치
↓
2초 UI settle
↓
actual like option 상태 확인
```

### Path A — actual like option이 이미 visible

```text
actual like option click
↓
postcondition verify
```

### Path B — actual like option이 hidden

```text
summary click
↓
300~500ms UI state settle
↓
reaction state 즉시 재확인
├─ LIKE active
│   → summary click 자체가 실제 공감을 적용한 것
│   → SUCCESS
└─ still NONE
    ↓
    actual like option visible wait (최대 2.5~3초)
    ├─ visible → actual option click → verify
    └─ hidden → 해당 글 실패, circuit breaker 열지 않음
```

---

# 3. 2초 대기 위치

사용자 요청의 2초 대기는 다음 위치에 둔다.

```python
summary.scroll_into_view_if_needed(timeout=2500)
interruptible_wait(stop_event, 2.0)
```

이 대기는 **UI가 viewport 이동 후 안정화되고 reaction module이 준비될 시간을 주기 위한 settle delay**로 사용한다.

랜덤한 인간 흉내/감지 회피 로직으로 구현하지 않는다.

---

# 4. `mouse.wheel()`로 임의 스크롤하지 않기

정확한 action target까지:

```python
summary.scroll_into_view_if_needed()
```

로 이동.

임의 `wheel(200)`은 target이 여전히 화면 밖일 수 있어 안정성이 떨어짐.

필요하면:

```python
page.evaluate(
    "(el) => el.scrollIntoView({block:'center', behavior:'smooth'})",
    summary
)
```

형태도 가능하지만,
Playwright locator의 `scroll_into_view_if_needed()`가 우선.

---

# 5. Like option hidden 상태에서 절대 scroll 금지

```python
if not like_opt.is_visible():
    # scroll_into_view_if_needed 호출 금지
```

visible 확인 이후에만 click.

실제 layer option은 summary 근처이므로 visible 이후 별도 scroll도 대개 불필요.

---

# 6. Summary click 직후 state re-check

필수:

```python
summary.click(timeout=2000)
interruptible_wait(stop_event, 0.4)

after_summary = cls.resolve_reaction_state(page)

if (
    after_summary.reacted
    and after_summary.reaction_type == ReactionType.LIKE
):
    logger.log(
        "✅ [LIKE] 요약 버튼 상호작용 후 공감 활성화 확인."
    )
    return LikeProcessResult(
        state_before=LikeState.NOT_LIKED,
        action_taken=True,
        state_after=LikeState.LIKED
    )
```

이번 로그에서 같은 `hyejin70222` 글이 다음 실행에 이미 공감 상태였던 점을 반드시 회귀 테스트로 넣는다.

---

# 7. Actual option visible wait

```python
like_opt = MobileDOMResolver.get_reaction_like_option(page)

try:
    like_opt.wait_for(state="visible", timeout=3000)
except:
    return LikeProcessResult(
        state_before=LikeState.NOT_LIKED,
        action_taken=False,
        state_after=LikeState.UNKNOWN,
        error="reaction_option_not_visible"
    )
```

이 경우 circuit breaker를 열지 않는다.

---

# 8. Circuit breaker 조건 축소

현재:

```python
except Exception:
    LikeCircuitBreaker.trip(...)
```

는 너무 넓다.

다음 경우에만 trip:

```text
실제 reaction을 변경하는 click이 수행됨
AND
그 이후 최종 상태를 확인할 수 없음
```

다음은 circuit open 금지:

```text
summary scroll 실패
summary button 없음
option 없음
option hidden timeout
target detached before actual click
```

이런 경우 해당 post like만 skip.

---

# 9. Like transaction 내부 phase flag

```python
actual_reaction_click_performed = False
summary_click_performed = False
```

사용.

`except`에서:

```python
if actual_reaction_click_performed:
    circuit.trip(...)
```

summary click만 했을 경우는 재상태검사를 수행한 뒤
상태 UNKNOWN이면 post failure만 반환.

---

# 10. Target revalidation

공감 실제 click 직전:

```python
TargetPostGuard.verify(page, post)
```

까지 넣는 것이 더 좋음.

그러려면 `execute_like_transaction()`에 `post` 전달.

---

# 11. Runtime threshold 300 → 999 문제

로그:

```text
공감수 999개
기준(300개 이상)
```

현재 코드 default는 999지만
`ConfigService`는 `data/config.json`이 있으면 그 값을 우선한다.

따라서 local runtime config에 300이 남아 있는 상태.

### 즉시 확인

```bash
cd /Volumes/무제/jusik/naver-blog-bot
python3 - <<'PY'
import json
p="data/config.json"
with open(p, encoding="utf-8") as f:
    d=json.load(f)
print(d.get("like_count_skip_threshold"))
PY
```

### 원하는 정책으로 수정

```bash
python3 - <<'PY'
import json, os, tempfile
p="data/config.json"
with open(p, encoding="utf-8") as f:
    d=json.load(f)
d["like_count_skip_threshold"]=999
tmp=p+".tmp"
with open(tmp,"w",encoding="utf-8") as f:
    json.dump(d,f,ensure_ascii=False,indent=2)
os.replace(tmp,p)
print("like_count_skip_threshold =", d["like_count_skip_threshold"])
PY
```

그리고 UI 시작 시 명확하게:

```text
[CONFIG] 공감수 제외 기준: 999 (source=data/config.json)
```

로그하도록 한다.

---

# 12. Comment JS SyntaxError 원인

현재:

```javascript
document.querySelectorAll(
  '.u_cbox_btn_upload,
   button[data-action="comment#upload"],
   button:has-text("등록")'
)
```

사용.

`button:has-text()`는 Playwright Locator engine selector이지
브라우저 native CSS selector가 아니다.

따라서 `document.querySelectorAll()`에서 SyntaxError 발생.

---

# 13. 최선의 수정 — delegated click listener

submit 버튼을 설치 시점에 `querySelectorAll`해서 listener를 붙이지 않는다.

DOM rerender에도 견디도록 document delegation 사용.

```javascript
window.__NAVER_FEED_CLICK_HANDLER__ = (e) => {
    const rawButton = e.target.closest
        ? e.target.closest('button, a, input[type="submit"]')
        : null;

    if (!rawButton) return;

    const isSubmit =
        rawButton.matches('.u_cbox_btn_upload') ||
        rawButton.matches('button[data-action="comment#upload"]') ||
        (
            rawButton.tagName === 'BUTTON' &&
            (rawButton.textContent || '').trim() === '등록'
        );

    if (isSubmit) {
        const editor = document.querySelector(
            '#naverComment__write_textarea, ' +
            'div.u_cbox_text[contenteditable="true"], ' +
            'textarea.u_cbox_text'
        );

        window.__NAVER_COMMENT_FINAL_TEXT__ = editor
            ? (editor.innerText || editor.value || '').trim()
            : '';

        window.__NAVER_COMMENT_SUBMITTED_FLAG__ = true;
        window.__NAVER_FEED_ACTION__ = 'SUBMIT_MANUAL';
        return;
    }

    const isClose =
        rawButton.matches('.u_cbox_btn_close') ||
        rawButton.matches('button[data-action="comment#close"]') ||
        rawButton.matches('a._close') ||
        rawButton.matches('button.btn_close');

    if (isClose) {
        window.__NAVER_FEED_ACTION__ = 'CLOSED';
    }
};

document.addEventListener(
    'click',
    window.__NAVER_FEED_CLICK_HANDLER__,
    true
);
```

기존 handler가 있으면 먼저 remove.

이렇게 하면 browser-native CSS만 사용.

---

# 14. Selector domain을 명시적으로 분리

프로젝트 규칙:

```text
Playwright `page.locator()`:
  :has-text()
  :text-is()
  :has()
  사용 가능

Browser `page.evaluate()` 내부의
document.querySelector/querySelectorAll:
  표준 CSS만 사용
```

테스트 추가:

```text
JS string 안에 ":has-text(" 존재하면 FAIL
JS string 안에 ":text-is(" 존재하면 FAIL
```

---

# 15. Login Required 오탐 방지

현재:

```python
login_box = page.locator(
    ".u_cbox_type_logged_out, .u_cbox_guide"
).first

if count > 0 and "로그인" in inner_text:
    login_required
```

문제:
hidden/template login guide가 DOM에 존재할 수 있음.

반드시:

```python
if (
    login_box.count() > 0
    and login_box.is_visible()
    and "로그인" in (login_box.inner_text() or "")
):
```

로 판단.

가능하면:

```css
.u_cbox_write_box.u_cbox_type_logged_out
```

를 우선.

---

# 16. open_comment_layer selector도 visible 기준

댓글 버튼:

```python
open_btn.count() > 0
```

만 보지 말고:

```python
open_btn.is_visible()
open_btn.is_enabled()
```

확인.

click 실패를 `pass`로 삼키지 말고:

```text
comment_open_click_failed
```

로 반환.

---

# 17. Comment submit verification fail-closed

현재 `submit_and_verify()`는:

```text
server mine 발견 X
editor clear X
```

이어도 마지막에 그냥 SUBMITTED를 반환.

이건 잘못됨.

최종:

```text
server mine PRESENT
→ SUBMITTED

또는
editor cleared + comment count 증가 같은 강한 secondary
→ SUBMITTED

그 외
→ FAILED/UNKNOWN
```

으로 변경.

예외 발생 시도 SUBMITTED 금지.

---

# 18. Page/context closed 처리

로그:

```text
Locator.count: Target page, context or browser has been closed
```

이런 Playwright closed-state는 recoverable DOM error처럼 처리하면 안 됨.

helper:

```python
def ensure_page_alive(page):
    if page is None or page.is_closed():
        raise BrowserDisconnectedError(...)
```

각 major phase 시작 전에 검사.

Playwright error text에:

```text
Target page, context or browser has been closed
```

가 있으면 FatalSessionError로 translate.

---

# 19. 프로필 락 구조의 실제 문제

앱 lock:

```text
data/browser_profile/.profile_lock
```

Chromium lock:

```text
data/browser_profile/SingletonLock
SingletonCookie
SingletonSocket
```

은 서로 다름.

현재 “락 초기화”는 `.profile_lock`만 삭제.

그래서 UI는 성공이라고 말하지만
Chromium `SingletonLock`이 살아 있으면 launch는 실패.

---

# 20. `_reset_lock()` 무조건 release 금지

현재:

```python
ProfileLockManager.release(USER_DATA_DIR)
messagebox("초기화 완료")
```

삭제.

대신:

```python
status = ProfileLockManager.inspect(USER_DATA_DIR)

if status.live_app_pid:
    "현재 앱 작업이 실행 중"
    return

if status.live_chromium_process:
    "프로필을 사용하는 Chromium이 아직 실행 중"
    return

cleanup_stale_locks()
```

---

# 21. 실제 Chromium process 탐지

macOS:

```text
ps -axo pid=,command=
```

결과에서:

```text
--user-data-dir=<profile path>
```

를 찾음.

중요:
외장 볼륨 경로의 Unicode NFC/NFD 차이가 있으므로
양쪽 문자열을 `unicodedata.normalize()` 후 비교.

---

# 22. Chromium stale singleton cleanup

live Chromium process가 없을 때만:

```text
SingletonLock
SingletonCookie
SingletonSocket
```

를 `os.path.lexists()`로 검사 후 삭제.

live process가 있으면 절대 삭제하지 않음.

---

# 23. Login Browser lifecycle 개선

현재 흐름:

```text
로그인 브라우저 열기
→ 사용자가 창 닫기
→ 닫힌 ctx에서 login cookie 확인 가능
```

개선:

```text
login browser open
↓
0.5초마다 login cookie poll
↓
로그인 성공 감지
↓
앱이 성공 메시지 표시
↓
앱이 BrowserSession.close() 수행
↓
Chromium 종료/Singleton release wait
```

사용자가 직접 창을 닫아야 세션을 저장하는 구조를 없앤다.

---

# 24. UI에서 login session 중 main start 금지

`self.login_session_active` 또는 login worker reference 유지.

로그인 브라우저가 열려 있으면:

```text
피드 작업 시작 버튼 disabled
락 초기화 버튼 disabled
```

로그인 session 종료 확인 후 다시 enable.

---

# 25. BrowserSession.close()

context.close 후 즉시 app lock release하지 말고:

```text
context.close
playwright.stop
↓
최대 5초 동안
Chromium user-data-dir process / SingletonLock 종료 확인
↓
app .profile_lock release
```

순서 권장.

---

# 26. EPIPE

로그의 Node EPIPE는 persistent context launch 실패/강제 cleanup 뒤
Playwright driver pipe가 끊긴 2차 증상으로 보는 것이 합리적.

핵심 해결:

```text
profile busy 상태에서 launch 자체를 시도하지 않기
live Chromium lock 삭제하지 않기
로그인 session 정상 종료 기다리기
```

---

# 27. Like unit/integration tests 추가

### A. hidden actual option + summary direct activation

before:

```text
NONE HIGH
like option hidden
```

summary click 후:

```text
LIKE active
```

Expected:

```text
SUCCESS
actual option scroll/click 0
circuit CLOSED
```

### B. summary opens layer

summary click 후:

```text
still NONE
like option visible
```

Expected:

```text
actual option click
LIKE active
SUCCESS
```

### C. layer fails to open

Expected:

```text
reaction_option_not_visible
action_taken=False
circuit CLOSED
```

### D. actual option clicked but state unknown

Expected:

```text
circuit OPEN
```

---

# 28. Comment tests

- install listener에 native querySelectorAll invalid pseudo selector 0
- dynamically rendered submit button도 delegated handler 감지
- hidden login guide → login_required 아님
- visible logged-out write box → login_required
- server submit verify 실패 → SUBMITTED 아님

---

# 29. Profile tests

- live `.profile_lock` PID → reset 거부
- live Chromium `--user-data-dir` → reset 거부
- stale custom lock + no Chromium → cleanup 허용
- stale SingletonLock + no Chromium → cleanup 허용
- login session active → feed start 금지

---

# 30. 실제 smoke 순서

공감만 먼저 검증:

```text
comment_enabled = false
max_items = 3
threshold = 999
```

확인:

```text
eligible 글 3개
summary scroll
2초 settle
공감 상태 정상 전환
circuit 0회
```

그 다음 댓글만:

```text
like_enabled = false
comment_enabled = true
max_items = 3
```

확인.

둘 다 한꺼번에 디버깅하지 않는다.

---

# 31. 완료 기준

LIKE:
- hidden option scroll timeout 0
- summary direct activation 경로 지원
- option-layer 경로 지원
- pre-click failure circuit open 0
- actual reaction clicked & unverified만 circuit open
- 실제 3글 연속 성공

COMMENT:
- browser JS invalid selector 0
- visible login detection
- adapter set/readback
- submit verify fail-closed

CONFIG:
- runtime threshold 999 로그 확인

PROFILE:
- false "락 초기화 완료" 0
- ProcessSingleton failure 0
- 로그인 session 종료 후 즉시 feed session 재시작 성공

---

# 32. 중요 정책

페이지 진입 후 공감 모듈까지 스크롤하고 2초 안정화 대기를 두는 것은
DOM/레이어 준비를 위한 deterministic UI settle로 구현한다.

랜덤 대기나 사용자를 흉내내는 동작으로 플랫폼 감지를 회피하도록
설계하지 않는다.
