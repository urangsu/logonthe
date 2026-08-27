"""One background uploader per app; local audits never wait for Google."""
import threading
import fcntl
from services.workspace.auth import GoogleAuth, WorkspaceError
from services.workspace.client import GoogleWorkspaceClient
from services.workspace.projection import project_report
from services.workspace.store import WorkspaceStore


class WorkspaceSync:
    def __init__(self, repository, notify=lambda message: None, auth=None, client_factory=GoogleWorkspaceClient):
        self.repository = repository
        self.store = WorkspaceStore(repository.db_path)
        self.auth = auth or GoogleAuth()
        self.notify = notify
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._requested = False
        self._retry_requested = False

    def start(self, retry=False):
        with self._lock:
            self._requested = True
            self._retry_requested = self._retry_requested or retry
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._drain, daemon=True, name='workspace-sync')
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stop.set()
            self._requested = False
            self._retry_requested = False

    def _drain(self):
        # A second app instance must not race a sheet write or file creation.
        with open(str(self.repository.db_path) + '.sync.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.notify('다른 앱이 Workspace 동기화 중입니다. 대기열은 보존됩니다.')
                with self._lock:
                    self._thread = None
                return
            while not self._stop.is_set():
                with self._lock:
                    if not self._requested:
                        self._thread = None
                        return
                    self._requested = False
                    retry = self._retry_requested
                    self._retry_requested = False
                try:
                    self._run(retry)
                except Exception:
                    self.notify('Workspace 대기열 확인 실패 · 로컬 결과는 보존됩니다.')
        with self._lock:
            self._thread = None
            restart = self._requested
        if restart:
            self.start()

    def _run(self, retry=False):
        for account, meta in self.store.connections().items():
            if self._stop.is_set():
                return
            if not meta.get('consented') or not (meta.get('auto_sync') or retry):
                continue
            history = [r for r in self.repository.list_runs() if r.get('blog_id') == meta.get('blog_id')]
            for report in history:
                if report.get('source_kind') in ('live', 'legacy_unverified'):
                    self.store.enqueue(account, project_report(report))
            jobs = self.store.jobs(account)
            try:
                credentials = self.auth.load(account)
                client = self.client_factory(credentials, self.store, account)
                generation = meta.get('connection_id')
                client.cancelled = lambda: self._stop.is_set() or self.store.connections().get(account, {}).get('connection_id') != generation
                if client.identity()['permissionId'] != account:
                    raise WorkspaceError('google_account_mismatch')
                metadata = client.ensure_workspace(meta)
                self.notify('비공개 Workspace 연결 확인됨')
                live = [r for r in history if r.get('source_kind') == 'live']
                for index, run in enumerate(live):
                    if index + 1 < len(live):
                        run['comparison'] = self.repository.compare_runs(live[index + 1]['run_id'], run['run_id'])
                for job in jobs:
                    if client.cancelled():
                        return
                    if job['state'] in ('auth_required', 'blocked') and not retry:
                        continue
                    report = self.repository.get_run(job['run_id'])
                    if not report:
                        self.store.mark(account, job['run_id'], 'blocked', 'run_missing')
                        continue
                    if report.get('blog_id') != meta.get('blog_id') or report.get('source_kind') not in ('live', 'legacy_unverified'):
                        self.store.mark(account, job['run_id'], 'cancelled', 'consent_scope_mismatch')
                        continue
                    for attempt in range(3):
                        try:
                            client.archive(metadata, report)
                            if live:
                                for run in live: run['archive_url'] = client.archive_url(run['run_id'])
                                client.publish(metadata, live[0], live)
                            if client.cancelled():
                                return
                            self.store.mark(account, job['run_id'], 'synced')
                            self.notify('Workspace 동기화 확인 완료')
                            break
                        except WorkspaceError as exc:
                            if client.cancelled():
                                return
                            state = 'retry' if exc.retryable else ('auth_required' if 'auth' in exc.code or 'reconnect' in exc.code else 'blocked')
                            self.store.mark(account, job['run_id'], state, exc.code)
                            if not exc.retryable or attempt == 2:
                                self.notify('업로드 미완료: ' + exc.code + ' (로컬 결과 보존)')
                                break
                            if self._stop.wait(2 ** attempt):
                                return
            except WorkspaceError as exc:
                self.notify('Workspace 연결 확인 필요: ' + exc.code)
            except Exception:
                self.notify('Workspace 동기화 중단: 로컬 결과는 보존됩니다.')

    def disconnect(self, account):
        self.stop()
        self.store.disconnect(account)
        self.auth.disconnect(account)
