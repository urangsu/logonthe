# Naver Blog Assistant V1.2 Functional Recovery 기록

이 문서는 `main`을 행동 기준으로 삼은 복구 작업의 변경·검증 기록이다. `codex/naver-assistant-v11`의 커밋은 merge하지 않았으며, 필요한 저수준 개선만 새 브랜치에 선별 이식한다.

## 사용자가 지적한 회귀와 대응

| 지적 | 대응 |
|---|---|
| 기존 자동 공감·댓글 입력·페이지 전환 흐름이 축소됨 | `main`의 `PostProcessor`/`MainWindow`를 유지하고 기본 `assisted_auto` 흐름을 보존했다. 공감·댓글·다음 글 사이의 기존 Pacing UI를 유지하고 첫 공감 전에도 설정된 동작 대기를 적용했다. |
| 본문을 직접 발췌하라고 하면 잠긴 글에서 사용할 수 없음 | SmartEditor, legacy iframe, 본문 후보 및 정제된 body fallback을 프레임별로 자동 탐색한다. 본문이 실제로 없을 때만 제목만으로 댓글을 만들지 않고 재확인 상태로 남긴다. |
| 난수 시간 조절 UI가 사라짐 | `MainWindow`의 동작 대기·다음 글·랜덤 휴지 확률·휴지 범위 입력과 `PacingService` 연결을 유지했다. |
| 프로그램을 끄면 로그인 상태가 풀림 | 기존 영구 프로필 계약인 `data/user_profile`을 사용하며, live Chromium이 있을 때는 락을 강제로 지우지 않는다. |
| 반응 집계에 메뉴 링크가 섞일 수 있음 | BuddyList는 검증된 단일 표와 `buddySeq`/`buddyBlogNo` 행만 읽고, 실제 범위 안의 pager만 사용한다. |

## 이번 브랜치에 포함한 변경

- 본문 extractor 후보 확장 및 프레임/정제 body fallback
- `data/user_profile` 영구 프로필 경로 유지
- BuddyList 실제 표 범위·행 식별·페이지 지문·기대 수 대조·종료 근거
- 감사 결과 JSON/CSV 원자적 저장, 최신 alias, 수식 형태 외부 문자열 이스케이프
- 부분 수집의 0건을 `무반응`으로 확정하지 않고 `확인 불가`로 유지
- 신규 이웃 유예는 참고 열이며 확정 무반응 집계에서 차감하지 않음
- `assistant_mode`를 `workflow_mode`로 안전하게 마이그레이션하되 기본값은 `assisted_auto`
- macOS 더블클릭 실행기 추가
- 기능 패리티·설정·부분 감사·CSV 안전성 회귀 테스트 추가

## 의도적으로 가져오지 않은 것

- `assistant_window` 기본화 및 manual helper 기본화
- Google Workspace/OAuth/Drive 업로드
- Audit의 48시간·신규 유예 기반 eligibility 차단 규칙
- v11 브랜치 전체 merge

## 검증 상태

- 정적/단위 회귀: `python3 -m unittest discover -s tests -q` 통과 (63 tests)
- 실제 네이버 로그인·SmartEditor/legacy iframe·공감 전후·댓글 등록·BuddyList 기대 수 대조는 이 worktree의 사용자 프로필과 계정 상태를 확인한 뒤 별도 smoke 기록으로 남긴다. 이 항목을 실행하지 않은 상태는 실사용 완료로 표시하지 않는다.
