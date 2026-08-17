# boxingdatasource-llm

Recherche et recoupement par IA (LLM) sur les données sources du pipeline.

Ce module est **séparé** de `boxingdatasource-pipeline` : le pipeline reste
100 % déterministe (collecte → shards), et l'IA n'intervient qu'ICI, là où
elle apporte de la valeur :

1. **Extraction prose** (`llm/extract.py`) — les articles WBC/WBO sont des
   récits en prose (« …the Japanese who won by unanimous decision ») que les
   parsers regex sautent. Le LLM extrait les combats de la prose, **validés
   ensuite par le schéma strict du pipeline** (`Fight.make`).
2. **Recoupement d'entités** (`llm/resolve.py`) — le même boxeur vu par
   Wikidata, Big Balls et les organisations avec des orthographes
   différentes (« O. Usyk », « Oleksandr Usyk », surnoms, Jr./Sr.). Le LLM
   tranche les cas ambigus que le déterministe (slug / similarité) ne
   résout pas.
3. **Vérification de la programmation** (`llm/verify.py`) — les combats
   à venir extraits des calendriers officiels (shards `fights-upcoming/`)
   passent des règles déterministes (date future, horizon ≤ 18 mois, noms
   valides, existence dans l'annuaire, doublon inter-orgs) puis un verdict
   LLM sur les cas douteux → rapport `fights-upcoming-verification.json` ;
   le front ne sert que les combats `confirmed`.

## Garde-fou absolu

Le LLM est un outil d'**extraction et de recoupement**, jamais une source
de vérité. Tout résultat généré est :

- validé contre le schéma (`config.schema` du pipeline) ;
- rejeté avec warning s'il ne passe pas ;
- croisé avec une source réelle avant d'être écrit.

## Prérequis

- Python ≥ 3.10 (aucune dépendance pip : urllib pur).
- Clé optionnelle `GEMINI_API_KEY` (Gemini 2.5 Flash, free tier
  ~1 500 req/jour) : `$GEMINI_API_KEY` > `boxing-app/.env.local` >
  `.env`. **Sans clé, le module reste testable** (mocking) et le pipeline
  fonctionne sans lui.

## Usage

```bash
# Extraction prose : articles WBC/WBO → combats valides
python main.py extract --source wbc --date 2026-08-12 --text "..."

# Recoupement : deux mentions désignent-elles le même boxeur ?
python main.py resolve --name-a "O. Usyk" --ctx-a "IBF, poids lourds" \
                       --name-b "Oleksandr Usyk" --ctx-b "Ukraine, né 1987"

# Paires suspectes dans un annuaire (sans LLM) — à faire trancher ensuite
python main.py report --annuaire ../boxing-app/public/data/boxers/merged.json

# BATCH : extrait les combats de TOUS les articles d'une source (WBC/WBO)
python main.py batch --source wbc --year 2026 --output batch-wbc.json

# BATCH : tranche les paires suspectes de l'annuaire (LLM, paire par paire)
python main.py resolve-batch --annuaire ../boxing-app/public/data/boxers/merged.json \
                             --max-pairs 20 --output resolve-report.json

# INTÉGRATION : convertit un batch au format Fight du pipeline (écriture
# par write_org_shard)
python main.py integrate --input batch-wbc.json --source wbc \
                         --output ../boxing-app/public/data/llm/fights-wbc.json

# VÉRIFICATION : combats programmés (règles déterministes + verdict LLM
# sur les cas douteux) → rapport confirmé/à-revoir
python main.py verify --shards ../boxing-app/public/data/fights-upcoming \
                      --annuaire ../boxing-app/public/data/boxers/merged.json \
                      --output ../boxing-app/public/data/fights-upcoming-verification.json
```

## Tests

```bash
python -m unittest discover -s tests
```
