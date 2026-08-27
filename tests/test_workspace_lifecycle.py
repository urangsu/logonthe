import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from services.audit_repository import AuditRepository
from services.workspace.auth import WorkspaceError
from services.workspace.client import GoogleWorkspaceClient
from services.workspace.store import WorkspaceStore
from services.workspace.sync import WorkspaceSync
from tests.test_workspace import report


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = AuditRepository(Path(self.temp.name) / 'audit.sqlite3')
        self.store = WorkspaceStore(self.repo.db_path)
        self.store.set_connection('account', {'blog_id': 'owner', 'consented': True, 'auto_sync': True})

    def test_cancelled_job_cannot_be_revived_by_late_error(self):
        self.store.enqueue('account', report())
        self.store.disconnect('account')
        self.store.mark('account', 'r1', 'retry', 'network')
        self.assertEqual(self.store.jobs('account'), [])

    def test_late_metadata_does_not_restore_disconnected_consent(self):
        metadata = self.store.connections()['account']
        self.store.disconnect('account')
        self.assertFalse(self.store.update_connection('account', metadata))
        self.assertEqual(self.store.connections(), {})
        self.store.set_connection('account', {'blog_id': 'other', 'consented': True})
        self.assertFalse(self.store.update_connection('account', metadata))
        self.assertEqual(self.store.connections()['account']['blog_id'], 'other')

    def test_user_doc_table_image_or_other_tab_never_empty(self):
        empty = {'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': '\n'}}]}}]}}
        self.assertTrue(GoogleWorkspaceClient._document_empty(empty))
        for block in ({'table': {'tableRows': []}}, {'paragraph': {'elements': [{'inlineObjectElement': {'inlineObjectId': 'img'}}]}}):
            self.assertFalse(GoogleWorkspaceClient._document_empty({'body': {'content': [block]}}))
        self.assertFalse(GoogleWorkspaceClient._document_empty({'tabs': [{'documentTab': {'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': 'USER'}}]}}]}}}]}))

    def test_definitive_creation_rejection_can_retry(self):
        client = GoogleWorkspaceClient(None, self.store, 'account', session=object())
        methods = []
        def fake(method, url, **kwargs):
            methods.append(method)
            if method == 'GET': return {'files': []}
            if methods.count('POST') == 1:
                raise WorkspaceError('google_permission_or_auth_required', definitely_rejected=True)
            return {'id': 'created'}
        client.request = fake
        with self.assertRaises(WorkspaceError): client._ensure_file('records', 'name', 'folder', 'parent')
        self.assertEqual(self.store.artifact('account', 'records'), (False, None))
        self.assertEqual(client._ensure_file('records', 'name', 'folder', 'parent'), 'created')
        self.assertEqual(methods.count('POST'), 2)

    def test_different_blog_or_fixture_job_is_never_sent(self):
        r = report()
        r['master_buddies'] = []
        self.repo.save_run(r)
        self.store.enqueue('account', r)
        self.store.set_connection('account', {'blog_id': 'other', 'consented': True, 'auto_sync': True})
        client = Mock()
        client.identity.return_value = {'permissionId': 'account'}
        client.ensure_workspace.return_value = {'blog_id': 'other'}
        sync = WorkspaceSync(self.repo, auth=Mock(), client_factory=lambda *args: client)
        sync._run(retry=True)
        client.archive.assert_not_called()
        self.assertEqual(self.store.jobs('account'), [])

    def test_resume_local_saved_unscheduled_run_after_restart(self):
        r = report(); r['master_buddies'] = []
        self.repo.save_run(r)
        client = Mock()
        client.identity.return_value = {'permissionId': 'account'}
        client.ensure_workspace.return_value = {'blog_id': 'owner'}
        client.archive_url.return_value = 'archive'
        sync = WorkspaceSync(self.repo, auth=Mock(), client_factory=lambda *args: client)
        sync._run()
        client.archive.assert_called_once()
        client.publish.assert_called_once()
        self.assertEqual(self.store.jobs('account'), [])

    def test_disconnect_during_failed_upload_keeps_cancelled(self):
        r = report(); r['master_buddies'] = []
        self.repo.save_run(r)
        client = Mock()
        client.identity.return_value = {'permissionId': 'account'}
        client.ensure_workspace.return_value = {'blog_id': 'owner'}
        def fail(*args):
            self.store.disconnect('account')
            raise WorkspaceError('network', retryable=True)
        client.archive.side_effect = fail
        sync = WorkspaceSync(self.repo, auth=Mock(), client_factory=lambda *args: client)
        sync._run()
        client.publish.assert_not_called()
        self.assertEqual(self.store.jobs('account'), [])


if __name__ == '__main__': unittest.main()
