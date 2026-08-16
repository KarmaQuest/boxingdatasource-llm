#!/usr/bin/env python3
"""CLI du module boxingdatasource-llm — recherche/recoupement par LLM.

    python main.py extract --text "…" --date 2026-08-12 [--source wbc]
        Extrait les combats d'un article WBC/WBO (prose) via le LLM.
    python main.py resolve --name-a "O. Usyk" --name-b "Oleksandr Usyk"
        Demande au LLM si deux mentions désignent le même boxeur.
    python main.py report --annuaire ../boxing-app/public/data/boxers/merged.json
        Détecte les paires de noms à similarité ambigüe dans un annuaire.

Sans GEMINI_API_KEY, les commandes LLM échouent proprement (message clair) ;
`report` fonctionne sans LLM (il liste les paires suspectes à trancher).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def cmd_extract(args) -> int:
    from llm.client import get_default_client
    from llm.extract import extract_fights_llm

    client = get_default_client()
    if not client.available:
        print("❌ GEMINI_API_KEY absente — le LLM est inactif.")
        print("   Ajoute-la : $env:GEMINI_API_KEY=… ou boxing-app/.env.local")
        return 1
    fights = extract_fights_llm(
        client, args.text, args.date, source=args.source
    )
    print(f"✅ {len(fights)} combats extraits de la prose ({args.source})")
    for f in fights:
        print(f"   {f['winner']} bat {f['loser']} ({f['method'] or '?'} "
              f"r{f['rounds'] or '?'}) — {f['weight_class'] or 'catégorie ?'}"
              f"{' [titre]' if f['is_title_fight'] else ''}")
    return 0


def cmd_resolve(args) -> int:
    from llm.client import get_default_client
    from llm.resolve import decide_same_person

    client = get_default_client()
    if not client.available:
        print("❌ GEMINI_API_KEY absente — le LLM est inactif.")
        return 1
    verdict = decide_same_person(
        client, args.name_a, args.ctx_a or "", args.name_b, args.ctx_b or ""
    )
    label = "MÊME personne" if verdict["same"] else "personnes DIFFÉRENTES"
    print(f"{args.name_a!r} vs {args.name_b!r} → {label} "
          f"(confiance {verdict['confidence']:.2f})")
    print(f"   raison : {verdict.get('reason', '')}")
    return 0


def cmd_report(args) -> int:
    from llm.resolve import suspicious_pairs

    annuaire = Path(args.annuaire)
    if not annuaire.exists():
        print(f"❌ Annuaire introuvable : {annuaire}")
        return 1
    data = json.loads(annuaire.read_text(encoding="utf-8"))
    names = [
        (b.get("name", ""), b.get("country", "") or b.get("weight_class", ""))
        for b in data
        if b.get("name")
    ]
    # O(n²) : on échantillonne pour rester rapide sur 20 k fiches
    sampled = names[: args.max_names]
    pairs = suspicious_pairs(sampled, args.lower, args.upper)
    print(f"{len(data)} fiches — échantillon {len(sampled)} noms → "
          f"{len(pairs)} paires à similarité ambigüe "
          f"({args.lower}-{args.upper}) à faire trancher par le LLM :")
    for a, b, ratio in pairs[:20]:
        print(f"   {ratio:.2f}  {a!r}  vs  {b!r}")
    if len(pairs) > 20:
        print(f"   … et {len(pairs) - 20} autres")
    print("\nTranche-les : python main.py resolve --name-a … --name-b …")
    return 0


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(prog="llm", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("extract", help="extrait les combats d'un article (prose)")
    p.add_argument("--text", required=True, help="texte de l'article")
    p.add_argument("--date", required=True, help="date de publication (YYYY-MM-DD)")
    p.add_argument("--source", default="wbc", choices=["wbc", "wbo"])
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("resolve", help="le même boxeur ? (LLM)")
    p.add_argument("--name-a", required=True)
    p.add_argument("--ctx-a", default="")
    p.add_argument("--name-b", required=True)
    p.add_argument("--ctx-b", default="")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("report", help="paires suspectes dans un annuaire (sans LLM)")
    p.add_argument("--annuaire", required=True, help="chemin vers merged.json")
    p.add_argument("--lower", type=float, default=0.80)
    p.add_argument("--upper", type=float, default=0.95)
    p.add_argument("--max-names", type=int, default=2000,
                   help="échantillon de noms comparés (O(n²), défaut 2000)")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
