"""Run explicitly on macOS: isolated native UI smoke, no provider/profile access."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from services.audit_repository import AuditRepository
from services.config import ConfigService
from ui.assistant_window import MainWindow


def run():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config = ConfigService(str(root / 'config.json'))
        config.save({'my_blog_id': 'ui_fixture', 'fixed_suffix': '', 'assistant_mode': True})
        repo = AuditRepository(root / 'audit.sqlite3')
        with patch.object(MainWindow, '_startup', lambda self: None):
            app = MainWindow(config, repo)
            try:
                app.update()
                for tab in ('글 탐색', '반응분석', 'Workspace', '설정'):
                    app.tabs.set(tab)
                    app.update()
                    assert app.tabs.tab(tab).winfo_ismapped(), tab
                assert app._save_settings()
                fixture = {'run_id': 'ui-fixture', 'blog_id': 'ui_fixture', 'source_kind': 'fixture',
                    'generated_at': 'UI 검증 자료 · 실제 분석 아님', 'audit_state': 'partial', 'quality_issues': ['fixture'],
                    'master_buddies': [{'blog_id': 'test_buddy', 'nickname': 'UI 검증 전용', 'new_posts_setting': 'unknown',
                                       'like_count': None, 'comment_count': None, 'comment_entry_count': None,
                                       'engaged_post_count': None, 'reaction_status': '확인 불가'}]}
                app.tabs.set('반응분석')
                app._refresh_audit(fixture)
                app.update()
                assert app.tree.item('test_buddy')['values'][2] == '확인 불가'
                app.filter_text.set('없음')
                app.update()
                assert not app.tree.get_children()
                app.filter_text.set('검증')
                app.update()
                assert len(app.tree.get_children()) == 1
                assert app.tree.winfo_width() > 600
                assert not (root / 'history.json').exists()
                print(json.dumps({'native_ui': 'passed', 'tabs': 4, 'search': 'passed', 'null_values': 'unknown', 'live_provider': False}))
            finally:
                app.sync.stop()
                app.destroy()


if __name__ == '__main__':
    run()
