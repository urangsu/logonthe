import os
import json
import time
import urllib.parse
from playwright.sync_api import sync_playwright

BLOG_ID = "skssnrnrpd"
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "collected_comments_skssnrnrpd.json")

def get_post_list():
    import urllib.request
    url = f"https://blog.naver.com/PostTitleListAsync.naver?blogId={BLOG_ID}&viewdate=&currentPage=1&categoryNo=0&countPerPage=30"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        raw_bytes = response.read()
        raw_str = raw_bytes.decode('utf-8', errors='ignore')
        # Naver PostTitleListAsync often contains invalid escapes like \' in pagingHtml
        import re
        # remove pagingHtml or use strict=False with escape cleanup
        cleaned_str = re.sub(r'\\\'', "'", raw_str)
        try:
            data = json.loads(cleaned_str, strict=False)
        except Exception:
            # fallback: regex extraction of post objects
            post_matches = re.findall(r'\{[^{}]*"logNo"\s*:\s*"(\d+)"[^{}]*"title"\s*:\s*"([^"]+)"[^{}]*"commentCount"\s*:\s*"(\d+)"[^{}]*\}', raw_str)
            posts = []
            for m in post_matches:
                posts.append({
                    "logNo": m[0],
                    "title": urllib.parse.unquote_plus(m[1]),
                    "commentCount": int(m[2]),
                    "categoryNo": "0",
                    "addDate": "",
                    "url": f"https://m.blog.naver.com/{BLOG_ID}/{m[0]}"
                })
            return posts
    
    posts = []
    for item in data.get("postList", []):
        log_no = item.get("logNo")
        title = urllib.parse.unquote_plus(item.get("title", ""))
        comment_count = int(item.get("commentCount", 0) or 0)
        category_no = item.get("categoryNo", "")
        add_date = item.get("addDate", "")
        posts.append({
            "logNo": log_no,
            "title": title,
            "commentCount": comment_count,
            "categoryNo": category_no,
            "addDate": add_date,
            "url": f"https://m.blog.naver.com/{BLOG_ID}/{log_no}"
        })
    return posts

def collect_comments(target_count=350):
    posts = get_post_list()
    print(f"Total posts found: {len(posts)}")
    
    all_comments = []
    seen_texts = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 430, "height": 900},
            locale="ko-KR",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()

        for post in posts:
            if len(all_comments) >= target_count:
                break

            log_no = post["logNo"]
            title = post["title"]
            expected_cmt = post["commentCount"]
            if expected_cmt == 0:
                continue

            print(f"\n[POST] {title} ({log_no}) - Expected comments: {expected_cmt}")
            try:
                page.goto(post["url"], wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)

                # 댓글 버튼 클릭
                cmt_btn = page.locator("button[data-click-area*='.re'], button:has(.blind:text-is('댓글')), .Interact__comment_btn--Wbuoq").first
                if cmt_btn.count() > 0:
                    try:
                        cmt_btn.click(timeout=2000)
                        page.wait_for_timeout(1500)
                    except Exception as e:
                        print(f"  Failed to click comment button: {e}")

                # 더보기 버튼 계속 클릭하여 댓글 확장
                for click_idx in range(15):
                    more_btn = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more, .u_cbox_paginate a").first
                    if more_btn.count() > 0 and more_btn.is_visible():
                        try:
                            more_btn.click(timeout=1500)
                            page.wait_for_timeout(800)
                        except Exception:
                            break
                    else:
                        break

                # 댓글 목록 추출
                comments_data = page.evaluate("""
                    () => {
                        const list = [];
                        const items = document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']");
                        for (let el of items) {
                            const isSecret = el.querySelector(".u_cbox_secret_contents, .u_cbox_ico_secret") !== null;
                            const textEl = el.querySelector(".u_cbox_contents, .u_cbox_text_main");
                            const nickEl = el.querySelector(".u_cbox_nick, .u_cbox_name");
                            const dateEl = el.querySelector(".u_cbox_date");
                            const isReply = el.classList.contains("u_cbox_type_reply") || el.closest(".u_cbox_reply_area") !== null;
                            const isAuthor = el.querySelector(".u_cbox_ico_editor, .u_cbox_ico_writer") !== null;

                            const text = textEl ? textEl.innerText.trim() : "";
                            const nick = nickEl ? nickEl.innerText.trim() : "";
                            const date = dateEl ? dateEl.innerText.trim() : "";

                            if (text && text !== "작성자가 삭제한 댓글입니다." && text !== "비밀 댓글입니다.") {
                                list.push({
                                    nick: nick,
                                    text: text,
                                    date: date,
                                    isReply: isReply,
                                    isAuthor: isAuthor,
                                    isSecret: isSecret
                                });
                            }
                        }
                        return list;
                    }
                """)

                post_collected = 0
                for c in comments_data:
                    c_text = c["text"]
                    unique_key = (c["nick"], c_text)
                    if unique_key in seen_texts:
                        continue
                    seen_texts.add(unique_key)

                    all_comments.append({
                        "post_log_no": log_no,
                        "post_title": title,
                        "post_category": post["categoryNo"],
                        "post_date": post["addDate"],
                        "nick": c["nick"],
                        "text": c_text,
                        "date": c["date"],
                        "is_reply": c["isReply"],
                        "is_author": c["isAuthor"],
                        "length": len(c_text)
                    })
                    post_collected += 1

                print(f"  Collected {post_collected} comments (Total so far: {len(all_comments)})")

            except Exception as e:
                print(f"  Error processing post {log_no}: {e}")

        browser.close()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)

    print(f"\nSuccessfully collected {len(all_comments)} comments to {OUTPUT_FILE}!")
    return all_comments

if __name__ == "__main__":
    collect_comments(350)
