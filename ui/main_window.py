import os
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox
from typing import Optional
import customtkinter as ctk

from app.models import FeedSourceType, UserAction
from app.state import StateManager, BotRuntimeState, FeedState
from app.controller import FeedController
from naver.auth_guard import NaverAuthGuard
from services.config import ConfigService
from services.history import HistoryStore
from services.draft import DraftService
from services.contextual_draft import ContextualDraftEngine
from services.clipboard_bridge import ClipboardCommandBridge
from services.gemini_existing_chrome import ExistingChromeGeminiBridge
from services.gemini_extension_bridge import GeminiExtensionBridge, GeminiBridgeHTTPServer
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
        self.geometry("940x640+20+20")
        self.minsize(860, 560)

        self.config_service = ConfigService()
        self.skip_on_comment_failure_var = ctk.BooleanVar(
            value=self.config_service.get("skip_on_comment_failure", True)
        )
        self.gemini_extension_bridge = GeminiExtensionBridge()
        self.gemini_bridge_server = GeminiBridgeHTTPServer(
            self.gemini_extension_bridge,
            port=int(self.config_service.get("gemini_bridge_port", 43127)),
        )
        try:
            self.gemini_bridge_server.start()
        except OSError as exc:
            logger.log(f"[GEMINI/EXTENSION] 로컬 브리지 시작 실패: {exc}", "ERROR")
        self.history_store = HistoryStore()
        self.state_mgr = StateManager()
        self.command_bridge = ClipboardCommandBridge()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # State 및 Logger 리스너 등록
        self.state_mgr.register_listener(lambda s: self.after(0, self._update_ui_state, s))
        logger.register_gui_callback(lambda msg: self.after(0, self._append_log, msg))

        logger.log("네이버 피드 어시스턴트(Feed Assistant) 준비 완료.")

    def _build_ui(self):
        # 1. Header Frame (Compact)
        header = ctk.CTkFrame(self)
        header.pack(fill="x", padx=12, pady=(4, 2))

        title_lbl = ctk.CTkLabel(
            header,
            text="📱 NAVER FEED ASSISTANT",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        title_lbl.pack(side="left", padx=8, pady=3)

        subtitle_lbl = ctk.CTkLabel(
            header,
            text="Human-in-the-loop 피드 순회 · 인기 가드 · 20대 커뮤니티 리듬 댓글 · Enter 승인",
            font=ctk.CTkFont(size=11),
            text_color="#81C784"
        )
        subtitle_lbl.pack(side="left", padx=4, pady=3)

        # 2. Main Tabview (피드 작업 vs 세부 옵션 분리)
        self.tabview = ctk.CTkTabview(self, height=270)
        self.tabview.pack(fill="x", padx=12, pady=(0, 2))

        tab_feed = self.tabview.add("⚡ 피드 작업")
        tab_settings = self.tabview.add("⚙️ 세부 옵션")

        # ==================== [탭 1: 피드 작업] ====================
        # Source Selection
        src_frame = ctk.CTkFrame(tab_feed, fg_color="transparent")
        src_frame.pack(fill="x", padx=4, pady=(2, 2))

        ctk.CTkLabel(src_frame, text="피드 대상:", font=ctk.CTkFont(weight="bold", size=11)).grid(row=0, column=0, padx=4, pady=1, sticky="w")
        self.source_var = ctk.StringVar(value=self.config_service.get("feed_source", FeedSourceType.TARGETED_SEARCH.value))

        ctk.CTkRadioButton(
            src_frame, text="이웃 새글", font=ctk.CTkFont(size=11),
            variable=self.source_var, value=FeedSourceType.NEIGHBOR.value, command=self._on_source_change
        ).grid(row=0, column=1, padx=4, pady=1)

        ctk.CTkRadioButton(
            src_frame, text="🎯 관심주제 검색", font=ctk.CTkFont(size=11),
            variable=self.source_var, value=FeedSourceType.TARGETED_SEARCH.value, command=self._on_source_change
        ).grid(row=0, column=2, padx=4, pady=1)

        ctk.CTkRadioButton(
            src_frame, text="추천 피드 (보조)", font=ctk.CTkFont(size=11),
            variable=self.source_var, value=FeedSourceType.RECOMMENDATION.value, command=self._on_source_change
        ).grid(row=0, column=3, padx=4, pady=1)

        ctk.CTkRadioButton(
            src_frame, text="URL 직접 입력", font=ctk.CTkFont(size=11),
            variable=self.source_var, value=FeedSourceType.DIRECT.value, command=self._on_source_change
        ).grid(row=0, column=4, padx=4, pady=1)

        # 🎯 관심주제 탐색 프레임
        self.discovery_frame = ctk.CTkFrame(tab_feed, fg_color="#1E293B", corner_radius=6)

        disc_head = ctk.CTkFrame(self.discovery_frame, fg_color="transparent")
        disc_head.pack(fill="x", padx=6, pady=(2, 1))
        ctk.CTkLabel(disc_head, text="주제 선택:", font=ctk.CTkFont(weight="bold", size=11), text_color="#38BDF8").pack(side="left")

        saved_cats = self.config_service.get("discovery_categories", ["FOOD", "CAFE", "PARENTING", "LIVING", "TRAVEL", "LIFESTYLE"])
        self.cat_vars = {
            "FOOD": ctk.BooleanVar(value="FOOD" in saved_cats),
            "CAFE": ctk.BooleanVar(value="CAFE" in saved_cats),
            "PARENTING": ctk.BooleanVar(value="PARENTING" in saved_cats),
            "LIVING": ctk.BooleanVar(value="LIVING" in saved_cats),
            "TRAVEL": ctk.BooleanVar(value="TRAVEL" in saved_cats),
            "LIFESTYLE": ctk.BooleanVar(value="LIFESTYLE" in saved_cats)
        }
        for cat_key, cat_name in [("FOOD", "맛집"), ("CAFE", "카페"), ("PARENTING", "육아"), ("LIVING", "리빙"), ("TRAVEL", "여행"), ("LIFESTYLE", "일상")]:
            ctk.CTkCheckBox(disc_head, text=cat_name, font=ctk.CTkFont(size=11), variable=self.cat_vars[cat_key]).pack(side="left", padx=3)

        custom_q_row = ctk.CTkFrame(self.discovery_frame, fg_color="transparent")
        custom_q_row.pack(fill="x", padx=6, pady=(1, 3))
        ctk.CTkLabel(custom_q_row, text="내 검색어(쉼표 구분):", font=ctk.CTkFont(size=10)).pack(side="left", padx=(0, 2))
        self.custom_discovery_entry = ctk.CTkEntry(custom_q_row, placeholder_text="예: 강남 찐맛집, 제주 맛집 추천", height=22, font=ctk.CTkFont(size=11))
        self.custom_discovery_entry.pack(side="left", fill="x", expand=True, padx=2)
        saved_custom = ", ".join(self.config_service.get("custom_discovery_queries", []))
        if saved_custom:
            self.custom_discovery_entry.insert(0, saved_custom)
        add_mac_clipboard_support(self.custom_discovery_entry, self)

        # Direct URLs Box
        self.direct_url_frame = ctk.CTkFrame(tab_feed)
        ctk.CTkLabel(self.direct_url_frame, text="대상 URL 목록:", font=ctk.CTkFont(weight="bold", size=11)).pack(anchor="w", padx=6, pady=(1, 0))
        self.direct_url_textbox = ctk.CTkTextbox(self.direct_url_frame, height=36, font=ctk.CTkFont(size=11))
        self.direct_url_textbox.pack(fill="x", padx=6, pady=1)
        add_mac_clipboard_support(self.direct_url_textbox, self)

        # 초기 뷰 상태 적용
        if self.source_var.get() == FeedSourceType.TARGETED_SEARCH.value:
            self.discovery_frame.pack(fill="x", padx=4, pady=2)
        elif self.source_var.get() == FeedSourceType.DIRECT.value:
            self.direct_url_frame.pack(fill="x", padx=4, pady=2)

        # 작업 옵션 행 (공감, 댓글, 비밀댓글, 실패 시 자동 스킵, 최대 글 수)
        opt_frame = ctk.CTkFrame(tab_feed, fg_color="transparent")
        opt_frame.pack(fill="x", padx=4, pady=1)

        self.like_enabled_var = ctk.BooleanVar(value=self.config_service.get("like_enabled", True))
        ctk.CTkCheckBox(opt_frame, text="공감(하트)", font=ctk.CTkFont(size=11), variable=self.like_enabled_var).pack(side="left", padx=4)

        self.comment_enabled_var = ctk.BooleanVar(value=self.config_service.get("comment_enabled", True))
        ctk.CTkCheckBox(opt_frame, text="댓글 초안", font=ctk.CTkFont(size=11), variable=self.comment_enabled_var).pack(side="left", padx=4)

        self.secret_comment_var = ctk.BooleanVar(value=self.config_service.get("secret_comment", False))
        ctk.CTkCheckBox(opt_frame, text="비밀댓글", font=ctk.CTkFont(size=11), variable=self.secret_comment_var).pack(side="left", padx=4)

        ctk.CTkCheckBox(
            opt_frame, text="⚡ 댓글 실패 시 자동 스킵", font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.skip_on_comment_failure_var, text_color="#38BDF8"
        ).pack(side="left", padx=6)

        ctk.CTkLabel(opt_frame, text="최대 글 수:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 2))
        self.max_items_entry = ctk.CTkEntry(opt_frame, width=40, height=22, font=ctk.CTkFont(size=11))
        self.max_items_entry.pack(side="left", padx=1)
        self.max_items_entry.insert(0, str(self.config_service.get("max_feed_items", 20)))
        add_mac_clipboard_support(self.max_items_entry, self)

        # Gemini & Composer Card
        ai_card = ctk.CTkFrame(tab_feed, border_width=1, border_color="#334155")
        ai_card.pack(fill="x", padx=4, pady=(1, 2))

        ai_head = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_head.pack(fill="x", padx=6, pady=(1, 1))

        self.gemini_web_enabled_var = ctk.BooleanVar(value=self.config_service.get("gemini_web_enabled", True))
        ctk.CTkCheckBox(
            ai_head, text="Gemini 연동",
            variable=self.gemini_web_enabled_var, font=ctk.CTkFont(weight="bold", size=11), text_color="#38BDF8"
        ).pack(side="left", padx=2)

        self.gemini_browser_mode_var = ctk.StringVar(value=self.config_service.get("gemini_browser_mode", "extension_existing_chrome"))
        ctk.CTkRadioButton(
            ai_head, text="일반 Chrome 확장 (권장)", font=ctk.CTkFont(size=10),
            variable=self.gemini_browser_mode_var, value="extension_existing_chrome"
        ).pack(side="left", padx=4)

        ctk.CTkRadioButton(
            ai_head, text="Apple Events", font=ctk.CTkFont(size=10),
            variable=self.gemini_browser_mode_var, value="existing_chrome_mac"
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            ai_head, text="확장 연결 테스트", height=20, font=ctk.CTkFont(size=10),
            fg_color="#334155", hover_color="#475569", command=self._test_chrome_connection
        ).pack(side="left", padx=6)

        # Live Context Box (Compact)
        ai_preview_box = ctk.CTkFrame(ai_card, fg_color="#0F172A")
        ai_preview_box.pack(fill="x", padx=6, pady=1)

        self.ai_post_title_lbl = ctk.CTkLabel(
            ai_preview_box, text="현재 글: (작업 시작 시 자동 추출)",
            font=ctk.CTkFont(size=11, weight="bold"), anchor="w", text_color="#E2E8F0"
        )
        self.ai_post_title_lbl.pack(fill="x", padx=6, pady=1)

        self.ai_post_excerpt_lbl = ctk.CTkLabel(
            ai_preview_box, text="본문 요약: (작업 시작 시 자동 추출)",
            font=ctk.CTkFont(size=10), anchor="w", text_color="#94A3B8"
        )
        self.ai_post_excerpt_lbl.pack(fill="x", padx=6, pady=(0, 1))

        # Action Buttons Bar (Compact)
        ai_btn_bar = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_btn_bar.pack(fill="x", padx=6, pady=(1, 2))

        self.btn_copy_prompt = ctk.CTkButton(
            ai_btn_bar, text="📋 AI 프롬프트", height=22, font=ctk.CTkFont(size=10), fg_color="#0284C7", hover_color="#0369A1",
            command=self._copy_ai_prompt
        )
        self.btn_copy_prompt.pack(side="left", padx=1)

        ctk.CTkButton(
            ai_btn_bar, text="🌐 Gemini 열기", height=22, font=ctk.CTkFont(size=10), fg_color="#4F46E5", hover_color="#4338CA",
            command=lambda: webbrowser.open("https://gemini.google.com/")
        ).pack(side="left", padx=1)

        self.btn_apply_clipboard = ctk.CTkButton(
            ai_btn_bar, text="📥 클립보드 댓글", height=22, font=ctk.CTkFont(size=10), fg_color="#0D9488", hover_color="#0F766E",
            command=self._apply_clipboard_comment
        )
        self.btn_apply_clipboard.pack(side="left", padx=1)

        self.btn_gemini_retry = ctk.CTkButton(
            ai_btn_bar, text="재시도", height=22, width=65, font=ctk.CTkFont(size=10),
            fg_color="#2563EB", hover_color="#1D4ED8",
            command=self._retry_gemini, state="disabled"
        )
        self.btn_gemini_retry.pack(side="left", padx=1)

        self.btn_gemini_local = ctk.CTkButton(
            ai_btn_bar, text="로컬초안", height=22, width=65, font=ctk.CTkFont(size=10),
            fg_color="#475569", hover_color="#334155",
            command=self._use_local_once, state="disabled"
        )
        self.btn_gemini_local.pack(side="left", padx=1)

        self.btn_gemini_skip = ctk.CTkButton(
            ai_btn_bar, text="현재글 스킵", height=22, width=75, font=ctk.CTkFont(size=10),
            fg_color="#7C2D12", hover_color="#9A3412",
            command=self._skip_gemini_post, state="disabled"
        )
        self.btn_gemini_skip.pack(side="left", padx=1)

        ctk.CTkButton(
            ai_btn_bar, text="🎲 다른댓글", height=22, width=65, font=ctk.CTkFont(size=10), fg_color="#475569", hover_color="#334155",
            command=lambda: self._refine_current_comment(mode="alternate")
        ).pack(side="left", padx=1)

        ctk.CTkButton(
            ai_btn_bar, text="🌟 칭찬더하기", height=22, width=75, font=ctk.CTkFont(size=10), fg_color="#D97706", hover_color="#B45309",
            command=lambda: self._refine_current_comment(mode="praise")
        ).pack(side="left", padx=1)

        ctk.CTkButton(
            ai_btn_bar, text="✂️ 더짧게", height=22, width=55, font=ctk.CTkFont(size=10), fg_color="#64748B", hover_color="#475569",
            command=lambda: self._refine_current_comment(mode="short")
        ).pack(side="left", padx=1)

        # ==================== [탭 2: 세부 옵션] ====================
        # 1. Like Popularity Guard
        guard_frame = ctk.CTkFrame(tab_settings, border_width=1, border_color="#334155")
        guard_frame.pack(fill="x", padx=4, pady=(2, 2))

        g_head = ctk.CTkFrame(guard_frame, fg_color="transparent")
        g_head.pack(fill="x", padx=6, pady=(2, 1))

        self.like_guard_chk_var = ctk.BooleanVar(value=self.config_service.get("like_popularity_guard_enabled", True))
        ctk.CTkCheckBox(
            g_head, text="🛡️ 공감수 높은 글 제외 (기준: ", font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.like_guard_chk_var, text_color="#F472B6"
        ).pack(side="left", padx=2)

        self.like_thresh_entry = ctk.CTkEntry(g_head, width=38, height=20, font=ctk.CTkFont(size=11))
        self.like_thresh_entry.pack(side="left", padx=1)
        self.like_thresh_entry.insert(0, str(self.config_service.get("like_count_skip_threshold", 999)))
        add_mac_clipboard_support(self.like_thresh_entry, self)
        ctk.CTkLabel(g_head, text="개 이상 스킵)", font=ctk.CTkFont(size=11)).pack(side="left", padx=(1, 10))

        self.visitor_guard_chk_var = ctk.BooleanVar(value=self.config_service.get("daily_visitor_guard_enabled", True))
        ctk.CTkCheckBox(
            g_head, text="🛡️ 일 방문자 많은 블로그 제외 (기준: ", font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.visitor_guard_chk_var, text_color="#F472B6"
        ).pack(side="left", padx=2)

        self.visitor_thresh_entry = ctk.CTkEntry(g_head, width=48, height=20, font=ctk.CTkFont(size=11))
        self.visitor_thresh_entry.pack(side="left", padx=1)
        self.visitor_thresh_entry.insert(0, str(self.config_service.get("daily_visitor_skip_threshold", 10000)))
        add_mac_clipboard_support(self.visitor_thresh_entry, self)
        ctk.CTkLabel(g_head, text="명 초과 스킵)", font=ctk.CTkFont(size=11)).pack(side="left", padx=1)

        g_sub = ctk.CTkFrame(guard_frame, fg_color="transparent")
        g_sub.pack(fill="x", padx=6, pady=(0, 2))
        ctk.CTkLabel(g_sub, text="방문자 확인 불가 시:", font=ctk.CTkFont(size=10)).pack(side="left", padx=2)
        self.unknown_policy_var = ctk.StringVar(value=self.config_service.get("daily_visitor_unknown_policy", "skip_like"))
        ctk.CTkRadioButton(g_sub, text="공감 안 함 (권장)", variable=self.unknown_policy_var, value="skip_like", font=ctk.CTkFont(size=10)).pack(side="left", padx=3)
        ctk.CTkRadioButton(g_sub, text="공감 진행", variable=self.unknown_policy_var, value="continue", font=ctk.CTkFont(size=10)).pack(side="left", padx=3)

        # 2. Template & Suffixes
        tmpl_frame = ctk.CTkFrame(tab_settings)
        tmpl_frame.pack(fill="x", padx=4, pady=2)

        tmpl_row1 = ctk.CTkFrame(tmpl_frame, fg_color="transparent")
        tmpl_row1.pack(fill="x", padx=6, pady=(2, 0))
        ctk.CTkLabel(tmpl_row1, text="댓글 기본 문구 (Spintax {A|B}):", font=ctk.CTkFont(weight="bold", size=11)).pack(side="left")
        ctk.CTkLabel(tmpl_row1, text="말투:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(15, 2))
        self.comment_style_preset_var = ctk.StringVar(value=self.config_service.get("comment_style_preset", "thoughtful"))
        ctk.CTkRadioButton(tmpl_row1, text="20대 커뮤니티", variable=self.comment_style_preset_var, value="community", font=ctk.CTkFont(size=11)).pack(side="left", padx=3)
        ctk.CTkRadioButton(tmpl_row1, text="조금 더 얌전하게", variable=self.comment_style_preset_var, value="calm", font=ctk.CTkFont(size=11)).pack(side="left", padx=3)
        ctk.CTkRadioButton(tmpl_row1, text="✨ 정성 소통형 (풍부한 길이)", variable=self.comment_style_preset_var, value="thoughtful", font=ctk.CTkFont(size=11)).pack(side="left", padx=3)

        self.tmpl_textbox = ctk.CTkTextbox(tmpl_frame, height=26, font=ctk.CTkFont(size=11))
        self.tmpl_textbox.pack(fill="x", padx=6, pady=1)
        self.tmpl_textbox.insert("1.0", self.config_service.get("comment_template", "{비쥬얼이 참 좋네요|너무 좋아 보여요|보기만 해도 기분 좋아지는 글이네요}~"))
        add_mac_clipboard_support(self.tmpl_textbox, self)

        suffix_box = ctk.CTkFrame(tmpl_frame, fg_color="transparent")
        suffix_box.pack(fill="x", padx=6, pady=(1, 2))

        ctk.CTkLabel(suffix_box, text="일반 꼬리말:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 2))
        self.general_suffix_entry = ctk.CTkEntry(suffix_box, font=ctk.CTkFont(size=11), width=240, height=20)
        self.general_suffix_entry.pack(side="left", padx=2)
        self.general_suffix_entry.insert(0, self.config_service.get("general_suffix", self.config_service.get("fixed_suffix", "")))
        add_mac_clipboard_support(self.general_suffix_entry, self)

        self.recom_suffix_chk_var = ctk.BooleanVar(value=self.config_service.get("recommendation_suffix_enabled", False))
        ctk.CTkCheckBox(suffix_box, text="추천 피드 전용 꼬리말:", variable=self.recom_suffix_chk_var, font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 2))
        self.recom_suffix_entry = ctk.CTkEntry(suffix_box, font=ctk.CTkFont(size=11), width=240, height=20)
        self.recom_suffix_entry.pack(side="left", padx=2)
        self.recom_suffix_entry.insert(0, self.config_service.get("recommendation_suffix", ""))
        add_mac_clipboard_support(self.recom_suffix_entry, self)

        # 3. Pacing Settings
        pacing_frame = ctk.CTkFrame(tab_settings)
        pacing_frame.pack(fill="x", padx=4, pady=2)

        p_head = ctk.CTkFrame(pacing_frame, fg_color="transparent")
        p_head.pack(fill="x", padx=6, pady=(1, 1))

        self.pacing_enabled_var = ctk.BooleanVar(value=self.config_service.get("pacing_enabled", True))
        ctk.CTkCheckBox(p_head, text="⏱️ 간격 조절(Pacing)", font=ctk.CTkFont(weight="bold", size=11), variable=self.pacing_enabled_var).pack(side="left", padx=2)

        self.random_pause_var = ctk.BooleanVar(value=self.config_service.get("random_pause_enabled", True))
        ctk.CTkCheckBox(p_head, text="☕ 랜덤 휴지(Pause)", font=ctk.CTkFont(weight="bold", size=11), variable=self.random_pause_var).pack(side="left", padx=8)

        ctk.CTkLabel(p_head, text="동작 대기:", font=ctk.CTkFont(size=10)).pack(side="left", padx=1)
        self.action_min_entry = ctk.CTkEntry(p_head, width=32, height=18, font=ctk.CTkFont(size=10)); self.action_min_entry.pack(side="left", padx=1); self.action_min_entry.insert(0, str(self.config_service.get("action_delay_min", 1.0)))
        ctk.CTkLabel(p_head, text="~", font=ctk.CTkFont(size=10)).pack(side="left")
        self.action_max_entry = ctk.CTkEntry(p_head, width=32, height=18, font=ctk.CTkFont(size=10)); self.action_max_entry.pack(side="left", padx=1); self.action_max_entry.insert(0, str(self.config_service.get("action_delay_max", 2.5)))
        ctk.CTkLabel(p_head, text="초", font=ctk.CTkFont(size=10)).pack(side="left", padx=(1, 6))

        ctk.CTkLabel(p_head, text="다음 글:", font=ctk.CTkFont(size=10)).pack(side="left", padx=1)
        self.next_min_entry = ctk.CTkEntry(p_head, width=32, height=18, font=ctk.CTkFont(size=10)); self.next_min_entry.pack(side="left", padx=1); self.next_min_entry.insert(0, str(self.config_service.get("next_post_delay_min", 2.0)))
        ctk.CTkLabel(p_head, text="~", font=ctk.CTkFont(size=10)).pack(side="left")
        self.next_max_entry = ctk.CTkEntry(p_head, width=32, height=18, font=ctk.CTkFont(size=10)); self.next_max_entry.pack(side="left", padx=1); self.next_max_entry.insert(0, str(self.config_service.get("next_post_delay_max", 5.0)))
        ctk.CTkLabel(p_head, text="초", font=ctk.CTkFont(size=10)).pack(side="left", padx=(1, 6))

        ctk.CTkLabel(p_head, text="Pause:", font=ctk.CTkFont(size=10)).pack(side="left", padx=1)
        self.pause_chance_entry = ctk.CTkEntry(p_head, width=28, height=18, font=ctk.CTkFont(size=10)); self.pause_chance_entry.pack(side="left", padx=1); self.pause_chance_entry.insert(0, str(int(float(self.config_service.get("random_pause_chance", 0.10)) * 100)))
        ctk.CTkLabel(p_head, text="%", font=ctk.CTkFont(size=10)).pack(side="left")
        self.pause_min_entry = ctk.CTkEntry(p_head, width=28, height=18, font=ctk.CTkFont(size=10)); self.pause_min_entry.pack(side="left", padx=1); self.pause_min_entry.insert(0, str(self.config_service.get("random_pause_min", 8.0)))
        ctk.CTkLabel(p_head, text="~", font=ctk.CTkFont(size=10)).pack(side="left")
        self.pause_max_entry = ctk.CTkEntry(p_head, width=28, height=18, font=ctk.CTkFont(size=10)); self.pause_max_entry.pack(side="left", padx=1); self.pause_max_entry.insert(0, str(self.config_service.get("random_pause_max", 20.0)))
        ctk.CTkLabel(p_head, text="초", font=ctk.CTkFont(size=10)).pack(side="left")

        p_detail = ctk.CTkFrame(pacing_frame, fg_color="transparent")
        p_detail.pack(fill="x", padx=6, pady=(0, 2))
        def add_range(parent, label, min_attr, max_attr, min_key, max_key, dmin, dmax):
            ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=10)).pack(side="left", padx=(2, 1))
            mn = ctk.CTkEntry(parent, width=32, height=18, font=ctk.CTkFont(size=10)); mn.pack(side="left", padx=1); mn.insert(0, str(self.config_service.get(min_key, dmin)))
            ctk.CTkLabel(parent, text="~", font=ctk.CTkFont(size=10)).pack(side="left")
            mx = ctk.CTkEntry(parent, width=32, height=18, font=ctk.CTkFont(size=10)); mx.pack(side="left", padx=1); mx.insert(0, str(self.config_service.get(max_key, dmax)))
            ctk.CTkLabel(parent, text="초", font=ctk.CTkFont(size=10)).pack(side="left", padx=(1, 8))
            setattr(self, min_attr, mn); setattr(self, max_attr, mx)
        add_range(p_detail, "안정화:", "settle_min_entry", "settle_max_entry", "page_settle_min", "page_settle_max", 1.0, 2.0)
        add_range(p_detail, "본문→공감:", "pre_like_min_entry", "pre_like_max_entry", "pre_like_delay_min", "pre_like_delay_max", 5.0, 10.0)
        add_range(p_detail, "공감→댓글:", "post_like_min_entry", "post_like_max_entry", "post_like_delay_min", "post_like_delay_max", 2.0, 5.0)

        # ==================== [하단 고정 영역] ====================
        # UX Shortcut Guide (Compact)
        guide_box = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=5)
        guide_box.pack(fill="x", padx=12, pady=(1, 2))
        guide_text = "⌨️ [댓글 승인] Enter = 최종 등록  |  Shift+Enter = 줄바꿈  |  Cmd+V = 붙여넣기  |  Esc = 이번 글 건너뛰기"
        ctk.CTkLabel(guide_box, text=guide_text, font=ctk.CTkFont(size=11, weight="bold"), text_color="#FBBF24").pack(pady=2)

        # Status Dashboard
        dash_frame = ctk.CTkFrame(self)
        dash_frame.pack(fill="x", padx=12, pady=1)

        self.status_msg_lbl = ctk.CTkLabel(dash_frame, text="대기 중", font=ctk.CTkFont(size=12, weight="bold"), text_color="#FBBF24")
        self.status_msg_lbl.pack(side="left", padx=8, pady=2)

        self.badge_lbl = ctk.CTkLabel(
            dash_frame,
            text="처리: 0/0 | ❤️ 공감: 0 | 💬 댓글: 0 | ⏭️ 건너뜀: 0",
            font=ctk.CTkFont(size=11)
        )
        self.badge_lbl.pack(side="right", padx=8, pady=2)

        # Action Buttons Bar
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=2)

        self.btn_start = ctk.CTkButton(
            btn_frame, text="▶ 피드 작업 시작", fg_color="#16A34A", hover_color="#15803D", height=32,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._start_task
        )
        self.btn_start.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_pause = ctk.CTkButton(
            btn_frame, text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309", height=32,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._toggle_pause, state="disabled"
        )
        self.btn_pause.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_skip_post = ctk.CTkButton(
            btn_frame, text="⏭️ 다음 글 스킵", fg_color="#4F46E5", hover_color="#4338CA", height=32,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._skip_to_next_post, state="disabled"
        )
        self.btn_skip_post.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_stop = ctk.CTkButton(
            btn_frame, text="⏹ 작업 중지", fg_color="#DC2626", hover_color="#B91C1C", height=32,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._stop_task, state="disabled"
        )
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=2)

        ctk.CTkButton(
            btn_frame, text="🌐 로그인 창", width=95, height=32, font=ctk.CTkFont(size=11),
            command=self._open_login_window
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="📊 반응 CSV", width=105, height=32,
            fg_color="#7C3AED", hover_color="#6D28D9",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._run_engagement_audit
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_frame, text="🔓 락 초기화", width=75, height=32, font=ctk.CTkFont(size=11),
            fg_color="#475569", hover_color="#334155",
            command=self._reset_lock
        ).pack(side="left", padx=2)

        # Log Console (Compact & Scrollable)
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(1, 4))

        log_head = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_head.pack(fill="x", padx=6, pady=(1, 0))
        ctk.CTkLabel(log_head, text="📋 실시간 작업 로그", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
        ctk.CTkButton(log_head, text="로그 지우기", width=65, height=18, font=ctk.CTkFont(size=10), command=self._clear_log).pack(side="right")

        self.log_textbox = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Courier", size=11))
        self.log_textbox.pack(fill="both", expand=True, padx=6, pady=(1, 3))
        add_mac_clipboard_support(self.log_textbox, self)

    def _refine_current_comment(self, mode: str = "alternate"):
        """현재 글의 맥락을 기반으로 다른 스타일/길이의 댓글을 즉시 생성하여 에디터에 적용"""
        snapshot = self.state_mgr.get_snapshot()
        title = snapshot.current_post_title
        excerpt = snapshot.current_post_excerpt

        if not title:
            messagebox.showinfo("알림", "현재 처리 중인 게시글이 없습니다. 피드 작업 시작 후 글에 진입했을 때 클릭해 주세요.")
            return

        praise_b = (mode == "praise")
        short_b = (mode == "short")
        preset = self.comment_style_preset_var.get() if hasattr(self, "comment_style_preset_var") else "community"

        res = ContextualDraftEngine.generate(title, excerpt, praise_boost=praise_b, short_boost=short_b, preset=preset)
        if not res or not res.body:
            messagebox.showwarning("생성 불가", "본문에서 구체적인 앵커를 찾지 못해 변형 댓글을 생성하지 못했습니다.")
            return

        source_type = FeedSourceType(self.source_var.get())
        suffix = DraftService.resolve_suffix(source_type, self.config_service)
        final_comment = DraftService.compose_body_and_suffix(res.body, suffix)

        from services.comments.community_rhythm import FinalQualityGate
        gate_res = FinalQualityGate.validate_final_text(final_comment, preset=preset, source="local")
        if not gate_res.valid:
            messagebox.showwarning("품질 게이트 실패", f"생성된 댓글이 품질 기준을 통과하지 못했습니다:\n\n- 사유: {gate_res.reason}\n- 위반: {gate_res.matched or gate_res.code}")
            return

        self.clipboard_clear()
        self.clipboard_append(final_comment)
        self.update()

        self.command_bridge.send_apply_clipboard_comment(final_comment)
        logger.log(f"🔄 [REFINE] 댓글 변형 적용 ({mode}): \"{res.body}\"")

    def _test_chrome_connection(self):
        diag = self.gemini_extension_bridge.preflight()
        if diag.ready:
            msg = f"일반 Chrome Gemini 확장 연결 성공\n\n제목: {diag.title}\nURL: {diag.url}"
            messagebox.showinfo("Gemini 연결 성공", msg)
            logger.log(f"[GEMINI/EXTENSION] 연결 성공: {diag.title} ({diag.url})")
        else:
            msg = f"Gemini 확장 연결 실패: {diag.status}\n\n확장을 설치하고 토큰을 저장한 뒤 로그인된 Gemini 탭을 새로고침하세요."
            messagebox.showwarning("Gemini 연결 실패", msg)
            logger.log(f"[GEMINI/EXTENSION] 연결 실패: {diag.message}", "WARNING")

    def _on_source_change(self):
        val = self.source_var.get()
        if val == FeedSourceType.TARGETED_SEARCH.value:
            self.discovery_frame.pack(fill="x", padx=4, pady=2)
            self.direct_url_frame.pack_forget()
        elif val == FeedSourceType.DIRECT.value:
            self.discovery_frame.pack_forget()
            self.direct_url_frame.pack(fill="x", padx=4, pady=2)
        else:
            self.discovery_frame.pack_forget()
            self.direct_url_frame.pack_forget()

    def _update_ui_state(self, state: BotRuntimeState):
        self.status_msg_lbl.configure(text=f"상태: {state.message}")
        self.badge_lbl.configure(
            text=f"처리: {state.processed_count}/{state.total_target_count} | ❤️ 공감: {state.likes_count} | 💬 댓글: {state.comments_count} | ⏭️ 건너뜀: {state.skipped_count}"
        )
        if state.current_post_title:
            self.ai_post_title_lbl.configure(text=f"현재 글: {state.current_post_title[:45]}")
        if state.current_post_excerpt:
            self.ai_post_excerpt_lbl.configure(text=f"본문 요약: {state.current_post_excerpt[:70]}...")
        gemini_failed_pause = state.current_state == FeedState.PAUSED and "Gemini 실패" in (state.message or "")
        button_state = "normal" if gemini_failed_pause else "disabled"
        for button_name in ("btn_gemini_retry", "btn_gemini_local", "btn_gemini_skip"):
            button = getattr(self, button_name, None)
            if button:
                button.configure(state=button_state)

    def _retry_gemini(self):
        self.command_bridge.send_gemini_retry()

    def _use_local_once(self):
        self.command_bridge.send_gemini_use_local_once()

    def _skip_gemini_post(self):
        self.command_bridge.send_gemini_skip_post()

    def _copy_ai_prompt(self):
        prompt = self.state_mgr.get_snapshot().current_ai_prompt
        if not prompt:
            messagebox.showinfo("알림", "현재 준비된 AI 프롬프트가 없습니다. 피드 작업 시작 후 글에 진입하면 자동으로 생성됩니다.")
            return

        self.clipboard_clear()
        self.clipboard_append(prompt)
        self.update()
        logger.log("📋 [AI] Gemini 댓글 생성용 프롬프트가 클립보드에 복사되었습니다.")
        messagebox.showinfo("복사 완료", "AI 프롬프트가 클립보드에 복사되었습니다!\nGemini에 붙여넣어 댓글을 생성하세요.")

    def _apply_clipboard_comment(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            text = ""

        if not text:
            messagebox.showwarning("경고", "클립보드에 댓글 텍스트가 없습니다. Gemini에서 생성된 댓글을 먼저 복사해 주세요.")
            return

        preset = self.comment_style_preset_var.get() if hasattr(self, "comment_style_preset_var") else "community"
        from services.comments.community_rhythm import FinalQualityGate
        gate_res = FinalQualityGate.validate_final_text(text, preset=preset, source="clipboard")
        if not gate_res.valid:
            logger.log(f"⚠️ [AI] 클립보드 댓글이 품질 게이트를 통과하지 못했습니다: [{gate_res.code}] {gate_res.reason} (매칭: {gate_res.matched})", "WARNING")
            messagebox.showwarning("품질 게이트 실패", f"클립보드 댓글이 품질 기준을 통과하지 못했습니다.\n\n- 사유: {gate_res.reason}\n- 위반 항목: {gate_res.matched or gate_res.code}")
            return

        self.command_bridge.send_apply_clipboard_comment(text)
        logger.log("📥 [AI] 클립보드의 댓글 텍스트를 현재 글 에디터에 적용 요청했습니다.")

    def _append_log(self, msg: str):
        self.log_textbox.insert("end", msg + "\n")
        self.log_textbox.see("end")

    def _clear_log(self):
        self.log_textbox.delete("1.0", "end")

    def _reset_lock(self):
        status = ProfileLockManager.inspect(USER_DATA_DIR)
        if status.live_app_pid:
            messagebox.showwarning("초기화 불가", f"현재 앱 작업(PID {status.live_app_pid})이 실행 중입니다.\n작업을 먼저 중지해 주세요.")
            return
        if status.live_chromium_pid:
            messagebox.showwarning("초기화 불가", f"프로필을 사용하는 Chromium 브라우저(PID {status.live_chromium_pid})가 아직 실행 중입니다.\n브라우저 창을 먼저 닫아주세요.")
            return

        cleaned = ProfileLockManager.cleanup_stale_locks(USER_DATA_DIR)
        if cleaned:
            logger.log("🔓 남아있던 잔여 락 파일(SingletonLock 등)이 안전하게 초기화되었습니다.")
            messagebox.showinfo("초기화 완료", "잔여 락이 안전하게 초기화되었습니다.\n이제 작업을 시작하실 수 있습니다.")
        else:
            logger.log("🔓 프로필이 이미 정상 상태입니다.")
            messagebox.showinfo("알림", "정리할 잔여 락이 없습니다.")

    def _open_login_window(self):
        if ProfileLockManager.is_locked(USER_DATA_DIR):
            messagebox.showwarning("알림", "프로필이 이미 사용 중입니다. 락 상태를 확인하거나 기존 브라우저를 닫아주세요.")
            return

        self.btn_start.configure(state="disabled")

        def task():
            logger.log("==================================================")
            logger.log("🌐 [LOGIN] 네이버 로그인용 브라우저를 시작합니다...")
            logger.log("💡 브라우저 창에서 네이버 로그인을 완료하시면 자동으로 감지하여 저장합니다.")
            session = BrowserSession(headless=False)
            try:
                ctx = session.start()
                page = ctx.new_page()
                page.goto("https://nid.naver.com/nidlogin.login")

                login_detected = False
                while True:
                    try:
                        if not ctx.pages or all(p.is_closed() for p in ctx.pages):
                            break
                    except Exception:
                        break

                    # 0.5초마다 로그인 쿠키 성공 감지
                    is_logged_in, _ = NaverAuthGuard.check_login_cookies(ctx)
                    if is_logged_in:
                        login_detected = True
                        logger.log("✅ [LOGIN] 네이버 로그인 성공 감지! 세션이 영구 저장되었습니다.")
                        self.after(0, lambda: messagebox.showinfo("로그인 완료", "✅ 네이버 로그인이 성공적으로 완료 및 저장되었습니다!\n이제 [피드 작업 시작] 버튼을 눌러 작업을 진행하세요."))
                        break

                    import time
                    time.sleep(0.5)

                if not login_detected:
                    logger.log("ℹ️ [LOGIN] 로그인 브라우저가 종료되었습니다.")
            except Exception as e:
                logger.log(f"로그인 브라우저 오류: {e}", "ERROR")
            finally:
                session.close()
                self.after(0, lambda: self.btn_start.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def _run_engagement_audit(self):
        """내 블로그 최근 글의 이웃별 누적 공감/댓글을 CSV 하나로 저장한다."""
        from tkinter import simpledialog
        from services.engagement_audit_service import EngagementAuditService
        import subprocess

        saved_id = self.config_service.get("my_blog_id", "")
        blog_id = simpledialog.askstring(
            "내 블로그 ID 입력",
            "반응자를 수집할 네이버 블로그 ID를 입력하세요 (예: iwbbt):",
            initialvalue=saved_id or "",
            parent=self
        )
        if not blog_id or not blog_id.strip():
            return

        blog_id = blog_id.strip()
        self.config_service.set("my_blog_id", blog_id)

        if ProfileLockManager.is_locked(USER_DATA_DIR):
            messagebox.showwarning("알림", "프로필이 사용 중입니다. 실행 중인 브라우저나 작업을 먼저 중지해 주세요.")
            return

        logger.log(f"👥 [AUDIT] '{blog_id}' 블로그 최근 글 반응자 수집을 시작합니다...")

        def task():
            session = BrowserSession(headless=True)
            try:
                ctx = session.start()
                page = ctx.new_page()
                res = EngagementAuditService.run_audit(
                    page=page,
                    my_blog_id=blog_id,
                    recent_post_count=int(self.config_service.get("engagement_audit_recent_posts", 5))
                )

                if res.get("success"):
                    rep = res["report"]
                    summary_csv = res.get("summary_csv_path", "")
                    audit_st = rep.get("audit_state", "complete").upper()

                    msg = (
                        f"🎉 [내 블로그 이웃 전수 및 무반응 감사 완료 (상태: {audit_st})]\n\n"
                        f"• 분석 대상 최근 글: {rep['recent_post_count']}개\n"
                        f"• 👥 전체 등록 이웃: {rep.get('total_buddies_count', 0)}명\n"
                        f"• ❤️ 최근 글 반응 이웃: {rep.get('reacted_buddies_count', 0)}명\n"
                        f"   (공감+댓글: {rep.get('both_like_and_comment_count', 0)}명, 공감만: {rep.get('liked_only_count', 0)}명, 댓글만: {rep.get('commented_only_count', 0)}명)\n"
                        f"• 🚫 최근 글 무반응 이웃: {rep.get('unresponsive_buddies_count', 0)}명\n"
                        f"   (추가일과 이후 2일 유예: {rep.get('grace_period_buddies_count', 0)}명 / 확인된 무반응: {rep.get('real_unresponsive_count', 0)}명)\n\n"
                        f"이웃별 누적 반응 CSV:\n{summary_csv}\n\n"
                        f"지금 파일을 바로 여시겠습니까?"
                    )

                    def show_dialog():
                        ans = messagebox.askyesno("감사 완료", msg)
                        if ans:
                            try:
                                subprocess.run(["open", summary_csv])
                            except Exception:
                                pass

                    self.after(0, show_dialog)
                else:
                    err_msg = res.get('error', 'unknown')
                    self.after(0, lambda: messagebox.showwarning("수집 실패", f"감사 실패: {err_msg}"))
            except Exception as e:
                err_text = str(e)
                logger.log(f"❌ [AUDIT] 수집 중 오류: {err_text}", "ERROR")
                self.after(0, lambda msg=err_text: messagebox.showerror("오류", f"수집 중 오류 발생: {msg}"))
            finally:
                session.close()

        threading.Thread(target=task, daemon=True).start()

    def _start_task(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("경고", "이미 작업이 실행 중입니다.")
            return

        # 1. 숫자 입력 검증
        try:
            max_items = int(self.max_items_entry.get().strip())
            act_min = float(self.action_min_entry.get().strip())
            act_max = float(self.action_max_entry.get().strip())
            nxt_min = float(self.next_min_entry.get().strip())
            nxt_max = float(self.next_max_entry.get().strip())
            p_chance = float(self.pause_chance_entry.get().strip()) / 100.0
            p_min = float(self.pause_min_entry.get().strip())
            p_max = float(self.pause_max_entry.get().strip())
            settle_min = float(self.settle_min_entry.get().strip())
            settle_max = float(self.settle_max_entry.get().strip())
            pre_like_min = float(self.pre_like_min_entry.get().strip())
            pre_like_max = float(self.pre_like_max_entry.get().strip())
            post_like_min = float(self.post_like_min_entry.get().strip())
            post_like_max = float(self.post_like_max_entry.get().strip())

            like_thresh = int(self.like_thresh_entry.get().strip())
            visitor_thresh = int(self.visitor_thresh_entry.get().strip())

            if not (1 <= max_items <= 500):
                raise ValueError("최대 처리 글 수는 1~500 사이여야 합니다.")
            if not (0 <= act_min <= act_max <= 300) or not (0 <= nxt_min <= nxt_max <= 300):
                raise ValueError("동작 간격 및 다음 글 대기 시간 범위가 올바르지 않습니다.")
            if not (0 <= p_chance <= 1.0) or not (0 <= p_min <= p_max <= 3600):
                raise ValueError("Pause 확률(0~100%) 및 시간 범위가 올바르지 않습니다.")
            if not all((0 <= lo <= hi <= 300) for lo, hi in ((settle_min, settle_max), (pre_like_min, pre_like_max), (post_like_min, post_like_max))):
                raise ValueError("세부 작업 간격 범위가 올바르지 않습니다.")
            if like_thresh < 1 or visitor_thresh < 1:
                raise ValueError("공감수 및 일 방문자 수 기준값은 1 이상이어야 합니다.")
        except ValueError as ve:
            messagebox.showwarning("입력 오류", str(ve) or "숫자 입력값을 확인해 주세요.")
            return

        source_val = self.source_var.get()
        direct_urls = []
        if source_val == FeedSourceType.DIRECT.value:
            raw_text = self.direct_url_textbox.get("1.0", "end-1c").strip()
            direct_urls = [u.strip() for u in raw_text.splitlines() if u.strip()]
            if not direct_urls:
                messagebox.showwarning("입력 오류", "URL 직접 입력 목록을 1개 이상 입력해 주세요.")
                return

        # 관심주제 설정 추출
        enabled_discovery_cats = [cat for cat, v in self.cat_vars.items() if v.get()]
        if not enabled_discovery_cats:
            enabled_discovery_cats = ["FOOD", "CAFE", "PARENTING", "LIVING", "TRAVEL", "LIFESTYLE"]

        raw_custom_q = self.custom_discovery_entry.get().strip()
        custom_queries_list = [q.strip() for q in raw_custom_q.split(",") if q.strip()]

        # Config 단조 업데이트 및 원자적 저장 (update_many 사용)
        cfg_data = {
            "feed_source": source_val,
            "discovery_categories": enabled_discovery_cats,
            "custom_discovery_queries": custom_queries_list,
            "max_feed_items": max_items,
            "like_enabled": self.like_enabled_var.get(),
            "comment_enabled": self.comment_enabled_var.get(),
            "comment_template": self.tmpl_textbox.get("1.0", "end-1c").strip(),
            "general_suffix": self.general_suffix_entry.get().strip(),
            "fixed_suffix": self.general_suffix_entry.get().strip(),
            "recommendation_suffix_enabled": self.recom_suffix_chk_var.get(),
            "recommendation_suffix": self.recom_suffix_entry.get().strip(),
            "comment_style_preset": self.comment_style_preset_var.get(),
            "secret_comment": self.secret_comment_var.get(),
            "skip_on_comment_failure": self.skip_on_comment_failure_var.get(),
            "direct_urls": direct_urls,

            "pacing_enabled": self.pacing_enabled_var.get(),
            "action_delay_min": act_min,
            "action_delay_max": act_max,
            "next_post_delay_min": nxt_min,
            "next_post_delay_max": nxt_max,
            "random_pause_enabled": self.random_pause_var.get(),
            "random_pause_chance": p_chance,
            "random_pause_min": p_min,
            "random_pause_max": p_max,
            "page_settle_min": settle_min,
            "page_settle_max": settle_max,
            "pre_like_delay_min": pre_like_min,
            "pre_like_delay_max": pre_like_max,
            "post_like_delay_min": post_like_min,
            "post_like_delay_max": post_like_max,

            "like_popularity_guard_enabled": self.like_guard_chk_var.get(),
            "like_count_skip_threshold": like_thresh,
            "daily_visitor_guard_enabled": self.visitor_guard_chk_var.get(),
            "daily_visitor_skip_threshold": visitor_thresh,
            "daily_visitor_unknown_policy": self.unknown_policy_var.get(),

            "ai_clipboard_enabled": True,
            "ai_context_max_chars": 700,
            "ai_prompt_style": "warm_short",

            "gemini_browser_mode": self.gemini_browser_mode_var.get(),
            "gemini_web_enabled": self.gemini_web_enabled_var.get()
        }
        self.config_service.update_many(cfg_data)
        logger.log(f"[CONFIG] 공감수 제외 기준: {like_thresh}개 (source=data/config.json)")

        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal", text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309")
        self.btn_skip_post.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self.stop_event.clear()
        self.pause_event.clear()
        self.command_bridge.clear()

        controller = FeedController(
            config=self.config_service,
            history=self.history_store,
            state_mgr=self.state_mgr,
            stop_event=self.stop_event,
            command_bridge=self.command_bridge,
            pause_event=self.pause_event,
            gemini_extension_bridge=self.gemini_extension_bridge,
        )
        self.current_controller = controller

        def worker():
            try:
                controller.run()
            finally:
                self.after(0, self._on_task_finished)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _toggle_pause(self):
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        if not self.pause_event.is_set():
            self.pause_event.set()
            self.btn_pause.configure(text="▶️ 작업 재개", fg_color="#2563EB", hover_color="#1D4ED8")
            logger.log("⏸️ 작업이 일시정지되었습니다. [▶️ 작업 재개] 버튼을 누르면 이어서 진행합니다.", "WARNING")
            self.state_mgr.update(new_state=FeedState.PAUSED, message="작업 일시정지됨 (재개 대기 중)")
        else:
            self.pause_event.clear()
            self.btn_pause.configure(text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309")
            logger.log("▶️ 작업을 다시 재개합니다.")
            self.state_mgr.update(message="작업 재개됨")

    def _skip_to_next_post(self):
        """현재 글 처리를 즉시 건너뛰고 다음 글로 바로 이동"""
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        logger.log("⏭️ [USER] 다음 글로 바로 넘어가기 버튼 클릭됨 (현재 글 스킵)")
        if hasattr(self, "current_controller") and self.current_controller:
            self.current_controller.request_skip_current_post()
        else:
            self.command_bridge.send_skip_post()
        if self.pause_event and self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.configure(text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309")

    def _stop_task(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.pause_event.clear()
            self.stop_event.set()
            logger.log("⏹ 작업 중지 신호 전송됨: 현재 단계 완료 즉시 안전하게 종료합니다...", "WARNING")

    def _on_task_finished(self):
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309")
        self.btn_skip_post.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.pause_event.clear()
        self.current_controller = None

    def _on_close(self):
        self.stop_event.set()
        try:
            self.gemini_bridge_server.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    from ui.log_clipboard_support import install_log_clipboard_support
    app = MainWindow()
    install_log_clipboard_support(app)
    app.mainloop()

