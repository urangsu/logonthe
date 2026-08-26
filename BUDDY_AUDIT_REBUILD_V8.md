# NAVER FEED ASSISTANT — BUDDY ENGAGEMENT AUDIT REBUILD v8

## 목적
기준은 `반응자 목록`이 아니라 `전체 이웃 목록`이다.

```text
전체 이웃 N명
→ 최근 실제 게시글 5개
→ 각 이웃별 공감한 글 수
→ 각 이웃별 댓글 단 글 수
→ 둘 다 0이면 무반응
```

댓글 내용은 이웃관리 판단에 사용하지 않는다.

## 1. 현재 가장 큰 결함
현재 결과는 `total_buddies_count=50`인데 실제 기대 이웃은 약 194명이다. 반응 참여자는 108명이다. 전체 이웃 baseline이 첫 50명만 수집되었기 때문에 이후 차집합은 무효다.

`BuddyListCollector`는 `?page=N`을 붙여 URL을 다시 열지만 실제 관리자 frame pagination이 이 parameter를 사용하는지 검증하지 않는다. 2페이지에서도 같은 50명이 보이면 `new_in_page == 0`으로 종료한다.

## 2. BuddyListCollector 전면 수정
금지:
`BuddyListManage.naver?...&page=2`를 추측으로 pagination API라고 가정.

우선:
1. 관리자 페이지 1회 진입
2. `papermain` frame resolve
3. 실제 pagination control DOM 확인
4. 다음 페이지 버튼 실제 click
5. 첫 row fingerprint 변화 확인
6. 새 row 수집
7. next disabled까지 반복

더 좋은 방식은 DevTools Network에서 페이지 이동 시 실제 request를 확인한 뒤 그 endpoint/parameter를 사용하는 것이다. 추측 endpoint 생성 금지.

## 3. 전수 수집 완료 조건
```python
@dataclass
class BuddyCollectionResult:
    buddies: dict[str, BuddyInfo]
    state: Literal["complete", "partial", "failed"]
    expected_total: int | None
    collected_total: int
    pages_visited: int
    page_fingerprints: list[str]
    error: str | None
```

COMPLETE:
- expected_total가 있으면 `collected_total == expected_total`
- 또는 실제 pagination 마지막 페이지 도달이 확인됨

그 외 PARTIAL. PARTIAL이면 무반응 감사 CSV 확정본 생성 금지.

## 4. 총 이웃 수 selector
`.total, em.point, strong.point, span.num...` 같은 generic selector 금지.
`전체` 라벨에 연결된 count 또는 pagination/network metadata로 확인.

## 5. row parser
`tds[1]`, `tds[2]`, `tds[3]` positional parsing만 믿지 않는다.
header text를 읽어 column-index map 생성.

checkbox value를 blog_id fallback으로 쓰지 않는다. buddySeq일 수 있다.
blog_id는 profile/blog URL의 실제 blogId/path에서만 확정.

## 6. 타입 정리
파일 하단 가짜 `class Any: pass` 삭제.
`from typing import Any` 정상 import.

## 7. 최근 글 5개 재정의
현재는 DOM에서 처음 발견한 5개 링크다.

수정:
```text
post cards 수집
→ log_no/title/published_at/pinned 여부
→ published_at DESC
→ 공지/고정 제외
→ 실제 일반 공개글 최근 5개
```

## 8. 공감 참여자 Collector
현재 문제:
- 페이지 전체 blog.naver.com 링크를 긁음
- 최대 5회만 load/scroll
- `scan_state`가 항상 complete
- displayed count 비교 없음

수정:
- 실제 reaction participant container 내부만 수집
- displayed participant count 읽기
- collected unique == displayed count일 때 COMPLETE
- 끝까지 못 읽으면 PARTIAL

## 9. 댓글 작성자 Collector
목표는 내용이 아니라 참여 횟수.

수집:
- blog_id
- nickname
- 해당 post의 comment entry 수

기본 지표:
- `comment_count`: 댓글을 단 최근 글 수(0~5)
- `comment_entry_count`: 실제 댓글 개수

`comment_sample`과 댓글 내용은 이웃관리 리포트에서 제거.

댓글 전체 count와 loaded count를 비교하고 불완전하면 PARTIAL.

## 10. Master Join
모든 이웃을 먼저 0으로 초기화한다.

```python
like_count = 0
comment_count = 0
comment_entry_count = 0
engaged_post_count = 0
```

각 post의 liker/commenter를 merge.

이웃 목록에 없는 반응자는 `non_buddy_reactors`로 분리.

## 11. 핵심 Master CSV
`data/buddy_engagement_audit_YYYYMMDD.csv`

행 수는 전체 이웃 수와 같아야 한다.

컬럼:
```text
blog_id
nickname
blog_title
group_name
buddy_type
added_date
last_post_date
like_count
comment_count
comment_entry_count
engaged_post_count
liked_only
commented_only
both_like_and_comment
no_reaction
scan_complete
```

## 12. 무반응 정의
```python
no_reaction = like_count == 0 and comment_count == 0
```

48시간 유예는 무반응 계산에서 빼지 않는다.
`is_recent_buddy` 별도 컬럼만 제공.

## 13. 산출물
1. `buddy_engagement_audit_YYYYMMDD.csv` — 전체 이웃 전원
2. `unresponsive_buddies_YYYYMMDD.csv` — master에서 두 count 모두 0
3. `non_buddy_reactors_YYYYMMDD.csv` — 반응했지만 현재 이웃이 아닌 사람

기존 participant-only `my_blog_engagement_audit.csv`는 보조 리포트로 내리거나 제거.

## 14. Audit validity gate
최근 5글 중 하나라도 liker/commenter scan이 COMPLETE가 아니면 `audit_state=PARTIAL`.

PARTIAL 상태에서는 사람을 `무반응 확정`으로 표시하지 않는다.

## 15. 테스트 재구성
현재 audit test는 BuddyListCollector를 mock 처리하므로 pagination bug를 못 잡는다.

추가:
```text
test_buddy_pagination_changes_fingerprint
test_buddy_second_page_same_rows_is_partial_not_complete
test_buddy_expected_194_collected_50_fails
test_buddy_master_csv_rows_equal_total_buddies
test_reaction_partial_not_complete
test_comment_partial_not_complete
test_like_count_per_post
test_comment_count_per_post
test_both_counts
test_zero_zero_is_unresponsive
test_non_buddy_reactor_separated
test_recent_posts_sorted_by_date
```

DOM fixture로 4페이지 50+50+50+44 시나리오를 검증.

## 16. Git 데이터 관리
생성 산출물과 개인화 corpus는 Git에 올리지 않는다.

`.gitignore`:
```text
data/my_blog_engagement_audit.*
data/buddy_engagement_audit_*.csv
data/unresponsive_buddies_*.csv
data/non_buddy_reactors_*.csv
data/user_learning_corpus.json
data/accumulated_visited_comments.json
data/naver_comment_corpus.json
data/real_comment_corpus.json
data/dom_db.json
```

이미 tracked이면 `git rm --cached`로 추적만 해제한다.

## 17. 완료 조건
예상 이웃이 194명이면:

```text
expected=194
collected=194
state=COMPLETE
master CSV rows=194
```

각 행에 `like_count`, `comment_count`가 반드시 존재.

무반응 CSV는 master에서 둘 다 0인 사람만 포함.

COMPLETE가 아닌 상태에서 `전수 수집 완료` 로그 출력 금지.
