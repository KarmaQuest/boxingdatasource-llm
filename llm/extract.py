"""Extraction de combats depuis la PROSE des articles WBC/WBO (LLM).

Les articles WBC/WBO sont des récits : « …the Japanese who won by
unanimous decision », « …she defended her crown against the Thai
challenger ». Les parsers regex (`spiders/wbc.py`, `spiders/wbo.py`)
sautent ces phrases à sujet pronominal — le LLM les lit et extrait les
combats, dans un format JSON STRICT.

Le résultat est ensuite validé par le schéma du pipeline
(`Fight.make`) : un combat qui ne passe pas est rejeté avec un warning.
Jamais de fabrication — le prompt interdit explicitement d'inventer un
nom, une méthode ou un round qui n'est pas dans le texte source.

Usage :
    from llm.client import get_default_client
    from llm.extract import extract_fights_llm
    fights = extract_fights_llm(client, text, date, source="wbc")

Retourne une liste de dicts PRÊTS pour `Fight.make` (jamais de Fight
directement : le schéma appartient au pipeline, ici on ne dépend que du
contrat JSON).
"""

from __future__ import annotations

import json
import re
from typing import Optional

from llm.client import LLMClient, LLMError

# Méthodes autorisées — même énumération que config.schema (pipeline).
_METHODS = ("UD", "SD", "MD", "TKO", "KO", "DQ", "PTS")

_PROMPT = """Tu es un extracteur de résultats de boxe. À partir de l'article de presse ci-dessous, extrais TOUS les combats dont le vainqueur est clairement identifiable, y compris les phrases à sujet pronominal (« the Japanese who won by unanimous decision » = il faut retrouver le nom du boxeur dans l'article).

RÈGLES STRICTES :
1. Ne JAMAIS inventer : si un nom, une méthode ou un round n'est pas dans le texte, mets une chaîne vide ou 0.
2. Le perdant peut être désigné par sa nationalité (« the Thai challenger ») : si l'article ne donne pas son nom complet, mets le nom partiel tel quel (ex. « Thai challenger »).
3. Méthodes autorisées uniquement : UD, SD, MD, TKO, KO, DQ, PTS.
4. `rounds` = rounds prévus (ex. « 10 rounds ») ou round de l'arrêt (ex. « fifth round » = 5), sinon 0.
5. `is_title_fight` = true si « title », « championship », « belt », « crown », « champion » apparaît.
6. `location` : ville/pays du chapeau de l'article (ex. « BANGKOK, THAILAND »), sinon "".
7. Réponds UNIQUEMENT un JSON valide de la forme :
   {{"fights": [{{"winner": "...", "loser": "...", "method": "UD", "rounds": 12, "weight_class": "...", "is_title_fight": false, "location": "..."}}]}}
   Si aucun combat, réponds {{"fights": []}}.

Date de publication (date du combat approximative) : {date}
Source : {source}

ARTICLE :
{text}
"""


def build_prompt(text: str, date: str, source: str = "wbc") -> str:
    """Construit le prompt d'extraction (partie testable)."""
    return _PROMPT.format(text=text, date=date, source=source)


def _clean_value(value: object) -> str:
    s = str(value or "").strip()
    return re.sub(r"\s+", " ", s)


def _validate_fight(raw: dict) -> Optional[dict]:
    """Valide un combat extrait par le LLM contre les règles du schéma.

    Retourne le combat nettoyé (prêt pour `Fight.make`), ou None si le
    combat est inutilisable (vainqueur manquant, méthode interdite…).
    """
    winner = _clean_value(raw.get("winner"))
    loser = _clean_value(raw.get("loser"))
    method = _clean_value(raw.get("method")).upper()
    if not winner or not loser:
        return None
    if winner.lower() == loser.lower():
        return None
    if method not in _METHODS:
        method = ""  # inconnu → laissé au schéma (qui rejettera si obligatoire)

    rounds_raw = raw.get("rounds")
    try:
        rounds = int(rounds_raw) if rounds_raw not in (None, "") else 0
    except (TypeError, ValueError):
        rounds = 0
    if rounds < 0:
        rounds = 0

    return {
        "winner": winner,
        "loser": loser,
        "method": method,
        "rounds": rounds,
        "weight_class": _clean_value(raw.get("weight_class")),
        "is_title_fight": bool(raw.get("is_title_fight")),
        "location": _clean_value(raw.get("location")),
    }


def parse_llm_json(text: str) -> list[dict]:
    """Parse la réponse LLM : extrait `{"fights": [...]}` (JSON strict)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise LLMError(f"pas d'objet JSON dans la réponse LLM : {text[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON LLM invalide : {exc}") from exc
    fights = data.get("fights", []) if isinstance(data, dict) else []
    if not isinstance(fights, list):
        raise LLMError("le champ « fights » doit être une liste")
    return [_validate_fight(f) for f in fights if isinstance(f, dict)]


def extract_fights_llm(
    client: LLMClient,
    text: str,
    date: str,
    source: str = "wbc",
) -> list[dict]:
    """Extrait les combats d'un article via le LLM.

    Retourne les combats VALIDÉS (déjà nettoyés) prêts pour `Fight.make`.
    Si le LLM échoue (pas de clé, JSON invalide), lève LLMError — l'appelant
    décide (warning + continue).
    """
    if not client.available:
        raise LLMError("pas de clé GEMINI_API_KEY — LLM inactif")
    response = client.complete(build_prompt(text, date, source))
    fights = parse_llm_json(response)
    return [f for f in fights if f is not None]
