import re
import urllib.parse
from typing import List, Dict, Any
from playwright.sync_api import Page
from src.logger import logger
from src.browser import random_sleep


def normalize_blog_post_url(url: str) -> str:
    """
    일반 블로그 URL(https://blog.naver.com/userId/123456)을
    iframe 없이 바로 접근 가능한 PostView URL로 변환합니다.
    """
    match = re.search(r"blog\.naver\.com/([a-zA-Z0-9_\-]+)/([0-9]+)", url)
    if match:
        blog_id = match.group(1)
        log_no = match.group(2)
        return f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}&redirect=Dlog&widgetTypeCall=true&directAccess=true"
    return url


class BlogCollector:
    @staticmethod
    def search_blog_posts(page: Page, keyword: str, max_count: int = 20) -> List[Dict[str, str]]:
        """
        네이버 블로그 섹션 검색 또는 통합 검색에서 키워드로 포스트 링크를 수집합니다.
        """
        encoded_kw = urllib.parse.quote(keyword)
        search_url = f"https://section.blog.naver.com/Search/Post.naver?pageNo=1&rangeType=ALL&orderBy=sim&keyword={encoded_kw}"
        
        logger.log(f"키워드 '{keyword}' 블로그 검색 이동: {search_url}")
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.log(f"검색 페이지 로드 안내: {e}", "WARNING")

        random_sleep(1.0, 2.0)

        # 아래로 스크롤하여 목록 렌더링
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1000)")
            random_sleep(0.3, 0.6)

        selectors = [
            "a.desc_inner",
            "a.title_link",
            "a.api_txt_lines.total_tit",
            "a.detail_tit",
            "div.info_post a.title"
        ]

        elements = []
        for sel in selectors:
            found = page.query_selector_all(sel)
            if found:
                elements.extend(found)

        results = []
        seen_urls = set()

        for el in elements:
            try:
                href = el.get_attribute("href")
                title = el.inner_text().strip()
                if href and ("blog.naver.com" in href or "in.naver.com" in href):
                    direct_url = normalize_blog_post_url(href)
                    if direct_url not in seen_urls:
                        seen_urls.add(direct_url)
                        results.append({"title": title, "url": direct_url})
                        if len(results) >= max_count:
                            break
            except Exception:
                continue

        logger.log(f"키워드 '{keyword}' 기반 {len(results)}개 포스팅 URL 수집 완료.")
        return results
