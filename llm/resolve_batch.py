"""Batch de recoupement LLM — tranche les paires de noms suspectes.

Le déterministe du pipeline (`annuaire/resolve.py`) résout par QID, slug
exact et similarité. Les paires AMBIGUËS (similarité 0.80-0.95) lui
échappent : ce batch les soumet au LLM avec le CONTEXTE (pays, catégorie,
date de naissance, sources) pour décider « même personne ? ».

Sortie : un rapport JSON avec les fusions recommandées (paires « same »
à confiance haute) — prêt pour une étape de fusion dans les shards.

Garde-fou : le LLM ne produit qu'une DÉCISION (same/different +
confiance), jamais de données. Une fusion n'est recommandée que si
`same == True` ET `confidence >= CONFIDENCE_THRESHOLD` (0.9).

Usage :
    from llm.resolve_batch import run_resolve_batch
    report = run_resolve_batch(client, "merged.json", max_pairs=20)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from llm.client import LLMClient, LLMError
from llm.resolve import decide_same_person, suspicious_pairs

# Fusion recommandée uniquement si confiance ≥ ce seuil (garde-fou)
CONFIDENCE_THRESHOLD = 0.9


def _context(entry: dict) -> str:
    """Contexte d'une fiche pour aider le LLM (pays, catégorie, naissance)."""
    parts = []
    if entry.get("country"):
        parts.append(f"pays {entry['country']}")
    if entry.get("weight_class"):
        parts.append(f"catégorie {entry['weight_class']}")
    if entry.get("birth_date"):
        parts.append(f"né {entry['birth_date']}")
    if entry.get("orgs"):
        parts.append(f"combats officiels ({', '.join(entry['orgs'][:3])})")
    return "; ".join(parts)


def run_resolve_batch(
    client: LLMClient,
    annuaire_path: str,
    max_pairs: int = 20,
    pause: float = 15.0,
    progress=None,
) -> dict:
    """Tranche les paires suspectes d'un annuaire merged.json via le LLM.

    `pause` : espace entre deux appels LLM — défaut 15 s (le free tier
    Gemini est ~2-4 req/min sur ce compte, un appel par paire suffit à
    saturer le quota minute).

    Retourne {
        "annuaire": chemin,
        "paires_suspectes": n,
        "paires_analysees": n,
        "fusions": [{name_a, name_b, confidence, reason}],  # à fusionner
        "distinctes": [{name_a, name_b, reason}],           # à garder séparées
        "inconclusives": [...],                             # confiance < seuil
        "errors": [...],
    }
    """
    if not client.available:
        raise LLMError("pas de clé GEMINI_API_KEY — LLM inactif")

    annuaire = Path(annuaire_path)
    data = json.loads(annuaire.read_text(encoding="utf-8"))
    names = [
        (b.get("name", ""), _context(b))
        for b in data
        if b.get("name")
    ]
    pairs = suspicious_pairs(names)
    report = {
        "annuaire": str(annuaire),
        "paires_suspectes": len(pairs),
        "paires_analysees": 0,
        "fusions": [],
        "distinctes": [],
        "inconclusives": [],
        "errors": [],
    }

    for i, (name_a, name_b, ratio) in enumerate(pairs[:max_pairs]):
        if progress:
            progress(i + 1, min(max_pairs, len(pairs)), name_a, name_b)
        ctx_a = next(c for n, c in names if n == name_a)
        ctx_b = next(c for n, c in names if n == name_b)
        try:
            verdict = decide_same_person(client, name_a, ctx_a, name_b, ctx_b)
        except LLMError as exc:
            report["errors"].append(f"{name_a} vs {name_b} : {exc}")
            continue
        report["paires_analysees"] += 1
        entry = {
            "name_a": name_a,
            "name_b": name_b,
            "confidence": verdict["confidence"],
            "reason": verdict.get("reason", ""),
        }
        if verdict["same"] and verdict["confidence"] >= CONFIDENCE_THRESHOLD:
            report["fusions"].append(entry)
        elif not verdict["same"]:
            report["distinctes"].append(entry)
        else:
            report["inconclusives"].append(entry)  # same mais confiance basse
        if pause:
            time.sleep(pause)
    return report


def save_report(report: dict, path: str) -> None:
    """Écrit le rapport de recoupement en JSON (UTF-8, lisible)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
