# TASKS — boxingdatasource-llm 🥊🤖

> **Feuille de route.** Tâches du module LLM, priorisées par valeur / effort,
> avec critères d'acceptation. Dernière mise à jour : 17/08/2026.

---

## Étape 1 — Fondations : client + extraction prose — ✅ FAIT

- **Objectif** : socle testable sans clé ni dépendance — client Gemini free
  tier (urllib pur) + extraction des combats depuis la prose des articles
  WBC/WBO que les parsers regex sautent.
- **Fichiers** : `llm/client.py` (client REST, température 0, retries
  429/503, clé `$GEMINI_API_KEY` > `boxing-app/.env.local` > `.env`),
  `llm/extract.py` (prompt strict, `_validate_fight`, parsing JSON robuste),
  `tests/test_client.py` (5), `tests/test_extract.py` (15).
- **Acceptation** : ✅ 20 tests verts ; sans clé, le module est inactif
  (`available == False`) mais testable (FakeClient) ; une réponse non-JSON ou
  un combat invalide est rejeté avec `LLMError`/warning, jamais de fabrication.
- **Modèle retenu** : `gemini-flash-lite-latest` — ⚠️ 2.5-flash/2.0-flash →
  404 nouveaux comptes ; flash-latest → 429 « high demand » permanent.

---

## Étape 2 — Batch d'extraction (sous-étape 1) — ✅ FAIT

- **Objectif** : passer CHAQUE article d'une source au LLM et agréger les
  combats extraits, en volume, avec tolérance aux pannes.
- **Fichiers** : `llm/sources.py` (WBC via WP API, WBO via RSS paginé, 1
  req/s, retry 429/5xx), `llm/batch.py` (`run_batch`, `save_results`,
  filtres `_is_result_article` — preview/bio/récaps exclus, pause 4 s),
  `tests/test_batch.py` (10).
- **Acceptation** : ✅ 10 tests verts ; un article qui échoue au LLM n'arrête
  pas le lot ; seuls les titres de résultats probables consomment des appels.
- **Run réel (16/08/2026)** : sortie dans
  `boxing-app/public/data/llm/fights-wbc.json` → **5 combats WBC extraits**
  (Thorslund, Zepeda ×2, Ibrahim Mafia, Mchanja Yohana). Le fichier est au
  **format pipeline** (champs `fighter_a/fighter_b/source`) → c'est la sortie
  d'un `integrate` (voir étape 4), pas le JSON brut d'un `run_batch`
  (`{"combats": [...]}`).
- **Fusion** : PR #1 `etape/1-batch-extract` → `main` ✅.

---

## Étape 3 — Recoupement d'entités (sous-étape 2) — ✅ FAIT

- **Objectif** : le déterministe (`annuaire/resolve.py`) résout par QID, slug
  et similarité ; le LLM tranche la zone **ambigüe** (0.80–0.95) que le
  déterministe ne décide pas.
- **Fichiers** : `llm/resolve.py` (`suspicious_pairs`, `decide_same_person`),
  `llm/resolve_batch.py` (`run_resolve_batch`, `save_report`,
  `CONFIDENCE_THRESHOLD = 0.9`, pause 15 s), `tests/test_resolve.py` (11),
  `tests/test_resolve_batch.py` (6).
- **Acceptation** : ✅ 17 tests verts ; le LLM ne renvoie qu'une DÉCISION
  (same/different + confiance) jamais des données ; une fusion n'est
  recommandée que si `same == True` ET `confidence >= 0.9` ; une erreur LLM
  ne bloque pas le lot.
- **Annuaire cible** : `boxing-app/public/data/boxers/merged.json`
  (Wikidata + Big Balls + combats orgs — **20 894 fiches** le 16/08/2026).

---

## Étape 4 — Intégration au format pipeline — ✅ FAIT (fusionnée via PR #2)

- **Objectif** : convertir les combats LLM au format de sortie du pipeline
  (le contrat `Fight` : date, location, weight_class, fighter_a, fighter_b,
  winner, method, rounds, is_title_fight) pour être écrits par
  `write_org_shard` sans modification du pipeline.
- **Fichiers** : `llm/integrate.py` (`to_pipeline_fight`,
  `fights_to_pipeline_format` — validation locale miroir du schéma : date
  YYYY-MM-DD, méthode dans l'énumération, winner = fighter_a|fighter_b|Draw|NC ;
  un combat non valide est rejeté avec warning) + `tests/test_integrate.py` (8).
  `main.py` expose `cmd_integrate`
  (`python main.py integrate --input batch.json --source wbc`).
- **Acceptation** : ✅ `python main.py integrate` fonctionne ; chaîne réelle
  validée (fights-wbc.json → `Fight.make` du pipeline, **5/5 combats valides**).
- **Fusion** : porté sur `etape/2-batch-resolve` puis PR #2 → `main`
  (`52cee38`). `etape/3-integration` supprimée, sauvegarde sous
  `archive/etape-3-integration`.
- **Enrichissement réel (17/08/2026)** : `boxing-app/public/data/fights/wbc.json`
  passe de **6 → 7 combats** (Ibrahim Mafia vs Samuel Martei ajouté). ⚠️ La
  dédup par id SHA-256 ne suffit pas (même combat ≠ même id selon la source) →
  fusion par **clé date + paire de boxeurs normalisée** (voir étape 5.2).

---

## Étape 5 — Maintenance & prochaines étapes

- **5.1** ✅ **Git** : `etape/2-batch-resolve` resynchronisée (force-push
  `e585f59…4b73fc4`) et fusionnée sur `main` (PR #2) ; `etape/3-integration`
  archivée (`archive/etape-3-integration`).
- **5.2** ✅ **Fusion smart des shards** : `merge_fights` dans
  `llm/integrate.py` (clé **date + paire de boxeurs normalisée** — guillemets,
  parenthèses non fermées, « Jr. », ponctuation ignorés ; combats déjà présents
  prioritaires) + `tests/test_merge.py` (9). Réutilisable pour tout shard
  (`wbc`, `wbo`).
- **5.3** 🔜 **Améliorer la couverture WBC** : run réel **différé** (quota
  free tier) — batch complet 2026 → `integrate` → `write_org_shard("wbc")`.
  Cible : ≥ 5 combats extraits avec moins d'erreurs que le run du 16/08
  (analyser 429, articles longs, prose sans vainqueur clair).
- **5.4** 🔜 **WBO** : câblage vérifié (batch `--source wbo` → RSS paginé
  `wboboxing.com/feed/`, test batch WBO en mock ✅). Procédure de run différé :
  `python main.py batch --source wbo --year 2026 --output batch-wbo.json` →
  `python main.py integrate --input batch-wbo.json --source wbo --output
  ../boxing-app/public/data/llm/fights-wbo.json` → fusion via `merge_fights` +
  `write_org_shard("wbo")`. 1 seul article de résultats en 2026 (Navarrete),
  couverture partielle assumée.
- **5.5** ✅ **Monitoring Gemini** : journalisation stdlib des appels
  (horodatage, statut, latence — jamais la clé) + **alerte après 3 × 429
  consécutifs** (quota free tier atteint) dans `llm/client.py`.
- **5.6** 🛡️ **Garde-fou continu** : chaque nouveau flux LLM doit passer la
  validation du schéma avant écriture ; jamais de donnée non validée.

---

## Conventions à respecter

- **Tests** : `python -m unittest discover -s tests -v` doit passer (68 tests,
  stdlib unittest, **zéro dépendance pip**).
- **Politesse réseau** : toute lecture de source passe par `llm/sources.py`
  (1 req/s min, retry 429/5xx) ; tout appel LLM respecte les pauses
  anti-rate-limit (4 s batch, 15 s resolve-batch).
- **Garde-fou schéma** : le LLM ne produit jamais de combat non validé ; la
  validation finale appartient au pipeline (`Fight.make`), jamais importé ici
  — on ne dépend que du contrat JSON.
- **Sécurité** : clé Gemini uniquement dans `.env` (gitignoré) ou
  `boxing-app/.env.local` ; jamais de clé en dur dans le code.
- **Git** : repo séparé (`github.com/KarmaQuest/boxingdatasource-llm`), une
  branche par sous-étape (`etape/N-*`) fusionnée sur `main` par PR ; ne pas
  laisser une branche diverger de l'origin.