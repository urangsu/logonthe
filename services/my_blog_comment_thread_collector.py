import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Literal
from playwright.sync_api import Page
from browser.session import interruptible_wait
from naver.interaction import CommentInteractionService
from src.logger import logger


@dataclass
class BlogCommentNode:
    comment_no: str
    parent_comment_no: Optional[str] = None
    root_comment_no: Optional[str] = None
    blog_id: Optional[str] = None
    nickname: str = ""
    text: str = ""
    date: str = ""
    is_mine: bool = False
    is_author: bool = False
    is_reply: bool = False
    is_secret: bool = False
    is_deleted: bool = False
    identity_valid: bool = True  # P0-5: comment_no 실제 확보 여부
    author_replies: List[str] = field(default_factory=list)  # 작성자의 모든 답글 리스트
    replies: List["BlogCommentNode"] = field(default_factory=list)

    @property
    def existing_author_reply(self) -> Optional[str]:
        return self.author_replies[0] if self.author_replies else None

    @existing_author_reply.setter
    def existing_author_reply(self, val: Optional[str]):
        if val and val not in self.author_replies:
            self.author_replies.append(val)

    @property
    def is_eligible_for_auto_reply(self) -> bool:
        """기본 생성 대상: identity_valid AND top-level 외부 댓글 AND secret/deleted 아님 AND 작성자 답글 없음"""
        if not self.identity_valid:
            return False
        if self.is_reply:
            return False
        if self.is_mine or self.is_author:
            return False
        if self.is_secret or self.is_deleted:
            return False
        if self.author_replies:
            return False
        if not self.text.strip():
            return False
        return True


@dataclass
class CommentThreadCollectionResult:
    """P1-5: 댓글 스레드 전수 수집 및 무결성 감사 결과"""
    status: Literal["complete", "partial", "failed"]
    roots: List[BlogCommentNode]
    loaded_total: int
    expected_total: Optional[int]
    unresolved_identity_count: int
    unresolved_parent_count: int
    more_clicks: int
    failure_reason: Optional[str] = None

    def __iter__(self):
        """기존 (nodes, status, expected_total) 3-tuple 언패킹 하위 호환성 지원"""
        return iter((self.roots, self.status, self.expected_total))


class MyBlogCommentThreadCollector:
    """
    내 블로그 글의 댓글 스레드를 전수 수집하고 계층 관계를 분석하는 서비스:
    - CommentInteractionService.open_comment_layer를 통한 신뢰성 있는 레이어 오픈
    - Fingerprint(comment ID set) 불변 감지 기반 더보기 반복 로드
    - 임의 ID 발급 금지 (identity_valid 검증)
    - 임의 부모 추측 금지 (parent_comment_no 엄격 증명)
    - Fail-closed COMPLETE / PARTIAL / FAILED 감사 판정
    """

    @classmethod
    def collect_threads(
        cls,
        page: Page,
        blog_id: str,
        log_no: str,
        stop_event: Optional[Any] = None,
        max_more_clicks: int = 25,
        expected_comment_count: Optional[int] = None,
    ) -> CommentThreadCollectionResult:
        url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
        try:
            cur_url = page.url or ""
            if log_no not in cur_url:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                interruptible_wait(stop_event, 1.5)

            # P0-4: CommentInteractionService.open_comment_layer 재사용
            open_ok, open_reason = CommentInteractionService.open_comment_layer(page, stop_event)
            if not open_ok:
                logger.log(f"[REPLY_COLLECTOR] 댓글 레이어 오픈 실패 ({open_reason})", "WARNING")
                return CommentThreadCollectionResult(
                    status="failed",
                    roots=[],
                    loaded_total=0,
                    expected_total=expected_comment_count,
                    unresolved_identity_count=0,
                    unresolved_parent_count=0,
                    more_clicks=0,
                    failure_reason=open_reason,
                )

            # P1-5: comment_id fingerprint set 기반 더보기 반복 로드
            clicks_done = 0
            has_remaining_more = False

            def get_current_id_set():
                try:
                    return set(page.evaluate("""() => {
                        const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                        return items.map(el => {
                            const info = el.getAttribute("data-info") || "";
                            const m = info.match(/commentNo:'?(\\d+)'?/);
                            return m ? m[1] : (el.id || "");
                        }).filter(Boolean);
                    }"""))
                except Exception:
                    return set()

            known_ids = get_current_id_set()

            for _ in range(max_more_clicks):
                if stop_event and stop_event.is_set():
                    break
                more_btn = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more, .u_cbox_paginate a").first
                if more_btn.count() > 0 and more_btn.is_visible():
                    try:
                        more_btn.click(timeout=1500)
                        clicks_done += 1
                        interruptible_wait(stop_event, 0.8)
                        new_ids = get_current_id_set()
                        if new_ids and new_ids.issubset(known_ids):
                            # fingerprint 변화 없음 (더 이상 새 댓글이 로드되지 않음)
                            break
                        known_ids.update(new_ids)
                    except Exception:
                        break
                else:
                    break

            # 최종 더보기 버튼 잔존 여부 확인
            final_more = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more, .u_cbox_paginate a").first
            has_remaining_more = bool(final_more.count() > 0 and final_more.is_visible())

            # DOM 평가하여 원시 댓글 데이터 추출
            eval_result = page.evaluate("""
                (myBlogId) => {
                    const countEl = document.querySelector(".u_cbox_count, .count, em.point");
                    let displayedCount = null;
                    if (countEl) {
                        const m = countEl.innerText.replace(/,/g, '').match(/\\d+/);
                        if (m) displayedCount = parseInt(m[0], 10);
                    }

                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    const parsed = [];

                    items.forEach((el, index) => {
                        let commentNo = null;
                        let parentCommentNo = null;
                        let isReply = el.classList.contains("u_cbox_type_reply");

                        const dataInfo = el.getAttribute("data-info") || "";
                        if (dataInfo) {
                            const cMatch = dataInfo.match(/commentNo:'?(\\d+)'?/);
                            if (cMatch) commentNo = cMatch[1];

                            const pMatch = dataInfo.match(/parentCommentNo:'?(\\d+)'?/);
                            if (pMatch) {
                                parentCommentNo = pMatch[1];
                                isReply = true;
                            }
                        }

                        if (!commentNo && el.id) {
                            const idMatch = el.id.match(/(\\d+)/);
                            if (idMatch) commentNo = idMatch[1];
                        }

                        const nickEl = el.querySelector(".u_cbox_nick, .nick, strong.user_name");
                        const nickname = nickEl ? nickEl.innerText.trim() : "";

                        const blogLinkEl = el.querySelector("a.u_cbox_name, a[class*='user_']");
                        let commentBlogId = "";
                        if (blogLinkEl) {
                            const href = blogLinkEl.getAttribute("href") || "";
                            const bMatch = href.match(/blog\\.naver\\.com\\/([a-zA-Z0-9_-]+)/);
                            if (bMatch) commentBlogId = bMatch[1];
                        }

                        const contentEl = el.querySelector(".u_cbox_contents, .u_cbox_text_wrap, .content");
                        const text = contentEl ? contentEl.innerText.trim() : "";

                        const dateEl = el.querySelector(".u_cbox_date, .date");
                        const date = dateEl ? dateEl.innerText.trim() : "";

                        const isMine = (commentBlogId && myBlogId && commentBlogId.toLowerCase() === myBlogId.toLowerCase());
                        const isAuthor = el.querySelector(".u_cbox_ico_editor, .u_cbox_ico_writer, .is_writer") !== null || isMine;
                        const isSecret = el.classList.contains("u_cbox_secret") || el.querySelector(".u_cbox_secret") !== null;
                        const isDeleted = el.classList.contains("u_cbox_delete") || el.querySelector(".u_cbox_delete") !== null || (text && text.includes("삭제된 댓글"));

                        parsed.push({
                            commentNo: commentNo,
                            parentCommentNo: parentCommentNo,
                            isReply: isReply,
                            nickname: nickname,
                            blogId: commentBlogId,
                            text: text,
                            date: date,
                            isMine: isMine,
                            isAuthor: isAuthor,
                            isSecret: isSecret,
                            isDeleted: isDeleted,
                            domIndex: index
                        });
                    });

                    return {
                        displayedCount: displayedCount,
                        comments: parsed
                    };
                }
            """, blog_id)

            displayed_count = eval_result.get("displayedCount") or expected_comment_count
            raw_comments = eval_result.get("comments", [])
            loaded_count = len(raw_comments)

            # 노드 빌드 및 P0-5 (Identity 검증) / P0-6 (부모 관계 엄격화)
            nodes_by_id: Dict[str, BlogCommentNode] = {}
            top_level_nodes: List[BlogCommentNode] = []
            unresolved_identity_count = 0
            unresolved_parent_count = 0

            for rc in raw_comments:
                c_no = rc.get("commentNo")
                identity_valid = True

                # P0-5: 임의 ID 발급 금지. 미확보 시 unresolved_identity 처리
                if not c_no:
                    identity_valid = False
                    c_no = f"unresolved_{rc.get('domIndex', 0)}"
                    unresolved_identity_count += 1

                node = BlogCommentNode(
                    comment_no=c_no,
                    parent_comment_no=rc.get("parentCommentNo"),
                    blog_id=rc.get("blogId"),
                    nickname=rc.get("nickname", ""),
                    text=rc.get("text", ""),
                    date=rc.get("date", ""),
                    is_mine=rc.get("isMine", False),
                    is_author=rc.get("isAuthor", False),
                    is_reply=rc.get("isReply", False),
                    is_secret=rc.get("isSecret", False),
                    is_deleted=rc.get("isDeleted", False),
                    identity_valid=identity_valid,
                )
                if identity_valid:
                    nodes_by_id[c_no] = node

                if not node.is_reply:
                    node.root_comment_no = node.comment_no
                    top_level_nodes.append(node)
                else:
                    p_no = node.parent_comment_no
                    if p_no and p_no in nodes_by_id:
                        parent = nodes_by_id[p_no]
                        parent.replies.append(node)
                        node.root_comment_no = parent.root_comment_no or parent.comment_no
                        if (node.is_author or node.is_mine) and node.text:
                            parent.existing_author_reply = node.text
                    else:
                        # P0-6: parent_comment_no가 없거나 부모를 찾을 수 없는 경우 임의 부착 금지
                        unresolved_parent_count += 1
                        node.identity_valid = False  # 답글 생성 대상 제외

            # Fail-closed 감사 판정
            audit_status: Literal["complete", "partial", "failed"] = "complete"

            if displayed_count is not None and displayed_count > 0 and loaded_count == 0:
                audit_status = "failed"
            elif has_remaining_more:
                audit_status = "partial"
            elif displayed_count is not None and loaded_count < displayed_count:
                audit_status = "partial"
            elif unresolved_identity_count > 0 or unresolved_parent_count > 0:
                audit_status = "partial"

            logger.log(
                f"[REPLY_COLLECTOR] 수집 완료: status={audit_status} "
                f"loaded={loaded_count}/{displayed_count or '?'} roots={len(top_level_nodes)} "
                f"unresolved_id={unresolved_identity_count} unresolved_parent={unresolved_parent_count} "
                f"more_clicks={clicks_done}"
            )

            return CommentThreadCollectionResult(
                status=audit_status,
                roots=top_level_nodes,
                loaded_total=loaded_count,
                expected_total=displayed_count,
                unresolved_identity_count=unresolved_identity_count,
                unresolved_parent_count=unresolved_parent_count,
                more_clicks=clicks_done,
            )

        except Exception as e:
            logger.log(f"[REPLY_COLLECTOR] 수집 실패: {e}", "ERROR")
            return CommentThreadCollectionResult(
                status="failed",
                roots=[],
                loaded_total=0,
                expected_total=expected_comment_count,
                unresolved_identity_count=0,
                unresolved_parent_count=0,
                more_clicks=0,
                failure_reason=str(e),
            )
