"""Google transport and app-owned artifacts. Never edits sharing permissions."""
import hashlib
import json
from urllib.parse import quote
from services.workspace.auth import WorkspaceError
from services.workspace.projection import tables, literal_cell, project_report

APP_ID = 'naver-blog-assistant-v11'
DRIVE = 'https://www.googleapis.com/drive/v3'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'
DOCS = 'https://docs.googleapis.com/v1/documents'
MANAGED_TABS = ('대시보드', '이웃별 현황', '글별 반응', '비이웃 반응', '분석 이력', '데이터 품질')
GUIDE = '''Naver Blog Assistant V1.1 운영 가이드 및 업그레이드 계획

분석 정확성 → 브라우저 보조 모드 → Google Workspace 연결 순서로 개선합니다.
댓글은 수동 ChatGPT를 기본으로 사용하며, 최종 등록은 네이버에서 직접 합니다.
프로그램은 자동 댓글 등록, 이웃 삭제, 새글보기 설정 변경을 하지 않습니다.

시트 읽는 법
공감한 글 수: 공감이 확인된 서로 다른 글 수.
댓글 단 글 수: 댓글이 확인된 서로 다른 글 수.
총댓글 수: 해당 이웃의 확인된 댓글 항목 수. 내 댓글은 제외.
반응한 글 수: 공감 또는 댓글이 확인된 글의 합집합.
새글보기: 내 계정 설정만 표시하며 상대방의 설정이나 푸시 알림이 아닙니다.
부분 수집 값은 확인된 최소 횟수이고, 확인 불가를 무반응으로 판단하지 않습니다.
신규 유예는 한국 날짜 기준 추가일과 이후 2일입니다.
미반응 검토에는 가입 이후·발행 48시간 이상·완전 수집 글이 최소 3개 필요합니다.

개선 제안
반응이 확인되지만 내 새글보기가 OFF인 이웃은 설정을 직접 검토하세요.
신규 이웃은 관찰 기간을 확보하고, 수집 실패는 재분석하세요.
반응 기록만으로 방문 여부·열람 여부·상대의 관심을 추정하지 않습니다.
관계 점수나 자동 삭제는 제공하지 않습니다.

개인정보와 복구
댓글 원문, 쿠키, 로그인 정보, 클립보드는 업로드하지 않습니다.
생성된 탭의 값은 앱에서 갱신합니다. 사용자 메모 탭과 추가 탭은 덮어쓰지 않습니다.
이 문서는 초기 생성 후 자동으로 수정하지 않습니다.
업로드 실패는 로컬에 보존되며 Workspace 탭에서 재시도할 수 있습니다.
기존 자료는 검증 전 참고자료로 보존하며 확정 통계에 섞지 않습니다.
'''
try:
    # The initial Docs file carries the same detailed operating plan shipped
    # with the app. Once initialized it is never overwritten again.
    from pathlib import Path
    _guide_path = Path(__file__).resolve().parent.parent / 'README_V1_1.md'
    if _guide_path.is_file():
        GUIDE += '\n\n' + _guide_path.read_text(encoding='utf-8')
except (OSError, UnicodeError):
    pass


class GoogleWorkspaceClient:
    def __init__(self, credentials, store, account=None, session=None):
        if session is None:
            try:
                from google.auth.transport.requests import AuthorizedSession
                session = AuthorizedSession(credentials)
            except ImportError as exc:
                raise WorkspaceError('workspace_dependencies_required') from exc
        self.session, self.store, self.account = session, store, account
        self.namespace = ''
        self.cancelled = lambda: False

    def request(self, method, url, **kwargs):
        if self.cancelled():
            raise WorkspaceError('sync_cancelled')
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
        except Exception as exc:
            if type(exc).__name__ == 'RefreshError':
                raise WorkspaceError('google_reconnect_required') from exc
            raise WorkspaceError('google_network_error', retryable=True) from exc
        if response.status_code in (401, 403):
            raise WorkspaceError('google_permission_or_auth_required', definitely_rejected=True)
        if response.status_code == 404:
            raise WorkspaceError('workspace_file_missing', definitely_rejected=True)
        if response.status_code == 429 or response.status_code >= 500:
            raise WorkspaceError('google_temporarily_unavailable', retryable=True, definitely_rejected=response.status_code == 429)
        if not 200 <= response.status_code < 300:
            raise WorkspaceError('google_request_rejected', definitely_rejected=True)
        try:
            return response.json()
        except ValueError as exc:
            raise WorkspaceError('google_response_invalid', retryable=True) from exc

    def identity(self):
        user = self.request('GET', DRIVE + '/about', params={'fields': 'user(permissionId,emailAddress,displayName)'})['user']
        if not user.get('permissionId') or not user.get('emailAddress'):
            raise WorkspaceError('google_identity_unavailable')
        return user

    def _ensure_file(self, key, name, mime, parent='root', media=None):
        return self._ensure_namespaced_file(self.namespace + key, name, mime, parent, media)

    def _ensure_namespaced_file(self, key, name, mime, parent='root', media=None):
        recorded, file_id = self.store.artifact(self.account, key)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        fields = 'id,mimeType,appProperties,parents,ownedByMe,trashed,permissions(type,role)'
        if file_id:
            file = self.request('GET', DRIVE + '/files/' + quote(file_id, safe=''), params={'fields': fields})
            if (file.get('trashed') or not file.get('ownedByMe') or file.get('mimeType') != mime
                    or file.get('appProperties', {}).get('app') != APP_ID
                    or file.get('appProperties', {}).get('artifact') != key_hash):
                raise WorkspaceError('workspace_ownership_changed')
            if not file.get('permissions') or any(p.get('role') != 'owner' for p in file['permissions']):
                raise WorkspaceError('workspace_sharing_changed')
            if parent != 'root' and file.get('parents') != [parent]:
                raise WorkspaceError('workspace_parent_changed')
            if parent == 'root':
                root = self.request('GET', DRIVE + '/files/root', params={'fields': 'id'})
                if file.get('parents') != [root['id']]:
                    raise WorkspaceError('workspace_parent_changed')
            return file_id
        # Key is generated internally, never user-supplied Drive query text.
        query = ("trashed=false and 'me' in owners and appProperties has { key='app' and value='" + APP_ID
                 + "' } and appProperties has { key='artifact' and value='" + key_hash + "' }")
        matches = self.request('GET', DRIVE + '/files', params={'q': query, 'fields': 'files(id)', 'pageSize': 10}).get('files', [])
        if len(matches) > 1:
            raise WorkspaceError('duplicate_workspace_artifacts')
        if matches:
            self.store.remember_artifact(self.account, key, matches[0]['id'])
            return self._ensure_namespaced_file(key, name, mime, parent, media)
        if recorded:
            # A prior creation request might have committed. Do not blindly create again.
            raise WorkspaceError('creation_outcome_unknown')
        self.store.remember_artifact(self.account, key)
        body = {'name': name, 'mimeType': mime, 'parents': [parent],
                'appProperties': {'app': APP_ID, 'artifact': key_hash}}
        if media is None:
            created = self._creation_request(key, DRIVE + '/files', params={'fields': 'id'}, json=body)
        else:
            boundary = 'naver_workspace_upload'
            payload = ('--' + boundary + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + json.dumps(body)
                       + '\r\n--' + boundary + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
                       + media + '\r\n--' + boundary + '--').encode('utf-8')
            created = self._creation_request(key, 'https://www.googleapis.com/upload/drive/v3/files',
                                   params={'uploadType': 'multipart', 'fields': 'id'},
                                   headers={'Content-Type': 'multipart/related; boundary=' + boundary}, data=payload)
        if not created.get('id'):
            raise WorkspaceError('creation_outcome_unknown')
        self.store.remember_artifact(self.account, key, created['id'])
        return created['id']

    def _creation_request(self, key, url, **kwargs):
        try:
            return self.request('POST', url, **kwargs)
        except WorkspaceError as exc:
            # A cancellation is a proof that this process never sent the
            # request. It must not leave a creation tombstone behind.
            if exc.definitely_rejected or exc.code == 'sync_cancelled':
                self.store.forget_rejected_intent(self.account, key)
            raise

    def _remember_connection(self, metadata):
        if self.cancelled() or not self.store.update_connection(self.account, metadata):
            raise WorkspaceError('sync_cancelled')

    @staticmethod
    def _document_empty(document):
        bodies = []
        if 'body' in document:
            bodies.append(document['body'])
        def visit(tabs):
            for tab in tabs:
                if 'body' in tab.get('documentTab', {}):
                    bodies.append(tab['documentTab']['body'])
                visit(tab.get('childTabs', []))
        visit(document.get('tabs', []))
        if not bodies:
            return False
        for body in bodies:
            for block in body.get('content', []):
                if 'sectionBreak' in block:
                    continue
                if 'paragraph' not in block:
                    return False
                for element in block['paragraph'].get('elements', []):
                    if 'textRun' not in element or element['textRun'].get('content', '').strip():
                        return False
        return True

    def ensure_workspace(self, metadata):
        self.namespace = 'blog:' + metadata['blog_id'] + ':'
        folder = self._ensure_file('root', 'Naver Blog Workspace', 'application/vnd.google-apps.folder')
        records = self._ensure_file('records', '분석 기록', 'application/vnd.google-apps.folder', folder)
        legacy = self._ensure_file('legacy', '기존 자료 - 검증 전', 'application/vnd.google-apps.folder', folder)
        sheet = self._ensure_file('sheet', '이웃 관계 대시보드', 'application/vnd.google-apps.spreadsheet', folder)
        doc = self._ensure_file('guide', '운영 가이드 및 업그레이드 계획', 'application/vnd.google-apps.document', folder)
        meta = dict(metadata, folder_id=folder, records_id=records, legacy_id=legacy, spreadsheet_id=sheet, document_id=doc)
        self._remember_connection(meta)
        if not meta.get('guide_initialized'):
            document = self.request('GET', DOCS + '/' + doc, params={'includeTabsContent': True})
            if self._document_empty(document):
                self.request('POST', DOCS + '/' + doc + ':batchUpdate', json={'requests': [{'insertText': {'location': {'index': 1}, 'text': GUIDE}}], 'writeControl': {'requiredRevisionId': document['revisionId']}})
            meta['guide_initialized'] = True
            self._remember_connection(meta)
        info = self.request('GET', SHEETS + '/' + sheet, params={'fields': 'sheets.properties'})
        present = {s['properties']['sheetId']: s['properties'] for s in info.get('sheets', [])}
        tab_ids = {name: 1100 + i for i, name in enumerate(MANAGED_TABS + ('사용자 메모',))}
        requests = []
        for name, sid in tab_ids.items():
            if sid in present:
                if present[sid]['title'] != name:
                    raise WorkspaceError('managed_tab_renamed')
            elif meta.get('tabs_initialized'):
                raise WorkspaceError('managed_tab_deleted')
            else:
                if any(p['title'] == name for p in present.values()):
                    raise WorkspaceError('managed_tab_conflict')
                requests.append({'addSheet': {'properties': {'sheetId': sid, 'title': name}}})
        if requests:
            self.request('POST', SHEETS + '/' + sheet + ':batchUpdate', json={'requests': requests})
        meta['tabs_initialized'] = True
        meta['tab_ids'] = tab_ids
        self._remember_connection(meta)
        return meta

    def archive_url(self, run_id):
        _, file_id = self.store.artifact(self.account, self.namespace + 'run:' + run_id)
        return 'https://drive.google.com/file/d/' + file_id + '/view' if file_id else ''

    def archive(self, metadata, report):
        projected = project_report(report)
        key = 'run:' + report['run_id']
        folder = metadata['records_id'] if report.get('source_kind') == 'live' else metadata['legacy_id']
        file_id = self._ensure_file(key, report['run_id'] + '.json', 'application/json', folder,
                                    json.dumps(projected, ensure_ascii=False, sort_keys=True))
        actual = self.request('GET', DRIVE + '/files/' + file_id, params={'alt': 'media'})
        if actual != projected:
            raise WorkspaceError('archive_readback_mismatch')
        return file_id

    def publish(self, metadata, report, history):
        spreadsheet = metadata['spreadsheet_id']
        data = tables(report, history)
        current = self.request('GET', SHEETS + '/' + spreadsheet + '/values/' + quote("'대시보드'!A1:F1", safe=''))
        marker = current.get('values', [[]])[0] if current.get('values') else []
        if len(marker) >= 6 and str(marker[5]) > str(report.get('generated_at', '')):
            return False  # older retry must not replace a newer dashboard
        info = self.request('GET', SHEETS + '/' + spreadsheet, params={'fields': 'sheets.properties'})
        grids = {s['properties']['sheetId']: s['properties']['gridProperties'] for s in info['sheets']}
        requests, expected = [], {}
        for name, table in data.items():
            sid = metadata['tab_ids'][name]
            rows = [['실행 ID', report['run_id'], '데이터 행 수', len(table) - 1, '분석 시각', report.get('generated_at', '')]] + table
            width = max(map(len, rows))
            old_grid = grids[sid]
            row_count = max(old_grid['rowCount'], len(rows))
            col_count = max(old_grid['columnCount'], width)
            requests += [
                {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'rowCount': row_count, 'columnCount': col_count, 'frozenRowCount': 2}}, 'fields': 'gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount'}},
                {'updateCells': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': row_count, 'startColumnIndex': 0, 'endColumnIndex': col_count},
                                 'rows': [{'values': [literal_cell(v) for v in row]} for row in rows], 'fields': 'userEnteredValue'}},
                {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': 2}, 'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}}, 'fields': 'userEnteredFormat.textFormat.bold'}},
            ]
            if len(rows) > 2:
                requests.append({'setBasicFilter': {'filter': {'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': len(rows), 'startColumnIndex': 0, 'endColumnIndex': width}}}})
            expected[name] = rows
        self.request('POST', SHEETS + '/' + spreadsheet + ':batchUpdate', json={'requests': requests})
        # Verify every written value, not merely a successful HTTP response.
        ranges = ["'" + name + "'!A1:Z" + str(len(rows) + 1) for name, rows in expected.items()]
        result = self.request('GET', SHEETS + '/' + spreadsheet + '/values:batchGet', params={'ranges': ranges, 'valueRenderOption': 'UNFORMATTED_VALUE'})
        values = result.get('valueRanges', [])
        if len(values) != len(expected):
            raise WorkspaceError('sheet_readback_mismatch')
        def normalize(rows):
            normalized = []
            for row in rows:
                row = list(row)
                while row and row[-1] == '':
                    row.pop()
                normalized.append(row)
            while normalized and not normalized[-1]:
                normalized.pop()
            return normalized
        if any(normalize(value.get('values', [])) != normalize(rows) for value, rows in zip(values, expected.values())):
            raise WorkspaceError('sheet_readback_mismatch')
        return True
