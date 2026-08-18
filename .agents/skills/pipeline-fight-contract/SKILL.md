---
name: pipeline-fight-contract
description: Contrat de données Fight du pipeline boxingdatasource-pipeline (champs, méthodes, id SHA-256 déterministe, garde-fous, clé de fusion). Utiliser quand on manipule des combats dans boxingdatasource-llm (extract, integrate, merge_fights, verify).
risk: high
source: custom
date_added: '2026-08-18'
---
Vous travaillez sur le module `boxingdatasource-llm`, qui produit des données
destinées au pipeline `boxingdatasource-pipeline`. Le contrat JSON est le
seul point de contact : on ne **jamais** importe le code du pipeline ici.

## Le contrat `Fight`

Chaque combat DOIT respecter (validé par `Fight.make` du pipeline) :

```json
{
  "date": "2026-08-15",
  "location": "Las Vegas, NV",
  "weight_class": "Poids super-moyens",
  "fighter_a": "Canelo Álvarez",
  "fighter_b": "Christian Mbilli",
  "winner": "Canelo Álvarez | Draw | NC",
  "method": "UD",
  "rounds": 12,
  "is_title_fight": true
}
```

- **date** : `YYYY-MM-DD` strict.
- **method** : STRICT `UD | SD | MD | TKO | KO | DQ | PTS`. Méthode inconnue →
  champ vidé (laissé au schéma), jamais inventée.
- **winner** : nom exact d'un boxeur, `Draw` ou `NC`.
- **rounds** : entier ≥ 0. Invalide → 0.
- **id** : SHA-256 déterministe calculé par le pipeline (normalisation
  minuscules + espaces réduits). Le module LLM ne calcule pas l'id.

## Garde-fous

1. **Jamais de fabrication** : prompt strict « Ne JAMAIS inventer ». Un combat
   sans vainqueur clair est rejeté avec warning, jamais écrit.
2. **Validation locale miroir** : `fights_to_pipeline_format` doit rejeter
   tout combat non valide AVANT écriture (idem `Fight.validate`).
3. **Parenthèses non fermées** : le LLM tronque parfois un nom
   (« Moriana (Lauriaga ») → `_clean_value` retire le contenu entre
   parenthèses non fermées (fixture réelle testée).

## Fusion des shards (intégration LLM → shard)

L'id SHA-256 **ne suffit pas** : le même combat vu par spider et LLM a des ids
différents (méthode PTS vs UD, `weight_class`, nom tronqué). La fusion dans un
shard passe par la **clé date + paire de boxeurs normalisée** (`merge_fights`
dans `llm/integrate.py`) — guillemets, parenthèses non fermées, « Jr. »,
ponctuation ignorés ; les combats déjà présents (source déterministe)
restent prioritaires. Réutiliser `merge_fights` pour tout shard (`wbc`, `wbo`).
