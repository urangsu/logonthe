import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Optional
import customtkinter as ctk

from app.models import FeedSourceType, UserAction
from app.state import StateManager, BotRuntimeState, FeedState
from app.controller import FeedController
from services.config import ConfigService
from services.history import HistoryStore
from browser.session import ProfileLockManager, BrowserSession, USER_DATA_DIR
from src.logger import logger

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


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("네이버 피드 어시스턴트 (Naver Feed Assistant)")
        self.geometry("980x880")
        self.minsize(850, 750)

        self.config_service = ConfigService()
        self.history_store = HistoryStore()
        self.state_mgr = StateManager()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        # 시작 시 혹시 남아있는 락 초기화
        ProfileLockManager.release(USER_DATA_DIR)

        self._build_ui()

        # State 및 Logger 리스너 등록
        self.state_mgr.register_listener(lambda s: self.after(0, self._update_ui_state, s))
        logger.register_gui_callback(lambda msg: self.after(0, self._append_log, msg))

        logger.log("네이버 피드 어시스턴트(Feed Assistant) 준비 완료.")

    def _build_ui(self):
        # 1. Header Frame
        header = ctk.CTkFrame(self)
        header.pack(fill="x", padx=15, pady=(10, 5))

        title_lbl = ctk.CTkLabel(
            header,
            text="📱 NAVER FEED ASSISTANT",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_lbl.pack(side="left", padx=15, pady=8)

        subtitle_lbl = ctk.CTkLabel(
            header,
            text="Human-in-the-loop 모바일 피드 순회 · 공감 · 초안 자동입력 · Enter 승인",
            font=ctk.CTkFont(size=12),
            text_color="#81C784"
        )
        subtitle_lbl.pack(side="left", padx=5, pady=8)

        # 2. Main Config Card
        cfg_card = ctk.CTkFrame(self)
        cfg_card.pack(fill="x", padx=15, pady=5)

        # Source Selection
        src_frame = ctk.CTkFrame(cfg_card, fg_color="transparent")
        src_frame.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(src_frame, text="피드 대상:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.source_var = ctk.StringVar(value=self.config_service.get("feed_source", FeedSourceType.NEIGHBOR.value))

        ctk.CTkRadioButton(
            src_frame, text="이웃 새글 피드 (FeedList)",
            variable=self.source_var, value=FeedSourceType.NEIGHBOR.value, command=self._on_source_change
        ).grid(row=0, column=1, padx=8, pady=4)

        ctk.CTkRadioButton(
            src_frame, text="탐색 추천 피드 (Recommendation)",
            variable=self.source_var, value=FeedSourceType.RECOMMENDATION.value, command=self._on_source_change
        ).grid(row=0, column=2, padx=8, pady=4)

        ctk.CTkRadioButton(
            src_frame, text="URL 직접 입력 목록",
            variable=self.source_var, value=FeedSourceType.DIRECT.value, command=self._on_source_change
        ).grid(row=0, column=3, padx=8, pady=4)

        # Direct URLs Box (Hidden by default)
        self.direct_url_frame = ctk.CTkFrame(cfg_card)
        ctk.CTkLabel(self.direct_url_frame, text="대상 URL 목록 (여러 줄 입력):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
        self.direct_url_textbox = ctk.CTkTextbox(self.direct_url_frame, height=60, font=ctk.CTkFont(size=12))
        self.direct_url_textbox.pack(fill="x", padx=10, pady=3)
        add_mac_clipboard_support(self.direct_url_textbox, self)

        # Operation Options & Limits
        opt_frame = ctk.CTkFrame(cfg_card, fg_color="transparent")
        opt_frame.pack(fill="x", padx=12, pady=4)

        self.like_enabled_var = ctk.BooleanVar(value=self.config_service.get("like_enabled", True))
        ctk.CTkCheckBox(opt_frame, text="공감(하트) 자동 처리", variable=self.like_enabled_var).pack(side="left", padx=8)

        self.comment_enabled_var = ctk.BooleanVar(value=self.config_service.get("comment_enabled", True))
        ctk.CTkCheckBox(opt_frame, text="댓글 초안 자동 입력", variable=self.comment_enabled_var).pack(side="left", padx=8)

        self.secret_comment_var = ctk.BooleanVar(value=self.config_service.get("secret_comment", False))
        ctk.CTkCheckBox(opt_frame, text="비밀댓글", variable=self.secret_comment_var).pack(side="left", padx=8)

        ctk.CTkLabel(opt_frame, text="최대 처리 글 수:").pack(side="left", padx=(20, 4))
        self.max_items_entry = ctk.CTkEntry(opt_frame, width=55)
        self.max_items_entry.pack(side="left", padx=2)
        self.max_items_entry.insert(0, str(self.config_service.get("max_feed_items", 20)))
        add_mac_clipboard_support(self.max_items_entry, self)

        # Template & Fixed Suffix
        tmpl_frame = ctk.CTkFrame(cfg_card)
        tmpl_frame.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(tmpl_frame, text="댓글 기본 문구 (Spintax {A|B} 지원):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(2, 0))
        self.tmpl_textbox = ctk.CTkTextbox(tmpl_frame, height=45, font=ctk.CTkFont(size=12))
        self.tmpl_textbox.pack(fill="x", padx=8, pady=2)
        self.tmpl_textbox.insert("1.0", self.config_service.get("comment_template", "{좋은|유익한|멋진} 포스팅 잘 읽었습니다!"))
        add_mac_clipboard_support(self.tmpl_textbox, self)

        ctk.CTkLabel(tmpl_frame, text="고정 끝말 (문구 뒤에 항상 붙는 맺음말):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(4, 0))
        self.suffix_entry = ctk.CTkEntry(tmpl_frame, font=ctk.CTkFont(size=12))
        self.suffix_entry.pack(fill="x", padx=8, pady=(2, 6))
        self.suffix_entry.insert(0, self.config_service.get("fixed_suffix", "오늘도 좋은 하루 보내세요 :)"))
        add_mac_clipboard_support(self.suffix_entry, self)

        # UX Shortcut Guide
        guide_box = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=6)
        guide_box.pack(fill="x", padx=15, pady=4)

        guide_text = "⌨️ [댓글 승인 단축키]  Enter = 최종 등록  |  Shift+Enter = 줄바꿈  |  Esc = 이번 글 건너뛰기"
        ctk.CTkLabel(guide_box, text=guide_text, font=ctk.CTkFont(size=12, weight="bold"), text_color="#38BDF8").pack(pady=5)

        # 3. Status Dashboard Frame
        dash_frame = ctk.CTkFrame(self)
        dash_frame.pack(fill="x", padx=15, pady=4)

        self.status_msg_lbl = ctk.CTkLabel(dash_frame, text="대기 중", font=ctk.CTkFont(size=13, weight="bold"), text_color="#FBBF24")
        self.status_msg_lbl.pack(side="left", padx=12, pady=6)

        self.badge_lbl = ctk.CTkLabel(
            dash_frame,
            text="처리: 0/0 | ❤️ 공감: 0 | 💬 댓글: 0 | ⏭️ 건너뜀: 0",
            font=ctk.CTkFont(size=12)
        )
        self.badge_lbl.pack(side="right", padx=12, pady=6)

        # 4. Action Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=6)

        self.btn_start = ctk.CTkButton(
            btn_frame, text="▶ 피드 작업 시작", fg_color="#16A34A", hover_color="#15803D", height=38,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._start_task
        )
        self.btn_start.pack(side="left", expand=True, fill="x", padx=4)

        self.btn_stop = ctk.CTkButton(
            btn_frame, text="⏹ 즉시 작업 중지", fg_color="#DC2626", hover_color="#B91C1C", height=38,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._stop_task, state="disabled"
        )
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=4)

        ctk.CTkButton(
            btn_frame, text="🌐 로그인 창 열기", width=120, height=38,
            command=self._open_login_window
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="🔓 락 초기화", width=90, height=38,
            fg_color="#475569", hover_color="#334155",
            command=self._reset_lock
        ).pack(side="left", padx=4)

        # 5. Log Console
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=15, pady=(4, 12))

        log_head = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_head.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(log_head, text="📋 실시간 작업 로그", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(log_head, text="로그 지우기", width=75, height=22, command=self._clear_log).pack(side="right")

        self.log_textbox = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Courier", size=12))
        self.log_textbox.pack(fill="both", expand=True, padx=6, pady=4)
        add_mac_clipboard_support(self.log_textbox, self)

    def _on_source_change(self):
        if self.source_var.get() == FeedSourceType.DIRECT.value:
            self.direct_url_frame.pack(fill="x", padx=12, pady=4)
        else:
            self.direct_url_frame.pack_forget()

    def _update_ui_state(self, state: BotRuntimeState):
        self.status_msg_lbl.configure(text=f"상태: {state.message}")
        self.badge_lbl.configure(
            text=f"처리: {state.processed_count}/{state.total_target_count} | ❤️ 공감: {state.likes_count} | 💬 댓글: {state.comments_count} | ⏭️ 건너뜀: {state.skipped_count}"
        )

    def _append_log(self, msg: str):
        self.log_textbox.insert("end", msg + "\n")
        self.log_textbox.see("end")

    def _clear_log(self):
        self.log_textbox.delete("1.0", "end")

    def _reset_lock(self):
        ProfileLockManager.release(USER_DATA_DIR)
        logger.log("🔓 프로필 락이 초기화되었습니다.")
        messagebox.showinfo("알림", "프로필 락이 초기화되었습니다.")

    def _open_login_window(self):
        if ProfileLockManager.is_locked(USER_DATA_DIR):
            messagebox.showwarning("알림", "프로필이 이미 사용 중입니다. 락 초기화 후 다시 시도해 주세요.")
            return

        def task():
            logger.log("로그인용 브라우저를 시작합니다...")
            session = BrowserSession(headless=False)
            try:
                ctx = session.start()
                page = ctx.new_page()
                page.goto("https://nid.naver.com/nidlogin.login")
                logger.log("💡 브라우저에서 네이버 로그인 완료 후 창을 닫아주시면 세션이 저장됩니다.")

                while True:
                    try:
                        if not ctx.pages or all(p.is_closed() for p in ctx.pages):
                            break
                    except Exception:
                        break
                    import time
                    time.sleep(0.5)

                logger.log("✅ 로그인 브라우저가 닫혔습니다. 세션이 저장되었습니다.")
            except Exception as e:
                logger.log(f"로그인 브라우저 오류: {e}", "ERROR")
            finally:
                session.close()

        threading.Thread(target=task, daemon=True).start()

    def _start_task(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("경고", "이미 작업이 실행 중입니다.")
            return

        # 입력값 수집 및 검증
        try:
            max_items = int(self.max_items_entry.get().strip())
            if max_items < 1 or max_items > 500:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("입력 오류", "최대 처리 글 수는 1 이상 500 이하의 숫자여야 합니다.")
            return

        source_val = self.source_var.get()
        direct_urls = []
        if source_val == FeedSourceType.DIRECT.value:
            raw_text = self.direct_url_textbox.get("1.0", "end-1c").strip()
            direct_urls = [u.strip() for u in raw_text.splitlines() if u.strip()]
            if not direct_urls:
                messagebox.showwarning("입력 오류", "URL 직접 입력 목록을 1개 이상 입력해 주세요.")
                return

        # Config 저장
        cfg_data = {
            "feed_source": source_val,
            "max_feed_items": max_items,
            "like_enabled": self.like_enabled_var.get(),
            "comment_enabled": self.comment_enabled_var.get(),
            "comment_template": self.tmpl_textbox.get("1.0", "end-1c").strip(),
            "fixed_suffix": self.suffix_entry.get().strip(),
            "secret_comment": self.secret_comment_var.get(),
            "direct_urls": direct_urls
        }
        self.config_service.save(cfg_data)

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.stop_event.clear()

        controller = FeedController(
            config=self.config_service,
            history=self.history_store,
            state_mgr=self.state_mgr,
            stop_event=self.stop_event
        )

        def worker():
            try:
                controller.run()
            finally:
                self.after(0, self._on_task_finished)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop_task(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            logger.log("⏹ 작업 중지 신호 전송됨: 현재 단계 완료 즉시 안전하게 종료합니다...", "WARNING")

    def _on_task_finished(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
