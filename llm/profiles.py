"""Vérification LLM des profils boxeurs (données physiques + palmarès).

Pourquoi : les sources (Big Balls, Wikidata) laissent parfois la garde vide,
la taille/allonge à 0, ou un palmarès incomplet. Cette feature demande au LLM
de CONFIRMER ou CORRIGER les champs d'un profil à partir de l'article
Wikipedia du boxeur (infobox + intro). Le LLM ne décide rien : il produit un
RAPPORT (champ, valeur, confiance 0-1, citation exacte). Une étape
d'application DÉTERMINISTE (confiance ≥ 0,9 + conformité au schéma) décide
ensuite — côté pipeline (`apply-profiles`), jamais dans le LLM.

Contrat du rapport (JSON, consommé par le pipeline) :
    {generated_at, model,
     profiles: [{slug, name, lang,
                 fields: {stance|height_cm|reach_cm|record:
                     {value, confidence (0-1), quote}}}]}

Usage :
    from llm.client import get_default_client
    from llm.profiles import load_candidates, verify_profiles, apply_report
    candidates = load_candidates("boxing-app/public/data/boxers/merged.json")
    report = verify_profiles(client, candidates)
    corrections = apply_report(report)          # confiance ≥ 0.9 + schéma
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("llm.profiles")

# Champs vérifiables (le contrat du rapport).
PROFILE_FIELDS = ("stance", "height_cm", "reach_cm", "record")

# Gardes valides (schéma app boxing-app : lib/data/types.ts).
STANCES = ("Orthodoxe", "Southpaw", "Switch")

# Seuil d'application par défaut (le pipeline peut le surcharger).
DEFAULT_THRESHOLD = 0.9

_USER_AGENT = (
    "ROUNDS-Boxing/0.1 (https://rounds.app - verify profiles, "
    "contact: dev@rounds.app)"
)


# ---------------------------------------------------------------------------
# Schéma de validation (côté application déterministe)
# ---------------------------------------------------------------------------


def _valid_stance(value: object) -> bool:
    return value in STANCES


def _valid_cm(value: object) -> bool:
    return isinstance(value, int) and value > 0 and not isinstance(value, bool)


def _valid_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("wins", "losses", "draws", "ko"):
        v = value.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return False
    return True


# Ordre d'application : la taille doit venir avant l'allonge ? Non — chaque
# champ est indépendant. Mais le record complet remplace tout le record.
_FIELD_VALIDATORS = {
    "stance": _valid_stance,
    "height_cm": _valid_cm,
    "reach_cm": _valid_cm,
    "record": _valid_record,
}


# ---------------------------------------------------------------------------
# Candidats
# ---------------------------------------------------------------------------


def load_candidates(annuaire: str | Path, limit: int = 0) -> list[dict]:
    """Candidats à la vérification : les boxeurs de merged.json avec un ID
    Wikidata et des données physiques incomplètes (garde vide, taille ou
    allonge à 0). `limit` > 0 borne le nombre de profils analysés."""
    path = Path(annuaire)
    if not path.exists():
        raise FileNotFoundError(f"annuaire introuvable : {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for b in data:
        if not b.get("wikidata_id"):
            continue
        slug = b.get("slug")
        name = b.get("name")
        if not slug or not name:
            continue
        stance = b.get("stance") or ""
        height = b.get("height_cm") or 0
        reach = b.get("reach_cm") or 0
        # on ne vérifie que les fiches avec un trou physique (le reste est
        # déjà fourni par une source fiable)
        if stance or height > 0 and reach > 0:
            continue
        out.append({
            "slug": slug,
            "name": name,
            "wikidata_id": b["wikidata_id"],
            "stance": stance,
            "height_cm": height,
            "reach_cm": reach,
            "record": b.get("record") or [0, 0, 0, 0],
        })
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Récupération de l'article Wikipedia (fr puis en)
# ---------------------------------------------------------------------------


def _api_get(url: str, timeout: float = 20.0) -> Optional[dict]:
    """GET JSON avec User-Agent descriptif + retry sur 429 (poli envers
    les APIs, comme le client LLM). Retourne None en cas d'échec."""
    for attempt in range(1, 4):
        req = urllib.request.Request(
            url, headers={"user-agent": _USER_AGENT, "accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(5.0 * attempt)
                continue
            return None
        except (OSError, TimeoutError):
            if attempt == 3:
                return None
            time.sleep(1.5 * attempt)
    return None


def resolve_title(wikidata_id: str) -> dict:
    """Résout un QID → titres d'articles fr/en (sitelinks)."""
    if not wikidata_id.startswith("Q"):
        return {}
    url = (
        "https://www.wikidata.org/w/api.php?action=wbgetentities"
        f"&ids={wikidata_id}&props=sitelinks&format=json&origin=*"
    )
    j = _api_get(url)
    sl = ((j or {}).get("entities") or {}).get(wikidata_id, {}).get("sitelinks") or {}
    out = {}
    if sl.get("frwiki", {}).get("title"):
        out["fr"] = sl["frwiki"]["title"]
    if sl.get("enwiki", {}).get("title"):
        out["en"] = sl["enwiki"]["title"]
    return out


def fetch_wikitext(lang: str, title: str) -> str:
    """Récupère le wikitext d'un article (langue donnée), vide si échec."""
    url = (
        f"https://{lang}.wikipedia.org/w/api.php?action=query&prop=revisions"
        "&rvprop=content&rvslots=main&redirects=1&format=json&formatversion=2"
        f"&titles={urllib.parse.quote(title)}"
    )
    j = _api_get(url)
    pages = ((j or {}).get("query") or {}).get("pages") or []
    for p in pages:
        content = ((p.get("revisions") or [{}])[0].get("slots") or {}).get(
            "main", {}
        ).get("content")
        if isinstance(content, str):
            return content
    return ""


def fetch_article(wikidata_id: str) -> tuple[str, str]:
    """Meilleur article disponible : fr d'abord, en en secours.
    Retourne (lang, wikitext) — ("", "") si aucun article n'est trouvé."""
    titles = resolve_title(wikidata_id)
    for lang in ("fr", "en"):
        title = titles.get(lang)
        if not title:
            continue
        wikitext = fetch_wikitext(lang, title)
        if wikitext:
            return lang, wikitext
    return "", ""


def _infobox_excerpt(wikitext: str, limit: int = 3500) -> str:
    """Début utile de l'article : l'infobox (garde, taille, allonge, record)
    puis l'intro — le wikitext des infobox déborde rarement 3500 caractères."""
    start = wikitext.find("{{Infobox")
    if start == -1:
        start = 0
    return wikitext[start:start + limit]


# ---------------------------------------------------------------------------
# Vérification LLM
# ---------------------------------------------------------------------------


_PROMPT_TEMPLATE = '''Tu es un vérificateur de données de boxe. À partir de l'extrait d'article Wikipedia ci-dessous, vérifie les champs suivants du boxeur « {name} » : la garde (stance), la taille (cm), l'allonge (reach, cm) et le palmarès professionnel (wins, losses, draws, ko).

Règles STRICTES :
- Ne renseigne un champ que s'il apparaît clairement dans l'extrait (infobox ou texte). Jamais d'invention.
- Champ absent ou illisible → "value": null et "confidence": 0.
- Garde (fr) : « Gaucher » / « Southpaw » → "Southpaw" ; « Fausse patte » → "Southpaw" ; « Orthodoxe » → "Orthodoxe" ; « Ambidextre » → "Switch".
- Taille et allonge : convertis en centimètres (ex. « 5 ft 8 in » → 173, « 1,91 m » → 191).
- Palmarès : ne renseigne wins/losses/draws/ko que s'ils sont explicites (infobox « victoires/défaites/matchs nuls/KO » ou tableau de carrière).
- « quote » : recopie la citation EXACTE de l'extrait qui appuie la valeur (un bout de phrase).
- confidence : 0.0 si le champ est absent ou incertain, 1.0 si la citation est explicite et sans ambiguïté.

Extrait (début de l'article, wikitext) :
"""
{excerpt}
"""

Réponds UNIQUEMENT en JSON strict, sans aucun commentaire, au format :
{
  "stance": {"value": "Southpaw" | null, "confidence": 0.0, "quote": ""},
  "height_cm": {"value": 191 | null, "confidence": 0.0, "quote": ""},
  "reach_cm": {"value": 198 | null, "confidence": 0.0, "quote": ""},
  "record": {"value": {"wins": 25, "losses": 0, "draws": 0, "ko": 16} | null, "confidence": 0.0, "quote": ""}
}'''


def _build_prompt(name: str, excerpt: str) -> str:
    return _PROMPT_TEMPLATE.replace("{name}", name).replace("{excerpt}", excerpt)


def _norm_stance(value: object) -> object:
    """Normalise la garde renvoyée par le LLM (majuscules, fr) → schéma."""
    if not isinstance(value, str):
        return value
    v = value.strip().lower()
    if v in ("orthodoxe", "orthodox", "garde orthodoxe"):
        return "Orthodoxe"
    if v in ("southpaw", "gaucher", "gauchère", "fausse patte"):
        return "Southpaw"
    if v in ("switch", "ambidextre"):
        return "Switch"
    return value


def _normalize_response(data: dict) -> dict:
    """Coerce la réponse JSON du LLM vers le contrat du rapport."""
    fields: dict = {}
    for field in PROFILE_FIELDS:
        raw = data.get(field) if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            fields[field] = {"value": None, "confidence": 0.0, "quote": ""}
            continue
        value = raw.get("value")
        if field == "stance":
            value = _norm_stance(value)
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        quote = raw.get("quote") or ""
        if not isinstance(quote, str):
            quote = ""
        # un champ non conforme au schéma n'est jamais « confirmé »
        if value is not None and not _FIELD_VALIDATORS[field](value):
            value = None
            confidence = 0.0
        fields[field] = {"value": value, "confidence": confidence, "quote": quote}
    return fields


def verify_one(client, profile: dict, wikitext: str) -> dict:
    """Demande au LLM de vérifier un profil contre l'article → champs."""
    prompt = _build_prompt(profile["name"], _infobox_excerpt(wikitext))
    data = client.complete_json(prompt, max_tokens=1500)
    return _normalize_response(data)


def verify_profiles(
    client,
    candidates: list[dict],
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Vérifie tous les candidats (Wikipedia → LLM) → rapport complet."""
    if not client.available:
        raise RuntimeError(
            "aucune clé API (GEMINI/GROQ/MISTRAL) — la vérification LLM est inactive"
        )
    profiles: list[dict] = []
    errors: list[str] = []
    total = len(candidates)
    for i, cand in enumerate(candidates, start=1):
        slug, name = cand["slug"], cand["name"]
        if progress:
            progress(i, total, name)
        lang, wikitext = fetch_article(cand["wikidata_id"])
        if not wikitext:
            errors.append(f"{slug} : article Wikipedia introuvable (QID {cand['wikidata_id']})")
            continue
        try:
            fields = verify_one(client, cand, wikitext)
        except Exception as exc:  # noqa: BLE001 — un échec ne bloque pas le lot
            errors.append(f"{slug} : échec LLM — {exc}")
            continue
        profiles.append({
            "slug": slug,
            "name": name,
            "lang": lang,
            "fields": fields,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": client.provider_name,
        "profiles": profiles,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Application déterministe (le LLM ne décide jamais)
# ---------------------------------------------------------------------------


def apply_report(
    report: dict,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Détermine les corrections à appliquer : chaque champ confirmé avec une
    confiance ≥ `threshold` ET conforme au schéma. Zéro invention — un champ
    non confirmé n'est jamais appliqué. Retourne la liste des profils
    corrigés (slug, name, corrections)."""
    if not 0 <= threshold <= 1:
        raise ValueError(f"threshold hors bornes : {threshold}")
    out: list[dict] = []
    for prof in report.get("profiles", []):
        corrections: dict = {}
        for field in PROFILE_FIELDS:
            entry = (prof.get("fields") or {}).get(field)
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            confidence = entry.get("confidence", 0.0)
            if value is None or confidence < threshold:
                continue
            if not _FIELD_VALIDATORS[field](value):
                continue
            corrections[field] = value
        if corrections:
            out.append({
                "slug": prof["slug"],
                "name": prof.get("name", prof["slug"]),
                "corrections": corrections,
            })
    return out


def save_report(report: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out