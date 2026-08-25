# NAVER FEED ASSISTANT
# STABILIZATION & REGRESSION ENGINEERING REVIEW v1.0

기준 저장소: `urangsu/logonthe`
기준 브랜치: `main`

## 1. 결론

현재 문제는 단일 버그가 아니라 구조적 안정성 문제다.

최근 실행에서 오류가 하나씩 연쇄적으로 드러나는 이유는 다음과 같다.

1. 런타임 인터페이스 계약 테스트 부족
2. 한 게시글 예외가 전체 세션을 종료시키는 구조
3. Navigation 실패 후에도 공감/댓글을 계속할 수 있는 fail-open 경로
4. DOM 상태 판정의 신뢰도 모델 부족
5. Config/History의 덮어쓰기 위험
6. 댓글 생성기가 의미 모델보다 문자열 조합에 의존
7. 원격 저장소에서 재현 가능한 tests/ 및 CI 부재

새 기능 추가보다 Stabilization Gate를 먼저 통과해야 한다.

---

## 2. P0 — Navigation Fail-Open 제거

현재 `PostProcessor.process()`는 `detail_page.goto()` 예외를 WARNING만 남기고 이후 Context/Like/Comment를 계속 수행한다.

이 경우:

```text
A 글 처리
→ B 글 이동 실패
→ detail_page에는 A 글이 남음
→ 프로그램은 B라고 생각하고 A 글에 공감/댓글 가능
```

이 위험이 있다.

### 수정

`TargetPostGuard`를 추가한다.

```python
class TargetPostGuard:
    @classmethod
    def verify(cls, page, expected_post):
        # current URL -> canonical blog_id/log_no
        # expected_post.key와 일치하는지 검사
        ...
```

Side effect 직전마다:

```text
current page canonical key == expected post.key
```

가 반드시 참이어야 한다.

Navigation은:

```text
goto 성공
+ canonical URL 검증
+ post DOM sentinel 확인
```

세 조건을 만족해야 성공으로 본다.

실패하면 `PostNavigationError`로 해당 글만 종료한다.

---

## 3. P0 — Per-Post Error Boundary

현재 Controller는 `processor.process()` 예외를 post 단위로 격리하지 않는다.

따라서 한 post 오류가 outer `except`까지 올라가 전체 BrowserSession을 종료한다.

### 수정

```python
for post in new_posts:
    try:
        result = processor.process(detail_page, post)
    except StopRequestedException:
        raise
    except FatalSessionError:
        raise
    except RecoverablePostError as e:
        result = build_failed_result(post, e)
        history.record_result(result)
        detail_page = recover_detail_page(detail_page)
        continue
```

### Error taxonomy

```text
RecoverablePostError
- NavigationError
- DOMContractError
- CommentUnavailableError
- GenerationError

FatalSessionError
- BrowserDisconnectedError
- PersistentProfileError
- InvalidConfigError
```

User STOP과 FatalSessionError만 전체 종료한다.

---

## 4. P0 — Resolver API Contract

최근 실제 오류:

```text
interaction.py -> get_comment_open_button()
resolver.py -> get_comment_button()
```

이런 API drift는 compile만으로 충분히 잡히지 않는다.

### 수정

Canonical API를 하나로 고정:

```text
get_comment_button
get_comment_write_box
get_comment_editor
get_secret_comment_checkbox
get_comment_submit_button
```

Runtime monkey-patch alias는 migration 후 제거한다.

### Contract test

```python
def test_comment_resolver_contract():
    names = [
        "get_comment_button",
        "get_comment_write_box",
        "get_comment_editor",
        "get_secret_comment_checkbox",
        "get_comment_submit_button",
    ]
    for name in names:
        assert callable(getattr(MobileDOMResolver, name, None))
```

`pyright`도 CI에 추가한다.

---

## 5. P0 — ConfigService 상태 손상

현재 `ConfigService.save(data)`는:

```python
self.data = data
```

로 전체 설정을 교체한다.

UI는 partial dict만 전달하므로 key 유실 가능성이 있다.

현재 tracked `config.json`에도 `schema_version`이 없으며,
코드 default like threshold는 999인데 tracked config는 300이다.

### 수정

```python
def update_many(self, values):
    merged = DEFAULT_CONFIG.copy()
    merged.update(self.data)
    merged.update(values)
    merged["schema_version"] = CURRENT_SCHEMA_VERSION
    self._atomic_save(merged)
    self.data = merged
```

UI는 `save()`가 아니라 `update_many()`만 사용한다.

### Runtime Config 분리

```text
config.example.json   # Git tracked
data/config.json      # Runtime, gitignored
```

Repo merge가 개인 실행설정을 바꾸지 않도록 한다.

---

## 6. P0 — History를 Monotonic Merge로 변경

현재 `HistoryStore.record_result()`는 기존 post record를 통째로 교체한다.

위험 예:

```text
1차 실행: comment SUBMITTED
2차 실행: comment_enabled=False
→ result.comment = NONE
→ 기존 SUBMITTED record overwrite
→ 다음 실행에서 중복 댓글 가능
```

### 수정

성공 상태를 downgrade하지 않는다.

```text
SUBMITTED > DRAFTED > NONE
```

`mark_skipped()`도 전체 record overwrite 금지.

Comment/Like를 action 단위로 merge한다.

History 저장도 temp + `os.replace()` atomic write.

저장 실패를 silent pass하지 말고 WARNING 로그.

---

## 7. P0 — Component-Level Idempotency

현재:

```python
if comment_enabled and history.is_comment_submitted(post.key):
    continue
```

는 댓글 완료 글이면 Like 처리까지 전부 건너뛴다.

수정:

```text
should_process_comment
should_process_like
```

를 독립적으로 판단한다.

댓글 완료여도 Like가 필요하면 Like만 처리 가능해야 한다.

---

## 8. P0 — Like를 Transaction + Circuit Breaker로 변경

최근:

```text
before NOT_LIKED
click
after polling NOT_LIKED
```

가 발생했다.

두 가능성:

```text
A. 클릭 실패
B. 실제 LIKED인데 resolver가 NOT_LIKED로 오판
```

B라면 다음 실행에서 다시 클릭해 기존 공감을 취소할 수 있다.

### LikeStateResult

```python
@dataclass
class LikeStateResult:
    state: LikeState
    confidence: float
    signals: list[str]
```

신호:

```text
aria-pressed
button class
parent class
SVG/icon state
count style
label
like-count delta
```

### 클릭 조건

```text
state == NOT_LIKED
AND confidence == HIGH
```

일 때만.

### Transaction

```text
PRECONDITION: NOT_LIKED HIGH
ACTION: click
POSTCONDITION: LIKED HIGH
```

POSTCONDITION 실패 시:

```text
LIKE_CIRCUIT_OPEN
```

으로 해당 session의 추가 Like click을 중지한다.
댓글은 계속 진행한다.

---

## 9. Like 처리 순서 변경

현재:

```text
Popularity Guard
→ LikeState
```

권장:

```text
LikeState
↓
LIKED -> 종료
UNKNOWN/LOW CONFIDENCE -> skip
NOT_LIKED HIGH
↓
Like Count Guard
↓
Daily Visitor Guard
↓
Click transaction
```

이미 LIKED면 stats page/visitor 조회도 하지 않는다.

---

## 10. Like DOM Diagnostic

클릭 전/후 자동 probe:

```json
{
  "class": "...",
  "aria_pressed": "...",
  "aria_label": "...",
  "inner_text": "...",
  "parent_class": "...",
  "icon_class": "...",
  "count": "...",
  "outer_html": "..."
}
```

실제 Naver DOM 5~10개를 확보하기 전 resolver state signature를 확정하지 않는다.

---

## 11. Daily Visitor Confidence 재정의

현재 broad text scan에서 `오늘` 숫자를 찾으면 `confidence="high"`로 기록한다.

이건 증거 수준과 맞지 않는다.

```text
HIGH:
'오늘/TODAY' label과 value의 명확한 DOM 관계

MEDIUM:
동일한 작은 container 안에서 label/value 확인

UNKNOWN:
page-wide text scan fallback
```

HIGH/MEDIUM만 숫자를 정책 판정에 사용한다.

UNKNOWN은 `daily_visitor_unknown_policy` 적용.

---

## 12. P0 — CommentEditorAdapter

현재 댓글 editor는 contenteditable 또는 textarea일 수 있는데 읽기/쓰기 로직이 분산돼 있다.

`read_final_text()`는 `inner_text()`를 사용하므로 textarea variant에서 빈 값이 될 수 있다.

### 신규

```python
class CommentEditorAdapter:
    def focus(self): ...
    def get_text(self): ...
    def set_text(self, text): ...
    def is_visible(self): ...
```

textarea이면 `input_value()`,
contenteditable이면 `inner_text()`.

모든 prepare/refine/read/verify가 Adapter를 사용한다.

---

## 13. Draft Write Verification

댓글 입력:

```text
set_text
↓
read_text
↓
normalized exact match
```

검증 후에만 WAITING_USER로 전환.

---

## 14. Submit Verification 강화

현재 editor가 비워졌다는 신호만으로 성공 판정될 수 있다.

Layer close/re-render도 비슷하게 보일 수 있다.

권장 성공 신호:

```text
A. 신규 comment DOM에 final text 일부 등장
B. comment count 증가
C. comment write network response success
D. editor cleared
```

A/C 중 하나 + D 같은 조합으로 검증.

---

## 15. Mouse Submit final text 보존

사용자가 마우스로 등록하면 editor가 먼저 비워질 수 있다.

delegated click capture 시:

```javascript
window.__NAVER_COMMENT_FINAL_TEXT__ = readEditorText()
```

를 저장하여 History에 실제 수정 문장을 남긴다.

StyleLearner를 하려면 이 데이터 정확성이 선행돼야 한다.

---

## 16. P0 — 댓글 Action Grammar 재설계

현재:

```text
action = "먹어보다"
template = "{action} 싶네요"
→ "먹어보다 싶네요"
```

는 단순 오타가 아니라 data model 오류다.

### 금지

```text
infinitive + suffix 문자열 조립
```

### 권장

`ActionForms`:

```python
@dataclass
class ActionForms:
    try_phrases: list[str]
    plan_phrases: list[str]
```

FOOD:

```text
저도 한번 먹어보고 싶네요
다음에 가면 이 메뉴도 주문해보고 싶어요
```

CAFE:

```text
저도 한번 마셔보고 싶네요
다음에 가면 이 메뉴도 주문해보고 싶어요
```

완성된 활용형을 보유한다.

---

## 17. Category Signal과 Subject Entity 분리

현재 `맛집` 같은 카테고리 키워드가 subject가 되어:

```text
맛집 진짜 맛있어 보여요
```

가 나온다.

### META subject 금지

```text
맛집
카페
여행
후기
리뷰
추천
메뉴
정보
제품
일상
방문
```

은 category signal에는 쓸 수 있지만 subject에는 쓰지 않는다.

### EntityRole

```text
LOCATION
PLACE
BRAND
MENU
DRINK
PRODUCT
CHARACTER
STYLE
ACTIVITY
TOPIC
META
```

Slot type이 맞지 않으면 candidate reject.

---

## 18. Flat Keyword Classifier 개선

현재 2글자 exact matching은 `근육빵빵 -> 빵` 문제는 막지만 edge case가 많다.

```text
시장님 vs 시장
산책 -> PET/TRAVEL
디저트 -> FOOD/CAFE
리뷰 -> BOOK_MOVIE/PRODUCT
후기 -> 모든 리뷰 글
```

### Weighted Signal

```python
KeywordSignal(text="히츠마부시", weight=10, kind="strong")
KeywordSignal(text="후기", weight=0.5, kind="meta")
```

Generic keyword는 낮은 가중치 또는 classifier에서 제외.

### Confidence Margin

1위와 2위 score 차이가 작으면 `UNKNOWN_TOPIC`.

억지 분류보다 UNKNOWN이 안전하다.

---

## 19. 1글자 Semantic Token 문제

현재 tokenizer가 2글자 이상만 사용한다.

따라서:

```text
책
앱
펌
```

같은 의미 있는 1글자 keyword가 누락될 수 있다.

1글자 token은 allowlist 방식으로 지원한다.

---

## 20. Evidence를 실제 Validator에 연결

현재 `CommentCandidate.evidence` field는 있지만 generator/validator에서 실질적으로 사용되지 않는다.

다음 형용사는 본문 evidence가 있어야 한다.

```text
아늑
평화롭
운치
달콤
정갈
탁 트인
조용
넓다
가성비
친절
바삭
```

근거가 없으면 reject.

---

## 21. 현재 v3.1 구현과 Spec 차이

현 Composer는 카테고리에 따라 4~6개 수준 후보만 만드는 경로가 많다.

또 BEAUTY/FASHION 외 다수 category가 generic `else` generator로 들어간다.

따라서 Category × Reaction Matrix는 policy data에는 있지만 실제 문장 생성에는 충분히 반영되지 않았다.

### Gate

```python
assert len(candidates) >= 12
```

각 category별 top-weight ReactionIntent가 실제 후보로 존재하는지도 테스트한다.

---

## 22. Top-3 Random 제거

현재:

```python
top_candidates = scored_candidates[:3]
chosen = random.choice(top_candidates)
```

는 같은 글에서도 품질 낮은 2~3위 후보가 선택될 수 있다.

수정:

```text
best score 선택
```

score 차이가 epsilon 이하일 때만 diversity tie-break.

---

## 23. Confidence 상수 제거

현재 wrapper는 실제 detector confidence 대신 0.85를 고정 반환한다.

실제 classifier confidence를 candidate/result에 전달한다.

---

## 24. UI Refiner Race Condition

현재 UI thread에서 직접 댓글을 생성한 뒤 text만 queue에 넣는다.

post A에서 버튼 클릭과 worker transition이 겹치면:

```text
A context 댓글
→ B editor에 적용
```

가능성이 있다.

### WorkerCommand

```python
WorkerCommand(
    kind=REFINE_COMMENT,
    post_key=current_post_key,
    mode="praise"
)
```

UI는 intent만 보낸다.

실제 생성은 worker thread에서 수행하고:

```text
command.post_key == active_post.key
```

일 때만 적용한다.

stale command는 discard.

---

## 25. StateManager Thread Safety

현재 mutable state를 lock 없이 worker/UI가 공유한다.

`threading.RLock`을 사용하고 listener에는 immutable/copy snapshot을 전달한다.

---

## 26. Profile Lock 결함

MainWindow 시작 시 unconditional:

```python
ProfileLockManager.release(USER_DATA_DIR)
```

가 실행된다.

다른 app instance가 실제 사용 중이어도 lock file을 지울 수 있다.

### 수정

startup auto-release 제거.

`is_locked()`의 PID 생존 검사로 stale lock만 자동 정리.

수동 “락 초기화”도 live PID이면 차단.

---

## 27. Gemini는 Session Preflight로 변경

JS Apple Events가 꺼져 있으면 매 post마다 확인하지 않는다.

Session 시작 시 한 번:

```text
Chrome found?
Gemini tab?
Apple Events JS?
Automation permission?
```

진단.

JS OFF면 해당 session Gemini disabled,
local engine으로 고정.

---

## 28. Gemini Bridge 추가 위험

AppleScript 내부에 JS 문자열을 직접 삽입하는 부분은 quote escaping 문제가 생길 수 있다.

`json.dumps(js_code)` 등 안전한 escaping 사용.

또:

```text
focus verify
→ paste
→ editor contains prompt verify
→ Enter
```

순서를 강제.

최종 answer도 fresh response identity를 확인.

---

## 29. Content Extraction 분리

현재 local engine도 AI prompt용 700자 excerpt를 그대로 쓴다.

권장:

```text
prompt_excerpt: 700자
analysis_text: 2,000~3,000자
```

분리.

단순 첫 700자보다:

```text
첫 문단
+ title entity 포함 문장
+ salient sentence
```

sampling.

---

## 30. Per-Post Observability

모든 로그에:

```text
post_key
phase
```

포함.

예:

```text
[post=abc:123 phase=LIKE_VERIFY]
```

Recoverable error 시 local diagnostics bundle:

```text
data/diagnostics/<timestamp>/<post_key>/
  meta.json
  resolver_probe.json
  relevant_dom.html
```

Gitignore.

---

## 31. Dry-Run Mode

실제 side effect 전에 반드시 read-only validation mode를 둔다.

### observe

```text
navigation
context extraction
category
subjects
candidate generation
like state probe
like count
visitor count
comment resolver probe
```

하지만:

```text
like click X
comment submit X
```

### draft_only

댓글창 + 초안 fill까지,
Like click OFF,
최종 등록은 사용자 승인.

### live

모든 stabilization gate 통과 후.

---

## 32. 테스트가 원격에 없음

현재 `.gitignore`의 `test_*.py` 항목은 제거됐지만 Git tree에는 `tests/` directory가 없다.

따라서 과거 “17개 테스트 통과”는 다른 개발자/CI가 재현할 수 없다.

### 최소 tests

```text
tests/test_contracts.py
tests/test_config.py
tests/test_history.py
tests/test_comment_classifier.py
tests/test_comment_grammar.py
tests/test_comment_validator.py
tests/test_like_state.py
tests/test_like_eligibility.py
tests/test_comment_dom.py
tests/test_count_parser.py
```

---

## 33. CI

`.github/workflows/test.yml`

Gate:

```text
pyright
unit tests
python compileall
```

모두 통과해야 main merge.

---

## 34. Comment Regression Corpus

최소 100 fixtures.

필수 adversarial:

```text
근육빵빵 -> FOOD 아님
시장님 -> FINANCE 아님
책 -> BOOK_MOVIE
앱 -> IT_GADGET
펌 -> BEAUTY
맛집 -> subject 금지
후기 -> subject 금지
카페 디저트 -> CAFE/FOOD conflict
강아지 산책 -> PET
여행 산책 -> TRAVEL
```

---

## 35. Grammar Gate

0건:

```text
먹어보다 싶
마셔보다 싶
가보다 싶
참고해보다 싶
써보다 싶
```

---

## 36. Subject Gate

0건:

```text
맛집 맛있어
후기 먹어보고
메뉴 가보고
광양 맛있어
```

---

## 37. History Regression

시나리오:

```text
Run 1 -> comment SUBMITTED
Run 2 -> comment disabled / like only
Run 3 -> comment enabled
```

Run 3에서 중복 comment 생성 금지.

---

## 38. Like Regression

```text
NOT_LIKED high -> click -> LIKED high = success
NOT_LIKED high -> click -> NOT_LIKED = circuit open
UNKNOWN -> click 0
LIKED -> visitor query 0 / click 0
```

---

## 39. 개발 Workflow 변경

앞으로:

```text
1. 버그 재현 fixture/test 작성
2. test 실패 확인
3. 코드 수정
4. 해당 test pass
5. 전체 regression pass
6. observe mode 20~30 posts
7. draft_only 10 posts
8. live 3 posts
9. 문제 없으면 확대
```

금지:

```text
live에서 에러 하나 발견
→ 해당 줄만 수정
→ 바로 live 재실행
```

---

## 40. Stabilization 구현 순서

### Phase 0 — Feature Freeze
새 카테고리/새 UX 추가 중지.

### Phase 1 — Safety Boundary
- TargetPostGuard
- PerPostErrorBoundary
- Error taxonomy
- Like circuit breaker

### Phase 2 — Persistence
- Config update_many + atomic
- runtime config Git 분리
- History monotonic merge + atomic

### Phase 3 — Contract
- Resolver canonical API
- pyright
- constructor/interface smoke tests

### Phase 4 — Interaction Transactions
- Like transaction
- CommentEditorAdapter
- strong comment submit verification

### Phase 5 — Comment Core
- ActionForms
- META subject 분리
- EntityRole
- weighted classifier
- evidence validator
- deterministic ranking

### Phase 6 — DOM Diagnostics
- Like 5~10 real samples
- Visitor 5~10 real samples
- Comment variants

### Phase 7 — Regression/CI
- 100 comment fixtures
- DOM fixtures
- config/history tests
- CI

### Phase 8 — Gemini
- session preflight
- safe AppleScript quoting
- paste verify
- fresh response identity

### Phase 9 — Style Learning / UI Refiner
Base engine 안정화 후.

---

## 41. Release Gate

다음 모두 통과 전 “완료” 금지.

### Runtime
- recoverable post error가 session 종료시키지 않음
- navigation mismatch side effect 0
- stale UI command 적용 0

### Like
- high-confidence NOT_LIKED만 click
- unverified transition -> circuit open
- already liked -> visitor query/click 0
- 999 threshold test pass
- 10,000 visitor boundary pass

### Comment
- contenteditable/textarea read/write pass
- draft write-back verify
- submit strong verify
- mouse submit final text 보존
- invalid grammar 0
- META subject 0
- unsupported trait 0

### Persistence
- config partial update 안전
- runtime config Git 분리
- submitted history downgrade 0
- atomic save

### Engineering
- tests tracked
- CI green
- pyright green
- 100 comment corpus green
- observe 30 posts green
- draft_only 10 posts green
- live 3 posts green

---

## 42. 핵심 우선순위

```text
P0-1 Target correctness
P0-2 Per-post error isolation
P0-3 Like fail-closed + circuit breaker
P0-4 Config/History correctness
P0-5 API contracts + tests + pyright
P0-6 Comment grammar/entity correctness
P1 DOM diagnostics/transaction verification
P1 Regression corpus/CI
P2 Gemini
P2 Style learning/UX
```

가장 중요한 순서는:

```text
Target correctness
→ State correctness
→ Persistence correctness
→ DOM correctness
→ Comment quality
→ Gemini convenience
```

이다.

현재는 새 댓글 문구를 추가하는 것보다
잘못된 게시글에 interaction하는 가능성,
공감 toggle 오판,
중복 댓글,
History/config state corruption을 먼저 차단해야 한다.
