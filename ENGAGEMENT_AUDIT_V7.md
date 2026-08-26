# NAVER FEED ASSISTANT — COMMENT LENGTH + MY BLOG ENGAGEMENT AUDIT v7

## 1. 댓글 길이 정책
- 최종 댓글 hard max: 100자
- 권장 목표: 35~75자
- 85~100자는 허용하되 penalty
- 100자 초과는 Gemini/Local 모두 reject 후 재생성 또는 Local fallback
- 테스트도 180자 허용 기준을 제거하고 100자 기준으로 복구

### FinalCommentQualityGate
```text
12~100자만 허용
35~75자 +3점
76~85자 +1점
86~100자 -1점
101자 이상 reject
```

Gemini prompt에도:
`최종 댓글은 공백 포함 100자 이하, 가능하면 35~75자 정도로 작성하세요.`

## 2. 자동 프롬프트 자기교정 중단
사용자 수정댓글은 계속 기록해도 되지만 다음 동작은 자동으로 하지 않는다.
- Gemini system/prompt 문구 자동 변경
- hard ban 목록 자동 변경
- StyleProfile을 prompt에 자동 주입
- Local Composer weight 자동 변경

권장 config:
```json
{
  "user_learning_record_enabled": true,
  "auto_prompt_learning_enabled": false,
  "auto_style_apply_enabled": false
}
```

즉:
`기록만 계속 → 사용자가 필요할 때 검토 요청 → 사람이 확인한 뒤 정책/프롬프트 수정`

## 3. 내 최근 글 반응자 수집 기능
피드 작업과 분리된 one-shot 관리 기능으로 구현.

UI:
`👥 내 최근 반응자 수집`

버튼 클릭 시:
1. 내 블로그 최근 글 5개 조회
2. 각 글 공감 참여자 목록 수집
3. 각 글 댓글 작성자 목록 수집
4. 동일 블로그 ID 기준 통합
5. 이웃관리용 CSV/JSON 저장
6. 화면에 요약 표시

피드 작업 때마다 자동 실행하지 않는다.

## 4. 내 블로그 ID
Config에 명시:
```json
{
  "my_blog_id": "",
  "engagement_audit_recent_posts": 5
}
```

가능하면 UI에 `내 블로그 ID` 입력란 추가.
로그인 세션에서 억지로 추론하지 않고 명시적 값 우선.

## 5. 저장 파일
### 원본 audit
`data/my_blog_engagement_audit.json`

### 사람이 보기 좋은 목록
`data/my_blog_engagement_audit.csv`

권장 컬럼:
```text
blog_id
nickname
profile_url
liked_post_count
commented_post_count
total_engagement_count
liked_post_titles
commented_post_titles
latest_engagement_at
comment_samples
is_liker
is_commenter
```

## 6. 집계 규칙
동일 `blog_id`를 한 사람으로 통합.

예:
- 최근 5개 중 좋아요 4회
- 댓글 2회

→ 한 행:
```text
liked_post_count=4
commented_post_count=2
total_engagement_count=6
```

정렬:
1. total_engagement_count desc
2. commented_post_count desc
3. liked_post_count desc

## 7. 개인정보 최소화
수집 목적은 이웃관리이므로 공개 블로그 수준 식별자만 저장:
- blog_id
- 공개 nickname
- 공개 profile/blog URL
- 최근 5글에서의 반응 이력

저장하지 않음:
- 이메일
- 전화번호
- 실명 추론
- 위치 추론
- 기타 프로필 개인정보

## 8. 댓글 작성자 수집
댓글 DOM에서:
- 작성자 blog/profile link
- nickname
- post title
- comment text 일부

를 읽는다.

내 댓글(`mine:true`, `u_cbox_type_mine`)은 제외.

답글 작성자는 별도 `is_reply` metadata를 둘 수 있으나 이웃관리 집계에는 포함 가능.

## 9. 공감 참여자 수집
공감 숫자만 읽는 기존 resolver와 별도로:
`ReactionParticipantCollector`

구조:
1. 해당 글 reaction summary/open UI
2. 공감 참여자 목록/레이어 열기
3. 공개 참여자 DOM 반복 수집
4. load-more/scroll이 있으면 bounded scan
5. blog/profile URL에서 blog_id 추출
6. 해당 post에 liked=true 기록

중요:
Naver DOM은 실제 DevTools/Playwright로 먼저 probe해서 selector를 확정.
추측 selector만으로 완료 보고 금지.

## 10. 3-state 결과
좋아요 참여자/댓글 작성자 각각:
- COMPLETE
- PARTIAL
- FAILED

목록이 lazy-load인데 끝까지 읽지 못했으면 COMPLETE라고 하지 않는다.

## 11. 최근 글 5개 수집
`MyBlogRecentPostService`

입력:
`my_blog_id`

출력:
최근 공개 게시글 최대 5개:
- log_no
- title
- url
- published_at

고정 5개가 기본이나 config 값으로 변경 가능하게 한다.

## 12. 서비스 구조
권장:
```text
services/my_blog_recent_posts.py
services/reaction_participant_collector.py
services/comment_participant_collector.py
services/engagement_audit_service.py
services/engagement_audit_store.py
```

## 13. 결과 schema
```json
{
  "generated_at": "...",
  "blog_id": "MY_ID",
  "recent_post_count": 5,
  "posts": [
    {
      "post_url": "...",
      "title": "...",
      "liker_scan_state": "complete",
      "commenter_scan_state": "complete"
    }
  ],
  "people": [
    {
      "blog_id": "abc123",
      "nickname": "닉네임",
      "profile_url": "https://blog.naver.com/abc123",
      "liked_post_count": 4,
      "commented_post_count": 2,
      "total_engagement_count": 6,
      "liked_posts": ["...", "..."],
      "commented_posts": ["..."],
      "comment_samples": ["..."]
    }
  ]
}
```

## 14. UI 결과
수집 완료 후:
```text
최근 글 5개 분석 완료
고유 반응자: 47명
공감 참여자: 39명
댓글 참여자: 21명
공감+댓글 모두: 13명
```

그리고:
- CSV 열기
- 폴더 열기
정도만 제공.

자동 이웃추가/자동 메시지/자동 답방은 하지 않는다.

## 15. 기존 UserLearningService 방향 수정
계속 저장:
- initial_draft
- final_submitted
- edit 여부

하지만 자동 적용하지 않음.

추가로 사람이 나중에 볼 수 있는 통계 정도만 계산 가능:
- 자주 삭제한 표현
- 자주 추가한 표현
- 평균 댓글 길이
- 이모티콘 빈도

이 통계도 `read-only report`로만 제공.

## 16. 필수 테스트
- comment max 100
- Gemini 101자 reject
- Local 101자 reject
- 35~75 preferred
- auto_prompt_learning_enabled false default
- saved user edits do not mutate prompt automatically
- recent posts exactly max 5
- duplicate blog_id merge
- liker/commenter same person merge
- own comment excluded
- partial scan is not COMPLETE
- CSV/JSON deterministic output

## 17. 완료 기준
1. 댓글 100자 초과 0건
2. 자동 프롬프트 교정 0건
3. 사용자 수정 기록은 계속 저장
4. 별도 버튼으로 최근 내 글 5개 one-shot 수집
5. 공감/댓글 참여자를 blog_id 기준 통합
6. CSV + JSON 생성
7. PARTIAL/FAILED 상태 명확히 표시
8. 실제 내 블로그 최근 5글 smoke test
