"""Lecture des articles sources WBC/WBO — depuis la source, pas les shards.

Le pipeline extrait les combats avec des parsers regex ; le LLM travaille
sur les ARTICLES BRUTS pour récupérer ce que le regex saute (phrases
pronominales). Ce module fetch les mêmes sources que le pipeline (URLs
validées le 16/08/2026) avec une politesse réseau minimale (1 requête /
s, retry sur 429/5xx).

Usage :
    from llm.sources import fetch_wbc_articles, fetch_wbo_articles
    articles = fetch_wbc_articles(year=2026)  # [(titre, date, contenu)]
"""

from __future__ import annotations

import html as _html
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

WBC_API = "https://wbcboxing.com/wp-json/wp/v2/posts"
WBO_RSS = "https://wboboxing.com/feed/"

# Politesse : 1 requête / s minimum, retry sur 429/5xx
MIN_INTERVAL = 1.0
_last_request: dict[str, float] = {}


def _polite_wait(domain: str) -> None:
    global _last_request
    last = _last_request.get(domain)
    if last is not None:
        wait = MIN_INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request[domain] = time.monotonic()


def _get(url: str, timeout: float = 30.0) -> bytes:
    """GET poli avec retry sur 429/5xx (3 tentatives, backoff)."""
    domain = urllib.parse.urlparse(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        _polite_wait(domain)
        req = urllib.request.Request(url, headers={
            "User-Agent": "ROUNDS-LLM/0.1 (+https://rounds.app; data sync)",
            "Accept": "application/json,text/xml,*/*;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 502, 503):
                time.sleep(2.0 * attempt)
                continue
            raise
        except (OSError, TimeoutError) as exc:
            last_exc = exc
            time.sleep(2.0 * attempt)
    raise last_exc or RuntimeError(f"fetch échoué : {url}")


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# WBC : API WordPress (tous les articles EN de l'année, contenu inline)
# ---------------------------------------------------------------------------

def fetch_wbc_articles(year: int = 2026) -> list[dict]:
    """Articles WBC EN de l'année → [{title, date, content}]."""
    url = f"{WBC_API}?lang=en&after={year}-01-01T00:00:00&per_page=100"
    data = json.loads(_get(url).decode("utf-8"))
    articles = []
    for post in data:
        title = (post.get("title") or {}).get("rendered") or ""
        date = (post.get("date") or "")[:10]
        content = _strip_html((post.get("content") or {}).get("rendered"))
        if title and content:
            articles.append({"title": title, "date": date, "content": content})
    return articles


# ---------------------------------------------------------------------------
# WBO : flux RSS (découverte des articles) puis fetch de chaque article
# ---------------------------------------------------------------------------

_RSS_ITEM_RE = re.compile(
    r"<item>(.*?)</item>", re.DOTALL | re.IGNORECASE
)
_RSS_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                           re.DOTALL | re.IGNORECASE)
_RSS_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL | re.IGNORECASE)


def fetch_wbo_articles(max_items: int = 30) -> list[dict]:
    """Articles WBO récents (RSS → fetch de chaque article) → list[dict]."""
    feed = _get(WBO_RSS).decode("utf-8", errors="replace")
    articles = []
    for item in _RSS_ITEM_RE.findall(feed)[:max_items]:
        title_m = _RSS_TITLE_RE.search(item)
        link_m = _RSS_LINK_RE.search(item)
        title = _html.unescape(title_m.group(1)).strip() if title_m else ""
        link = link_m.group(1).strip() if link_m else ""
        if not link:
            continue
        try:
            body = _get(link)
        except Exception:
            continue  # un article injoignable ne bloque pas le lot
        text = _strip_html(body.decode("utf-8", errors="replace"))
        if text:
            articles.append({"title": title, "date": "", "link": link,
                             "content": text})
    return articles
