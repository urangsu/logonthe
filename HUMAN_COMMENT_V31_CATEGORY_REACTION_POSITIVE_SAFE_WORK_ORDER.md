# NAVER FEED ASSISTANT
# HUMAN-LIKE COMMENT COMPOSER v3.1
# CATEGORY × REACTION MATRIX + POSITIVE-SAFE TONE MASTER WORK ORDER

> 저장소: `urangsu/logonthe`
> 기준 브랜치: `main`
> 문서 성격: **이전 Human-Like Comment Composer v3.0 작업지시서를 대체하는 최종 통합본**
>
> 핵심 목표:
> 1. 댓글 구조를 “카테고리별 고정 템플릿”이 아니라
>    **카테고리(주제) × 반응유형(의도)** 2차원 구조로 개편
> 2. 칭찬/긍정 리액션을 기본축으로 강화
> 3. ‘나’의 취향/미래 행동을 자연스럽게 섞어 사람 느낌 강화
> 4. 거짓 경험, 광고성 보증, 불쾌감을 줄 수 있는 비교/평가, 과도한 질문 제거
> 5. GENERAL 고정 폴백 폐기
> 6. 다중 후보 생성 + 점수화 + 최근 반복 회피 + 사용자 수정 학습
> 7. Gemini는 optional alternative
> 8. 기존 Like Guard / Pacing / History / Human-in-the-loop Enter 승인 유지
>
> 이 문서에서 “10/10”은 아래 Acceptance Gate를 모두 만족하는 프로젝트 내부 기준이다.

---

# 0. 최우선 톤 정책

댓글의 기본 목표는:

```text
칭찬 55~70%
+
구체 반응 15~25%
+
나의 의향/취향 10~25%
+
질문/정보 보충 0~10%
```

이다.

즉 **칭찬이 기본값**이고,
의향/질문/정보는 보조축이다.

---

# 1. 절대 피해야 할 톤

## 1.1 상대 선택을 평가하는 느낌

금지:

```text
저라면 이걸 골랐을 것 같아요
저라면 이 색보다 저 색을 선택했을 것 같아요
저라면 이렇게 했을 것 같아요
```

이유:
작성자의 선택을 간접적으로 비교/평가하는 느낌이 날 수 있음.

대체:

```text
저는 이것도 한번 해보고 싶네요
저도 이 메뉴 같이 주문해보고 싶어요
이 색도 예뻐 보여서 한번 입어보고 싶네요
```

---

# 2. 부정적으로 들릴 수 있는 표현 제거

금지/강한 penalty:

```text
생각보다
의외로
그나마
나쁘지 않네요
괜찮은 편이네요
무난하네요
호불호 있겠네요
취향 탈 것 같아요
아쉽지만
그래도
저는 다른 쪽이 더
```

이런 표현은 칭찬 댓글 목적과 맞지 않음.

---

# 3. 외모/신체 평가 위험 표현 제거

특히 BEAUTY/FASHION/PET/PEOPLE 관련.

금지:

```text
얼굴이 작아 보여요
살이 빠져 보여요
몸매가 좋아 보여요
피부가 하얘 보여요
나이 들어 보여요
어려 보여요
```

대신:

```text
스타일이 자연스럽게 잘 어울려요
색감이 깔끔해 보여요
전체적인 분위기가 좋네요
```

처럼 결과/스타일 중심.

---

# 4. 지나친 친밀감 금지

금지:

```text
우리 아이도
우리 집도
저희 가족도
저도 딱 같은 일을 겪었어요
```

실제 경험이 확인되지 않으므로 생성하지 않음.

대신:

```text
이런 상황 공감되는 분들 많을 것 같아요
이런 방법은 저도 한번 따라 해보고 싶네요
```

---

# 5. 칭찬의 기본 구조

가장 안전한 기본:

```text
구체 요소
+
긍정 반응
```

예:

```text
오차즈케로 마무리하는 방식이 좋네요.
딸기라떼 색감이 정말 예뻐 보여요.
산책하면서 전시까지 볼 수 있는 점이 좋아 보여요.
```

---

# 6. 칭찬 + 나의 의향

```text
딸기라떼 색감이 예쁘네요. 저도 한번 마셔보고 싶어요 :)
```

```text
히츠마부시 진짜 맛있어 보여요. 다음에 가면 저도 먹어보고 싶네요.
```

```text
장도 분위기가 좋아 보여요. 여수 가면 한번 들러봐야겠어요 :)
```

---

# 7. 칭찬 + 현재 취향

```text
이런 편안한 분위기 저도 좋아해요.
```

```text
이런 자연스러운 스타일은 저도 취향이에요 :)
```

---

# 8. 반응유형 7종 공통축

모든 카테고리가 이 축을 공유한다.

```python
class ReactionIntent(str, Enum):
    PRAISE = "praise"
    EMPATHY = "empathy"
    TRY_INTENT = "try_intent"
    PLAN_INTENT = "plan_intent"
    PREFERENCE = "preference"
    DETAIL_PRAISE = "detail_praise"
    QUESTION = "question"
    INFO_REACTION = "info_reaction"
```

실제로는 칭찬을 별도 기본축으로 두므로 8개로 관리해도 됨.

---

# 9. 공감·경험형 재정의

기존:

```text
저도 그런 적 있어요
```

는 거짓 경험 위험.

새 정의:

```text
EMPATHY = 공감되거나 친근하게 느껴지는 현재 반응
```

예:

```text
이런 분위기 좋아하는 분들 많을 것 같아요.
저도 이런 스타일 좋아해요.
이런 방법은 괜히 따라 해보고 싶어지네요.
```

과거 경험을 주장하지 않는다.

---

# 10. 방문/시도 의향형

```text
저도 한번 가보고 싶네요.
저도 한번 먹어보고 싶어요.
저도 한번 써보고 싶네요.
저도 한번 따라 해보고 싶어요.
```

---

# 11. 계획 편입형

```text
다음에 이쪽 가면 들러봐야겠어요.
다음에 주문할 때 같이 먹어보고 싶네요.
여행 갈 때 코스에 넣어봐야겠어요.
다음에 스타일 바꿀 때 참고해보고 싶어요.
```

---

# 12. 선택/취향형 재정의

기존:

```text
저라면 이거 고를 것 같아요
```

삭제.

새 정의:

```text
내가 좋아하는 방향을 긍정적으로 표현
```

예:

```text
저는 이것도 같이 주문해보고 싶네요.
이런 색감 저도 좋아해요.
이런 스타일은 저도 취향이에요.
```

---

# 13. 관찰·디테일 칭찬형

가장 중요.

실제 evidence가 있어야 함.

```text
오차즈케로 마무리하는 방식이 좋네요.
마당 있는 공간이라 분위기가 더 편안해 보여요.
전시와 산책을 같이 할 수 있는 점이 좋아 보여요.
```

---

# 14. 질문형

기본 비중 매우 낮게.

질문은 답글 유도용이지만
자동 댓글에서 남발하면 부담스러움.

허용 조건:

```text
본문에 답이 명시되지 않은 실제 궁금증
```

금지 질문:

```text
왜 이렇게 하셨어요?
가격은 얼마예요? (본문에 있음)
어디 사세요?
몇 살이에요?
자녀 몇 명이에요?
직업이 뭐예요?
```

---

# 15. 질문형 추천 비율

```text
전체 댓글의 3~8%
```

기본 OFF에 가깝게.

---

# 16. 정보 보충형 재정의

기존:

```text
근처에 비슷한 맛집이 있던데...
```

는 외부 사실 hallucination 위험.

새 정의:

```text
글에서 이미 나온 정보에 대한 반응
```

예:

```text
주차 가능하다는 점도 편하겠네요.
웨이팅 정보까지 있어서 가기 전에 참고하기 좋겠어요.
```

외부 지식 추가 금지.

---

# 17. Category × Reaction Matrix 구조

각 category는:

```python
CATEGORY_REACTION_WEIGHTS = {
    "FOOD": {...},
    "CAFE": {...},
    ...
}
```

형태.

---

# 18. FOOD

권장 weight:

```text
PRAISE          1.4
DETAIL_PRAISE   1.5
TRY_INTENT      1.3
PLAN_INTENT     0.8
PREFERENCE      0.9
EMPATHY         0.5
QUESTION        0.2
INFO_REACTION   0.4
```

---

# 19. FOOD 예시

칭찬:

```text
히츠마부시 진짜 맛있어 보여요.
```

디테일:

```text
오차즈케로 마무리하는 방식이 좋네요.
```

시도:

```text
저도 한번 먹어보고 싶네요 :)
```

계획:

```text
다음에 가면 이것도 같이 주문해보고 싶어요.
```

취향:

```text
저는 이런 구성 좋아해서 이것도 한번 주문해보고 싶네요.
```

질문:
본문에 웨이팅 정보 없음 + 방문글인 경우만:

```text
웨이팅은 어느 정도인지 궁금하네요 :)
```

---

# 20. CAFE

```text
PRAISE          1.4
DETAIL_PRAISE   1.5
TRY_INTENT      1.2
PLAN_INTENT     1.0
PREFERENCE      1.0
EMPATHY         0.8
QUESTION        0.2
INFO_REACTION   0.3
```

---

# 21. CAFE 예시

```text
딸기라떼 색감이 정말 예쁘네요.
```

```text
마당 있는 공간이라 분위기가 더 편안해 보여요.
```

```text
저도 이 메뉴 한번 마셔보고 싶어요 :)
```

```text
근처 가게 되면 한번 들러보고 싶네요.
```

```text
이런 편안한 분위기 저도 좋아해요.
```

---

# 22. TRAVEL

```text
PRAISE          1.3
DETAIL_PRAISE   1.5
TRY_INTENT      1.1
PLAN_INTENT     1.4
PREFERENCE      0.6
EMPATHY         0.8
QUESTION        0.2
INFO_REACTION   0.5
```

---

# 23. TRAVEL 예시

```text
산책하면서 전시까지 같이 볼 수 있는 점이 좋네요.
```

```text
사진 분위기가 편안해서 한번 가보고 싶어져요 :)
```

```text
여수 가면 한번 들러봐야겠어요.
```

```text
이런 여유로운 여행 코스 저도 좋아해요.
```

---

# 24. BEAUTY

```text
PRAISE          1.5
DETAIL_PRAISE   1.2
TRY_INTENT      0.8
PLAN_INTENT     0.7
PREFERENCE      1.2
EMPATHY         0.6
QUESTION        0.1
INFO_REACTION   0.2
```

---

# 25. BEAUTY 예시

```text
시스루 쉐도우펌 느낌이 자연스럽고 좋네요.
```

```text
전체적으로 부드러운 느낌이라 잘 어울려요.
```

```text
저도 이런 자연스러운 스타일 좋아해요 :)
```

```text
다음에 스타일 바꿀 때 이런 느낌도 한번 보고 싶네요.
```

외모 평가 금지.

---

# 26. FASHION

```text
PRAISE          1.5
DETAIL_PRAISE   1.4
PREFERENCE      1.2
TRY_INTENT      0.9
PLAN_INTENT     0.5
EMPATHY         0.6
QUESTION        0.1
INFO_REACTION   0.2
```

예:

```text
컬러 조합이 깔끔해서 보기 좋네요.
```

```text
가방 포인트가 전체 분위기랑 잘 어울려요.
```

```text
저도 이런 조합 한번 입어보고 싶네요 :)
```

---

# 27. LIFESTYLE

일상/살림/육아를 세부 subcategory로 나눌 수 있음.

```text
PRAISE          1.1
DETAIL_PRAISE   1.1
EMPATHY         1.3
TRY_INTENT      1.1
PLAN_INTENT     0.8
PREFERENCE      0.5
QUESTION        0.15
INFO_REACTION   0.5
```

---

# 28. LIFESTYLE 예시

```text
정리 방식이 보기 좋게 잘 되어 있네요.
```

단 `깔끔하게 정리` hard-ban과 충돌하지 않게 표현 다양화.

```text
공간을 나눠둔 방식이 좋아 보여요.
```

```text
이 방법은 저도 한번 따라 해보고 싶네요.
```

```text
이런 루틴은 저도 한번 적용해보고 싶어요.
```

---

# 29. PARENTING 별도

거짓 육아 경험 금지.

금지:

```text
저희 아이도 그랬어요
저도 육아하면서...
```

허용:

```text
아이 반응이 좋아 보여서 보기 좋네요.
```

```text
이런 놀이 방식은 한번 참고해보고 싶네요.
```

---

# 30. PET

거짓 반려 경험 금지.

금지:

```text
저희 강아지도
우리 고양이도
저희 아이도
```

허용:

```text
표정이 편안해 보여서 귀엽네요 :)
```

```text
이런 놀이 방식 좋아하는 아이들 많을 것 같아요.
```

---

# 31. BOOK/MOVIE

```text
PRAISE          1.0
DETAIL_PRAISE   1.4
EMPATHY         1.1
TRY_INTENT      1.0
PLAN_INTENT     1.0
PREFERENCE      0.6
QUESTION        0.15
INFO_REACTION   0.3
```

---

# 32. BOOK/MOVIE 예시

```text
이 부분 해석이 인상 깊네요.
```

단 `인상적입니다` 같은 문어체와 다르게 구어체.

```text
저도 이 작품 한번 보고 싶어졌어요.
```

```text
다음에 볼 리스트에 넣어봐야겠네요 :)
```

---

# 33. IT/GADGET

```text
PRAISE          1.0
DETAIL_PRAISE   1.5
TRY_INTENT      0.8
PLAN_INTENT     0.6
PREFERENCE      0.7
EMPATHY         0.4
QUESTION        0.2
INFO_REACTION   0.6
```

---

# 34. IT/GADGET 예시

```text
화면 구성이 직관적으로 보여서 좋네요.
```

```text
이 기능은 저도 한번 써보고 싶네요.
```

```text
다음에 기기 바꿀 때 이런 부분도 같이 봐야겠어요.
```

---

# 35. FITNESS

건강/신체 성과 평가 주의.

금지:

```text
몸 좋아지셨네요
살 빠지셨네요
이 운동이면 무조건 효과 있겠네요
```

허용:

```text
동작 순서가 보기 쉽게 나와 있어서 좋네요.
```

```text
저도 이 루틴 한번 따라 해보고 싶어요.
```

```text
다음 운동할 때 이 동작도 넣어보고 싶네요.
```

---

# 36. INTERIOR/HOME

```text
PRAISE          1.5
DETAIL_PRAISE   1.5
PREFERENCE      1.1
TRY_INTENT      0.8
PLAN_INTENT     0.7
EMPATHY         0.4
QUESTION        0.1
INFO_REACTION   0.3
```

예:

```text
조명이랑 가구 톤이 잘 어울리네요.
```

```text
저도 이런 느낌의 공간 좋아해요 :)
```

```text
다음에 공간 꾸밀 때 이런 조합 참고해보고 싶네요.
```

---

# 37. PRODUCT

⚠ 광고성 보증/진성 구매자 위장 방지.

금지:

```text
눈여겨보고 있었는데
꼭 사야겠어요
구매각이네요
믿고 사도 되겠네요
효과 확실해 보이네요
```

---

# 38. PRODUCT weight

```text
PRAISE          1.0
DETAIL_PRAISE   1.4
TRY_INTENT      0.3
PLAN_INTENT     0.5
PREFERENCE      0.4
EMPATHY         0.3
QUESTION        0.15
INFO_REACTION   1.2
```

---

# 39. PRODUCT 예시

```text
이런 제품도 나오는지 몰랐네요.
```

```text
구성 방식이 한눈에 보여서 어떤 제품인지 알기 좋네요.
```

```text
필요할 때 참고해두면 좋겠어요.
```

실사용 evidence 있음:

```text
실제로 쓰는 모습까지 있어서 느낌이 잘 전해지네요.
```

---

# 40. FINANCE

⚠ 금융 의견 지지/보증 금지.

금지:

```text
좋은 관점이네요
이 종목 좋아 보이네요
매수해도 되겠네요
전망이 맞는 것 같아요
이 전략 좋네요
```

---

# 41. FINANCE weight

```text
PRAISE          0.4
DETAIL_PRAISE   0.8
TRY_INTENT      0.4
PLAN_INTENT     0.8
PREFERENCE      0.1
EMPATHY         0.4
QUESTION        0.15
INFO_REACTION   1.4
```

---

# 42. FINANCE 예시

```text
금리 부분은 저도 같이 봐야 할 내용 같네요.
```

```text
이런 자료가 같이 있으니 흐름 확인하기 좋네요.
```

```text
다음에 볼 때 이 지표도 같이 확인해봐야겠어요.
```

판단/보증 없음.

---

# 43. WORK / OFFICE

```text
PRAISE          0.9
DETAIL_PRAISE   1.2
TRY_INTENT      0.8
PLAN_INTENT     0.8
PREFERENCE      0.4
EMPATHY         0.6
QUESTION        0.1
INFO_REACTION   0.8
```

예:

```text
업무 흐름을 나눠둔 방식이 보기 좋네요.
```

```text
이 방법은 저도 한번 참고해보고 싶어요.
```

---

# 44. GENERAL 폐기

`GENERAL` 고정 문구를 생성하지 않는다.

현재:

```text
기분 좋게 잘 읽었습니다
사진 분위기가 좋네요
```

같은 범용 fallback 금지.

---

# 45. UNKNOWN_TOPIC 전략

카테고리 분류 실패:

```text
제목 keyword
+
첫 유효 문장
+
evidence reaction
```

으로만 생성.

예:

제목:

```text
2026 다이어리 기록 시작
```

본문 첫 문장:

```text
올해는 하루 한 줄씩 기록해보려고...
```

댓글:

```text
하루 한 줄씩 기록하는 방식 좋네요. 저도 한번 해보고 싶어요 :)
```

---

# 46. UNKNOWN_TOPIC에서도 칭찬 우선

```text
{keyword} 얘기 재밌게 봤어요
```

보다:

```text
{evidence detail} 방식이 좋아 보여요.
```

우선.

---

# 47. Candidate Generator

한 글당:

```text
12~18개
```

후보 생성.

---

# 48. 후보 분포

가능한 경우:

```text
4~6개 PRAISE/DETAIL_PRAISE
3~4개 TRY/PLAN
2~3개 PREFERENCE/EMPATHY
0~1개 QUESTION
1~2개 INFO_REACTION
```

칭찬 비중을 가장 높게.

---

# 49. Negative Tone Filter

새 validator:

```text
services/comments/validators.py
```

---

# 50. BANNED_NEGATIVE_PHRASES

```text
생각보다
의외로
그나마
나쁘지
무난하
아쉽
그래도
호불호
취향 탈
별로
애매
```

---

# 51. BANNED_JUDGMENT_PHRASES

```text
저라면
제가 보기에는
개인적으로는
더 나은
이쪽이 낫
저쪽보다
```

---

# 52. BANNED_MACRO_PHRASES

```text
유익한 정보
잘 보고 갑니다
작성자님
인상적입니다
도움이 되었습니다
관점이
깔끔하게 잘 정리
```

---

# 53. BANNED_FAKE_EXPERIENCE

```text
저도 가봤
저도 먹어봤
저도 써봤
저도 구매했
저도 이용해봤
저희 아이도
우리 강아지도
우리 고양이도
```

---

# 54. EvidenceRequirementValidator

다음 표현은 evidence 필요:

```text
아늑
조용
넓
바삭
달콤
친절
저렴
가성비
탁 트인
운치
평화롭
```

---

# 55. PraiseSafetyValidator

칭찬이 사람/신체가 아니라:

```text
스타일
색감
공간
구성
메뉴
사진
디테일
```

을 대상으로 하는지 확인.

---

# 56. QuestionSafetyValidator

질문 생성 전:

```text
본문에 답 없음?
사적 정보 아님?
불쾌감 가능성 낮음?
광고/투자 판단 요구 아님?
```

모두 만족해야 허용.

---

# 57. 정보 보충형 외부 지식 금지

자동 생성 댓글이 외부 사실을 끼워 넣지 않는다.

금지:

```text
근처에 다른 맛집도 있어요
그 브랜드도 좋아요
이 방법이 더 효과적이에요
```

근거 없음.

---

# 58. FirstPerson Weight

기본:

```text
0.35~0.50
```

하지만 모든 댓글에 `저도` 금지.

---

# 59. Opener Pool

```text
저도
저는
다음에
기회 되면
근처 가면
사진 보니까
이 부분은
특히
```

---

# 60. `저는 이것도` 조건

secondary entity >= 1.

예:

```text
파스타 + 리조또
```

허용:

```text
저는 이것도 같이 주문해보고 싶네요.
```

단 lone menu이면:

```text
저도 이 메뉴 한번 주문해보고 싶네요.
```

---

# 61. “저는 이것도” 남발 방지

최근 10개 중 exact phrase 1회 이상이면 cooldown.

변형:

```text
저도 이건 한번 먹어보고 싶네요.
다음에 가면 이것도 같이 주문해보고 싶어요.
이 메뉴도 한번 맛보고 싶네요.
```

---

# 62. 끝맺음 다양화

허용:

```text
~네요
~해요
~보여요
~같아요
~겠어요
~싶네요
~싶어요
~봐야겠어요
~해보고 싶어요
```

---

# 63. `~더라구요` 제한

사용자가 실제 경험한 것처럼 들릴 수 있음.

자동 댓글에서는 기본 금지.

예:

```text
좋더라구요
맛있더라구요
```

과거 경험 뉘앙스.

따라서 StyleLearner가 있어도
자동 생성에는 사용하지 않음.

---

# 64. 말줄임표

`...`는 아주 낮은 빈도로만.

```text
2~5%
```

예:

```text
다음에 가면 한번 들러보고 싶네요...
```

하지만 너무 자주 쓰면 인위적.

---

# 65. 이모티콘

```text
:) 15~25%
ㅎㅎ 5~10%
! 15~25%
없음 50%+
```

user style로 학습.

---

# 66. Candidate Scoring

```python
score =
    praise_quality * 1.8
  + context_relevance * 2.5
  + evidence_support * 2.2
  + specificity * 1.4
  + naturalness * 1.7
  + user_style_similarity * 1.5
  + intent_fit * 0.8
  + positivity * 1.0
  + length_fit * 0.5
  - recent_similarity * 2.7
  - opener_repetition * 1.2
  - ending_repetition * 1.2
  - generic_penalty * 3.5
  - negative_tone_penalty * 5.0
  - hallucination_risk * 10.0
```

---

# 67. positivity score

부정/비교/평가 없는
긍정 리액션 후보에 boost.

---

# 68. Hard Reject

다음 하나라도:

```text
거짓 경험
사적 질문
외모/신체 평가
투자 지지
제품 구매 보증
없는 사실
hard banned phrase
```

후보 즉시 reject.

---

# 69. StyleLearner

기존 History:

```text
draft
submitted_text
```

활용.

---

# 70. 학습

사용자가 자주:

```text
저도 → 유지
다음에 → 추가
:) → 삭제
좋네요 → 유지
```

하면 weights 반영.

---

# 71. 단 Safety Ban은 학습보다 우선

사용자가 실제 댓글에:

```text
저라면
```

을 많이 썼더라도
프로젝트 정책상 기본 자동 생성에서는 금지 가능.

즉:

```text
Safety > Learned Style
```

---

# 72. History metadata

추가:

```text
draft_source
category
reaction_intent
first_person_intent
primary_subject
secondary_subjects
candidate_id
score
```

---

# 73. UI 버튼

```text
[ 다른 댓글 ]
[ 더 짧게 ]
[ 칭찬 더하기 ]
[ 내 말투로 ]
```

이전 `조금 더 친근하게` 대신:

```text
칭찬 더하기
```

를 권장.

---

# 74. “칭찬 더하기”

현재 candidate pool에서:

```text
praise_quality
positivity
detail_praise
```

가중치를 올려 재선택.

---

# 75. “다른 댓글”

다음 score 후보.

---

# 76. “더 짧게”

45자 이하 우선.

---

# 77. “내 말투로”

StyleLearner weight 상승.

단 safety filter 유지.

---

# 78. Gemini Prompt도 동일 정책

Gemini prompt:

```text
칭찬을 기본으로 하고,
작성자가 기분 나쁠 수 있는 비교/평가/부정 표현은 피할 것.

필요하면:
"저도 한번 가보고 싶네요"
"다음에 가면 이것도 같이 주문해보고 싶어요"
처럼 현재 취향이나 미래 의향을 자연스럽게 표현.

"저라면", "생각보다", "의외로", "관점", "유익한 정보",
"잘 보고 갑니다"는 쓰지 말 것.

실제로 방문/구매/사용한 것처럼 과거 경험을 만들지 말 것.

질문은 정말 자연스러운 경우만.
사적인 질문은 하지 말 것.
```

---

# 79. 카테고리 추가

최종 최소:

```text
FOOD
CAFE
TRAVEL
BEAUTY
FASHION
LIFESTYLE
PARENTING
PET
BOOK_MOVIE
IT_GADGET
FITNESS
INTERIOR_HOME
PRODUCT
FINANCE
WORK
UNKNOWN_TOPIC
```

16개.

---

# 80. 카테고리 추가 원칙

새 카테고리가 생겨도:

```text
새 템플릿 7개
```

를 만드는 구조 금지.

필요한 것:

```text
category vocabulary
entity-role priority
reaction weight
safety rule
```

만 추가.

---

# 81. 패턴은 reaction intent가 소유

예:

```text
TRY_INTENT
```

공통 패턴:

```text
저도 {action}해보고 싶네요.
다음에 {condition} {action}해보고 싶어요.
```

category가 slot을 채움.

---

# 82. category action resolver

FOOD:

```text
먹어보다 / 주문하다
```

CAFE:

```text
마셔보다 / 가보다
```

TRAVEL:

```text
가보다 / 들러보다
```

BEAUTY:

```text
해보다 / 참고하다
```

FITNESS:

```text
따라 해보다 / 넣어보다
```

---

# 83. Category × Reaction Matrix data-driven

예:

```python
CATEGORY_POLICY = {
    "FOOD": CategoryPolicy(
        actions=["먹어보다", "주문하다"],
        subject_roles=[MENU, PLACE],
        reaction_weights={...},
        forbidden_traits=[...],
    )
}
```

---

# 84. 실제 표현 조합은 grammar 기반

예:

```text
ReactionIntent.TRY_INTENT
+
MENU
+
먹어보다
```

→

```text
저도 {menu} 한번 먹어보고 싶네요.
```

---

# 85. random template 증가 방식 금지

문장 다양성은:

```text
intent
opener
subject
verb
connector
ending
punctuation
```

조합으로 확보.

---

# 86. UI / Config / Test P0 유지

이전 v3의 다음 항목 그대로 필수:

```text
ConfigService.update_many()
atomic save
schema 보존
test_*.py gitignore 제거
tests git tracked
```

---

# 87. Like Guard 순서

```text
LikeState
→ LIKED early exit
→ UNKNOWN early exit
→ NOT_LIKED
→ like count
→ daily visitor
→ click
```

---

# 88. Like threshold

```text
999 이상 skip
```

---

# 89. Daily visitor

```text
10,000 초과 skip
```

즉:

```text
10,000 허용
10,001 skip
```

---

# 90. Visitor DOM

실제 최소 5개 블로그 diagnostics gate 유지.

---

# 91. Test Matrix

최소 80 fixture.

분배:

```text
FOOD 8
CAFE 8
TRAVEL 8
BEAUTY 5
FASHION 5
LIFESTYLE 6
PARENTING 4
PET 4
BOOK_MOVIE 5
IT_GADGET 5
FITNESS 5
INTERIOR_HOME 5
PRODUCT 6
FINANCE 6
WORK 4
UNKNOWN_TOPIC 6
```

---

# 92. 칭찬 비율 테스트

전체 최종 후보 샘플에서:

```text
긍정/칭찬 반응 포함 >= 70%
```

권장.

---

# 93. Negative phrase test

0건:

```text
생각보다
의외로
나쁘지
무난
호불호
아쉽
저라면
```

---

# 94. Macro test

0건:

```text
관점
유익
작성자님
잘 보고 갑니다
깔끔하게 잘 정리
```

---

# 95. Fake experience test

0건:

```text
저도 가봤
저도 먹어봤
저도 써봤
저희 아이도
우리 강아지도
```

---

# 96. Question ratio test

```text
<= 8%
```

---

# 97. 질문 safety test

사적/민감 질문:

```text
0
```

---

# 98. PRODUCT safety test

금지:

```text
꼭 사고 싶
믿고 사
효과 좋아 보
눈여겨보고 있었
```

---

# 99. FINANCE safety test

금지:

```text
매수
추천
전망 맞
좋은 관점
투자해도
```

---

# 100. UNKNOWN_TOPIC test

generic fallback:

```text
기분 좋게 읽었습니다
잘 보고 갑니다
사진 분위기
```

자동 사용 0.

반드시 actual keyword/evidence 포함.

---

# 101. Candidate diversity

한 post 후보 12개 중:

```text
동일 opener <= 3
동일 ending <= 3
동일 reaction intent <= 5
```

---

# 102. Recent history diversity

최근 20개 final comment 대비:

```text
0.85 이상 similarity 후보 reject
```

---

# 103. Acceptance Gate — Comment

- [ ] 카테고리 × 반응유형 분리
- [ ] reaction grammar data-driven
- [ ] 16 category
- [ ] 8 common reaction intent
- [ ] 칭찬 기본 weight 최상위
- [ ] candidate 12~18개
- [ ] candidate scoring
- [ ] evidence validation
- [ ] entity role
- [ ] negative tone hard filter
- [ ] fake experience hard filter
- [ ] body/appearance unsafe filter
- [ ] finance/product special safety
- [ ] question <= 8%
- [ ] GENERAL fixed fallback 제거
- [ ] UNKNOWN dynamic fallback
- [ ] History style learning
- [ ] recent similarity
- [ ] opener cooldown
- [ ] ending cooldown
- [ ] “칭찬 더하기” UI
- [ ] “다른 댓글” UI
- [ ] “더 짧게” UI
- [ ] “내 말투로” UI

---

# 104. 샘플 목표 FOOD

```text
히츠마부시 진짜 맛있어 보여요.
오차즈케로 마무리하는 것도 좋네요 :)
```

```text
파스타도 괜찮아 보이는데 저는 리조또도 같이 주문해보고 싶네요.
```

단 `괜찮아 보이는데`는 약한 비교로 들릴 수 있으므로 더 좋은 최종:

```text
파스타도 맛있어 보이고 리조또도 궁금하네요.
저는 이것도 같이 주문해보고 싶어요 :)
```

---

# 105. 샘플 목표 CAFE

```text
딸기라떼 색감이 정말 예쁘네요.
저도 한번 마셔보고 싶어요 :)
```

```text
마당 있는 공간이라 분위기가 편안해 보여요.
근처 가면 한번 들러보고 싶네요.
```

---

# 106. 샘플 목표 TRAVEL

```text
산책하면서 전시까지 같이 볼 수 있는 점이 좋네요.
여수 가면 한번 들러봐야겠어요 :)
```

---

# 107. 샘플 목표 BEAUTY

```text
시스루 쉐도우펌 느낌이 자연스럽고 좋네요.
저도 이런 스타일 좋아해요 :)
```

---

# 108. 샘플 목표 FASHION

```text
컬러 조합이 잘 어울리네요.
저도 이런 느낌 한번 입어보고 싶어요 :)
```

---

# 109. 샘플 목표 LIFESTYLE

```text
수납을 나눠둔 방식이 좋아 보여요.
이 방법은 저도 한번 따라 해보고 싶네요.
```

---

# 110. 샘플 목표 BOOK/MOVIE

```text
이 장면을 이렇게 보는 시선이 재밌네요.
저도 작품 한번 보고 싶어졌어요.
```

`관점` 대신 `시선`도 너무 평가적이면 낮은 weight.

더 안전:

```text
이 장면 얘기가 특히 재밌네요.
저도 작품 한번 보고 싶어졌어요.
```

---

# 111. 샘플 목표 IT

```text
화면 구성이 보기 편해 보여요.
이 기능은 저도 한번 써보고 싶네요.
```

---

# 112. 샘플 목표 FITNESS

```text
운동 순서가 잘 보여서 따라 하기 좋겠네요.
저도 이 루틴 한번 해보고 싶어요.
```

---

# 113. 샘플 목표 PRODUCT

```text
이런 제품도 나오는지 몰랐네요.
구성까지 같이 볼 수 있어서 어떤 제품인지 알기 좋았어요.
```

---

# 114. 샘플 목표 FINANCE

```text
금리 부분은 저도 같이 봐야 할 내용 같네요.
다음에 시장 볼 때 한번 더 확인해봐야겠어요.
```

---

# 115. 구현 Phase

## Phase 0
Config / tests / history schema.

## Phase 1
ReactionIntent + CategoryPolicy.

## Phase 2
Entity/Evidence analyzer.

## Phase 3
Grammar-based candidate generator.

## Phase 4
Positive/Safety validators.

## Phase 5
Candidate scorer.

## Phase 6
Recent history + style learner.

## Phase 7
UI 4 buttons.

## Phase 8
Gemini prompt alignment.

## Phase 9
LikeState-first guard.

## Phase 10
Visitor diagnostics.

## Phase 11
80 fixture regression.

---

# 116. 완료 보고 샘플

최소 32개.

```text
FOOD 4
CAFE 4
TRAVEL 4
BEAUTY 2
FASHION 2
LIFESTYLE 2
PARENTING 1
PET 1
BOOK_MOVIE 2
IT 2
FITNESS 2
INTERIOR 2
PRODUCT 2
FINANCE 2
WORK 1
UNKNOWN 1
```

---

# 117. 각 샘플에 보고

```text
title
category
reaction intent
primary subject
evidence
first-person intent
score
final comment
```

---

# 118. 자동 품질 검사 보고

반드시:

```text
negative phrase hits = 0
fake experience hits = 0
macro phrase hits = 0
private question hits = 0
finance endorsement hits = 0
product purchase endorsement hits = 0
```

---

# 119. Codex / Claude 최종 지시

```text
이번 작업은 기존 Human-Like v3 문서를 대체한다.

댓글 시스템을 “카테고리별 템플릿”에서
“카테고리 × 반응유형” 구조로 바꾼다.

공통 ReactionIntent를:
PRAISE
EMPATHY
TRY_INTENT
PLAN_INTENT
PREFERENCE
DETAIL_PRAISE
QUESTION
INFO_REACTION
로 정의한다.

새 category가 추가되어도
카테고리별 템플릿 7개를 새로 만드는 방식은 금지한다.

카테고리는:
- vocabulary
- entity priority
- action verbs
- reaction weights
- safety rules
만 정의하고,
실제 문장은 공통 reaction grammar가 생성한다.

칭찬을 기본값으로 강화한다.
최종 댓글 샘플의 70% 이상이
긍정/칭찬 반응을 포함하도록 설계한다.

"저라면" 표현은 제거한다.

대신:
"저도 이 메뉴 한번 먹어보고 싶네요"
"저는 이것도 같이 주문해보고 싶네요"
"다음에 가면 이것도 같이 먹어보고 싶어요"
처럼 현재 취향/미래 의향을 사용한다.

단 "이것도"는 secondary entity가 실제로 있을 때만.

다음 표현은 hard/strong ban:
- 생각보다
- 의외로
- 나쁘지 않네요
- 무난하네요
- 호불호
- 아쉽지만
- 저라면
- 관점
- 유익한 정보
- 잘 보고 갑니다
- 작성자님
- 깔끔하게 잘 정리

거짓 경험:
- 저도 가봤는데
- 저도 먹어봤는데
- 저도 써봤는데
- 저희 아이도
- 우리 강아지도
를 자동 생성하지 않는다.

BEAUTY/FASHION에서는 외모·몸 평가 금지.
PRODUCT에서는 구매 보증/진성 고객 위장 표현 금지.
FINANCE에서는 투자 의견 지지/추천/보증 표현 금지.

질문형은 전체 8% 이하.
본문에 답이 이미 있거나 사적 질문이면 생성 금지.

INFO_REACTION은 외부 정보를 새로 만들지 말고
본문에 이미 나온 정보에 대한 반응만 한다.

GENERAL 고정 fallback을 삭제하고
UNKNOWN_TOPIC은 제목/첫 유효 문장/evidence 기반으로만 생성한다.

한 글당 12~18개 후보를 생성하며
칭찬/디테일 후보가 가장 많아야 한다.

최종 후보는 scoring으로 선택한다.
random.choice를 최종 결정에 사용하지 않는다.

CandidateScorer에:
praise quality
positivity
context relevance
evidence support
specificity
naturalness
user style
intent fit
length
recent similarity
opener/ending repetition
generic penalty
negative tone
hallucination risk
를 반영한다.

History 기반 StyleLearner를 구현하되
Safety Rule이 학습된 말투보다 우선한다.

UI에는:
- 다른 댓글
- 더 짧게
- 칭찬 더하기
- 내 말투로
를 추가한다.

ConfigService partial save,
atomic save,
tests git tracking,
History metadata,
LikeState-first Popularity Guard,
visitor DOM diagnostics도 기존 v3 지시대로 완료한다.

80개 fixture,
32개 실제 샘플,
negative/fake/macro/private-question/finance/product unsafe hit 0건
증거가 없으면 10/10 완료라고 보고하지 않는다.
```

---

# END
