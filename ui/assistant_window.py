"""V1.1 desktop shell. Tk stays on the main thread; browser and uploads do not."""
import json
import queue
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from app.controller import FeedController
from app.state import StateManager
from browser.session import BrowserSession
from naver.auth_guard import NaverAuthGuard
from services.audit_repository import AuditRepository
from services.config import ConfigService, ConfigConflictError
from services.engagement_audit_service import EngagementAuditService
from services.history import HistoryStore
from services.runtime_gate import browser_worker, BrowserWorkerBusy
from services.workspace.auth import WorkspaceError
from services.workspace.client import GoogleWorkspaceClient
from services.workspace.sync import WorkspaceSync

ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((Path(__file__).parent / 'tokens.json').read_text())
SOURCES = {'이웃 새글': 'neighbor', '주제별 글': 'targeted_search', '추천 글': 'recommendation', '직접 URL': 'direct'}
STATUS = {'complete': '완전 수집', 'partial': '부분 결과', 'failed': '수집 실패', 'cancelled': '취소됨'}


class MainWindow(ctk.CTk):
    def __init__(self, config=None, repository=None):
        super().__init__()
        ctk.set_appearance_mode('dark')
        self.title('Naver Blog Assistant')
        self.geometry('1080x780')
        self.minsize(900, 660)
        self.configure(fg_color=TOKENS['surface_base'])
        self.config_service = config or ConfigService()
        self.repository = repository or AuditRepository()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.oauth_worker = None
        self.closing = False
        self.sync = WorkspaceSync(self.repository, lambda msg: self.events.put(('workspace', msg)))
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._label(self, 'Naver Blog Assistant', 27, True).grid(row=0, column=0, sticky='w', padx=28, pady=(24, 3))
        self._label(self, '직접 쓰고, 근거를 확인하고, 한곳에서 관리합니다.', 14, secondary=True).grid(row=1, column=0, sticky='w', padx=28, pady=(0, 18))
        self.tabs = ctk.CTkTabview(self, fg_color=TOKENS['surface_panel'], segmented_button_fg_color=TOKENS['surface_raised'], segmented_button_selected_color=TOKENS['accent_hover'])
        self.tabs.grid(row=2, column=0, sticky='nsew', padx=24, pady=(0, 16))
        for name in ('글 탐색', '반응분석', 'Workspace', '설정'):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
        self._build_feed()
        self._build_audit()
        self._build_workspace()
        self._build_settings()
        self.status = self._label(self, '대기 중 · 댓글 등록은 네이버에서 직접 합니다.', 13, secondary=True)
        self.status.grid(row=3, column=0, padx=28, pady=(0, 18), sticky='w')
        self.after(100, self._poll)
        self.after(150, self._startup)

    def _label(self, parent, text, size=14, bold=False, secondary=False):
        return ctk.CTkLabel(parent, text=text, anchor='w', justify='left', wraplength=940,
                            text_color=TOKENS['text_secondary' if secondary else 'text_primary'],
                            font=ctk.CTkFont(family='Apple SD Gothic Neo', size=size, weight='bold' if bold else 'normal'))

    def _button(self, parent, text, command, secondary=False):
        return ctk.CTkButton(parent, text=text, command=command, height=38, corner_radius=8,
                             fg_color=TOKENS['surface_raised' if secondary else 'accent_hover'],
                             hover_color=TOKENS['border' if secondary else 'accent'], text_color=TOKENS['text_primary'])

    def _row(self, parent, row):
        frame = ctk.CTkFrame(parent, fg_color='transparent')
        frame.grid(row=row, column=0, sticky='ew', padx=18, pady=8)
        return frame

    def _build_feed(self):
        tab = self.tabs.tab('글 탐색')
        self._label(tab, '글에 맞는 댓글을 직접 완성하세요', 21, True).grid(row=0, sticky='w', padx=18, pady=(22, 6))
        self._label(tab, '글 확인 → 프롬프트 복사 → ChatGPT → 결과 붙여넣기 → 검증 → 댓글창에 넣기\n브라우저 안의 보조 패널을 사용합니다. 자동 공감·자동 등록·API 요청은 실행하지 않습니다.', secondary=True).grid(row=1, sticky='w', padx=18, pady=8)
        row = self._row(tab, 2)
        source = next((k for k, v in SOURCES.items() if v == self.config_service.get('feed_source')), '이웃 새글')
        self.source = ctk.StringVar(value=source)
        ctk.CTkOptionMenu(row, values=list(SOURCES), variable=self.source, width=190).pack(side='left')
        self._button(row, '탐색 시작', self._start_feed).pack(side='left', padx=12)
        self._button(row, '중단', self._stop_worker, True).pack(side='left')
        self._button(row, '네이버 로그인', self._login, True).pack(side='right')
        self.feed_status = self._label(tab, '진행 중인 글 없음', 16, True)
        self.feed_status.grid(row=3, sticky='w', padx=18, pady=(20, 8))
        self.feed_detail = self._label(tab, '작성 중에는 반응분석을 동시에 시작하지 않습니다.\n전환하려면 중단을 누르고 초안을 보존한 뒤 새 작업을 시작하세요.', secondary=True)
        self.feed_detail.grid(row=4, sticky='w', padx=18, pady=8)
        self._label(tab, '작업 모드', 15, True).grid(row=5, sticky='w', padx=18, pady=(28, 4))
        self._label(tab, '설정에서 수동 보조 또는 기존 자동화 모드를 선택합니다.\n자동화 모드에서도 최종 댓글 등록은 네이버 화면에서 확인 절차를 거칩니다.', secondary=True).grid(row=6, sticky='w', padx=18, pady=8)

    def _build_audit(self):
        tab = self.tabs.tab('반응분석')
        self._label(tab, '이웃 관계 현황', 21, True).grid(row=0, sticky='w', padx=18, pady=(22, 6))
        row = self._row(tab, 1)
        self._label(row, '최근 공개 글').pack(side='left', padx=(0, 12))
        self.post_count = ctk.StringVar(value=str(self.config_service.get('engagement_audit_recent_posts', 5)))
        if self.post_count.get() not in ('5', '10', '20'): self.post_count.set('5')
        ctk.CTkOptionMenu(row, values=['5', '10', '20'], variable=self.post_count, width=80).pack(side='left')
        self._button(row, '분석 시작', self._start_audit).pack(side='left', padx=12)
        self._button(row, '취소', self._stop_worker, True).pack(side='left')
        self.audit_summary = self._label(tab, '저장된 분석을 불러옵니다.', 15, True)
        self.audit_summary.grid(row=2, sticky='w', padx=18, pady=8)
        self.audit_quality = self._label(tab, '부분 수집에서 발견하지 못한 반응은 확인 불가입니다.', 12, secondary=True)
        self.audit_quality.grid(row=3, sticky='w', padx=18, pady=4)
        controls = self._row(tab, 4)
        self.filter_text = ctk.StringVar()
        ctk.CTkEntry(controls, textvariable=self.filter_text, placeholder_text='ID·닉네임 검색', width=240).pack(side='left')
        self.filter_text.trace_add('write', lambda *_: self._render_rows())
        self._button(controls, '분석 기록 폴더', self._open_exports, True).pack(side='right')
        frame = self._row(tab, 5)
        tab.grid_rowconfigure(5, weight=1)
        frame.grid(sticky='nsew')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Audit.Treeview', background=TOKENS['surface_panel'], fieldbackground=TOKENS['surface_panel'], foreground=TOKENS['text_primary'], rowheight=30, borderwidth=0)
        style.configure('Audit.Treeview.Heading', background=TOKENS['surface_raised'], foreground=TOKENS['text_primary'], relief='flat')
        columns = ('nickname', 'feed', 'likes', 'comments', 'entries', 'engaged', 'state')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', style='Audit.Treeview', height=10)
        for col, title, width in zip(columns, ('이웃', '내 새글보기', '공감 글', '댓글 글', '총댓글', '반응 글', '판단 상태'), (180, 110, 80, 80, 80, 80, 140)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', self._visit_buddy)
        self.tree.bind('<Return>', self._visit_buddy)
        self.current_report = None

    def _build_workspace(self):
        tab = self.tabs.tab('Workspace')
        self._label(tab, 'Google Workspace', 21, True).grid(row=0, sticky='w', padx=18, pady=(22, 6))
        self._label(tab, '내 드라이브의 비공개 Naver Blog Workspace 폴더에 저장합니다.\n앱용 Desktop OAuth 클라이언트 JSON과 Google 계정 승인이 필요합니다.', secondary=True).grid(row=1, sticky='w', padx=18, pady=8)
        self.workspace_status = self._label(tab, '연결되지 않음 · 로컬 분석은 계속 사용할 수 있습니다.', 15, True)
        self.workspace_status.grid(row=2, sticky='w', padx=18, pady=14)
        row = self._row(tab, 3)
        self._button(row, 'Google 계정 연결', self._connect_google).pack(side='left')
        self._button(row, '전송 / 재시도', lambda: self.sync.start(retry=True), True).pack(side='left', padx=10)
        self._button(row, '폴더 열기', self._open_workspace, True).pack(side='left')
        self._button(row, '연결 해제', self._disconnect_google, True).pack(side='right')
        self._label(tab, '전송 항목', 16, True).grid(row=4, sticky='w', padx=18, pady=(22, 6))
        self._label(tab, '블로그 ID·닉네임·링크·그룹·추가일·반응 집계·내 새글보기 설정·관측 시각\n댓글 원문, 쿠키, 로그인 정보, 클립보드는 전송하지 않습니다.', secondary=True).grid(row=5, sticky='w', padx=18, pady=6)
        self._label(tab, '갱신 및 보존', 16, True).grid(row=6, sticky='w', padx=18, pady=(22, 6))
        self._label(tab, '처음 연결할 때 자동 동기화 여부를 선택합니다.\n사용자 메모·추가 탭·공유 권한과 운영 문서의 편집 내용은 수정하지 않습니다.\n연결 해제는 대기열과 로컬 토큰만 중단·제거하며, Google 파일은 삭제하지 않습니다.', secondary=True).grid(row=7, sticky='w', padx=18, pady=6)

    def _build_settings(self):
        tab = self.tabs.tab('설정')
        pane = ctk.CTkScrollableFrame(tab, fg_color='transparent')
        pane.grid(row=0, sticky='nsew')
        tab.grid_rowconfigure(0, weight=1)
        pane.grid_columnconfigure(1, weight=1)
        self.settings = {}
        fields = [('my_blog_id', '내 블로그 ID', ''), ('max_feed_items', '탐색할 최대 글 수', 20),
                  ('posts_per_query', '검색어별 글 수', 3), ('ai_context_max_chars', '본문 발췌 최대 글자 수', 1800),
                  ('fixed_suffix', '선택 꼬리말', ''), ('direct_urls', '직접 URL (한 줄에 하나)', []),
                  ('custom_discovery_queries', '추가 검색어 (한 줄에 하나)', []),
                  ('action_delay_min', '행동 간 최소 대기(초)', 1.0), ('action_delay_max', '행동 간 최대 대기(초)', 2.5),
                  ('next_post_delay_min', '다음 글 최소 대기(초)', 2.0), ('next_post_delay_max', '다음 글 최대 대기(초)', 5.0),
                  ('random_pause_chance', '랜덤 휴지 확률(0~1)', 0.10), ('random_pause_min', '랜덤 휴지 최소(초)', 8.0),
                  ('random_pause_max', '랜덤 휴지 최대(초)', 20.0)]
        self.float_keys = {'action_delay_min', 'action_delay_max', 'next_post_delay_min', 'next_post_delay_max',
                           'random_pause_chance', 'random_pause_min', 'random_pause_max'}
        for i, (key, label, default) in enumerate(fields):
            self._label(pane, label).grid(row=i, column=0, sticky='nw', padx=16, pady=12)
            value = self.config_service.get(key, default)
            if isinstance(default, list):
                widget = ctk.CTkTextbox(pane, height=75)
                widget.insert('1.0', '\n'.join(value))
            else:
                widget = ctk.CTkEntry(pane)
                widget.insert(0, str(value))
            widget.grid(row=i, column=1, sticky='ew', padx=16, pady=8)
            self.settings[key] = widget
        self.append_suffix = ctk.BooleanVar(value=bool(self.config_service.get('append_fixed_suffix_to_ai', False)))
        ctk.CTkCheckBox(pane, text='꼬리말을 포함한 최종 문장 검사 (12~100자)', variable=self.append_suffix).grid(row=7, column=1, sticky='w', padx=16, pady=12)
        self.legacy_automation = ctk.BooleanVar(value=not bool(self.config_service.get('assistant_mode', True)))
        ctk.CTkCheckBox(pane, text='기존 자동화 모드 (Gemini 입력·답변 복사·공감·댓글창 삽입)', variable=self.legacy_automation).grid(row=8, column=1, sticky='w', padx=16, pady=8)
        self.gemini_enabled = ctk.BooleanVar(value=bool(self.config_service.get('gemini_web_enabled', False)))
        ctk.CTkCheckBox(pane, text='Gemini 웹 자동 생성 사용', variable=self.gemini_enabled).grid(row=9, column=1, sticky='w', padx=16, pady=8)
        self.gemini_browser_mode = ctk.StringVar(value=str(self.config_service.get('gemini_browser_mode', 'existing_chrome_mac')))
        self._label(pane, 'Gemini 브라우저', secondary=True).grid(row=10, column=0, sticky='w', padx=16, pady=8)
        ctk.CTkOptionMenu(pane, variable=self.gemini_browser_mode,
                          values=['existing_chrome_mac', 'managed_playwright']).grid(row=10, column=1, sticky='w', padx=16, pady=8)
        self.pacing_enabled = ctk.BooleanVar(value=bool(self.config_service.get('pacing_enabled', True)))
        ctk.CTkCheckBox(pane, text='행동·페이지 간 대기 사용', variable=self.pacing_enabled).grid(row=11, column=1, sticky='w', padx=16, pady=8)
        self.random_pause_enabled = ctk.BooleanVar(value=bool(self.config_service.get('random_pause_enabled', True)))
        ctk.CTkCheckBox(pane, text='랜덤 휴지 사용', variable=self.random_pause_enabled).grid(row=12, column=1, sticky='w', padx=16, pady=8)
        self._button(pane, '설정 저장', self._save_settings).grid(row=13, column=1, sticky='e', padx=16, pady=16)
        self._label(pane, '자동화 모드는 기존 웹 자동화 흐름을 복원합니다. Google 로그인 차단·CAPTCHA·사이트 변경에 영향을 받을 수 있습니다.\n페이지 간 대기와 행동 간 난수 지연은 위 입력값을 사용합니다. 최종 댓글 등록은 네이버 화면의 확인 절차를 거칩니다.\nGoogle 로그인을 일반 Chrome에서 유지하려면 existing_chrome_mac을 선택하고 Chrome의 Apple Events JavaScript 허용을 켜세요.\n설정 저장 전 원본을 백업하며 다른 프로세스의 변경은 덮어쓰지 않습니다.', 12, secondary=True).grid(row=14, column=0, columnspan=2, sticky='w', padx=16, pady=12)

    def _save_settings(self):
        values = {}
        for key, widget in self.settings.items():
            value = widget.get('1.0', 'end').strip() if isinstance(widget, ctk.CTkTextbox) else widget.get().strip()
            if key in ('max_feed_items', 'posts_per_query', 'ai_context_max_chars'):
                bounds = (1, 100) if key == 'max_feed_items' else (1, 20) if key == 'posts_per_query' else (100, 5000)
                try:
                    value = int(value)
                    if not bounds[0] <= value <= bounds[1]: raise ValueError()
                except ValueError:
                    messagebox.showerror('설정 확인', f'{key}: {bounds[0]}~{bounds[1]} 사이 정수를 입력하세요.')
                    return False
            elif key in self.float_keys:
                maximum = 1.0 if key == 'random_pause_chance' else 3600.0
                try:
                    value = float(value)
                    if value < 0 or value > maximum: raise ValueError()
                except ValueError:
                    messagebox.showerror('설정 확인', f'{key}: 0~{maximum} 범위의 숫자를 입력하세요.')
                    return False
            elif key in ('direct_urls', 'custom_discovery_queries'):
                value = [s.strip() for s in value.splitlines() if s.strip()]
            values[key] = value
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('작업 중', '현재 작업을 중단한 뒤 설정을 변경하세요.')
            return False
        if self.sync.store.connections() and any(m.get('blog_id') != values['my_blog_id'] for m in self.sync.store.connections().values()):
            messagebox.showerror('계정 확인', '내 블로그 ID를 바꾸려면 먼저 Workspace 연결을 해제하세요.')
            return False
        values.update(feed_source=SOURCES[self.source.get()], engagement_audit_recent_posts=int(self.post_count.get()),
                      assistant_mode=not self.legacy_automation.get(), append_fixed_suffix_to_ai=self.append_suffix.get(),
                      gemini_web_enabled=self.gemini_enabled.get(), gemini_browser_mode=self.gemini_browser_mode.get(),
                      pacing_enabled=self.pacing_enabled.get(), random_pause_enabled=self.random_pause_enabled.get())
        try:
            self.config_service.save(values)
        except (ConfigConflictError, OSError) as exc:
            messagebox.showerror('설정 저장 중단', str(exc))
            return False
        self.status.configure(text='설정 저장됨 · 원본 백업 보존')
        return True

    def _run_worker(self, name, action):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('다른 작업 진행 중', '중단 버튼을 누르고 현재 초안의 보존이 끝난 뒤 전환하세요.')
            return
        self.stop_event.clear()
        self.status.configure(text=name + ' 시작 중')
        def run():
            try:
                with browser_worker(): action()
            except BrowserWorkerBusy as exc:
                self.events.put(('status', str(exc)))
            except Exception as exc:
                # Do not expose provider response bodies, tokens or cookies.
                self.events.put(('status', name + ' 중단: ' + type(exc).__name__ + ' · 프로필 사용 중/로그인 상태를 확인하세요.'))
            finally:
                self.events.put(('worker_done', name))
        self.worker = threading.Thread(target=run, name='naver-browser', daemon=True)
        self.worker.start()

    def _start_feed(self):
        if not self._save_settings(): return
        manager = StateManager()
        manager.register_listener(lambda state: self.events.put(('feed', state)))
        def run():
            FeedController(self.config_service, HistoryStore(), manager, self.stop_event).run()
        self._run_worker('글 탐색', run)

    def _start_audit(self):
        if not self._save_settings(): return
        blog_id = self.config_service.get('my_blog_id', '')
        if not blog_id:
            self.tabs.set('설정')
            messagebox.showinfo('내 블로그 ID 필요', '설정에서 내 블로그 ID를 입력하세요.')
            return
        count = int(self.post_count.get())
        def run():
            session = BrowserSession(headless=False)
            try:
                session.start()
                if not NaverAuthGuard.check_login_cookies(session.context)[0]:
                    self.events.put(('status', '네이버 로그인이 필요합니다. 글 탐색 탭에서 로그인 창을 여세요.'))
                    return
                result = EngagementAuditService.run_audit(session.get_feed_page(), blog_id, count, self.stop_event, self.repository)
                self.events.put(('audit', result))
                if result.get('run_id'): self.sync.start()
            finally:
                session.close()
        self._run_worker('반응분석', run)

    def _login(self):
        def run():
            session = BrowserSession(headless=False)
            try:
                session.start()
                page = session.get_feed_page()
                page.goto('https://nid.naver.com/nidlogin.login', wait_until='domcontentloaded')
                self.events.put(('status', '브라우저에서 직접 로그인하세요. 완료하면 창을 닫거나 중단을 누르세요.'))
                while not self.stop_event.is_set() and not page.is_closed(): page.wait_for_timeout(300)
            finally:
                session.close()
        self._run_worker('네이버 로그인', run)

    def _stop_worker(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('작업 중단', '현재 작업을 중단할까요? 보조 패널 초안은 글별 로컬 보관함에 보존됩니다.'):
                return
            self.stop_event.set()
            self.status.configure(text='중단 요청됨 · 현재 작업의 정리와 저장을 기다리는 중')

    def _startup(self):
        def initialize():
            try:
                self.repository.import_legacy(ROOT / 'data')
                self.events.put(('refresh', None))
                self.sync.start()
            except Exception:
                self.events.put(('status', '기존 자료 가져오기 실패 · 원본 파일은 보존됩니다.'))
        threading.Thread(target=initialize, daemon=True).start()

    def _refresh_audit(self, report=None):
        history = self.repository.list_runs()
        report = report or next((r for r in history if r.get('source_kind') == 'live'), None)
        self.current_report = report
        if not report:
            legacy = sum(r.get('source_kind') == 'legacy_unverified' for r in history)
            self.audit_summary.configure(text='새 분석 결과 없음' + (f' · 기존 자료 {legacy}건은 검증 전 참고자료로 분리됨' if legacy else ''))
            self._render_rows()
            return
        last_complete = next((r.get('generated_at') for r in history if r.get('source_kind') == 'live' and r.get('audit_state') == 'complete'), '없음')
        self.audit_summary.configure(text=f"{STATUS.get(report.get('audit_state'), '확인 불가')} · {report.get('generated_at', '')} · 확인된 이웃 {len(report.get('master_buddies', []))}명")
        issues = report.get('quality_issues', [])
        self.audit_quality.configure(text=f"마지막 완전 수집: {last_complete}\n부분 값은 확인된 최소 횟수 · 품질 진단 {len(issues)}건 · 자세한 근거는 실행별 JSON에 보존")
        self._render_rows()

    def _render_rows(self):
        if not hasattr(self, 'tree'): return
        self.tree.delete(*self.tree.get_children())
        query = self.filter_text.get().strip().lower()
        for b in (self.current_report or {}).get('master_buddies', []):
            if query and query not in (b.get('blog_id', '') + ' ' + b.get('nickname', '')).lower(): continue
            counts = [b.get(k) for k in ('like_count', 'comment_count', 'comment_entry_count', 'engaged_post_count')]
            counts = ['확인 불가' if x is None else str(x) + ('+' if b.get('counts_are_lower_bounds') else '') for x in counts]
            self.tree.insert('', 'end', iid=b['blog_id'], values=[b.get('nickname') or b['blog_id'], {'on': 'ON', 'off': 'OFF'}.get(b.get('new_posts_setting'), '확인 불가'), *counts, b.get('reaction_status', '확인 불가')])

    def _visit_buddy(self, event=None):
        from services.audit_models import canonical_blog_id
        selected = self.tree.selection()
        if selected and canonical_blog_id(selected[0]): webbrowser.open('https://m.blog.naver.com/' + selected[0])

    def _open_exports(self):
        import subprocess
        folder = self.repository.db_path.parent / 'audit_exports'
        if folder.is_dir(): subprocess.Popen(['open', str(folder)])
        else: messagebox.showinfo('분석 기록', '분석을 실행하면 실행 ID별 JSON·CSV가 생성됩니다.')

    def _connect_google(self):
        if self.oauth_worker and self.oauth_worker.is_alive(): return
        if self.sync.store.connections():
            messagebox.showinfo('계정 연결', '계정을 다시 선택하려면 먼저 연결을 해제하세요. 기존 Google 파일은 유지됩니다.')
            return
        if not self.config_service.get('my_blog_id'):
            messagebox.showinfo('계정 연결', '설정에서 내 블로그 ID를 먼저 저장하세요.')
            self.tabs.set('설정')
            return
        path = filedialog.askopenfilename(title='Google Desktop OAuth 클라이언트 JSON 선택', filetypes=[('JSON', '*.json')])
        if not path: return
        self.workspace_status.configure(text='시스템 브라우저에서 Google 계정을 선택하세요.')
        def authorize():
            try:
                credentials = self.sync.auth.authorize(path)
                identity = GoogleWorkspaceClient(credentials, self.sync.store).identity()
                self.events.put(('oauth_consent', (identity, credentials)))
            except Exception as exc:
                code = exc.code if isinstance(exc, WorkspaceError) else type(exc).__name__
                self.events.put(('workspace', '연결 미완료: ' + code))
        self.oauth_worker = threading.Thread(target=authorize, daemon=True)
        self.oauth_worker.start()

    def _approve_google(self, payload):
        identity, credentials = payload
        text = f"계정: {identity['emailAddress']}\n폴더: 새 비공개 Naver Blog Workspace\n\n블로그 식별 정보·링크·관계 정보·반응 집계·내 새글보기·관측 시각을 전송합니다.\n댓글 원문·쿠키·로그인 정보·클립보드는 제외합니다.\n기존 분석도 검증 전 폴더에 허용 필드만 보관합니다.\n\n이 계정과 항목으로 연결할까요?"
        if not messagebox.askyesno('전송 항목 승인', text):
            self.workspace_status.configure(text='연결 취소됨 · 토큰 저장과 파일 전송 없음')
            return
        auto = messagebox.askyesno('자동 동기화', '분석이 로컬에 저장된 뒤 자동으로 동기화할까요?\n아니요를 선택하면 전송 / 재시도 버튼으로만 전송합니다.')
        account = identity['permissionId']
        try:
            self.sync.auth.save(account, credentials)
            self.sync.store.set_connection(account, {'email': identity['emailAddress'], 'blog_id': self.config_service.get('my_blog_id'), 'consented': True, 'auto_sync': auto})
            self.workspace_status.configure(text='계정 승인됨 · 비공개 Workspace 준비 중')
            self.sync.start(retry=True)
        except Exception as exc:
            code = exc.code if isinstance(exc, WorkspaceError) else type(exc).__name__
            self.workspace_status.configure(text='연결 저장 미완료: ' + code)

    def _disconnect_google(self):
        connections = self.sync.store.connections()
        if not connections: return
        if not messagebox.askyesno('Google 연결 해제', '대기 중 전송을 중단하고 Keychain의 토큰을 제거합니다. 이미 전송된 파일은 삭제하지 않습니다. 진행할까요?'): return
        for account in connections:
            try: self.sync.disconnect(account)
            except Exception:
                self.workspace_status.configure(text='대기열 중단됨 · Keychain 토큰 제거 실패: 키체인 접근에서 확인하세요.')
                return
        self.workspace_status.configure(text='연결 해제됨 · Google 파일은 보존됩니다.')

    def _open_workspace(self):
        meta = next(iter(self.sync.store.connections().values()), {})
        if meta.get('folder_id'): webbrowser.open('https://drive.google.com/drive/folders/' + meta['folder_id'])
        else: messagebox.showinfo('Workspace', 'Google 연결과 최초 폴더 생성이 완료된 뒤 열 수 있습니다.')

    def _poll(self):
        for _ in range(100):
            try: event = self.events.get_nowait()
            except queue.Empty: break
            kind, value = event
            if kind == 'feed':
                self.feed_status.configure(text=value.current_post_title or value.message)
                self.feed_detail.configure(text=value.message)
                self.status.configure(text=f'글 탐색 · {value.processed_count}/{value.total_target_count} · {value.message}')
            elif kind == 'workspace': self.workspace_status.configure(text=value)
            elif kind == 'status': self.status.configure(text=value)
            elif kind == 'oauth_consent' and not self.closing: self._approve_google(value)
            elif kind == 'audit':
                if value.get('report'): self._refresh_audit(value['report'])
                self.status.configure(text='분석 로컬 저장됨' if value.get('run_id') else '분석 미완료: ' + value.get('error', '확인 필요'))
            elif kind == 'refresh':
                self._refresh_audit()
                meta = next(iter(self.sync.store.connections().values()), {})
                if meta: self.workspace_status.configure(text=meta.get('email', '') + (' · 자동 동기화 ON' if meta.get('auto_sync') else ' · 수동 전송'))
            elif kind == 'worker_done' and self.closing:
                self.destroy()
                return
        if self.winfo_exists(): self.after(100, self._poll)

    def _close(self):
        active = self.worker and self.worker.is_alive()
        if active and not messagebox.askyesno('앱 종료', '현재 작업을 중단하고 초안을 보존한 뒤 종료할까요?'): return
        self.closing = True
        self.sync.stop()
        if active:
            self.stop_event.set()
            self.status.configure(text='초안과 실행 상태 정리 후 종료합니다.')
        else: self.destroy()
