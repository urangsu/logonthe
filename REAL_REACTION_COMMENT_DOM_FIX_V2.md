# NAVER FEED ASSISTANT
# REAL NAVER REACTION / COMMENT DOM FIX ORDER v2.0

기준 저장소: `urangsu/logonthe`
기준 브랜치: `main`

## 1. 핵심 결론

현재 공감 기능은 실제 Naver Blog reaction 구조를 정확히 모델링하지 못했다.

현재 코드:

```text
get_like_button()
→ button.u_likeit_button / a.u_likeit_button
→ click()
```

하지만 Naver Blog reaction UI는 단일 binary like가 아니라 다중 reaction 구조다.

상위 `u_likeit_button`은 reaction summary/opener일 수 있고,
실제 좋아요 선택은 하위 `[data-type="like"]` reaction option이다.

따라서 현재 현상:

```text
NOT_LIKED HIGH
→ 상위 버튼 클릭
→ reaction selector만 열림
→ 실제 좋아요는 선택 안 됨
→ 상태는 계속 NOT_LIKED
→ circuit breaker OPEN
```

과 부합한다.

## 2. 현재 HIGH NOT_LIKED 판정 버그

현재 `LikeTransactionService.resolve_like_state()`에는 다음 논리가 있다.

```javascript
if (
    cls.includes('off') ||
    (!cls.includes('_on') && !cls.includes('active'))
) {
    notLikedSignals.push('class_off_inactive');
}
```

이 로직은 잘못됐다.

class가 단순히 `u_likeit_button`뿐이어도 `_on`도 `active`도 없으므로
NOT_LIKED signal을 추가한다.

즉 neutral element도 OFF로 취급한다.

이 signal과 `aria-pressed=false`가 같이 잡히면
실제로는 신뢰할 수 없는데도 `NOT_LIKED/HIGH`가 된다.

## 3. 즉시 삭제

완전 삭제:

```javascript
(!cls.includes('_on') && !cls.includes('active'))
```

NOT_LIKED는 실제 reaction option의 explicit negative signal만 허용:

```text
exact class token "off"
aria-pressed == false
aria-checked == false
known zeroface/off signature
```

## 4. Resolver 역할 분리

기존 `get_like_button` 하나로 처리하지 않는다.

새 API:

```python
get_reaction_module(page)
get_reaction_summary_button(page)
get_reaction_like_option(page)
get_active_reaction_option(page)
get_reaction_count_element(page)
```

## 5. Reaction module 후보

```css
.u_likeit_list_module
.u_likeit._reactionModule
div[data-sid="BLOG"][data-cid]
```

실제 DOM diagnostic으로 확정한다.

## 6. Summary / opener

```css
a.u_likeit_button
button.u_likeit_button
```

직접 좋아요 target으로 취급하지 않는다.

역할:

```text
reaction chooser open
aggregate status display
```

## 7. 실제 좋아요 option

우선 selector:

```css
a.u_likeit_list_btn[data-type="like"]
a.u_likeit_list_button[data-type="like"]
button.u_likeit_list_btn[data-type="like"]
button.u_likeit_list_button[data-type="like"]
[role="radio"][data-type="like"]
```

이 element가 실제 click target이다.

## 8. 다른 reaction 보존

사용자가 이미 좋아요/최고예요/감사해요/재밌어요/놀라워요/슬퍼요 중
어떤 reaction이든 남겼다면 자동으로 변경하지 않는다.

```text
active reaction exists
→ ALREADY_REACTED
→ click 0
```

## 9. Reaction state model

```python
@dataclass
class ReactionStateResult:
    reacted: bool
    reaction_type: str | None
    confidence: LikeConfidence
    signals: list[str]
```

## 10. State resolution

1. reaction option 목록 찾기
2. 각 option에서:
   - aria-pressed
   - aria-checked
   - exact off token
   - exact active/on token
3. 하나라도 active → reacted=True HIGH
4. known option 모두 explicit off → reacted=False HIGH
5. 그 외 UNKNOWN

## 11. Class token exact match

금지:

```python
"off" in cls
```

권장:

```python
tokens = set(cls.split())
"off" in tokens
```

## 12. 좋아요 클릭 flow

```text
ReactionState 확인
↓
이미 reaction 있음 → skip

NONE HIGH
↓
like option visible?
├─ yes → click actual option
└─ no
   ↓
   summary/opener click
   ↓
   like option visible wait
   ↓
   click actual option
```

## 13. Postcondition

actual like option을 polling한다.

성공 signal:

```text
aria-pressed=true
OR aria-checked=true
OR explicit active/on state
```

aggregate count green은 secondary signal로 사용.

## 14. Circuit breaker

actual reaction option을 클릭한 사실이 확인된 경우에만
transition failure로 circuit breaker를 trip한다.

summary/opener만 눌렀는데 like option을 못 찾은 경우는
`REACTION_OPTION_NOT_FOUND`로 해당 글만 실패 처리한다.

## 15. 현재 test_like_transaction.py의 문제

현재 테스트는 사실상:

```text
circuit breaker가 열리는가
reset되는가
```

만 검증한다.

실제 다음은 검증하지 않는다.

```text
NOT_LIKED DOM 판정
actual like option resolve
click 후 aria 변화
다른 reaction active
summary opener와 actual option 구분
```

따라서 "29 tests OK"는 실제 공감 정상 작동의 증거가 아니다.

## 16. Reaction DOM fixture tests

추가:

```text
tests/fixtures/reaction_none.html
tests/fixtures/reaction_liked.html
tests/fixtures/reaction_other_active.html
tests/fixtures/reaction_neutral_summary.html
```

neutral summary button 하나만으로 `NOT_LIKED HIGH`가 되면 테스트 실패.

## 17. 실제 Browser diagnostic

첫 live eligible post에서:

```text
[REACTION_DIAG]
module_count=
summary_count=
option_count=
like_option_count=

summary:
tag=
class=
aria-pressed=

like_option:
tag=
class=
aria-pressed=
aria-checked=
data-type=
visible=
```

클릭 후 동일 데이터 기록.

## 18. 댓글 문제도 비활성화라고 단정하지 않기

현재:

```text
editor 못 찾음
→ 댓글 비활성화 글
```

로 로그하는데 진단이 잘못됐다.

가능 원인:

```text
댓글 기능 비활성
open button selector 실패
open click 실패
댓글 layer load 지연
로그인 요구
DOM selector 변경
iframe/frame context
```

## 19. Comment failure reason

```text
COMMENT_DISABLED
COMMENT_OPEN_BUTTON_NOT_FOUND
COMMENT_OPEN_CLICK_FAILED
COMMENT_LAYER_TIMEOUT
COMMENT_EDITOR_NOT_FOUND
COMMENT_LOGIN_REQUIRED
COMMENT_FRAME_NOT_RESOLVED
```

로 분리한다.

## 20. CommentEditorAdapter 실제 연결

`CommentEditorAdapter` 파일은 존재하지만 현재 main path는 여전히
`CommentInteractionService`가 직접 `get_comment_editor()`와
`replace_editor_text()`를 사용한다.

즉 adapter가 실제로 연결되지 않았다.

수정:

```python
adapter = CommentEditorAdapter.resolve(page)
adapter.set_text(draft)
adapter.get_text()
```

prepare/refine/read/verify 모두 adapter 사용.

## 21. Comment open polling

open button click 후 고정 0.8초 대기 금지.

최대 5초 동안:

```text
comment root
editor
login prompt
disabled signal
```

중 하나가 나타날 때까지 polling.

## 22. 댓글 비활성 판정

명시적 disabled signal이 있을 때만.

editor missing만으로 disabled 처리 금지.

## 23. 완료 보고 기준

다음 LIKE gate 전부 통과해야 완료.

```text
1. neutral summary button으로 NOT_LIKED HIGH 오판 0
2. 실제 data-type=like option resolve
3. NONE state resolve
4. existing like resolve
5. existing other reaction resolve
6. 다른 reaction 보존
7. summary와 actual option 구분
8. actual option click
9. postcondition active 확인
10. aggregate green secondary 확인
11. 실제 blog 3개 연속 like 성공
12. already reacted 글 click 0
```

COMMENT gate:

```text
1. open button resolve
2. click 결과 확인
3. editor/frame/root 구분
4. adapter main path 사용
5. draft set read-back
6. explicit disabled signal만 disabled 판정
7. editor missing 오진 0
8. 실제 댓글 가능 blog 3개 draft 표시 성공
```

## 24. 구현 우선순위

P0:
- Like resolver를 reaction-module 기반으로 재작성
- false HIGH 로직 삭제
- actual `data-type=like` option 클릭
- reaction fixture tests

P1:
- live reaction diagnostics 3글
- CommentEditorAdapter 실제 main path 통합
- comment failure reason 세분화
- comment polling/frame handling

## 25. 최종 지시

```text
현재 공감 코드의 get_like_button() 하나로
summary/opener와 실제 reaction option을 같이 취급하지 말 것.

Naver Blog 다중 공감 구조를 모델링하고
실제 data-type="like" reaction option을 클릭할 것.

!_on && !active를 NOT_LIKED 근거로 사용하는 코드 즉시 삭제.

explicit off / aria false는 실제 reaction option에서만 상태 근거로 사용.

어떤 reaction이든 이미 active이면 기존 사용자 선택을 보존하고 click하지 말 것.

실제 option click 후 option의 aria-checked/aria-pressed/class 변화로 postcondition 검증.

29개 unit test 통과를 실제 공감 정상화 증거로 사용하지 말 것.
실제 reaction DOM fixture와 live smoke 결과가 필요하다.

CommentEditorAdapter 역시 파일만 만들지 말고
CommentInteractionService main path에서 실제 사용하게 할 것.

editor를 못 찾았다는 이유만으로 "댓글 비활성화"라고 로그하지 말 것.

위 LIKE gate 12개와 COMMENT gate 8개 전부 통과 전
"완벽 구현" 또는 "완료"라고 보고하지 말 것.
```
