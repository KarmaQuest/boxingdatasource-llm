"""Recoupement d'entités par LLM — le même boxeur vu par plusieurs sources.

Le déterministe (`annuaire/resolve.py` du pipeline) résout par QID, slug
exact et similarité. Le LLM intervient sur les cas AMBIGUS que le
déterministe ne tranche pas :

- variantes internationales : « O. Usyk » vs « Oleksandr Usyk » ;
- surnoms / Jr. / Sr. : « Canelo » vs « Canelo Álvarez » ;
- orthographes : « Niyomtrong » vs « Niyomtrong » (thai), « Barboza Jr »
  vs « Barboza Jr. » ;
- homonymes : deux boxeurs au même nom (le LLM reçoit le CONTEXTE —
  pays, date, catégorie — pour distinguer).

Garde-fou : le LLM ne renvoie qu'une DÉCISION (same/different + score de
confiance), jamais des données. La fusion des champs reste au déterministe.

Usage :
    from llm.client import get_default_client
    from llm.resolve import decide_same_person
    verdict = decide_same_person(client, "O. Usyk", "Oleksandr Usyk")
    # → {"same": True, "confidence": 0.95, "reason": "…"}
"""

from __future__ import annotations

import json
import re
from typing import Optional

from llm.client import LLMClient, LLMError

_PROMPT = """Tu es un expert en résolution d'entités dans le monde de la boxe. Deux mentions de boxeurs te sont données avec leur contexte. Décide si elles désignent LA MÊME personne.

Mention A : « {name_a} »
  Contexte A : {ctx_a}

Mention B : « {name_b} »
  Contexte B : {ctx_b}

RÈGLES :
1. Les surnoms entre guillemets, les « Jr. »/« Sr. » et les initiales ne changent pas l'identité.
2. Les orthographes différentes d'un même nom (transcriptions, accents) désignent souvent la même personne — utilise le contexte (pays, date, catégorie) pour trancher.
3. Si le contexte contredit la même personne (pays différents, époques différentes…), réponds « different ».
4. Réponds UNIQUEMENT un JSON valide :
   {{"same": true|false, "confidence": 0.0-1.0, "reason": "explication courte"}}

Si tu hésites franchement, mets confidence < 0.5 (le déterministe décidera).
"""


def build_prompt(name_a: str, ctx_a: str, name_b: str, ctx_b: str) -> str:
    return _PROMPT.format(name_a=name_a, ctx_a=ctx_a, name_b=name_b, ctx_b=ctx_b)


def _parse_verdict(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise LLMError(f"pas d'objet JSON dans la réponse LLM : {text[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON LLM invalide : {exc}") from exc
    if not isinstance(data, dict) or "same" not in data:
        raise LLMError(f"réponse LLM incomplète : {data}")
    data["same"] = bool(data.get("same"))
    try:
        data["confidence"] = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    return data


def decide_same_person(
    client: LLMClient,
    name_a: str,
    ctx_a: str,
    name_b: str,
    ctx_b: str,
) -> dict:
    """Demande au LLM si deux mentions désignent le même boxeur.

    Retourne {"same": bool, "confidence": float, "reason": str}.
    """
    if not client.available:
        raise LLMError("pas de clé GEMINI_API_KEY — LLM inactif")
    response = client.complete(build_prompt(name_a, ctx_a, name_b, ctx_b))
    return _parse_verdict(response)


# ---------------------------------------------------------------------------
# Rapport sur un annuaire : détecte les paires suspectes à faire trancher
# ---------------------------------------------------------------------------

# Zone ambigüe → LLM : assez proche pour être le même, assez différent
# pour douter. Les variantes d'accents (0.96+) sont exclues : le slug
# normalisé du déterministe les résout déjà sans LLM.
SIMILARITY_AMBIGUOUS = (0.80, 0.95)


def suspicious_pairs(
    names: list[tuple[str, str]],
    lower: float = SIMILARITY_AMBIGUOUS[0],
    upper: float = SIMILARITY_AMBIGUOUS[1],
) -> list[tuple[str, str, float]]:
    """Paires de noms à similarité ambigüe (à faire trancher par le LLM).

    `names` : liste de (nom, contexte) — les noms sont comparés deux à
    deux (normalisés), les paires dont la similarité tombe dans la zone
    ambigüe sont retournées avec leur score. O(n²) borné : on s'arrête
    aux premiers résultats (liste de noms d'une même source).
    """
    import difflib

    def norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
        return re.sub(r"\s+", " ", s).strip()

    pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for i, (na, ca) in enumerate(names):
        for nb, cb in names[i + 1:]:
            a, b = norm(na), norm(nb)
            if not a or not b or a == b:
                continue
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if lower <= ratio <= upper:
                pairs.append((na, nb, ratio))
    return pairs
