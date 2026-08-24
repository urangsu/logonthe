# NAVER FEED ASSISTANT
# EXISTING CHROME GEMINI + WARM COMMENT + SOURCE SUFFIX MASTER FIX ORDER

> 대상 저장소: `urangsu/logonthe`
> 기준 브랜치: `main` 최신 상태를 분석한 뒤 `refactor/mobile-feed-assistant`에서 수정
> 목적:
> 1. 사용자가 평소 사용 중이며 Google 로그인이 이미 되어 있는 **일반 Google Chrome의 기존 Gemini 탭을 실제로 재사용**
> 2. 네이버 글 제목/본문이 Gemini 프롬프트에 실제로 포함되는지 검증 가능한 구조로 수정
> 3. Gemini 댓글을 딱딱한 분석형 문장이 아니라 **짧고 정감 있는 칭찬형 댓글**로 변경
> 4. `Recommendation.naver`에서는 일반 꼬리말 대신 **“제 블로그에도 놀러 와주세요” 계열 전용 꼬리말**을 자동 사용
> 5. 추천용 꼬리말은 사용자가 UI에서 수정하거나 완전히 비울 수 있음
> 6. 기존 Spintax / 일반 꼬리말 / Human-in-the-loop Enter 승인 / Pacing 기능은 유지
>
> AI API는 사용하지 않는다.
> Gemini 웹페이지에서 생성된 댓글도 네이버에 자동 등록하지 않는다.
> 네이버 최종 댓글 등록은 기존대로 사용자 Enter 승인으로만 수행한다.

---

# 0. 이번 수정의 핵심 결론

현재 코드의 “기존 Gemini 탭 재사용” 구현은 이름과 실제 동작이 다르다.

현재 `BrowserSession`은 기본적으로:

```text
data/user_profile
```

을 사용하는 Playwright Persistent Context를 새로 실행한다.

그 뒤:

```python
for p in self.context.pages:
    if "gemini.google.com" in p.url:
        ...
```

으로 Gemini를 찾는다.

여기서 `self.context.pages`는 **Playwright가 현재 관리하는 BrowserContext 내부 탭들만** 뜻한다.

사용자가 평소 사용 중인:

```text
Google Chrome
├─ 네이버
├─ 유튜브
├─ Gemini ← 이미 Google 로그인되어 있는 이 탭
└─ 기타 탭
```

은 이 Context에 포함되지 않는다.

따라서 “기존 열린 Gemini 탭 검색” 코드를 아무리 개선해도
BrowserSession이 일반 Chrome에 실제 연결되지 않는 이상 해당 탭은 보이지 않는다.

이 문제를 먼저 바로잡지 않고 Gemini selector만 수정하는 작업은 중단한다.

---

# 1. 현재 코드에서 확인된 정확한 문제

## 1.1 BrowserSession에는 `cdp_url`이 있으나 Controller가 사용하지 않음

현재 `BrowserSession.__init__()`은:

```python
cdp_url: Optional[str] = None
```

을 받을 수 있다.

그러나 `FeedController.run()`은:

```python
self.session = BrowserSession(headless=False)
```

만 호출한다.

즉:

```text
cdp_url = None
```

이며 CDP 연결 코드는 실제 실행되지 않는다.

결과:

```text
“기존 Chrome 탭 감지”
```

기능은 사용자의 Chrome이 아니라 프로그램 전용 Persistent Context 탭만 탐색한다.

---

# 2. 일반 Chrome은 Playwright가 자동으로 볼 수 없음

사용자가 이미 평소 실행해 둔 일반 Chrome은
Playwright가 임의로 attach할 수 있는 대상이 아니다.

Playwright `connect_over_cdp()`를 사용하려면 Chrome이 애초에:

```text
remote debugging
```

이 활성화된 상태로 실행되어 있어야 한다.

이미 일반 방식으로 실행 중인 Chrome에
나중에 `cdp_url=9222`만 지정한다고 기존 탭이 갑자기 보이는 것은 아니다.

따라서 사용자의 요구:

> 지금 내가 평소 사용하고 있고 로그인되어 있는 Gemini 탭을 그대로 써라.

를 가장 정확히 만족시키기 위해 macOS에서는
Gemini 제어를 Naver용 Playwright BrowserSession과 분리한다.

---

# 3. 최종 브라우저 구조

다음 구조로 변경한다.

```text
Naver Automation
└─ Playwright BrowserSession
   ├─ feed_page
   └─ detail_page


Gemini Assistant
└─ ExistingChromeGeminiBridge
   └─ 사용자가 평소 쓰는 Google Chrome
      └─ 이미 열려 있는 gemini.google.com 탭
```

중요:

```text
Naver용 브라우저와 Gemini용 브라우저는 동일 Context일 필요가 없다.
```

Gemini 결과는 문자열이므로
OS clipboard 또는 Python 반환값으로 두 영역을 연결하면 된다.

---

# 4. Gemini Bridge 추상화

기존 `GeminiWebBridge`를 직접 PostProcessor에서 호출하는 구조를 수정한다.

신규 인터페이스:

```python
from typing import Protocol, Optional

class GeminiBridge(Protocol):
    def is_available(self) -> bool:
        ...

    def generate_comment(
        self,
        prompt: str,
        stop_event=None
    ) -> Optional[str]:
        ...

    def get_status(self) -> str:
        ...
```

구현체:

```text
ExistingChromeGeminiBridge
ManagedPlaywrightGeminiBridge
```

이번 사용자의 기본값:

```text
ExistingChromeGeminiBridge
```

ManagedPlaywright는 fallback/legacy 선택지로만 남긴다.

---

# 5. macOS ExistingChromeGeminiBridge

신규 파일 권장:

```text
services/gemini_existing_chrome.py
```

사용:

```text
osascript
Google Chrome AppleScript
System Events
pbcopy / pbpaste
```

---

# 6. 왜 AppleScript Bridge인가

사용자가 원하는 것은:

```text
“프로그램이 만든 테스트용 크롬”
```

이 아니라:

```text
“내가 원래 켜놓은 로그인된 크롬 탭”
```

이다.

macOS에서는 AppleScript로 현재 Google Chrome의:

```text
window
tab
title
URL
active tab
```

을 찾고 활성화할 수 있다.

Gemini DOM 접근까지 하려면
Chrome의 JavaScript from Apple Events 권한이 필요할 수 있다.

이 권한이 없으면 프로그램은 조용히 다른 브라우저를 여는 것이 아니라:

```text
Gemini 기존 Chrome DOM 접근 권한이 없습니다.
```

라고 명확히 중단한다.

---

# 7. Existing Chrome 연결 테스트 기능

GUI에 새 버튼 추가:

```text
[ Gemini 기존 탭 연결 테스트 ]
```

테스트는 다음을 수행한다.

```text
1. Google Chrome 프로세스 실행 여부
2. 모든 window/tab URL 검색
3. gemini.google.com 포함 탭 탐색
4. 해당 탭 title / URL 확인
5. DOM JavaScript 실행 가능 여부 확인
6. 결과 GUI 표시
```

성공:

```text
● Gemini 기존 Chrome 연결됨
  Gemini - Google
  https://gemini.google.com/app/...
```

실패:

```text
○ Gemini 탭을 찾지 못했습니다.
```

권한 문제:

```text
⚠ Chrome Apple Events JavaScript 접근 권한이 필요합니다.
```

---

# 8. 절대 Silent Fallback 하지 말 것

현재 CDP 코드는 실패하면:

```text
CDP 연결 실패
→ Persistent Context로 전환
```

한다.

이 동작은 사용자를 혼란스럽게 만든다.

사용자가:

```text
기존 Chrome 사용
```

을 선택했다면 실패 시:

```text
기존 Chrome 연결 실패
```

로 끝내야 한다.

새 Playwright Chrome을 몰래 열지 않는다.

---

# 9. Browser Mode config

추가:

```json
{
  "gemini_browser_mode": "existing_chrome_mac"
}
```

허용값:

```text
existing_chrome_mac
managed_playwright
```

CDP는 Advanced 옵션으로 보류 가능.

---

# 10. UI Gemini 브라우저 설정

현재:

```text
Gemini 대화 모드
- 새 대화
- 지정 URL
```

위에 다음을 추가.

```text
Gemini 사용 브라우저

● 현재 켜져 있는 일반 Chrome의 Gemini 탭
○ 프로그램 전용 브라우저
```

기본:

```text
현재 켜져 있는 일반 Chrome
```

---

# 11. 기존 Gemini 탭 발견 정책

모든 Google Chrome window/tab 탐색.

조건:

```python
"gemini.google.com" in url
```

여러 개 있으면:

1. 현재 active tab이 Gemini면 그것 사용
2. 아니면 가장 앞쪽 window의 Gemini
3. 그래도 여러 개면 마지막 사용 탭 또는 첫 발견

로그:

```text
[GEMINI/EXTERNAL] 기존 Gemini 탭 발견:
window=1 tab=4 url=...
```

---

# 12. 현재 Gemini 대화 보존

현재 `GeminiWebBridge.ensure_open()`은
기존 Gemini 탭이 있어도 설정된 custom URL과 다르면:

```python
page.goto(target_url)
```

한다.

이 동작은 기존 대화를 덮어버릴 수 있다.

새 ExistingChrome 모드에서는:

```text
이미 Gemini 탭 존재
→ 절대 자동 navigation 하지 않음
```

정책.

사용자가 켜놓은 현재 대화를 그대로 사용한다.

---

# 13. 새 대화가 필요한 경우

이번 기본은:

```text
기존 대화 유지
```

사용자가 별도로 “새 대화”를 선택했을 때만
Gemini UI에서 새 대화 URL로 이동.

초기 기본은 기존 conversation reuse.

---

# 14. Existing Chrome prompt 전송 방식

DOM에 직접 `innerHTML`을 주입하는 방식을 기본으로 사용하지 않는다.

Gemini는 Angular/Quill/ContentEditable 상태를 내부적으로 관리하기 때문에
DOM만 바뀌고 application state가 바뀌지 않을 수 있다.

권장 방식:

```text
1. prompt를 OS clipboard에 저장
2. Gemini 입력 editor를 JS로 focus
3. System Events로 실제 Cmd+V
4. 실제 keyboard Enter
```

즉:

```text
DOM은 위치 찾기와 focus에 사용
입력은 실제 keyboard paste 사용
```

---

# 15. 입력 전 검증

Prompt를 보내기 전:

```text
Gemini 입력 editor 발견
```

확인.

없으면:

```text
ERROR
```

다른 contenteditable에 무작정 입력 금지.

---

# 16. Prompt 전송 후 검증

전송 직전에:

```text
existing_response_count
```

를 기록.

전송 후 반드시:

```text
response_count > before_count
```

또는 신규 model response element 생성

을 확인.

이 신호 없이 이전 답변을 새로운 답변이라고 가져오지 않는다.

---

# 17. 현재 gemini_web.py의 답변 추출 결함

현재 코드는 전송 전에 기존 response count를 저장하지 않는다.

따라서 기존 Gemini 대화에 이미 답변이 있으면:

```text
old answer
```

을 읽고 1.5초 안정화 후 새 답변처럼 판단할 가능성이 있다.

반드시:

```python
before_response_ids = ...
before_count = ...
```

저장.

새 response가 실제로 생긴 이후에만 extraction 시작.

---

# 18. Response Selector 개선

현재처럼:

```text
model-response
div.response-container
message-content
div.markdown
...
```

를 각각 순회한 뒤
마지막으로 읽힌 문자열을 선택하는 구조는 폐기.

대신:

```text
모든 response 후보
→ 실제 DOM 순서로 정렬
→ visible
→ Gemini model response 영역인지 확인
→ 가장 마지막 신규 response
```

선택.

---

# 19. 이전 답변 재복사 방지

각 요청마다:

```text
request_id
before count
after count
latest new response
```

추적.

새 응답 확인이 안 되면:

```text
None
```

반환.

클립보드에 기존 답변을 덮어쓰지 않는다.

---

# 20. Streaming 완료 판정

조건:

```text
새 response 존재
AND
텍스트 길이 > 0
AND
최근 1.5~2.0초 동안 텍스트 변화 없음
AND
생성 중지 버튼이 보이지 않음
```

최대 timeout 예:

```text
60초
```

Stop Event 지원.

---

# 21. Gemini 답변 clipboard

답변 문자열을 Python으로 받아:

```text
pbcopy
```

에 기록.

그 뒤 즉시:

```text
pbpaste
```

를 다시 읽어서 같은 문자열인지 검증.

로그:

```text
[GEMINI] OS clipboard verification OK
```

실패:

```text
[GEMINI] clipboard verification failed
```

---

# 22. Naver 글 내용이 Gemini에 전달되지 않는 문제

현재 `ContentContextExtractor`는:

```text
첫 번째로 count > 0인 selector
```

를 바로 선택한다.

문제:

```text
존재하지만 숨겨진 container
텍스트가 거의 없는 wrapper
잘못된 article
```

도 첫 번째로 잡힐 수 있다.

또한 page load 직후 실제 SmartEditor 본문 렌더링이 아직 끝나지 않은 경우도 있다.

---

# 23. Content Extractor 새 정책

`get_post_content_locator()`가 단일 Locator를 즉시 반환하지 않게 한다.

대신 후보를 수집한다.

```text
.se-main-container
.se-viewer
#postViewArea
.post_ct
.post_view
div.post_content
article
```

각 후보에 대해:

```text
visible?
text length?
comment UI 포함?
navigation UI 포함?
```

점수 계산.

---

# 24. 본문 후보 점수 예

```python
score = 0

if visible:
    score += 100

score += min(text_length, 3000)

if "댓글" 영역 비중이 매우 높음:
    score -= 1000

if text_length < 80:
    score -= 500
```

가장 높은 candidate 선택.

---

# 25. 본문 최소 길이

기본:

```text
80자
```

미만이면:

```text
본문 추출 실패/부족
```

로 판단.

단 제목만으로 Gemini 댓글을 만들 수는 있으나
상태와 로그에 명확히 표시.

---

# 26. 본문 렌더링 대기

상세 이동 후 고정 1초만 기다리지 않는다.

최대 몇 초 동안:

```text
meaningful content candidate
```

가 나타나는지 polling.

예:

```text
최대 5초
0.2초 interval
```

Stop interruptible.

---

# 27. Context 로그

Gemini 요청 전에 반드시 다음 로그 출력.

```text
[CONTEXT] title="..."
[CONTEXT] excerpt_chars=623
[CONTEXT] excerpt_preview="..."
```

본문 0자이면:

```text
[CONTEXT] 본문 추출 실패 — 제목 기반 Prompt로 전환
```

---

# 28. GUI Context 미리보기

현재 GUI의:

```text
현재 글
본문 요약
```

에 실제 값을 넣는다.

본문이 없는데:

```text
(작업 시작 시 자동 추출)
```

상태로 남지 않게 한다.

실패:

```text
본문 일부: 추출 실패 (제목만 사용)
```

---

# 29. Gemini prompt 전송 로그

전송 전에:

```text
[GEMINI] prompt_chars=...
[GEMINI] title included=yes
[GEMINI] excerpt included=yes/no
```

출력.

민감도가 높으면 full prompt는 일반 log에 남기지 않는다.

debug mode에서만 full prompt.

---

# 30. 댓글 톤 변경

현재 Prompt는:

```text
꼼꼼히 읽은 사람이
구체적인 포인트
광고성 표현 절대 금지
...
```

와 같이 너무 규칙 중심이라 결과가 딱딱해질 수 있다.

새 기본 톤은:

```text
warm_short
```

---

# 31. 댓글의 목표

다음 느낌을 목표.

```text
짧다
정감 있다
부담 없다
칭찬 중심
본문의 구체적 단어 하나 정도는 언급
질문은 기본적으로 없음
```

---

# 32. 새 Prompt 기본형

```text
아래 네이버 블로그 글에 어울리는 짧은 댓글 하나만 써줘.

댓글 느낌:
- 친근하고 정감 있는 한국어 대화체
- 1~2문장 정도로 짧게
- 글에서 실제로 보이는 장소, 메뉴, 분위기, 경험 중 하나를 자연스럽게 한 번 언급
- 분석하듯 말하지 말고 가볍게 칭찬하거나 공감하는 느낌
- 너무 정중하거나 딱딱한 문어체는 피하기
- “유익한 정보 감사합니다”, “좋은 정보네요”, “잘 보고 갑니다” 같은 매크로 문구는 쓰지 않기
- 억지 질문 금지
- 과장된 감탄 금지
- 이모지는 없어도 되고 사용해도 1개 정도만
- 댓글 본문만 출력

예시 느낌:
“분위기가 정말 편안해 보여서 저도 한번 가보고 싶네요 :)”
“사진 보니까 메뉴가 진짜 맛있어 보여요. 조합도 너무 좋네요!”
“산책하기 정말 좋아 보이네요. 사진도 편안한 느낌이라 좋았어요.”

제목:
{title}

본문 일부:
{excerpt}
```

---

# 33. Prompt에서 예시 그대로 복사 방지

추가:

```text
위 예시 문장을 그대로 사용하지 말고 글 내용에 맞게 새로 작성할 것.
```

---

# 34. 질문 기본 금지

기존:

```text
궁금할 경우 질문 1개 허용
```

을 기본 Prompt에서 제거.

질문은 댓글을 길고 부자연스럽게 만들 가능성이 높음.

---

# 35. 댓글 길이

기본:

```text
1~2문장
```

권장 결과:

```text
대략 25~80자
```

정확한 글자 수 강제는 하지 않아도 됨.

---

# 36. 카테고리별 자연스러운 반응

복잡한 classifier는 만들지 않는다.

Gemini가 본문을 보고 판단.

Prompt에 예시 용도로:

```text
맛집 → 메뉴/맛/분위기
여행 → 장소/풍경/산책/코스
카페 → 공간/음료/분위기
일상 → 경험/사진/공감
```

정도만 안내 가능.

---

# 37. Spintax 기본 문구도 덜 딱딱하게 변경

현재:

```text
{좋은|유익한|멋진} 포스팅 잘 읽었습니다!
```

기본값 변경 예:

```text
{사진 분위기가 너무 좋네요|정말 좋아 보여요|보기만 해도 기분 좋아지는 글이네요} :)
```

그러나 Spintax는 Gemini 실패 fallback이므로
범용 문구는 너무 구체적으로 만들지 않는다.

---

# 38. General suffix 유지

기존 사용자가 쓰던 일반 꼬리말 유지.

Config 이름을 명확히 한다.

기존:

```text
fixed_suffix
```

Migration 후:

```text
general_suffix
```

기본:

```text
오늘도 좋은 하루 보내세요 :)
```

---

# 39. Recommendation 전용 suffix 추가

Config:

```json
{
  "recommendation_suffix_enabled": true,
  "recommendation_suffix": "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)"
}
```

---

# 40. 추천 전용 꼬리말 톤

추천 기본 후보:

```text
시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)
```

또는:

```text
제 블로그에도 한번 놀러 와주시면 반가울 것 같아요 :)
```

기본값은 첫 번째 권장.

---

# 41. Source별 suffix 선택

신규:

```python
DraftService.resolve_suffix(
    source,
    config
)
```

정책:

```text
NEIGHBOR
→ general_suffix

DIRECT
→ general_suffix

RECOMMENDATION
→ recommendation_suffix_enabled이면 recommendation_suffix
→ disabled면 ""
```

---

# 42. 추천 꼬리말 자동 변경

사용자가 피드 source를:

```text
Recommendation
```

으로 선택하면 UI 꼬리말 표시도 자동으로:

```text
추천 피드용 꼬리말
```

을 보여준다.

그러나 general suffix 값 자체를 덮어쓰지 않는다.

각각 별도 저장.

---

# 43. 추천 꼬리말 삭제 가능

사용자가:

```text
recommendation_suffix
```

입력창을 완전히 비우면:

```text
append 없음
```

으로 처리.

빈 문자열을 default 문장으로 강제로 복원하지 않는다.

---

# 44. 추천 꼬리말 On/Off

UI:

```text
☑ 추천 피드 전용 꼬리말 사용

[ 시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :) ]
```

checkbox OFF:

```text
추천 댓글에는 꼬리말 없음
```

---

# 45. Gemini 댓글에도 suffix 적용

이번 사용자 요구는 Recommendation에서 꼬리말 자동 변경이므로
Gemini 결과에도 적용한다.

기존:

```text
append_fixed_suffix_to_ai = false
```

정책 변경.

신규:

```json
"append_source_suffix_to_ai": true
```

기본 true.

---

# 46. AI 댓글 source suffix 처리

Gemini answer:

```text
사진 보니까 음식이 정말 맛있어 보이네요 :)
```

Recommendation:

```text
사진 보니까 음식이 정말 맛있어 보이네요 :)
시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)
```

Neighbor:

```text
사진 보니까 음식이 정말 맛있어 보이네요 :)
오늘도 좋은 하루 보내세요 :)
```

---

# 47. 중복 suffix 방지

이미 AI 답변 끝에 동일 문자열이 포함되면 재첨부 금지.

```python
if suffix and suffix not in body:
    ...
```

최소 exact match.

---

# 48. Prompt에는 방문 유도 꼬리말 넣지 않기

Gemini에게:

```text
내 블로그 방문해달라는 내용도 넣어줘
```

라고 하지 않는다.

그 부분은 프로그램이 source별로 일관되게 처리.

장점:

```text
AI 결과 편차 없음
추천/이웃 source 정책 명확
사용자가 UI에서 쉽게 삭제 가능
```

---

# 49. PostProcessor 변경

현재:

```python
DraftService.generate(
    self.comment_template,
    self.fixed_suffix
)
```

를 source-aware로 수정.

예:

```python
suffix = DraftService.resolve_suffix(
    source=post.source,
    general_suffix=self.general_suffix,
    recommendation_enabled=self.recommendation_suffix_enabled,
    recommendation_suffix=self.recommendation_suffix
)
```

---

# 50. Gemini 결과 후처리

```python
body = gemini_answer.strip()

final_ai_draft = DraftService.compose(
    body=body,
    suffix=source_suffix
)
```

`auto_apply_ai_comment`이 false라 해도
clipboard에 복사하는 최종 Gemini 댓글 문자열에는 source suffix를 포함시킬지 옵션화.

사용자 요구 기준 기본:

```text
포함
```

---

# 51. OS Clipboard의 최종 내용

Recommendation에서 Gemini 생성 성공 시 clipboard:

```text
AI 본문
+
추천 전용 꼬리말
```

이 들어가게 한다.

사용자는 Cmd+V 한 번이면 된다.

---

# 52. Existing Chrome Gemini + Naver clipboard 흐름

최종:

```text
Naver detail_page
↓
title + excerpt 추출
↓
warm_short prompt 생성
↓
사용자의 일반 Chrome 기존 Gemini 탭 발견
↓
기존 탭 활성화
↓
prompt pbcopy
↓
Gemini editor focus
↓
실제 Cmd+V
↓
실제 Enter
↓
새 response 생성 확인
↓
완료된 새 response만 추출
↓
source suffix 추가
↓
pbcopy
↓
Naver Playwright detail_page 활성화
↓
기본 draft 또는 AI draft 대기
↓
사용자 Cmd+V / 적용
↓
사용자 직접 수정
↓
Enter
↓
Naver submit verify
```

---

# 53. Gemini 실패 fallback

기존 Chrome Gemini 실패:

```text
Gemini 탭 없음
Apple Events 권한 없음
editor 없음
응답 없음
timeout
```

이어도 Naver 작업 자체가 죽으면 안 된다.

Fallback:

```text
Spintax 기본 댓글
```

댓글창 입력.

GUI:

```text
Gemini 생성 실패 — 기본 댓글 초안으로 전환했습니다.
```

---

# 54. Gemini 탭 없음

사용자가 Existing Chrome 모드 선택.

Gemini 탭 없음.

정책:

```text
자동 Playwright Gemini 생성 금지
```

대신:

```text
⚠ 기존 Chrome에서 Gemini 탭을 열어주세요.
```

표시.

옵션 버튼:

```text
[일반 Chrome에서 Gemini 열기]
```

AppleScript:

```text
tell application "Google Chrome" to open location "https://gemini.google.com/"
```

---

# 55. 로그인 여부

기존 일반 Chrome은 사용자가 이미 로그인했다고 가정.

Gemini page가 login page이면:

```text
로그인 필요
```

로 표시.

자동 로그인 금지.

---

# 56. BrowserSession에서 Gemini ownership 제거

ExistingChrome 모드에서는
Naver `BrowserSession`이 gemini tab을 소유하지 않는다.

즉:

```text
BrowserSession.close()
```

가 사용자의 Gemini 탭을 닫으면 안 된다.

---

# 57. External resource ownership flag

필요하면:

```python
self.owns_context = True
self.owns_gemini_page = False
```

등 명시.

하지만 ExistingChromeGeminiBridge를 BrowserSession 밖으로 완전히 빼는 것을 더 권장.

---

# 58. 현재 `close()` 위험

현재 BrowserSession은:

```python
if self.gemini_page:
    self.gemini_page.close()
```

한다.

외부 Chrome attach 구조를 쓴다면 사용자의 탭을 닫는 버그가 된다.

External Gemini는 BrowserSession close 대상에서 제거.

---

# 59. Controller Gemini 생성 방식

현재:

```python
gemini_page = self.session.get_gemini_page()
```

를 제거/분기.

신규:

```python
gemini_bridge = GeminiBridgeFactory.create(
    mode=config.get("gemini_browser_mode")
)
```

PostProcessor에:

```python
gemini_bridge
```

를 전달.

---

# 60. PostProcessor constructor

기존:

```text
gemini_page
```

필드 제거 권장.

대신:

```python
gemini_bridge: Optional[GeminiBridge]
```

---

# 61. 기존 Managed Gemini는 유지 가능

fallback mode:

```text
managed_playwright
```

선택 시에만 현재 `GeminiWebBridge` + managed `Page` 사용.

기존 코드를 완전히 버릴 필요는 없음.

---

# 62. ExistingChrome bridge는 macOS 전용

현재 사용자 환경이 macOS이므로 먼저 macOS 지원.

다른 OS:

```text
NotImplemented / managed_playwright fallback
```

단, 자동 fallback은 사용자가 명시적으로 선택했을 때만.

---

# 63. Chrome AppleScript helper

신규:

```text
services/chrome_apple_events.py
```

책임:

```text
list_tabs()
find_tab(url_contains)
activate_tab()
execute_javascript()
paste_clipboard()
press_enter()
```

Gemini-specific logic과 분리.

---

# 64. AppleScript 실행 실패 구분

```text
Chrome not running
Gemini tab not found
JavaScript from Apple Events disabled
Accessibility permission denied
script timeout
```

각각 다른 error code.

---

# 65. Accessibility 권한

System Events keyboard 사용에 macOS Accessibility 권한이 필요할 수 있음.

프로그램에서 OS permission bypass 금지.

오류 시 안내.

---

# 66. Connection diagnostics panel

Gemini 카드에:

```text
연결 상태:
○ 미확인
● 일반 Chrome Gemini 연결됨
⚠ 권한 필요
```

추가.

---

# 67. “Gemini 열기” 버튼 수정

현재 `webbrowser.open()`은
default browser policy에 따라 다른 브라우저를 열 수도 있다.

Existing Chrome 모드에서는:

```text
Google Chrome
```

을 명시적으로 사용.

---

# 68. Naver 본문 추출 개선 파일

수정:

```text
naver/resolver.py
naver/content_extractor.py
```

---

# 69. Resolver 역할

`get_post_content_locator()`를:

```text
get_post_content_candidates()
```

로 바꾸는 것을 권장.

---

# 70. Extractor 역할

실제 candidate 평가를 `ContentContextExtractor`가 담당.

Resolver는 selector 후보만 제공.

---

# 71. Title 추출도 visible 검사

현재 title selector:

```text
.se-title-text
tit_area
...
title
```

후보 중 visible + non-empty 우선.

`<title>`은 마지막 fallback.

---

# 72. `<title>` 정제

브라우저 title에는:

```text
: 네이버 블로그
```

등이 붙을 수 있음.

DOM heading 우선.

---

# 73. 본문 댓글영역 제외

content candidate text 안에서:

```text
댓글
답글
프로필
```

이 과도하게 섞인 경우 낮은 점수.

---

# 74. SmartEditor text chunk

가능하면 `.se-text-paragraph` 계열 실제 DOM이 확인되면
본문 paragraph만 합치는 방식이 더 정확.

단 실제 DOM inspect 후 추가.

추측 selector를 primary로 넣지 않는다.

---

# 75. Diagnostic tool 추가

`diagnostics/inspect_post_context.py`

출력:

```json
{
  "url": "...",
  "title_candidates": [...],
  "content_candidates": [
    {
      "selector": "...",
      "visible": true,
      "chars": 1234,
      "preview": "..."
    }
  ],
  "selected": "...",
  "final_excerpt_chars": 700
}
```

---

# 76. Gemini prompt preview

GUI에서 실제 전송될 Prompt 전체를 볼 필요는 없음.

표시:

```text
제목
본문 일부 2~3줄
```

이 실제로 들어왔는지 확인 가능.

---

# 77. Warm comment examples

목표 예:

```text
장어가 진짜 먹음직스러워 보여요. 오차즈케로 마무리하는 것도 너무 좋네요 :)
```

```text
산책하기 정말 좋아 보이는 곳이네요. 사진 분위기도 편안해서 좋았어요!
```

```text
딸기라떼 색감부터 너무 맛있어 보여요. 이런 분위기 카페 좋아하는데 한번 가보고 싶네요 :)
```

---

# 78. 피해야 할 결과

```text
해당 장소의 매력이 잘 전달되는 유익한 포스팅이네요.
```

```text
상세한 후기 덕분에 많은 도움이 되었습니다.
```

```text
작성자님의 경험이 매우 인상적입니다.
```

너무 문어체/분석형.

---

# 79. Prompt에 금지 톤 추가

```text
“해당”, “작성자님”, “인상적입니다”, “유익합니다”, “도움이 되었습니다” 같은 딱딱한 표현은 가급적 쓰지 말 것.
```

---

# 80. 댓글 끝맺음 스타일

자연스러운:

```text
~네요
~보여요
~좋아요
~가보고 싶네요
~맛있어 보여요
~좋더라고요
```

AI에 예시로 제공 가능.

---

# 81. 너무 과한 인터넷 말투 금지

```text
대박
미쳤네요
레전드
```

기본 Prompt에서 금지.

---

# 82. Recommendation suffix 문맥

AI 본문과 한 줄 띄워서 붙임.

```python
return f"{body}\n{suffix}"
```

사용자가 원하면 same line option은 V2.

---

# 83. UI Source 변경 시 suffix preview

Neighbor 선택:

```text
현재 적용 꼬리말:
오늘도 좋은 하루 보내세요 :)
```

Recommendation 선택:

```text
현재 적용 꼬리말:
시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)
```

---

# 84. Recommendation suffix field는 항상 수정 가능

피드 source를 바꾸더라도 설정값 보존.

---

# 85. Recommendation suffix를 지웠을 때

빈값 그대로 config 저장.

DEFAULT_CONFIG가 다시 자동으로 채우지 않게 해야 함.

---

# 86. ConfigService의 현재 저장 버그도 같이 수정

현재 UI는 부분 dict를:

```python
config_service.save(cfg_data)
```

로 저장한다.

`save()`는:

```python
self.data = data
```

로 전체 교체한다.

따라서 `schema_version`, `browser_mode` 등
UI에서 전달되지 않는 설정이 다시 사라질 수 있다.

이번 수정에 반드시 포함.

---

# 87. `update_many()` 도입

```python
def update_many(self, values):
    merged = DEFAULT_CONFIG_V2.copy()
    merged.update(self.data)
    merged.update(values)
    merged["schema_version"] = 2
    self._atomic_save(merged)
```

UI:

```python
self.config_service.update_many(cfg_data)
```

---

# 88. Config schema 확장

예:

```json
{
  "schema_version": 3,

  "general_suffix": "오늘도 좋은 하루 보내세요 :)",

  "recommendation_suffix_enabled": true,
  "recommendation_suffix": "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)",

  "append_source_suffix_to_ai": true,

  "gemini_web_enabled": true,
  "gemini_browser_mode": "existing_chrome_mac",
  "gemini_reuse_existing_tab": true,

  "ai_prompt_style": "warm_short"
}
```

스키마를 3으로 올리는 것을 권장.

---

# 89. v2 → v3 migration

기존:

```text
fixed_suffix
```

가 있으면:

```text
general_suffix
```

로 이동.

Recommendation suffix는 default 추가.

---

# 90. Existing Gemini custom URL

기존 config의:

```text
gemini_custom_url
```

은 유지.

하지만 Existing tab 발견 시:

```text
현재 탭을 우선
```

한다.

custom URL은:

```text
Gemini 탭이 없을 때 새로 열기 위한 주소
```

로 역할 변경.

---

# 91. 테스트: 기존 Chrome bridge

mock AppleScript runner.

검증:

```text
Gemini tab found
Gemini tab not found
multiple tabs
permission denied
```

---

# 92. 테스트: 기존 탭 보존

기존 URL:

```text
/app/ABC
```

설정 URL:

```text
/app/XYZ
```

Existing reuse mode에서는:

```text
navigation 0회
```

---

# 93. 테스트: Response freshness

기존 response 3개.

새 prompt 전송.

새 response가 생기지 않음.

결과:

```text
None
```

기존 3번째 답변 반환 금지.

---

# 94. 테스트: New response

before=3
after=4

4번째 response만 반환.

---

# 95. 테스트: Clipboard verification

`pbcopy` 후 `pbpaste` 비교.

---

# 96. 테스트: Content extraction

후보:

```text
hidden 1500 chars
visible 20 chars
visible 900 chars
```

선택:

```text
visible 900 chars
```

---

# 97. 테스트: Warm Prompt

Prompt에:

```text
1~2문장
정감
칭찬
딱딱한 문어체 피하기
```

포함.

---

# 98. 테스트: Recommendation suffix

Source:

```text
RECOMMENDATION
```

→ recommendation suffix.

Source:

```text
NEIGHBOR
```

→ general suffix.

---

# 99. 테스트: Recommendation suffix empty

```text
recommendation_suffix=""
```

→ suffix 없음.

default 문구 자동 복원 금지.

---

# 100. 테스트: Gemini answer suffix

AI answer + Recommendation source

→ recommendation suffix 1회만.

---

# 101. 테스트: Gemini failure fallback

Gemini fail.

→ Spintax draft + source suffix.

Naver workflow 계속.

---

# 102. 테스트: Stop

Gemini response polling 중 Stop.

즉시 종료.

---

# 103. 테스트: External Chrome ownership

앱 종료 후 사용자의 Chrome window/tab에 close command 전송 0회.

---

# 104. 구현 Phase 0

**Config integrity 먼저.**

- update_many
- atomic save
- schema v3 migration

GO:

```text
설정 유실 없음
```

---

# 105. Phase 1

Existing Chrome tab discovery.

아직 Prompt 전송 금지.

GUI 연결 테스트만.

GO:

```text
사용자가 보고 있는 실제 Gemini tab URL/title 식별 성공
```

---

# 106. Phase 2

Apple Events JavaScript capability probe.

GO:

```text
document.title 등 harmless JS 결과 회수
```

---

# 107. Phase 3

ExistingChromeGeminiBridge prompt 전송.

아직 response extraction 전에
실제 Gemini 입력란에서 prompt가 보이는지 확인.

GO:

```text
실제 keyboard paste
실제 Enter
```

---

# 108. Phase 4

Fresh response detection + extraction + pbcopy.

GO:

```text
과거 response 오인 0
```

---

# 109. Phase 5

Naver ContentContextExtractor 개선.

GO:

```text
excerpt 80자 이상 정상 추출 샘플 확보
```

---

# 110. Phase 6

Warm-short Prompt 변경.

GO:

수동 샘플 10개 생성 후
딱딱한 문어체 비율 낮음.

---

# 111. Phase 7

Source-specific suffix.

GO:

Neighbor / Recommendation 전환 테스트.

---

# 112. Phase 8

UI 통합.

추가:

```text
Gemini 브라우저 모드
기존 탭 연결 테스트
일반 꼬리말
추천 꼬리말 on/off
추천 꼬리말 입력
```

---

# 113. Phase 9

E2E.

---

# 114. E2E Scenario A

Existing Chrome에서 이미 Gemini 로그인 및 대화창 열림.

Naver Assistant 시작.

결과:

```text
기존 Gemini 탭 그대로 사용
새 Chrome 생성 없음
기존 Gemini URL 변경 없음
```

---

# 115. E2E Scenario B

Recommendation.

Naver 글:

```text
title/excerpt
```

Gemini:

```text
짧고 정감 있는 댓글
```

Clipboard:

```text
Gemini 댓글
추천 전용 꼬리말
```

Naver:

```text
Cmd+V
수정
Enter
```

---

# 116. E2E Scenario C

Neighbor.

Clipboard:

```text
Gemini 댓글
일반 꼬리말
```

---

# 117. E2E Scenario D

Recommendation suffix textbox 빈값.

Clipboard:

```text
Gemini 댓글 only
```

---

# 118. E2E Scenario E

본문 추출 실패.

Gemini prompt:

```text
제목 only
제목에 없는 사실을 만들지 말 것
```

GUI warning.

---

# 119. E2E Scenario F

Gemini 기존 Chrome 권한 실패.

Naver 댓글:

```text
Spintax fallback
```

프로그램 freeze 없음.

---

# 120. E2E Scenario G

Gemini old answers 존재.

새 prompt response 생성 실패.

old answer clipboard 복사 금지.

---

# 121. 절대 금지

```text
“기존 Chrome 사용”이라고 표시하면서 app용 persistent Chrome 사용
```

---

# 122. 절대 금지

External Gemini tab 자동 close.

---

# 123. 절대 금지

Existing Gemini conversation 자동 navigation.

---

# 124. 절대 금지

Old Gemini response를 new response처럼 clipboard 복사.

---

# 125. 절대 금지

본문 0자인데 “본문 포함됨” 로그.

---

# 126. 절대 금지

Recommendation suffix가 빈 문자열인데 default로 재생성.

---

# 127. 절대 금지

AI 댓글을 자동 submit.

---

# 128. 최종 파일 구조 권장

```text
services/
├── gemini_bridge.py
├── gemini_existing_chrome.py
├── gemini_web.py
├── chrome_apple_events.py
├── ai_prompt.py
└── draft.py
```

---

# 129. `GeminiWebBridge` 역할 변경

기존 Playwright managed browser 전용으로 명확히 이름 변경 가능:

```text
ManagedPlaywrightGeminiBridge
```

---

# 130. GeminiBridgeFactory

```python
class GeminiBridgeFactory:
    @staticmethod
    def create(config, ...):
        mode = config.get("gemini_browser_mode")

        if mode == "existing_chrome_mac":
            return ExistingChromeGeminiBridge(...)

        if mode == "managed_playwright":
            return ManagedPlaywrightGeminiBridge(...)

        return None
```

---

# 131. PostProcessor는 브라우저 종류를 모르게 한다

현재 PostProcessor가 `gemini_page`를 직접 받는 구조를 제거.

```python
gemini_bridge.generate_comment(prompt)
```

만 호출.

---

# 132. UI는 Gemini DOM을 모르게 한다

연결 테스트 결과만 표시.

---

# 133. Logging 예

```text
[GEMINI/EXTERNAL] Chrome 실행 확인
[GEMINI/EXTERNAL] Gemini tab found window=2 tab=3
[GEMINI/EXTERNAL] JavaScript bridge OK
[CONTEXT] title=...
[CONTEXT] excerpt_chars=692
[GEMINI] prompt pasted via real Cmd+V
[GEMINI] before_response_count=5
[GEMINI] new_response_count=6
[GEMINI] response stable
[GEMINI] clipboard verified
```

---

# 134. 디버그시 full prompt

기본 로그에는 full prompt 출력하지 않음.

---

# 135. UI 예시

```text
🤖 Gemini 댓글 도우미

사용 브라우저
● 현재 켜져 있는 일반 Chrome
○ 프로그램 전용 브라우저

연결 상태
● 기존 Gemini 탭 연결됨

[기존 탭 연결 테스트]

현재 글
광양 중마동 ...

본문 일부
오랜만에 방문해서 ...

댓글 스타일
● 짧고 정감 있는 칭찬형

일반 꼬리말
[ 오늘도 좋은 하루 보내세요 :) ]

☑ 추천피드 전용 꼬리말
[ 시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :) ]
```

---

# 136. 기존 Pacing 유지

이번 변경으로 PacingService 제거/변경하지 않는다.

---

# 137. Gemini 응답 생성 시간에는 Random Pause 없음

기존 정책 유지.

---

# 138. 다음 글 대기

등록 완료 후 기존:

```text
next_post_delay
random pause
```

유지.

---

# 139. 프로그램 시작 연결 순서

```text
Naver BrowserSession 시작
↓
Gemini bridge init
↓
Existing Chrome mode면 tab probe
↓
Feed source open
```

Gemini 없다고 Feed 전체를 중단할 필요 없음.

---

# 140. Gemini 가용성

```text
available=false
```

이어도 template 댓글 가능.

---

# 141. Connection State

BotRuntimeState에:

```python
gemini_connection_status
gemini_tab_title
gemini_tab_url
```

optional 추가.

---

# 142. Context State

추가:

```python
context_excerpt_chars
context_extraction_ok
```

GUI/debug에 사용.

---

# 143. Prompt style config

```json
"ai_prompt_style": "warm_short"
```

기존 `natural` migration:

```text
natural → warm_short
```

가능.

---

# 144. Default Spintax

권장:

```text
{정말 좋아 보여요|사진 분위기가 너무 좋네요|보기만 해도 기분 좋아지네요} :)
```

다만 사용자 기존 설정이 있으면 migration에서 덮어쓰지 않는다.

새 설치 default만 변경.

---

# 145. General suffix migration

기존 `fixed_suffix` 값 보존.

---

# 146. Recommendation suffix default

신규이므로 새 default 삽입.

---

# 147. README 변경

기존 Chrome 기능 설명.

필수 조건:

```text
macOS
Google Chrome 실행
Gemini 탭 오픈/로그인
필요한 Apple Events/Accessibility 권한
```

---

# 148. README에서 “자동 로그인” 표현 금지

기존 user Chrome session 재사용.

---

# 149. Troubleshooting

### Gemini 탭을 못 찾음

- Chrome인지 확인
- URL에 gemini.google.com 포함 확인
- 연결 테스트

### Prompt가 안 들어감

- Apple Events JS 권한
- Accessibility 권한
- input selector diagnostics

### 옛날 답변이 복사됨

- response freshness diagnostics 확인

### 본문 0자

- inspect_post_context 실행

---

# 150. Implementation Report 형식

각 Phase 후:

```markdown
## Phase X

### 변경 파일

### 구현

### 실제 검증

### 테스트

### 알려진 한계

### GO / NO-GO
```

---

# 151. 과장 보고 금지

다음 표현 금지:

```text
완벽히 수정
100% 해결
무조건 작동
```

실제 기존 Chrome 탭을 식별한 로그/테스트가 없으면:

```text
구현 완료
```

라고 보고하지 않는다.

---

# 152. 최종 Acceptance

## Existing Chrome

- [ ] 사용자가 평소 사용 중인 Chrome 탭을 실제 탐색
- [ ] 프로그램 전용 profile 탭이 아님
- [ ] 기존 Gemini URL 변경 없음
- [ ] 앱 종료 후 탭 유지
- [ ] 연결 테스트 UI

## Context

- [ ] 실제 제목
- [ ] 실제 본문 일부
- [ ] excerpt chars 로그
- [ ] 실패 표시

## Gemini

- [ ] 실제 Cmd+V 기반 prompt 입력
- [ ] new response freshness
- [ ] streaming 완료
- [ ] clipboard verification

## Tone

- [ ] 1~2문장
- [ ] 정감 있는 칭찬
- [ ] 딱딱한 문어체 억제
- [ ] 질문 기본 없음

## Suffix

- [ ] Neighbor general
- [ ] Recommendation special
- [ ] Recommendation field 수정 가능
- [ ] 빈값 가능
- [ ] AI 댓글에도 적용
- [ ] 중복 추가 없음

## Safety

- [ ] Enter 최종 승인 유지
- [ ] Gemini 실패 fallback
- [ ] Stop 정상
- [ ] Pacing 유지

---

# 153. Codex / Claude에 전달할 최종 명령

```text
현재 main의 “기존 Gemini 탭 재사용” 구현은
Playwright BrowserContext 내부 탭만 검색하므로
사용자가 평소 사용하는 일반 Google Chrome 탭을 재사용하지 못한다.

이 문제를 selector 수정으로 해결하려 하지 말고
Gemini browser ownership을 Naver BrowserSession에서 분리한다.

macOS 기본 Gemini 모드를 `existing_chrome_mac`으로 구현한다.

Google Chrome AppleScript를 통해 실행 중인 일반 Chrome의 모든 탭을 조사하고
gemini.google.com 탭을 찾는다.

Gemini 탭을 찾으면 기존 conversation URL을 절대 자동으로 변경하지 않는다.

Prompt 입력은 DOM innerHTML 주입이 아니라:
1. prompt를 OS clipboard에 넣고
2. Gemini editor를 JS로 focus하고
3. 실제 Cmd+V keyboard paste
4. 실제 Enter
방식으로 수행한다.

전송 전에 response count/state를 snapshot하고,
반드시 새 model response가 생긴 뒤
그 신규 response만 추출한다.
기존 답변을 재사용하지 않는다.

답변 완료 후 OS clipboard에 복사하고 pbpaste로 검증한다.

동시에 Naver ContentContextExtractor를 수정한다.
현재처럼 첫 번째 selector를 즉시 선택하지 말고
visible 여부와 text length를 평가해 가장 적절한 본문 container를 선택한다.

Gemini 요청 전 로그에:
- title
- excerpt_chars
- excerpt preview
를 남긴다.

AIPromptBuilder 기본 스타일을 `warm_short`으로 바꾼다.
댓글은 짧고 정감 있는 1~2문장의 칭찬/공감형 문장으로 만들고
“작성자님”, “유익합니다”, “인상적입니다”, “도움이 되었습니다” 같은
딱딱한 표현을 억제한다.
질문은 기본적으로 만들지 않는다.

Source별 suffix를 추가한다.

NEIGHBOR/DIRECT:
기존 일반 꼬리말 사용.

RECOMMENDATION:
`recommendation_suffix_enabled`가 true이면
`recommendation_suffix` 사용.

추천 기본 문구:
“시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)”

추천 꼬리말은 UI에서 사용자가 자유롭게 수정하거나
완전히 빈 문자열로 만들 수 있어야 한다.
빈 문자열인 경우 절대 default로 자동 복원하지 않는다.

Gemini AI 댓글에도 source suffix를 기본적으로 적용한다.
하지만 네이버 최종 등록은 기존과 동일하게
사용자 Enter 승인이 있어야만 수행한다.

기존 Pacing / Random Pause / History / Safe LikeState는 유지한다.

ConfigService의 save(partial_dict) 설정 유실 문제도
이번 변경 전에 update_many + atomic save 방식으로 수정한다.

Phase별 실제 테스트 증거 없이 “완벽히 해결”했다고 보고하지 않는다.
```

---

# END
