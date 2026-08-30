import re
import threading
import hashlib
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from playwright.sync_api import Page, Locator
from services.like_transaction import LikeConfidence
from browser.session import interruptible_wait
from src.logger import logger


class CommentPresenceState(str, Enum):
    PRESENT = "present"     # 내 댓글이 이미 존재함 (작성 스킵)
    ABSENT = "absent"       # 내 댓글이 확실히 없음 (작성 가능)
    UNKNOWN = "unknown"     # 불완전한 목록 등으로 판별 불가 (Fail-closed 기본 스킵 권장)


@dataclass
class CommentPresenceResult:
    state: CommentPresenceState
    confidence: LikeConfidence
    comment_no: Optional[str] = None
    comment_text: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    loaded_comment_count: int = 0
    total_comment_count: Optional[int] = None
    list_complete: bool = True


@dataclass(frozen=True)
class CommentSubmissionBaseline:
    mine_comment_nos: set[str]
    mine_text_hashes: set[str]
    captured_at: float


class ServerCommentDuplicateGuard:
    """
    서버 사이드 중복 댓글 방지 가드 (Server-Side Duplicate Comment Guard)
    - 실제 네이버 Cbox 댓글 DOM 목록을 실시간 스캔
    - 1순위 Strong Signal: data-info 속성의 mine:true 정규식 일치
    - 2순위 Strong Signal: exact class 'u_cbox_type_mine'
    - Lazy-load / Pagination 인식: 부분 목록 스캔 시 조기 ABSENT 판정 금지 (Fail-Closed)
    """

    MINE_REGEX = re.compile(r'(?:^|[,{\s])mine\s*:\s*true(?:[,}\s]|$)', re.IGNORECASE)

    @staticmethod
    def _text_hash(value: str) -> str:
        normalized = re.sub(r"\s+", " ", (value or "").strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def capture_submission_baseline(cls, page: Page) -> CommentSubmissionBaseline:
        """Capture own comment identifiers/text before a submit click."""
        try:
            rows = page.evaluate("() => window.__NAVER_COMMENT_SUBMISSION_BASELINE__ || null")
            if rows is None:
                rows = page.evaluate("""
                () => Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"))
                  .filter(item => /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/i.test(item.getAttribute('data-info') || '') ||
                                  (item.className || '').split(/\\s+/).includes('u_cbox_type_mine'))
                  .map(item => ({
                    info: item.getAttribute('data-info') || '',
                    text: (item.querySelector('.u_cbox_contents, .u_cbox_text_mention, p.text')?.innerText || '').trim()
                  }))
                """)
        except Exception:
            rows = []
        numbers = set()
        hashes = set()
        for row in rows or []:
            match = re.search(r"commentNo\s*:\s*['\"]?([0-9]+)", row.get("info", ""), re.I)
            if match:
                numbers.add(match.group(1))
            if row.get("text"):
                hashes.add(cls._text_hash(row["text"]))
        return CommentSubmissionBaseline(numbers, hashes, time.time())

    @classmethod
    def scan_page_for_my_comment(
        cls,
        page: Page,
        max_scroll_attempts: int = 3,
        stop_event: Optional[threading.Event] = None,
        baseline: Optional[CommentSubmissionBaseline] = None,
        expected_text: Optional[str] = None,
    ) -> CommentPresenceResult:
        if not page:
            return CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, evidence=["page_none"])

        try:
            # 1. 댓글 총 개수 및 에디터 가시성 확인
            header_info = page.evaluate("""
                () => {
                    const totalEl = document.querySelector(".u_cbox_count, .u_cbox_header .u_cbox_info .u_cbox_count, .Interact__num--_KPO5");
                    let totalCount = null;
                    if (totalEl) {
                        const raw = (totalEl.innerText || "").replace(/[^0-9]/g, '');
                        if (raw) totalCount = parseInt(raw, 10);
                    }
                    const editor = document.querySelector("#naverComment__write_textarea, .u_cbox_write_box");
                    return {
                        totalCount: totalCount,
                        hasEditor: !!editor
                    };
                }
            """)

            total_count = header_info.get("totalCount")
            has_editor = header_info.get("hasEditor", False)

            if total_count == 0 and has_editor:
                return CommentPresenceResult(
                    state=CommentPresenceState.ABSENT,
                    confidence=LikeConfidence.HIGH,
                    evidence=["total_count_zero_and_editor_ready"],
                    loaded_comment_count=0,
                    total_comment_count=0,
                    list_complete=True
                )

            # 2. Bounded Exhaustive Scan (댓글 목록 탐색)
            for attempt in range(max_scroll_attempts + 1):
                if stop_event and stop_event.is_set():
                    break

                scan_res = page.evaluate("""
                    () => {
                        const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                        let foundMine = false;
                        let foundCommentNo = null;
                        let foundText = null;
                        const evidence = [];

                        for (const item of items) {
                            const dataInfo = item.getAttribute("data-info") || "";
                            const clsList = (item.className || "").split(/\\s+/);

                            // Strong Signal 1: data-info regex mine:true
                            const isMineDataInfo = /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/i.test(dataInfo);
                            // Strong Signal 2: exact class u_cbox_type_mine
                            const isMineClass = clsList.includes("u_cbox_type_mine");

                            if (isMineDataInfo || isMineClass) {
                                foundMine = true;
                                const contentEl = item.querySelector(".u_cbox_contents, .u_cbox_text_mention, p.text");
                                foundText = contentEl ? contentEl.innerText.trim() : "";
                                const noMatch = dataInfo.match(/commentNo\\s*:\\s*['"]?([0-9]+)['"]?/i);
                                foundCommentNo = noMatch ? noMatch[1] : null;

                                if (isMineDataInfo) evidence.push("data_info_mine_true");
                                if (isMineClass) evidence.push("class_u_cbox_type_mine");
                            }
                        }

                        // 더보기 버튼 확인
                        const moreBtn = document.querySelector(".u_cbox_btn_more, button[data-action*='paginate'], a.u_cbox_paginate_next");
                        const hasMore = moreBtn ? (moreBtn.offsetParent !== null && !moreBtn.disabled) : false;

                        return {
                            foundMine: foundMine,
                            foundCommentNo: foundCommentNo,
                            foundText: foundText,
                            evidence: evidence,
                            loadedCount: items.length,
                            hasMore: hasMore
                        };
                    }
                """)

                # 제출 후에는 기존 mine 댓글이 아니라 새 댓글 + 정확한 본문이어야 한다.
                candidate_no = scan_res.get("foundCommentNo")
                candidate_hash = cls._text_hash(scan_res.get("foundText", ""))
                if baseline is not None:
                    is_new = (candidate_no and candidate_no not in baseline.mine_comment_nos) or (
                        candidate_hash and candidate_hash not in baseline.mine_text_hashes
                    )
                    text_matches = not expected_text or re.sub(r"\s+", " ", scan_res.get("foundText", "").strip()) == re.sub(r"\s+", " ", expected_text.strip())
                    scan_res["baseline_match"] = bool(scan_res.get("foundMine") and not is_new)
                    if not (scan_res.get("foundMine") and is_new and text_matches):
                        scan_res["foundMine"] = False

                # Strong Signal 발견 시 즉시 PRESENT 반환
                if scan_res.get("foundMine"):
                    logger.log(f"  🛑 [SERVER_GUARD] 서버 댓글 목록에서 내 댓글 발견! ({scan_res.get('evidence')}) - 중복 작성 차단")
                    return CommentPresenceResult(
                        state=CommentPresenceState.PRESENT,
                        confidence=LikeConfidence.HIGH,
                        comment_no=scan_res.get("foundCommentNo"),
                        comment_text=scan_res.get("foundText"),
                        evidence=scan_res.get("evidence"),
                        loaded_comment_count=scan_res.get("loadedCount", 0),
                        total_comment_count=total_count,
                        list_complete=True
                    )

                loaded_count = scan_res.get("loadedCount", 0)
                has_more = scan_res.get("hasMore", False)

                # 더 이상 로드할 댓글이 없거나, 이미 전체 댓글 수 이상 로드된 경우
                if not has_more or (total_count is not None and loaded_count >= total_count):
                    if baseline is not None and scan_res.get("baseline_match"):
                        return CommentPresenceResult(
                            state=CommentPresenceState.UNKNOWN,
                            confidence=LikeConfidence.MEDIUM,
                            evidence=["existing_mine_comment_only"],
                            loaded_comment_count=loaded_count,
                            total_comment_count=total_count,
                            list_complete=True,
                        )
                    return CommentPresenceResult(
                        state=CommentPresenceState.ABSENT,
                        confidence=LikeConfidence.HIGH,
                        evidence=["full_list_scanned_no_mine"],
                        loaded_comment_count=loaded_count,
                        total_comment_count=total_count,
                        list_complete=True
                    )

                # 더보기 버튼 클릭 시도
                if attempt < max_scroll_attempts and has_more:
                    more_btn = page.locator(".u_cbox_btn_more, a.u_cbox_paginate_next").first
                    if more_btn and more_btn.count() > 0:
                        try:
                            more_btn.click(timeout=1000)
                            interruptible_wait(stop_event, 0.5)
                        except Exception:
                            pass

            # 끝까지 확인하지 못하고 부분 목록만 남은 경우 (Fail-Closed UNKNOWN)
            logger.log("  ⚠️ [SERVER_GUARD] 댓글 목록 일부만 스캔되어 내 댓글 유무가 불확실합니다 (UNKNOWN).", "WARNING")
            return CommentPresenceResult(
                state=CommentPresenceState.UNKNOWN,
                confidence=LikeConfidence.MEDIUM,
                evidence=["partial_list_unconfirmed"],
                loaded_comment_count=loaded_count,
                total_comment_count=total_count,
                list_complete=False
            )

        except Exception as e:
            logger.log(f"[SERVER_GUARD] 댓글 스캔 중 예외: {e}", "WARNING")
            return CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence=LikeConfidence.UNKNOWN, evidence=[f"exception: {e}"])
