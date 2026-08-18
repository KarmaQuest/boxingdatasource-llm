---
name: stdlib-unittest
description: Conventions de tests de boxingdatasource-llm (stdlib unittest, zéro dépendance pip, FakeClient, fixtures réelles). Utiliser quand on écrit ou modifie tests/.
risk: low
source: custom
date_added: '2026-08-18'
---
Le module `boxingdatasource-llm` est **stdlib pur** (urllib, re, json,
difflib) : aucun framework de test externe, pas de pytest. Les tests vivent
dans `tests/` et se lancent avec :

```bash
python -m unittest discover -s tests -v
```

## Règles

1. **Zéro dépendance** : ne pas importer pytest, mock externe, etc.
2. **FakeClient** : sans `GEMINI_API_KEY`, le LLM est inactif (`available ==
   False`) mais testable — les tests utilisent un faux client qui renvoie des
   réponses JSON codées en dur (fixtures réelles quand possible).
3. **Fixtures réelles** : extraire des vrais articles/combats observés (ex.
   « Moriana (Lauriaga ») pour ancrer les tests dans la réalité.
4. **Couvrir les cas limites** : parenthèses non fermées, réponses non-JSON
   (fences markdown, texte bavard, accolades dans les chaînes), méthode
   inconnue, rounds invalides, absence de clé.
5. **Un test = un comportement** : nommer `test_*`, rester lisible.
6. **Ne pas lancer de vrai réseau** dans les tests : les appels HTTP et LLM
   sont mockés/fixturés.

## Couverture actuelle (82 tests)

- `test_client.py` (8) : clé, disponibilité, erreur propre sans clé, monitoring.
- `test_extract.py` (15) : prompt, validation, parsing JSON, extraction.
- `test_resolve.py` (11) : verdicts, décision, paires suspectes (0.80–0.95).
- `test_batch.py` (11) : filtres de titres, run_batch WBC/WBO, tolérance aux pannes.
- `test_resolve_batch.py` (6) : contexte, fusions/distinctes/inconclusives, erreurs.
- `test_integrate.py` (8) : to_pipeline_fight, fights_to_pipeline_format.
- `test_merge.py` (9) : fusion smart des shards (dédup date+paire normalisée).
- `test_verify.py` (9) : vérification des combats à venir (crédibilité, LLM).
- `test_status.py` (5) : rapport d'état local (fichiers, vérification, config).

Toute nouvelle fonctionnalité doit être livrée **avec ses tests** et le
compteur total doit rester vert.
