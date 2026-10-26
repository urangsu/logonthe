# 댓글 후속 제안 문구 등록 사고 점검

## 현황

2026-10-18 로컬 등록 상태 기록 2,239건에서 후속 제안이 포함된 최종 등록 댓글 22건(16개 블로그)을 확인했다. 물음표 포함 등록 댓글 24건을 모두 검토해 정상 반응 2건과 오염 22건을 구분했다. 물음표 없는 편집 제안 후보 검색에서는 추가 후보가 없었다. 네이버 사이트를 현재 시점에 다시 조회하거나 댓글을 삭제/수정하지는 않았다.

아래가 정정된 전체 22개 글 목록이다. 블로그 표시명이 아닌 고유 ID로 구분한다.

- i9930820: https://m.blog.naver.com/i9930820/224439864249
- jungaya20: https://m.blog.naver.com/jungaya20/224442007266
- reviewt_: https://m.blog.naver.com/reviewt_/224443533806
- alsdud1765: https://m.blog.naver.com/alsdud1765/224440451372 / https://m.blog.naver.com/alsdud1765/224443052739
- qazwscplm_: https://m.blog.naver.com/qazwscplm_/224440637879 / https://m.blog.naver.com/qazwscplm_/224440583804
- aquaqua11: https://m.blog.naver.com/aquaqua11/224442986325 / https://m.blog.naver.com/aquaqua11/224441554092
- sunsame1: https://m.blog.naver.com/sunsame1/224443511289 / https://m.blog.naver.com/sunsame1/224442806752
- angelhyeonji33: https://m.blog.naver.com/angelhyeonji33/224444499485
- qaaz3355: https://m.blog.naver.com/qaaz3355/224427619130 / https://m.blog.naver.com/qaaz3355/224441883711
- uhaeng_: https://m.blog.naver.com/uhaeng_/224444314814 / https://m.blog.naver.com/uhaeng_/224443241480
- haan1129: https://m.blog.naver.com/haan1129/224444140396
- with2seo: https://m.blog.naver.com/with2seo/224444095496
- songdae-ri: https://m.blog.naver.com/songdae-ri/224444082860
- yoon_fade: https://m.blog.naver.com/yoon_fade/224443891826
- 7edvnmxy_197: https://m.blog.naver.com/7edvnmxy_197/224443889843
- ha_eun0915: https://m.blog.naver.com/ha_eun0915/224442512477

이전 감사의 2,194건/0건은 당시 기록과 좁은 패턴 기준이었다. 이후 이번 감사에서도 처음 보고한 4건은 부분 검색 결과에 불과했다. 광역 검색과 전체 질문 문장 검토로 최종 22건을 확인했다. 좁은 패턴 검사 결과를 전체 정상 판정이나 방지 보장으로 설명해서는 안 된다.

## 원인과 조치

- 기존 검사는 '필요하신가요/써드릴까요'에 편중돼 확인 요청, 만족 여부 질문, 포인트 변경 제안을 놓쳤다. 실제 네 응답과 추가 표현/공백/줄바꿈/숨은 문자 변형을 회귀 검사에 포함한다.
- 원문 응답, 정제 단계, 최종 품질 검사에서 동일한 오염 판정을 적용한다. 모호한 응답에서 정상 문장을 추측해 잘라 등록하지 않는다.
- 재생성 시 후속 제안을 제거하라는 구체적 피드백을 준다. 한 번 재생성 후에도 오염되면 response_meta_contamination으로 입력/등록을 차단한다.
- 오염된 등록 댓글이 최근 댓글 참고 자료로 재유입될 수 있었다. HistoryStore 반환값, 현재 실행 참고 목록, 학습 텍스트 유효성 검사에서 제외한다. 실제 등록 이력과 중복 방지 상태는 삭제하지 않는다.
- 정상적인 메뉴 질문, 다른 메뉴/와인/디저트 반응 및 이모지는 유지한다.
- 실제 22개 등록 텍스트를 고정 테스트 자료로 보존하고 원문/한 줄/숨은 문자/CRLF 변형을 전부 차단 검사한다. 전체 차단/오탐/이력 격리 검사는 196개다.
- 적용 확인 로그는 `[COMMENT][META_GUARD_READY] version=2026-10-18-meta-v3`이다. 기존 앱을 재시작한 후 새 작업에서 확인한다.

## 한계

패턴 검사는 모든 미지의 표현에 대한 의미적 보장이 아니다. 실전 출력과 현재 적용된 런타임 확인이 필요하다. 기존 앱은 재시작 전까지 이전 Python 코드를 사용할 수 있다. 기존 댓글의 삭제/수정은 사용자가 직접 수행하며, 로컬 중복 방지 이력은 보존한다.
