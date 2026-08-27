"""Local, explicitly operated Naver panel. No generation or submission automation."""
import hashlib
import json
import os
import tempfile
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

from app.models import (CommentProcessResult, CommentSubmitState, LikeProcessResult,
                        LikeState, PostProcessResult, WorkerCommandType)
from app.state import FeedState
from app.errors import FatalSessionError
from naver.content_extractor import ContentContextExtractor
from naver.resolver import MobileDOMResolver
from naver.target_guard import TargetPostGuard
from services.helper_policy import ASSETS, POLICY, validate_comment
from services.like_transaction import LikeCircuitBreaker, LikeConfidence, LikeTransactionService

ROOT = Path(__file__).resolve().parent.parent


class HelperDraftStore:
    """Each post has an atomic local record. Never uploads drafts or answers."""
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / "data" / "helper_drafts")

    def path_for(self, post_key):
        return self.directory / (hashlib.sha256(post_key.encode("utf-8")).hexdigest() + ".json")

    def load(self, post_key):
        path = self.path_for(post_key)
        if not path.exists():
            return {}
        # Do not replace unreadable or mismatched user data with a blank record.
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("postKey") != post_key:
            raise ValueError("저장된 초안의 글 식별자가 일치하지 않습니다. 원본을 보존했습니다.")
        return data

    def save(self, post_key, snapshot):
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {**snapshot, "postKey": post_key, "updatedAt": time.time(), "schemaVersion": 1}
        fd, name = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path_for(post_key))
        finally:
            if os.path.exists(name):
                os.unlink(name)


class ManualHelperProcessor:
    """One process call waits until the user explicitly chooses next, skip or stop."""
    def __init__(self, config, state_manager=None, stop_event=None, pause_event=None,
                 command_bridge=None, drafts_path=None, **_unused):
        self.config = config
        self.state_mgr = state_manager
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event
        self.command_bridge = command_bridge
        self.drafts = HelperDraftStore(drafts_path or config.get("helper_drafts_path", None))
        self._version = 0
        self._bound_pages = set()
        self._guard_pages = set()
        self._queue = deque()
        self._seen = set()
        self._active = None
        self._snapshot = {}
        self._storage_error = None

    @staticmethod
    def accepts(command, post_key, navigation_version, seen):
        request_id = command.get("requestId")
        if (command.get("postKey") != post_key or command.get("navigationVersion") != navigation_version
                or not isinstance(request_id, str) or not request_id or len(request_id) > 128
                or request_id in seen):
            return False
        seen.add(request_id)
        return True

    def _save(self, snapshot, *, allow_submitted=False):
        if not isinstance(snapshot, dict):
            return
        clean = {name: snapshot.get(name, "") for name in ("answer", "excerpt", "suffix", "inserted", "nativeText")}
        if not all(isinstance(value, str) and len(value) <= 200000 for value in clean.values()):
            return
        clean["status"] = snapshot.get("status", "none")
        if clean["status"] == "submitted" and not allow_submitted:
            clean["status"] = "unknown"
        clean["pending"] = snapshot.get("pending")
        if clean["status"] not in {state.value for state in CommentSubmitState}:
            clean["status"] = "unknown"
        self._snapshot = clean
        try:
            self.drafts.save(self._active["post"].key, clean)
            self._storage_error = None
        except (OSError, ValueError, TypeError):
            self._storage_error = "초안을 디스크에 저장하지 못했습니다. 중지 전에 내용을 별도로 복사해 주세요."

    def _binding(self, source, command):
        active = self._active
        if not active or not isinstance(command, dict):
            return False
        if source.get("page") != active["page"] or source.get("frame") != active["page"].main_frame:
            return False
        if not self.accepts(command, active["post"].key, self._version, self._seen):
            return False
        action = command.get("action")
        # A page may request a visual refresh with arbitrary fields. Only an
        # observed, newly identified native comment can advance submission state.
        snapshot = command.get("snapshot")
        if action == "observed_submit":
            pending = snapshot.get("pending", {}) if isinstance(snapshot, dict) else {}
            observed = pending.get("observed", {}) if isinstance(pending, dict) else {}
            valid_observation = (isinstance(observed, dict) and observed.get("id") and
                                 observed.get("text") == pending.get("text"))
            if valid_observation:
                self._save(snapshot, allow_submitted=True)
            else:
                return False
        elif action in ("save", "native_submit", "insert"):
            # Navigation controls carry a UI snapshot too, but that snapshot
            # may be empty on a newly rendered locked panel. Never save it.
            self._save(snapshot)
        if action != "save":
            self._queue.append(command)
        return True

    def _install(self, page, post, context, locked=False):
        if page not in self._bound_pages:
            page.expose_binding("__naverHelperCommand", self._binding)
            self._bound_pages.add(page)
        page.evaluate((ASSETS / "helper_policy.js").read_text(encoding="utf-8"))
        state = LikeState.UNKNOWN
        if not locked:
            state = LikeTransactionService.resolve_like_state(page).state
        tokens = json.loads((ROOT / "ui" / "tokens.json").read_text(encoding="utf-8"))
        # The caller has just run TargetPostGuard on the initial page. Once a
        # navigation is detected, `locked` is permanent for this post call.
        same_target = not locked
        page.evaluate((ASSETS / "helper_panel.js").read_text(encoding="utf-8"), {
            "postKey": post.key, "navigationVersion": self._version, "url": page.url,
            # Never put the previous post's title/body/draft into an arbitrary
            # page. A locked panel contains only navigation controls and status.
            "title": context.title if same_target else "",
            "excerpt": context.excerpt if same_target else "",
            "policy": POLICY, "tokens": tokens,
            "draft": self._snapshot if same_target else {}, "locked": locked or not same_target,
            "likeState": state.value if same_target else LikeState.UNKNOWN.value,
        })

    def _response(self, page, **values):
        payload = {"postKey": self._active["post"].key, "navigationVersion": self._version, **values}
        if self._storage_error:
            payload["message"] = self._storage_error
        page.evaluate("data => window.__naverHelper && window.__naverHelper.response(data)", payload)

    def _insert(self, page, post, command):
        if self.stop_event.is_set() or (self.pause_event and self.pause_event.is_set()):
            self._response(page, message="중지 또는 일시정지 상태에서는 댓글 입력을 실행하지 않습니다.")
            return
        TargetPostGuard.verify(page, post)
        if self._snapshot.get("status") in ("unknown", "submitted"):
            self._response(page, message="등록 여부가 불명확하거나 등록 기록이 있어 다시 입력하지 않습니다.")
            return
        snapshot = command.get("snapshot", {})
        checked = validate_comment(snapshot.get("answer", ""), snapshot.get("suffix", ""))
        if not checked["valid"]:
            self._response(page, message="\n".join(checked["reasons"]))
            return
        if self.stop_event.is_set() or (self.pause_event and self.pause_event.is_set()):
            self._response(page, message="중지 또는 일시정지 상태에서는 댓글 입력을 실행하지 않습니다.")
            return
        inserted = page.evaluate("data => window.__naverHelper ? window.__naverHelper.insert(data) : {ok:false,reason:'panel_missing'}", {
            "postKey": post.key, "navigationVersion": self._version, "text": checked["text"],
            "previousText": command.get("previousText"),
        })
        if inserted.get("ok"):
            self._snapshot.update(status="drafted", inserted=checked["text"])
            pending = self._snapshot.get("pending") or {}
            observed = pending.get("observed") or {}
            trusted = (self._snapshot.get("status") == "submitted" and
                       observed.get("id") and observed.get("text") == pending.get("text"))
            self._save(self._snapshot, allow_submitted=bool(trusted))
            self._response(page, status="drafted", inserted=checked["text"], message="댓글창에 초안만 넣었습니다. 내용을 확인한 뒤 네이버 등록 버튼을 직접 누르세요.")
        else:
            self._response(page, message="입력하지 않았습니다. 페이지나 초안·기존 입력이 바뀌었거나 댓글창을 확인할 수 없습니다.")

    def _like(self, page, post):
        if self.stop_event.is_set() or (self.pause_event and self.pause_event.is_set()):
            self._response(page, message="중지 또는 일시정지 상태에서는 공감을 실행하지 않습니다.")
            return LikeProcessResult(state_before=LikeState.UNKNOWN, state_after=LikeState.UNKNOWN, eligibility_reason="cancelled_before_like")
        TargetPostGuard.verify(page, post)
        before = LikeTransactionService.resolve_like_state(page)
        if (before.state != LikeState.NOT_LIKED or before.confidence != LikeConfidence.HIGH
                or LikeCircuitBreaker.is_open()):
            self._response(page, likeState=before.state.value, message="공감 상태가 이미 선택되었거나 불명확하여 클릭하지 않았습니다.")
            return LikeProcessResult(state_before=before.state, state_after=before.state, eligibility_reason="manual_precondition_blocked")
        if self.stop_event.is_set() or (self.pause_event and self.pause_event.is_set()):
            self._response(page, message="중지 또는 일시정지 상태에서는 공감을 실행하지 않습니다.")
            return LikeProcessResult(state_before=before.state, state_after=LikeState.UNKNOWN, eligibility_reason="cancelled_before_like")
        # Atomic same-document recheck + visible exact reaction only. Never click a summary opener.
        action = page.evaluate("data => window.__naverHelper ? window.__naverHelper.like(data) : {ok:false}", {
            "postKey": post.key, "navigationVersion": self._version,
        })
        if not action.get("ok"):
            self._response(page, likeState="unknown", message="공감 선택지를 안전하게 확인할 수 없어 클릭하지 않았습니다. 필요하면 네이버에서 직접 공감 메뉴를 열고 상태를 다시 확인하세요.")
            return LikeProcessResult(state_before=before.state, state_after=LikeState.UNKNOWN, eligibility_reason="manual_option_unavailable")
        # Observe only after the one user-authorized click; no retry, toggle or other mutation.
        after = LikeState.UNKNOWN
        until = time.monotonic() + 2
        while time.monotonic() < until and not self.stop_event.is_set():
            if self._active["navigated"] or page.url != self._active["url"]:
                break
            after = LikeTransactionService.resolve_like_state(page).state
            if after == LikeState.LIKED:
                break
            page.wait_for_timeout(100)
        if after != LikeState.LIKED:
            after = LikeState.UNKNOWN
            LikeCircuitBreaker.trip("manual_like_unverified")
        if not self._active["navigated"]:
            self._response(page, likeState=after.value, message="공감 선택을 확인했습니다." if after == LikeState.LIKED else "공감 결과를 확인하지 못했습니다. 재시도하지 않습니다.")
        return LikeProcessResult(state_before=before.state, action_taken=True, state_after=after)

    def process(self, detail_page, post, action_plan=None):
        page = detail_page
        if page not in self._guard_pages:
            page.add_init_script(path=str(ASSETS / "helper_keyboard.js"))
            self._guard_pages.add(page)
        if self.state_mgr:
            self.state_mgr.update(new_state=FeedState.OPENING_POST, post=post, message="게시글을 여는 중입니다. 자동 댓글·공감은 실행하지 않습니다.")
        page.goto(post.url, wait_until="domcontentloaded", timeout=30000)
        TargetPostGuard.verify(page, post)
        post.title = MobileDOMResolver.get_post_title(page) or post.title or ""
        context = ContentContextExtractor.extract(page, post, max_chars=POLICY["maxBodyLength"])
        post.title, post.excerpt = context.title, context.excerpt
        self._version += 1
        self._queue.clear()
        self._seen.clear()
        self._snapshot = self.drafts.load(post.key) or {
            "excerpt": context.excerpt, "answer": "",
            "suffix": str(self.config.get("fixed_suffix", "")) if self.config.get("append_fixed_suffix_to_ai", False) else "",
            "status": "none", "inserted": "", "pending": None,
        }
        # A prior page cannot self-assert a successful submission. Keep a
        # submitted record only when it contains the observed id and text.
        previous_pending = self._snapshot.get("pending") or {}
        if self._snapshot.get("status") == "submitted" and not (
            isinstance(previous_pending.get("observed"), dict)
            and previous_pending.get("observed", {}).get("id")
            and previous_pending.get("observed", {}).get("text") == previous_pending.get("text")
        ):
            self._snapshot["status"] = "unknown"
        self._active = {"page": page, "post": post, "url": page.url, "navigated": False}
        result = PostProcessResult(post=post)
        already_submitted = self._snapshot.get("status") == "submitted"

        def navigation(frame):
            if frame == page.main_frame:
                self._active["navigated"] = True

        page.on("framenavigated", navigation)
        locked = False
        choice = None
        try:
            self._install(page, post, context)
            page.bring_to_front()
            if self.state_mgr:
                self.state_mgr.update(new_state=FeedState.WAITING_USER, message="브라우저 도우미에서 본문 확인 → 프롬프트 복사 → 답변 편집을 진행하세요. 초안은 이 기기에 보존됩니다.",
                                      current_post_title=post.title, current_post_excerpt=post.excerpt,
                                      current_ai_prompt="", ai_clipboard_ready=False)
            while not self.stop_event.is_set():
                if page.is_closed():
                    self.stop_event.set()
                    break
                if self._active["navigated"] or page.url != self._active["url"]:
                    self._version += 1
                    self._queue.clear()
                    self._seen.clear()
                    self._active["url"] = page.url
                    self._active["navigated"] = False
                    locked = True
                    self._install(page, post, context, locked=True)
                if not locked:
                    page.evaluate("() => window.__naverHelper && window.__naverHelper.observe()")
                if self._storage_error:
                    self._response(page)
                # Old unscoped clipboard commands are deliberately ignored by this manual runtime.
                if self.command_bridge:
                    external = self.command_bridge.pop_command()
                    if external and external.kind == WorkerCommandType.HELPER_ACTION:
                        data = {"action": external.action, "postKey": external.post_key,
                                "requestId": external.request_id, "navigationVersion": external.navigation_version}
                        if self.accepts(data, post.key, self._version, self._seen) and external.action in ("next", "skip", "stop", "open_chatgpt"):
                            self._queue.append(data)
                while self._queue:
                    command = self._queue.popleft()
                    action = command.get("action")
                    if command.get("navigationVersion") != self._version:
                        continue
                    if action in ("next", "skip", "stop"):
                        choice = action
                        if action == "stop":
                            self.stop_event.set()
                        break
                    if action == "open_chatgpt":
                        # Keep authentication outside the managed automation
                        # browser so Google sees the user's normal Chrome.
                        webbrowser.open("https://chatgpt.com/", new=2)
                        self._response(page, message="기본 브라우저에서 ChatGPT를 열었습니다. 로그인 후 답변을 붙여넣으세요.")
                        continue
                    if locked or (self.pause_event and self.pause_event.is_set()):
                        self._response(page, message="페이지 변경 또는 일시정지 상태에서는 입력과 공감을 실행하지 않습니다.")
                        continue
                    if self.stop_event.is_set():
                        choice = "stop"
                        break
                    if page.url != self._active["url"] or self._active["navigated"]:
                        continue
                    if action == "insert":
                        self._insert(page, post, command)
                    elif action == "like":
                        result.like_result = self._like(page, post)
                    elif action == "check_like":
                        state = LikeTransactionService.resolve_like_state(page).state
                        self._response(page, likeState=state.value, message="현재 공감 상태를 다시 확인했습니다. 상태 불명은 클릭하지 않습니다.")
                if choice:
                    break
                page.wait_for_timeout(100)
        finally:
            try:
                if not page.is_closed() and not locked:
                    snapshot = page.evaluate("() => window.__naverHelper && window.__naverHelper.snapshot()")
                    if snapshot:
                        pending = snapshot.get("pending") or {}
                        observed = pending.get("observed") or {}
                        trusted = (snapshot.get("status") == "submitted" and
                                   observed.get("id") and
                                   observed.get("text") == pending.get("text"))
                        self._save(snapshot, allow_submitted=bool(trusted))
                    page.evaluate("() => window.__naverHelper && window.__naverHelper.dispose()")
                page.remove_listener("framenavigated", navigation)
            except Exception:
                pass
            pending = self._snapshot.get("pending") or {}
            observed = pending.get("observed") or {}
            trusted = (self._snapshot.get("status") == "submitted" and
                       observed.get("id") and observed.get("text") == pending.get("text"))
            self._save(self._snapshot, allow_submitted=bool(trusted))
            self._active = None
        if self._storage_error:
            raise FatalSessionError(self._storage_error)
        status = self._snapshot.get("status", "none")
        if choice == "skip" and status not in ("submitted", "unknown"):
            status = "skipped"
        elif status not in ("submitted", "unknown", "drafted"):
            status = "drafted" if self._snapshot.get("answer") else "skipped" if choice in ("next", "skip") else "none"
        pending = self._snapshot.get("pending") or {}
        result.comment_result = CommentProcessResult(
            status=CommentSubmitState(status), draft_text=self._snapshot.get("nativeText") or self._snapshot.get("answer") or None,
            submitted_text=pending.get("text") if status == "submitted" else None,
            error=self._storage_error or ("manual_submission_unverified" if status == "unknown" else None),
        )
        if self.state_mgr:
            self.state_mgr.update(inc_processed=True, inc_like=result.like_result.state_after == LikeState.LIKED and result.like_result.action_taken,
                                  inc_comment=status == "submitted" and not already_submitted, inc_skip=choice == "skip")
        return result
