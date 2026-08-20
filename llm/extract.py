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
import unicodedata
from typing import Optional

from llm.client import LLMClient, LLMError

# Méthodes autorisées — même énumération que config.schema (pipeline).
_METHODS = ("UD", "SD", "MD", "TKO", "KO", "DQ", "PTS", "TD")

_PROMPT = """Tu es un extracteur de résultats de boxe. À partir de l'article de presse ci-dessous, extrais TOUS les combats dont le vainqueur est clairement identifiable, y compris les phrases à sujet pronominal (« the Japanese who won by unanimous decision » = il faut retrouver le nom du boxeur dans l'article).

RÈGLES STRICTES :
1. Ne JAMAIS inventer : si un nom, une méthode ou un round n'est pas dans le texte, mets une chaîne vide ou 0.
2. Le perdant peut être désigné par sa nationalité (« the Thai challenger ») : si l'article ne donne pas son nom complet, mets le nom partiel tel quel (ex. « Thai challenger »).
3. Méthodes autorisées uniquement : UD, SD, MD, TKO, KO, DQ, PTS, TD.
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
    s = re.sub(r"\s+", " ", s)
    # un nom ne contient jamais de parenthèse (ex. « (Lauriaga » tronqué
    # par le LLM) → on retire le contenu entre parenthèses non fermées
    if s.count("(") > s.count(")"):
        s = re.sub(r"\s*\([^)]*$", "", s).strip()
    return s


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


def _first_json_object(text: str) -> Optional[str]:
    """Extrait le PREMIER objet JSON équilibré de la réponse (robuste).

    Gère les réponses bavardes (« Voici le JSON : {...} »), les fences
    markdown (```json … ```) et les accolades dans les chaînes (ex.
    un surnom entre accolades). Échoue proprement si aucun objet."""
    in_string = False
    escape = False
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    return None


def parse_llm_json(text: str) -> list[dict]:
    """Parse la réponse LLM : extrait `{"fights": [...]}` (JSON strict)."""
    obj = _first_json_object(text)
    if obj is None:
        raise LLMError(f"pas d'objet JSON dans la réponse LLM : {text[:200]}")
    try:
        data = json.loads(obj)
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON LLM invalide : {exc} — réponse : {text[:200]}") from exc
    fights = data.get("fights", []) if isinstance(data, dict) else []
    if not isinstance(fights, list):
        raise LLMError("le champ « fights » doit être une liste")
    return [_validate_fight(f) for f in fights if isinstance(f, dict)]


def _norm_text(s: str) -> str:
    """Normalise pour la recherche de nom : minuscules, accents retirés."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def _surname(name: str) -> str:
    """Nom de famille = dernier mot significatif du nom normalisé."""
    toks = [t for t in _norm_text(name).split() if len(t) >= 3]
    return toks[-1] if toks else ""


def _name_in_text(name: str, text: str) -> bool:
    """Le nom (ou son nom de famille) apparaît-il dans le texte de l'article ?

    Garde-fou anti-hallucination : un vainqueur n'apparaissant JAMAIS dans
    l'article est très probablement inventé par le LLM (ex. « Terence
    Crawford » dans un article sur une autre affiche).
    """
    n = _norm_text(name)
    if not n:
        return True  # nom inconnu → laisse passer (jugé par le schéma)
    nt = _norm_text(text)
    if n in nt:
        return True
    sur = _surname(name)
    if len(sur) >= 3 and sur in nt:
        return True
    return False


def filter_plausible(fights: list[dict], text: str) -> tuple[list[dict], list[dict]]:
    """Garde les combats dont le vainqueur est cité dans l'article.

    Retourne `(gardés, écartés)` — les combats écartés sont des
    hallucinations probables, jamais écrits.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for f in fights:
        if f is not None and _name_in_text(f.get("winner", ""), text):
            kept.append(f)
        else:
            dropped.append(f)
    return kept, dropped


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
    kept, dropped = filter_plausible(fights, text)
    # Les combats écartés (vainqueur absent de l'article) sont des
    # hallucinations probables — on ne les écrit jamais.
    return kept
