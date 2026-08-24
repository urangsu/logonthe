# NAVER FEED ASSISTANT — MASTER REFACTOR & IMPLEMENTATION SPEC

> **문서 성격:** 마스터 작업지시서 / 아키텍처 기준서 / 구현 순서표 / QA 기준서  
> **대상 저장소:** `urangsu/logonthe`  
> **기준 브랜치:** `main`  
> **기준일:** 2026-08-24  
> **대상 구현자:** Claude Code / Codex / Python 엔지니어  
> **최우선 목표:** 기존 PC 중심 네이버 블로그 자동화 프로그램을 **모바일 피드 기반 Human-in-the-loop Feed Assistant**로 전면 개편  
> **품질 우선순위:** 안정성 > 잘못된 클릭 방지 > 중복 방지 > 복구 가능성 > DOM 변경 내성 > UX > 속도 > 기능 수

---

# 0. 이 문서의 목적

이 문서는 기존 `SPEC.md`의 설계 방향을 유지하되, 현재 GitHub 저장소의 실제 소스코드를 직접 검토한 결과를 반영하여 작성한 **최종 마스터 작업지시서**다.

이 문서를 기준으로 구현자는 다음을 수행해야 한다.

1. 기존 구조를 단순 패치하지 않는다.
2. `AutoLiker`, `AutoCommenter` 중심 구조를 폐기한다.
3. `FeedController → FeedSource → FeedPost → PostProcessor` 구조로 재설계한다.
4. 모바일 `FeedList.naver`, `Recommendation.naver`, 직접 URL을 Source로 지원한다.
5. 공감/댓글 interaction을 공통 엔진으로 통합한다.
6. 댓글은 자동 제출하지 않는다.
7. 사용자가 초안을 수정하고 **Enter를 눌렀을 때만** 등록한다.
8. 댓글 등록 성공을 검증한 뒤에만 History에 성공 상태를 저장한다.
9. 공감 상태를 3상태(`LIKED`, `NOT_LIKED`, `UNKNOWN`)로 판별한다.
10. 상태를 모르면 절대 공감 버튼을 클릭하지 않는다.
11. 네이버 DOM selector의 Production Source of Truth를 하나로 통합한다.
12. hashed CSS module class는 fallback으로만 사용한다.
13. 기존 `dom_db.json`은 runtime selector DB가 아니라 **실측 DOM evidence**로 취급한다.
14. 기존의 “안티 디텍션 회피” 로직은 제거한다.
15. persistent BrowserContext 기반의 단일 작업 세션으로 만든다.
16. Recommendation은 `feed_page + detail_page` 구조를 기본으로 한다.
17. Infinite Scroll은 page 번호가 아니라 canonical post key 증가로 관리한다.
18. GUI는 “공감 기능 / 댓글 기능” 탭이 아니라 하나의 작업 흐름 중심 UI로 교체한다.
19. 모든 변경은 신규 refactor branch에서 단계적으로 진행한다.
20. 각 단계는 Acceptance Gate를 통과하기 전 다음 단계로 넘어가지 않는다.

---

# 1. 현재 저장소 상태 요약

현재 저장소에는 다음 주요 파일이 존재한다.

```text
.
├── .gitignore
├── README.md
├── SPEC.md
├── config.json
├── main.py
├── requirements.txt
├── data/
│   └── dom_db.json
└── src/
    ├── __init__.py
    ├── browser.py
    ├── collector.py
    ├── commenter.py
    ├── dom_registry.py
    ├── liker.py
    ├── logger.py
    ├── spintax.py
    └── types.py
```

현재 커밋 흐름은 다음 단계다.

```text
initial legacy bot commit
        ↓
DOM selector registry / dom_db 추가
```

즉, **새 설계를 위한 자료는 들어왔지만 실제 애플리케이션 구조는 아직 Legacy**다.

현재 상태를 다음과 같이 정의한다.

```text
설계 준비도        : 높음
실측 DOM 자료      : 중간 이상
모바일 구현        : 거의 미착수
아키텍처 migration : 미착수
GUI migration      : 미착수
안전한 상태판별    : 미완성
성공검증           : 없음
추천피드 처리      : 없음
```

---

# 2. 현재 코드 파일별 최종 판정

| 파일 | 판정 | 조치 |
|---|---|---|
| `main.py` | REWRITE | UI + orchestration 분리 |
| `src/browser.py` | REFACTOR | persistent/session 로직 활용, anti-detection 제거 |
| `src/liker.py` | REWRITE | global selector, unsafe toggle 제거 |
| `src/commenter.py` | REWRITE | PC iframe/textarea/auto-submit 폐기 |
| `src/collector.py` | DELETE/EXTRACT | URL parser만 `url_utils.py`로 이동 |
| `src/types.py` | REPLACE | 상태 모델 대폭 확장 |
| `src/spintax.py` | KEEP | DraftService에서 호출 |
| `src/logger.py` | KEEP + EXTEND | structured event logging 추가 |
| `src/dom_registry.py` | REPLACE | runtime resolver 구조로 변경 |
| `data/dom_db.json` | KEEP AS EVIDENCE | 실측 DOM 증거 DB |
| `config.json` | MIGRATE | schema v2 |
| `README.md` | LATER REWRITE | migration 완료 후 변경 |
| `SPEC.md` | KEEP / SUPERSEDED | 본 문서를 상위 기준서로 사용 |
| `.gitignore` | KEEP + ADD | diagnostics output ignore |
| `requirements.txt` | CLEANUP | dev dependency 분리 검토 |

---

# 3. 현재 코드에서 확인된 핵심 구조 문제

## 3.1 `main.py` 책임 과다

현재 `main.py`는 다음을 모두 담당한다.

- CustomTkinter root window
- 탭 생성
- 입력 위젯
- config load
- 로그인 브라우저
- CDP chrome 실행
- profile lock
- 공감 시작
- 댓글 시작
- worker thread
- stop
- logger callback
- 숫자 validation
- UI state toggle

이 구조는 신규 기능 추가 시 `main.py`가 계속 커지는 구조다.

### 목표

최종 `main.py`는 최대한 아래 수준으로 축소한다.

```python
from ui.main_window import MainWindow

def main():
    app = MainWindow()
    app.mainloop()

if __name__ == "__main__":
    main()
```

업무 로직은 `app/`, 네이버 DOM은 `naver/`, Browser lifecycle은 `browser/`, 설정과 History는 `services/`로 분리한다.

---

## 3.2 공감/댓글 기능이 사용자 workflow가 아니라 기능 단위로 분리됨

현재:

```text
1번 기능: 공감
2번 기능: 댓글
```

새 구조:

```text
게시글 발견
 ↓
공감 여부 확인
 ↓
필요 시 공감
 ↓
댓글 열기
 ↓
초안 입력
 ↓
사용자 수정
 ↓
Enter 승인
 ↓
성공 검증
 ↓
History 저장
 ↓
다음 게시글
```

따라서 Top-level `AutoLiker`, `AutoCommenter` 개념을 제거한다.

---

# 4. 기존 `liker.py` 상세 문제

현재 `liker.py`는 여러 selector를 전역적으로 수집하고 `ElementHandle` 배열을 만들고 Y좌표를 이용해 중복 제거한다.

이 구조의 문제:

1. 게시글 card scope가 없다.
2. 같은 카드의 여러 selector가 같은 버튼을 중복 수집할 수 있다.
3. Y좌표 중복 제거는 DOM identity가 아니다.
4. virtualized feed에서 Y좌표가 바뀔 수 있다.
5. scroll 이후 detached handle 가능성이 있다.
6. `query_selector_all` + `ElementHandle` 방식은 Locator보다 복원성이 낮다.
7. click 실패 시 `force=True`를 사용한다.
8. 그래도 실패하면 `dispatch_event("click")`까지 실행한다.
9. 공감 상태 판별 실패와 미공감 상태가 동일하게 `False`로 처리된다.
10. 상태 UNKNOWN에서 클릭이 발생할 수 있다.

### 가장 위험한 문제

현재 의미상:

```text
미공감 → False
판별 실패 → False
예외 → False
UNKNOWN → False
```

그리고 `False`면 click.

이는 잘못된 상태 판독 시 이미 누른 공감을 취소할 수 있는 구조다.

### 새 정책

```python
class LikeState(Enum):
    LIKED = "liked"
    NOT_LIKED = "not_liked"
    UNKNOWN = "unknown"
```

정책:

```text
LIKED       → 아무 것도 하지 않음
NOT_LIKED   → click
UNKNOWN     → 절대 click 금지
```

---

# 5. 기존 `commenter.py` 상세 문제

현재 댓글 엔진은 다음 흐름이다.

```text
입력 URL
 ↓
normalize_blog_post_url()
 ↓
PC PostView URL
 ↓
mainFrame 탐색
 ↓
댓글 영역 탐색
 ↓
textarea 탐색
 ↓
댓글 fill
 ↓
submit button click
 ↓
random sleep
 ↓
"댓글 등록 완료" 로그
 ↓
history 저장
```

문제:

1. 모바일 `contenteditable div` 구조와 맞지 않는다.
2. PC PostView URL로 변환한다.
3. iframe fallback이 중심이다.
4. 댓글 자동 submit.
5. 사용자 수정/승인 workflow 없음.
6. 실제 submit 성공 검증 없음.
7. submit click 자체를 성공으로 처리.
8. 중복 재등록 방지 수준이 단순 URL set.
9. 사용자가 수정한 최종 댓글을 저장할 수 없음.
10. 댓글 disabled / login required / submit failed 상태 모델이 빈약함.

### 새 정책

```text
댓글창 열기
 ↓
contenteditable editor 확인
 ↓
draft fill
 ↓
사용자 수정 대기
 ↓
Enter
 ↓
final text read
 ↓
submit click
 ↓
success verification
 ↓
History SUBMITTED
```

---

# 6. 기존 `browser.py` 상세 판정

현재 BrowserManager에서 살릴 부분:

- persistent user profile
- profile lock
- sync_playwright lifecycle
- CDP optional support
- context close 관리
- interruptible_wait
- stop_event 사용

제거하거나 변경할 부분:

```text
--disable-blink-features=AutomationControlled
navigator.webdriver override
낡은 고정 Chrome 122 user-agent
1280x850 desktop viewport
find_or_create_page()의 임의 마지막 탭 재사용
Worker별 browser 생명주기 전제
```

### 새로운 BrowserSession 목표

```text
BrowserSession
 ├─ context
 ├─ feed_page
 └─ detail_page
```

BrowserContext는 하나.

---

# 7. Anti-detection 관련 레거시 제거

다음 목적의 코드는 삭제한다.

```text
AutomationControlled 제거
webdriver property override
안티 디텍션 문구
회피 목적 랜덤 delay
```

이 프로그램의 목적은:

```text
대량 자동 행위
```

가 아니라:

```text
Human-in-the-loop 반복 작업 보조
```

다.

Delay는 탐지 회피가 아니라 다음 용도만 허용.

- 실제 UI animation wait
- DOM transition
- API/렌더링 race 방지
- 사용자 입력 polling
- retry backoff

가능한 경우 fixed sleep 대신 Playwright condition wait를 사용한다.

---

# 8. DOM Source of Truth 정책

현재:

```text
src/dom_registry.py
data/dom_db.json
src/liker.py selectors
src/commenter.py selectors
```

여러 군데에 DOM 정보가 중복되어 있다.

이 구조를 유지하지 않는다.

---

## 8.1 Production Source of Truth

Production runtime selector/판정 로직:

```text
naver/resolver.py
```

또는:

```text
naver/selectors.py
naver/resolver.py
```

딱 한 곳.

---

## 8.2 `dom_db.json` 역할

`data/dom_db.json`은 runtime selector config로 사용하지 않는다.

역할:

```text
실측한 DOM evidence
DOM snapshot metadata
이전/현재 selector 비교
regression 분석
```

즉 “관찰 데이터”다.

---

## 8.3 `dom_registry.py`

현재 파일은 hashed class를 primary로 쓰는 부분이 있다.

예:

```text
card_wrapper__F0VEP
link__XWBJA
Interact__comment_btn--Wbuoq
```

이 값들은 Production Primary가 될 수 없다.

따라서 `dom_registry.py`는 새 resolver 구현이 완료되면 폐기하거나 최소 정보 only registry로 축소한다.

---

# 9. Selector 우선순위

무조건 다음 우선순위를 따른다.

1. Accessible role + name
2. Stable id
3. Stable data attribute
4. Stable semantic href
5. Stable form/action attribute
6. Accessible text / blind text
7. structural relationship
8. class prefix
9. current hashed CSS class

예: 댓글 버튼

```python
page.get_by_role("button", name="댓글")
```

fallback:

```python
page.locator("button[data-click-area='pst.re']")
```

fallback:

```python
blind = page.locator("span.blind").filter(has_text="댓글")
blind.locator("xpath=ancestor::button[1]")
```

최종 fallback:

```python
page.locator("button[class*='Interact__comment_btn']")
```

---

# 10. 현재 확보된 모바일 DOM

## 10.1 FeedList

```text
https://m.blog.naver.com/FeedList.naver
```

관찰:

```css
li.card_wrapper__F0VEP
li[class*='card_wrapper__']
```

post link:

```css
a.link__XWBJA
```

Production primary는 별도 resolver에서 stable signal로 재정의.

---

## 10.2 Recommendation

```text
https://m.blog.naver.com/Recommendation.naver
```

추천 화면은 Discovery Source로 사용.

추천 카드에서 post URL을 추출하고 실제 공감/댓글은 detail page에서 공통 처리한다.

---

## 10.3 모바일 댓글 버튼

관찰:

```html
<button
    type="button"
    class="Interact__comment_btn--Wbuoq"
    data-click-area="pst.re">

    <span class="Interact__icon--Sn7xy">
        <span class="blind">댓글</span>
    </span>
</button>
```

사용 우선순위:

```text
role/name
data-click-area
blind text ancestor
class prefix
hash class
```

---

## 10.4 모바일 댓글 editor

```html
<div
    title="댓글"
    id="naverComment__write_textarea"
    class="u_cbox_text u_cbox_text_mention"
    contenteditable="true"
    data-area-code="RPC.input">
</div>
```

입력:

```python
editor.fill(draft)
editor.focus()
```

금지:

```text
innerText 직접 삽입
textContent 직접 삽입
custom input event
execCommand
```

---

## 10.5 Placeholder

```html
<div class="u_cbox_guide">
    댓글을 입력해주세요.
</div>
```

입력창이 아님.

---

## 10.6 등록 버튼

```css
button.u_cbox_btn_upload
```

보조:

```css
button[data-action='write#request']
```

---

# 11. Like State 판별 설계

이 기능은 P0.

## 11.1 상태 모델

```python
class LikeState(Enum):
    LIKED = "liked"
    NOT_LIKED = "not_liked"
    UNKNOWN = "unknown"
```

---

## 11.2 판정 후보

다음 값을 조사한다.

- `aria-pressed`
- button class
- icon class
- reaction class
- blind text
- title
- data attributes
- accent state
- count element style

한 신호만으로 결정하지 말고 우선순위를 정의한다.

예:

```text
aria-pressed=true       → LIKED
aria-pressed=false      → NOT_LIKED
stable active marker    → LIKED
stable inactive marker  → NOT_LIKED
reliable icon signature → state
그 외                   → UNKNOWN
```

---

## 11.3 UNKNOWN 정책

UNKNOWN이면:

```text
click 금지
warning
history optional
다음 글로 이동
```

절대:

```text
모르니까 일단 클릭
```

하지 않는다.

---

# 12. 댓글 성공 검증 설계

현재 코드의 가장 큰 결함 중 하나.

`submit_btn.click()`은 성공이 아니다.

---

## 12.1 Submission 상태

```python
class CommentSubmitState(Enum):
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNKNOWN = "unknown"
```

---

## 12.2 성공 신호 후보

실측 DOM에서 다음을 확인한다.

1. editor empty
2. comment count 증가
3. 새 comment wrapper 생성
4. 본인 작성 comment text 등장
5. submit state reset
6. toast / notice
7. network response 성공

권장 정책:

```text
Primary DOM signal
+
Secondary supporting signal
```

예:

```text
editor empty
AND
new comment visible
```

또는:

```text
editor empty
AND
comment count increment
```

---

## 12.3 UNKNOWN 정책

등록 버튼을 눌렀지만 성공 여부 확인 불가:

```text
UNKNOWN
```

History:

```text
comment.status = unknown
```

자동 재등록 금지.

중복 댓글 위험 때문.

---

# 13. 댓글 사용자 승인 UX

기본:

```text
Enter       등록
Shift+Enter 줄바꿈
Esc         건너뛰기
```

등록은 **사용자의 명시적 승인**.

---

## 13.1 Keyboard Listener

Document-level capture listener.

단, 댓글 editor에 focus가 있을 때만 Enter를 가로챈다.

```javascript
const editor = e.target.closest?.(
    "#naverComment__write_textarea"
);

if (!editor) return;
```

---

## 13.2 Python polling

Promise blocking 금지.

```python
while True:
    if stop_event.is_set():
        return UserAction.STOP

    action = page.evaluate(
        "() => window.__NAVER_FEED_ACTION__"
    )

    if action:
        ...
```

polling interval:

```text
0.05 ~ 0.15 sec
```

기본 0.1 sec.

---

# 14. 최종 댓글 저장

사용자는 draft를 수정할 수 있다.

따라서:

```text
draft
```

와:

```text
submitted_text
```

를 구분한다.

등록 직전:

```python
final_text = editor.inner_text().strip()
```

History:

```json
{
  "draft": "...",
  "submitted_text": "...",
  "status": "submitted"
}
```

---

# 15. 새로운 전체 아키텍처

```text
┌─────────────────────────────────────┐
│                 GUI                 │
└──────────────────┬──────────────────┘
                   │
                   ▼
             FeedController
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
     Neighbor   Recommend  Direct
      Source      Source   Source
          └────────┼────────┘
                   ▼
                FeedPost
                   │
                   ▼
             PostProcessor
             │            │
             ▼            ▼
        LikeService   CommentService
                          │
                          ▼
                     DraftService
                          │
                          ▼
                      UserAction
                          │
                          ▼
                     HistoryStore
```

---

# 16. 권장 폴더 구조 — 현실적인 V1

기존 SPEC보다 초기 파일 수를 줄인다.

```text
logonthe/
│
├── main.py
├── config.json
├── requirements.txt
├── README.md
├── SPEC.md
├── MASTER_SPEC.md
│
├── app/
│   ├── __init__.py
│   ├── controller.py
│   ├── models.py
│   └── state.py
│
├── browser/
│   ├── __init__.py
│   └── session.py
│
├── naver/
│   ├── __init__.py
│   ├── resolver.py
│   ├── interaction.py
│   ├── url_utils.py
│   └── sources.py
│
├── services/
│   ├── __init__.py
│   ├── config.py
│   ├── draft.py
│   └── history.py
│
├── ui/
│   ├── __init__.py
│   └── main_window.py
│
├── diagnostics/
│   ├── inspect_feed.py
│   ├── inspect_post.py
│   ├── inspect_recommendation.py
│   └── dump_state.py
│
├── tests/
│   ├── fixtures/
│   └── ...
│
├── data/
│   ├── dom_db.json
│   ├── history.json
│   └── user_profile/
│
└── legacy/
    └── optional migration archive
```

커지면 이후:

```text
naver/sources/
ui/panels/
```

로 분할.

---

# 17. Core Model

## 17.1 FeedSourceType

```python
class FeedSourceType(str, Enum):
    NEIGHBOR = "neighbor"
    RECOMMENDATION = "recommendation"
    DIRECT = "direct"
```

---

## 17.2 FeedPost

```python
@dataclass
class FeedPost:
    key: str
    source: FeedSourceType
    url: str

    blog_id: str | None = None
    log_no: str | None = None
    title: str | None = None
    author: str | None = None
```

### Locator 저장 금지

FeedPost는 stable data only.

이유:

- infinite scroll detach
- virtualization
- DOM rerender
- detail page separate

---

# 18. URL Canonicalization

기존 collector에서 URL parsing 아이디어만 추출한다.

새 `naver/url_utils.py`:

```python
parse_blog_post_url()
canonicalize_post_url()
build_post_key()
```

지원 입력:

```text
https://m.blog.naver.com/foo/123
https://blog.naver.com/foo/123
https://blog.naver.com/PostView.naver?blogId=foo&logNo=123
```

출력:

```text
blog_id=foo
log_no=123
key=foo:123
canonical_mobile_url=https://m.blog.naver.com/foo/123
```

tracking query 제거.

---

# 19. FeedSource Interface

```python
class FeedSource(Protocol):

    def open(self) -> None:
        ...

    def discover_posts(self) -> list[FeedPost]:
        ...

    def load_more(self) -> bool:
        ...

    def exhausted(self) -> bool:
        ...
```

Source는 interaction을 하지 않는다.

책임:

```text
어떤 글을 처리할 것인가
```

---

# 20. NeighborFeedSource

URL:

```text
https://m.blog.naver.com/FeedList.naver
```

책임:

- feed open
- card discovery
- URL extraction
- title/author optional extraction
- post key canonicalization
- duplicate skip
- scroll
- feed exhaustion

공감/댓글 책임 없음.

---

# 21. RecommendationFeedSource

URL:

```text
https://m.blog.naver.com/Recommendation.naver
```

역할:

```text
Discovery source
```

추천 카드 자체에서 interaction하지 않는 것을 기본.

흐름:

```text
Recommendation page
 ↓
card discovery
 ↓
post URL extraction
 ↓
FeedPost
 ↓
detail_page.goto(url)
 ↓
PostProcessor
```

---

# 22. DirectUrlSource

사용자가 직접 여러 URL을 입력하는 경우.

입력:

```text
URL 1
URL 2
URL 3
```

각 URL canonicalize.

중복 제거.

잘못된 URL은 warning 후 skip.

---

# 23. BrowserSession

## 23.1 목표

```python
class BrowserSession:

    context
    feed_page
    detail_page
```

---

## 23.2 Context

프로그램 작업 시작 시 1회 생성.

작업 종료 시 1회 종료.

공감/댓글마다 생성 금지.

---

## 23.3 Recommendation

```text
feed_page
  Recommendation
  scroll state 유지

detail_page
  실제 post
```

`go_back()` 반복 금지.

---

## 23.4 Neighbor

두 전략 중 실측 결과에 따라 선택.

### A

```text
feed_page card → detail_page
```

### B

```text
feed_page 내부 interaction
```

기본은 A를 우선 검토.

이유:

- interaction 공통화
- 댓글 DOM 통일
- card UI 변형 영향 최소화
- FeedList/Recommendation 동일 PostProcessor

즉 가능하면 이웃 피드도 URL discovery만 하고 detail page에서 interaction하는 구조가 가장 단순하고 안정적.

---

# 24. 모바일 Context

현재 desktop:

```text
1280 x 850
```

새 V1 권장:

```python
viewport={"width": 430, "height": 900}
locale="ko-KR"
timezone_id="Asia/Seoul"
```

`is_mobile`, `has_touch`는 persistent context 실제 지원/동작을 검증한 후 추가.

수동 UA override는 우선 제거.

---

# 25. Page Ownership

기존:

```python
find_or_create_page()
```

처럼 마지막 page 임의 재사용 금지.

명시적으로:

```python
session.feed_page
session.detail_page
```

관리.

로그인 page는 별도 lifecycle.

---

# 26. CDP 정책

V1 기본:

```text
Persistent browser
```

CDP:

```text
Advanced / Legacy
```

로 강등.

CDP attach browser를 앱이 close했을 때 실제 Chrome lifecycle이 어떻게 되는지 검증되지 않았다면 default로 사용 금지.

---

# 27. State Machine

```python
class FeedState(Enum):
    IDLE = auto()

    STARTING_BROWSER = auto()
    OPENING_SOURCE = auto()
    DISCOVERING = auto()

    OPENING_POST = auto()

    CHECKING_LIKE = auto()
    LIKING = auto()

    OPENING_COMMENT = auto()
    FILLING_DRAFT = auto()
    WAITING_USER = auto()

    SUBMITTING = auto()
    VERIFYING = auto()

    RECORDING = auto()
    SKIPPING = auto()

    LOADING_MORE = auto()

    STOPPING = auto()
    STOPPED = auto()
    COMPLETED = auto()
    ERROR = auto()
```

GUI는 이 State를 표시.

---

# 28. UserAction

```python
class UserAction(Enum):
    SUBMIT = auto()
    SKIP = auto()
    STOP = auto()
```

향후:

```text
PAUSE
RETRY
OPEN_CURRENT_POST
```

추가 가능.

---

# 29. FailureReason

```python
class FailureReason(Enum):
    LOGIN_REQUIRED = auto()
    NAVIGATION_FAILED = auto()

    POST_URL_INVALID = auto()
    POST_UNAVAILABLE = auto()

    LIKE_BUTTON_NOT_FOUND = auto()
    LIKE_STATE_UNKNOWN = auto()

    COMMENT_DISABLED = auto()
    COMMENT_BUTTON_NOT_FOUND = auto()
    COMMENT_EDITOR_NOT_FOUND = auto()
    COMMENT_SUBMIT_NOT_FOUND = auto()
    COMMENT_SUBMIT_UNVERIFIED = auto()

    BROWSER_DISCONNECTED = auto()
```

---

# 30. PostProcessor

목표 코드 가독성:

```python
class PostProcessor:

    def process(self, page, post):
        if self.settings.like_enabled:
            self.like_service.process(page, post)

        if self.settings.comment_enabled:
            result = self.comment_service.prepare(page, post)

            if not result.ready:
                return result

            action = self.user_action_service.wait(page)

            if action == UserAction.SKIP:
                return self.history.mark_skipped(post)

            if action == UserAction.STOP:
                raise StopRequested()

            final_text = self.comment_service.read_final_text(page)

            self.comment_service.submit(page)

            verified = self.comment_service.verify(page, final_text)

            return self.history.record_comment(
                post,
                final_text,
                verified
            )
```

---

# 31. LikeService

책임:

```text
button resolve
state resolve
safe click
post-click state verify
```

---

## 31.1 Like click 후 검증

`click()` 성공만으로 신규 공감 성공이라 하지 않는다.

가능하면 클릭 후:

```text
NOT_LIKED → LIKED
```

상태 전환을 검증.

전환 확인 실패:

```text
UNKNOWN
```

History / log에 기록.

---

# 32. CommentService

책임:

```text
comment button resolve
open
editor resolve
draft fill
final text read
submit
verification
```

사용자 키보드 대기 자체는 UserActionService로 분리 가능.

---

# 33. DraftService

기존 `spintax.py` 사용.

```python
class DraftService:

    def generate(self, template, suffix):
        body = parse_spintax(template).strip()

        if not suffix.strip():
            return body

        return f"{body}\n{suffix.strip()}"
```

향후 AI 댓글 생성도 같은 Interface로 교체 가능.

---

# 34. Config schema v2

```json
{
  "schema_version": 2,

  "feed_source": "neighbor",
  "max_feed_items": 20,

  "like_enabled": true,
  "comment_enabled": true,

  "comment_template": "{좋은|유익한|멋진} 포스팅 잘 읽었습니다!",
  "fixed_suffix": "오늘도 좋은 하루 보내세요 :)",

  "secret_comment": false,

  "browser_mode": "persistent"
}
```

Legacy 제거:

```text
max_pages
default_keywords
anti-detection delay 중심 옵션
```

---

# 35. Config Migration

기존 config가 있을 수 있다.

```python
def migrate_config(data: dict) -> dict:
    version = data.get("schema_version", 1)

    if version == 1:
        ...
```

migration 후:

```text
schema_version=2
```

저장.

---

# 36. History schema v2

단순 URL set 폐기.

```json
{
  "schema_version": 2,
  "posts": {
    "foo:123": {
      "source": "neighbor",
      "url": "https://m.blog.naver.com/foo/123",
      "title": "...",
      "author": "...",

      "like": {
        "state_before": "not_liked",
        "action": "clicked",
        "state_after": "liked",
        "timestamp": "..."
      },

      "comment": {
        "status": "submitted",
        "draft": "...",
        "submitted_text": "...",
        "timestamp": "..."
      }
    }
  }
}
```

---

# 37. History 상태

Comment:

```text
NONE
DRAFTED
SKIPPED
SUBMITTED
FAILED
UNKNOWN
```

Like:

```text
NOT_PROCESSED
ALREADY_LIKED
LIKED
UNKNOWN
FAILED
```

---

# 38. Duplicate 정책

canonical post key 기준.

```text
blogId:logNo
```

URL raw string 기준으로 중복 판정하지 않는다.

---

# 39. Infinite Scroll

`max_pages` 삭제.

`max_feed_items` 사용.

---

## 39.1 Discovery tracking

```python
seen_keys: set[str]
queued_keys: set[str]
processed_keys: set[str]
```

---

## 39.2 load_more 성공

단순 scrollHeight만으로 판단하지 않는다.

우선:

```text
canonical post key set 증가
```

를 사용.

---

## 39.3 End of feed

예:

```text
3회 연속 새로운 key 없음
→ exhausted
```

---

# 40. Recommendation scroll 유지

Recommendation에서는:

```text
go_back
```

을 기본으로 쓰지 않는다.

`feed_page`는 그대로.

`detail_page`만 다음 URL로 navigation.

---

# 41. UI 최종 방향

기존 탭:

```text
공감
댓글
세션
```

폐기.

---

## 41.1 Setup Screen

```text
Naver Feed Assistant

작업 소스
● 이웃 피드
○ 추천 피드
○ 직접 URL

작업
☑ 공감
☑ 댓글 초안

최대 처리 글
[ 20 ]

댓글 초안
[ {좋은|유익한|멋진} 포스팅 잘 읽었습니다! ]

고정 끝말
[ 오늘도 좋은 하루 보내세요 :) ]

등록 방식
● 브라우저에서 수정 후 Enter

Enter       등록
Shift+Enter 줄바꿈
Esc         건너뛰기

[ 작업 시작 ]
```

---

## 41.2 Running Screen

```text
작업 중

소스
추천 피드

진행
7 / 20

현재 글
순천 조례호수공원 산책...

공감
✓ 완료

댓글
● 사용자 확인 대기

Enter 등록
Esc 건너뛰기

[ 중지 ]
```

---

# 42. UI와 Worker 분리

Tkinter main thread:

```text
UI only
```

Worker:

```text
FeedController
Playwright
```

Thread-safe UI update:

```python
root.after(...)
```

event / callback / queue 방식.

---

# 43. Worker 개수

기본:

```text
1
```

공감/댓글 병렬 worker 금지.

Browser lifecycle도 worker 하나에 귀속.

---

# 44. Stop Behavior

`stop_event` 체크 위치:

- discovery loop
- post navigation
- like resolve
- like click 전
- comment open
- fill 전/후
- user polling
- scroll wait
- source load_more
- verification loop

---

## 44.1 Submit 직후 stop

submit click 이후 stop이 들어오면:

```text
verification까지 수행
↓
history 기록
↓
종료
```

중간 불명확 종료 방지.

---

# 45. Pause

V1 필수 아님.

먼저 Stop 안정화.

---

# 46. Login detection

작업 시작 전 probe.

로그인 풀림:

```text
자동 우회 금지
작업 중단
GUI 안내
```

---

# 47. Comment disabled

댓글 버튼이 없는 것과 DOM 오류를 구분.

가능하면:

```text
COMMENT_DISABLED
```

상태 marker를 실측.

---

# 48. Unavailable post

지원해야 할 상태:

```text
삭제
비공개
이웃공개
로그인 필요
접근제한
```

각각 stable marker 실측 후 FailureReason mapping.

---

# 49. Diagnostics

기존 임시 inspect script를 삭제하지 않는다.

정식 도구로 유지.

---

## 49.1 `diagnostics/inspect_feed.py`

출력:

- URL
- card 후보 개수
- card outerHTML 일부
- href
- title
- author
- data attrs
- roles
- unique post keys
- scrollHeight
- card count

---

## 49.2 `diagnostics/inspect_post.py`

출력:

- like button outerHTML
- like state attributes
- comment button outerHTML
- editor outerHTML
- submit outerHTML
- comment count
- before/after state

---

## 49.3 `inspect_recommendation.py`

출력:

- card wrapper 후보
- href 목록
- unique post key
- category button
- selected category
- scroll container
- loader/sentinel

---

# 50. Diagnostics output 정책

console only 금지.

JSON 파일 저장.

```json
{
  "timestamp": "...",
  "url": "...",
  "page_type": "post",
  "like": {...},
  "comment": {...}
}
```

---

# 51. 민감정보 보호

DOM dump에 다음이 포함될 수 있다.

- 닉네임
- 댓글
- 개인 블로그
- profile URL

따라서:

```text
diagnostics/output/
data/dom_dumps/
```

`.gitignore` 추가.

---

# 52. Tests

최소:

```text
pytest
```

도입.

`requirements-dev.txt` 검토.

---

## 52.1 Fixture

```text
tests/fixtures/
 ├─ feed_neighbor_card.html
 ├─ feed_recommendation_card.html
 ├─ post_unliked.html
 ├─ post_liked.html
 ├─ comment_editor.html
 ├─ comment_submitted.html
 ├─ comment_disabled.html
 └─ post_unavailable.html
```

---

## 52.2 URL tests

```python
test_mobile_url()
test_pc_short_url()
test_postview_url()
test_tracking_query_removed()
test_invalid_url()
```

---

## 52.3 Resolver tests

```text
find comment button
find editor
find submit
like state liked
like state not liked
like state unknown
```

---

# 53. Logging

현재 logger는 유지.

추가:

```python
logger.event(...)
```

형태 검토.

---

## 53.1 이벤트 예

```text
[SESSION]
[SOURCE]
[DISCOVER]
[NAVIGATE]
[LIKE]
[COMMENT]
[WAIT_USER]
[SUBMIT]
[VERIFY]
[HISTORY]
[ERROR]
```

---

# 54. Debug information

Post error 발생 시 최소 기록:

```text
post key
post url
state
failure reason
selector attempts
current page url
```

DOM full dump는 optional debug mode.

---

# 55. Production Safety Rules

절대 금지:

1. 좌표 기반 click.
2. LikeState UNKNOWN에서 click.
3. CommentSubmit UNKNOWN에서 자동 재등록.
4. submit 결과 검증 전 History success.
5. 댓글 editor 미확인 상태에서 다른 textbox에 fallback input.
6. hash class 하나만 믿는 primary selector.
7. infinite scroll 무한 loop.
8. stop_event 무시.
9. 사용자의 Enter 없이 댓글 자동 등록.
10. click 실패했다고 무조건 force click.
11. click 실패했다고 dispatch_event로 강제 성공 처리.
12. login UI를 자동 우회.
13. detached ElementHandle 장기간 보관.
14. DOM position을 post identity로 사용.
15. raw URL을 유일한 duplicate key로 사용.

---

# 56. Performance Rules

우선:

```text
wait_for
expect
URL change
state change
new key discovered
```

고정 sleep 최소화.

사용자 polling만 짧은 interval.

---

# 57. Error recovery

Post 단위 오류는 가능하면 session 전체를 죽이지 않는다.

```python
try:
    process(post)
except RecoverablePostError:
    history...
    continue
```

---

# 58. Session abort condition

다음은 session stop:

```text
browser disconnected
login lost
source 구조 반복 실패
persistent profile failure
fatal Playwright error
```

---

# 59. Branch Strategy

현재 `main` 직접 대수술 금지.

생성:

```text
refactor/mobile-feed-assistant
```

모든 migration은 여기서 수행.

---

# 60. Backup

작업 전:

```text
현재 main commit SHA 기록
tag 생성 권장
```

예:

```text
legacy-pre-mobile-refactor
```

---

# 61. Commit Strategy

권장 commit:

```text
1. chore: snapshot legacy bot before mobile refactor
2. refactor: extract browser session lifecycle
3. feat: add feed domain models and state enums
4. feat: add canonical naver post url parser
5. feat: add resilient mobile DOM resolver
6. feat: add neighbor feed discovery source
7. test: add neighbor feed fixture coverage
8. feat: add safe like state resolver
9. test: add like state regression fixtures
10. feat: add comment editor draft workflow
11. feat: add enter escape user approval handling
12. feat: add comment submission verification
13. feat: add structured history store
14. feat: add recommendation discovery source
15. feat: add feed and detail page browser strategy
16. refactor: replace legacy workers with FeedController
17. refactor: replace legacy GUI with Feed Assistant UI
18. chore: migrate config schema v2
19. test: add end-to-end smoke diagnostics
20. docs: rewrite README for Feed Assistant
```

---

# 62. Migration Phases

# Phase 0 — Freeze & Evidence

해야 할 것:

- legacy tag
- current file backup
- current screenshots
- current working commands
- current config sample
- DOM evidence 확보

코드 기능 변경 없음.

### Gate

```text
legacy 재현 가능
```

---

# Phase 1 — Foundation

구현:

- app/models.py
- app/state.py
- url_utils.py
- config migration
- History v2 skeleton

Legacy UI 유지 가능.

### Gate

```text
unit tests pass
legacy runtime unaffected
```

---

# Phase 2 — BrowserSession

구현:

```text
Persistent BrowserContext
feed_page
detail_page
```

anti-detection 제거.

### Gate

```text
로그인 profile 유지
feed/detail 두 page 안정적으로 유지
Stop 후 clean close
```

---

# Phase 3 — Neighbor Discovery

공감/댓글 금지.

오직:

```text
FeedList open
20개 canonical FeedPost 수집
중복 제거
scroll
exhaustion
```

### Gate

- duplicate 없음
- canonical key 생성
- detached locator 의존 없음
- max item 준수

---

# Phase 4 — Like State

아직 click하지 않고 먼저 read-only diagnostic.

```text
LIKED
NOT_LIKED
UNKNOWN
```

판별.

### Gate

사용자가 수동 확인한 샘플과 일치.

---

# Phase 5 — Safe Like

`NOT_LIKED`만 클릭.

클릭 후 state transition 검증.

### Gate

- already-liked 취소 0건
- unknown click 0건
- 상태전환 검증

---

# Phase 6 — Comment Draft Only

자동 submit 금지.

```text
comment open
editor find
fill draft
focus
```

### Gate

다양한 post에서 초안 입력 성공.

---

# Phase 7 — User Approval

```text
Enter
Shift+Enter
Esc
Stop
```

### Gate

- Enter only editor focused
- Shift+Enter newline
- Esc skip
- Stop 즉시 반응

---

# Phase 8 — Submit Verification

등록 성공 signal 확정.

### Gate

```text
실제 성공만 SUBMITTED
불명확 → UNKNOWN
자동 재등록 없음
```

---

# Phase 9 — History v2

draft/final text/like state 저장.

### Gate

재실행 후 중복 처리 없음.

---

# Phase 10 — Recommendation Discovery

오직 URL discovery부터.

### Gate

추천 카드 여러 형태에서 canonical post URL 안정 추출.

---

# Phase 11 — Recommendation Interaction

`detail_page`에서 기존 공통 PostProcessor 사용.

### Gate

Neighbor와 동일 PostProcessor 코드 사용.

별도 Recommendation commenter 금지.

---

# Phase 12 — Controller

Legacy two-worker 제거.

```text
FeedController
```

통합.

### Gate

Neighbor / Recommendation / Direct 동일 controller path.

---

# Phase 13 — GUI

Legacy tab 제거.

Feed Assistant UI.

### Gate

기능이 GUI에서 하나의 workflow로 보임.

---

# Phase 14 — Legacy Cleanup

삭제:

```text
AutoLiker
AutoCommenter
BlogCollector old path
legacy selector arrays
PC pagination worker
```

필요하면 `legacy/` temporary archive 후 제거.

---

# Phase 15 — Documentation

README 재작성.

SPEC hierarchy:

```text
MASTER_SPEC.md = 최상위
SPEC.md = 과거 설계 참고 또는 archived
README = 사용자용
```

---

# 63. 각 파일에 대한 구체적 작업

## 63.1 `main.py`

REWRITE.

남길 것:

```text
앱 진입점
```

삭제:

- Bot GUI full class
- workers
- BrowserManager direct usage
- liker/commenter imports
- legacy tabs

---

## 63.2 `src/browser.py`

REFACTOR 후 새 위치:

```text
browser/session.py
```

살릴 것:

- ProfileLockManager
- USER_DATA_DIR
- persistent context
- lifecycle
- interruptible wait

삭제:

- webdriver override
- AutomationControlled
- old user-agent
- arbitrary page reuse

---

## 63.3 `src/liker.py`

REWRITE.

새 위치 후보:

```text
naver/interaction.py
```

또는:

```text
services/like.py
```

global scan 제거.

---

## 63.4 `src/commenter.py`

REWRITE.

새 공통 interaction engine.

PC iframe logic는 legacy fallback이 필요하다고 명시될 때만 별도 유지.

기본 모바일 V1에서는 사용 안 함.

---

## 63.5 `collector.py`

URL parser만 추출.

나머지 keyword search는 V1 core에서 제거.

향후 필요하면 SearchSource로 다시 추가.

---

## 63.6 `dom_registry.py`

현재 구조 유지 금지.

runtime resolver로 교체.

---

## 63.7 `dom_db.json`

유지.

단:

```text
runtime import 금지
```

evidence/versioned snapshots.

---

## 63.8 `types.py`

Replace.

models/state에 분산.

---

## 63.9 `spintax.py`

Keep.

테스트 추가.

---

## 63.10 `logger.py`

Keep.

Structured fields 추가.

---

# 64. Recommendation Category

V1 자동 선택 필수 아님.

권장:

```text
사용자가 브라우저에서 카테고리 선택
↓
현재 Recommendation feed 처리
```

V2 자동화.

---

# 65. Recommendation Category V2

DOM 확보 후:

```text
button role
text
selected state
data attribute
```

기반으로 구현.

hash class 기반 금지.

---

# 66. DOM 추가 조사 최우선 목록

개발 시작 가능하지만 아래는 P0 evidence.

---

## 66.1 공감 전/후 동일 button outerHTML

공감 전.

공감 후.

필요:

```text
aria-pressed
class
data-*
blind
icon class
title
```

---

## 66.2 댓글 등록 전/후

전:

```text
editor
submit
comment count
```

후:

```text
editor
comment count
new comment wrapper
```

---

## 66.3 Recommendation card

최소 서로 다른 card 3종:

```text
wrapper outerHTML
href
```

---

## 66.4 Infinite Scroll

FeedList / Recommendation:

```text
loader
sentinel
scroll container
```

없어도 괜찮음.

---

# 67. Like Resolver 구현 전 조건

공감 버튼 전/후 evidence 없으면:

```text
Like click 기능 구현 보류 가능
Read-only state diagnostic 우선
```

추측으로 state logic 확정 금지.

---

# 68. Submit Verification 구현 전 조건

성공 signal 실측 없으면:

```text
submit 기능은 개발 가능하나
Production success 판정은 UNKNOWN 처리
```

임의로 editor empty만 성공으로 확정 금지.

---

# 69. UI UX 원칙

사용자는 automation 내부 구조를 몰라도 된다.

보여줄 것:

```text
어디서
몇 개
무슨 작업
현재 어떤 글
무슨 상태
사용자가 뭘 하면 되는지
```

숨길 것:

```text
CSS selector
Playwright
DOM resolver
Worker
Thread
CDP technical detail
```

Advanced panel에만 technical options.

---

# 70. 상태 표현

예:

```text
현재 글 7/20
공감: 완료
댓글: 수정 대기
```

에러:

```text
공감 상태 확인 실패 — 이 글은 건너뜁니다.
```

사용자가 이해 가능한 message.

---

# 71. Default Safety Settings

```text
max_feed_items = 20
like_enabled = true
comment_enabled = true
comment auto submit = false
unknown like = skip
unknown submit = do not retry
```

---

# 72. Data Integrity

History write는 atomic write 권장.

예:

```text
temp file
fsync
replace
```

프로그램 강제 종료 시 JSON corruption 방지.

---

# 73. History Lock

Worker 1개라 초기엔 필요 없음.

향후 multi-process 금지 상태 유지.

---

# 74. Config write

사용자 설정 저장 시 atomic.

---

# 75. Runtime invariant

항상:

```text
작업 active = worker 1개
BrowserContext = 1개
FeedSource = 1개
PostProcessor = 1개
```

---

# 76. Post identity invariant

한 session에서 같은 key는 최대 1회 queue.

---

# 77. Like invariant

UNKNOWN state는 click하지 않는다.

---

# 78. Comment invariant

UserAction.SUBMIT 없이 submit 버튼 click 금지.

---

# 79. Verification invariant

submit click과 submitted state는 다르다.

---

# 80. History invariant

SUBMITTED는 verified success만.

---

# 81. Source invariant

FeedSource는 interaction을 하지 않는다.

---

# 82. PostProcessor invariant

PostProcessor는 DOM selector 문자열을 직접 가지지 않는다.

---

# 83. Resolver invariant

DOM knowledge는 resolver/interaction 계층만.

---

# 84. UI invariant

UI는 Playwright object를 직접 다루지 않는다.

---

# 85. Branch merge criteria

main merge 전:

```text
Neighbor E2E pass
Recommendation E2E pass
Stop pass
Like safety pass
Comment approval pass
Submit verification pass
History recovery pass
README updated
```

---

# 86. E2E Smoke Scenario — Neighbor

```text
1. 로그인 profile 존재
2. 앱 실행
3. Neighbor 선택
4. max=5
5. 시작
6. FeedList open
7. canonical 5개 discovery
8. 첫 글 detail open
9. like state
10. 필요 시 like
11. comment open
12. draft fill
13. 사용자가 수정
14. Enter
15. submit
16. verification
17. history
18. 다음 글
19. 5개 후 완료
```

---

# 87. E2E Smoke Scenario — Recommendation

```text
1. Recommendation 선택
2. feed_page Recommendation
3. category는 사용자가 선택 가능
4. card URL discovery
5. detail_page 이동
6. PostProcessor
7. feed_page scroll 유지
8. 다음 card
```

---

# 88. E2E Smoke Scenario — Skip

댓글 초안 상태에서:

```text
Esc
```

결과:

```text
submit 없음
history skipped
next post
```

---

# 89. E2E Smoke Scenario — Stop

WAITING_USER 상태에서 Stop.

결과:

```text
빠른 종료
브라우저 clean close
history corruption 없음
```

---

# 90. E2E Smoke Scenario — Already Liked

상태:

```text
LIKED
```

결과:

```text
click 없음
comment 계속 가능
```

---

# 91. E2E Smoke Scenario — Like Unknown

상태:

```text
UNKNOWN
```

결과:

```text
click 없음
warning
comment processing policy는 설정에 따라 계속
```

기본은 comment는 계속 가능.

---

# 92. E2E Smoke Scenario — Comment disabled

결과:

```text
COMMENT_DISABLED
history status
next post
```

---

# 93. E2E Smoke Scenario — Submit unknown

결과:

```text
자동 재등록 없음
UNKNOWN
사용자에게 표시
next/stop 정책 명확
```

권장 default:

```text
warning 후 next
```

---

# 94. Legacy PC 기능 정책

V1 모바일 전환이 목표.

PC 기능을 동시에 유지하려다 architecture를 복잡하게 만들지 않는다.

필요하면:

```text
legacy branch/tag
```

로 보존.

새 main product는 모바일 중심.

---

# 95. Search keyword 기능

V1 core 제외.

향후:

```text
SearchFeedSource
```

로 재도입 가능.

Collector를 legacy 형태로 controller에 다시 붙이지 않는다.

---

# 96. Secret Comment

UI option으로 남길 수 있음.

그러나 comment editor/submit 안정화 후 구현.

---

# 97. Fixed suffix

반드시 template과 별도 저장.

예:

```text
template
{좋은|유익한} 글 잘 읽었습니다!

suffix
오늘도 좋은 하루 보내세요 :)
```

---

# 98. Draft preview

V2 고려.

현재 생성된 draft를 GUI에서 preview하는 것보다 browser editor에서 바로 보는 것이 우선.

---

# 99. AI Draft

V1 제외.

추후:

```python
class DraftProvider(Protocol):
    def generate(post_context) -> str:
        ...
```

SpintaxDraftProvider

→ AIDraftProvider

교체 가능.

---

# 100. Architecture Decision Record 요약

## ADR-001

공감/댓글 기능별 worker → Feed workflow controller.

## ADR-002

Recommendation/Neighbor → Source Adapter 차이.

## ADR-003

Interaction → 공통 detail page processor.

## ADR-004

LikeState 3-state.

## ADR-005

Comment submit human approval only.

## ADR-006

submit verification required.

## ADR-007

DOM selector Source of Truth one place.

## ADR-008

dom_db evidence only.

## ADR-009

BrowserContext one per session.

## ADR-010

Recommendation feed/detail two pages.

---

# 101. Reviewer Checklist — Architecture

- [ ] AutoLiker top-level 제거
- [ ] AutoCommenter top-level 제거
- [ ] FeedController 존재
- [ ] FeedSource abstraction 존재
- [ ] canonical FeedPost 존재
- [ ] Source가 interaction 안 함
- [ ] PostProcessor 공통
- [ ] DOM resolver 중앙화
- [ ] History v2
- [ ] Config v2

---

# 102. Reviewer Checklist — Browser

- [ ] persistent context 1개
- [ ] feed_page 명시
- [ ] detail_page 명시
- [ ] anti-detection 코드 제거
- [ ] 낡은 UA override 제거
- [ ] arbitrary last-page reuse 제거
- [ ] clean close
- [ ] profile lock

---

# 103. Reviewer Checklist — Feed

- [ ] mobile FeedList
- [ ] Recommendation
- [ ] Direct URL
- [ ] canonical key
- [ ] duplicate skip
- [ ] infinite scroll
- [ ] exhaustion
- [ ] max_feed_items

---

# 104. Reviewer Checklist — Like

- [ ] 3-state
- [ ] UNKNOWN safe
- [ ] already-liked no click
- [ ] click 후 state verify
- [ ] force click 제거
- [ ] dispatch_event fallback 제거 또는 극히 제한

---

# 105. Reviewer Checklist — Comment

- [ ] contenteditable
- [ ] locator.fill
- [ ] user edit
- [ ] Enter approval
- [ ] Shift+Enter
- [ ] Esc
- [ ] final text read
- [ ] submit verify
- [ ] unknown no retry

---

# 106. Reviewer Checklist — UI

- [ ] 단일 workflow
- [ ] source selector
- [ ] like toggle
- [ ] comment toggle
- [ ] max items
- [ ] template
- [ ] suffix
- [ ] running status
- [ ] progress
- [ ] stop
- [ ] user instruction

---

# 107. Reviewer Checklist — Data

- [ ] history atomic
- [ ] config migration
- [ ] user profile ignored
- [ ] history ignored
- [ ] diagnostics output ignored
- [ ] submitted only verified

---

# 108. Reviewer Checklist — Diagnostics

- [ ] inspect_feed
- [ ] inspect_post
- [ ] inspect_recommendation
- [ ] JSON output
- [ ] before/after state
- [ ] DOM snapshot optional

---

# 109. Definition of Ready — 개발 시작

다음은 이미 대부분 충족.

```text
repository accessible
legacy code reviewed
SPEC exists
mobile DOM basic evidence exists
git history clean
sensitive profile ignored
```

추가 evidence:

```text
like before/after
comment submit before/after
recommendation card href
```

는 병행 확보.

---

# 110. Definition of Done

완료 조건:

```text
1. 앱 실행
2. persistent session
3. source 선택
4. feed open
5. post discovery
6. canonical key
7. duplicate check
8. detail open
9. safe like state
10. conditional like
11. comment open
12. draft fill
13. user edit
14. Enter
15. final text read
16. submit
17. verify
18. history
19. next
20. max reached
21. clean shutdown
```

Neighbor/Recommendation 모두 동일 PostProcessor 사용.

---

# 111. Merge Blocker — P0

다음 중 하나라도 있으면 main merge 금지.

```text
LIKE UNKNOWN click
already-liked unlike
댓글 자동 submit
submit success 미검증
history false success
source 별 duplicate interaction code
hashed class only selector
stop 무응답
infinite loop
profile/session 깨짐
```

---

# 112. P1

```text
History recovery
Recommendation feed/detail
structured logging
diagnostics
config migration
```

---

# 113. P2

```text
category automation
AI draft
advanced UI
session analytics
draft preview
```

---

# 114. 예상 구현 난이도

| 영역 | 난이도 |
|---|---|
| URL canonicalization | 낮음 |
| Domain models | 낮음 |
| Config migration | 낮음 |
| BrowserSession | 중간 |
| FeedList discovery | 중간 |
| Recommendation discovery | 중간~높음 |
| LikeState | 높음 |
| Comment editor | 중간 |
| Enter/Esc | 중간 |
| Submit verification | 높음 |
| History v2 | 중간 |
| GUI migration | 중간 |
| E2E 안정화 | 높음 |

---

# 115. 가장 먼저 실제로 해야 할 개발 작업

아래 순서를 바꾸지 말 것.

```text
A. branch
B. browser session
C. models
D. url canonicalization
E. Neighbor read-only discovery
F. resolver
G. Like read-only state
H. Safe like
I. Comment draft
J. User approval
K. Submit verification
L. History
M. Recommendation
N. GUI
O. cleanup
```

---

# 116. Claude / Codex에게 주는 실제 명령 원칙

작업 시작 시 다음을 전달.

```text
이 작업은 기존 AutoLiker/AutoCommenter에 모바일 selector를 추가하는 패치가 아니다.

MASTER_SPEC.md를 최상위 설계 기준으로 사용한다.

한 번에 전체 rewrite하지 말고 Phase별로 구현한다.

각 Phase 후:
1. 변경 파일
2. 테스트 결과
3. 남은 위험
4. 다음 Phase 진입 가능 여부
를 보고한다.

Acceptance Gate를 통과하지 못하면 다음 Phase로 넘어가지 않는다.
```

---

# 117. Implementation Report 형식

매 Phase 완료 후:

```markdown
## Phase X 결과

### 변경 파일
- ...

### 구현 내용
- ...

### 테스트
- ...

### 통과한 Acceptance
- ...

### 실패/미확정
- ...

### 실제 DOM 추가 확인 필요
- ...

### 다음 Phase
- GO / NO-GO
```

---

# 118. 금지된 보고 방식

```text
"완벽히 구현했습니다"
"100% 안정적입니다"
"모든 DOM과 일치합니다"
```

같은 과장 보고 금지.

실제 증거만 보고.

---

# 119. 실제 DOM과 Spec 충돌 시

우선순위:

```text
실제 DOM evidence
>
테스트
>
MASTER_SPEC
>
과거 SPEC
>
legacy code
```

Spec이 틀리면 Spec update.

실제 구현을 Spec에 억지로 맞추지 않는다.

---

# 120. Runtime fallback 철학

Fallback은 “아무거나 찾기”가 아니다.

각 fallback은 semantic equivalence가 있어야 한다.

예:

댓글 버튼 찾을 때:

```text
댓글 이름 button
pst.re button
댓글 blind text의 button ancestor
```

는 같은 의미.

하지만:

```text
페이지의 첫 번째 button
```

은 금지.

---

# 121. Locator policy

가능하면 Locator.

ElementHandle 최소화.

장기간 저장 금지.

---

# 122. Card scope policy

Feed page에서 DOM interaction이 필요한 경우:

```text
card.locator(...)
```

만 사용.

전역 하트 scan 금지.

---

# 123. Recommendation card diversity

masonry에서:

```text
large
small
image-only
text-heavy
```

다른 card 유형 테스트.

---

# 124. FeedList card diversity

```text
single image
multi image
text-focused
```

fixture 확보.

---

# 125. Comment editor race

댓글 버튼 클릭 후 즉시 editor가 없을 수 있음.

```python
editor.wait_for(state="visible")
```

condition wait.

---

# 126. Submit button enable state

fill 직후 button enabled 되는지 확인.

필요하면:

```text
enabled
aria-disabled
class state
```

검증.

---

# 127. Final text empty

사용자가 전체 댓글을 지우고 Enter 가능.

정책:

```text
빈 final text이면 submit 금지
GUI/log 안내
WAITING_USER 유지
```

---

# 128. Maximum comment length

실제 Naver 제한을 추측하지 않는다.

서버/DOM validation 발생 시 error handling.

---

# 129. Secret comment

checkbox 상태도 toggle 안전하게.

현재 체크 상태를 먼저 확인.

---

# 130. Browser focus

초안 fill 후:

```python
editor.focus()
```

브라우저 bring_to_front 필요 여부 실제 테스트.

사용자가 직접 Enter를 브라우저에서 누르므로 적절한 tab focus가 중요.

---

# 131. GUI/Browser focus UX

댓글 초안 준비 완료 시:

```text
detail_page.bring_to_front()
```

를 검토.

사용자가 즉시 수정할 수 있도록.

---

# 132. GUI minimize / browser foreground

V2 UX.

초기엔 browser bring_to_front 우선.

---

# 133. Session progress

`processed_count` 정의:

추천:

```text
최종 처리 결과가 나온 post
```

DRAFT 준비만 된 상태는 완료로 count하지 않음.

---

# 134. Skip count

별도:

```text
submitted
skipped
failed
liked
already_liked
unknown
```

session summary.

---

# 135. Session summary

완료 시:

```text
처리 20
공감 신규 12
이미 공감 5
공감 불명 3
댓글 등록 14
건너뜀 4
실패 2
```

---

# 136. README 최종 구조

Migration 후:

```text
What it is
How it works
Human approval
Supported sources
Install
Login
Run
Keyboard controls
Safety behavior
Troubleshooting
Architecture short note
```

“안티 디텍션” 문구 제거.

---

# 137. requirements cleanup

현재:

```text
playwright
customtkinter
pillow
requests
```

실제 import 검사.

사용하지 않는 dependency 제거.

dev:

```text
pytest
```

추가.

---

# 138. Python version

README에 지원 버전 명시 권장.

실제 CI/test 환경에 맞춰:

```text
Python 3.11+
```

등 확정.

추측으로 작성하지 말고 현재 실행환경 확인.

---

# 139. CI

V2/P1 고려.

최소 unit test:

```text
pytest
```

GitHub Actions 가능.

브라우저 live E2E는 로그인 필요 때문에 CI 제외 가능.

---

# 140. Live smoke tests

로컬 profile 환경에서 수동.

테스트 account/real account 정책 사용자 판단.

---

# 141. No destructive automation

댓글/공감 외:

```text
삭제
이웃추가
공유
신고
```

기능 추가 금지 unless explicit.

---

# 142. Repository cleanliness

diagnostic generated output commit 금지.

---

# 143. Legacy removal timing

새 Neighbor E2E pass 전에는 legacy 파일 삭제하지 않아도 됨.

하지만 merge 전:

```text
dead code
duplicate selector logic
```

정리.

---

# 144. `src/dom_registry.py` migration detail

Step 1:

새 resolver 구현.

Step 2:

legacy code가 resolver 사용.

Step 3:

dom_registry references 0 확인.

Step 4:

삭제 또는 `diagnostics/dom_observations.py` 형태로 축소.

---

# 145. `dom_db.json` versioning

새 observation을 추가한다면:

```json
{
  "version": "...",
  "captured_at": "...",
  "page": "...",
  "elements": ...
}
```

현재 값이 최신이라는 보장 없음.

---

# 146. DOM evidence confidence

각 selector에:

```text
observed
verified
fallback
deprecated
```

상태를 둘 수 있음.

runtime은 JSON을 읽지 않더라도 documentation에 활용.

---

# 147. Comment success evidence

가장 높은 우선순위 DOM 조사.

등록 후 새 comment wrapper에서:

```text
작성자
text
time
```

중 최소 text 확인.

---

# 148. Comment count race

count 증가가 늦을 수 있음.

timeout window 내 polling.

---

# 149. Duplicate comment prevention

History 외에도 현재 post의 최신 내 댓글 text 확인은 V2.

V1 canonical History로 충분.

---

# 150. Crash recovery

작업 중 앱 crash.

재시작 시:

```text
SUBMITTED verified history만 skip
UNKNOWN은 사용자 policy
```

UNKNOWN default:

```text
skip and warn
```

재등록 위험 방지.

---

# 151. History unknown policy

```text
unknown → 자동 재시도 금지
```

사용자 수동 reset 기능은 V2.

---

# 152. Current repo-specific implementation priorities

현재 저장소에서 가장 먼저 바꿀 파일 순서:

```text
1. src/types.py / app state
2. src/browser.py → browser/session.py
3. src/collector.py URL extraction
4. new resolver
5. new source
6. liker rewrite
7. commenter rewrite
8. controller
9. main GUI
```

---

# 153. Do not start from GUI

GUI부터 바꾸지 않는다.

먼저 headless가 아니라 visible browser에서 core workflow 검증.

CLI/diagnostic driver 가능.

---

# 154. Temporary development harness

예:

```python
python -m diagnostics.run_neighbor_smoke
```

GUI 없이:

```text
FeedList
→ one post
→ like state print
→ comment editor fill
```

핵심 로직 검증.

---

# 155. GUI cutover timing

core Neighbor + Recommendation smoke 통과 후.

---

# 156. Refactor size control

한 PR/commit에 30개 파일 대수술 금지.

Phase incremental.

---

# 157. Main branch protection mindset

각 phase가 runnable.

---

# 158. Rollback

문제 시:

```text
legacy tag
```

로 즉시 복원 가능.

---

# 159. Future extensibility

추후 Source:

```text
Search
MyBlog
Keyword
Manual queue
```

추가 가능.

PostProcessor 변경 없어야 함.

---

# 160. Future DraftProvider

```text
Spintax
AI
Manual
Template presets
```

---

# 161. Future Storage

JSON → SQLite로 바꿔도 HistoryStore interface 유지.

---

# 162. Future UI

CustomTkinter 유지 가능.

현재 scope에서 UI framework 변경 필요 없음.

---

# 163. Non-goal

Electron / Web app 전환 아님.

---

# 164. Non-goal

네이버 내부 API reverse engineering 아님.

---

# 165. Non-goal

CAPTCHA bypass 아님.

---

# 166. Non-goal

대량 자동 engagement 아님.

---

# 167. Non-goal

multi-account bot farm 아님.

---

# 168. Quality bar

아래 수준을 목표:

```text
selector 하나 깨져도 전체 앱이 오작동하지 않음
상태 모르면 안전하게 skip
중복 댓글 위험 최소화
중지 즉시 반응
브라우저 세션 꼬임 최소화
다음 작업자가 코드를 읽고 이해 가능
```

---

# 169. 최종 구현자 요약 지시

이 저장소에서 다음을 절대로 하지 말 것.

```text
기존 liker.py에 모바일 selector만 추가
기존 commenter.py에 contenteditable selector만 추가
Recommendation을 3번 기능으로 별도 구현
dom_registry.py의 hashed primary를 그대로 사용
submit click 후 바로 success 저장
Like state boolean으로 유지
main.py에 새 GUI 로직을 계속 추가
```

대신:

```text
FeedSource
FeedPost
FeedController
PostProcessor
Resolver
LikeState
Human approval
Verification
History v2
```

를 중심으로 재구성.

---

# 170. 최종 GO/NO-GO 기준

## GO

- BrowserSession stable
- Neighbor discovery stable
- Like state verified
- Comment draft stable
- User approval stable
- Submit verification reliable
- History stable

## NO-GO

- Like state 추측
- submit success 추측
- Recommendation URL 불안정
- Stop unreliable
- selector duplicated
- Legacy/new flow 혼재

---

# 171. 최종 체크 — 마스터 지시서 우선순위

작업자는 문서 충돌 시:

```text
1. 실제 라이브 DOM
2. 실제 테스트 결과
3. MASTER_SPEC.md
4. 기존 SPEC.md
5. legacy README
6. legacy code의 의도
```

순으로 판단.

---

# 172. 즉시 다음 작업

1. `refactor/mobile-feed-assistant` branch 생성.
2. legacy tag.
3. P0 DOM evidence 확보.
4. `BrowserSession` foundation.
5. `FeedPost` / state model.
6. URL canonicalization.
7. Neighbor read-only discovery.
8. Like read-only diagnostic.
9. 그 다음에만 click.
10. Comment draft.
11. Enter/Esc.
12. Submit verification.
13. History.
14. Recommendation.
15. GUI.
16. cleanup / README.

---

# Appendix A — P0 DOM 조사 템플릿

## Like Before

```text
URL:
Post key:

Button outerHTML:

Icon outerHTML:

aria-pressed:
class:
data attrs:
title:
blind text:
```

## Like After

동일 항목.

---

# Appendix B — Comment Submit 조사 템플릿

## Before

```text
Editor outerHTML:
Submit outerHTML:
Comment count outerHTML:
```

## After

```text
Editor outerHTML:
Submit outerHTML:
Comment count outerHTML:
New comment wrapper outerHTML:
```

---

# Appendix C — Recommendation Card

```text
Card type:
Wrapper outerHTML:
All href:
Selected post href:
Canonical key:
```

---

# Appendix D — Phase Completion Template

```markdown
# Phase X Completion

## Scope

## Files Changed

## Tests

## Acceptance Gate

## Evidence

## Known Risks

## DOM Unknowns

## GO / NO-GO

## Next Phase
```

---

# Appendix E — Suggested New Files

```text
app/controller.py
app/models.py
app/state.py
browser/session.py
naver/resolver.py
naver/interaction.py
naver/url_utils.py
naver/sources.py
services/config.py
services/draft.py
services/history.py
ui/main_window.py
diagnostics/inspect_feed.py
diagnostics/inspect_post.py
diagnostics/inspect_recommendation.py
```

---

# Appendix F — Legacy Files to Remove After Cutover

```text
src/liker.py
src/commenter.py
src/collector.py
src/dom_registry.py
```

단, 기능 migration 완료와 test pass 후 제거.

---

# Appendix G — Recommended `.gitignore` Additions

```gitignore
diagnostics/output/
data/dom_dumps/
*.html.snapshot
```

---

# Appendix H — Final Product Mental Model

이 프로그램은:

```text
"자동으로 댓글을 많이 다는 봇"
```

이 아니다.

최종 정의:

```text
"네이버 피드의 반복적인 공감·댓글 준비 과정을 빠르게 처리하고,
최종 댓글 등록은 사용자가 직접 승인하는 Feed Assistant"
```

이 정의에 맞지 않는 설계는 제거한다.

---

# END OF MASTER SPEC
