import os
import re
import json
import hashlib
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page
from app.models import FeedPost
from src.logger import logger

ACCUMULATED_COMMENTS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "accumulated_visited_comments.json"))

MACRO_FILTER = re.compile(
    r'(잘\s*보고\s*갑니다|서이추|이웃\s*신청|포스팅\s*잘|하트\s*꾹|답방|맞방|소통\s*해요|오늘도\s*좋은\s*하루|'
    r'좋은\s*글\s*감사|유익한\s*정보|공감하고\s*가요|서로이웃)',
    re.IGNORECASE
)

NICK_REGEX = re.compile(r'@[\w\d_]+')
EMAIL_REGEX = re.compile(r'[\w\.-]+@[\w\.-]+')
PHONE_REGEX = re.compile(r'01[016789]-?\d{3,4}-?\d{4}')


class VisitedCommentCollector:
    """
    작업 중 진입한 실제 네이버 블로그 글의 기존 댓글들을 비식별화하여 자동으로 누적 수집하는 서비스:
    - 매크로 배제, 닉네임/개인정보 완전 마스킹
    - 본문 앵커 매핑 후 accumulated_visited_comments.json에 저장
    """

    @staticmethod
    def scrub_privacy(text: str) -> str:
        t = NICK_REGEX.sub("", text)
        t = EMAIL_REGEX.sub("", t)
        t = PHONE_REGEX.sub("", t)
        return t.strip()

    @classmethod
    def collect_from_page(cls, page: Page, post: FeedPost) -> int:
        try:
            raw_comments = page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    return items.map(el => {
                        const txtEl = el.querySelector(".u_cbox_contents, .u_cbox_text_mention, p.text");
                        return txtEl ? txtEl.innerText.trim() : "";
                    }).filter(t => t.length >= 8);
                }
            """)

            if not raw_comments:
                return 0

            post_hash = hashlib.sha256(post.url.encode('utf-8')).hexdigest()[:12]
            valid_entries = []

            for c in raw_comments:
                clean_txt = cls.scrub_privacy(c)
                if len(clean_txt) < 8 or len(clean_txt) > 200:
                    continue
                if MACRO_FILTER.search(clean_txt):
                    continue
                if "http://" in clean_txt or "https://" in clean_txt:
                    continue

                valid_entries.append({
                    "post_id_hash": post_hash,
                    "post_title": post.title or "",
                    "comment_text": clean_txt,
                    "length": len(clean_txt)
                })

            if not valid_entries:
                return 0

            os.makedirs(os.path.dirname(ACCUMULATED_COMMENTS_FILE), exist_ok=True)
            existing = []
            if os.path.exists(ACCUMULATED_COMMENTS_FILE):
                try:
                    with open(ACCUMULATED_COMMENTS_FILE, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            existing_hashes = {e.get("comment_text") for e in existing}
            new_added = 0
            for item in valid_entries:
                if item["comment_text"] not in existing_hashes:
                    existing.append(item)
                    existing_hashes.add(item["comment_text"])
                    new_added += 1

            if new_added > 0:
                with open(ACCUMULATED_COMMENTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                logger.log(f"  📚 [CORPUS] 현재 글의 우수 댓글 {new_added}개를 학습 코퍼스(accumulated_visited_comments.json)에 자동 누적했습니다.")

            return new_added
        except Exception as e:
            return 0
