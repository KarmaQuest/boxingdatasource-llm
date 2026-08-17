"""Vérification des combats PROGRAMMÉS (shards fights-upcoming/).

Rôle : garde-fou SUR la programmation du pipeline, comme `extract`/`resolve`
le sont pour les résultats — le LLM ne fabrique jamais de données, il
VALIDE ce que le déterministe a extrait des calendriers officiels.

Deux passes :

1. **Règles déterministes** (zéro LLM, zéro coût) — chaque combat :
   - `future_date`   : la date est dans le futur ;
   - `plausible_date`: pas au-delà de 18 mois (un calendrier annonce
     rarement plus loin ; au-delà = suspect) ;
   - `names_ok`      : les deux noms sont présents, différents, non vides ;
   - `annuaire`      : au moins un des deux boxeurs est connu de l'annuaire
     recoupé (`boxers/merged.json`, 19 586 boxeurs) — par nom exact
     normalisé, sinon par nom de famille ;
   - `no_dup_across_orgs` : la même affiche (paire de noms) n'est PAS
     annoncée par deux organisations avec des dates différentes (True =
     pas de doublon = bon).

2. **Pass LLM** (si `GEMINI_API_KEY` dispo) — uniquement sur les combats
   qui ont échoué au moins une règle : le LLM juge la plausibilité réelle
   (boxeurs existants ? affiche crédible ? catégorie cohérente ?). Verdict
   `confirmed` / `flagged` + score de confiance + raison. Le rapport final
   liste tout : `confirmed` = publiable tel quel, `flagged` = à revoir.

Sortie : `fights-upcoming-verification.json` (côté pipeline, consommé par
le front pour marquer les combats « vérifiés par IA » si souhaité).

Usage :
    python main.py verify --shards ../boxing-app/public/data/fights-upcoming \
                          --annuaire ../boxing-app/public/data/boxers/merged.json \
                          [--output fights-upcoming-verification.json]
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from llm.client import get_default_client

# Un calendrier n'annonce pas à plus de 18 mois — au-delà = date suspecte.
MAX_HORIZON_MONTHS = 18

_ANNUAIRE = Path(__file__).resolve().parents[1] / ".." / "boxing-app" / "public" / "data" / "boxers" / "merged.json"


def _norm(s: str) -> str:
    """Comparable : minuscules, accents ôtés, non-alphanumériques retirés."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z ]", " ", s).strip()


def _surname(s: str) -> str:
    parts = _norm(s).split()
    return parts[-1] if parts else ""


class Annuaire:
    """Index des noms connus (merged.json) pour le contrôle d'existence.

    Chargé une seule fois : noms exacts normalisés + noms de famille (un
    boxeur est « connu » si son nom complet ou son nom de famille matche).
    """

    def __init__(self, path: Path) -> None:
        self.names: set[str] = set()
        self.surnames: set[str] = set()
        self.loaded = False
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            boxers = data if isinstance(data, list) else data.get("boxers", [])
            for b in boxers:
                name = _norm(b.get("name", ""))
                if name:
                    self.names.add(name)
                    sur = name.split()[-1]
                    if len(sur) >= 3:
                        self.surnames.add(sur)
                for alias in b.get("aliases", ()) or ():
                    a = _norm(alias)
                    if a:
                        self.names.add(a)
            self.loaded = True

    def knows(self, fighter_name: str) -> bool:
        if not self.loaded:
            return False  # annuaire absent → on ne peut pas confirmer
        n = _norm(fighter_name)
        if not n:
            return False
        if n in self.names:
            return True
        sur = n.split()[-1]
        return len(sur) >= 3 and sur in self.surnames


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _scheduled_from_shards(shards_dir: Path) -> list[dict]:
    """Charge tous les shards fights-upcoming/*.json, en gardant l'org."""
    fights: list[dict] = []
    if not shards_dir.exists():
        return fights
    for path in sorted(shards_dir.glob("*.json")):
        if path.name.endswith("-index.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for f in data:
            f = dict(f)
            f.setdefault("org_slug", path.stem)
            fights.append(f)
    return fights


def deterministic_checks(
    fight: dict, today: date, annuaire: Annuaire, pair_orgs: dict
) -> dict[str, bool]:
    """Applique les règles déterministes → dict check → bool."""
    d = _parse_date(fight.get("date", ""))
    a = fight.get("fighter_a", "")
    b = fight.get("fighter_b", "")

    future_date = bool(d and d > today)
    plausible_date = bool(d and d <= today + timedelta(days=MAX_HORIZON_MONTHS * 31))

    names_ok = (
        bool(a and b)
        and len(_norm(a)) >= 3
        and len(_norm(b)) >= 3
        and _norm(a) != _norm(b)
    )

    annuaire_ok = annuaire.knows(a) or annuaire.knows(b)

    # paire (noms normalisés triés) → ensemble des orgs qui l'annoncent
    pair = "|".join(sorted((_norm(a), _norm(b))))
    orgs = pair_orgs.get(pair, set())
    # True = PAS de doublon inter-orgs (bon) — cohérent avec les autres
    # checks où « True » signifie « la règle est respectée ».
    no_dup_across_orgs = len(orgs) <= 1

    return {
        "future_date": future_date,
        "plausible_date": plausible_date,
        "names_ok": names_ok,
        "annuaire": annuaire_ok,
        "no_dup_across_orgs": no_dup_across_orgs,
    }


_LLM_PROMPT = """Tu es un expert en boxe chargé de VÉRIFIER une affiche annoncée par une organisation officielle. Tu ne dois PAS inventer d'information : tu évalues la plausibilité à partir de ta connaissance du sport.

Combat programmé :
- Date : {date}
- Organisation : {org}
- Catégorie : {weight_class}
- Boxeur A : {a}
- Boxeur B : {b}
- Type : {bout_type}
- Lieu : {location}

Vérifie :
1. Les deux noms ressemblent-ils à de VRAIS boxeurs (pas des placeholders, pas de noms absurdes) ?
2. L'affiche est-elle plausible (les deux boxeurs existent au niveau annoncé, catégorie cohérente) ?
3. Tout doute sérieux sur la date, le lieu ou la catégorie ?

Réponds UNIQUEMENT un JSON valide :
{{"verdict": "confirmed" ou "flagged", "confidence": 0.0-1.0, "reason": "explication courte"}}

Si tu hésites, mets confidence < 0.5 et verdict "flagged"."""


def verify_with_llm(
    client, fight: dict, checks: dict[str, bool]
) -> dict:
    """Jugement LLM de plausibilité d'un combat programmé (JSON strict)."""
    try:
        data = client.complete_json(
            _LLM_PROMPT.format(
                date=fight.get("date", "?"),
                org=fight.get("org", fight.get("org_slug", "?")),
                weight_class=fight.get("weight_class", "?"),
                a=fight.get("fighter_a", "?"),
                b=fight.get("fighter_b", "?"),
                bout_type=fight.get("bout_type", "") or "—",
                location=fight.get("location", "") or "—",
            ),
            max_tokens=300,
        )
    except Exception as exc:
        return {"verdict": "flagged", "confidence": 0.0,
                "reason": f"LLM indisponible : {exc}"}
    verdict = data.get("verdict") if isinstance(data, dict) else None
    if verdict not in ("confirmed", "flagged"):
        return {"verdict": "flagged", "confidence": 0.0,
                "reason": f"réponse LLM invalide : {data!r}"}
    return {
        "verdict": verdict,
        "confidence": float(data.get("confidence", 0.0)),
        "reason": str(data.get("reason", ""))[:200],
    }


def verify_schedule(
    shards_dir: Path,
    annuaire_path: Optional[Path] = None,
    today: Optional[date] = None,
    max_llm: int = 60,
    use_llm: bool = True,
) -> dict:
    """Vérifie tous les combats programmés des shards.

    Retourne le rapport : {generated_at, total, confirmed, flagged, items}.
    Les combats `flagged` sont ceux qui ont échoué une règle déterministe
    OU reçu un verdict LLM négatif.
    """
    today = today or date.today()
    annuaire = Annuaire(annuaire_path or _ANNUAIRE)
    fights = _scheduled_from_shards(shards_dir)

    # index des paires → orgs (dédup inter-orgs)
    pair_orgs: dict[str, set[str]] = {}
    for f in fights:
        a, b = _norm(f.get("fighter_a", "")), _norm(f.get("fighter_b", ""))
        if a and b:
            pair_orgs.setdefault("|".join(sorted((a, b))), set()).add(
                f.get("org_slug", "?")
            )

    client = get_default_client() if use_llm else None
    llm_available = bool(client and client.available)

    items: list[dict] = []
    llm_calls = 0
    for f in fights:
        checks = deterministic_checks(f, today, annuaire, pair_orgs)
        failed = [k for k, ok in checks.items() if not ok]

        llm: dict = {}
        if failed and llm_available and llm_calls < max_llm:
            llm = verify_with_llm(client, f, checks)
            llm_calls += 1
            status = (
                "confirmed" if llm.get("verdict") == "confirmed" else "flagged"
            )
        elif failed:
            status = "flagged"  # pas de LLM dispo → la règle déterministe prime
        else:
            status = "confirmed"  # toutes les règles passent, LLM inutile

        items.append({
            "id": f.get("id", ""),
            "org": f.get("org_slug", ""),
            "date": f.get("date", ""),
            "weight_class": f.get("weight_class", ""),
            "fighter_a": f.get("fighter_a", ""),
            "fighter_b": f.get("fighter_b", ""),
            "checks": checks,
            "failed_checks": failed,
            "llm": llm,
            "status": status,
        })

    confirmed = sum(1 for i in items if i["status"] == "confirmed")
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "annuaire_loaded": annuaire.loaded,
        "llm_available": llm_available,
        "llm_calls": llm_calls,
        "total": len(items),
        "confirmed": confirmed,
        "flagged": len(items) - confirmed,
        "items": items,
    }


def write_report(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="verify", description=__doc__)
    parser.add_argument("--shards", required=True, help="dossier fights-upcoming/")
    parser.add_argument("--annuaire", default=str(_ANNUAIRE),
                        help="boxers/merged.json (annuaire recoupé)")
    parser.add_argument("--output", default="fights-upcoming-verification.json")
    parser.add_argument("--no-llm", action="store_true", help="règles déterministes seules")
    parser.add_argument("--max-llm", type=int, default=60)
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    report = verify_schedule(
        Path(args.shards),
        annuaire_path=Path(args.annuaire),
        max_llm=args.max_llm,
        use_llm=not args.no_llm,
    )
    out = Path(args.output)
    write_report(report, out)
    print(f"Vérification : {report['total']} combats — "
          f"{report['confirmed']} confirmés, {report['flagged']} à revoir")
    if not report["annuaire_loaded"]:
        print(f"  ⚠️ annuaire non chargé ({args.annuaire}) — contrôle existence inactif")
    print(f"  LLM : {'disponible' if report['llm_available'] else 'absent'} "
          f"({report['llm_calls']} appels)")
    for item in report["items"]:
        if item["status"] == "flagged":
            print(f"  🚩 {item['org']} {item['date']} — "
                  f"{item['fighter_a']} vs {item['fighter_b']} "
                  f"[{', '.join(item['failed_checks'])}] "
                  f"{item['llm'].get('reason', '')}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
