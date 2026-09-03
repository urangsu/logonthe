import os
import json
import time
import re
from playwright.sync_api import sync_playwright

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "collected_comments_theqoo_muk.json")

def collect_theqoo_comments(target_count=550):
    all_comments = []
    seen_texts = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 1. 핫게시판 글 목록 수집 (여러 페이지 순회)
        post_urls = []
        for page_num in range(1, 10):
            list_url = f"https://theqoo.net/muk?filter_mode=hot&page={page_num}"
            print(f"[LIST] Scraping post list page {page_num}: {list_url}")
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)

                # 공지 제외하고 일반 핫게시글 링크 추출
                links = page.evaluate("""
                    () => {
                        const res = [];
                        const rows = document.querySelectorAll("tbody tr, .bd_lst_wrap tr");
                        for (const tr of rows) {
                            if (tr.classList.contains("notice")) continue;
                            const a = tr.querySelector("td.title a:not(.replyNum), a.hx:not(.replyNum)");
                            const replyA = tr.querySelector("a.replyNum");
                            if (a && a.href && a.href.includes("/muk/")) {
                                const m = a.href.match(/muk\\/(\\d+)/);
                                if (m) {
                                    const replyCount = replyA ? parseInt(replyA.innerText.trim(), 10) || 0 : 0;
                                    res.push({
                                        id: m[1],
                                        url: "https://theqoo.net/muk/" + m[1] + "?filter_mode=hot",
                                        title: a.innerText.trim(),
                                        replyCount: replyCount
                                    });
                                }
                            }
                        }
                        return res;
                    }
                """)

                for item in links:
                    if not any(x["id"] == item["id"] for x in post_urls):
                        post_urls.append(item)

                if len(post_urls) >= 40:
                    break
            except Exception as e:
                print(f"Error fetching list page {page_num}: {e}")

        print(f"Total hot posts found: {len(post_urls)}")

        # 2. 각 게시글 접속하여 댓글 수집
        for post in post_urls:
            if len(all_comments) >= target_count:
                break

            post_id = post["id"]
            post_title = post["title"]
            print(f"\n[POST] {post_title} ({post['url']}) - Expected replies: {post['replyCount']}")

            try:
                page.goto(post["url"], wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)

                # 더쿠는 '댓글 더 보기' 버튼이 있는 경우 클릭
                for _ in range(5):
                    more_btn = page.locator(".show_more.comment_header, .fdb_tag.button, button:has-text('댓글 더 보기')").first
                    if more_btn.count() > 0 and more_btn.is_visible():
                        try:
                            more_btn.click(timeout=1500)
                            page.wait_for_timeout(1000)
                        except Exception:
                            break
                    else:
                        break

                # 댓글 추출
                comments = page.evaluate("""
                    () => {
                        const list = [];
                        // 더쿠 게시판 댓글 셀렉터
                        const items = document.querySelectorAll(".fdb_lst_ul > li, .comment_item, li[class*='comment_']");
                        for (const el of items) {
                            const bodyEl = el.querySelector(".comment_content, .xe_content, .comment_body, div[class*='comment_']");
                            const authorEl = el.querySelector(".meta .author, .member_profile, .nick, a[class*='member_']");
                            const dateEl = el.querySelector(".meta .date, .date");
                            
                            if (bodyEl) {
                                // 텍스트만 추출 (이미지나 스티커 대체 텍스트 포함)
                                const text = bodyEl.innerText.trim();
                                const author = authorEl ? authorEl.innerText.trim() : "익명";
                                const date = dateEl ? dateEl.innerText.trim() : "";
                                
                                // 삭제된 댓글 등 필터링
                                if (text && !text.includes("삭제된 댓글입니다") && text.length >= 2) {
                                    list.push({
                                        author: author,
                                        text: text,
                                        date: date
                                    });
                                }
                            }
                        }
                        return list;
                    }
                """)

                post_collected = 0
                for c in comments:
                    c_text = c["text"]
                    if c_text in seen_texts:
                        continue
                    seen_texts.add(c_text)

                    all_comments.append({
                        "source": "theqoo_muk",
                        "post_id": post_id,
                        "post_title": post_title,
                        "post_url": post["url"],
                        "author": c["author"],
                        "text": c_text,
                        "date": c["date"],
                        "length": len(c_text)
                    })
                    post_collected += 1

                print(f"  Collected {post_collected} comments (Total so far: {len(all_comments)})")

            except Exception as e:
                print(f"  Error scraping post {post_id}: {e}")

        browser.close()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)

    print(f"\nSuccessfully collected {len(all_comments)} comments from theqoo to {OUTPUT_FILE}!")
    return all_comments

if __name__ == "__main__":
    collect_theqoo_comments(550)
