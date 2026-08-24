# NAVER FEED ASSISTANT
# GEMINI INPUT FIX + CONTEXTUAL LOCAL COMMENT ENGINE WORK ORDER

> 대상 저장소: `urangsu/logonthe`
> 기준: 2026-08-24 현재 `main`
> 목표:
> - 기존 일반 Chrome의 로그인된 Gemini 탭에 실제 prompt가 들어가도록 수정
> - prompt가 Gemini 입력창에 들어갔는지 검증 후에만 Enter 전송
> - 기존 답변을 새 답변으로 오인하지 않도록 freshness 검증
> - Gemini가 실패해도 단순 고정 멘트가 아니라 네이버 글 제목/본문을 분석한 로컬 댓글 생성
> - Gemini 성공 시 실제 Gemini 결과를 기본 draft로 사용할 수 있도록 흐름 수정
> - Human-in-the-loop 최종 Enter 승인 유지
> - 기존 source별 suffix / pacing / LikeState / History 유지

---

# 1. 현재 장애 원인 — 반드시 먼저 이해하고 수정

## 1.1 ExistingChromeGeminiBridge는 Gemini editor를 포커스하지 않는다

현재 `services/gemini_existing_chrome.py`의 실제 흐름은:

```text
Gemini tab 찾기
→ prompt pbcopy
→ Chrome activate
→ 해당 window/tab 활성화
→ Cmd+V
→ Enter
```

문제는 이 사이에:

```text
Gemini prompt editor focus
```

가 없다.

현재 코드의 주석은:

```text
-- 입력창 포커스를 위해 Cmd+V 붙여넣기
```

라고 되어 있지만 `Cmd+V` 자체는 focus를 만들지 않는다.

따라서:
- Gemini 홈 화면이 보이기만 하고
- prompt가 안 들어가거나
- 이전에 focus되어 있던 곳으로 paste가 가거나
- 아무 일도 발생하지 않을 수 있다.

이것이 현재 사용자가 보는 “Gemini 홈만 켜지고 아무 것도 안 함”의 가장 직접적인 원인이다.

---

# 2. Gemini 입력 흐름을 "Blind Keyboard"에서 "Focus → Paste Verify → Send Verify"로 바꾼다

새 흐름:

```text
Gemini tab 발견
↓
tab 활성화
↓
JS로 Gemini prompt editor 정확히 탐색
↓
editor.focus()
↓
document.activeElement가 editor 또는 editor 내부인지 확인
↓
prompt를 pbcopy
↓
실제 Cmd+V
↓
editor innerText에 prompt가 실제 들어갔는지 확인
↓
검증 성공한 경우에만 Enter
↓
editor가 비워지거나 새 response가 시작되는지 확인
↓
새 response 완료 대기
```

---

# 3. Gemini Editor Resolver

`ExistingChromeGeminiBridge` 안에 하드코딩하지 말고 helper로 분리 가능.

후보:

```javascript
[
  "rich-textarea div[contenteditable='true']",
  "div.ql-editor[contenteditable='true']",
  "div[role='textbox'][contenteditable='true']",
  "div[contenteditable='true'][aria-label]",
  "textarea"
]
```

중요:

페이지 전체 첫 번째 `div[contenteditable=true]`를 무조건 사용하지 않는다.

각 후보에 대해:

```text
visible
not disabled
reasonable bounding rect
prompt area 주변
```

를 확인한다.

---

# 4. JavaScript focus probe

Apple Events JavaScript가 허용된 경우:

```javascript
(() => {
    const selectors = [
        "rich-textarea div[contenteditable='true']",
        "div.ql-editor[contenteditable='true']",
        "div[role='textbox'][contenteditable='true']",
        "textarea"
    ];

    for (const selector of selectors) {
        const nodes = [...document.querySelectorAll(selector)];

        for (const el of nodes) {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);

            const visible =
                rect.width > 20 &&
                rect.height > 20 &&
                style.display !== "none" &&
                style.visibility !== "hidden";

            if (!visible) continue;

            el.focus();

            return {
                ok: true,
                selector,
                tag: el.tagName,
                text: el.innerText || el.value || "",
                active:
                    document.activeElement === el ||
                    el.contains(document.activeElement)
            };
        }
    }

    return { ok: false };
})()
```

`ok=true AND active=true`가 아니면 paste하지 않는다.

---

# 5. JS OFF이면 blind Cmd+V 금지

현재는 JS OFF여도:

```text
Chrome activate
→ Cmd+V
→ Enter
```

를 실행한다.

이 fallback을 제거한다.

JS/DOM focus를 확인할 수 없다면:

```text
Gemini 자동 입력 불가
→ 로컬 contextual draft로 fallback
```

해야 한다.

잘못된 위치에 prompt를 붙이는 것보다 안전하다.

---

# 6. 권한 메시지 정확히 구분

사용자가 “Chrome 조종 권한”을 줬다고 해도 다음은 별개다.

1. macOS Automation/Accessibility permission
2. Chrome의 `Allow JavaScript from Apple Events`

`test_connection()` 결과에:

```text
Chrome control = OK
JavaScript from Apple Events = OFF
```

를 별도 표시.

JS OFF인 경우 GUI:

```text
⚠ Chrome 제어 권한은 있으나 Gemini 입력창 DOM 접근은 꺼져 있습니다.
Chrome > 보기 > 개발자 > Apple Events의 JavaScript 허용을 켜주세요.
```

---

# 7. Paste 검증

Cmd+V 후 0.1~1.0초 동안 editor text를 읽는다.

검증:

```python
normalize(editor_text).contains(normalize(prompt[:80]))
```

또는 전체 길이 비교.

성공:

```text
[GEMINI] prompt paste verified
```

실패:

```text
[GEMINI] prompt paste verification FAILED
```

실패 시 Enter 절대 누르지 않는다.

---

# 8. Enter 전송 검증

Enter 후 다음 중 하나 확인:

```text
editor text가 비워짐
또는
response count 증가 시작
또는
generation stop button 등장
```

아무 신호도 없으면:

```text
SEND_FAILED
```

로 처리하고 local contextual draft fallback.

---

# 9. 현재 before_count 미사용 버그 수정

현재 코드는:

```python
before_count = ...
```

를 구하지만 response loop에서 실제로 사용하지 않는다.

이건 명백한 논리 버그다.

반드시:

```python
after_count > before_count
```

가 되기 전까지 response extraction을 시작하지 않는다.

---

# 10. 기존 마지막 답변을 새 답변으로 읽는 문제 차단

기존 대화에 답변이 5개 있고
새 prompt 전송이 실패한 경우:

현재 구현은 5번째 답변을 읽어올 수 있다.

수정:

```text
before_count = 5

Enter

poll:
  count=5 → 아직 새 답변 없음
  count=5 → 없음
  count=6 → 새 response 시작
```

`6번째 response`만 추적한다.

---

# 11. response selector 중복 카운트 문제

현재:

```text
model-response
message-content
div.markdown
```

을 한 querySelectorAll에 같이 넣으면
하나의 Gemini 답변 안에서 parent + child가 동시에 count되어
답변 개수가 2~3개씩 잡힐 수 있다.

따라서 "response count" selector는 하나의 가장 상위 stable wrapper로 확정해야 한다.

우선 실제 DOM inspect.

예:

```text
model-response
```

가 실제 각 model turn의 상위 wrapper면 이것만 사용.

하위 markdown은 text extraction 용도로만 사용.

---

# 12. Gemini 신규 응답 tracking

가능하면:

```javascript
const responses = [...document.querySelectorAll("model-response")];
return responses.map((el, i) => ({
  index: i,
  text: el.innerText || "",
  id: el.id || "",
  dataTestId: el.getAttribute("data-test-id") || ""
}));
```

before snapshot과 after snapshot 비교.

---

# 13. Gemini 완료 판정

새 response가 나타난 후:

```text
text length > 0
AND
1.5~2.0초 동안 text 변경 없음
AND
generation stop button 없음
```

에서 완료.

timeout:

```text
60초
```

Stop Event interruptible.

---

# 14. 클립보드 복사

최종 Gemini answer:

```text
source-specific suffix까지 붙인 최종 댓글
```

을 pbcopy.

pbpaste로 동일성 검증.

---

# 15. 두 번째 직접 원인 — auto_apply_ai_comment 기본값

현재 Processor:

```python
if self.auto_apply_ai_comment and gemini_answer:
    draft_text = ...
else:
    draft_text = DraftService.generate(...)
```

즉 Gemini answer가 있어도:

```text
auto_apply_ai_comment = false
```

면 네이버 댓글창에는 기본 template이 들어간다.

사용자가 지금 느끼는:

```text
Gemini는 안 움직이고
네이버에는 자동 멘트만 들어감
```

은 이 설정 때문에 더 강하게 나타난다.

---

# 16. draft 선택 정책 수정

새 정책 권장:

```text
Gemini 활성 + Gemini 성공
→ Gemini 결과를 댓글 draft로 사용

Gemini 활성 + Gemini 실패
→ Contextual Local Draft 사용

Gemini 비활성
→ Contextual Local Draft 사용

본문 context도 실패
→ 기존 Spintax fallback
```

즉:

```text
Gemini
  ↓ fail
Local Context Engine
  ↓ fail
Spintax
```

3단계 fallback.

---

# 17. auto_apply_ai_comment 의미 변경

기존 이름이 혼란스럽다.

추천:

```text
use_gemini_result_as_draft
```

기본:

```text
true
```

중요:

이것은 자동 등록이 아니다.

```text
Gemini 결과를 editor에 초안으로 넣음
→ 사용자가 직접 읽고 수정
→ Enter
```

이므로 Human-in-the-loop 원칙 유지.

---

# 18. 네이버 로컬 댓글 엔진 신규 추가

신규:

```text
services/contextual_draft.py
```

목적:

Gemini 없이도 제목 + 본문 일부를 이용해
글 내용과 약하게 연결된 자연스러운 댓글을 만든다.

외부 API/LLM/NLP dependency 없음.

Python 표준 라이브러리만.

---

# 19. ContextualDraftEngine 입력

```python
@dataclass
class DraftContext:
    title: str
    excerpt: str
    source: FeedSourceType
```

출력:

```python
@dataclass
class ContextualDraftResult:
    body: str
    category: str
    subject: str
    template_id: str
    confidence: float
```

---

# 20. 1단계 — Text normalization

```python
text = f"{title} {excerpt}"
```

처리:

- HTML 없음
- whitespace 압축
- 특수기호 정리
- URL 제거
- 해시태그 정리
- 숫자 단위는 보존 가능

---

# 21. 2단계 — 문장 분리

간단:

```python
re.split(r"[.!?。\n]+", excerpt)
```

유효 문장:

```text
15~140자
```

우선.

---

# 22. 3단계 — Token 추출

정규식:

```python
re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·&+\-]{1,20}", text)
```

---

# 23. Stopword

예:

```text
오늘
이번
정말
너무
그리고
하지만
그래서
포스팅
블로그
후기
리뷰
사진
정보
방문
다녀왔
했어요
합니다
입니다
있어요
있습니다
같아요
```

단 category keyword는 stopword에서 제외.

---

# 24. Keyword score

각 token:

```text
title 등장          + 5
excerpt 첫 250자     + 2
본문 반복 등장       + min(freq, 3)
category lexicon     + 3
2글자 일반어         - 1
stopword            제외
```

Top 3~6 keyword.

---

# 25. 제목 keyword를 특히 중요하게

블로그 제목에는:

```text
지역
장소
가게
메뉴
제품
여행지
서비스명
```

이 들어가는 경우가 많음.

따라서 title exact token을 body frequency보다 우선.

---

# 26. Category detector

단순 lexicon score.

## FOOD

```text
맛집
메뉴
고기
삼겹살
장어
돈까스
돈카츠
파스타
리조또
쭈꾸미
비빔밥
수제비
빵
국수
라면
치킨
회
초밥
덮밥
디저트
```

## CAFE

```text
카페
커피
라떼
아메리카노
디저트
케이크
빙수
말차
녹차
딸기라떼
```

## TRAVEL

```text
여행
산책
공원
전시
미술관
섬
바다
해변
숙소
호텔
축제
정원
수영장
관광
코스
```

## BEAUTY

```text
헤어
펌
커트
미용실
스타일
염색
시스루
쉐도우펌
```

## PRODUCT

```text
제품
구매
사용
가격
배송
착용
개봉
```

## FINANCE

```text
주식
ETF
투자
시장
종목
금리
경제
```

나머지:

```text
GENERAL
```

---

# 27. category score

```python
score[category] +=
    title_hits * 3 +
    excerpt_hits
```

최고점 category.

동점:

```text
title hit가 많은 category 우선
```

---

# 28. Subject 후보

우선:

1. title keyword
2. title bigram
3. excerpt high-score token

예:

```text
"광양 모리 히츠마부시"
→ 히츠마부시

"완도 아내의 정원"
→ 아내의 정원

"딸기라떼 녹차라떼"
→ 딸기라떼
```

---

# 29. Multi-word named phrase

제목을 공백 단위 token만 자르면:

```text
아내의 정원
오천그린광장
예술의 섬 장도
```

같은 phrase를 잃을 수 있다.

간단한 bigram 후보:

```python
words[i] + " " + words[i+1]
```

score.

---

# 30. Evidence sentence

본문 sentence 중:

```text
subject 또는 top keyword 포함
```

하면서 가장 자연스러운 문장을 하나 찾는다.

score:

```text
subject hit +5
top keyword hit +2
20~100 chars +2
```

---

# 31. Local draft는 "요약"하지 않는다

목표는 본문 전체 요약이 아니라:

```text
글에서 실제 단어 하나
+
가벼운 반응 한 문장
```

이다.

그래야 로컬 알고리즘이 어설픈 사실을 만들지 않는다.

---

# 32. FOOD templates

subject가 있다:

```text
"{subject} 진짜 맛있어 보여요. 사진 보니까 더 먹어보고 싶네요 :)"

"{subject}부터 눈에 확 들어오네요. 보기만 해도 맛있어 보여요!"

"{subject} 조합이 정말 좋아 보여요. 이런 메뉴는 저도 좋아해서 더 눈길이 가네요 :)"
```

주의:

`조합`이라는 표현은 여러 메뉴/ingredient signal이 있을 때만.

---

# 33. CAFE templates

```text
"{subject}가 눈에 확 들어오네요. 분위기도 편안해 보여서 좋네요 :)"

"{subject} 색감부터 너무 좋아 보여요. 이런 카페 분위기 참 좋더라고요!"

"사진 분위기가 편안해서 좋네요. {subject}도 한번 맛보고 싶어요 :)"
```

---

# 34. TRAVEL templates

```text
"{subject} 분위기가 정말 좋아 보이네요. 천천히 둘러보기 좋을 것 같아요 :)"

"{subject} 사진 보니까 한번 가보고 싶네요. 편안한 분위기가 좋네요!"

"사진만 봐도 {subject} 느낌이 참 좋네요. 산책하듯 둘러보기 좋아 보여요 :)"
```

`산책` 표현은 본문/title에 산책/공원/정원/섬 signal이 있을 때만.

---

# 35. BEAUTY templates

```text
"{subject} 느낌이 자연스럽고 예뻐 보여요. 분위기가 잘 살아나는 것 같네요 :)"

"{subject} 스타일이 깔끔해서 보기 좋네요. 자연스러운 느낌이 마음에 들어요!"
```

`자연스럽다`는 style-related context에선 비교적 안전.

---

# 36. PRODUCT templates

```text
"{subject} 사용감이 어떤지 궁금했는데 후기 보니 느낌이 오네요. 깔끔하게 정리돼서 보기 좋았어요 :)"
```

질문형은 쓰지 않음.

단 `사용` signal 없는 경우:

```text
"{subject}가 눈에 들어오네요. 실제 후기라 더 편하게 볼 수 있었어요 :)"
```

---

# 37. FINANCE templates

금융 글에는 지나친 감탄형 댓글이 어색하다.

```text
"{subject} 부분이 눈에 들어오네요. 흐름을 편하게 볼 수 있어서 좋았어요."

"{subject} 얘기가 특히 눈에 들어오네요. 정리가 깔끔해서 보기 편했어요."
```

---

# 38. GENERAL templates

subject 있음:

```text
"{subject}가 특히 눈에 들어오네요. 글 분위기가 편안해서 좋았어요 :)"

"{subject} 얘기 보니까 더 관심이 가네요. 사진이랑 같이 보니 느낌이 잘 전해져요!"
```

subject 없음:

```text
"사진 분위기가 편안해서 좋네요. 글도 부담 없이 잘 읽었어요 :)"

"보기만 해도 기분 좋아지는 느낌이네요. 사진 분위기도 참 좋았어요 :)"
```

---

# 39. "잘 보고 갑니다" 금지

기본 local engine도 다음 표현 사용 금지:

```text
잘 보고 갑니다
유익한 정보
좋은 정보
감사합니다
작성자님
인상적입니다
도움이 되었습니다
```

---

# 40. Hallucination 방지

Local engine은:

```text
본문/title에서 실제 추출된 subject
```

만 구체적으로 언급.

없으면 generic warm fallback.

없는 메뉴/장소/특징 생성 금지.

---

# 41. Template repetition 방지

세션 내 최근 사용 template ID 저장:

```python
deque(maxlen=6)
```

후보 중 최근 template 제외.

전부 최근 사용이면 reset.

---

# 42. Subject repetition

같은 subject가 여러 글에 반복되어도 정상.

하지만 body template pattern을 변경.

---

# 43. 짧은 확률 variation

예:

```text
:)
!
없음
```

중 선택.

단 punctuation randomization은 과하지 않게.

---

# 44. Context confidence

예:

```text
title 존재 + excerpt >= 80 + subject 있음
→ HIGH

title 존재 + subject 있음
→ MEDIUM

title만 있음
→ LOW
```

LOW:

기존 Spintax보다 조금 나은 generic draft.

---

# 45. Draft fallback chain 코드

```python
def choose_draft(...):

    if gemini_enabled:
        ai = gemini_bridge.generate_comment(prompt)

        if ai:
            return compose(ai, suffix), "gemini"

    local = contextual_engine.generate(post)

    if local and local.body:
        return compose(local.body, suffix), "local_context"

    return DraftService.generate(
        template,
        suffix
    ), "spintax"
```

---

# 46. Gemini success를 네이버 draft로 쓰는 정책

추천 default:

```text
gemini_result_as_draft = true
```

Gemini 결과를 자동 입력하되
등록은 하지 않는다.

사용자:

```text
읽기
수정
Enter
```

---

# 47. Gemini result clipboard도 유지

Gemini 결과:

```text
댓글 body + source suffix
```

를 OS clipboard에도 유지.

따라서 사용자가 원하면 Cmd+V로 재적용 가능.

---

# 48. Source suffix

기존 `DraftService.resolve_suffix()` 유지.

Recommendation:

```text
recommendation_suffix
```

Neighbor/Direct:

```text
general_suffix
```

---

# 49. Contextual draft에도 suffix 동일 적용

Local engine은 body만 생성.

suffix는 `DraftService.compose_body_and_suffix()`가 담당.

책임 분리.

---

# 50. ContentContextExtractor 추가 개선

현재 이미:
- visible 검사
- 길이 score
- SmartEditor selector bonus
를 하고 있어 이전보다 개선됐다.

하지만 다음 보강:

```text
selected selector
raw chars
cleaned chars
score
```

debug log.

---

# 51. 본문 700자 sampling 개선

현재는 앞 700자만 사용.

블로그 초반에는:

```text
인사
위치
광고 고지
목차
```

가 몰릴 수 있다.

간단 개선:

```text
first 350 chars
+
middle salient sentence 200 chars
+
last useful sentence 150 chars
```

또는 sentence score 기반 top 3 sentence.

---

# 52. Prompt용 excerpt와 Local Draft용 context 분리 가능

AI Prompt:

```text
최대 700자
```

Local engine:

```text
전체 clean text 최대 2000~3000자
```

를 사용하면 keyword 추출이 더 정확.

`PostContext`:

```python
title
excerpt
analysis_text
```

추가 권장.

---

# 53. analysis_text

```text
cleaned max 2500 chars
```

Gemini로는 보내지 않아도 됨.

Local Context Engine에서만 사용.

---

# 54. Context extractor 결과

```python
@dataclass
class PostContext:
    title: str = ""
    excerpt: str = ""
    analysis_text: str = ""
```

---

# 55. Gemini Prompt 로그

전송 직전:

```text
[GEMINI] editor_focus=OK
[GEMINI] prompt_chars=812
[GEMINI] prompt_paste=VERIFIED
[GEMINI] before_response_count=4
[GEMINI] send=VERIFIED
[GEMINI] new_response_index=4
```

---

# 56. 실패 로그

```text
[GEMINI] editor_focus=FAILED
→ fallback=local_context
```

사용자가 무엇이 실패했는지 즉시 알 수 있어야 한다.

---

# 57. GUI 상태

Gemini 실패:

```text
Gemini 입력 실패 — 글 내용 기반 기본 댓글로 전환
```

현재처럼 조용히 template을 넣지 않는다.

---

# 58. Local Draft debug

```text
[LOCAL_DRAFT]
category=food
subject=히츠마부시
template=food_02
confidence=HIGH
```

---

# 59. UI에 Draft Source 표시

선택:

```text
댓글 초안 출처:
🤖 Gemini
🧩 글 내용 기반
📝 기본 템플릿
```

있으면 디버깅에 매우 유용.

---

# 60. Config 변경

추가:

```json
{
  "gemini_result_as_draft": true,
  "local_context_draft_enabled": true,
  "local_context_analysis_chars": 2500,
  "local_context_recent_template_window": 6
}
```

---

# 61. 기존 `auto_apply_ai_comment`

migration:

```text
auto_apply_ai_comment
→ gemini_result_as_draft
```

기존 값이 있으면 보존 가능.

---

# 62. ConfigService 버그도 아직 남아있다

현재 `save(data)`는:

```python
self.data = data
```

로 전체 설정을 교체.

UI partial save 시 key 유실 가능.

이전 작업지시대로:

```text
update_many
atomic save
```

이번에 반드시 처리.

---

# 63. Config atomic write

```python
tmp_path = config_path + ".tmp"

write tmp
os.replace(tmp_path, config_path)
```

---

# 64. 테스트 — Gemini focus

mock execute JS:

```text
editor found + active false
→ fail

editor found + active true
→ continue
```

---

# 65. 테스트 — paste verification fail

Cmd+V 후 editor unchanged.

Expected:

```text
Enter 0회
fallback local
```

---

# 66. 테스트 — stale Gemini answer

before_count = 4
send fails
after_count = 4

Expected:

```text
old answer returned = false
```

---

# 67. 테스트 — new response

before_count = 4
after_count = 5
5번째 text stable

→ Gemini answer.

---

# 68. 테스트 — local FOOD

title:

```text
광양 모리 히츠마부시 가족모임 후기
```

excerpt:

```text
장어덮밥을 먹고 마지막에는 오차즈케...
```

draft must contain at least one actual keyword:

```text
히츠마부시
장어
오차즈케
```

---

# 69. 테스트 — local TRAVEL

title:

```text
완도 아내의 정원
```

draft:

```text
아내의 정원
```

또는 title actual subject 포함.

없는 사실 생성 금지.

---

# 70. 테스트 — title only

excerpt empty.

Output:
- title token 기반
- 구체적 invented fact 없음

---

# 71. 테스트 — suffix

Local/Gemini/Spintax 모두
동일 source suffix resolver를 사용.

---

# 72. 테스트 — recommendation suffix empty

빈값이면 body only.

---

# 73. E2E 1

Gemini editor focus 성공.

결과:

```text
Gemini tab
→ prompt 보임
→ Enter
→ new response
→ clipboard
→ Naver editor에 Gemini draft
→ 사용자 Enter
```

---

# 74. E2E 2

Gemini JS OFF.

결과:

```text
Blind Cmd+V 실행 안 함
→ Local Context Draft
```

---

# 75. E2E 3

Gemini prompt paste fail.

결과:

```text
Enter 안 누름
→ Local Context Draft
```

---

# 76. E2E 4

Gemini timeout.

Local fallback.

---

# 77. E2E 5

Context extraction 성공 + Gemini disabled.

글 keyword 포함 local draft.

---

# 78. 절대 금지

Gemini 입력창 focus 확인 없이 Cmd+V.

---

# 79. 절대 금지

paste 검증 없이 Enter.

---

# 80. 절대 금지

before_count를 구해놓고 사용하지 않는 코드.

---

# 81. 절대 금지

Gemini failure를 숨기고 generic template만 삽입.

---

# 82. 절대 금지

Local draft에서 본문에 없는 구체적 사실 생성.

---

# 83. 절대 금지

Local algorithm을 "AI"라고 표시.

UI에는:

```text
글 내용 기반
```

으로 표시.

---

# 84. 구현 순서

## Phase A — Gemini focus/send fix

1. Editor resolver
2. JS focus
3. paste verification
4. send verification
5. fresh response tracking

다른 기능보다 먼저.

---

# 85. Phase B — Local Context Engine

1. token
2. stopword
3. category
4. subject
5. templates
6. repetition memory

---

# 86. Phase C — Fallback chain

```text
Gemini → local → Spintax
```

---

# 87. Phase D — UI/Config

- Draft source 표시
- Gemini result as draft
- Config update_many
- atomic save

---

# 88. Phase E — Regression

- Neighbor
- Recommendation
- Direct URL
- Gemini ON/OFF
- Gemini permission fail
- Context fail
- Stop
- Pacing
- suffix

---

# 89. 구현자에게 줄 최종 지시

```text
현재 Gemini 기존 Chrome 구현에서 가장 먼저 고칠 버그는
Gemini 입력 editor에 focus하지 않은 채 Cmd+V와 Enter를 보내는 것이다.

ExistingChromeGeminiBridge.generate_comment()에서
Chrome tab을 활성화한 뒤 반드시 Apple Events JavaScript로
실제 visible Gemini editor를 찾고 focus한다.

document.activeElement가 editor임을 검증한 후에만 Cmd+V한다.

Cmd+V 이후 editor innerText/value에 prompt가 실제 들어갔는지 검증한다.
검증 실패 시 Enter를 절대 누르지 말고 local contextual draft로 fallback한다.

현재 코드의 before_count는 계산만 하고 사용하지 않는다.
response freshness tracking에 반드시 사용한다.
새 response count가 증가하지 않으면 기존 마지막 답변을 반환하지 않는다.

Gemini 결과 사용 정책도 변경한다.
Gemini가 성공하면 그 결과를 네이버 댓글 draft로 사용하고,
최종 등록은 기존처럼 사용자 Enter 승인으로만 한다.

Gemini가 실패하면 단순 Spintax로 바로 가지 말고
새 `ContextualDraftEngine`을 호출한다.

ContextualDraftEngine은:
- title/excerpt/analysis_text 사용
- regex token 추출
- stopword 제거
- title token 가중치
- food/cafe/travel/beauty/product/finance/general 카테고리 점수
- 실제 글에 등장한 subject 하나 선택
- 카테고리별 짧고 정감 있는 1~2문장 template 선택
- 최근 template 6개 반복 회피
를 구현한다.

없는 사실을 만들어내지 않는다.
구체적인 subject를 찾지 못하면 generic warm fallback을 사용한다.

Draft fallback 우선순위:
1. Gemini
2. Local Context
3. Spintax

모든 draft는 기존 source-specific suffix resolver를 마지막에 통과한다.

또한 ConfigService.save(partial_dict)의 설정 유실 문제를
update_many + atomic save 방식으로 수정한다.

테스트에서 반드시 검증:
- focus 실패 시 paste/send 없음
- paste 실패 시 Enter 없음
- response count 증가 없으면 old response 반환 없음
- local draft가 실제 title/excerpt keyword를 최소 하나 사용
- recommendation suffix empty 존중
- 최종 네이버 submit은 사용자 Enter만
```

---

# END
