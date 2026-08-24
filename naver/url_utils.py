import re
import urllib.parse
from typing import Optional, Tuple
from app.models import FeedPost, FeedSourceType


def parse_blog_post_url(url: str) -> Optional[Tuple[str, str]]:
    """
    다양한 네이버 블로그 URL에서 blog_id와 log_no를 안정적으로 추출합니다.
    
    지원 형태:
    - https://m.blog.naver.com/userId/123456789012
    - https://blog.naver.com/userId/123456789012
    - https://blog.naver.com/PostView.naver?blogId=userId&logNo=123456789012
    - https://m.blog.naver.com/PostView.naver?blogId=userId&logNo=123456789012
    """
    if not url or not isinstance(url, str):
        return None

    # 1. Query parameter 형태 (PostView.naver?blogId=...&logNo=...)
    if "blogId=" in url and "logNo=" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        blog_id = params.get("blogId", [None])[0]
        log_no = params.get("logNo", [None])[0]
        if blog_id and log_no and log_no.isdigit():
            return blog_id, log_no

    # 2. Path 형태 (blog.naver.com/userId/logNo)
    match = re.search(r"(?:m\.)?blog\.naver\.com/([a-zA-Z0-9_\-]+)/([0-9]+)", url)
    if match:
        blog_id = match.group(1)
        log_no = match.group(2)
        # PostView, BlogHome, MyBlog 등 특수 네임스페이스 제외
        if blog_id.lower() not in ["postview", "bloghome", "myblog", "sectionsearch", "recommendation", "feedlist"]:
            return blog_id, log_no

    return None


def canonicalize_post_url(blog_id: str, log_no: str) -> str:
    """모바일 표준 게시글 URL 생성"""
    return f"https://m.blog.naver.com/{blog_id}/{log_no}"


def build_post_key(blog_id: str, log_no: str) -> str:
    """고유 Post 식별 키 생성"""
    return f"{blog_id}:{log_no}"


def extract_canonical_post(
    raw_url: str,
    source: FeedSourceType,
    title: Optional[str] = None,
    author: Optional[str] = None
) -> Optional[FeedPost]:
    """임의의 raw_url로부터 표준화된 FeedPost 객체 생성"""
    parsed = parse_blog_post_url(raw_url)
    if not parsed:
        return None

    blog_id, log_no = parsed
    key = build_post_key(blog_id, log_no)
    url = canonicalize_post_url(blog_id, log_no)

    return FeedPost(
        key=key,
        source=source,
        url=url,
        blog_id=blog_id,
        log_no=log_no,
        title=title.strip() if title else None,
        author=author.strip() if author else None
    )
