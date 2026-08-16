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

# Recoupement : noms ambigus entre Wikidata / Big Balls / combats des orgs
python main.py resolve --name "O. Usyk" --candidates "Oleksandr Usyk" "Olexandr Usyk"

# Rapport complet de recoupement sur les shards du pipeline
python main.py report --annuaire ../boxing-app/public/data/boxers/merged.json
```

## Tests

```bash
python -m unittest discover -s tests
```
