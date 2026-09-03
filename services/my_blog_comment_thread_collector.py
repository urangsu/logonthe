import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Literal
from playwright.sync_api import Page
from browser.session import interruptible_wait
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
        """기본 생성 대상: top-level 외부 댓글 AND secret/deleted 아님 AND 작성자 답글 없음"""
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


class MyBlogCommentThreadCollector:
    """
    내 블로그 글의 댓글 스레드를 전수 수집하고 계층 관계를 분석하는 서비스:
    - PostView 페이지 진입 후 댓글 레이어 오픈
    - 댓글 더보기 반복 클릭하여 전체 로드
    - 표시된 총 댓글 수와 로드된 수 비교하여 COMPLETE / PARTIAL 판정
    - 댓글 간 부모-자식(parent_comment_no) 관계 및 작성자 기존 답글(existing_author_reply) 연결
    """

    @classmethod
    def collect_threads(
        cls,
        page: Page,
        blog_id: str,
        log_no: str,
        stop_event: Optional[Any] = None,
        max_more_clicks: int = 20,
    ) -> Tuple[List[BlogCommentNode], Literal["complete", "partial", "failed"], Optional[int]]:
        url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
        try:
            logger.log(f"[REPLY_COLLECTOR] 글 진입 및 댓글 스레드 수집 시작: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 1.2)

            # 1. 댓글 버튼 클릭하여 레이어 오픈
            cmt_btn = page.locator(
                "button[data-click-area*='.re'], button.Interact__comment_btn--Wbuoq, button:has(.blind:text-is('댓글'))"
            ).first
            if cmt_btn.count() > 0:
                try:
                    cmt_btn.click(timeout=2000)
                    interruptible_wait(stop_event, 1.2)
                except Exception:
                    pass

            # 2. 더보기 반복 클릭하여 전체 로드 (fingerprint 불변 감지)
            has_remaining_more = False
            for _ in range(max_more_clicks):
                if stop_event and stop_event.is_set():
                    break
                more_btn = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more, .u_cbox_paginate a").first
                if more_btn.count() > 0 and more_btn.is_visible():
                    try:
                        cur_count = page.locator("li.u_cbox_comment, li[class*='cbox_comment']").count()
                        more_btn.click(timeout=1500)
                        interruptible_wait(stop_event, 0.8)
                        new_count = page.locator("li.u_cbox_comment, li[class*='cbox_comment']").count()
                        if new_count <= cur_count:
                            # fingerprint 불변 (더 이상 추가 로드 안 됨)
                            break
                    except Exception:
                        break
                else:
                    break

            # 최종 더보기 잔존 여부 확인
            final_more = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more, .u_cbox_paginate a").first
            has_remaining_more = bool(final_more.count() > 0 and final_more.is_visible())

            # 3. DOM 평가하여 원시 댓글 데이터 추출
            eval_result = page.evaluate("""
                (myBlogId) => {
                    const countEl = document.querySelector(".u_cbox_count, .count, em.point");
                    let displayedCount = null;
                    if (countEl) {
                        const m = countEl.innerText.match(/\\d+/);
                        if (m) displayedCount = parseInt(m[0], 10);
                    }

                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    const rawList = [];

                    for (let idx = 0; idx < items.length; idx++) {
                        const el = items[idx];
                        const dataInfo = el.getAttribute("data-info") || "";

                        // commentNo 추출
                        let commentNo = el.getAttribute("data-comment-no") || "";
                        let parentCommentNo = el.getAttribute("data-parent-comment-no") || "";
                        if (!commentNo && dataInfo) {
                            const m = dataInfo.match(/commentNo\\s*:\\s*['"]?(\\d+)['"]?/);
                            if (m) commentNo = m[1];
                            const pm = dataInfo.match(/parentCommentNo\\s*:\\s*['"]?(\\d+)['"]?/);
                            if (pm) parentCommentNo = pm[1];
                        }
                        if (!commentNo) {
                            commentNo = "dom_" + idx;
                        }

                        const isReply = el.classList.contains("u_cbox_type_reply") ||
                                        el.closest(".u_cbox_reply_area") !== null ||
                                        Boolean(parentCommentNo);

                        const isMine = el.className.includes("type_mine") ||
                                       /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/.test(dataInfo);

                        const isAuthor = el.querySelector(".u_cbox_ico_editor, .u_cbox_ico_writer, .u_cbox_ico_owner") !== null;

                        const isSecret = el.querySelector(".u_cbox_secret_contents, .u_cbox_ico_secret") !== null ||
                                         el.innerText.includes("비밀 댓글입니다");

                        const isDeleted = el.innerText.includes("삭제된 댓글입니다") ||
                                          el.querySelector(".u_cbox_deleted") !== null;

                        const userLink = el.querySelector(".u_cbox_info a, a.u_cbox_name, .u_cbox_nick a, a[href*='blog.naver.com']");
                        const nickEl = el.querySelector(".u_cbox_nick, .u_cbox_name");
                        const textEl = el.querySelector(".u_cbox_contents, .u_cbox_text_main");
                        const dateEl = el.querySelector(".u_cbox_date");

                        let targetId = "";
                        if (userLink) {
                            const href = userLink.href || "";
                            if (href.includes("blogId=")) {
                                const m = href.match(/blogId=([a-zA-Z0-9_-]+)/);
                                if (m) targetId = m[1];
                            } else if (href.includes("m.blog.naver.com/")) {
                                const part = href.split("m.blog.naver.com/")[1];
                                targetId = part.split("?")[0].split("/")[0];
                            } else if (href.includes("blog.naver.com/")) {
                                const part = href.split("blog.naver.com/")[1];
                                targetId = part.split("?")[0].split("/")[0];
                            }
                        }

                        // blog_id가 글 작성자 본인과 일치하면 isAuthor=True
                        const authorFinal = isAuthor || (Boolean(targetId) && Boolean(myBlogId) && targetId.toLowerCase() === myBlogId.toLowerCase());

                        rawList.push({
                            comment_no: String(commentNo),
                            parent_comment_no: parentCommentNo ? String(parentCommentNo) : null,
                            blog_id: targetId || null,
                            nickname: nickEl ? nickEl.innerText.trim() : "",
                            text: textEl ? textEl.innerText.trim() : "",
                            date: dateEl ? dateEl.innerText.trim() : "",
                            is_mine: isMine,
                            is_author: authorFinal,
                            is_reply: isReply,
                            is_secret: isSecret,
                            is_deleted: isDeleted
                        });
                    }

                    return {
                        displayedCount: displayedCount,
                        comments: rawList
                    };
                }
            """, blog_id)

            displayed_count = eval_result.get("displayedCount")
            raw_comments = eval_result.get("comments", [])

            # 4. 계층 구조화 및 부모-자식 답글 연결
            top_level_nodes: List[BlogCommentNode] = []
            node_map: Dict[str, BlogCommentNode] = {}

            # 1차 패스: 노드 생성
            for raw in raw_comments:
                node = BlogCommentNode(
                    comment_no=raw["comment_no"],
                    parent_comment_no=raw["parent_comment_no"],
                    blog_id=raw["blog_id"],
                    nickname=raw["nickname"],
                    text=raw["text"],
                    date=raw["date"],
                    is_mine=raw["is_mine"],
                    is_author=raw["is_author"],
                    is_reply=raw["is_reply"],
                    is_secret=raw["is_secret"],
                    is_deleted=raw["is_deleted"],
                )
                node_map[node.comment_no] = node

            # 2차 패스: 부모-대댓글 트리 연결 및 existing_author_reply 검출
            for node in node_map.values():
                p_no = node.parent_comment_no
                if p_no and p_no in node_map and p_no != node.comment_no:
                    parent = node_map[p_no]
                    parent.replies.append(node)
                    node.root_comment_no = parent.root_comment_no or parent.comment_no
                    if (node.is_author or node.is_mine) and node.text:
                        parent.existing_author_reply = node.text
                elif not node.is_reply:
                    top_level_nodes.append(node)
                else:
                    # parentCommentNo가 명시되지 않은 대댓글의 경우, 직전 top-level 노드에 귀속
                    if top_level_nodes:
                        last_parent = top_level_nodes[-1]
                        last_parent.replies.append(node)
                        node.parent_comment_no = last_parent.comment_no
                        node.root_comment_no = last_parent.comment_no
                        if (node.is_author or node.is_mine) and node.text:
                            last_parent.existing_author_reply = node.text
                    else:
                        top_level_nodes.append(node)

            loaded_count = len(raw_comments)
            audit_status: Literal["complete", "partial", "failed"] = "complete"
            if has_remaining_more:
                audit_status = "partial"
            elif displayed_count is not None and loaded_count < displayed_count:
                audit_status = "partial"

            logger.log(
                f"[REPLY_COLLECTOR] 수집 완료: status={audit_status} "
                f"loaded={loaded_count}/{displayed_count or '?'} top_level={len(top_level_nodes)}"
            )
            return top_level_nodes, audit_status, displayed_count

        except Exception as e:
            logger.log(f"[REPLY_COLLECTOR] 수집 실패: {e}", "ERROR")
            return [], "failed", None
