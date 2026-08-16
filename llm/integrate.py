"""Intégration — les combats LLM deviennent des données du pipeline.

Le LLM extrait des combats depuis la prose (batch extract) ; ce module
les convertit au FORMAT de sortie du pipeline (le contrat `Fight` :
date, location, weight_class, fighter_a, fighter_b, winner, method,
rounds, is_title_fight) pour être écrits par `write_org_shard` sans
modification du pipeline.

Validation locale (miroir du schéma pipeline, sans importer le repo) :
- date YYYY-MM-DD ;
- méthode dans {UD, SD, MD, TKO, KO, DQ, PTS} ;
- winner = fighter_a | fighter_b | Draw | NC ;
- noms normalisés (espaces réduits).

Un combat qui ne passe pas est rejeté avec un warning — jamais écrit.

Usage :
    from llm.integrate import fights_to_pipeline_format
    dicts = fights_to_pipeline_format(results, source="wbc", date="2026-08-12")
"""

from __future__ import annotations

import re
from typing import Optional

METHODS = ("UD", "SD", "MD", "TKO", "KO", "DQ", "PTS")
WINNER_DRAW = "Draw"
WINNER_NC = "NC"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _is_valid_date(d: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", d))


def to_pipeline_fight(
    fight: dict,
    source: str,
    date: str = "",
    location_fallback: str = "",
) -> Optional[dict]:
    """Convertit un combat LLM en dict au format Fight du pipeline.

    Retourne None si le combat est inutilisable (date manquante, winner
    invalide, méthode interdite…).
    """
    f_date = _norm(fight.get("date") or date)
    if not _is_valid_date(f_date):
        return None

    fighter_a = _norm(fight.get("winner"))
    fighter_b = _norm(fight.get("loser"))
    winner = _norm(fight.get("winner"))
    method = _norm(fight.get("method")).upper()
    rounds_raw = fight.get("rounds")
    try:
        rounds = int(rounds_raw) if rounds_raw not in (None, "") else 0
    except (TypeError, ValueError):
        rounds = 0
    if rounds < 0:
        rounds = 0

    if not fighter_a or not fighter_b or fighter_a.lower() == fighter_b.lower():
        return None
    if method not in METHODS:
        return None  # méthode inconnue → on ne devine jamais

    location = _norm(fight.get("location") or location_fallback)
    weight_class = _norm(fight.get("weight_class"))
    is_title = bool(fight.get("is_title_fight"))

    return {
        "date": f_date,
        "location": location,
        "weight_class": weight_class,
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "winner": winner,
        "method": method,
        "rounds": rounds,
        "is_title_fight": is_title,
        "source": source,  # information pour le pipeline (non contractuel)
    }


def fights_to_pipeline_format(
    batch_results: dict,
    source: str = "wbc",
    default_date: str = "",
) -> list[dict]:
    """Convertit un résultat de `run_batch` en liste de dicts pipeline.

    `batch_results` : la sortie de llm.batch.run_batch ({"combats": [
    {title, date, fights: [...]}, …]}).

    Retourne la liste des combats VALIDÉS, prêts pour `write_org_shard`.
    """
    out: list[dict] = []
    for article in batch_results.get("combats", []):
        article_date = _norm(article.get("date"))
        for fight in article.get("fights", []):
            converted = to_pipeline_fight(
                fight, source, date=article_date or default_date,
                location_fallback="",
            )
            if converted:
                out.append(converted)
    return out
