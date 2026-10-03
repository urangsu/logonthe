"""Bounded public style audit; never persist comment text or member identifiers."""

import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


LIST_URL = "https://theqoo.net/cook?filter_mode=hot"


def fetch(url, cookies, payload=None, token=None, referer=None):
    if urlsplit(url).netloc != "theqoo.net":
        raise ValueError("Only theqoo.net is permitted")
    command = ["curl", "--fail", "--silent", "--show-error", "--max-time", "20",
               "--max-filesize", "3000000", "-b", cookies, "-c", cookies]
    if payload is not None:
        command += ["-H", "Content-Type: application/json; charset=utf-8",
                    "-H", "X-CSRF-Token: " + token,
                    "-H", "Referer: " + referer, "--data", json.dumps(payload)]
    # No retries, redirects, authentication, or access-control workarounds.
    return subprocess.check_output(command + [url], timeout=25)


def discover_posts(html, limit):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for row in soup.select("table.bd_lst tr:not(.notice)"):
        for anchor in row.select("td.title a"):
            url = urljoin(LIST_URL, anchor.get("href", ""))
            parsed = urlsplit(url)
            if parsed.netloc == "theqoo.net" and re.fullmatch(r"/cook/\d+", parsed.path):
                canonical = "https://theqoo.net" + parsed.path
                if canonical not in urls:
                    urls.append(canonical)
                break
        if len(urls) >= limit:
            break
    return urls


def comment_texts(response):
    if response.get("error"):
        raise RuntimeError("Public comment request rejected: " + str(response.get("message")))
    entries = response.get("comment_list")
    if not isinstance(entries, list):
        raise RuntimeError("Unexpected comment response; refusing empty success")
    texts = []
    for entry in entries[:40]:
        if entry.get("is_writer"):
            continue
        text = BeautifulSoup(entry.get("ct", ""), "html.parser").get_text(" ", strip=True)
        if text and "삭제된 댓글" not in text:
            texts.append(text)
    return texts


def summarize(texts):
    return {
        "comments_analyzed": len(texts),
        "median_characters": statistics.median(map(len, texts)) if texts else None,
        "short_at_most_40": sum(len(t) <= 40 for t in texts),
        "laughter_or_tears": sum(bool(re.search(r"[ㅋㅎㅠㅜ]{2,}", t)) for t in texts),
        "reaction_opening": sum(bool(re.match(r"(?:와|오|어웅|우와|헐|대박)", t)) for t in texts),
        "questions": sum("?" in t for t in texts),
        "terminal_period": sum(t.endswith(".") for t in texts),
    }


def sample(max_posts=6):
    if not 1 <= max_posts <= 6:
        raise ValueError("Sample at most six posts")
    with tempfile.TemporaryDirectory() as directory:
        cookies = str(Path(directory) / "anonymous_cookies")
        posts = discover_posts(fetch(LIST_URL, cookies), max_posts)
        if not posts:
            raise RuntimeError("No public cooking posts found")
        all_texts = []
        sources = []
        for url in posts:
            time.sleep(1)
            soup = BeautifulSoup(fetch(url, cookies), "html.parser")
            token = soup.select_one('meta[name="csrf-token"]')
            if not token:
                raise RuntimeError("Missing anonymous session token; stopping")
            time.sleep(1)
            response = json.loads(fetch(
                "https://theqoo.net/index.php", cookies,
                {"act": "dispTheqooContentCommentListTheqoo",
                 "document_srl": int(urlsplit(url).path.rsplit("/", 1)[1]), "cpage": 0},
                token["content"], url,
            ))
            texts = comment_texts(response)
            sources.append({"url": url, **summarize(texts)})
            all_texts.extend(texts)
        return {
            "source": LIST_URL,
            "method": "six_posts_max_latest_public_comment_page_only",
            "raw_comments_retained": False,
            "member_identifiers_retained": False,
            "sources": sources,
            "aggregate": summarize(all_texts),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-posts", type=int, default=6)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = sample(args.max_posts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
