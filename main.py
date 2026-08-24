import os
import sys
import subprocess
import json
import time
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Optional, List
import customtkinter as ctk

# naver-blog-bot 절대 경로를 sys.path에 추가하여 어디서 실행해도 모듈 로드 가능하도록 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.logger import logger
from src.browser import BrowserManager, ProfileLockManager, USER_DATA_DIR, DEBUG_PROFILE_DIR, is_cdp_ready
from src.collector import BlogCollector
from src.liker import AutoLiker
from src.commenter import AutoCommenter
from src.types import TaskStatus

CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "config.json"))
DEFAULT_FEED_URL = "https://section.blog.naver.com/BlogHome.naver?directoryNo=0&currentPage=1&groupId=0"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


def add_mac_clipboard_support(widget, root):
    """Mac 환경에서 Command+V, Command+C, Command+A 단축키 지원"""
    def paste_event(e=None):
        try:
            clipboard_text = root.clipboard_get()
            if isinstance(widget, ctk.CTkTextbox):
                widget.insert("insert", clipboard_text)
            elif isinstance(widget, ctk.CTkEntry):
                widget.insert("insert", clipboard_text)
        except Exception:
            pass
        return "break"

    def copy_event(e=None):
        try:
            if isinstance(widget, ctk.CTkTextbox):
                sel = widget.get("sel.first", "sel.last")
            elif isinstance(widget, ctk.CTkEntry):
                sel = widget.selection_get()
            root.clipboard_clear()
            root.clipboard_append(sel)
        except Exception:
            pass
        return "break"

    def select_all_event(e=None):
        try:
            if isinstance(widget, ctk.CTkTextbox):
                widget.tag_add("sel", "1.0", "end")
            elif isinstance(widget, ctk.CTkEntry):
                widget.select_range(0, "end")
        except Exception:
            pass
        return "break"

    widget.bind("<Command-v>", paste_event)
    widget.bind("<Command-V>", paste_event)
    widget.bind("<Command-c>", copy_event)
    widget.bind("<Command-C>", copy_event)
    widget.bind("<Command-a>", select_all_event)
    widget.bind("<Command-A>", select_all_event)
    widget.bind("<Control-v>", paste_event)
    widget.bind("<Control-c>", copy_event)
    widget.bind("<Control-a>", select_all_event)


class NaverBlogBotGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("네이버 블로그 오토봇 (공감 & 댓글 자동화)")
        self.geometry("950x820")

        self.is_running = False
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        # 시작 시 혹시 남아있는 락 초기화
        ProfileLockManager.release(USER_DATA_DIR)

        self.config_data = self._load_config()

        self._build_ui()
        # Tkinter Thread-safe Logging
        logger.register_gui_callback(lambda msg: self.after(0, self._append_log, msg))
        logger.log("네이버 블로그 오토봇 준비 완료.")

    def _load_config(self) -> dict:
        default_cfg = {
            "min_delay": 0.0,
            "max_delay": 8.0,
            "max_pages": 5,
            "browser_mode": "new",
            "default_keywords": ["재테크", "주식", "맛집", "일상"],
            "comment_template": "{좋은|유익한|멋진} 포스팅 잘 읽고 갑니다! {오늘도 행복한 하루 보내세요|응원합니다|감사합니다}!",
            "secret_comment": False
        }
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                    default_cfg.update(user_cfg)
            except Exception as e:
                logger.log(f"config.json 로드 실패, 기본값을 사용합니다: {e}", "WARNING")
        return default_cfg

    def _build_ui(self):
        # Header Title
        title_frame = ctk.CTkFrame(self)
        title_frame.pack(fill="x", padx=15, pady=(10, 4))
        
        title_label = ctk.CTkLabel(
            title_frame,
            text="🤖 Naver Blog Auto Bot (공감 1번 기능 & 댓글 2번 기능)",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_label.pack(pady=6)

        # Tabview
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=4)

        self.tab_like = self.tabview.add("❤️ 1번 기능: 공감(하트) 자동화")
        self.tab_comment = self.tabview.add("💬 2번 기능: 댓글 자동화")
        self.tab_login = self.tabview.add("🔑 브라우저/세션 연결 관리")

        self._build_like_tab()
        self._build_comment_tab()
        self._build_login_tab()

        # Log Output Frame
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=15, pady=(4, 10))

        log_label_frame = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_label_frame.pack(fill="x", padx=5, pady=2)

        ctk.CTkLabel(log_label_frame, text="📋 실시간 작업 로그", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(log_label_frame, text="로그 지우기", width=80, height=24, command=self._clear_log).pack(side="right")

        self.log_textbox = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Courier", size=12))
        self.log_textbox.pack(fill="both", expand=True, padx=5, pady=4)
        add_mac_clipboard_support(self.log_textbox, self)

    def _build_like_tab(self):
        frame = self.tab_like

        info_lbl = ctk.CTkLabel(
            frame,
            text="블로그홈/피드 스크롤 중 공감(하트) 자동 클릭 + 2페이지/다음 그룹 페이징 자동 이동 + 0~8초 난수 지연",
            font=ctk.CTkFont(size=12),
            text_color="#66BB6A"
        )
        info_lbl.pack(pady=(2, 4))

        # Target Type Selection
        target_type_frame = ctk.CTkFrame(frame)
        target_type_frame.pack(fill="x", padx=10, pady=3)

        self.like_target_mode = ctk.StringVar(value="feed")
        ctk.CTkLabel(target_type_frame, text="작업 모드:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=4, sticky="w")
        
        ctk.CTkRadioButton(
            target_type_frame, text="블로그 홈 / 이웃새글 피드",
            variable=self.like_target_mode, value="feed", command=self._on_like_mode_change
        ).grid(row=0, column=1, padx=8, pady=4)

        ctk.CTkRadioButton(
            target_type_frame, text="URL 직접 입력 (줄바꿈 지원)",
            variable=self.like_target_mode, value="url", command=self._on_like_mode_change
        ).grid(row=0, column=2, padx=8, pady=4)

        # Feed URL Entry Frame
        self.like_feed_frame = ctk.CTkFrame(frame)
        self.like_feed_frame.pack(fill="x", padx=10, pady=3)

        ctk.CTkLabel(self.like_feed_frame, text="피드 URL 주소:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.like_feed_entry = ctk.CTkEntry(self.like_feed_frame, font=ctk.CTkFont(size=12))
        self.like_feed_entry.pack(fill="x", padx=10, pady=3)
        self.like_feed_entry.insert(0, DEFAULT_FEED_URL)
        add_mac_clipboard_support(self.like_feed_entry, self)

        preset_btn_box = ctk.CTkFrame(self.like_feed_frame, fg_color="transparent")
        preset_btn_box.pack(fill="x", padx=10, pady=(0, 4))
        
        ctk.CTkButton(
            preset_btn_box, text="블로그 홈 피드", width=100, height=24,
            command=lambda: self._set_like_feed_url(DEFAULT_FEED_URL)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            preset_btn_box, text="이웃 새글 피드", width=100, height=24,
            command=lambda: self._set_like_feed_url("https://section.blog.naver.com/BlogHome.naver?directoryNo=0&currentPage=1&groupId=1")
        ).pack(side="left", padx=2)

        # Multi-URL Textbox Frame (Hidden by default)
        self.like_multi_url_frame = ctk.CTkFrame(frame)
        ctk.CTkLabel(self.like_multi_url_frame, text="대상 URL 목록 (여러 줄 입력):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.like_multi_url_textbox = ctk.CTkTextbox(self.like_multi_url_frame, height=65, font=ctk.CTkFont(size=12))
        self.like_multi_url_textbox.pack(fill="x", padx=10, pady=3)
        add_mac_clipboard_support(self.like_multi_url_textbox, self)

        # Browser Mode Radio (Default: new - 오토봇 전용 세션 브라우저)
        browser_opt_frame = ctk.CTkFrame(frame)
        browser_opt_frame.pack(fill="x", padx=10, pady=3)

        self.browser_mode_var = ctk.StringVar(value=self.config_data.get("browser_mode", "new"))
        ctk.CTkLabel(browser_opt_frame, text="브라우저 모드:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=4, sticky="w")
        
        ctk.CTkRadioButton(
            browser_opt_frame, text="오토봇 전용 세션 브라우저 (기본/권장)",
            variable=self.browser_mode_var, value="new"
        ).grid(row=0, column=1, padx=8, pady=4, sticky="w")
        
        ctk.CTkRadioButton(
            browser_opt_frame, text="이미 켜져 있는 크롬 탭 연동 (포트 9222)",
            variable=self.browser_mode_var, value="existing"
        ).grid(row=0, column=2, padx=8, pady=4, sticky="w")

        # Options: Delays, Max Pages
        opt_frame = ctk.CTkFrame(frame)
        opt_frame.pack(fill="x", padx=10, pady=3)

        ctk.CTkLabel(opt_frame, text="최대 페이지:").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.like_max_pages_entry = ctk.CTkEntry(opt_frame, width=50)
        self.like_max_pages_entry.grid(row=0, column=1, padx=2, pady=4)
        self.like_max_pages_entry.insert(0, str(self.config_data.get("max_pages", 5)))
        add_mac_clipboard_support(self.like_max_pages_entry, self)

        ctk.CTkLabel(opt_frame, text="난수 지연(초):").grid(row=0, column=2, padx=6, pady=4, sticky="w")
        self.like_min_delay = ctk.CTkEntry(opt_frame, width=45)
        self.like_min_delay.grid(row=0, column=3, padx=2, pady=4)
        self.like_min_delay.insert(0, str(self.config_data.get("min_delay", 0.0)))
        add_mac_clipboard_support(self.like_min_delay, self)

        ctk.CTkLabel(opt_frame, text="~").grid(row=0, column=4, padx=2, pady=4)
        self.like_max_delay = ctk.CTkEntry(opt_frame, width=45)
        self.like_max_delay.grid(row=0, column=5, padx=2, pady=4)
        self.like_max_delay.insert(0, str(self.config_data.get("max_delay", 8.0)))
        add_mac_clipboard_support(self.like_max_delay, self)

        # Action Buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=6)

        self.btn_like_start = ctk.CTkButton(
            btn_frame, text="▶ 1번 공감 자동화 시작", fg_color="#2E7D32", hover_color="#1B5E20", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._start_like_task
        )
        self.btn_like_start.pack(side="left", expand=True, fill="x", padx=5)

        self.btn_like_stop = ctk.CTkButton(
            btn_frame, text="⏹ 즉시 작업 중지", fg_color="#C62828", hover_color="#8E0000", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._stop_task, state="disabled"
        )
        self.btn_like_stop.pack(side="right", expand=True, fill="x", padx=5)

    def _on_like_mode_change(self):
        mode = self.like_target_mode.get()
        if mode == "feed":
            self.like_multi_url_frame.pack_forget()
            self.like_feed_frame.pack(fill="x", padx=10, pady=3, after=self.tab_like.winfo_children()[1])
        else:
            self.like_feed_frame.pack_forget()
            self.like_multi_url_frame.pack(fill="x", padx=10, pady=3, after=self.tab_like.winfo_children()[1])

    def _set_like_feed_url(self, url: str):
        self.like_feed_entry.delete(0, "end")
        self.like_feed_entry.insert(0, url)

    def _build_comment_tab(self):
        frame = self.tab_comment

        info_lbl = ctk.CTkLabel(
            frame,
            text="네이버 블로그 댓글 자동 작성 (Spintax 문구 다양화, 비밀댓글 옵션, 중복 방지, 0~8초 난수 지연)",
            font=ctk.CTkFont(size=12),
            text_color="#42A5F5"
        )
        info_lbl.pack(pady=(2, 4))

        # Mode Selection
        mode_frame = ctk.CTkFrame(frame)
        mode_frame.pack(fill="x", padx=10, pady=3)

        ctk.CTkLabel(mode_frame, text="작성 대상:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=4, sticky="w")
        self.cmt_target_type = ctk.StringVar(value="keyword")
        
        ctk.CTkRadioButton(
            mode_frame, text="키워드 검색 자동 수집",
            variable=self.cmt_target_type, value="keyword", command=self._on_cmt_mode_change
        ).grid(row=0, column=1, padx=8, pady=4)
        
        ctk.CTkRadioButton(
            mode_frame, text="특정 URL 직접 입력(줄바꿈 지원)",
            variable=self.cmt_target_type, value="url", command=self._on_cmt_mode_change
        ).grid(row=0, column=2, padx=8, pady=4)

        # Keyword Entry Box
        self.cmt_kw_frame = ctk.CTkFrame(frame)
        self.cmt_kw_frame.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(self.cmt_kw_frame, text="검색 키워드 입력:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.cmt_kw_entry = ctk.CTkEntry(self.cmt_kw_frame, font=ctk.CTkFont(size=12))
        self.cmt_kw_entry.pack(fill="x", padx=10, pady=3)
        self.cmt_kw_entry.insert(0, "재테크")
        add_mac_clipboard_support(self.cmt_kw_entry, self)

        # Multi-URL Textbox for Comment
        self.cmt_url_frame = ctk.CTkFrame(frame)
        ctk.CTkLabel(self.cmt_url_frame, text="대상 URL 목록 (여러 줄 입력):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.cmt_url_textbox = ctk.CTkTextbox(self.cmt_url_frame, height=55, font=ctk.CTkFont(size=12))
        self.cmt_url_textbox.pack(fill="x", padx=10, pady=3)
        add_mac_clipboard_support(self.cmt_url_textbox, self)

        # Comment Template
        tmpl_frame = ctk.CTkFrame(frame)
        tmpl_frame.pack(fill="x", padx=10, pady=3)

        ctk.CTkLabel(tmpl_frame, text="댓글 문구 템플릿 (Spintax {A|B} 지원):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.cmt_text_box = ctk.CTkTextbox(tmpl_frame, height=55, font=ctk.CTkFont(size=12))
        self.cmt_text_box.pack(fill="x", padx=10, pady=3)
        self.cmt_text_box.insert("1.0", self.config_data.get("comment_template", "{좋은|유익한|멋진} 포스팅 잘 읽고 갑니다! {오늘도 행복한 하루 보내세요|응원합니다|감사합니다}!"))
        add_mac_clipboard_support(self.cmt_text_box, self)

        # Options
        opt_frame = ctk.CTkFrame(frame)
        opt_frame.pack(fill="x", padx=10, pady=3)

        self.secret_cmt_var = ctk.BooleanVar(value=self.config_data.get("secret_comment", False))
        ctk.CTkCheckBox(opt_frame, text="비밀댓글 작성", variable=self.secret_cmt_var).grid(row=0, column=0, padx=8, pady=4)

        ctk.CTkLabel(opt_frame, text="난수 지연(초):").grid(row=0, column=1, padx=8, pady=4, sticky="w")
        self.cmt_min_delay = ctk.CTkEntry(opt_frame, width=45)
        self.cmt_min_delay.grid(row=0, column=2, padx=2, pady=4)
        self.cmt_min_delay.insert(0, str(self.config_data.get("min_delay", 0.0)))
        add_mac_clipboard_support(self.cmt_min_delay, self)

        ctk.CTkLabel(opt_frame, text="~").grid(row=0, column=3, padx=2, pady=4)
        self.cmt_max_delay = ctk.CTkEntry(opt_frame, width=45)
        self.cmt_max_delay.grid(row=0, column=4, padx=2, pady=4)
        self.cmt_max_delay.insert(0, str(self.config_data.get("max_delay", 8.0)))
        add_mac_clipboard_support(self.cmt_max_delay, self)

        # Buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=6)

        self.btn_cmt_start = ctk.CTkButton(
            btn_frame, text="▶ 2번 댓글 자동화 시작", fg_color="#1976D2", hover_color="#0D47A1", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._start_comment_task
        )
        self.btn_cmt_start.pack(side="left", expand=True, fill="x", padx=5)

        self.btn_cmt_stop = ctk.CTkButton(
            btn_frame, text="⏹ 즉시 작업 중지", fg_color="#C62828", hover_color="#8E0000", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._stop_task, state="disabled"
        )
        self.btn_cmt_stop.pack(side="right", expand=True, fill="x", padx=5)

    def _on_cmt_mode_change(self):
        mode = self.cmt_target_type.get()
        if mode == "keyword":
            self.cmt_url_frame.pack_forget()
            self.cmt_kw_frame.pack(fill="x", padx=10, pady=3, after=self.tab_comment.winfo_children()[1])
        else:
            self.cmt_kw_frame.pack_forget()
            self.cmt_url_frame.pack(fill="x", padx=10, pady=3, after=self.tab_comment.winfo_children()[1])

    def _build_login_tab(self):
        frame = self.tab_login

        info_box = ctk.CTkTextbox(frame, height=130)
        info_box.pack(fill="x", padx=15, pady=10)
        info_box.insert("1.0", 
            "💡 [네이버 로그인 및 세션 관리 안내]\n\n"
            "방식 1. [오토봇 전용 세션 브라우저 (기본/권장)]\n"
            "  ① 아래 [🌐 로그인 전용 브라우저 열기] 버튼 클릭 후 네이버 로그인 진행\n"
            "  ② 로그인이 완료되면 브라우저 창을 닫아주세요. (세션이 data/user_profile에 영구 저장됨)\n"
            "  ③ 이후 1번/2번 자동화 실행 시 저장된 세션을 사용하여 캡차 없이 안전하게 실행됩니다.\n\n"
            "방식 2. [이미 켜져 있는 크롬 브라우저 탭 활용 (CDP 9222 포트)]\n"
            "  ① 아래 [🚀 9222 포트로 크롬 실행] 버튼 클릭\n"
            "  ② 열린 크롬에서 네이버 로그인 및 블로그 피드를 열어둔 뒤 1번 기능 실행"
        )
        info_box.configure(state="disabled")

        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.pack(pady=10)

        ctk.CTkButton(
            btn_box,
            text="🌐 로그인 전용 브라우저 열기 (권장: 세션 1회 저장)",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            command=self._open_login_browser
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            btn_box,
            text="🚀 9222 포트로 크롬 열기 (CDP)",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            fg_color="#00838F", hover_color="#006064",
            command=self._launch_debug_chrome
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            btn_box,
            text="🔓 프로필 락 초기화",
            font=ctk.CTkFont(size=12),
            height=38,
            fg_color="#455A64", hover_color="#263238",
            command=self._reset_profile_lock
        ).pack(side="left", padx=6)

    def _reset_profile_lock(self):
        ProfileLockManager.release(USER_DATA_DIR)
        logger.log("🔓 프로필 락이 초기화되었습니다. 다시 작업을 시작할 수 있습니다.")
        messagebox.showinfo("알림", "프로필 락이 초기화되었습니다.")

    def _launch_debug_chrome(self):
        def task():
            os.makedirs(DEBUG_PROFILE_DIR, exist_ok=True)
            logger.log("디버깅 포트(9222) 전용 크롬 브라우저를 실행합니다...")
            cmd = [
                "open", "-na", "Google Chrome", "--args",
                "--remote-debugging-port=9222",
                f"--user-data-dir={DEBUG_PROFILE_DIR}",
                DEFAULT_FEED_URL
            ]
            try:
                subprocess.Popen(cmd)
                ready = False
                for _ in range(10):
                    time.sleep(0.5)
                    if is_cdp_ready():
                        ready = True
                        break

                if ready:
                    logger.log("✅ 9222 CDP 포트가 정상 오픈되었습니다! (기존 탭 연동 준비 완료)")
                else:
                    logger.log("⚠️ 크롬 창이 열렸으나 9222 포트 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.", "WARNING")

            except Exception as e:
                logger.log(f"크롬 실행 실패: {e}", "ERROR")

        threading.Thread(target=task, daemon=True).start()

    def _open_login_browser(self):
        """Worker Thread 내에서 로그인 브라우저 시작 및 창 닫힘 정확 감지"""
        if ProfileLockManager.is_locked(USER_DATA_DIR):
            messagebox.showwarning("알림", "프로필이 이미 다른 작업에서 사용 중입니다.\n[🔓 프로필 락 초기화]를 누르시거나 기존 작업을 닫아주세요.")
            return

        def task():
            logger.log("로그인용 브라우저를 시작합니다...")
            mgr = BrowserManager(headless=False, use_cdp=False)
            try:
                context = mgr.start()
                page = context.new_page()
                page.goto("https://nid.naver.com/nidlogin.login")
                logger.log("💡 브라우저에서 로그인을 완료하신 뒤 창을 닫아주시면 세션이 저장됩니다.")

                # 창 닫힘 정확 감지
                while True:
                    try:
                        if not context.pages or all(p.is_closed() for p in context.pages):
                            break
                    except Exception:
                        break
                    time.sleep(0.5)

                logger.log("✅ 로그인 브라우저가 닫혔습니다. 세션 정보가 안전하게 저장되었습니다.")

            except Exception as e:
                logger.log(f"로그인 브라우저 오류: {e}", "ERROR")
            finally:
                mgr.close()

        threading.Thread(target=task, daemon=True).start()

    def _append_log(self, msg: str):
        self.log_textbox.insert("end", msg + "\n")
        self.log_textbox.see("end")

    def _clear_log(self):
        self.log_textbox.delete("1.0", "end")

    def _validate_numeric(self, pages_str: str, min_d_str: str, max_d_str: str):
        try:
            pages = int(pages_str.strip())
            min_d = float(min_d_str.strip())
            max_d = float(max_d_str.strip())
        except ValueError:
            return None, "페이지 수와 지연시간은 유효한 숫자여야 합니다."

        if not (1 <= pages <= 100):
            return None, "최대 페이지 수는 1 이상 100 이하로 입력해 주세요."
        if not (0.0 <= min_d <= 3600.0) or not (0.0 <= max_d <= 3600.0):
            return None, "지연시간은 0초 이상 3600초 이하로 입력해 주세요."
        if min_d > max_d:
            return None, "최소 지연시간이 최대 지연시간보다 클 수 없습니다."

        return (pages, min_d, max_d), None

    def _start_like_task(self):
        if self.is_running:
            messagebox.showwarning("경고", "이미 작업이 실행 중입니다.")
            return

        browser_mode = self.browser_mode_var.get()
        if browser_mode == "existing" and not is_cdp_ready():
            messagebox.showwarning(
                "브라우저 연결 오류",
                "9222 포트의 Chrome이 준비되지 않았습니다.\n\n"
                "1. [🔑 세션/연결 관리] 탭에서 '9222 포트로 크롬 열기'를 먼저 실행하시거나,\n"
                "2. '오토봇 전용 세션 브라우저' 모드를 선택해 주세요."
            )
            return

        mode = self.like_target_mode.get()
        if mode == "feed":
            target_input = self.like_feed_entry.get().strip()
        else:
            target_input = self.like_multi_url_textbox.get("1.0", "end-1c").strip()

        if not target_input:
            messagebox.showwarning("경고", "대상 URL을 1개 이상 입력해 주세요.")
            return

        vals, err = self._validate_numeric(
            self.like_max_pages_entry.get(),
            self.like_min_delay.get(),
            self.like_max_delay.get()
        )
        if err:
            messagebox.showwarning("입력 오류", err)
            return

        max_pages, min_delay, max_delay = vals

        self.is_running = True
        self.stop_event.clear()
        self._set_button_states(running=True)

        self.worker_thread = threading.Thread(
            target=self._run_like_worker,
            args=(target_input, max_pages, min_delay, max_delay, browser_mode),
            daemon=True
        )
        self.worker_thread.start()

    def _run_like_worker(self, target_input: str, max_pages: int, min_delay: float, max_delay: float, browser_mode: str):
        urls = [u.strip() for u in target_input.splitlines() if u.strip()]
        logger.log("==========================================")
        logger.log(f"[1번 기능] 공감(하트) 자동화 작업 시작 (총 {len(urls)}개 대상)")

        use_cdp = (browser_mode == "existing")
        mgr = BrowserManager(headless=False, use_cdp=use_cdp)
        final_status = TaskStatus.COMPLETED
        total_liked = 0

        try:
            context = mgr.start()
            page = mgr.find_or_create_page("blog.naver.com")
            liker = AutoLiker(min_delay=min_delay, max_delay=max_delay, stop_event=self.stop_event)

            for idx, target_url in enumerate(urls, 1):
                if self.stop_event.is_set():
                    final_status = TaskStatus.STOPPED
                    break

                logger.log(f"[{idx}/{len(urls)}] 대상 처리 시작: {target_url}")
                liked, status = liker.process_url_for_likes(page, target_url, max_pages=max_pages)
                total_liked += liked

                if status == TaskStatus.STOPPED or self.stop_event.is_set():
                    final_status = TaskStatus.STOPPED
                    break

            if final_status == TaskStatus.COMPLETED:
                logger.log(f"✅ [1번 기능] 작업 완료! (총 누적 공감: {total_liked}개)")
            elif final_status == TaskStatus.STOPPED:
                logger.log(f"⏹ [1번 기능] 사용자 요청으로 작업 중지. (처리된 공감: {total_liked}개)", "WARNING")

        except Exception as e:
            final_status = TaskStatus.FAILED
            logger.log(f"❌ [1번 기능] 작업 실패: {e}", "ERROR")

        finally:
            mgr.close()
            self.is_running = False
            self.stop_event.clear()
            self.after(0, lambda: self._set_button_states(running=False))

    def _start_comment_task(self):
        if self.is_running:
            messagebox.showwarning("경고", "이미 작업이 실행 중입니다.")
            return

        mode = self.cmt_target_type.get()
        if mode == "keyword":
            target_input = self.cmt_kw_entry.get().strip()
        else:
            target_input = self.cmt_url_textbox.get("1.0", "end-1c").strip()

        cmt_template = self.cmt_text_box.get("1.0", "end-1c").strip()

        if not target_input or not cmt_template:
            messagebox.showwarning("경고", "대상 및 댓글 문구를 입력해 주세요.")
            return

        vals, err = self._validate_numeric("1", self.cmt_min_delay.get(), self.cmt_max_delay.get())
        if err:
            messagebox.showwarning("입력 오류", err)
            return

        _, min_delay, max_delay = vals
        secret_comment = self.secret_cmt_var.get()

        self.is_running = True
        self.stop_event.clear()
        self._set_button_states(running=True)

        self.worker_thread = threading.Thread(
            target=self._run_comment_worker,
            args=(mode, target_input, cmt_template, secret_comment, min_delay, max_delay),
            daemon=True
        )
        self.worker_thread.start()

    def _run_comment_worker(self, target_type: str, target_input: str, cmt_template: str, secret_comment: bool, min_delay: float, max_delay: float):
        logger.log("==========================================")
        logger.log("[2번 기능] 댓글 자동 작성 작업 시작")

        mgr = BrowserManager(headless=False, use_cdp=False)
        final_status = TaskStatus.COMPLETED

        try:
            context = mgr.start()
            page = context.new_page()
            commenter = AutoCommenter(min_delay=min_delay, max_delay=max_delay, stop_event=self.stop_event)

            if target_type == "keyword":
                targets = BlogCollector.search_blog_posts(page, target_input, max_count=10)
                for idx, t in enumerate(targets, 1):
                    if self.stop_event.is_set():
                        final_status = TaskStatus.STOPPED
                        break
                    logger.log(f"[{idx}/{len(targets)}] '{t['title']}' 댓글 작성 중...")
                    _, status = commenter.post_comment(page, t['url'], cmt_template, secret_comment=secret_comment)
                    if status == TaskStatus.STOPPED:
                        final_status = TaskStatus.STOPPED
                        break
            else:
                urls = [u.strip() for u in target_input.splitlines() if u.strip()]
                for idx, url in enumerate(urls, 1):
                    if self.stop_event.is_set():
                        final_status = TaskStatus.STOPPED
                        break
                    logger.log(f"[{idx}/{len(urls)}] URL 댓글 작성 중: {url}")
                    _, status = commenter.post_comment(page, url, cmt_template, secret_comment=secret_comment)
                    if status == TaskStatus.STOPPED:
                        final_status = TaskStatus.STOPPED
                        break

            if final_status == TaskStatus.COMPLETED:
                logger.log("✅ [2번 기능] 댓글 자동 작성 작업 완료!")
            elif final_status == TaskStatus.STOPPED:
                logger.log("⏹ [2번 기능] 사용자 요청으로 작업 중지.", "WARNING")

        except Exception as e:
            final_status = TaskStatus.FAILED
            logger.log(f"❌ [2번 기능] 댓글 작업 실패: {e}", "ERROR")

        finally:
            mgr.close()
            self.is_running = False
            self.stop_event.clear()
            self.after(0, lambda: self._set_button_states(running=False))

    def _stop_task(self):
        if self.is_running:
            self.stop_event.set()
            logger.log("⏹ 중지 신호 전송됨: 진행 중인 단계가 완료되는 즉시 중단합니다...", "WARNING")

    def _set_button_states(self, running: bool):
        state_start = "disabled" if running else "normal"
        state_stop = "normal" if running else "disabled"

        self.btn_like_start.configure(state=state_start)
        self.btn_like_stop.configure(state=state_stop)
        self.btn_cmt_start.configure(state=state_start)
        self.btn_cmt_stop.configure(state=state_stop)


if __name__ == "__main__":
    app = NaverBlogBotGUI()
    app.mainloop()
