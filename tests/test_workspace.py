import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from services.workspace.auth import GoogleAuth, WorkspaceError, SCOPES
from services.workspace.client import GoogleWorkspaceClient, APP_ID, MANAGED_TABS
from services.workspace.projection import project_report, literal_cell
from services.workspace.store import WorkspaceStore


def report(run_id='r1', at='2026-08-27T12:00:00+09:00'):
    return {'run_id': run_id, 'blog_id': 'owner', 'generated_at': at,
            'source_kind': 'live', 'audit_state': 'partial', 'policy_version': 'v1.1',
            'master_buddies': [{'blog_id': 'reader', 'nickname': '=IMPORTXML("bad")',
                                'new_posts_setting': 'unknown', 'engaged_post_count': 1,
                                'comment_text': 'PRIVATE COMMENT'}],
            'posts': [], 'cookies': 'SECRET', 'clipboard': 'PRIVATE',
            'quality_issues': ['page_limit', 'token=SECRET', {'code': 'count_mismatch', 'raw': 'SECRET'}]}


class SheetsMemory:
    """Emulates literal Sheets updateCells, retaining unowned tabs."""
    def __init__(self):
        self.writes = []
        self.values = {'사용자 메모': [['내 메모']], '사용자 추가 탭': [['KEEP']]}
        self.ids = {name: 1100 + i for i, name in enumerate(MANAGED_TABS + ('사용자 메모',))}
        self.corrupt_readback = False

    def request(self, method, url, **kwargs):
        if url.endswith('/values:batchGet'):
            values = [copy.deepcopy(self.values[ran.split("'")[1]]) for ran in kwargs['params']['ranges']]
            if self.corrupt_readback:
                values[0][0][1] = 'wrong_run'
            return {'valueRanges': [{'values': v} for v in values]}
        if '/values/' in url:
            return {'values': self.values.get('대시보드', [])[:1]}
        if url.endswith(':batchUpdate'):
            self.writes.append(kwargs['json'])
            for request in kwargs['json']['requests']:
                if 'updateCells' not in request:
                    continue
                update = request['updateCells']
                name = next(n for n, i in self.ids.items() if i == update['range']['sheetId'])
                self.values[name] = [[next(iter(c['userEnteredValue'].values())) for c in r['values']] for r in update['rows']]
            return {}
        return {'sheets': [{'properties': {'sheetId': sid, 'title': name, 'gridProperties': {'rowCount': 100, 'columnCount': 26}}} for name, sid in self.ids.items()]}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name) / 'audit.sqlite3')

    def test_allowlist_excludes_private_material(self):
        result = project_report(report())
        serialized = json.dumps(result)
        for private in ('SECRET', 'PRIVATE', 'cookies', 'clipboard', 'comment_text'):
            self.assertNotIn(private, serialized)
        self.assertEqual(result['quality_issues'], ['page_limit', 'collection_issue', 'count_mismatch'])
        self.assertEqual(literal_cell('=IMPORTXML("bad")'), {'userEnteredValue': {'stringValue': '=IMPORTXML("bad")'}})

    def test_queue_idempotent_and_immutable(self):
        r = project_report(report())
        self.store.enqueue('account', r)
        self.store.enqueue('account', r)
        self.assertEqual(len(self.store.jobs('account')), 1)
        changed = dict(r, audit_state='complete')
        with self.assertRaisesRegex(ValueError, 'immutable_run_changed'):
            self.store.enqueue('account', changed)
        self.store.disconnect('account')
        self.assertEqual(self.store.jobs('account'), [])
        self.store.enqueue('account', r)
        self.assertEqual(len(self.store.jobs('account')), 1)

    def test_sheet_atomic_literal_verified_and_notes_preserved(self):
        fake = SheetsMemory()
        client = GoogleWorkspaceClient(None, self.store, 'a', session=object())
        client.request = fake.request
        self.assertTrue(client.publish({'spreadsheet_id': 's', 'tab_ids': fake.ids}, report(), [report()]))
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(fake.values['사용자 메모'], [['내 메모']])
        self.assertEqual(fake.values['사용자 추가 탭'], [['KEEP']])
        self.assertIn('=IMPORTXML', json.dumps(fake.writes))
        self.assertNotIn('formulaValue', json.dumps(fake.writes))
        fake.corrupt_readback = True
        with self.assertRaisesRegex(WorkspaceError, 'sheet_readback_mismatch'):
            client.publish({'spreadsheet_id': 's', 'tab_ids': fake.ids}, report(), [report()])

    def test_stale_retry_cannot_replace_newer_dashboard(self):
        fake = SheetsMemory()
        fake.values['대시보드'] = [['실행 ID', 'new', '데이터 행 수', 3, '분석 시각', '2026-08-28T12:00:00+09:00']]
        client = GoogleWorkspaceClient(None, self.store, 'a', session=object())
        client.request = fake.request
        self.assertFalse(client.publish({'spreadsheet_id': 's', 'tab_ids': fake.ids}, report(), []))
        self.assertEqual(fake.writes, [])

    def test_ambiguous_creation_recovers_by_artifact_key_without_duplicate(self):
        client = GoogleWorkspaceClient(None, self.store, 'a', session=object())
        self.store.remember_artifact('a', 'records')
        calls = []
        def request(method, url, **kw):
            calls.append(method)
            if url.endswith('/files'):
                return {'files': [{'id': 'created'}]}
            return {'id': 'created', 'mimeType': 'application/vnd.google-apps.folder', 'ownedByMe': True,
                    'parents': ['root-folder'], 'permissions': [{'type': 'user', 'role': 'owner'}],
                    'appProperties': {'app': APP_ID, 'artifact': hashlib.sha256(b'records').hexdigest()}}
        client.request = request
        self.assertEqual(client._ensure_file('records', '분석 기록', 'application/vnd.google-apps.folder', 'root-folder'), 'created')
        self.assertNotIn('POST', calls)

    def test_unknown_creation_never_blindly_recreates(self):
        client = GoogleWorkspaceClient(None, self.store, 'a', session=object())
        self.store.remember_artifact('a', 'records')
        client.request = lambda *a, **kw: {'files': []}
        with self.assertRaisesRegex(WorkspaceError, 'creation_outcome_unknown'):
            client._ensure_file('records', '분석 기록', 'application/vnd.google-apps.folder', 'root-folder')

    def test_sharing_change_blocks_writes_and_never_changes_permissions(self):
        self.store.remember_artifact('a', 'records', 'id')
        client = GoogleWorkspaceClient(None, self.store, 'a', session=object())
        client.request = lambda *a, **kw: {'id': 'id', 'ownedByMe': True, 'mimeType': 'folder',
            'appProperties': {'app': APP_ID, 'artifact': hashlib.sha256(b'records').hexdigest()},
            'parents': ['p'], 'permissions': [{'role': 'owner'}, {'role': 'reader', 'type': 'anyone'}]}
        with self.assertRaisesRegex(WorkspaceError, 'workspace_sharing_changed'):
            client._ensure_file('records', 'records', 'folder', 'p')

    def test_only_drive_file_scope(self):
        self.assertEqual(SCOPES, ['https://www.googleapis.com/auth/drive.file'])

    def test_http_auth_and_network_errors_are_sanitized(self):
        from unittest.mock import Mock
        session = Mock()
        session.request.return_value.status_code = 403
        client = GoogleWorkspaceClient(None, self.store, 'a', session=session)
        with self.assertRaisesRegex(WorkspaceError, 'google_permission_or_auth_required'):
            client.request('GET', 'https://example.invalid')
        session.request.side_effect = OSError('token=secret')
        with self.assertRaises(WorkspaceError) as ctx:
            client.request('GET', 'https://example.invalid')
        self.assertTrue(ctx.exception.retryable)
        self.assertNotIn('secret', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
