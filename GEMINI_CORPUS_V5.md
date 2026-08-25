# NAVER FEED ASSISTANT
# GEMINI BRIDGE + REAL KOREAN COMMENT CORPUS v5

## A. Gemini Web Bridge 고도화

### 1. 현재 코드 평가

현재 main은:
- managed Playwright에서 response text 직접 추출
- `button[aria-label*='복사']` 마지막 버튼 클릭 시도
- `DraftService.clean_ai_response()`에서 UI header 제거
- existing Chrome은 response count snapshot 후 DOM 직접 추출

까지 구현되어 있다.

하지만 다음이 남아 있다.

1. managed Playwright는 `before_count` / fresh-response identity가 부족하다.
2. global last copy button은 response scope가 없다.
3. `aria-label*='복사'`는 코드블록 복사 등 다른 버튼까지 잡을 수 있다.
4. RESPONSE_SELECTORS에 top-level response와 nested content selector가 섞여 있다.
5. existing Chrome은 docstring과 달리 paste read-back 검증이 없다.
6. AppleScript JS string escaping helper가 없다.
7. clean_ai_response만으로는 댓글 품질/safety 검증이 충분하지 않다.
8. Gemini output이 local CommentValidator를 통과하지 않는다.

## 2. 권장 아키텍처

```text
PROMPT
↓
request_id 생성
↓
before ResponseSnapshot
↓
editor set
↓
read-back prompt verify
↓
send
↓
새 response identity 확인
↓
generation completion 확인
↓
DOM body extraction
↓
response-scoped Copy fallback
↓
request_id marker parse
↓
clean_ai_response
↓
same CommentValidator as local
↓
valid → use
invalid → local fallback
```

## 3. Request ID Marker

각 요청마다 UUID short id 생성.

Gemini prompt 마지막에:

```text
출력은 설명/따옴표/마크다운 없이 정확히 아래 형식 하나만 사용하세요.

[[CMT:{request_id}]]
댓글 한 개
[[/CMT]]
```

Extractor는 동일 request_id marker가 있는 응답만 허용.

marker 미일치:
→ Gemini 결과 폐기
→ local engine fallback

## 4. ResponseSnapshot

```python
@dataclass
class ResponseSnapshot:
    top_level_count: int
    last_signature: str
    last_text_hash: str
```

top-level만 셈:

```css
model-response
div.response-container
```

`message-content`, `div.markdown`은 response count에 넣지 않는다.

## 5. Fresh Response Gate

전송 전 snapshot 저장.

전송 후:

```text
new top-level node appeared
OR
last response signature changed
```

가 있어야 신규 응답.

단순히 global `.last` text가 존재한다고 신규 답변으로 보지 않는다.

## 6. Response Body Extraction

먼저 latest NEW response container 하나를 확정.

그 안에서만:

```css
message-content
div.markdown
div.model-response-text
.response-body-inner
```

순서로 body를 찾는다.

없으면 top-level response `inner_text()` fallback 후 toolbar text clean.

## 7. Copy 버튼은 response-scoped fallback

최선:

```python
latest_response.get_by_role(
    "button",
    name=re.compile(r"^(복사|Copy)$", re.I)
)
```

또는 CSS:

```css
button[aria-label="복사"]
button[aria-label="Copy"]
```

반드시 `latest_response` 안에서만 탐색.

## 8. `*='복사'`는 2차 fallback

다음은 primary로 쓰지 않는다.

```css
button[aria-label*="복사"]
```

이유:
- 코드 복사
- 링크 복사
- 기타 action copy
등을 포함할 수 있음.

## 9. global `.last` 금지

금지:

```python
page.locator("button[aria-label*='복사']").last
```

권장:

```python
latest_response.locator(
    "button[aria-label='복사'], button[aria-label='Copy']"
).last
```

## 10. Copy 버튼의 역할

Direct DOM text extraction:
PRIMARY

Response-scoped copy:
SECONDARY FALLBACK

OS clipboard / pbpaste:
TERTIARY VERIFICATION

## 11. Completion Gate

```text
new response identified
AND
text stable >= 1.5 sec
AND
Stop generation button absent
AND
(response-scoped copy button visible OR stable body text valid)
```

## 12. Existing Chrome 개선

현재 existing Chrome은:
- prompt를 pbcopy
- Cmd+V
- 바로 Enter

이므로 실제 paste read-back이 없다.

반드시:

```text
Cmd+V
↓
Gemini editor innerText/input value read
↓
prompt hash 또는 prefix/suffix 일치
↓
Enter
```

검증.

## 13. AppleScript JS escaping helper

모든 JS를 직접 문자열 결합하지 않고 중앙 helper로 안전하게 escape한다.

예:

```python
def applescript_js_string(js: str) -> str:
    return json.dumps(js, ensure_ascii=False)
```

## 14. Managed Playwright 입력도 Read-back

`page.evaluate()` injection 후:

```text
editor text == prompt
```

검증.

실패:
→ locator.fill
→ 다시 검증
→ 그래도 실패하면 fallback

## 15. AI Cleaner 이후 같은 Comment Validator 사용

```text
DraftService.clean_ai_response
↓
CommentValidator.validate
```

Local engine과 동일한:
- fake past experience
- generic macro
- unsupported detail
- negative/judgment phrase
- finance/product policy
- grammar
검증 통과 필요.

## 16. AI Response Invalid Gate

다음이면 Gemini 결과 폐기:

```text
request_id marker 없음
너무 짧음
너무 긴 설명문
번호 목록
여러 댓글 후보
Markdown heading/code block
"Gemini의 응답"만 존재
prompt echo
금지어
unsupported factual detail
```

댓글 길이 권장:
15~180자.

## 17. Cleaner 확장

UI toolbar literal이 본문에 섞였을 때 제거 후보:

```text
Gemini의 응답
Gemini's Response
복사
Copy
좋아요
싫어요
공유
Share
다시 시도
Regenerate
```

단 실제 문장 내부 단어를 무차별 replace하지 말고
독립 line/leading/trailing UI token일 때만 제거.

---

# B. 실제 한국어 댓글 코퍼스 설계

## 18. 핵심 원칙

목표는 특정 사람을 흉내내는 것이 아니라:

```text
네이버 댓글의 자연스러운 문장 길이
구어체 종결
구체 anchor 사용
긍정 반응 방식
질문 비율
emoji/ㅎㅎ 빈도
```

같은 aggregate style feature를 학습하는 것.

공개 댓글을 그대로 대량 재생성하도록 학습하지 않는다.

## 19. 추천 데이터 비중

### Tier 1 — Naver Blog 실제 댓글: 65~75%

타깃 플랫폼과 동일하므로 최우선.

구성:

```text
일반 개인 블로그 45~55%
Naver Influencer category seed 15~20%
```

인플루언서만 모으면 팬댓글/마케팅 댓글 스타일로 치우칠 수 있으므로
일반 블로그가 더 많아야 한다.

## 20. Naver category seed

Naver Influencer keyword category를 seed discovery에 활용:

```text
여행
스타일
푸드
테크
라이프
게임
동물/펫
스포츠
엔터테인먼트
컬쳐
경제/비즈니스
어학/교육
```

앱 category로 mapping:

```text
FOOD / CAFE      ← 푸드
TRAVEL           ← 여행
BEAUTY/FASHION   ← 스타일
IT_GADGET        ← 테크
LIFESTYLE        ← 라이프
PET              ← 동물/펫
FITNESS          ← 스포츠
BOOK_MOVIE       ← 컬쳐/엔터테인먼트
FINANCE/WORK     ← 경제/비즈니스
HOBBY_GOODS      ← 게임/엔터테인먼트
```

## 21. 일반 Naver blog sampling

FOOD:
```text
서울 맛집 내돈내산
부산 맛집 후기
돈까스 맛집
파스타 맛집
```

CAFE:
```text
카페 내돈내산
디저트 카페
한옥 카페
```

TRAVEL:
```text
여행 코스
국내 여행 후기
전시 여행
```

BEAUTY:
```text
헤어 후기
펌 후기
네일 후기
```

FASHION:
```text
데일리룩
가방 코디
신발 코디
```

LIFESTYLE:
```text
살림
정리수납
일상
육아
```

PET:
```text
강아지 산책
고양이 일상
반려동물
```

IT:
```text
아이폰 사용기
노트북 후기
앱 사용법
```

BOOK/MOVIE:
```text
책 리뷰
영화 후기
전시 후기
```

PRODUCT/HOBBY:
```text
키링 후기
랜덤 굿즈
피규어
신상 편의점
```

FINANCE:
```text
경제 공부
ETF 공부
재테크 기록
```

## 22. Post selection

권장:
- 공개 댓글
- 댓글 5~60개 정도
- 최근 6~12개월 글 우선
- 지나치게 유명한 셀럽/브랜드 공식 계정 제외
- 이벤트/서이추 목적 글 제외
- 한 블로그에서 최대 2~3 post

## 23. 목표 corpus 규모

첫 버전:

```text
200~300 posts
2,000~3,000 high-quality comments
```

## 24. 카테고리당 권장

16 category 기준:

```text
각 100~180개 comment
```

FOOD/CAFE/TRAVEL은 200~300까지 가능.

## 25. Tier 2 — AI-Hub 한국어 SNS: 10~15%

목적:
- 구어체 종결
- 축약
- 자연스러운 일상 반응
- 말줄임/ㅎㅎ/이모티콘 빈도

본문 anchor 학습에는 사용하지 않음.

실제 사용 전 해당 데이터셋 라이선스/이용 조건 확인.

## 26. Tier 3 — Brunchstory/Tistory: 5~10%

목적:
- BOOK/MOVIE
- CULTURE
- LIFESTYLE
- WORK
- ESSAY 계열

Naver 댓글보다 길고 문어체 비율이 높으므로 low weight.

## 27. YouTube / Instagram

Core corpus로 비추천.

YouTube:
- 짧은 감탄
- 밈
- 과한 emoji
- 주제 anchor 부족

Instagram:
- emoji/짧은 칭찬 편향
- Naver blog 문장 구조와 차이 큼

사용한다면 각 0~5% auxiliary style only.

## 28. Naver Cafe 비추천

회원 전용/비공개 영역이 많고,
게시글 댓글보다 thread conversation 성격이 강함.

## 29. 리뷰 사이트/지도 리뷰 비추천

Naver Place/Kakao Map/쇼핑 리뷰 등은:

```text
"먹어봤어요"
"써봤어요"
"방문했어요"
```

같은 실제 구매/방문 경험 문장이 많아
fabricated past experience 위험을 키울 수 있다.

---

# C. 수집 Schema

## 30. 저장할 것

```json
{
  "post_id_hash": "...",
  "source": "naver_blog",
  "category": "FOOD",
  "post_title": "...",
  "post_excerpt": "...",
  "comment_text": "...",
  "is_reply": false,
  "anchor_terms": ["플래터", "필라프"],
  "reaction_type": "DETAIL_PRAISE",
  "first_person_tense": "future",
  "emoji_count": 1,
  "length": 47
}
```

## 31. 저장하지 않을 것

가능하면 제거:

```text
작성자 닉네임
Naver ID
profile URL
전화번호
email
실명
@mention
```

## 32. URL 보관

원문 검증이 필요한 경우 raw URL은 local-only source map에 두고
derived corpus에는 hash 사용.

---

# D. Quality Filter

## 33. Macro filter

기본 reject:

```text
잘 보고 갑니다
포스팅 잘 보고
서이추
서로이웃
답방
맞방
하트 꾹
공감하고 가요
소통해요
오늘도 좋은 하루
```

## 34. Cross-blog duplicate filter

normalized same comment가
3개 이상 서로 다른 blog에 반복되면 reject.

## 35. Anchor score

좋은 댓글은 본문 실제 entity/detail 1~2개를 언급.

## 36. Past-experience label

```text
저도 가봤는데
먹어봤는데
써봤는데
```

등은:

```text
first_person_tense = past
generation_allowed = false
```

로 label.

## 37. 댓글 품질 점수

```python
score =
  anchor_specificity * 2.0
+ naturalness * 1.5
+ positive_reaction * 1.0
+ conversationality * 1.0
+ useful_first_person_future * 0.8
- macro_similarity * 3.0
- repetition * 2.0
- unsafe_past_transfer * 4.0
```

---

# E. Corpus Split

## 38. blog 단위 split

댓글 단위 random split 금지.

```text
70% train/reference
15% dev
15% test
```

동일 blog는 한 split에만 포함.

---

# F. Antigravity 작업

## 39. Phase 1 — Corpus mining

1. Naver Blog public post 200~300개
2. 16 category 균형
3. post당 quality comment 5~15개
4. author identity 제거
5. macro/duplicate 제거
6. past-experience label
7. entity anchor 자동 추출
8. reply/non-reply 구분

## 40. Phase 2 — Style analysis

```text
평균 길이
문장 수
~네요/~어요/~겠어요 비중
저도/저는/다음에 opener 비중
ㅎㅎ/ㅋㅋ/emoji 비중
anchor 개수
질문 비율
칭찬 비율
future-intent 비율
말줄임표 비율
```

category별 별도 통계.

## 41. Phase 3 — Archetype mining

실제 댓글을 그대로 template화하지 않고
semantic archetype으로 변환.

예:

```text
DETAIL_PRAISE
primary_anchor=main_item
secondary_anchor=side_item
intent=CURIOUS_TO_TRY
ending=casual_positive
```

## 42. Phase 4 — Generator 적용

Corpus는 직접 copy source가 아니라:

```text
ReactionIntent weight
Ending distribution
Opener distribution
Anchor density
Length target
Emoji probability
```

를 업데이트하는 근거로 사용.

---

# G. Acceptance

Gemini:
- stale response 0
- UI header-only accepted 0
- response-scoped copy
- matching request_id required
- paste readback
- same validator as local

Corpus:
- 2,000+ filtered comments
- 16 category coverage
- identities removed
- macro cross-blog duplicates removed
- blog-level split
- copied full comment template generation 금지
