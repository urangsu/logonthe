# Naver Feed Assistant 모바일 전면개편 작업지시서

> 문서 목적: 현재 `naver-blog-bot`을 PC 중심의 “공감 자동화 + 댓글 자동화” 프로그램에서,  
> **네이버 모바일 피드 기반의 Human-in-the-loop Feed Assistant**로 전면 재설계한다.
>
> 대상 구현자: Claude Code / Codex / Python 엔지니어  
> 기준일: 2026-08-24  
> 우선순위: 안정성 > DOM 변경 내성 > 사용자 제어 > 속도 > 기능 수

---

## 0. Executive Summary

현재 프로그램은 다음 구조를 가진다.

- `main.py`
  - GUI
  - 설정
  - 브라우저 실행
  - 작업 Thread
  - 공감 Worker
  - 댓글 Worker
  - 로그인/프로필 관리
- `src/liker.py`
  - 공감 클릭
  - PC 블로그 홈 페이지 이동
- `src/commenter.py`
  - 댓글 작성 및 등록
- `src/collector.py`
  - 키워드 기반 게시글 수집
- `data/history.json`
  - 중복 방지

현재 기본 URL은 PC 블로그 홈이며, GUI도 “1번 기능: 공감 / 2번 기능: 댓글”로 분리되어 있다.  
댓글 기능 역시 피드 기반이 아니라 키워드 검색 또는 직접 URL 입력을 받아 별도 Worker에서 처리한다.

이번 개편에서는 이 구조를 폐기하고 다음 형태로 전환한다.

```text
GUI
 │
 ▼
FeedController
 │
 ├─ NeighborFeedSource
 │     └─ https://m.blog.naver.com/FeedList.naver
 │
 ├─ RecommendationFeedSource
 │     └─ https://m.blog.naver.com/Recommendation.naver
 │
 └─ DirectUrlSource
       │
       ▼
     FeedPost
       │
       ▼
  PostProcessor
   │       │
   ▼       ▼
LikeService CommentService
             │
             ▼
         DraftService
             │
             ▼
       User Approval
       Enter / Esc
             │
             ▼
        HistoryStore
```

핵심 원칙은 아래와 같다.

1. **이웃 피드와 추천 피드는 서로 다른 Source Adapter로 처리한다.**
2. **공감/댓글은 공통 PostProcessor가 처리한다.**
3. **댓글은 초안까지만 자동 입력하고 최종 등록은 사용자가 Enter로 승인한다.**
4. **CSS hashed class에 직접 의존하지 않는다.**
5. **네이버 DOM을 아는 코드는 `naver/` 계층에만 둔다.**
6. **BrowserContext는 1개를 유지한다.**
7. Recommendation 피드는 `feed_page`, 실제 게시물은 `detail_page`로 분리한다.
8. Infinite Scroll 기반으로 동작하며 `max_pages`는 제거하고 `max_feed_items`를 사용한다.
9. 등록 성공을 확인하기 전에는 History에 `SUBMITTED`로 기록하지 않는다.
10. “안티 디텍션”을 목표로 하지 않는다. 사용자의 명시적 승인과 안정성 중심으로 설계한다.

---

# 1. 현재 소스 진단

## 1.1 현재 `main.py`의 구조적 문제

현재 `main.py`는 약 700라인 수준이며 다음 책임을 모두 가진다.

- CustomTkinter GUI 생성
- config 로드
- Chrome/CDP 실행
- 로그인 브라우저 실행
- profile lock 관리
- 공감 시작/중지
- 댓글 시작/중지
- Worker Thread 생성
- logger callback
- 입력 validation

이 구조에서는 모바일 피드, 추천 피드, 상세글, AI 댓글 등 기능이 추가될수록 `main.py`가 계속 비대해진다.

### 변경 원칙

`main.py`의 최종 책임은 아래만 남긴다.

```python
def main():
    app = App()
    app.run()
```

GUI 생성은 `ui/`, 작업 실행은 `app/controller.py`, 브라우저는 `browser/`, 네이버 DOM 로직은 `naver/`로 분리한다.

---

## 1.2 현재 기능 단위 분리의 문제

현재 프로그램은 사용자의 작업 흐름이 아니라 내부 기능을 기준으로 나뉘어 있다.

```text
1번 기능 = 공감
2번 기능 = 댓글
```

하지만 실제 사용 흐름은 아래와 같다.

```text
게시글 발견
  ↓
공감
  ↓
댓글
  ↓
사용자 수정
  ↓
등록
  ↓
다음 게시글
```

따라서 공감과 댓글을 서로 다른 Top-level Worker로 두지 않는다.

### 변경 후

```text
FeedController
  └─ PostProcessor
       ├─ LikeService
       └─ CommentService
```

---

# 2. 현재 확보된 모바일 DOM 정보

> 주의: 아래 DOM 값은 현재 라이브 모바일 환경에서 확인된 것으로 전달받은 값이다.  
> CSS Module/hash 형태 class는 변경될 수 있으므로 “확정 selector”가 아니라 “현재 관찰값”으로 취급한다.

---

## 2.1 이웃 피드

URL:

```text
https://m.blog.naver.com/FeedList.naver
```

확인된 카드 계열:

```css
li.card_wrapper__F0VEP
```

현재 관찰된 전체 class 예:

```css
li.type_a__Fxu9d.item__rkExs.card_wrapper__F0VEP
```

게시물 링크 예:

```css
a.link__XWBJA[href*='m.blog.naver.com/']
```

### 구현 규칙

아래 selector를 Production logic의 1차 기준으로 사용하지 않는다.

```css
.card_wrapper__F0VEP
.link__XWBJA
```

이 값들은 CSS Module build 결과일 가능성이 높다.

대신:

1. 의미 있는 attribute
2. href pattern
3. 접근성 label
4. data attribute
5. 구조 기반 locator
6. hashed class fallback

순서로 Resolver를 만든다.

---

## 2.2 모바일 게시글 공감

현재 확인값:

```html
<span class="u_likeit_icon __reaction__zeroface"></span>
```

또는 실제 버튼:

```css
.u_likeit_button
```

### 중요한 규칙

`__reaction__zeroface`가 보인다고 무조건 “미공감 상태”라고 단정하지 않는다.

공감은 toggle이므로 잘못 판단하고 클릭하면 기존 공감을 취소할 수 있다.

반드시 “현재 공감 여부” 판별 함수를 별도로 구현한다.

```python
def get_like_state(page) -> LikeState:
    ...
```

예상 상태:

```python
class LikeState(Enum):
    LIKED = auto()
    NOT_LIKED = auto()
    UNKNOWN = auto()
```

`UNKNOWN`이면 클릭하지 않고 로그를 남긴다.

---

## 2.3 댓글 버튼

현재 라이브 DOM:

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

다음 class는 직접 selector로 사용하지 않는다.

```css
.Interact__comment_btn--Wbuoq
.Interact__icon--Sn7xy
```

우선순위:

```python
page.get_by_role("button", name="댓글")
```

fallback:

```python
blind = page.locator("span.blind").filter(has_text="댓글")
button = blind.locator(
    "xpath=ancestor::button[1]"
)
```

최종 fallback으로 현재 hash class 사용 가능.

---

## 2.4 댓글 Editor

확인된 실제 입력 요소:

```html
<div
    title="댓글"
    id="naverComment__write_textarea"
    class="u_cbox_text u_cbox_text_mention"
    contenteditable="true"
    data-area-code="RPC.input">
</div>
```

Primary selector:

```css
#naverComment__write_textarea
```

Fallback:

```css
[contenteditable='true'][data-area-code='RPC.input']
```

Fallback 2:

```css
[contenteditable='true'][title='댓글']
```

입력:

```python
editor.fill(draft)
editor.focus()
```

직접 `innerText`, `textContent`, `execCommand`, custom `input` event를 사용하지 않는다.

---

## 2.5 댓글 Placeholder

확인값:

```html
<div
    class="u_cbox_guide"
    data-action="write#placeholder"
    data-param="@event"
    style="display: block;">
    댓글을 입력해주세요.
</div>
```

이 요소는 입력창이 아니다.

절대 다음처럼 사용하지 않는다.

```python
page.locator(".u_cbox_guide").fill(...)
```

오직 editor 탐색 실패 시 “댓글 영역이 열렸는지” 참고하는 fallback marker로만 사용할 수 있다.

---

## 2.6 댓글 등록 버튼

확인값:

```css
button.u_cbox_btn_upload
```

현재 추가 class:

```css
.__uis_naverComment_writeButton
```

Primary:

```python
page.locator("button.u_cbox_btn_upload")
```

등록 버튼 class가 변경될 가능성을 고려해 추후 role/name이나 form 구조 fallback을 추가한다.

---

## 2.7 비밀댓글

확인값:

```css
input#naverComment__write_textarea_secret_check
```

현재 버전에서는 옵션으로 유지하되 V1 핵심 기능에서 우선순위는 낮다.

---

# 3. Target Product Definition

프로그램 이름은 내부적으로 다음 개념으로 통일한다.

```text
Naver Feed Assistant
```

“Auto Bot”보다는 “Feed Assistant”가 제품 실제 동작과 맞다.

목표는:

> 사용자가 피드를 탐색하면서 반복적인 공감 및 댓글 초안 입력 작업을 빠르게 처리하되, 댓글의 최종 게시 여부는 사용자가 직접 결정하는 보조 도구.

---

# 4. Scope

## 4.1 V1 필수

- 모바일 이웃 피드
- 모바일 추천 피드
- 직접 URL
- 공감
- 댓글창 오픈
- 댓글 초안 입력
- 고정 suffix
- Enter 등록 승인
- Shift+Enter 줄바꿈
- Esc Skip
- Stop
- max_feed_items
- History
- 중복 방지
- 댓글 성공 검증
- 로그

---

## 4.2 V1 제외

아래 기능은 초기 리팩터링과 동시에 만들지 않는다.

- AI 자동 댓글 생성
- 완전자동 댓글 등록
- 카테고리 자동 탐색
- 브라우저 fingerprint 조작
- captcha 우회
- 안티 디텍션 로직
- 프록시
- 다계정
- 병렬 댓글 등록
- headless 대량 처리

---

# 5. Target Folder Structure

```text
naver-feed-assistant/
│
├── main.py
├── config.json
├── requirements.txt
├── README.md
│
├── app/
│   ├── __init__.py
│   ├── controller.py
│   ├── models.py
│   ├── state.py
│   └── events.py
│
├── browser/
│   ├── __init__.py
│   ├── manager.py
│   └── profile_lock.py
│
├── naver/
│   ├── __init__.py
│   ├── selectors.py
│   ├── resolver.py
│   ├── interaction.py
│   └── sources/
│       ├── __init__.py
│       ├── base.py
│       ├── neighbor.py
│       ├── recommendation.py
│       └── direct.py
│
├── services/
│   ├── __init__.py
│   ├── draft_service.py
│   ├── history_service.py
│   └── config_service.py
│
├── ui/
│   ├── __init__.py
│   ├── main_window.py
│   ├── settings_panel.py
│   ├── running_panel.py
│   └── log_panel.py
│
├── diagnostics/
│   ├── inspect_feed.py
│   ├── inspect_post.py
│   ├── inspect_recommendation.py
│   └── dump_dom.py
│
└── data/
    ├── history.json
    └── user_profile/
```

---

# 6. Core Models

## 6.1 FeedSourceType

```python
from enum import Enum

class FeedSourceType(str, Enum):
    NEIGHBOR = "neighbor"
    RECOMMENDATION = "recommendation"
    DIRECT = "direct"
```

---

## 6.2 FeedPost

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class FeedPost:
    key: str
    source: FeedSourceType

    url: str
    title: Optional[str] = None
    blog_id: Optional[str] = None
    log_no: Optional[str] = None
```

Locator를 영구 모델에 저장하지 않는 것을 권장한다.

이유:

- infinite scroll에서 DOM element가 detach될 수 있음
- Recommendation feed virtualization 가능성
- detail page를 별도 page로 운영할 예정

필요한 경우 source가 일시적 Locator를 사용하되 `FeedPost`에는 stable data만 저장한다.

---

## 6.3 UserAction

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
OPEN_BROWSER
```

추가 가능.

---

## 6.4 FeedState

```python
class FeedState(Enum):
    IDLE = auto()

    OPENING_FEED = auto()
    DISCOVERING = auto()

    OPENING_POST = auto()
    LIKING = auto()

    OPENING_COMMENT = auto()
    FILLING_DRAFT = auto()
    WAITING_USER = auto()

    SUBMITTING = auto()
    VERIFYING = auto()

    SKIPPING = auto()
    LOADING_MORE = auto()

    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()
    COMPLETED = auto()
```

GUI는 Controller의 state를 읽어서 표시한다.

---

# 7. Browser Architecture

## 7.1 BrowserContext는 1개만 유지

프로그램 시작:

```text
Persistent BrowserContext
```

종료:

```text
Persistent BrowserContext close
```

작업마다 새 Context를 만들지 않는다.

---

## 7.2 Page 전략

### Neighbor Feed

가능한 경우:

```text
feed_page
```

하나에서 처리.

하지만 실제 게시글의 인터랙션이 feed card 내부에서 불안정하면 detail_page 전략을 동일하게 적용할 수 있도록 설계한다.

### Recommendation Feed

기본:

```text
BrowserContext
 ├─ feed_page
 │    Recommendation.naver
 │
 └─ detail_page
      실제 블로그 글
```

이유:

- feed scroll 위치 유지
- go_back() 의존 제거
- infinite scroll 복원 문제 제거
- detail page DOM을 공통 PostProcessor가 사용 가능

---

## 7.3 Mobile Emulation

모바일 URL 자체를 사용하므로 필수 요건으로 간주하지 않는다.

그러나 재현성 향상을 위해 BrowserManager에 다음 옵션을 넣을 수 있다.

```python
viewport={"width": 430, "height": 900}
is_mobile=True
has_touch=True
```

주의:

기존 `src/browser.py` 실제 코드 확인 후 persistent context 생성 방식에 맞게 적용할 것.

---

# 8. Feed Source Interface

`naver/sources/base.py`

```python
from abc import ABC, abstractmethod

class FeedSource(ABC):

    @abstractmethod
    def open(self) -> None:
        ...

    @abstractmethod
    def discover_posts(self) -> list[FeedPost]:
        ...

    @abstractmethod
    def load_more(self) -> bool:
        ...

    @abstractmethod
    def exhausted(self) -> bool:
        ...
```

Source는 공감이나 댓글을 처리하지 않는다.

Source의 책임은 오직:

```text
어떤 글을 처리할 것인가?
```

이다.

---

# 9. NeighborFeedSource

URL:

```text
https://m.blog.naver.com/FeedList.naver
```

## 책임

1. 피드 열기
2. visible card 탐색
3. card에서 실제 post URL 추출
4. FeedPost 생성
5. 이미 본 post는 제외
6. scroll
7. 새 card 로딩 확인

---

## 9.1 Card 탐색 전략

절대 아래처럼 전체 문서의 하트를 순회하지 않는다.

```python
page.locator(".u_likeit_button").all()
```

Card 단위로 URL을 발견한다.

현재 관찰된:

```css
li[class*='card_wrapper__']
```

는 fallback으로 사용.

가능하면 다음 정보를 추가 조사하여 primary selector를 확정한다.

- card 고유 data attribute
- 내부 article role
- stable link href
- card 내 click-area
- semantic wrapper

---

# 10. RecommendationFeedSource

URL:

```text
https://m.blog.naver.com/Recommendation.naver
```

Recommendation 페이지의 역할은:

```text
Discovery
```

이다.

가능하면 추천 카드 자체에서 공감/댓글을 직접 하지 않는다.

### 권장 흐름

```text
Recommendation card
        ↓
Post URL 추출
        ↓
FeedPost 생성
        ↓
detail_page.goto(post.url)
        ↓
공통 PostProcessor
```

이렇게 하면 Recommendation UI의 masonry/grid 레이아웃은 최소한만 다뤄도 된다.

---

# 11. Post URL Normalization

중복 판정의 핵심이다.

가능하면 URL에서:

```text
blogId
logNo
```

를 추출한다.

예:

```text
https://m.blog.naver.com/{blogId}/{logNo}
```

canonical key:

```python
key = f"{blog_id}:{log_no}"
```

query string, tracking parameter, mobile/PC hostname 차이는 제거한다.

예:

```text
m.blog.naver.com/foo/123?trackingCode=...
blog.naver.com/foo/123
```

둘 다:

```text
foo:123
```

로 정규화한다.

---

# 12. Naver Selector Resolver

`naver/selectors.py`에는 문자열만 모아두고,
`naver/resolver.py`에서 실제 우선순위 로직을 구현한다.

---

## 12.1 댓글 버튼

```python
def find_comment_button(page):

    by_role = page.get_by_role("button", name="댓글")

    if by_role.count():
        return by_role.first

    blind = page.locator("span.blind").filter(
        has_text="댓글"
    )

    if blind.count():
        return blind.first.locator(
            "xpath=ancestor::button[1]"
        )

    fallback = page.locator(
        "button[class*='Interact__comment_btn']"
    )

    if fallback.count():
        return fallback.first

    return None
```

---

## 12.2 댓글 editor

```python
def find_comment_editor(page):

    selectors = [
        "#naverComment__write_textarea",
        "[contenteditable='true'][data-area-code='RPC.input']",
        "[contenteditable='true'][title='댓글']",
    ]

    for selector in selectors:
        loc = page.locator(selector)

        if not loc.count():
            continue

        try:
            if loc.first.is_visible():
                return loc.first
        except Exception:
            pass

    return None
```

---

## 12.3 공감 버튼

공감은 단순 selector뿐 아니라 상태 판정이 중요하다.

```python
def find_like_button(page):
    ...
```

우선:

- button/accessibility
- `.u_likeit_button`
- `span.u_likeit_icon`의 clickable ancestor

순으로 구현한다.

---

# 13. Like State Resolver

공감 버튼은 toggle이므로 반드시 상태 확인 후 클릭한다.

```python
class LikeState(Enum):
    LIKED = auto()
    NOT_LIKED = auto()
    UNKNOWN = auto()
```

판정 후보:

- `aria-pressed`
- button active class
- reaction state class
- hidden/accessibility text
- tooltip/title
- 현재 icon class
- data attribute

### 절대 금지

```python
button.click()
```

를 모든 글에 무조건 실행.

### 정책

```text
NOT_LIKED → click
LIKED     → skip
UNKNOWN   → click 금지 + warning
```

---

# 14. PostInteractionAdapter

```python
class PostInteractionAdapter:

    def __init__(self, page, resolver):
        self.page = page
        self.resolver = resolver

    def like_if_needed(self):
        ...

    def open_comment(self):
        ...

    def fill_comment(self, text):
        ...

    def submit_comment(self):
        ...

    def verify_comment_submission(self):
        ...
```

이 클래스는 Neighbor/Recommendation을 알 필요가 없다.

오직 현재 열린 “게시글 화면”만 처리한다.

---

# 15. DraftService

`services/draft_service.py`

```python
class DraftService:

    def __init__(self, template: str, suffix: str):
        self.template = template
        self.suffix = suffix

    def generate(self) -> str:
        body = spin(self.template).strip()
        suffix = self.suffix.strip()

        if not suffix:
            return body

        return f"{body}\n{suffix}"
```

---

## 15.1 Config

```json
{
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

기존:

```json
"max_pages": 5
```

는 migration 후 제거.

---

# 16. Human-in-the-loop Keyboard UX

기본 UX:

```text
Enter       등록
Shift+Enter 줄바꿈
Esc         건너뛰기
```

최종 댓글 등록은 사용자가 명시적으로 Enter를 눌렀을 때만 실행.

---

## 16.1 Document-level Listener

댓글 editor element에 listener를 직접 붙이면 DOM rerender 시 제거될 수 있다.

따라서 document capture listener를 설치한다.

단, 무조건 전역 Enter를 가로채면 안 된다.

반드시:

```text
현재 event.target이 댓글 editor 또는 editor 내부일 때
```

만 처리.

예:

```javascript
const editor = e.target.closest?.(
    "#naverComment__write_textarea"
);

if (!editor) {
    return;
}
```

---

## 16.2 Python Polling

`page.evaluate(Promise)`로 무기한 blocking하지 않는다.

Python:

```python
while True:

    if stop_event.is_set():
        return UserAction.STOP

    action = page.evaluate(
        "() => window.__NAVER_FEED_ACTION__"
    )

    if action == "SUBMIT":
        return UserAction.SUBMIT

    if action == "SKIP":
        return UserAction.SKIP

    time.sleep(0.1)
```

---

# 17. Comment Submission

Enter가 눌렸다고 성공 처리하지 않는다.

흐름:

```text
User Enter
 ↓
SUBMIT signal
 ↓
등록 버튼 활성 여부 확인
 ↓
button.click()
 ↓
submission success verification
 ↓
History SUBMITTED
```

---

# 18. 반드시 구현할 Comment Success Verification

현재 추가 DOM 조사가 가장 필요한 부분 중 하나다.

가능한 성공 신호 후보:

1. editor 내용이 비워짐
2. 댓글 목록에 방금 작성한 text가 등장
3. 댓글 count 증가
4. 등록 완료 toast
5. write area state 변화
6. network response
7. 새 댓글 element 생성

### 권장

DOM 기반 1개 + 보조 조건 1개를 조합한다.

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

### 실패 정책

```text
등록 버튼은 클릭했지만 성공 신호를 확인하지 못함
```

이면:

```text
UNKNOWN
```

으로 기록한다.

자동으로 재등록하지 않는다.

중복 댓글 위험이 있기 때문이다.

---

# 19. HistoryStore

현재 단순 URL 중복 방지를 확장한다.

권장 schema:

```json
{
  "foo:123456": {
    "source": "recommendation",
    "url": "https://m.blog.naver.com/foo/123456",
    "title": "example",

    "like_status": "liked",

    "comment": {
      "status": "submitted",
      "draft": "좋은 글 잘 읽었습니다.",
      "submitted_text": "좋은 글 잘 읽었습니다.",
      "timestamp": "2026-08-24T22:20:00+09:00"
    }
  }
}
```

상태:

```text
DISCOVERED
LIKED
DRAFTED
SUBMITTED
SKIPPED
FAILED
UNKNOWN
```

---

# 20. 실제 수정된 댓글 저장

초안이 자동 입력된 뒤 사용자가 수정할 수 있다.

따라서 History에는 `draft`만 저장하면 안 된다.

등록 직전:

```python
final_text = editor.inner_text()
```

를 읽고:

```json
{
  "draft": "...",
  "submitted_text": "사용자가 실제 수정한 최종 댓글"
}
```

로 저장한다.

---

# 21. Infinite Scroll

기존 PC pagination:

```text
max_pages
```

제거.

대신:

```text
max_feed_items
```

사용.

기본:

```text
20
```

다만 대량 자동 처리보다 Human-in-the-loop 사용을 전제로 하므로 기본값은 지나치게 높게 잡지 않는다.

---

## 21.1 Scroll 완료 판정

단순:

```python
page.mouse.wheel(0, 800)
time.sleep(1)
```

만 사용하지 않는다.

다음 중 하나로 새 데이터 로딩을 확인한다.

- post key set 증가
- card count 증가
- loading indicator disappearance
- sentinel intersection
- scrollHeight 변화

예:

```python
before = len(discovered_keys)

scroll()

wait_until(
    len(new_discovered_keys) > before
)
```

---

## 21.2 End of Feed

연속 N회 스크롤 후 새로운 `FeedPost.key`가 없으면 종료.

예:

```text
3회 연속 새 게시글 없음 → exhausted
```

무한 루프 방지.

---

# 22. Recommendation Feed Navigation

Recommendation은 `go_back()` 전략을 기본으로 사용하지 않는다.

권장:

```text
feed_page  = Recommendation.naver
detail_page = actual post
```

각 post:

```python
detail_page.goto(post.url)
processor.process(detail_page, post)
```

완료 후 다음 URL로 이동.

feed_page scroll state는 그대로 유지한다.

---

# 23. UI Redesign

기존:

```text
공감 탭
댓글 탭
로그인 탭
```

을 아래로 변경.

---

## 23.1 Main Screen

```text
┌────────────────────────────────────────────┐
│ Naver Feed Assistant                       │
├────────────────────────────────────────────┤
│                                            │
│ 작업 소스                                  │
│ ● 이웃 피드                                │
│ ○ 추천/탐색                                │
│ ○ 직접 URL                                 │
│                                            │
│ 작업                                        │
│ ☑ 공감                                     │
│ ☑ 댓글 초안                                │
│                                            │
│ 최대 처리 글                               │
│ [ 20 ]                                     │
│                                            │
│ 댓글 초안                                  │
│ ┌────────────────────────────────────────┐ │
│ │ {좋은|유익한} 포스팅 잘 읽었습니다!  │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ 고정 끝말                                  │
│ ┌────────────────────────────────────────┐ │
│ │ 오늘도 좋은 하루 보내세요 :)          │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ Enter       등록                           │
│ Shift+Enter 줄바꿈                         │
│ Esc         건너뛰기                       │
│                                            │
│            [ ▶ 작업 시작 ]                 │
└────────────────────────────────────────────┘
```

---

## 23.2 Running Screen

```text
┌────────────────────────────────────────────┐
│ 작업 중                                    │
├────────────────────────────────────────────┤
│ 소스       추천 > 국내여행                 │
│ 진행       7 / 20                          │
│                                            │
│ 현재 글                                    │
│ 순천 조례호수공원 산책...                  │
│                                            │
│ 공감       ✓ 완료                          │
│ 댓글       ● 사용자 확인 대기              │
│                                            │
│ 브라우저에서 댓글을 수정한 뒤              │
│ Enter를 눌러 등록하세요.                   │
│                                            │
│ Enter       등록                           │
│ Esc         건너뛰기                       │
│                                            │
│ [일시정지]                      [중지]      │
└────────────────────────────────────────────┘
```

---

# 24. Thread / Concurrency

Tkinter main thread에서는 Playwright 작업을 하지 않는다.

권장:

```text
GUI Main Thread
      │
      └── Worker Thread 1개
             │
             └── FeedController
```

브라우저 automation worker는 1개만 사용한다.

공감과 댓글을 별도 worker로 병렬 실행하지 않는다.

---

# 25. Stop Behavior

Stop은 어느 상태에서도 가능한 한 빠르게 동작해야 한다.

`stop_event` 확인 위치:

- Feed discovery loop
- scroll wait
- post navigation
- like before click
- comment open
- draft fill
- user action polling
- submission verification

### 중요한 점

등록 버튼 클릭 직후 stop이 들어왔다면:

등록 결과 verification까지는 수행하고 종료한다.

이유:

submission 상태가 불명확하게 끝나는 것을 방지.

---

# 26. Pause

V1에서는 선택사항.

구현한다면:

```python
pause_event
```

사용.

댓글 editor에 사용자가 입력 중인 상태에서는 pause와 유사하므로 우선순위는 낮다.

---

# 27. Error Handling

각 Post 처리는 전체 session을 죽이지 않도록 한다.

```python
try:
    process(post)
except RecoverablePostError:
    history.mark_failed(...)
    continue
```

단 아래는 session 중단:

- login lost
- browser disconnected
- repeated DOM structure failure
- profile corruption
- unrecoverable navigation failure

---

# 28. Failure Categories

```python
class FailureReason(Enum):
    POST_NOT_FOUND = auto()
    LIKE_BUTTON_NOT_FOUND = auto()
    LIKE_STATE_UNKNOWN = auto()

    COMMENT_DISABLED = auto()
    COMMENT_BUTTON_NOT_FOUND = auto()
    COMMENT_EDITOR_NOT_FOUND = auto()
    COMMENT_SUBMIT_NOT_FOUND = auto()
    COMMENT_SUBMIT_UNVERIFIED = auto()

    NAVIGATION_FAILED = auto()
    LOGIN_REQUIRED = auto()
```

로그와 History에 남긴다.

---

# 29. Logging

기존 logger를 유지하되 structured message를 도입한다.

예:

```text
[FEED] source=recommendation opened
[DISCOVER] key=foo:123 title="..."
[LIKE] key=foo:123 state=NOT_LIKED action=CLICK
[COMMENT] key=foo:123 editor=READY
[DRAFT] key=foo:123 filled
[WAIT] key=foo:123 action=USER
[SUBMIT] key=foo:123 clicked
[VERIFY] key=foo:123 success
```

GUI용 message와 debug log를 가능하면 분리한다.

---

# 30. Diagnostic Scripts

기존 임시로 만들었다 삭제한:

```text
inspect_m_feed.py
inspect_m_post.py
inspect_targeted.py
```

방식은 좋은 접근이다.

다만 앞으로는 삭제하지 않고 `diagnostics/`에 유지한다.

---

## 30.1 inspect_feed.py

수집:

- 모든 visible card wrapper 후보
- card outerHTML 일부
- href
- 제목
- data-* attrs
- role
- clickable child
- interaction button
- scrollHeight
- card count

---

## 30.2 inspect_post.py

수집:

- like button HTML
- before-like 상태
- after-like 상태
- comment button HTML
- comment editor HTML
- submit button HTML
- comment count HTML
- 등록 전/후 변화

---

## 30.3 inspect_recommendation.py

수집:

- recommendation card wrapper
- post links
- category buttons
- current selected category
- masonry/grid wrapper
- scroll container
- loading sentinel
- 중복 post URL

---

## 30.4 dump_dom.py

문제가 생겼을 때 현재 page HTML을 timestamp와 함께 저장.

단 개인정보/쿠키/민감 데이터가 저장되지 않도록 주의.

---

# 31. Selector 정책

## 금지

```python
".Interact__comment_btn--Wbuoq"
".Interact__icon--Sn7xy"
".card_wrapper__F0VEP"
```

만 단독 primary selector로 사용하는 코드.

---

## 허용

fallback:

```python
button[class*="Interact__comment_btn"]
li[class*="card_wrapper__"]
```

### 우선순위

1. Role/name
2. Stable ID
3. Stable data attribute
4. Semantic href pattern
5. Text/blind accessibility marker
6. Structural relation
7. class prefix
8. current hashed class

---

# 32. Login / Session

현재 persistent user profile 방식은 유지 가능.

목표:

- 최초 로그인 1회
- 이후 persistent profile 사용
- 작업 시작 전 로그인 상태 확인

작업 시작 전에 간단한 login probe 수행.

예:

```text
naver login redirect 여부
profile/menu presence
```

로그인이 풀린 경우 자동 로그인 우회 시도 금지.

사용자에게 로그인 필요 상태 표시.

---

# 33. Recommendation Category

초기 V1에서는 자동 category 선택을 필수로 하지 않는다.

권장 V1 UX:

```text
추천 피드를 브라우저에서 연다.
사용자가 원하는 카테고리를 선택한다.
[작업 시작] 클릭.
현재 선택된 feed를 처리한다.
```

V2에서 category automation 추가.

추가할 경우 반드시:

- category button text
- selected state
- role
- data attribute

를 실제 DOM에서 확보한 뒤 구현.

---

# 34. 현재 소스 Migration Plan

## Phase 0 — 백업

- 현재 동작 브랜치 tag
- `main.py`
- `src/browser.py`
- `src/liker.py`
- `src/commenter.py`
- `src/collector.py`
- `config.json`
- `history.json`

백업.

---

## Phase 1 — 구조 분리

기능 변경 없이:

```text
main.py → UI / Controller / Browser
```

분리.

Acceptance:

현재 기능이 이전과 동일하게 실행.

---

## Phase 2 — 모바일 Browser / Feed Source

- `FeedList.naver`
- mobile context
- NeighborFeedSource
- post URL normalization
- FeedPost
- max_feed_items

Acceptance:

댓글/공감 없이 게시글 20개 key를 안정적으로 순회.

---

## Phase 3 — 공감

- Like resolver
- LikeState
- safe click
- already-liked skip
- UNKNOWN safety

Acceptance:

이미 공감한 글의 공감을 취소하지 않음.

---

## Phase 4 — 댓글 UI

- comment button resolver
- editor resolver
- `locator.fill`
- suffix
- Enter / Shift+Enter / Esc
- stop polling

Acceptance:

자동 등록 없이 초안까지만 입력.

---

## Phase 5 — 등록

- submit button
- final text read
- success verification
- History update

Acceptance:

실제 성공 확인된 댓글만 SUBMITTED.

---

## Phase 6 — Recommendation

- RecommendationFeedSource
- feed_page/detail_page
- URL discovery
- common PostProcessor

Acceptance:

Neighbor와 동일한 공감/댓글 engine 재사용.

---

## Phase 7 — UI 통합

기존 1번/2번 기능 삭제.

Feed Assistant single workflow UI.

---

## Phase 8 — Recovery / Diagnostics

- History recovery
- DOM dump
- failure categories
- structured logs

---

# 35. Commit 단위 권장

```text
1. refactor: split app controller and ui
2. refactor: isolate browser session manager
3. feat: add feed models and state machine
4. feat: add neighbor mobile feed source
5. feat: add canonical post id parser
6. feat: add resilient naver selector resolver
7. feat: add safe like state resolver
8. feat: add comment draft workflow
9. feat: add user enter/esc approval
10. feat: verify comment submission
11. feat: persist feed history states
12. feat: add recommendation source
13. feat: add recommendation detail page workflow
14. refactor: replace legacy GUI with feed assistant
15. test: add DOM fixtures and diagnostic scripts
```

한 commit에 모든 파일을 한꺼번에 갈아엎지 않는다.

---

# 36. Acceptance Criteria

## A. Browser

- [ ] BrowserContext가 session 중 재생성되지 않는다.
- [ ] Naver 로그인 profile이 유지된다.
- [ ] Neighbor/Recommendation에서 동일한 context 사용.
- [ ] Recommendation에서 feed scroll position이 유지된다.

## B. Feed

- [ ] 모바일 FeedList에서 post URL을 안정적으로 수집.
- [ ] 동일 글 중복 처리 없음.
- [ ] Infinite scroll 종료 조건 존재.
- [ ] max_feed_items 정확히 준수.

## C. Like

- [ ] 공감 상태 확인 후 클릭.
- [ ] 이미 공감된 글을 다시 클릭하지 않음.
- [ ] 상태 UNKNOWN에서는 클릭하지 않음.

## D. Comment

- [ ] 댓글 버튼 안정적으로 탐색.
- [ ] editor는 contenteditable 실제 요소 사용.
- [ ] `locator.fill()`로 초안 입력.
- [ ] suffix 분리.
- [ ] Enter 등록.
- [ ] Shift+Enter 줄바꿈.
- [ ] Esc skip.
- [ ] 사용자가 수정한 최종 text 저장.
- [ ] 성공 확인 전 SUBMITTED 기록 금지.

## E. Stop

- [ ] 댓글 대기 중 즉시 Stop 가능.
- [ ] Scroll 중 Stop 가능.
- [ ] Post 사이에서도 Stop 가능.
- [ ] 등록 클릭 직후에는 결과 확인 후 종료.

## F. Recommendation

- [ ] Recommendation 카드의 실제 post URL 추출.
- [ ] detail_page에서 공통 PostProcessor 사용.
- [ ] go_back 의존 없음.
- [ ] scroll state 유지.

## G. UI

- [ ] 공감/댓글 별도 탭 제거.
- [ ] source 선택 가능.
- [ ] like/comment toggle.
- [ ] max items.
- [ ] draft.
- [ ] fixed suffix.
- [ ] 실행 중 현재 상태 표시.
- [ ] 현재 처리 개수 표시.

---

# 37. 반드시 추가로 실제 DOM에서 찾아야 할 항목

아래 정보가 확보되면 구현 정확도가 크게 올라간다.

---

## 37.1 공감 버튼 “전/후 상태” DOM

### 필요

공감 전:

```html
전체 button outerHTML
```

공감 후:

```html
전체 button outerHTML
```

특히 확인:

- `aria-pressed`
- class 변경
- title
- data-*
- blind text
- reaction icon class
- count 주변 DOM

### 이유

가장 중요한 것은 공감 여부 판별.

이것이 없으면 실수로 unlike 가능.

---

## 37.2 댓글 등록 성공 전/후 DOM

댓글 등록 전:

- comment count
- editor
- submit button

등록 후:

- editor
- comment count
- 새 comment wrapper
- toast
- success message

### 가장 필요한 정보

“등록이 성공했다”고 확실히 판단할 수 있는 stable signal.

---

## 37.3 Recommendation 카드의 실제 href

카드 한 개의:

```html
card outerHTML
```

또는:

```text
모든 a[href]
```

를 확인.

필요:

```text
post URL이 카드에서 직접 얻어지는지
```

---

## 37.4 Recommendation 카드 Stable Wrapper

현재 화면상 masonry 카드가 다양한 크기.

다음 중 무엇이 있는지 확인:

- `article`
- `li`
- role
- data-id
- data-click-area
- semantic href
- card id

hashed class 외 stable marker 필요.

---

## 37.5 FeedList 카드 Stable Wrapper

현재:

```css
li[class*='card_wrapper__']
```

보다 더 안정적인 marker가 있는지 확인.

---

## 37.6 Recommendation Category DOM

예:

```text
전체
경제
육아
연애
푸드
리빙
레시피
일상
국내여행
맛집
```

각 버튼의:

```html
outerHTML
```

현재 선택된 버튼의:

```html
outerHTML
```

둘 다 필요.

---

## 37.7 Infinite Scroll Loader

FeedList / Recommendation 각각 하단에서:

- loading spinner
- sentinel
- observer target
- “더보기”
- scroll container

가 있는지 확인.

---

## 37.8 댓글 불가 게시글

댓글이 막힌 글 하나가 있다면:

- 댓글 버튼 존재 여부
- 버튼 disabled 여부
- 댓글 영역 메시지

확인.

`COMMENT_DISABLED`를 정확히 판정하는 데 필요.

---

## 37.9 공감 불가 게시글

공감이 비활성화된 글이 있다면 DOM 확보.

---

## 37.10 삭제/비공개/로그인 Redirect

실제 post URL에 접근했을 때:

- 삭제
- 비공개
- 이웃공개
- 로그인 필요

각 state marker가 있으면 좋다.

---

# 38. 사용자에게 요청할 실제 조사 순서

다음 순서로 찾는 것이 가장 효율적이다.

### 1순위 — Like State

1. 아직 공감하지 않은 글 열기
2. like button outerHTML 저장
3. 공감 클릭
4. 같은 button outerHTML 저장

---

### 2순위 — Comment Submit State

1. 댓글창 열기
2. editor outerHTML
3. submit button outerHTML
4. 댓글 count outerHTML
5. 테스트 댓글 실제 등록
6. 등록 직후 위 3개 다시 추출
7. 새 댓글 element outerHTML 추출

---

### 3순위 — Recommendation Card

추천 카드 2~3개에서:

```text
wrapper outerHTML
실제 href
```

저장.

크기가 다른 카드 각각 확보하면 좋다.

---

### 4순위 — FeedList Card

서로 다른 card type:

- 이미지 1장
- 이미지 여러 장
- 텍스트 중심

3종 정도 wrapper 확인.

---

### 5순위 — Infinite Scroll

스크롤 전/후:

```text
card count
scrollHeight
loading element
```

확인.

---

# 39. Diagnostic Script 출력 형식 권장

임시 inspect script는 결과를 console만 찍지 말고 JSON으로 저장한다.

예:

```json
{
  "timestamp": "...",
  "url": "...",
  "page_type": "post",

  "like": {
    "button_html": "...",
    "aria_pressed": "...",
    "class": "..."
  },

  "comment": {
    "button_html": "...",
    "editor_html": "...",
    "submit_html": "..."
  }
}
```

이후 DOM이 바뀌면 이전 JSON과 diff 가능.

---

# 40. Test Strategy

실제 Naver만 대상으로 테스트하지 않는다.

실제 DOM 일부를 fixture HTML로 저장하고 locator unit test를 만든다.

예:

```text
tests/fixtures/
 ├─ neighbor_card.html
 ├─ recommendation_card.html
 ├─ post_unliked.html
 ├─ post_liked.html
 ├─ comment_editor.html
 └─ comment_submitted.html
```

---

## 40.1 Resolver Tests

```python
def test_find_comment_button():
    ...

def test_find_editor():
    ...

def test_like_state_unliked():
    ...

def test_like_state_liked():
    ...
```

---

# 41. Production Safety Rules

다음 동작은 금지.

- selector 실패 시 화면 임의 좌표 클릭
- like 상태 UNKNOWN에서 클릭
- submit 결과 UNKNOWN에서 자동 재등록
- 댓글창을 못 찾으면 다른 text box에 입력
- recommendation 카드 URL 불명확한 상태에서 임의 첫 링크 사용
- hash class 하나만 믿고 동작
- endless scroll
- stop_event 무시
- 사용자의 Enter 없이 댓글 자동 등록

---

# 42. Performance Guidelines

속도는 `sleep`을 줄이는 방식보다 Event 기반 wait로 개선.

금지:

```python
time.sleep(3)
time.sleep(5)
```

권장:

```python
locator.wait_for(...)
page.wait_for_url(...)
expect(...)
```

Polling이 필요한 사용자 입력 대기만 짧은 polling 사용.

---

# 43. Definition of Done

이번 전면개편은 아래 조건이 만족될 때 완료로 본다.

```text
1. 프로그램 실행
2. persistent browser 자동 연결
3. source 선택
4. mobile feed open
5. 글 발견
6. 중복 확인
7. 공감 여부 판정
8. 필요한 경우 공감
9. 댓글창 open
10. 초안 fill
11. 사용자 수정
12. Enter
13. 실제 등록 성공 검증
14. history 저장
15. 다음 글
16. max_feed_items 도달
17. 정상 종료
```

Recommendation에서도 4~15가 동일한 PostProcessor로 수행되어야 한다.

---

# 44. 구현자에게 주는 최종 지시

이 작업은 기존 `AutoLiker`와 `AutoCommenter`에 모바일 selector를 덧붙이는 패치 작업으로 처리하지 말 것.

목표는 아래 구조로 “개념 자체”를 변경하는 것이다.

```text
Legacy

GUI
 ├─ AutoLiker Worker
 └─ AutoCommenter Worker


Target

GUI
  ↓
FeedController
  ↓
FeedSource
  ↓
FeedPost
  ↓
PostProcessor
 ├─ LikeService
 └─ CommentService
       ↓
    User Approval
       ↓
    History
```

Recommendation 기능 역시 별도 “3번 기능”으로 추가하지 말 것.

```text
NeighborFeedSource
RecommendationFeedSource
DirectUrlSource
```

라는 Source 차이로만 처리하고,
게시글 내부 공감/댓글 interaction은 반드시 공통 엔진을 사용한다.

DOM selector는 중앙 Resolver로 통합하고,
hashed CSS class는 fallback으로만 사용한다.

최종 결과물은 “자동화 기능이 많아 보이는 프로그램”이 아니라,
사용자의 반복 동작을 최소화하면서도 잘못된 클릭·중복 댓글·세션 꼬임에 강한
**작고 예측 가능하며 복구 가능한 Feed Assistant**여야 한다.

---

# Appendix A — 구현 전 추가 확보하면 좋은 파일

현재 다음 파일을 함께 확보하면 실제 코드 변경 지시를 더 정확하게 만들 수 있다.

```text
src/browser.py
src/liker.py
src/commenter.py
src/collector.py
src/types.py
src/spintax.py
src/logger.py
config.json
requirements.txt
data/history.json (개인정보 제거 가능)
```

특히 우선순위:

```text
1. browser.py
2. liker.py
3. commenter.py
4. collector.py
```

`main.py`만으로는 BrowserContext 생성 방식과 기존 DOM 처리 구현을 모두 확인할 수 없기 때문이다.

---

# Appendix B — 가장 먼저 확보할 DOM 5개

개발을 시작하기 전에 가능하면 아래 5개를 확보한다.

```text
1. 공감 전 like button outerHTML
2. 공감 후 like button outerHTML
3. 댓글 등록 전/후 comment area DOM
4. Recommendation 카드 outerHTML + 실제 href
5. Recommendation / FeedList infinite-scroll loader DOM
```

이 5개가 있으면 V1의 핵심 selector/state machine을 거의 확정할 수 있다.
