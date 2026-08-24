# NAVER FEED ASSISTANT
# HUMAN-LIKE COMMENT COMPOSER v2.1
# + LIKE POPULARITY GUARD MASTER WORK ORDER

> 저장소: `urangsu/logonthe`
> 기준 브랜치: `main`
> 문서 성격: **이전 Human-Like Comment Composer v2 작업지시서를 대체하는 통합 수정지시서**
>
> 이번 범위:
> 1. 글 내용을 읽고 반응하는 로컬 댓글 엔진을 한 단계 더 고도화
> 2. “저도 가보고 싶어요”, “다음에 가면 들러봐야겠어요”처럼
>    댓글 작성자인 **‘나’의 의향·행동·취향**이 자연스럽게 들어가는 반응 유형 추가
> 3. 사람이 쓴 것처럼 문장 구조와 반응 목적을 다양화
> 4. Gemini는 optional 대안으로 유지
> 5. 공감수가 **999 또는 999+ 이상인 게시글은 공감 클릭 금지**
> 6. 해당 블로그의 **일 방문자 수가 10,000명을 초과하면 공감 클릭 금지**
> 7. 방문자 수를 확인할 수 없는 경우 정책을 명시적으로 처리
> 8. 댓글은 계속 작성 가능하되 공감만 eligibility 조건에 따라 skip
> 9. 기존 Human-in-the-loop 최종 Enter 승인, Pacing, History,
>    source-specific suffix, LikeState 3-state 원칙은 유지
>
> Gemini/API 여부와 무관하게 로컬 댓글 엔진만으로도
> “본문을 읽고 짧게 실제 반응을 남긴 느낌”을 만들어야 한다.

---

# 0. 현재 저장소에서 확인된 출발점

현재 `services/contextual_draft.py`는:

```text
제목 + excerpt
→ 카테고리 점수
→ subject 1개
→ 카테고리별 고정 템플릿 2~4개
→ random.choice
→ 최근 template_id 6개 회피
```

구조다.

현재 실제 템플릿에는:

```text
{subject} 관점이 눈에 들어오네요.
{subject}가 눈에 쏙 들어오네요.
{subject} 얘기가 특히 눈에 들어오네요.
보기 편했어요.
```

같은 상투 표현이 남아 있다.

이 구조를 단순히 템플릿 수만 늘리는 식으로 유지하지 않는다.

---

# 1. 이번 댓글 엔진의 최종 목표

좋은 댓글은 “글 요약”이나 “글 평가”가 아니다.

다음 네 요소 중 2~3개를 자연스럽게 조합한다.

```text
A. 글에서 실제로 나온 구체 포인트
B. 그 포인트에 대한 감정/칭찬
C. 댓글 작성자인 '나'의 의향·취향·행동
D. 짧은 자연스러운 마무리
```

예:

```text
히츠마부시 진짜 맛있어 보이네요.
다음에 가면 오차즈케까지 꼭 먹어보고 싶어요 :)
```

```text
딸기라떼 색감부터 너무 맛있어 보여요.
저도 이런 분위기 카페 좋아해서 한번 가보고 싶네요 ㅎㅎ
```

```text
장도는 산책하면서 전시까지 같이 볼 수 있는 게 좋네요.
여수 가면 한번 들러봐야겠어요 :)
```

이런 문장은 단순:

```text
관점이 눈에 들어오네요
```

보다 댓글 작성자의 존재가 느껴진다.

---

# 2. 중요한 원칙 — ‘나’를 넣되 경험을 위조하지 않는다

허용:

```text
저도 가보고 싶네요
저도 먹어보고 싶어요
저도 이런 분위기 좋아해요
다음에 가면 들러봐야겠어요
근처 갈 일 있으면 한번 가보고 싶네요
여행 가게 되면 코스에 넣어봐야겠어요
저라면 이 메뉴부터 먹어보고 싶네요
다음에 주문한다면 이 조합으로 먹어보고 싶어요
저도 이런 스타일 좋아해서 눈길이 가네요
```

금지:

```text
저도 가봤는데
저도 먹어봤는데
저도 써봤는데
저도 투자하고 있는데
저도 이용해봤는데
저도 예전에 방문했어요
```

실제로 하지 않은 과거 경험은 생성하지 않는다.

즉:

```text
미래 의향 / 현재 취향 / 현재 반응
```

은 허용.

```text
과거 경험 / 구매 / 방문 사실
```

은 금지.

---

# 3. First-Person Intent Layer 추가

기존 Reaction Archetype과 별개로:

```text
FirstPersonIntent
```

레이어를 추가한다.

---

# 4. FirstPersonIntent 종류

```python
class FirstPersonIntent(str, Enum):
    NONE = "none"

    WANT_TO_VISIT = "want_to_visit"
    PLAN_TO_VISIT = "plan_to_visit"

    WANT_TO_TRY = "want_to_try"
    WANT_TO_EAT = "want_to_eat"
    WANT_TO_DRINK = "want_to_drink"

    LIKE_THIS_STYLE = "like_this_style"
    LIKE_THIS_MOOD = "like_this_mood"

    WOULD_CHOOSE = "would_choose"
    WOULD_ADD_TO_ROUTE = "would_add_to_route"

    CURIOUS_TO_SEE = "curious_to_see"
```

---

# 5. WANT_TO_VISIT

조건:

```text
여행
카페
맛집
전시
정원
공원
숙소
오프라인 장소
```

문장 pool:

```text
저도 한번 가보고 싶네요
저도 기회 되면 가보고 싶어요
근처 갈 일 있으면 한번 들러보고 싶네요
다음에 이쪽 가면 한번 들러봐야겠어요
여행 가게 되면 한번 가보고 싶네요
```

---

# 6. PLAN_TO_VISIT

WANT보다 조금 더 행동 느낌.

```text
다음에 여수 가면 한번 들러봐야겠어요
광양 쪽 갈 때 기억해뒀다가 가보고 싶네요
근처 가게 되면 코스에 넣어봐야겠어요
```

중요:

지역/entity가 글에서 실제 확인된 경우만 사용.

---

# 7. WANT_TO_EAT

음식:

```text
저도 한번 먹어보고 싶네요
사진 보니까 저도 먹어보고 싶어져요
다음에 가면 이 메뉴는 꼭 먹어보고 싶네요
저라면 이 메뉴부터 먹어볼 것 같아요
```

---

# 8. WANT_TO_DRINK

음료/카페:

```text
저도 이건 한번 마셔보고 싶네요
다음에 가면 이 메뉴부터 주문해보고 싶어요
```

---

# 9. LIKE_THIS_STYLE

뷰티/제품/인테리어/패션:

```text
저도 이런 자연스러운 느낌 좋아해요
이런 스타일은 저도 취향이라 더 눈길이 가네요
저도 깔끔한 느낌 좋아해서 괜찮아 보여요
```

`눈길이 가네요`는 기존 generic phrase로 과사용하지 않는다.
이 문구가 필요하다면 매우 낮은 weight.

더 자연스러운 대안:

```text
저도 이런 스타일 좋아해서 괜히 더 보게 되네요
```

---

# 10. LIKE_THIS_MOOD

카페/여행/일상:

```text
저도 이런 편안한 분위기 좋아해요
이런 느낌의 공간은 저도 좋아해서 가보고 싶네요
저도 조용한 분위기 좋아해서 괜찮아 보여요
```

단:

```text
조용한
```

은 실제 text signal이 있을 때만.

---

# 11. WOULD_CHOOSE

여러 선택지가 실제로 있을 때.

예:

```text
파스타 / 리조또
딸기라떼 / 녹차라떼
```

허용:

```text
둘 다 맛있어 보이는데 저는 파스타부터 먹어보고 싶네요 :)
```

주의:

이건 사용자 개인 취향을 가볍게 표현하는 것이므로
후보 중 낮은 빈도로만 사용.

---

# 12. WOULD_ADD_TO_ROUTE

여행/장소:

```text
여수 가면 코스에 한번 넣어봐야겠어요
근처 여행할 때 들러보면 좋을 것 같네요
```

실제 지역/장소가 있어야 함.

---

# 13. FirstPersonIntent 사용률

모든 댓글에:

```text
저도
제가
다음에
```

를 넣으면 또 템플릿 티가 난다.

기본 목표:

```text
전체 댓글의 약 35~55%:
First-person intent 포함 가능

나머지:
구체 반응형
```

실제 rate는 StyleLearner가 조절.

---

# 14. 동일 표현 반복 방지

최근 10개 댓글에서:

```text
저도 가보고 싶네요
```

가 2회 이상 나오면
그 exact ending은 cooldown.

다른 표현 사용:

```text
다음에 한번 들러봐야겠어요
기회 되면 가보고 싶어요
근처 가면 기억해둬야겠네요
```

---

# 15. "저도" cooldown

최근 5개 중 2개가 `저도`로 시작하면:

```text
저도
```

opener penalty.

대신:

```text
다음에
근처 갈 일 있으면
사진 보니까
이 메뉴는
```

등 사용.

---

# 16. 사용자 말투 Seed v2.1

기본 선호:

```text
~네요
~해요
~보여요
~같아요
~싶네요
~봐야겠어요
~먹어보고 싶어요
~가보고 싶어요
```

가벼운 반응어:

```text
진짜
은근
딱
괜찮아 보여요
좋네요
맛있어 보여요
```

`은근`, `딱`은 문맥에 맞을 때만.

---

# 17. 사람 느낌을 높이는 핵심

AI/템플릿 댓글은 자주:

```text
A는 B해 보이네요.
C도 D해 보여요.
```

형태만 반복한다.

새 Composer는 문장 기능을 다르게 한다.

예:

```text
[반응] + [행동 의향]
```

```text
[구체 정보] + [처음 알았다는 반응]
```

```text
[메뉴 A/B] + [내 선택]
```

```text
[분위기] + [내 취향]
```

---

# 18. Reaction Archetype 확장

기존 10종에 추가:

```text
PERSONAL_VISIT_INTENT
PERSONAL_TASTE_INTENT
PERSONAL_STYLE_PREFERENCE
PERSONAL_CHOICE
FUTURE_PLAN
```

---

# 19. HUMAN-LIKE COMMENT PIPELINE

최종:

```text
Naver Post
↓
ContentContextExtractor
↓
CommentContextAnalyzer
↓
Category/Subcategory
↓
Entity Role Extraction
↓
Evidence Sentence
↓
Reaction Archetype candidates
↓
FirstPersonIntent candidates
↓
UserStyleProfile
↓
CandidateGenerator 6~10개
↓
Hallucination guard
↓
Generic phrase penalty
↓
Recent repetition penalty
↓
User style similarity score
↓
Best Candidate
↓
Source Suffix
↓
Naver editor
↓
User 수정
↓
Enter
```

---

# 20. 현재 ContextualDraftEngine의 generic phrase blacklist

전역 banned:

```text
관점이 눈에 들어오네요
관점이 한눈에
눈에 쏙 들어오네요
얘기가 눈에 들어오네요
보기 편했어요
보기 편하네요
유익한 정보
좋은 정보
도움이 되었습니다
도움이 됐어요
작성자님
인상적입니다
잘 보고 갑니다
정리가 잘 되어
깔끔하게 정리
```

---

# 21. 완전 금지와 repetition penalty 구분

완전 금지:

```text
유익한 정보 감사합니다
잘 보고 갑니다
작성자님
인상적입니다
```

강한 penalty:

```text
눈에 들어오네요
보기 편했어요
정리가 깔끔
```

---

# 22. 카테고리 확장

```text
FOOD_RESTAURANT
FOOD_MENU
CAFE
TRAVEL_PLACE
TRAVEL_EVENT
TRAVEL_EXHIBITION
HOTEL_STAY
BEAUTY_HAIR
BEAUTY_NAIL
PRODUCT
SHOPPING
FINANCE_MARKET
FINANCE_INVESTMENT
DAILY
PARENTING
PET
FITNESS
WORK
GENERAL
```

---

# 23. FOOD 댓글 유형

가능한 조합:

```text
메뉴 비주얼
먹는 방식
두 메뉴 중 하나
곁들임/마무리
다음 방문 시 주문 의향
```

---

# 24. FOOD 예

실제 context:

```text
히츠마부시
오차즈케
```

후보:

```text
히츠마부시 진짜 맛있어 보이네요. 오차즈케로 마무리하는 것도 좋네요 :)
```

```text
오차즈케까지 먹는 방식이 좋네요. 저도 한번 제대로 먹어보고 싶어요 :)
```

```text
장어 비주얼이 너무 좋네요. 다음에 가면 히츠마부시는 꼭 먹어보고 싶어요!
```

---

# 25. CAFE 예

context:

```text
딸기라떼
녹차라떼
마당
```

후보:

```text
딸기라떼 색감부터 맛있어 보여요. 저도 이런 분위기 카페 좋아해서 한번 가보고 싶네요 :)
```

```text
마당 있는 분위기가 참 편안해 보이네요. 다음에 근처 가면 들러보고 싶어요 :)
```

```text
딸기라떼랑 녹차라떼 둘 다 괜찮아 보이는데 저는 딸기라떼부터 먹어보고 싶네요 ㅎㅎ
```

---

# 26. TRAVEL 예

context:

```text
장도
전시
산책
```

후보:

```text
산책하면서 전시까지 같이 볼 수 있는 게 좋네요. 여수 가면 한번 들러봐야겠어요 :)
```

```text
장도 사진 분위기가 편안해서 좋네요. 저도 천천히 한번 걸어보고 싶어요 :)
```

---

# 27. BEAUTY 예

context:

```text
시스루 쉐도우펌
자연스러운 스타일
```

후보:

```text
시스루 쉐도우펌 느낌이 자연스럽고 괜찮네요. 저도 이런 스타일 좋아해요 :)
```

```text
과하지 않고 자연스러운 느낌이 좋네요. 다음에 펌할 때 이런 스타일도 한번 보고 싶어요.
```

---

# 28. FINANCE 예

1인칭 미래 행동을 조심스럽게 허용.

```text
ETF 구성 차이가 확실하네요. 저도 비교할 때 이 부분은 같이 봐야겠어요.
```

단 투자 권유나 실제 투자 경험 생성 금지.

---

# 29. GENERAL / DAILY

```text
이런 분위기의 하루는 저도 좋아해요. 사진도 편안한 느낌이라 좋네요 :)
```

```text
사진 보니까 괜히 저도 한번 해보고 싶어지네요 :)
```

실제 행동 대상이 있어야 함.

---

# 30. UserStyleLearner는 FirstPersonIntent도 학습

History의 submitted_text에서:

```text
저도
다음에
가보고
먹어보고
들러봐야
해보고
좋아해요
```

빈도 분석.

사용자가 실제로 자주 유지하면:

```text
first_person_rate ↑
```

자주 삭제하면:

```text
first_person_rate ↓
```

---

# 31. 사용자 수정 학습

현재 History에는:

```text
draft
submitted_text
```

가 있으므로 활용.

예:

초안:

```text
분위기가 좋아 보여요 :)
```

final:

```text
저도 이런 분위기 좋아해서 한번 가보고 싶네요 ㅎㅎ
```

학습:

```text
first-person intent ↑
future intent ↑
ㅎㅎ ↑
":)" ↓ 가능
```

---

# 32. Candidate Score v2.1

```python
score =
    relevance * 2.2
  + specificity * 1.3
  + user_style_similarity * 1.5
  + first_person_fit * 0.8
  + archetype_fit * 1.0
  + length_fit * 0.5
  - repetition_penalty * 2.2
  - generic_penalty * 2.5
  - hallucination_risk * 5.0
```

---

# 33. First-person fit

무조건 높이지 않는다.

FOOD/CAFE/TRAVEL:

```text
높음
```

FINANCE:

```text
중간
```

정보성 글:

```text
낮음
```

---

# 34. 후보 최소 개수

한 글당:

```text
6~10개
```

내부 후보.

---

# 35. Candidate 다양성 조건

최소:

```text
2개 = pure reaction
2개 = first-person intent
1개 = specific detail
1개 = short warm
```

가능할 때.

---

# 36. 최근 댓글 중복 검사

History 최근 20개 `submitted_text`.

Jaccard + difflib SequenceMatcher.

```text
similarity >= 0.60
→ 큰 penalty
```

---

# 37. 같은 opener 반복

최근:

```text
저도
사진 보니까
분위기가
```

시작 빈도 기록.

한 opener가 최근 6개 중 3개 이상:

```text
cooldown
```

---

# 38. 같은 ending 반복

최근:

```text
가보고 싶네요 :)
```

3번:

다음 후보는:

```text
들러봐야겠어요
가보고 싶어요
기억해둬야겠네요
```

등으로 이동.

---

# 39. UI 편의 기능

기존 계획 유지:

```text
[ 다른 댓글 ]
[ 더 짧게 ]
[ 조금 더 친근하게 ]
```

추가 권장:

```text
[ 내가 하는 말투로 ]
```

이 버튼은:

```text
first-person intent weight
+
learned style weight
```

를 올린 후보를 선택.

---

# 40. "내가 하는 말투로"는 과거 경험 생성 금지

이 버튼도:

```text
저도 가봤는데
```

등을 만들면 안 됨.

미래/취향만.

---

# 41. Gemini 역할

기본:

```text
Local HumanLike Composer 즉시
```

Gemini:

```text
선택 대안
```

추천.

Gemini가 정상화돼도 workflow의 필수 dependency가 아니다.

---

# 42. 이제 공감 기능에 Popularity Guard 추가

사용자 요구:

```text
좋아요 999
좋아요 999+
→ 공감 클릭하지 않음

블로그 일 방문자 > 10,000
→ 공감 클릭하지 않음
```

댓글 여부와는 별개.

---

# 43. Like 처리 최종 Pipeline

현재:

```text
Like button
↓
LikeState
↓
LIKED / NOT_LIKED / UNKNOWN
```

앞에 Eligibility 추가.

새:

```text
Post
↓
LikePopularityGuard
   ├─ like count
   └─ daily visitor
↓
ELIGIBLE / SKIP / UNKNOWN
↓
LikeState
↓
safe click
```

---

# 44. 신규 상태

```python
class LikeEligibility(str, Enum):
    ELIGIBLE = "eligible"

    SKIP_LIKE_COUNT = "skip_like_count"
    SKIP_DAILY_VISITORS = "skip_daily_visitors"

    UNKNOWN_LIKE_COUNT = "unknown_like_count"
    UNKNOWN_DAILY_VISITORS = "unknown_daily_visitors"
```

혹은 result object에 reason.

---

# 45. LikeEligibilityResult

```python
@dataclass
class LikeEligibilityResult:
    eligible: bool

    like_count: int | None = None
    like_count_raw: str | None = None

    daily_visitors: int | None = None
    daily_visitors_raw: str | None = None

    reason: str | None = None
```

---

# 46. Config

```json
{
  "like_popularity_guard_enabled": true,

  "like_count_skip_threshold": 999,

  "daily_visitor_guard_enabled": true,
  "daily_visitor_skip_threshold": 10000,

  "daily_visitor_unknown_policy": "skip_like"
}
```

---

# 47. 공감수 기준

정확히:

```text
like_count >= 999
→ 공감 클릭 금지
```

따라서:

```text
998
→ 가능

999
→ skip

999+
→ skip

1,000
→ skip

1천
→ skip

1.2천
→ skip

1만
→ skip
```

---

# 48. Compact count parser

신규 helper:

```text
naver/count_parser.py
```

---

# 49. Count parsing 지원

```text
"0"
"12"
"999"
"999+"
"1,234"

"1천"
"1.2천"
"9.9천"

"1만"
"1.2만"

"10K"
"1.2K"
```

K는 Naver DOM에서 실제 관찰될 때만 필요하나
parser 자체는 지원 가능.

---

# 50. Parse examples

```python
parse_count("999")    == 999
parse_count("999+")   == 999
parse_count("1,234")  == 1234
parse_count("1.2천")  == 1200
parse_count("1만")    == 10000
parse_count("1.2만")  == 12000
```

---

# 51. 999+ 의미

실제 값은:

```text
>= 999
```

다.

threshold가 999이므로
999로 parse해도 skip 조건 만족.

---

# 52. Like count resolver

`MobileDOMResolver`에:

```python
get_like_count_text(page, like_btn=None)
```

추가.

우선 like button scope 내부에서 찾는다.

---

# 53. 후보 signal

실제 current DOM을 우선 확인하되
후보 예:

```text
.u_likeit_text
._count
span[class*='count']
span[class*='num']
aria-label
button innerText
```

---

# 54. Like count는 button 전체 innerText fallback 가능

하지만:

```text
공감 999
```

같이 text가 섞일 수 있으므로
count parser가 숫자 부분 추출.

---

# 55. Like count UNKNOWN 정책

공감수 text를 못 읽는 경우.

사용자 요구는:

```text
999 이상을 누르지 않기
```

이다.

안전 기본:

```text
like_count_unknown_policy = "continue"
```

또는 conservative:

```text
skip_like
```

여기서는 사용자 경험과 안정성 균형상:

```text
like count 자체가 안 보여도 LikeState가 안전하게 판정되면
daily visitor 조건까지 통과 후 공감 가능
```

으로 둘 수 있다.

단 설정 가능하게.

추천 config:

```json
"like_count_unknown_policy": "continue"
```

---

# 56. 일 방문자 수 Guard

현재 저장소에는:

```text
일 방문자 수 추출 코드
DOM selector
실측 fixture
```

가 없다.

따라서 추측 selector를 Production에 바로 박지 않는다.

먼저 DOM diagnostics.

---

# 57. Daily Visitor의 의미

반드시:

```text
오늘/일 방문자
```

를 읽는다.

누적 방문자:

```text
total visitors
```

와 혼동 금지.

---

# 58. 사용자 기준

```text
daily_visitors > 10000
→ 공감 skip
```

사용자가 표현한 “1만이 넘으면”을 literal하게 따르면:

```text
10,000
→ 허용
10,001 이상
→ skip
```

다만 운영상 경계가 단순하도록:

```text
>= 10000
```

로 할 수도 있음.

이번 지시서 기본은 사용자 문장을 그대로 반영하여:

```text
> 10,000
```

으로 한다.

Config:

```json
"daily_visitor_skip_threshold": 10000,
"daily_visitor_skip_operator": "gt"
```

원하면 이후 `gte`.

---

# 59. DailyVisitor resolver architecture

신규:

```text
services/blog_popularity.py
```

---

# 60. BlogPopularityService

```python
class BlogPopularityService:

    def get_daily_visitors(
        self,
        context,
        blog_id: str
    ) -> DailyVisitorResult:
        ...
```

---

# 61. BlogPopularityResult

```python
@dataclass
class DailyVisitorResult:
    value: int | None
    raw_text: str | None
    source: str | None
    confidence: str
    error: str | None = None
```

---

# 62. 방문자 조회 page

현재 BrowserSession은:

```text
feed_page
detail_page
gemini_page
```

가 있다.

방문자 확인 때문에 detail_page를 다른 URL로 보내면
현재 post 처리 흐름이 깨진다.

따라서:

```text
stats_page
```

를 lazy 생성.

---

# 63. BrowserSession 확장

```python
self.stats_page: Optional[Page] = None
```

```python
def get_stats_page(self):
    if not self.stats_page or self.stats_page.is_closed():
        self.stats_page = self.context.new_page()
    return self.stats_page
```

---

# 64. stats_page 목적

오직:

```text
블로그 홈/profile의 공개 방문자 정보 확인
```

---

# 65. stats_page lifecycle

Session당 1개.

매 글 new_page 금지.

---

# 66. Blog ID cache

같은 블로그의 여러 글이 나올 수 있음.

```python
cache: dict[str, DailyVisitorResult]
```

Session 동안 한 번 확인하면 재사용.

---

# 67. Cache TTL

한 session에서는:

```text
동일 blog_id 1회
```

이면 충분.

장기 persistent cache는 V2.

---

# 68. 방문자 DOM 진단 우선

신규 diagnostic:

```text
diagnostics/inspect_blog_visitors.py
```

입력:

```text
blog_id
```

출력:

```text
mobile blog home
desktop blog home
candidate text
nearby labels
outerHTML
```

---

# 69. 방문자 DOM 실측 대상

확인:

```text
오늘
TODAY
오늘 방문자
방문자
전체
누적
```

어떤 label과 value가 묶여 있는지.

---

# 70. selector 확정 원칙

Priority:

```text
label text "오늘" / "오늘 방문자"
→ sibling/value relation

stable id/data attribute

semantic container

class prefix

hashed class last
```

---

# 71. 절대 금지

```text
페이지에서 보이는 첫 번째 큰 숫자
```

를 방문자 수라고 간주.

---

# 72. 누적 방문자 오인 방지

예:

```text
오늘 3,241
전체 12,500,000
```

이면:

```text
3,241
```

만 사용.

---

# 73. 블로그 홈 URL

`blog_id`로 공개 blog home에 접근하는 URL은
현재 실제 Naver 동작을 확인 후 확정.

후보를 코드에 추측으로 강제하지 않는다.

diagnostic에서:

```text
https://m.blog.naver.com/{blog_id}
```

를 먼저 확인할 수 있으나
Production resolver는 실측 후.

---

# 74. 일 방문자 정보가 공개되지 않는 블로그

```text
UNKNOWN
```

---

# 75. UNKNOWN daily visitor 정책

사용자 목표는:

```text
1만 초과 블로그의 공감을 누르지 않는 것
```

이므로 conservative default 권장:

```json
"daily_visitor_unknown_policy": "skip_like"
```

즉 방문자 수를 확인하지 못하면:

```text
공감은 skip
댓글은 계속
```

---

# 76. 왜 UNKNOWN을 skip 권장하나

UNKNOWN을 eligible로 두면:

```text
실제로 5만 방문자 블로그
+
visitor DOM 파싱 실패
→ 공감 클릭
```

이 가능.

사용자 조건을 확실히 지키려면
unknown = skip이 논리적으로 맞다.

---

# 77. UI 옵션

```text
공감 대상 제한

☑ 공감수 높은 글 제외
공감수 [999] 이상이면 공감 안 함

☑ 일 방문자 많은 블로그 제외
일 방문자 [10,000] 초과면 공감 안 함

방문자 수 확인 불가 시
● 공감 안 함
○ 다른 조건만 보고 진행
```

---

# 78. LikeEligibilityService

신규:

```text
services/like_eligibility.py
```

---

# 79. API

```python
class LikeEligibilityService:

    def evaluate(
        self,
        detail_page,
        stats_page,
        post,
        config
    ) -> LikeEligibilityResult:
        ...
```

---

# 80. 평가 순서

비용이 싼 것부터.

```text
1. Like count
2. Daily visitor cache
3. 필요 시 stats_page 조회
```

---

# 81. Like count에서 바로 skip이면 visitor 조회 불필요

예:

```text
공감 999+
```

즉시:

```text
SKIP_LIKE_COUNT
```

stats_page navigation 하지 않는다.

---

# 82. Daily visitor 조회 조건

```text
like_count < threshold
AND
daily visitor guard enabled
```

일 때만.

---

# 83. PostProcessor integration

현재:

```python
LikeInteractionService.safe_process_like()
```

직전.

새:

```python
elig = like_eligibility.evaluate(...)

if not elig.eligible:
    LikeProcessResult(
        action_taken=False,
        skip_reason=...
    )
else:
    safe_process_like(...)
```

---

# 84. LikeProcessResult 확장

현재:

```text
state_before
action_taken
state_after
error
```

추가:

```python
eligibility_reason: str | None
like_count: int | None
daily_visitors: int | None
```

---

# 85. Like skip은 FAILURE가 아님

조건에 따라 의도적으로 skip한 것.

따라서:

```text
error
```

로 기록하지 않는다.

---

# 86. 로그

공감수 skip:

```text
🚫 [LIKE] 공감수 999+ — 설정 기준(999 이상)에 따라 공감하지 않습니다.
```

---

# 87. 방문자 skip

```text
🚫 [LIKE] 일 방문자 12,384명 — 설정 기준(10,000 초과)에 따라 공감하지 않습니다.
```

---

# 88. 방문자 UNKNOWN

```text
ℹ️ [LIKE] 일 방문자 수를 확인하지 못했습니다.
설정 정책(skip_like)에 따라 공감을 건너뜁니다.
```

---

# 89. 댓글은 계속 진행

중요:

```text
Like Eligibility skip
≠ Post skip
```

즉:

```text
공감 X
댓글 O
```

가능.

---

# 90. 처리 예 1

```text
공감수 1,245
```

결과:

```text
visitor 조회 안 함
공감 skip
댓글 진행
```

---

# 91. 처리 예 2

```text
공감수 82
일 방문자 14,532
```

결과:

```text
공감 skip
댓글 진행
```

---

# 92. 처리 예 3

```text
공감수 53
일 방문자 2,314
LikeState NOT_LIKED
```

결과:

```text
공감 클릭
상태 verify
댓글 진행
```

---

# 93. 처리 예 4

```text
공감수 53
일 방문자 UNKNOWN
unknown policy = skip_like
```

결과:

```text
공감 skip
댓글 진행
```

---

# 94. 처리 예 5

```text
공감수 53
일 방문자 2,314
LikeState UNKNOWN
```

결과:

```text
기존 안전 정책대로 공감 skip
댓글 진행
```

---

# 95. Popularity Guard와 LikeState 순서

```text
Eligibility
↓
LikeState
```

이유:

공감수 999+면
LikeState resolution/click 필요 없음.

---

# 96. 단 기존 LIKED 글

이미 LIKED인 경우:

공감수/visitor guard가 굳이 필요 없을 수 있음.

더 효율적인 순서:

```text
Like button
↓
LikeState
↓
LIKED
  → 아무 것도 안 함
NOT_LIKED
  → Eligibility 검사
UNKNOWN
  → 아무 것도 안 함
```

이 방식이 더 효율적.

최종 권장 순서:

```text
1. resolve LikeState
2. LIKED → done
3. UNKNOWN → skip
4. NOT_LIKED → Popularity Guard
5. eligible → click
```

---

# 97. 따라서 safe_process_like 리팩터링

현재 `safe_process_like()` 안에서
state + click을 모두 처리한다.

분리 권장:

```text
resolve_like_state
evaluate_popularity
click_like_and_verify
```

---

# 98. LikeInteractionService API

```python
state = resolve_like_state(page)

if state == LIKED:
    ...

if state == UNKNOWN:
    ...

eligibility = popularity_guard.evaluate(...)

if not eligibility.eligible:
    ...

return click_and_verify(...)
```

---

# 99. stats_page를 LikeInteractionService가 알 필요 없음

PopularityGuard에 전달.

책임 분리.

---

# 100. Controller에서 stats_page 전달

```python
stats_page = self.session.get_stats_page()
```

하지만 lazy.

daily guard disabled면 page 생성하지 않기.

---

# 101. Processor constructor

```python
stats_page: Optional[Page]
like_eligibility_service
```

또는 BrowserSession 참조보다
필요 page만 전달.

---

# 102. History

Like record:

```json
"like": {
  "state_before": "not_liked",
  "action": "skipped",
  "state_after": "not_liked",

  "eligibility_reason": "daily_visitors_over_threshold",
  "like_count": 82,
  "daily_visitors": 14532
}
```

---

# 103. 개인정보/데이터 최소화

daily visitor 수는 공개 통계지만
장기 History에 꼭 저장할 필요는 없음.

Debug 목적이라면 저장.

사용자가 원치 않으면:

```text
eligibility_reason만 저장
```

가능.

이번 권장:

```text
count 값도 저장
```

왜 skip됐는지 확인 가능.

---

# 104. like count history

같은 이유.

---

# 105. CountParser unit tests

최소:

```text
0
15
998
999
999+
1,000
1,234
1천
1.2천
9.9천
1만
1.2만
```

---

# 106. Threshold unit test

```text
998 → eligible
999 → skip
999+ → skip
1000 → skip
```

---

# 107. Daily visitor threshold test

기본 operator `gt`.

```text
9999 → eligible
10000 → eligible
10001 → skip
12000 → skip
```

---

# 108. Unknown visitor test

```text
None
+
policy skip_like
→ skip
```

---

# 109. Cache test

동일 blog_id 5개 post.

visitor page navigation:

```text
1회
```

---

# 110. Different blog test

3 blog IDs:

```text
최대 3회
```

---

# 111. visitor selector fixture

실제 DOM 확보 후:

```text
tests/fixtures/blog_daily_visitor.html
tests/fixtures/blog_total_and_daily_visitors.html
```

---

# 112. 누적/일 방문자 구분 test

```text
오늘 8,231
전체 9,999,999
```

결과:

```text
8231
```

---

# 113. 1만 표기 test

```text
오늘 1.2만
```

→ 12000.

---

# 114. DOM 미확정 시 배포 Gate

Daily visitor guard를 Production ON으로 만들기 전에:

```text
최소 5개 서로 다른 블로그
```

에서 실제 DOM 확인.

---

# 115. DOM 확인 실패 상태

기능을 억지 구현하지 말고:

```text
daily visitor = UNKNOWN
```

정책 적용.

---

# 116. 댓글 고도화 테스트셋

실제 개인정보 제거한 40~60개 샘플.

---

# 117. 필수 댓글 테스트 유형

```text
Food
Cafe
Travel
Exhibition
Beauty
Product
Finance
Daily
```

---

# 118. First-person 후보 test

FOOD 샘플에서
6~10 후보 중 최소 2개:

```text
먹어보고 싶
다음에
저도
```

계열.

---

# 119. TRAVEL first-person test

최소 2개:

```text
가보고 싶
들러봐야
코스에
```

---

# 120. 과거 경험 위조 test

모든 후보에서:

```text
가봤
먹어봤
써봤
이용해봤
```

같은 과거 경험 표현 금지.

---

# 121. Generic phrase test

모든 후보:

```text
관점
유익
잘 보고 갑니다
작성자님
인상적
```

0건.

---

# 122. Recent repetition test

같은 category 20개 생성.

exact normalized sentence duplicate:

```text
<= 2회
```

권장.

---

# 123. opener diversity

최근 10개:

```text
저도
```

로 시작하는 비중 너무 높지 않게.

예:

```text
<= 40%
```

---

# 124. suffix와 first-person 본문 분리

Recommendation suffix:

```text
시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)
```

가 본문과 겹쳐:

```text
저도 가보고 싶어요
제 블로그도 와주세요
```

두 개의 행동 문장이 이어질 수 있음.

이건 괜찮지만 과하면 영업성 느낌.

따라서 Recommendation에서는
본문 first-person intent rate를 약간 낮추는 것도 검토.

예:

```text
NEIGHBOR: 0.50
RECOMMENDATION: 0.38
```

---

# 125. 추천피드에서 방문유도 꼬리말이 있으므로

본문은:

```text
구체적 칭찬
+
짧은 개인 반응
```

정도.

---

# 126. Gemini Prompt도 First-person 방향 반영

Gemini 대안 prompt에:

```text
필요하면 "저도 가보고 싶네요", "다음에 가면 한번 들러보고 싶어요"처럼
댓글 작성자의 현재 취향이나 미래 의향을 한 번 자연스럽게 넣어도 된다.

하지만 실제로 방문/구매/사용한 것처럼 과거 경험을 지어내지 말 것.
```

추가.

---

# 127. Gemini Prompt에서 강제하지 않기

매번 “저도” 넣으라고 하면 반복.

```text
필요하면 / 자연스러우면
```

으로 지시.

---

# 128. UI 최종 예

```text
댓글 생성
● 글 내용 기반
○ Gemini 우선
○ 기본 템플릿

댓글 말투
☑ 내 말투 학습 사용

[ 다른 댓글 ]
[ 더 짧게 ]
[ 조금 더 친근하게 ]
[ 내가 하는 말투로 ]

공감 대상 제한
☑ 공감 999 이상 제외
☑ 일 방문자 10,000 초과 블로그 제외

방문자 확인 실패
● 공감 안 함
○ 다른 조건만 보고 진행
```

---

# 129. Like skip UI badge

현재 글:

```text
❤️ 공감: 건너뜀 (공감 999+)
```

또는:

```text
❤️ 공감: 건너뜀 (일 방문자 1.2만)
```

---

# 130. session summary

추가:

```text
공감 인기글 제외: 5
공감 방문자 기준 제외: 3
공감 방문자 확인불가 제외: 2
```

P2.

---

# 131. 코드 수정 파일 예상

```text
services/contextual_draft.py
→ legacy fallback

services/comments/analyzer.py
services/comments/archetypes.py
services/comments/style_profile.py
services/comments/learner.py
services/comments/scorer.py
services/comments/composer.py

services/like_eligibility.py
services/blog_popularity.py

naver/count_parser.py
naver/resolver.py

browser/session.py

app/models.py
app/processor.py
app/controller.py
app/state.py

services/config.py
services/history.py
services/ai_prompt.py

ui/main_window.py

diagnostics/inspect_blog_visitors.py

tests/
```

---

# 132. ConfigService 수정 재강조

현재 저장소에서 `save(data)`가 전체 dict를 교체하는 구조라면
이번에도 반드시:

```text
update_many
atomic save
```

로 수정.

새 popularity config가 UI 저장 과정에서 사라지면 안 됨.

---

# 133. Schema

현재 버전에서 한 단계 올리기.

예:

```text
schema_version = 4
```

실제 현재 schema와 migration history 확인 후 결정.

---

# 134. Migration

기존 user config의:

```text
general_suffix
recommendation_suffix
pacing
gemini
```

값 절대 덮어쓰지 않는다.

새 key만 default 추가.

---

# 135. 작업 구현 Phase

## Phase 0 — Config Integrity

```text
update_many
atomic save
schema migration
```

---

# 136. Phase 1 — CountParser + Like Count

```text
like count text resolver
compact parser
999 threshold
```

아직 visitor 없음.

---

# 137. Phase 2 — Daily Visitor Diagnostics

```text
inspect_blog_visitors.py
5개 이상 실제 blog DOM
```

Gate 없이 selector 확정 금지.

---

# 138. Phase 3 — BlogPopularityService

```text
stats_page
cache
daily visitor parser
unknown policy
```

---

# 139. Phase 4 — LikeEligibility integration

```text
LikeState
→ NOT_LIKED만
→ popularity guard
→ click
```

---

# 140. Phase 5 — CommentContextAnalyzer v2

```text
entity role
phrase extraction
evidence
subcategory
```

---

# 141. Phase 6 — FirstPersonIntent

```text
visit
eat
drink
style
plan
choice
```

---

# 142. Phase 7 — Candidate Generator + Scorer

```text
6~10 candidate
generic penalty
repetition penalty
hallucination guard
```

---

# 143. Phase 8 — UserStyleProfile + Learner

History의 submitted_text 활용.

---

# 144. Phase 9 — UI Candidate Controls

```text
다른 댓글
더 짧게
조금 더 친근하게
내 말투로
```

---

# 145. Phase 10 — Gemini Prompt Alignment

First-person intent optional.

---

# 146. Phase 11 — Full Regression

Neighbor
Recommendation
Direct

like:
998 / 999 / 999+
visitor:
9999 / 10000 / 10001 / unknown

댓글:
category 다양성
first-person
repetition
suffix
Enter approval.

---

# 147. Definition of Done — Like

- [ ] 현재 LikeState 3-state 유지
- [ ] LIKED는 클릭 안 함
- [ ] UNKNOWN은 클릭 안 함
- [ ] NOT_LIKED일 때만 eligibility 확인
- [ ] 공감수 998은 threshold 통과
- [ ] 공감수 999 skip
- [ ] 공감수 999+ skip
- [ ] 1,000 이상 skip
- [ ] 일 방문자 10,001 이상 skip
- [ ] 10,000은 기본 operator=gt에 따라 통과
- [ ] visitor unknown default skip_like
- [ ] like skip이어도 comment 계속
- [ ] 같은 blog visitor 조회 cache
- [ ] 누적 방문자를 오늘 방문자로 오인하지 않음

---

# 148. Definition of Done — Comment

- [ ] “관점이 눈에 들어오네요” 0건
- [ ] generic macro 문구 0건
- [ ] 6~10 후보 생성
- [ ] 최소 2개 reaction archetype
- [ ] 가능한 글에서 first-person 후보 생성
- [ ] `저도 가보고 싶어요` 계열 다양화
- [ ] `다음에 ~하면/가면` future behavior 다양화
- [ ] 과거 경험 위조 0건
- [ ] 실제 context entity만 구체적으로 언급
- [ ] 최근 opener/ending cooldown
- [ ] 사용자 수정 기반 style learning
- [ ] source-specific suffix 유지
- [ ] 최종 등록은 Enter

---

# 149. Sample Target Comments

## 맛집

```text
히츠마부시 진짜 맛있어 보이네요.
다음에 가면 오차즈케까지 꼭 먹어보고 싶어요 :)
```

```text
장어 비주얼이 너무 좋네요.
저도 이런 메뉴는 한번 제대로 먹어보고 싶어요!
```

---

# 150. 카페

```text
딸기라떼 색감부터 맛있어 보이네요.
저도 이런 분위기 카페 좋아해서 한번 가보고 싶어요 :)
```

```text
마당 분위기가 편안해 보여서 좋네요.
근처 가게 되면 한번 들러봐야겠어요 ㅎㅎ
```

---

# 151. 여행

```text
산책하면서 전시까지 같이 볼 수 있는 게 좋네요.
여수 가면 한번 들러봐야겠어요 :)
```

```text
사진만 봐도 분위기가 편안하네요.
저도 천천히 한번 걸어보고 싶어요 :)
```

---

# 152. 제품

```text
디자인이 깔끔해서 괜찮아 보이네요.
저도 이런 스타일 좋아해서 한번 써보고 싶어요 :)
```

단 제품의 실제 사용 의향은 허용.

---

# 153. 금융

```text
금리 부분이 핵심이네요.
저도 시장 볼 때 이 부분은 같이 봐야겠어요.
```

실제 투자 경험/행동을 위조하지 않는다.

---

# 154. 최종 금지사항

다음으로 이번 작업을 “완료” 처리하지 말 것.

```text
템플릿만 50개 추가
저도 prefix를 랜덤으로 붙임
999 텍스트만 exact match
daily visitor 첫 숫자 사용
unknown visitor인데 그냥 like
Gemini가 만든 문장을 무조건 자동 적용
```

---

# 155. Codex / Claude에 전달할 최종 명령

```text
이 문서는 이전 HUMAN-LIKE COMMENT COMPOSER v2 작업지시서를 대체한다.

댓글 엔진에서 단순 카테고리+고정 템플릿 random.choice 방식을
최종 생성기로 사용하지 않는다.

FirstPersonIntent 레이어를 추가하여
"저도 가보고 싶네요",
"다음에 가면 들러봐야겠어요",
"저도 한번 먹어보고 싶어요",
"저도 이런 분위기 좋아해요",
"저라면 이 메뉴부터 먹어보고 싶네요"
같은 현재 취향/미래 의향/행동 의사를 자연스럽게 표현한다.

단:
"저도 가봤는데",
"저도 먹어봤는데",
"저도 써봤는데"
같은 실제 경험 위조는 절대 생성하지 않는다.

First-person 표현은 모든 댓글에 넣지 않는다.
최근 opener와 ending을 추적하고,
"저도" 또는 "가보고 싶네요"가 연속 반복되면 cooldown을 적용한다.

한 글당 6~10개 내부 후보를 생성하고:
- context relevance
- specificity
- first-person fit
- user style similarity
- recent repetition
- generic phrase penalty
- hallucination risk
로 점수화한다.

History의 draft/submitted_text를 이용해
사용자가 실제로 남긴 말투를 로컬 StyleLearner가 학습한다.

동시에 Like Popularity Guard를 구현한다.

현재 LikeState를 먼저 판별한다:
LIKED → 아무 작업 없음
UNKNOWN → 기존 정책대로 click 금지
NOT_LIKED → popularity eligibility 평가

공감 count가 999 이상이면 click 금지.
999+, 1,000, 1천, 1.2천, 1만 등의 표기를
공통 CountParser로 파싱한다.

daily visitor guard도 추가한다.
일 방문자 수가 10,000을 초과하면 공감 click 금지.

중요:
현재 저장소에는 daily visitor DOM 실측 selector가 없으므로
먼저 diagnostics/inspect_blog_visitors.py를 만들고
실제 서로 다른 블로그 최소 5개에서
"오늘 방문자"와 "누적 방문자" DOM을 구분해 캡처한 뒤 resolver를 확정한다.
추측 selector를 Production primary로 넣지 않는다.

daily visitor 조회는 detail_page를 사용하지 말고
BrowserSession에 lazy `stats_page`를 추가한다.

같은 blog_id는 session cache를 사용해
방문자 정보를 1회만 조회한다.

방문자 수를 확인할 수 없는 경우 기본 정책은:
daily_visitor_unknown_policy = skip_like
로 하여 공감만 건너뛴다.
댓글은 계속 진행한다.

Like skip reason을:
- like_count_over_threshold
- daily_visitors_over_threshold
- daily_visitors_unknown
으로 기록하고 UI/log에 표시한다.

공감수 999 이상이 이미 확인되면
visitor 조회를 하지 않고 즉시 like skip하여 불필요한 navigation을 줄인다.

기존:
- Human-in-the-loop Enter submit
- Recommendation 전용 suffix
- 일반 suffix
- Pacing
- History
- LikeState UNKNOWN safe behavior
를 유지한다.

테스트에서 반드시:
998 → like eligible
999 → like skip
999+ → like skip
daily visitors 9999 → eligible
10000 → eligible
10001 → skip
visitor unknown → default skip
을 검증한다.

댓글 테스트에서는:
"관점", "눈에 들어오네요", "유익한 정보", "잘 보고 갑니다"
류 상투 문구가 최종 candidate에 나오지 않아야 한다.

FOOD/CAFE/TRAVEL 샘플에서
first-person 또는 future-intent 후보가 실제로 생성되는지 확인한다.

실제 댓글 샘플 최소 12개와
Like Eligibility 테스트 결과를 제시하기 전에는
완료라고 보고하지 않는다.
```

---

# END
