"""Batch d'extraction — les articles sources → combats LLM en volume.

Le parser regex du pipeline n'attrape qu'une partie des combats des
articles WBC/WBO (les phrases à sujet pronominal sont sautées). Ce batch
passe CHAQUE article au LLM et agrège les combats extraits, avec :

- un résumé (combats par article, taux de réussite) ;
- une sortie JSON réutilisable (`--output`) ;
- la tolérance aux pannes : un article qui échoue au LLM n'arrête pas le lot.

⚠️ Le LLM est un outil d'extraction : chaque combat retourné est validé
(parse_llm_json → _validate_fight) et peut être écrit dans les shards du
pipeline via `Fight.make` — jamais de données non validées.

Usage :
    from llm.batch import run_batch
    results = run_batch(client, "wbc", year=2026)
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Optional

from llm.client import LLMClient, LLMError
from llm.extract import extract_fights_llm
from llm.sources import fetch_wbc_articles, fetch_wbo_articles

# Sécurité : pause entre deux appels LLM (éviter le rate-limit Gemini).
# Le free tier Gemini est ~15 req/min → 4 s minimum entre deux appels
# (30 req/min théoriques avec 2 s, trop pour le quota).
LLM_PAUSE_S = 4.0

# Mêmes filtres de titres que le spider WBC du pipeline (validés 16/08/2026) :
# on ne passe au LLM que les articles de RÉSULTATS probables — les preview/
# bio racontent des combats historiques (datés à tort) et les récaps
# (« On This Day… ») ne sont pas des résultats actuels. Évite aussi de
# brûler des appels LLM sur les articles sans résultats.
_STALE_TITLE_RE = re.compile(
    r"\bon this day\b|\bone year since\b|\bremembering\b|\bnostalgia\b|\blegend\b",
    re.IGNORECASE,
)
_SKIP_TITLE_RE = re.compile(
    r"make weight|ready to|set for|to defend|\bdefend\b|preview|possible|grand opening|"
    r"opens |on this day|one year since|remember|nostalgia|legend|career|path to|biograph|"
    r"feature|interview|how to|join |congratulat|ratings|ranking|directives|guidelines|"
    r"hydration|protocol|mourns|passing|receives|receiving|belt from|book |magazine|issue|"
    r"winners of|best of|wbc convention|meeting|seminar|conference",
    re.IGNORECASE,
)
_RESULT_TITLE_RE = re.compile(
    r"results|defeated|crowned|reigns|wins |claim|victory|knockout|\btko\b|stopped|"
    r"outpointed|historic night|absolute war|great .* bouts|concludes",
    re.IGNORECASE,
)


def _is_result_article(title: str) -> bool:
    """Article de résultats probable (filtres identiques au pipeline)."""
    if not title:
        return False
    if _SKIP_TITLE_RE.search(title) or _STALE_TITLE_RE.search(title):
        return False
    return bool(_RESULT_TITLE_RE.search(title))


def run_batch(
    client: LLMClient,
    source: str,
    year: int = 2026,
    max_articles: int = 50,
    pause: float = LLM_PAUSE_S,
    progress=None,
) -> dict:
    """Extrait les combats de tous les articles d'une source via le LLM.

    Retourne {
        "source": source,
        "articles": n,
        "articles_ok": n,
        "combats": [{title, date, fights: [...]}],
        "total_fights": n,
        "errors": [message, ...],
    }
    """
    if not client.available:
        raise LLMError("pas de clé GEMINI_API_KEY — LLM inactif")

    if source == "wbc":
        articles = fetch_wbc_articles(year)
    elif source == "wbo":
        articles = fetch_wbo_articles(max_items=max_articles)
    else:
        raise ValueError(f"source inconnue : {source} (wbc | wbo)")

    results = {"source": source, "articles": len(articles),
               "articles_ok": 0, "combats": [], "total_fights": 0,
               "errors": [], "articles_skipped": 0}
    for i, article in enumerate(articles[:max_articles]):
        if progress:
            progress(i + 1, len(articles[:max_articles]), article["title"])
        # les titres API contiennent des entités HTML (&#8220;…) → décodage
        # avant le filtrage et l'affichage
        title = html.unescape(article["title"])
        if not _is_result_article(title):
            results["articles_skipped"] += 1
            continue  # preview / bio / guidelines → pas un article de résultats
        try:
            fights = extract_fights_llm(
                client, article["content"], article["date"] or "",
                source=source,
            )
        except LLMError as exc:
            results["errors"].append(f"{article['title']} : {exc}")
            continue
        results["articles_ok"] += 1
        results["total_fights"] += len(fights)
        if fights:
            results["combats"].append({
                "title": article["title"],
                "date": article["date"],
                "fights": fights,
            })
        if pause:
            time.sleep(pause)
    return results


def save_results(results: dict, path: str) -> None:
    """Écrit le résultat du batch en JSON (UTF-8, lisible)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
