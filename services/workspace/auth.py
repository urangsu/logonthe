"""Desktop OAuth with an explicit macOS Keychain backend, never a plaintext fallback."""
import json
import sys
from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/drive.file']
SERVICE = 'NaverBlogAssistant.GoogleWorkspace'


class WorkspaceError(RuntimeError):
    def __init__(self, code, retryable=False, definitely_rejected=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.definitely_rejected = definitely_rejected


class GoogleAuth:
    def __init__(self, vault=None):
        self._vault = vault

    @property
    def vault(self):
        if self._vault is None:
            if sys.platform != 'darwin':
                raise WorkspaceError('macos_keychain_required')
            try:
                from keyring.backends.macOS import Keyring
                self._vault = Keyring()
            except ImportError as exc:
                raise WorkspaceError('workspace_dependencies_required') from exc
        return self._vault

    def authorize(self, client_file):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise WorkspaceError('workspace_dependencies_required') from exc
        data = json.loads(Path(client_file).read_text(encoding='utf-8'))
        cfg = data.get('installed', {})
        if (cfg.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth'
                or cfg.get('token_uri') != 'https://oauth2.googleapis.com/token'
                or not cfg.get('client_id')):
            raise WorkspaceError('google_desktop_client_required')
        flow = InstalledAppFlow.from_client_config(data, SCOPES, autogenerate_code_verifier=True)
        return flow.run_local_server(host='127.0.0.1', port=0, open_browser=True,
                                     timeout_seconds=120, prompt='consent select_account',
                                     authorization_prompt_message='Google 계정 승인 창을 열었습니다.',
                                     success_message='승인되었습니다. 앱으로 돌아가 전송 항목을 확인하세요.')

    def save(self, account, credentials):
        if not credentials.refresh_token:
            raise WorkspaceError('offline_consent_required')
        self.vault.set_password(SERVICE, account, credentials.to_json())

    def load(self, account):
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise WorkspaceError('workspace_dependencies_required') from exc
        raw = self.vault.get_password(SERVICE, account)
        if not raw:
            raise WorkspaceError('google_reconnect_required')
        data = json.loads(raw)
        if data.get('token_uri') != 'https://oauth2.googleapis.com/token' or set(data.get('scopes', [])) != set(SCOPES):
            raise WorkspaceError('credential_scope_mismatch')
        return Credentials.from_authorized_user_info(data, SCOPES)

    def disconnect(self, account):
        if self.vault.get_password(SERVICE, account):
            self.vault.delete_password(SERVICE, account)
