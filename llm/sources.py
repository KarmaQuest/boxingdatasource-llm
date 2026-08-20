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

import datetime
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


def _get(url: str, timeout: float = 30.0, use_browser_ua: bool = False) -> bytes:
    """GET poli avec retry sur 429/5xx (3 tentatives, backoff)."""
    domain = urllib.parse.urlparse(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        _polite_wait(domain)
        # WBO bloque les User-Agents techniques — utiliser un UA navigateur
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            if use_browser_ua else
            "ROUNDS-LLM/0.1 (+https://rounds.app; data sync)"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": ua,
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


def _decode_body(raw: bytes) -> str:
    """Décode du contenu web de façon robuste.

    Les articles WBO sont annoncés UTF-8 mais contiennent parfois des
    guillemets Windows-1252 (« „ ” “ ») : `errors="replace"` produit du
    mojibake (« �? »). On tente UTF-8 strict, puis Windows-1252 (qui mappe
    chaque octet et ne « remplace » donc jamais) — jamais de mojibake.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1252")


def _strip_html(raw: str) -> str:
    """Extrait le texte lisible d'un HTML WordPress.

    Priorise le contenu <article> ou entry-content (WordPress) pour éviter
    de mélanger le JSON-LD / navigation / sidebar au vrai texte.
    """
    # Extraire le contenu principal (article ou entry-content)
    article_m = re.search(r"<article[^>]*>(.*?)</article>", raw or "",
                          re.DOTALL | re.IGNORECASE)
    if article_m:
        raw = article_m.group(1)
    else:
        entry_m = re.search(r"class=['\"]entry-content['\"]>(.*?)</div>",
                            raw or "", re.DOTALL | re.IGNORECASE)
        if entry_m:
            raw = entry_m.group(1)
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
_RSS_PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL | re.IGNORECASE)

# Widgets de la sidebar du site WBO : la page d'un article affiche
# « UPCOMING FIGHTS » (et « FOLLOW US ») en bas de colonne. Ces lignes
# n'appartiennent PAS au contenu de l'article : les couper évite que le
# LLM les prenne pour des résultats de l'article.
_SIDEBAR_MARKERS = ("UPCOMING FIGHTS", "FOLLOW US")


def _rss_pubdate_to_iso(pubdate: str) -> str:
    """Convertit un `<pubDate>` RSS (« Tue, 18 Nov 2025 12:00:00 +0000 »)
    en `YYYY-MM-DD`. Retourne "" si le format est illisible."""
    try:
        dt = datetime.datetime.strptime(pubdate.strip(),
                                        "%a, %d %b %Y %H:%M:%S %z")
        return dt.date().isoformat()
    except ValueError:
        return ""


def _strip_sidebar(text: str) -> str:
    """Coupe le contenu d'un article WBO au premier widget de la sidebar."""
    low = text.upper()
    for marker in _SIDEBAR_MARKERS:
        idx = low.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.rstrip()


def fetch_wbo_articles(max_items: int = 30, max_pages: int = 10) -> list[dict]:
    """Articles WBO récents (RSS paginé → filtrage titre → fetch contenu).

    Stratégie optimisée : on parcourt les pages RSS pour ne garder que
    les titres matchant ``_is_result_article``, puis on fetch le contenu
    uniquement pour ceux-ci. Évite de burner des requêtes HTTP sur les
    rankings / rulings / rankings.
    """
    from llm.batch import _is_result_article

    # Étape 1 : collecter les titres + liens depuis les pages RSS
    candidates: list[tuple[str, str, str]] = []  # [(title, link, date)]
    seen_links: set[str] = set()
    for page in range(1, max_pages + 1):
        feed_url = WBO_RSS if page == 1 else f"{WBO_RSS}?paged={page}"
        try:
            feed = _decode_body(_get(feed_url, use_browser_ua=True))
        except Exception:
            break
        items = _RSS_ITEM_RE.findall(feed)
        if not items:
            break
        for item in items:
            title_m = _RSS_TITLE_RE.search(item)
            link_m = _RSS_LINK_RE.search(item)
            pub_m = _RSS_PUBDATE_RE.search(item)
            title = _html.unescape(title_m.group(1)).strip() if title_m else ""
            link = link_m.group(1).strip() if link_m else ""
            date = _rss_pubdate_to_iso(pub_m.group(1)) if pub_m else ""
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            if _is_result_article(title):
                candidates.append((title, link, date))

    # Étape 2 : fetch le contenu uniquement pour les articles candidates
    articles = []
    for title, link, date in candidates[:max_items]:
        try:
            body = _get(link, use_browser_ua=True)
        except Exception:
            continue
        text = _strip_sidebar(_strip_html(_decode_body(body)))
        if text:
            articles.append({"title": title, "date": date, "link": link,
                             "content": text})
    return articles
