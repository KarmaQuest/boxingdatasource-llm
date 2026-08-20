#!/usr/bin/env python3
"""CLI du module boxingdatasource-llm — recherche/recoupement par LLM.

    python main.py extract --text "…" --date 2026-08-12 [--source wbc]
        Extrait les combats d'un article WBC/WBO (prose) via le LLM.
    python main.py resolve --name-a "O. Usyk" --name-b "Oleksandr Usyk"
        Demande au LLM si deux mentions désignent le même boxeur.
    python main.py report --annuaire ../boxing-app/public/data/boxers/merged.json
        Détecte les paires de noms à similarité ambigüe dans un annuaire.
    python main.py batch --source wbc --year 2026 --output batch.json
        Extrait les combats de TOUS les articles d'une source (LLM).
    python main.py resolve-batch --annuaire merged.json --max-pairs 20
        Tranche les paires suspectes d'un annuaire (LLM).
    python main.py verify-profiles --annuaire …/merged.json --output report.json
        Vérifie les profils boxeurs contre Wikipedia (LLM, rapport) — puis
        applique avec : pipeline apply-profiles --report report.json.

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


def cmd_batch(args) -> int:
    from llm.batch import run_batch, save_results
    from llm.client import get_default_client

    client = get_default_client()
    if not client.available:
        print("❌ GEMINI_API_KEY absente — le LLM est inactif.")
        return 1

    def progress(i, total, title):
        print(f"   [{i}/{total}] {title[:60]}")

    print(f"Batch {args.source} (max {args.max_articles} articles)…")
    results = run_batch(
        client, args.source, year=args.year,
        max_articles=args.max_articles, progress=progress,
    )
    print(f"\n✅ {results['articles_ok']}/{results['articles']} articles "
          f"parsés — {results['total_fights']} combats extraits par le LLM")
    for title, date, fights in [
        (c["title"], c["date"], c["fights"]) for c in results["combats"]
    ]:
        for f in fights:
            print(f"   {date} {f['winner']} bat {f['loser']} "
                  f"({f['method'] or '?'} r{f['rounds'] or '?'})")
    if results["errors"]:
        print(f"\n⚠️ {len(results['errors'])} articles en échec :")
        for e in results["errors"][:5]:
            print(f"   {e[:100]}")
    if args.output:
        save_results(results, args.output)
        print(f"\n✍️  {args.output}")
    return 0


def cmd_resolve_batch(args) -> int:
    from llm.client import get_default_client
    from llm.resolve_batch import run_resolve_batch, save_report

    client = get_default_client()
    if not client.available:
        print("❌ GEMINI_API_KEY absente — le LLM est inactif.")
        return 1

    def progress(i, total, name_a, name_b):
        print(f"   [{i}/{total}] {name_a!r} vs {name_b!r}")

    report = run_resolve_batch(
        client, args.annuaire, max_pairs=args.max_pairs, progress=progress,
    )
    print(f"\n{report['paires_suspectes']} paires suspectes détectées — "
          f"{report['paires_analysees']} analysées")
    print(f"✅ {len(report['fusions'])} fusions recommandées (confiance ≥ 0.9) :")
    for f in report["fusions"]:
        print(f"   {f['name_a']!r} = {f['name_b']!r} "
              f"(confiance {f['confidence']:.2f})")
    print(f"➖ {len(report['distinctes'])} paires distinctes confirmées")
    print(f"❓ {len(report['inconclusives'])} inconclusives "
          f"(confiance < 0.9)")
    if report["errors"]:
        print(f"⚠️ {len(report['errors'])} erreurs :")
        for e in report["errors"][:5]:
            print(f"   {e[:110]}")
    if args.output:
        save_report(report, args.output)
        print(f"\n✍️  {args.output}")
    return 0


def cmd_integrate(args) -> int:
    from llm.integrate import fights_to_pipeline_format

    batch = json.loads(Path(args.input).read_text(encoding="utf-8"))
    fights = fights_to_pipeline_format(batch, source=args.source,
                                       default_date=args.date)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fights, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"✅ {len(fights)} combats au format pipeline (source {args.source})")
    for f in fights[:10]:
        print(f"   {f['date']} {f['fighter_a']} bat {f['fighter_b']} "
              f"({f['method']} r{f['rounds'] or '?'})")
    if len(fights) > 10:
        print(f"   … et {len(fights) - 10} autres")
    if args.output:
        print(f"\n✍️  {args.output}")
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


def cmd_verify_profiles(args) -> int:
    from llm.client import get_default_client
    from llm.profiles import (
        apply_report,
        load_candidates,
        save_report,
        verify_profiles,
    )

    client = get_default_client()
    if not client.available:
        print("❌ GEMINI_API_KEY absente — le LLM est inactif.")
        return 1

    candidates = load_candidates(args.annuaire, limit=args.limit)
    print(f"{len(candidates)} profils à vérifier (merged.json, Wikidata + "
          f"physique incomplète)…")

    def progress(i, total, name):
        print(f"   [{i}/{total}] {name}")

    report = verify_profiles(client, candidates, progress=progress)
    save_report(report, args.output)

    corrections = apply_report(report, threshold=args.threshold)
    print(f"\n✅ {len(report['profiles'])} profils vérifiés — "
          f"{len(corrections)} à corriger (confiance ≥ {args.threshold:.1f}) :")
    for c in corrections:
        for field, value in c["corrections"].items():
            print(f"   {c['name']} → {field} = {value}")
    if report["errors"]:
        print(f"\n⚠️ {len(report['errors'])} échecs :")
        for e in report["errors"][:5]:
            print(f"   {e[:110]}")
    print(f"\n✍️  {args.output}")
    print("Applique : pipeline apply-profiles --report "
          f"{args.output} --annuaire {args.annuaire}")
    return 0


def cmd_status(args) -> int:
    """Rapport de synthèse : sorties générées + vérification + config — zéro
    appel LLM. `--json` pour un objet stable consommé par boxing-ops."""
    import json as _json

    from status import collect_status, render_human

    status = collect_status()
    if args.json:
        print(_json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(render_human(status))
    return 0


def cmd_verify(args) -> int:
    from llm.verify import main as verify_main

    argv = ["--shards", args.shards, "--output", args.output,
            "--max-llm", str(args.max_llm)]
    if args.annuaire:
        argv += ["--annuaire", args.annuaire]
    if args.no_llm:
        argv += ["--no-llm"]
    return verify_main(argv)


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

    p = sub.add_parser("batch", help="extrait les combats de tous les articles d'une source (LLM)")
    p.add_argument("--source", required=True, choices=["wbc", "wbo"])
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--max-articles", type=int, default=50)
    p.add_argument("--output", default="", help="chemin JSON de sortie")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("resolve-batch", help="tranche les paires suspectes d'un annuaire (LLM)")
    p.add_argument("--annuaire", required=True, help="chemin vers merged.json")
    p.add_argument("--max-pairs", type=int, default=20,
                   help="nombre max de paires analysées (défaut 20)")
    p.add_argument("--output", default="", help="chemin JSON du rapport")
    p.set_defaults(func=cmd_resolve_batch)

    p = sub.add_parser("integrate", help="convertit un batch LLM au format pipeline (Fight)")
    p.add_argument("--input", required=True, help="JSON du batch (run_batch)")
    p.add_argument("--source", required=True, choices=["wbc", "wbo"])
    p.add_argument("--date", default="", help="date par défaut si absente")
    p.add_argument("--output", default="", help="chemin JSON de sortie")
    p.set_defaults(func=cmd_integrate)

    p = sub.add_parser("report", help="paires suspectes dans un annuaire (sans LLM)")
    p.add_argument("--annuaire", required=True, help="chemin vers merged.json")
    p.add_argument("--lower", type=float, default=0.80)
    p.add_argument("--upper", type=float, default=0.95)
    p.add_argument("--max-names", type=int, default=2000,
                   help="échantillon de noms comparés (O(n²), défaut 2000)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("verify", help="vérifie les combats programmés (règles + LLM)")
    p.add_argument("--shards", required=True, help="dossier fights-upcoming/")
    p.add_argument("--annuaire", default="", help="boxers/merged.json (défaut : app)")
    p.add_argument("--output", default="fights-upcoming-verification.json")
    p.add_argument("--no-llm", action="store_true",
                   help="règles déterministes seules (zéro appel LLM)")
    p.add_argument("--max-llm", type=int, default=60)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("verify-profiles",
                       help="vérifie les profils boxeurs (Wikipedia → LLM, rapport)")
    p.add_argument("--annuaire", required=True, help="boxers/merged.json")
    p.add_argument("--output", default="profiles-verification.json",
                   help="chemin JSON du rapport")
    p.add_argument("--limit", type=int, default=0,
                   help="max de profils analysés (0 = tous les candidats)")
    p.add_argument("--threshold", type=float, default=0.9,
                   help="seuil d'application affiché (défaut 0.9)")
    p.set_defaults(func=cmd_verify_profiles)

    p = sub.add_parser("status", help="rapport de synthèse (sorties, vérification, config)")
    p.add_argument("--json", action="store_true",
                   help="sortie JSON stable (consommé par boxing-ops)")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
