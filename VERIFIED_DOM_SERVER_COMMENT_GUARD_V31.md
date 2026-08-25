# NAVER FEED ASSISTANT
# VERIFIED DOM + SERVER COMMENT DUPLICATE GUARD v3.1

## 핵심 결론

사용자가 확보한 실제 Naver Mobile Blog DOM과 현재 `main` 코드를 대조하면 다음이 확인된다.

1. 공감 summary/opener와 실제 `data-type="like"` reaction option을 현재 코드가 혼동하고 있다.
2. 댓글 열기 실제 stable selector는 `button[data-click-area="pst.re"]`인데 현재 코드는 `pst.reply`를 우선한다.
3. `CommentEditorAdapter` 파일은 존재하지만 main interaction path에서 실제 사용되지 않는다.
4. 중복 댓글 방지는 Local History만으로 충분하지 않다.
5. `mine:true`와 `u_cbox_type_mine`은 강한 서버 신호지만, 삭제/수정 버튼은 단독 확정 신호로 쓰면 안 된다.
6. 댓글 목록이 lazy-load/pagination이면 첫 화면에서 mine을 못 찾았다고 ABSENT로 확정하면 안 된다.
7. 현재 `controller.py`는 `should_like`/`should_comment`를 계산하지만 `processor.process()`에 전달하지 않아 per-component idempotency가 실제로 작동하지 않는다.

---

## 1. 실제 Reaction DOM 계약

```html
<div class="u_likeit_list_module _reactionModule">
  <a class="u_likeit_button _button" aria-pressed="false">
    <span class="u_likeit_text _count">15</span>
  </a>

  <a class="u_likeit_list_button _button off"
     data-type="like"
     role="menuitem"
     aria-pressed="false">
  </a>
</div>
```

### Resolver 분리

```python
get_reaction_module(page)
get_reaction_summary_button(page)
get_reaction_options(page)
get_reaction_like_option(page)
get_active_reaction_option(page)
get_reaction_total_count_text(page)
```

`get_like_button()` 하나로 summary와 actual option을 같이 처리하지 않는다.

### 실제 좋아요 selector

```css
a.u_likeit_list_button[data-type="like"]
button.u_likeit_list_button[data-type="like"]
a.u_likeit_list_btn[data-type="like"]
button.u_likeit_list_btn[data-type="like"]
[data-type="like"][role="menuitem"]
[data-type="like"][role="radio"]
```

### Summary/opener

```css
.u_likeit_list_module > a.u_likeit_button
.u_likeit_list_module > button.u_likeit_button
a.u_likeit_button[data-like-click-area]
```

summary는 actual like target으로 클릭하지 않는다.

---

## 2. Reaction 상태 판정

```python
class ReactionType(str, Enum):
    LIKE = "like"
    IMPRESSIVE = "impressive"
    THANKS = "thanks"
    HAHA = "haha"
    WOW = "wow"
    SAD = "sad"
    NONE = "none"
    UNKNOWN = "unknown"
```

Strong active:

```text
aria-pressed=true
aria-checked=true
exact class token "on"
exact class token "active"
```

Strong inactive:

```text
aria-pressed=false
aria-checked=false
exact class token "off"
```

삭제해야 하는 기존 로직:

```javascript
(!cls.includes('_on') && !cls.includes('active'))
```

활성 클래스가 없다는 이유만으로 NOT_LIKED를 만들면 안 된다.

class는 substring이 아니라 exact token으로 판정한다.

```python
tokens = set(class_string.split())
```

---

## 3. 다른 reaction 보존

어떤 `data-type` option이든 active면:

```text
ALREADY_REACTED
```

자동으로 `like`로 변경하지 않는다.

---

## 4. 실제 Like flow

```text
reaction module resolve
↓
active reaction 있음?
├─ YES → skip
└─ NO HIGH
    ↓
    aggregate count guard
    ↓
    daily visitor guard
    ↓
    like option visible?
    ├─ YES → click actual like option
    └─ NO
        ↓
        summary opener click
        ↓
        like option visible wait
        ↓
        click actual like option
```

Postcondition:

```text
actual data-type=like option
aria-pressed=true
OR aria-checked=true
OR exact on/active
```

summary 숫자 초록색 변화는 secondary signal만 사용한다.

999 threshold는 summary aggregate count를 기준으로 한다.

---

## 5. Circuit Breaker

다음 경우에만 trip:

```text
actual data-type=like option click 성공
+
postcondition 미확인
```

summary opener만 클릭했거나 option을 못 찾은 경우는 해당 글 failure일 뿐 session circuit open 사유가 아니다.

---

## 6. 실제 댓글 Open DOM

```html
<button
  class="Interact__comment_btn--Wbuoq"
  data-click-area="pst.re">
  <span class="blind">댓글</span>
</button>
```

Primary:

```css
button[data-click-area="pst.re"]
button[data-click-area*="pst.re"]
button:has(.blind:text-is("댓글"))
```

Fallback:

```css
a.btn_comment
a.u_cbox_btn_reply
button[class^="Interact__comment_btn--"]
button[class*="Interact__comment_btn--"]
```

hashed suffix는 primary로 사용하지 않는다.

---

## 7. 실제 Comment Editor DOM

```html
<div
 id="naverComment__write_textarea"
 class="u_cbox_text u_cbox_text_mention"
 contenteditable="true"
 data-area-code="RPC.input">
</div>
```

Primary:

```css
#naverComment__write_textarea
```

Secondary:

```css
div.u_cbox_text[contenteditable="true"]
```

Legacy:

```css
textarea.u_cbox_text
```

Submit:

```css
button[data-action="comment#upload"]
button.u_cbox_btn_upload
```

`CommentEditorAdapter`를 prepare/refine/read/verify 전 경로에서 실제 사용한다.

---

## 8. Comment open은 polling

고정 0.8초 대기 금지.

최대 5초 동안:

```text
editor ready
login required
explicit disabled
comment root loaded
timeout
```

중 하나를 기다린다.

editor missing만으로 `댓글 비활성화`라고 로그하지 않는다.

---

## 9. Server-side duplicate comment guard

bool API 금지.

```python
class CommentPresenceState(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
```

```python
@dataclass
class CommentPresenceResult:
    state: CommentPresenceState
    confidence: Confidence
    comment_no: str | None
    comment_text: str | None
    evidence: list[str]
    loaded_comment_count: int
    total_comment_count: int | None
    list_complete: bool
```

---

## 10. 내 댓글 Strong Signal

### 1순위

`data-info`의 `mine:true`.

공백 허용 regex:

```regex
(?:^|[,{\s])mine\s*:\s*true(?:[,}\s]|$)
```

`eval()` 금지.

### 2순위

exact class:

```text
u_cbox_type_mine
```

이 둘 중 하나면 `PRESENT / HIGH`.

---

## 11. 삭제/수정 버튼은 보조 신호

다음만으로 PRESENT 확정 금지:

```text
u_cbox_btn_delete
u_cbox_btn_modify
button[data-action*="delete"]
```

블로그 작성자/관리자 권한에서는 타인 댓글에 관리 UI가 보일 가능성이 있으므로 weak supporting signal로만 사용한다.

닉네임 일치도 weak signal.

`작성자` blind icon도 mine 판정에 사용하지 않는다.

---

## 12. Lazy-load / pagination

첫 로드된 댓글에서 mine을 못 찾았다고 `ABSENT`로 만들지 않는다.

ABSENT HIGH 조건:

```text
전체 댓글 목록 scan 완료
AND
mine strong signal 없음
```

또는:

```text
total count == 0
AND
editor ready
```

Partial list이면:

```text
UNKNOWN
```

기본 정책:

```json
"comment_duplicate_unknown_policy": "skip_comment"
```

중복 댓글 방지가 목적이므로 fail-closed.

---

## 13. Bounded exhaustive scan

```text
mine scan
↓
load more / scroll
↓
mine scan
↓
...
```

최대 comment/page/time 제한을 둔다.

제한 도달 전 전체 목록을 끝까지 확인하지 못하면 `UNKNOWN`.

---

## 14. Local History와 Live Naver State

역할 분리:

```text
History = 과거 audit/cache
Live DOM = 현재 server truth
```

History NONE + Server PRESENT:

```text
댓글 생성 0
History를 server_detected submitted로 reconcile
```

History SUBMITTED + Server ABSENT HIGH:

```text
사용자가 댓글을 삭제했을 수 있으므로 server state를 우선
```

---

## 15. 현재 Controller P0 버그

현재:

```python
should_like = ...
should_comment = ...
```

를 계산하지만 실제 호출은:

```python
processor.process(detail_page, post)
```

뿐이다.

Processor는 `self.like_enabled`, `self.comment_enabled` 전역 플래그를 사용한다.

따라서:

```text
history comment=SUBMITTED
history like=UNKNOWN
```

이면 `should_comment=False`, `should_like=True`라도 Processor에서 댓글 경로가 다시 실행될 수 있다.

### 수정

```python
@dataclass
class PostActionPlan:
    process_like: bool
    process_comment: bool
    local_like_recorded: bool
    local_comment_recorded: bool
```

```python
processor.process(
    detail_page,
    post,
    action_plan=plan
)
```

Processor는 `action_plan.process_comment`, `action_plan.process_like`를 사용한다.

---

## 16. 최종 처리 순서

```text
NAVIGATE
↓
TargetPostGuard
↓
cheap context extraction
↓

LIKE
live reaction state
↓
already reacted → skip
none high
↓
aggregate 999 guard
↓
visitor guard
↓
actual data-type=like click
↓
verify
↓

COMMENT
open comment layer
↓
server comment presence scan
├─ PRESENT
│   → draft generation 0
│   → input 0
│   → history reconcile
│
├─ UNKNOWN
│   → default skip
│
└─ ABSENT HIGH
    ↓
    Gemini/local draft generation
    ↓
    CommentEditorAdapter set/read-back
    ↓
    user review
    ↓
    Enter
    ↓
    submit
    ↓
    server mine:true/class 재확인
```

중요: Server presence check 전 Gemini를 호출하지 않는다.

---

## 17. Submit Verification 강화

등록 후:

```text
editor cleared
+
server list에 mine:true/u_cbox_type_mine 등장
+
가능하면 final text normalized match
```

를 강한 성공 조건으로 사용한다.

---

## 18. Test 필수

Reaction:

```text
neutral summary -> NOT_LIKED HIGH 아님
reaction none
like active
other reaction active
hidden layer -> opener -> option
actual like transition
aggregate 998
aggregate 999
```

Comment Presence:

```text
mine:true -> PRESENT
u_cbox_type_mine -> PRESENT
other comment -> PRESENT 아님
other comment + delete button -> PRESENT 아님
nickname 같음 + mine false -> PRESENT 아님
partial list -> UNKNOWN
complete list no mine -> ABSENT
empty comments + editor ready -> ABSENT
lazy load 후 mine 발견 -> PRESENT
```

Controller:

```text
history comment submitted + like pending
→ comment path 재실행 0
```

---

## 19. 완료 Gate

LIKE:
- actual `data-type=like` click
- other reaction 보존
- false HIGH 제거
- postcondition active 확인
- 3 actual posts 연속 성공

COMMENT:
- `pst.re` open 성공
- Adapter main path
- PRESENT/ABSENT/UNKNOWN
- strong mine signals
- partial list fail-closed
- server PRESENT -> Gemini 0 / draft 0
- submit 후 server mine 검증
- known existing-comment post duplicate 0
- 3 actual commentable posts draft 성공

위 증거 전에는 “완료”라고 보고하지 않는다.
