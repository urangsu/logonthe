# NAVER FEED ASSISTANT
# PACING + GEMINI CLIPBOARD ASSISTANT IMPLEMENTATION WORK ORDER

> 대상 저장소: `urangsu/logonthe`
> 대상 브랜치: `refactor/mobile-feed-assistant`
> 목적: 현재 Human-in-the-loop Feed Assistant에
> 1) 사용자 설정형 작업 간격,
> 2) 랜덤 pause,
> 3) 제목/본문 맥락 기반 댓글 보조,
> 4) Gemini 수동 복붙 워크플로
> 를 추가한다.
>
> Gemini/OpenAI 등 어떠한 AI API도 이번 단계에서는 사용하지 않는다.
> AI 서비스의 웹 UI를 Playwright로 자동 조작하지 않는다.
> 최종 댓글 등록은 기존대로 사용자의 Enter 승인 후에만 수행한다.

---

# 0. 핵심 요구사항

이번 변경의 목표는 다음과 같다.

```text
현재

글 열기
→ 공감
→ 댓글창
→ Spintax 초안 입력
→ 사용자 수정
→ Enter
→ 등록 검증
→ 바로 다음 글


변경 후

글 열기
→ 필요 시 짧은 작업 간격
→ 공감
→ 필요 시 짧은 작업 간격
→ 댓글창
→ 기본 초안 입력
→ 제목 + 본문 일부 추출
→ Gemini용 프롬프트 준비
→ 사용자가 필요하면 Gemini에서 댓글 생성
→ 결과를 클립보드에서 댓글창으로 적용
→ 사용자 직접 최종 수정
→ Enter
→ 등록 검증
→ 다음 글 전 랜덤 작업 간격
→ 일정 확률로 랜덤 Pause
→ 다음 글
```

---

# 1. 안전 원칙

이 기능의 랜덤 대기 시간은 브라우저가 지나치게 빠르게 화면을 전환하지 않도록 하고,
사용자가 현재 처리 상태를 인지할 수 있게 하는 **작업 pacing 기능**으로 구현한다.

다음은 하지 않는다.

```text
- CAPTCHA 우회
- fingerprint 위장
- navigator.webdriver 조작
- proxy rotation
- platform detection 우회 로직
- AI 결과 자동 게시
- Gemini 웹페이지 자동 입력/자동 클릭
```

---

# 2. 현재 코드와 통합해야 할 위치

현재 구조를 유지한다.

```text
FeedController
    ↓
PostProcessor
    ↓
LikeInteractionService
CommentInteractionService
    ↓
UserAction
    ↓
HistoryStore
```

새 기능은 아래처럼 추가한다.

```text
FeedController
    │
    ├── PacingService
    │
    └── PostProcessor
           │
           ├── LikeInteractionService
           ├── ContentContextExtractor
           ├── DraftService
           ├── AIPromptBuilder
           ├── ClipboardCommandBridge
           └── CommentInteractionService
```

---

# 3. 신규 파일 권장

다음을 추가한다.

```text
services/
├── pacing.py
├── ai_prompt.py
└── clipboard_bridge.py

naver/
└── content_extractor.py
```

기존 파일 수정:

```text
app/controller.py
app/processor.py
app/models.py
app/state.py

services/config.py
services/draft.py

naver/interaction.py
naver/resolver.py

ui/main_window.py

config.json
README.md
tests/
```

---

# 4. PacingService

신규:

```text
services/pacing.py
```

Pacing 관련 모든 random 계산은 이 파일에서만 한다.

다른 파일에서 직접:

```python
random.uniform(...)
time.sleep(...)
```

를 난립시키지 않는다.

---

# 5. Pacing 설정

기본 config에 다음 필드를 추가한다.

```json
{
  "pacing_enabled": true,

  "action_delay_min": 1.0,
  "action_delay_max": 2.5,

  "next_post_delay_min": 2.0,
  "next_post_delay_max": 5.0,

  "random_pause_enabled": true,
  "random_pause_chance": 0.10,
  "random_pause_min": 8.0,
  "random_pause_max": 20.0
}
```

숫자는 기본 예시값이다.

사용자가 GUI에서 변경할 수 있어야 한다.

---

# 6. Pacing 종류

Pacing을 3종으로 구분한다.

## 6.1 ACTION_DELAY

짧은 UI 동작 사이.

예:

```text
글 진입 완료
→ 공감 확인 전

공감 완료
→ 댓글 열기 전
```

설정:

```text
action_delay_min
action_delay_max
```

---

## 6.2 NEXT_POST_DELAY

현재 글 처리가 완전히 끝난 뒤 다음 글을 열기 전.

설정:

```text
next_post_delay_min
next_post_delay_max
```

ACTION_DELAY보다 기본적으로 길게 설정.

---

## 6.3 RANDOM_PAUSE

가끔 발생하는 긴 휴지.

다음 글로 넘어가는 **안전한 경계에서만** 발생.

설정:

```text
random_pause_enabled
random_pause_chance
random_pause_min
random_pause_max
```

예:

```text
10% 확률
8초 ~ 20초
```

---

# 7. Random Pause가 발생하면 안 되는 위치

절대 다음 상태에서는 랜덤 pause를 시작하지 않는다.

```text
WAITING_USER
SUBMITTING
VERIFYING
댓글 editor에 사용자가 입력 중
브라우저 navigation 중
공감 버튼 click 직전
```

특히 댓글 등록 버튼을 누른 뒤 verification 전에 pause 금지.

---

# 8. Random Pause 허용 위치

권장 위치:

```text
한 게시글 처리가 종료된 직후
AND
다음 게시글을 열기 전
```

또는:

```text
Feed load_more 직전
```

---

# 9. PacingService API

예시:

```python
from dataclasses import dataclass
from enum import Enum
import random
import threading

from browser.session import interruptible_wait


class PacingKind(str, Enum):
    ACTION = "action"
    NEXT_POST = "next_post"
    PAUSE = "pause"


@dataclass
class PacingResult:
    kind: PacingKind
    seconds: float
    interrupted: bool = False


class PacingService:
    def __init__(self, config, stop_event=None, state_manager=None):
        self.config = config
        self.stop_event = stop_event
        self.state_manager = state_manager

    def _range(self, min_key, max_key):
        low = float(self.config.get(min_key, 0))
        high = float(self.config.get(max_key, low))

        if high < low:
            low, high = high, low

        return low, high

    def wait_action(self):
        if not self.config.get("pacing_enabled", True):
            return PacingResult(PacingKind.ACTION, 0)

        low, high = self._range(
            "action_delay_min",
            "action_delay_max"
        )

        seconds = random.uniform(low, high)

        interrupted = interruptible_wait(
            self.stop_event,
            seconds
        )

        return PacingResult(
            PacingKind.ACTION,
            seconds,
            interrupted
        )

    def wait_next_post(self):
        if not self.config.get("pacing_enabled", True):
            return PacingResult(PacingKind.NEXT_POST, 0)

        low, high = self._range(
            "next_post_delay_min",
            "next_post_delay_max"
        )

        seconds = random.uniform(low, high)

        interrupted = interruptible_wait(
            self.stop_event,
            seconds
        )

        return PacingResult(
            PacingKind.NEXT_POST,
            seconds,
            interrupted
        )

    def maybe_pause(self):
        if not self.config.get(
            "random_pause_enabled",
            False
        ):
            return None

        chance = float(
            self.config.get(
                "random_pause_chance",
                0.10
            )
        )

        chance = max(0.0, min(1.0, chance))

        if random.random() >= chance:
            return None

        low, high = self._range(
            "random_pause_min",
            "random_pause_max"
        )

        seconds = random.uniform(low, high)

        interrupted = interruptible_wait(
            self.stop_event,
            seconds
        )

        return PacingResult(
            PacingKind.PAUSE,
            seconds,
            interrupted
        )
```

---

# 10. 모든 대기는 interruptible

랜덤 대기 중에도 GUI의 Stop은 즉시 반응해야 한다.

절대:

```python
time.sleep(20)
```

사용 금지.

반드시:

```python
interruptible_wait(stop_event, seconds)
```

사용.

---

# 11. Pacing UI

현재 Main Config Card에 새 섹션 추가.

예:

```text
작업 간격

☑ 작업 간격 사용

일반 동작 대기
[ 1.0 ] 초 ~ [ 2.5 ] 초

다음 글 열기 전
[ 2.0 ] 초 ~ [ 5.0 ] 초

☑ 랜덤 Pause

Pause 발생 확률
[ 10 ] %

Pause 시간
[ 8 ] 초 ~ [ 20 ] 초
```

---

# 12. 입력 검증

조건:

```text
0 <= action_min <= action_max <= 300
0 <= next_min <= next_max <= 300
0 <= pause_min <= pause_max <= 3600
0 <= pause_chance <= 100
```

GUI에서 `%`로 입력받고 config에는:

```text
0.10
```

형태로 저장.

---

# 13. Pacing 상태 표시

대기 중에는 상태 Dashboard를 업데이트한다.

예:

```text
다음 글로 이동 전 대기 중... 3.4초
```

Pause:

```text
잠시 쉬는 중... 14.7초
```

단순히 화면이 멈춘 것처럼 보이면 안 된다.

---

# 14. Random Pause 카운트다운

선택 기능.

초 단위로 상태 표시하면 UX가 좋다.

```text
Pause 14초
Pause 13초
Pause 12초
...
```

이를 위해 PacingService가 state callback을 받을 수 있다.

그러나 V1에서는:

```text
Pause 14.3초
```

1회 표시만으로도 충분.

---

# 15. Pacing 적용 위치

## 15.1 PostProcessor

상세 진입 직후:

```text
ACTION_DELAY
```

공감 작업 후 댓글을 열기 전:

```text
ACTION_DELAY
```

---

## 15.2 FeedController

한 PostProcessResult 저장 완료 후:

```text
NEXT_POST_DELAY
```

그 후:

```text
maybe_pause()
```

그리고 다음 게시글.

---

# 16. 권장 순서

```python
result = processor.process(detail_page, post)

history.record_result(result)

if stop_event.is_set():
    break

pacing.wait_next_post()

if stop_event.is_set():
    break

pacing.maybe_pause()
```

---

# 17. 한 글에서 여러 random pause 금지

긴 random pause는 게시글당 최대 1회.

짧은 action delay는 여러 번 가능.

---

# 18. 사용자 승인 상태에는 pacing 개입 금지

```text
WAITING_USER
```

상태에서는 사용자가 직접 시간을 사용하므로 추가 random delay가 불필요하다.

---

# 19. AI API 기능은 이번 단계에서 제외

다음 파일/기능은 만들지 않는다.

```text
Gemini API client
Google API key
OpenAI client
HTTP AI request
API key 설정 UI
token usage
AI response parser
```

---

# 20. 대신 Gemini Clipboard Assistant 구현

목표:

```text
현재 게시글
 ↓
제목/본문 일부 추출
 ↓
Gemini용 프롬프트 자동 생성
 ↓
[프롬프트 복사]
 ↓
[Gemini 열기]
 ↓
사용자가 Gemini에 붙여넣음
 ↓
Gemini 댓글 복사
 ↓
[클립보드 댓글 적용]
 ↓
현재 Naver 댓글 editor 교체
 ↓
사용자 수정
 ↓
Enter
```

---

# 21. 외부 Gemini 페이지 자동 조작 금지

다음은 하지 않는다.

```text
Gemini DOM selector
Gemini textarea 자동 fill
Gemini submit 자동 click
Gemini response scraping
```

Gemini는 사용자 수동 영역.

---

# 22. Gemini 열기

Python 표준 라이브러리:

```python
import webbrowser

webbrowser.open(
    "https://gemini.google.com/"
)
```

버튼:

```text
Gemini 열기
```

---

# 23. 게시글 Context 추출

신규:

```text
naver/content_extractor.py
```

목표:

```text
title
excerpt
```

만 가져온다.

전체 글을 AI 프롬프트에 복사하지 않는다.

---

# 24. FeedPost 확장

기존 FeedPost에 optional field 추가.

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

    excerpt: str | None = None
```

---

# 25. title 사용 우선순위

이미 Feed Source에서 title을 가져온다면 먼저 사용.

없으면 상세 페이지에서 title 추출.

---

# 26. excerpt 목표 길이

기본:

```text
400 ~ 900 characters
```

권장 default:

```text
700 chars
```

config:

```json
"ai_context_max_chars": 700
```

---

# 27. ContentContextExtractor

예:

```python
@dataclass
class PostContext:
    title: str = ""
    excerpt: str = ""
```

```python
class ContentContextExtractor:

    @classmethod
    def extract(cls, page, post):
        title = post.title or cls.extract_title(page)
        excerpt = cls.extract_excerpt(page)

        return PostContext(
            title=title,
            excerpt=excerpt
        )
```

---

# 28. 본문 selector

실제 모바일 DOM을 우선 확인한다.

runtime에서 임의로:

```text
body.innerText 전체
```

를 복사하는 방식은 피한다.

resolver에:

```text
get_post_title()
get_post_content()
```

를 추가.

Selector는 현재 라이브 DOM 실측 후 확정한다.

---

# 29. Extractor fallback

본문 영역을 찾지 못하면:

```text
title only
```

로 prompt 생성.

본문 전체 body text fallback은 사용하지 않거나 마지막 debug fallback으로만 둔다.

---

# 30. excerpt 정제

다음 텍스트 제거 시도:

```text
공감
댓글
공유
이웃추가
NAVER
메뉴
작성자 profile UI
댓글 목록
```

단, 무리한 NLP 구현 필요 없음.

본문 DOM을 정확히 찾는 것이 우선.

---

# 31. AIPromptBuilder

신규:

```text
services/ai_prompt.py
```

AI API가 아니라 **문자열 생성기**다.

---

# 32. 기본 Prompt

```text
아래 네이버 블로그 글에 달 댓글 초안을 하나 작성해줘.

목표:
글을 실제로 읽은 사람이 남긴 것처럼 자연스럽고 구체적인 댓글.

조건:
- 1~3문장
- 글 제목이나 본문에 실제로 나온 구체적인 포인트를 최소 1개 언급
- 과도한 칭찬 금지
- 광고성 표현 금지
- "좋은 정보 감사합니다", "잘 보고 갑니다"처럼 어느 글에나 붙일 수 있는 표현은 피하기
- 억지 질문은 하지 않기
- 실제로 궁금할 만한 내용이 있을 때만 짧은 질문 1개까지 허용
- 이모지는 0~1개
- 작성자 이름을 억지로 부르지 않기
- 댓글 본문만 출력

제목:
{title}

본문 일부:
{excerpt}
```

---

# 33. 제목만 있는 경우 Prompt

```text
본문 일부를 읽지 못했으므로 제목에 없는 구체적인 사실을 만들어내지 말 것.
```

문구 추가.

AI hallucination 완화.

---

# 34. AI Prompt Tone

V1에서는 옵션 3개만 추천.

```text
자연스러운 감상
구체적인 공감
짧은 질문 포함 가능
```

config:

```json
"ai_prompt_style": "natural"
```

그러나 UI가 복잡하면 V1에서는 단일 natural prompt만 구현 가능.

---

# 35. AI Assistant 설정

config 추가:

```json
{
  "ai_clipboard_enabled": true,
  "ai_context_max_chars": 700,
  "ai_prompt_style": "natural"
}
```

---

# 36. AI 도움 기능은 optional

사용자가 끄면 기존:

```text
Spintax
→ editor fill
→ Enter
```

흐름 그대로.

---

# 37. 기본 댓글 초안은 유지

Gemini 도움 기능이 켜져 있어도 기존 Spintax 초안을 먼저 입력한다.

이유:

```text
Gemini를 쓰지 않아도 작업 가능
Gemini가 늦어도 workflow 중단 없음
```

---

# 38. AI Prompt 준비 시점

상세 페이지 진입 후.

권장:

```text
detail_page.goto()
 ↓
Context extract
 ↓
AI prompt build
 ↓
Like
 ↓
Comment draft
```

또는 Like와 병렬할 필요 없음.

---

# 39. Runtime State 확장

`BotRuntimeState`에 다음 optional fields 추가.

```python
current_post_title: str = ""
current_post_excerpt: str = ""
current_ai_prompt: str = ""
ai_clipboard_ready: bool = False
```

UI는 StateManager를 통해 읽는다.

---

# 40. Playwright thread affinity

매우 중요.

Tkinter UI thread에서:

```python
detail_page.locator(...)
editor.fill(...)
```

를 직접 실행하지 않는다.

현재 Browser/Playwright는 Worker Thread 소유.

따라서 클립보드 댓글 적용은 Worker로 명령을 보내야 한다.

---

# 41. Command Queue 추가

신규:

```text
services/clipboard_bridge.py
```

실제로는 thread-safe WorkerCommand queue 역할.

---

# 42. Command 모델

`app/models.py`:

```python
class WorkerCommandType(str, Enum):
    APPLY_CLIPBOARD_COMMENT = "apply_clipboard_comment"
```

```python
@dataclass
class WorkerCommand:
    kind: WorkerCommandType
    text: str = ""
```

---

# 43. Controller/Processor command queue

MainWindow 생성 시:

```python
queue.Queue()
```

생성.

FeedController/PostProcessor/CommentInteractionService에 전달.

---

# 44. UI의 “클립보드 댓글 적용”

UI thread는 OS clipboard만 읽는다.

```python
text = self.clipboard_get()
```

그 다음:

```python
command_queue.put(
    WorkerCommand(
        kind=APPLY_CLIPBOARD_COMMENT,
        text=text
    )
)
```

Playwright를 직접 건드리지 않는다.

---

# 45. wait_for_user_action 확장

현재:

```text
Enter
Esc
Stop
```

폴링.

여기에 WorkerCommand queue 확인 추가.

흐름:

```python
while True:
    if stop:
        return STOP

    process_pending_commands()

    action = page.evaluate(...)

    if action:
        return ...
```

---

# 46. APPLY_CLIPBOARD_COMMENT 처리

Worker Thread에서:

```python
editor = MobileDOMResolver.get_comment_editor(page)

editor.fill(command.text.strip())
editor.focus()
```

그리고 WAITING_USER 계속 유지.

Enter를 자동 발생시키지 않는다.

---

# 47. 클립보드 내용 empty

적용 금지.

UI:

```text
클립보드에 댓글 텍스트가 없습니다.
```

---

# 48. AI 결과 적용 후 fixed suffix

설정 추가 권장:

```json
"append_fixed_suffix_to_ai": false
```

기본 false.

이유:

Gemini 결과에 자연스러운 마무리가 이미 있을 수 있음.

사용자가 원하면 true.

---

# 49. fixed suffix 적용 helper

```python
DraftService.compose_body_and_suffix(
    body,
    suffix,
    append=True
)
```

AI / template 공통.

중복 suffix 방지.

---

# 50. AI 결과 적용 시 trim

```text
leading/trailing whitespace 제거
```

markdown code fence가 들어오면 사용자에게 그대로 보여도 되나,
간단하게 다음만 제거 가능.

```text
``` 
```

그러나 과도한 자동 후처리는 하지 않는다.

---

# 51. “프롬프트 복사” 버튼

UI State에 저장된:

```text
current_ai_prompt
```

를 clipboard에 넣음.

Tkinter:

```python
clipboard_clear()
clipboard_append(prompt)
update()
```

---

# 52. “Gemini 열기” 버튼

```text
webbrowser.open()
```

Playwright session 내부에 Gemini tab을 만들지 않는다.

Feed Assistant browser와 외부 AI browser state 분리.

---

# 53. “클립보드 댓글 적용” 버튼

WAITING_USER 상태에서만 활성.

다른 상태:

```text
disabled
```

---

# 54. AI Helper UI

댓글 Template 섹션 아래 추가.

```text
AI 댓글 도움

☑ Gemini 복붙 도우미 사용

현재 글
제목: ...

본문 일부
...

[ AI 프롬프트 복사 ]
[ Gemini 열기 ]
[ 클립보드 댓글 적용 ]
```

---

# 55. excerpt UI

전체 700자를 그대로 크게 표시하면 UI 복잡.

2~4줄 preview.

버튼:

```text
본문 일부 보기
```

는 V2.

V1은 제목만 표시해도 됨.

---

# 56. 상태 메시지

AI prompt 준비:

```text
댓글 초안 준비 완료 — 필요하면 Gemini 프롬프트를 복사하세요.
```

AI 결과 적용:

```text
Gemini 댓글을 댓글창에 적용했습니다. 수정 후 Enter를 눌러주세요.
```

---

# 57. CommentInteractionService 수정

새 메서드 권장:

```python
@staticmethod
def replace_editor_text(page, text):
    editor = MobileDOMResolver.get_comment_editor(page)

    if editor.count() == 0:
        return False

    editor.fill(text)
    editor.focus()
    return True
```

---

# 58. final text 정책 수정

현재 코드가:

```python
cmt_res.submitted_text = final_text or draft_text
```

형태라면 변경.

사용자가 댓글을 전부 지운 상태에서 Enter했을 때 기존 draft로 몰래 되돌려 제출하면 안 된다.

정책:

```text
final_text empty
→ submit 금지
→ WAITING_USER 유지
```

---

# 59. 빈 댓글 Enter

UI/로그:

```text
댓글이 비어 있어 등록하지 않았습니다.
내용을 입력한 뒤 Enter를 눌러주세요.
```

---

# 60. UserAction 확장 여부

Enter를 받았는데 empty일 경우:

```text
SUBMIT
```

을 반환했더라도 Processor가 validation 후 다시 wait loop로 들어감.

---

# 61. PostProcessor AI Context 통합

예:

```python
context = ContentContextExtractor.extract(
    detail_page,
    post,
    max_chars=...
)

post.title = context.title or post.title
post.excerpt = context.excerpt

ai_prompt = AIPromptBuilder.build(
    post=post,
    style=...
)

state_mgr.update(
    current_post_title=post.title,
    current_post_excerpt=post.excerpt,
    current_ai_prompt=ai_prompt,
    ai_clipboard_ready=True
)
```

---

# 62. AI Helper와 History

History에 Gemini prompt를 저장할 필요 없음.

기본적으로 저장:

```text
draft
submitted_text
```

만.

privacy와 파일 증가 방지.

---

# 63. AI 제공 여부 기록

원한다면:

```json
"draft_source": "template"
```

또는:

```json
"draft_source": "clipboard_ai"
```

만 저장.

---

# 64. CommentProcessResult 확장

```python
draft_source: str = "template"
```

AI clipboard 적용되면:

```text
clipboard_ai
```

---

# 65. Pacing History 저장 금지

각 random delay 값을 History에 저장할 필요 없음.

Debug logger에만:

```text
[PACING] next_post wait=3.42s
```

---

# 66. Pacing Log

```text
[PACING] action delay 1.42s
[PACING] next-post delay 3.88s
[PACING] random pause 14.21s
```

---

# 67. Session summary

추가 optional:

```text
랜덤 Pause 2회
총 Pause 27.4초
```

P2.

---

# 68. ConfigService P0 수정

현재 UI에서 Config 저장 시 일부 key만 저장하는 경우
`schema_version`, `browser_mode` 등 기본 필드가 사라질 수 있다.

따라서 `ConfigService.save()`를 partial replacement용으로 사용하지 않는다.

---

# 69. update_many 도입

```python
def update_many(self, values):
    merged = DEFAULT_CONFIG_V2.copy()
    merged.update(self.data)
    merged.update(values)

    merged["schema_version"] = 2

    self._atomic_save(merged)
    self.data = merged
```

UI에서는:

```python
config_service.update_many(cfg_data)
```

사용.

---

# 70. Config atomic write

가능하면:

```text
config.json.tmp
→ replace
```

방식.

---

# 71. 새 config 전체 예시

```json
{
  "schema_version": 2,

  "feed_source": "neighbor",
  "max_feed_items": 20,

  "like_enabled": true,
  "comment_enabled": true,

  "comment_template": "{좋은|유익한|멋진} 포스팅 잘 읽었습니다!",
  "fixed_suffix": "오늘도 좋은 하루 보내세요 :)",
  "append_fixed_suffix_to_ai": false,

  "secret_comment": false,

  "browser_mode": "persistent",
  "direct_urls": [],

  "pacing_enabled": true,
  "action_delay_min": 1.0,
  "action_delay_max": 2.5,

  "next_post_delay_min": 2.0,
  "next_post_delay_max": 5.0,

  "random_pause_enabled": true,
  "random_pause_chance": 0.10,
  "random_pause_min": 8.0,
  "random_pause_max": 20.0,

  "ai_clipboard_enabled": true,
  "ai_context_max_chars": 700,
  "ai_prompt_style": "natural"
}
```

---

# 72. GUI 레이아웃 우선순위

현재 화면이 이미 길기 때문에 모든 항목을 한 줄에 늘어놓지 않는다.

권장 Card 순서:

```text
1. 피드 대상
2. 작업 옵션
3. 댓글 설정
4. AI 댓글 도움
5. 작업 속도
6. 상태
7. 작업 버튼
8. 로그
```

---

# 73. 작업 속도 Card

별도 Frame.

초기 화면에서 한눈에 보이되 Advanced 수준으로 과도하게 복잡하지 않게.

---

# 74. 퍼즈라는 이름

UI에서는:

```text
랜덤 Pause
```

또는:

```text
중간 휴지
```

사용.

설명:

```text
게시글 처리 사이에 가끔 긴 대기 시간을 둡니다.
```

---

# 75. 테스트 — PacingService

반드시 unit test.

```text
min/max 범위 안
min > max normalize
chance=0 pause 없음
chance=1 항상 pause
stop_event 즉시 interrupt
disabled면 0초
```

실제 test에서는 sleep이 길어지지 않도록 random/interruptible_wait mock.

---

# 76. 테스트 — PromptBuilder

```text
title + excerpt 포함
excerpt 없을 때 안내 문구
max chars 준수
댓글 only 요청 포함
```

---

# 77. 테스트 — Clipboard command

```text
empty clipboard reject
command queue 전달
worker thread에서 fill
Enter 자동 발생 안 함
```

---

# 78. 테스트 — final empty

```text
editor empty + Enter
→ submit button click 0회
```

---

# 79. 테스트 — AI disabled

기존 template workflow가 변경되지 않아야 함.

---

# 80. 테스트 — Stop during random pause

```text
20초 pause 시작
→ Stop
→ 즉시 종료
```

---

# 81. 테스트 — Stop during next post delay

동일.

---

# 82. 테스트 — Stop while AI 사용

WAITING_USER에서 Stop 가능.

Gemini 외부 창은 앱이 닫지 않는다.

---

# 83. Gemini browser ownership

Feed Assistant 종료 시:

```text
Gemini 브라우저 창 자동 종료 금지
```

사용자의 일반 browser로 열었기 때문.

---

# 84. Prompt Privacy

프롬프트는:

```text
글 제목
본문 일부
```

를 포함한다.

UI에:

```text
AI 프롬프트에는 현재 글 제목과 본문 일부가 포함됩니다.
```

짧게 안내 가능.

---

# 85. Prompt에 작성자 개인정보 최소화

author는 기본 prompt에 넣지 않는다.

필요 없음.

---

# 86. 본문 이미지 분석

이번 단계 제외.

---

# 87. OCR

제외.

---

# 88. 댓글 카테고리 자동분류

이번 V1에서는 별도 로직으로 만들 필요 없음.

Gemini prompt에서 본문을 보고 문맥에 맞게 작성하도록 지시.

---

# 89. 제목 활용

반드시 Prompt에 title 포함.

제목과 excerpt가 모순이면 excerpt를 우선하라는 별도 지시 필요 없음.

---

# 90. generic comment 억제

Prompt에 다음 조건 포함.

```text
어느 글에나 붙일 수 있는 표현을 피할 것
본문의 구체적인 대상/경험/장소/메뉴/팁 중 하나를 언급할 것
```

---

# 91. 억지 질문 억제

```text
답글을 유도하려고 억지 질문을 만들지 말 것
실제로 궁금한 경우만 질문
```

---

# 92. 길이

추천:

```text
1~3 문장
```

너무 길게 요청하지 않는다.

---

# 93. 댓글 톤

추천 Prompt:

```text
친근한 네이버 블로그 댓글
과장되지 않은 대화체
```

---

# 94. fixed suffix 중복

AI 결과에 suffix와 동일한 문장이 이미 있으면 재첨부 금지.

간단한 normalization:

```text
trim
case-insensitive 필요 없음 (한국어 중심)
```

---

# 95. AI 결과가 너무 긴 경우

자동 자르지 않는다.

사용자가 수정.

---

# 96. AI 결과 markdown

Gemini가:

```text
"댓글:"
```

을 붙이는 경우가 있을 수 있다.

Prompt에서:

```text
댓글 본문만 출력
```

강하게 지시.

자동 parser는 V1 보류.

---

# 97. Pacing과 브라우저 navigation

`page.goto()` 호출 **직전**에 next-post delay를 걸 수도 있으나,
Controller에서 이전 post 완료 후 기다리는 방식이 더 단순.

중복 대기 금지.

---

# 98. 첫 글

첫 글 진입 전 next-post delay는 기본적으로 없음.

source open 이후 첫 post는 바로 처리.

원하면 별도 initial delay V2.

---

# 99. 마지막 글

마지막 post 완료 후:

```text
next_post_delay
random pause
```

실행할 필요 없음.

Controller는 다음 target 존재 여부 확인 후 대기.

---

# 100. DirectUrlSource

동일 pacing 적용.

---

# 101. RecommendationFeedSource

동일 pacing 적용.

---

# 102. FeedList load_more

새 post가 없어 load_more할 때 짧은 UI wait는 기존 condition을 유지.

랜덤 long pause를 매 scroll마다 발생시키지 않는다.

---

# 103. Pacing 단일 책임

PacingService는:

```text
언제 wait할지 결정하지 않는다.
얼마나 wait할지 + wait 실행
```

Controller/Processor가 “언제”를 결정.

---

# 104. Random seed

Production에서 fixed seed 사용 금지.

Tests에서만 seed/mock.

---

# 105. GUI 설정 저장

작업 시작 시 현재 UI 값을 config에 merge.

AI/Pacing 설정도 함께 저장.

---

# 106. GUI 재실행

저장된 Pacing/AI 설정 복원.

---

# 107. Invalid config recovery

JSON에 잘못된 문자열 등이 들어와도 default로 fallback.

---

# 108. 수치 clamp

Service에서 한번 더 방어.

GUI validation만 믿지 않는다.

---

# 109. Comment WAIT loop command processing

Pseudo:

```python
while True:

    if stop_event.is_set():
        return STOP

    while not command_queue.empty():
        command = command_queue.get_nowait()

        if command.kind == APPLY_CLIPBOARD_COMMENT:
            replace_editor_text(page, command.text)
            state_mgr.update(
                message="클립보드 댓글을 적용했습니다. 수정 후 Enter를 눌러주세요."
            )

    browser_action = read_keyboard_action()

    if browser_action:
        return browser_action

    time.sleep(0.1)
```

---

# 110. keyboard action reset

AI clipboard 적용 후 기존:

```text
window.__NAVER_FEED_ACTION__
```

값이 이전 SUBMIT/SKIP 상태로 남지 않도록 reset 정책 확인.

새 post prepare 시 항상 null.

---

# 111. AI apply 이후 focus

반드시:

```python
editor.focus()
```

사용자가 바로 수정 가능.

---

# 112. Browser foreground

AI 결과 적용 시 detail page를 foreground로:

```python
page.bring_to_front()
```

검토.

사용자가 Gemini에서 돌아오지 않아도 버튼 누르면 Naver 댓글창이 앞으로 와서 수정 가능.

---

# 113. Gemini open button 후 Feed Assistant GUI

GUI는 계속 유지.

---

# 114. 버튼 활성 상태

```text
AI 프롬프트 복사:
current_ai_prompt 존재 시 활성

Gemini 열기:
항상 활성 또는 AI enabled일 때

클립보드 댓글 적용:
WAITING_USER일 때만
```

---

# 115. 작업 종료 시 AI UI reset

```text
current title = ""
prompt = ""
buttons disabled
```

---

# 116. Post skip 시 UI reset

다음 글 context로 교체.

---

# 117. State race

UI state update는 기존 `after(0, ...)` 사용.

---

# 118. Clipboard encoding

한국어 Unicode 정상 유지.

---

# 119. newline

Gemini 2~3문장 결과의 줄바꿈 유지 가능.

---

# 120. suffix append

AI apply 이후:

```python
body = clipboard_text.strip()

if append_fixed_suffix_to_ai:
    body = DraftService.append_suffix(...)
```

---

# 121. 테스트 fixture

본문 추출을 위해 모바일 post HTML fixture 추가.

```text
tests/fixtures/mobile_post_content.html
```

---

# 122. 실제 DOM 미확정 부분

본문 title/content selector는 반드시 라이브 inspect 후 확정.

이 작업에서 임의 hashed class 하나만 primary로 박지 말 것.

---

# 123. diagnostics 확장

`inspect_post.py`에:

```text
post title candidates
content container candidates
text length
first 1,000 chars
```

출력 추가.

---

# 124. Content extractor fallback confidence

```python
class ExtractionConfidence(Enum):
    HIGH
    MEDIUM
    LOW
```

까지는 V2.

V1은 title/excerpt empty 처리로 충분.

---

# 125. AI prompt fallback

excerpt empty:

```text
제목 기반으로만 작성하되,
제목에 없는 사실을 만들어내지 말 것.
```

---

# 126. current branch config issue 반드시 수정

현재 ConfigService 기본 스키마는 v2지만,
UI가 `save(partial_dict)`를 호출하면 기존 필드가 사라질 수 있다.

따라서 이번 작업의 첫 커밋에서
ConfigService merge-save를 먼저 고친다.

---

# 127. 구현 순서

## Phase P0 — Config integrity

- `update_many`
- atomic save
- schema_version 보존
- browser_mode 보존
- tests

GO 조건:

```text
UI 저장 후 기존 config key 소실 0
```

---

# 128. Phase P1 — PacingService

- config fields
- service
- UI
- validation
- tests

GO:

```text
모든 wait interruptible
범위 준수
```

---

# 129. Phase P2 — Controller pacing integration

- next post delay
- random pause
- safe state only

GO:

```text
WAITING_USER / VERIFYING pause 없음
Stop 즉시
```

---

# 130. Phase P3 — Content extraction

- post title
- excerpt
- max chars
- diagnostics
- fixture tests

GO:

```text
최소 title 안정 추출
본문 실패해도 workflow 지속
```

---

# 131. Phase P4 — AI PromptBuilder

- prompt
- title/excerpt
- test

GO:

```text
API dependency 0
```

---

# 132. Phase P5 — UI Gemini helper

- Prompt copy
- Gemini open
- context display
- clipboard result apply button

GO:

```text
UI thread Playwright access 0
```

---

# 133. Phase P6 — Command Queue

- WorkerCommand
- queue
- WAIT loop integration
- replace editor
- focus

GO:

```text
clipboard text 적용 후 Enter 전 submit 0
```

---

# 134. Phase P7 — Empty final validation

- empty submit block
- re-wait

GO:

```text
빈 댓글 제출 0
```

---

# 135. Phase P8 — Regression

Neighbor
Recommendation
Direct URL
Template-only
AI clipboard
Stop during pause
Stop during user wait

모두 테스트.

---

# 136. 권장 Commit

```text
fix: preserve config schema during ui updates
feat: add interruptible pacing service
feat: add configurable next-post delay and random pause
feat: extract mobile post title and excerpt context
feat: add local gemini prompt builder
feat: add gemini clipboard helper ui
feat: apply clipboard comments through worker command queue
fix: block empty comment submission
test: cover pacing and clipboard assistant workflows
docs: document manual gemini assistant workflow
```

---

# 137. 구현 금지 shortcut

다음 방식으로 빨리 구현하지 말 것.

```text
ui button에서 detail_page 직접 접근
time.sleep(random)
controller 곳곳에서 random.uniform 중복
Gemini page Playwright 자동화
body.innerText 전체 AI prompt 복사
AI clipboard 결과 자동 Enter
final_text empty면 기존 draft 대신 제출
config save에서 dict 전체 교체
```

---

# 138. Acceptance — Pacing

- [ ] 사용자 min/max 설정
- [ ] 다음 글 min/max 별도
- [ ] random Pause on/off
- [ ] Pause chance
- [ ] Pause min/max
- [ ] Stop interrupt
- [ ] 현재 대기 상태 UI 표시
- [ ] 마지막 글 후 불필요한 pause 없음
- [ ] WAITING_USER에서 pause 없음
- [ ] VERIFYING에서 pause 없음

---

# 139. Acceptance — Gemini helper

- [ ] API 없음
- [ ] post title prompt 포함
- [ ] excerpt prompt 포함
- [ ] excerpt 실패 시 title-only
- [ ] Prompt copy
- [ ] Gemini open
- [ ] clipboard result apply
- [ ] worker thread에서 editor fill
- [ ] user final edit 가능
- [ ] Enter 전 자동 submit 없음
- [ ] AI disabled 시 기존 workflow 정상

---

# 140. Acceptance — Comment Quality

Prompt 조건:

- [ ] 구체적 내용 언급
- [ ] 범용 복붙 표현 억제
- [ ] 과도한 칭찬 억제
- [ ] 광고 표현 억제
- [ ] 억지 질문 억제
- [ ] 1~3문장
- [ ] 댓글만 출력
- [ ] title-only에서 사실 창작 금지

---

# 141. Acceptance — Config

- [ ] schema_version 유지
- [ ] 기존 v2 field 유지
- [ ] pacing defaults
- [ ] ai clipboard defaults
- [ ] partial UI save 시 필드 유실 없음
- [ ] invalid ranges validation

---

# 142. Acceptance — Threading

- [ ] Tkinter UI thread는 Playwright 접근 안 함
- [ ] Worker만 Playwright 소유
- [ ] Queue thread-safe
- [ ] UI update는 after()
- [ ] Stop deadlock 없음

---

# 143. 최종 사용자 흐름

```text
[작업 시작]

추천/이웃 글 수집
    ↓
글 열기
    ↓
공감 처리
    ↓
댓글 기본 초안
    ↓
현재 글 제목/본문 일부 준비

┌───────────────────────────┐
│ AI 도움 필요 없음          │
│ → 직접 수정 → Enter       │
└───────────────────────────┘

또는

┌───────────────────────────┐
│ AI 프롬프트 복사           │
│ Gemini 열기                │
│ Gemini에 붙여넣기          │
│ 결과 복사                  │
│ 클립보드 댓글 적용         │
│ 직접 수정                  │
│ Enter                      │
└───────────────────────────┘

    ↓
등록 검증
    ↓
History
    ↓
다음 글 대기 2~5초
    ↓
일정 확률 Random Pause
    ↓
다음 글
```

---

# 144. 최종 제품 원칙

AI는 댓글을 **제안**한다.

프로그램은 댓글을 **준비**한다.

사용자가 댓글을 **결정**한다.

Enter가 최종 승인이다.

랜덤 pacing은 Human-in-the-loop 작업 세션의
화면 전환과 작업 템포를 조절하는 보조 기능이다.

---

# 145. Claude/Codex 실행 지시

다음 문구를 그대로 구현자에게 전달해도 된다.

```text
현재 refactor/mobile-feed-assistant 브랜치를 기준으로 작업한다.

MASTER_SPEC.md의 기존 Human-in-the-loop 원칙을 유지하고,
이번 WORK_ORDER의 P0~P8을 순서대로 구현한다.

AI API는 절대로 추가하지 않는다.
Gemini 웹 UI를 Playwright로 자동화하지 않는다.

먼저 ConfigService의 partial save 데이터 유실 문제부터 수정한다.

그 다음 PacingService를 단일 Source of Truth로 만들고
다음 글 이동 간격과 random Pause를 구현한다.
모든 wait는 interruptible이어야 한다.

그 다음 상세 게시글에서 제목과 본문 일부를 추출하고,
Gemini에 수동으로 붙여넣을 prompt를 생성한다.

GUI에는:
- AI 프롬프트 복사
- Gemini 열기
- 클립보드 댓글 적용
을 추가한다.

클립보드 댓글 적용 시 UI Thread가 Playwright를 직접 호출하면 안 된다.
thread-safe command queue를 이용해 Worker Thread에서
현재 comment editor의 텍스트를 교체하고 다시 focus한다.

어떤 경우에도 Gemini 결과를 자동 등록하지 않는다.
사용자가 브라우저에서 Enter를 눌렀을 때만 submit한다.

빈 최종 댓글은 submit하지 않고 다시 사용자 입력 상태로 돌아간다.

각 Phase마다:
- 변경 파일
- 테스트
- Acceptance 결과
- 남은 리스크
- GO/NO-GO
를 보고한다.
```

---

# END
