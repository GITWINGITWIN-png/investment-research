"""Read full article text from article pages; the owner allowed this on 2026-11-08 ("fetching articles to read is fine for a try").

Used in only two places: InfoQuest's daily Form 59 table (about 1 page a day) and news for stocks Opus digs into (<= 2 stocks x 3 articles a day),
reading like a person opening links, not a crawler sweeping a whole site.

## Rules the code enforces itself (not relying on a manual check done once)

- Ask the site's robots.txt before every fetch; if the owner forbids it one day, the system stops that day
- At least 2 seconds between pages; identify with the project's USER_AGENT (never pretend to be a browser)
- Store what was read in the point-in-time store (`data/raw/th/articles/`) for personal use only, never republished
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit

import requests

from .. import config, storage

# Classes WordPress themes use to wrap article text; if none is found, fall back to every <p> on the page
_CONTENT = re.compile(r"entry-content|post-content|article-content|td-post-content|single-content|content-detail|news-content")
_last_fetch = 0.0


@lru_cache(maxsize=32)
def _robots(host: str) -> robotparser.RobotFileParser | None:
    """Read robots.txt with the same USER_AGENT used to fetch articles.

    Why not `RobotFileParser.read()` directly: it asks with Python's default User-Agent, to which many Thai news sites
    answer 403 (seen 2026-11-08: InfoQuest, Kaohoon), and it then reads that as "the whole site is forbidden" even though robots.txt
    allows us. Asking with our real identity is the right way to find out whether the site lets *us* fetch.
    """
    parser = robotparser.RobotFileParser(f"https://{host}/robots.txt")
    try:
        response = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": config.USER_AGENT},
                                timeout=config.TH_ARTICLE_TIMEOUT_SEC)
    except requests.RequestException:
        return None                                       # robots.txt cannot be read = no fetch this round (err on the side of caution)
    if response.status_code == 200:
        parser.parse(response.text.splitlines())
    elif response.status_code in (401, 403):
        parser.disallow_all = True                        # The site really refuses us -> respect it
    elif 400 <= response.status_code < 500:
        parser.allow_all = True                           # No robots.txt = no restrictions (per the standard)
    else:
        return None
    return parser


def allowed(url: str) -> bool:
    parser = _robots(urlsplit(url).netloc)
    return bool(parser and parser.can_fetch(config.USER_AGENT, url))


def fetch_html(url: str) -> tuple[str | None, str]:
    """Return (html, status); never raise: one broken article must not break the whole round"""
    global _last_fetch
    if not allowed(url):
        return None, "robots_disallow"
    wait = config.TH_ARTICLE_DELAY_SEC - (time.monotonic() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    try:
        response = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                                timeout=config.TH_ARTICLE_TIMEOUT_SEC)
    except requests.RequestException as exc:
        return None, f"error:{type(exc).__name__}"
    finally:
        _last_fetch = time.monotonic()
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    response.encoding = response.apparent_encoding or response.encoding
    return response.text, "ok"


def main_text(html: str) -> str:
    """Article text from an HTML page: strip menus, ads and scripts, then take the paragraphs in the article box"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "header", "form", "noscript", "iframe"]):
        tag.decompose()
    box = soup.find(class_=_CONTENT) or soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in box.find_all(["p", "li", "h2", "h3"])]
    text = "\n".join(p for p in paragraphs if len(p) > 1)
    return re.sub(r"[ \t ]+", " ", text).strip()


def read(items: list[dict[str, Any]], max_chars: int = config.TH_ARTICLE_MAX_CHARS) -> list[dict[str, Any]]:
    """Fetch the text for a list of news items (each needs url and title) -> store it, and return them with `text`"""
    out = []
    for item in items:
        html, status = fetch_html(item["url"])
        text = main_text(html)[:max_chars] if html else ""
        out.append({"url": item["url"], "title": item.get("title", ""), "publisher": item.get("publisher", ""),
                    "published_at": item.get("published_at", ""), "status": status, "chars": len(text), "text": text})
    if out:
        day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
        storage.write_jsonl(out, config.RAW_TH_ARTICLES / f"{day}.jsonl", kind="th_articles",
                            ok=sum(1 for o in out if o["status"] == "ok"))
    return out
