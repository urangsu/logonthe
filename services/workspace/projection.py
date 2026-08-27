"""Explicit upload allowlist. Remote strings are always literal cell values."""
import json
import re

RUN_FIELDS = ('run_id', 'blog_id', 'generated_at', 'started_at', 'finished_at', 'policy_version',
              'source_kind', 'audit_state', 'recent_post_count', 'requested_post_count',
              'total_buddies_count', 'expected_buddies_count', 'reacted_buddies_count',
              'buddy_list_state')
BUDDY_FIELDS = ('blog_id', 'nickname', 'blog_title', 'group_name', 'buddy_type', 'added_date',
                'last_post_date', 'new_posts_setting', 'setting_observed_at', 'like_count',
                'comment_count', 'comment_entry_count', 'engaged_post_count', 'reaction_status',
                'scan_complete', 'eligible_post_count', 'is_recent_buddy', 'counts_are_lower_bounds', 'relationship_state')
POST_FIELDS = ('log_no', 'title', 'post_url', 'published_at', 'liker_count', 'liker_displayed',
               'liker_scan_state', 'commenter_count', 'commenter_displayed', 'commenter_scan_state',
               'comment_entry_count')


def safe_value(value):
    if value is None:
        return '확인 불가'
    if isinstance(value, (str, bool, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def project_report(report):
    result = {k: report[k] for k in RUN_FIELDS if k in report}
    for key, fields in (('master_buddies', BUDDY_FIELDS), ('non_buddy_reactors', BUDDY_FIELDS), ('unknown_relationship_reactors', BUDDY_FIELDS), ('posts', POST_FIELDS)):
        result[key] = [{k: row[k] for k in fields if k in row} for row in report.get(key, [])]
    # Only structured diagnostic codes are allowed; never copy arbitrary error payloads.
    issues = report.get('quality_issues', [])
    codes = [x.get('code', 'collection_issue') if isinstance(x, dict) else x for x in issues]
    result['quality_issues'] = [x if isinstance(x, str) and re.fullmatch(r'(?:\d{1,30}:(?:reaction|comment):)?[a-z][a-z0-9_]{0,79}', x)
                              else 'collection_issue' for x in codes]
    return result


def tables(report, history):
    r = project_report(report)
    buddies = r['master_buddies']
    feed = {state: sum(b.get('new_posts_setting', 'unknown') == state for b in buddies)
            for state in ('on', 'off', 'unknown')}
    complete = next((h.get('generated_at') for h in history if h.get('audit_state') == 'complete' and h.get('source_kind') == 'live'), None)
    result = {
        '대시보드': [['항목', '값'], ['분석 시각', r.get('generated_at')], ['분석 상태', r.get('audit_state')],
                  ['마지막 완전 수집', complete], ['확인된 이웃', len(buddies)],
                  ['관리 화면 총 이웃', r.get('expected_buddies_count')],
                  ['반응 확인 인원', sum(isinstance(b.get('engaged_post_count'), int) and b['engaged_post_count'] > 0 for b in buddies)],
                  ['내 새글보기 ON', feed['on']], ['내 새글보기 OFF', feed['off']], ['내 새글보기 확인 불가', feed['unknown']],
                  ['주의', '부분 수집 횟수는 확인된 최소 횟수입니다. 방문·열람 여부는 알 수 없습니다.']],
        '이웃별 현황': [['블로그 ID', '닉네임', '블로그명', '그룹', '이웃 구분', '추가일', '내 새글보기', '설정 확인 시각',
                       '공감한 글 수', '댓글 단 글 수', '총댓글 수', '반응한 글 수', '판단 상태', '검사 완전성', '블로그 링크']],
        '글별 반응': [['글 제목', '글 링크', '발행 시각', '확인 공감자', '표시 공감수', '공감 수집', '댓글 작성자', '댓글 항목 수', '댓글 수집']],
        '비이웃 반응': [['블로그 ID', '닉네임', '공감한 글 수', '댓글 단 글 수', '총댓글 수', '반응한 글 수', '블로그 링크']],
        '분석 이력': [['실행 ID', '시각', '상태', '대상 글 수', '관측 이웃', '출처', '비교 상태', '기록 링크']],
        '데이터 품질': [['항목', '내용'], ['집계 기준', '공감한 글 수와 댓글 단 글 수의 합집합을 반응한 글 수로 계산합니다.'],
                      ['새글보기', '내 계정 설정만 읽습니다. 푸시 알림이나 상대방 설정이 아닙니다.']],
    }
    for b in buddies:
        result['이웃별 현황'].append([b.get(k) for k in ('blog_id', 'nickname', 'blog_title', 'group_name', 'buddy_type', 'added_date', 'new_posts_setting', 'setting_observed_at', 'like_count', 'comment_count', 'comment_entry_count', 'engaged_post_count', 'reaction_status', 'scan_complete')] + ['https://m.blog.naver.com/' + b.get('blog_id', '')])
    for p in r['posts']:
        result['글별 반응'].append([p.get(k) for k in ('title', 'post_url', 'published_at', 'liker_count', 'liker_displayed', 'liker_scan_state', 'commenter_count', 'comment_entry_count', 'commenter_scan_state')])
    for b in r['non_buddy_reactors']:
        result['비이웃 반응'].append([b.get(k) for k in ('blog_id', 'nickname', 'like_count', 'comment_count', 'comment_entry_count', 'engaged_post_count')] + ['https://m.blog.naver.com/' + b.get('blog_id', '')])
    for h in history:
        comparison = h.get('comparison') or {}
        reason = '비교 가능' if comparison.get('comparable') else ', '.join(comparison.get('reasons', [])) or '동일 글·정책·대상 집합일 때만 비교'
        result['분석 이력'].append([h.get(k) for k in ('run_id', 'generated_at', 'audit_state', 'recent_post_count', 'total_buddies_count', 'source_kind')] + [reason, h.get('archive_url', '')])
    for b in buddies:
        if b.get('new_posts_setting') == 'off' and isinstance(b.get('engaged_post_count'), int) and b['engaged_post_count'] > 0:
            result['데이터 품질'].append(['설정 검토', b['blog_id'] + ': 반응이 있지만 내 새글보기는 OFF'])
        if b.get('is_recent_buddy') is True:
            result['데이터 품질'].append(['추가 관찰', b['blog_id'] + ': 신규 이웃 달력 유예 기간'])
        elif b.get('scan_complete') is not True:
            result['데이터 품질'].append(['재확인 필요', b['blog_id'] + ': 수집이 완전하지 않아 부재 확정 불가'])
    result['데이터 품질'].extend([['수집 진단', x] for x in r['quality_issues']])
    result['데이터 품질'].extend([['이웃 여부 확인 불가', b.get('blog_id', '')] for b in r['unknown_relationship_reactors']])
    return {name: [[safe_value(v) for v in row] for row in rows] for name, rows in result.items()}


def literal_cell(value):
    if isinstance(value, bool):
        return {'userEnteredValue': {'boolValue': value}}
    if isinstance(value, (int, float)):
        return {'userEnteredValue': {'numberValue': value}}
    return {'userEnteredValue': {'stringValue': str(value)}}
