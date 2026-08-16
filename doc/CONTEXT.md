# CONTEXT — boxingdatasource-llm 🥊🤖

> **Fichier de contexte du module LLM.** Tout ce qui a été mis en place,
> pour reprendre le travail sans rien perdre. Dernière mise à jour :
> 17/08/2026.

## 1. Vue d'ensemble & mission

Module **séparé** de `boxingdatasource-pipeline` : recherche et recoupement
par IA (LLM) sur les données sources du pipeline. Le pipeline reste **100 %
déterministe** (collecte → shards) ; l'IA n'intervient **qu'ici**, là où elle
apporte de la valeur :

1. **Extraction prose** (`llm/extract.py`) — les articles WBC/WBO sont des
   récits en prose (« …the Japanese who won by unanimous decision ») que les
   parsers regex sautent. Le LLM extrait les combats de la prose, **validés
   ensuite par le schéma strict du pipeline** (`Fight.make`).
2. **Recoupement d'entités** (`llm/resolve.py`) — le même boxeur vu par
   Wikidata, Big Balls et les organisations avec des orthographes différentes
   (« O. Usyk », « Oleksandr Usyk », surnoms, Jr./Sr.). Le LLM tranche les cas
   ambigus que le déterministe (slug / similarité) ne résout pas.

### Garde-fou absolu

Le LLM est un outil d'**extraction et de recoupement**, jamais une source de
vérité. Tout résultat généré est :

- validé contre le schéma (`config/schema.py` du pipeline — `Fight.make`) ;
- rejeté avec warning s'il ne passe pas ;
- croisé avec une source réelle avant d'être écrit.

## 2. Emplacement & frontières

| Élément | Chemin |
| --- | --- |
| Module LLM (projet Python) | `/boxingdatasource-llm` (racine workspace, séparé du pipeline) |
| Pipeline (projet Python) | `/boxingdatasource-pipeline` |
| App Next.js | `/boxing-app` |
| Shards de résultats | `boxing-app/public/data/fights/` (générés par le pipeline) — `wbc.json` = **7 combats** (6 spider + Ibrahim Mafia, LLM validé le 17/08/2026) |
| Annuaire fusionné (Wikidata+BigBalls+orgs) | `boxing-app/public/data/boxers/merged.json` (généré par `annuaire/resolve.py` du pipeline — **20 894 fiches** le 16/08/2026) |
| Sortie du batch LLM | `boxing-app/public/data/llm/fights-wbc.json` (5 combats WBC au format pipeline, extraits le 16/08/2026) |

Le module écrit **dans** `boxing-app/public/data/llm/` mais ne modifie **jamais**
le code Next.js ni le code du pipeline. Il est consommé comme des assets
statiques (zéro requête runtime).

## 3. Architecture

```
boxingdatasource-llm/
├── main.py                  # CLI : extract / resolve / batch / resolve-batch / integrate / report
├── llm/
│   ├── client.py            # client Gemini free tier (REST urllib pur, température 0, retries, monitoring)
│   ├── extract.py           # extraction de combats depuis la PROSE des articles WBC/WBO
│   ├── resolve.py           # recoupement d'entités (le même boxeur vu par plusieurs sources)
│   ├── batch.py             # batch d'extraction : tous les articles d'une source → combats
│   ├── resolve_batch.py     # batch de recoupement : paires suspectes d'un annuaire → fusions
│   ├── integrate.py         # pont : combats LLM → format Fight du pipeline + fusion smart des shards
│   ├── sources.py           # lecture des articles sources WBC (WP API) / WBO (RSS), politesse réseau
│   └── __init__.py          # exports publics (LLMClient, LLMError, get_default_client…)
├── tests/                   # 68 tests — stdlib unittest (aucune dépendance)
│   ├── test_client.py       # 8   : clé, disponibilité, erreur propre sans clé, monitoring
│   ├── test_extract.py      # 15  : prompt, validation, parsing JSON, extraction
│   ├── test_resolve.py      # 11  : verdicts, décision, paires suspectes (0.80–0.95)
│   ├── test_batch.py        # 11  : filtres de titres, run_batch WBC/WBO, tolérance aux pannes
│   ├── test_resolve_batch.py# 6   : contexte, fusions/distinctes/inconclusives, erreurs
│   ├── test_integrate.py    # 8   : to_pipeline_fight, fights_to_pipeline_format (validations)
│   └── test_merge.py        # 9   : fusion smart des shards (dédup date+paire normalisée)
├── README.md                # vue d'ensemble + usage
└── .gitignore               # __pycache__/, *.pyc, .env
```

Le module est **stdlib pur** (urllib, re, json, difflib) — **aucune dépendance
pip** ; il fonctionne sans clé (mocking des tests) et le pipeline tourne sans
lui.

## 4. Client LLM & modèle

`llm/client.py` — `LLMClient` (Gemini `generateContent`, REST via urllib) :

- **Modèle** : `gemini-flash-lite-latest` — le SEUL qui répond sans 429 sur ce
  compte free tier. ⚠️ `gemini-2.5-flash` / `2.0-flash` renvoient **404** pour
  les nouveaux comptes (16/08/2026 : « no longer available to new users ») ;
  `gemini-flash-latest` (3.7-flash) est saturé « high demand » → 429 permanent.
- **Clé** : `$GEMINI_API_KEY` > `boxing-app/.env.local` > `.env` pipeline.
  Sans clé, `available == False`, le module est **inactif mais testable**.
- **Température 0** (déterministe), `maxOutputTokens` 2000, timeout 60 s.
- **Retries** : 429/503 → backoff **8, 16, 24 s** (4 tentatives max).
- **Monitoring** (17/08/2026) : journalisation stdlib de chaque appel
  (horodatage, modèle, statut ok/retry/erreur, latence — jamais la clé ni le
  prompt) ; **alerte** après 3 échecs 429 consécutifs → « quota free tier
  atteint » (warning `llm.client`).
- **Réponse strictement JSON** : `complete_json`/`parse_llm_json` extraient le
  premier objet `{…}` (fences markdown, texte bavard, accolades dans les
  chaînes gérées) ; réponse non-JSON → `LLMError` propre, **jamais de
  fabrication**.

## 5. Extraction prose (articles WBC/WBO)

`llm/extract.py` — lit la prose que les parsers regex du pipeline sautent :

- Prompt strict : « Ne JAMAIS inventer », méthodes autorisées
  `UD|SD|MD|TKO|KO|DQ|PTS`, perdant possiblement réduit à sa nationalité
  (« Thai challenger »), JSON de sortie fixe `{"fights": [...]}`.
- `_validate_fight` : nettoyage (espaces, parenthèses non fermées retirées),
  vainqueur/perdant obligatoires et différents, méthode inconnue → vidée
  (laissée au schéma), rounds invalides → 0.
- Sortie = liste de dicts **prêts pour `Fight.make`** (le contrat JSON, jamais
  de `Fight` importé du pipeline : zéro dépendance inter-projets).

## 6. Recoupement d'entités

`llm/resolve.py` — le déterministe (`annuaire/resolve.py` du pipeline) résout
par QID, slug exact et similarité (`SIMILARITY_CUTOFF 0.86`). Le LLM tranche
la zone **ambigüe** :

- `SIMILARITY_AMBIGUOUS = (0.80, 0.95)` — `suspicious_pairs()` détecte les
  paires de noms dans cette zone (variantes d'accents ≥ 0.96 exclues : le slug
  normalisé les résout déjà, inutile de brûler un appel).
- `decide_same_person(client, name_a, ctx_a, name_b, ctx_b)` → le LLM reçoit le
  **contexte** (pays, catégorie, naissance, combats officiels) et ne renvoie
  qu'une **DÉCISION** `{"same", "confidence", "reason"}` — jamais des données.
- **Garde-fou** : fusion recommandée seulement si `same == True` ET
  `confidence >= 0.9` (`CONFIDENCE_THRESHOLD` dans `resolve_batch.py`).

## 7. Batchs & politesse réseau

`llm/sources.py` — mêmes sources que le pipeline (URLs validées 16/08/2026) :

- **WBC** : API WordPress `wp-json/wp/v2/posts` (`lang=en&after={année}`, contenu inline).
- **WBO** : flux RSS paginé `/feed/` (l'API REST WP est en 401) → fetch de chaque article.
- Politesse : **1 requête/s** min par domaine, retry sur 429/5xx (backoff 2 s×n).

`llm/batch.py` — filtres de titres `_is_result_article` (identiques au spider
WBC du pipeline : preview/bio/récaps « On This Day » exclus) pour **ne pas
brûler d'appels LLM** sur les articles sans résultats. Pause **4 s** entre deux
appels (`LLM_PAUSE_S` — free tier ~15 req/min). Un article en échec LLM
n'arrête pas le lot.

`llm/resolve_batch.py` — pause **15 s** par paire (~2–4 req/min sur ce compte).

## 8. Sécurité & garde-fous

- `.env` (clé `GEMINI_API_KEY`) est **gitignoré** ; ⚠️ régénérer la clé si ce
  fichier ou un chat partagé est exposé.
- Le contenu des articles est traité comme **donnée, jamais comme instruction**.
- Le LLM ne produit **jamais** de combat non validé : un échec (pas de clé,
  JSON invalide, schéma) est rejeté avec warning, jamais écrit.

## 9. Git & branches

Dépôt : **`github.com/KarmaQuest/boxingdatasource-llm`**. Branches :

| Branche | État |
| --- | --- |
| `main` | socle `9eebe18` + PR #1 (`9bec2f1` : batch extraction) + PR #2 (`52cee38` : batch résolution + **intégration**) + README (`95d6af8`) |
| `etape/1-batch-extract` | fusionnée via PR #1 |
| `etape/2-batch-resolve` | fusionnée via PR #2 (resynchronisée par force-push `e585f59…4b73fc4`) |
| `etape/3-integration` | ❌ supprimée (loc. + remote) — contenu porté dans PR #2, point de sauvegarde `archive/etape-3-integration` |

✅ `python main.py integrate` fonctionne sur `main` (`llm/integrate.py` + 8
tests présents).

## 10. Commandes utiles

```bash
cd /d/DesktopFiles/ProjetPerso/Freebuff/boxingdatasource-llm

python main.py extract --source wbc --date 2026-08-12 --text "..."   # prose → combats
python main.py resolve --name-a "O. Usyk" --name-b "Oleksandr Usyk"  # même boxeur ? (LLM)
python main.py batch --source wbc --year 2026 --output batch.json    # tous les articles → combats
python main.py resolve-batch --annuaire merged.json --max-pairs 20   # paires suspectes → fusions
python main.py integrate --input batch.json --source wbc --output out.json  # batch LLM → format Fight
python main.py report --annuaire ../boxing-app/public/data/boxers/merged.json  # paires suspectes (sans LLM)

python -m unittest discover -s tests -v   # 68 tests
```

Sans `GEMINI_API_KEY`, les commandes LLM échouent proprement (message clair) ;
`report` fonctionne sans LLM (il liste les paires à trancher).

## 11. Décisions & pièges

1. **Module séparé du pipeline** : le pipeline reste 100 % déterministe ; l'IA
   vit uniquement ici (demandé par l'utilisateur).
2. **Zéro dépendance pip** : urllib pur → testable immédiatement, sans venv.
3. **Garde-fou schéma** : le LLM ne renvoie que des données au contrat JSON ;
   la validation finale appartient au pipeline (`Fight.make`), jamais importé ici.
4. **Modèle Gemini lite** (16/08/2026) : `gemini-flash-lite-latest` est le seul
   à répondre sur le free tier — 2.5-flash/2.0-flash → 404 nouveaux comptes,
   flash-latest → 429 « high demand ». Température 0 + retries 429/503.
5. **Rate-limit free tier** (~15 req/min) : pause 4 s (batch) / 15 s
   (resolve-batch) — sinon 429 en rafale.
6. **Filtres de titres** (`_is_result_article`) : identiques au spider WBC —
   prévient le gaspillage d'appels sur preview/bio/récaps historiques.
7. **WBO sans base structurée** : RSS paginé (API WP en 401), 1 seul article
   de résultats en 2026 — couverture partielle assumée.
8. **Parenthèses non fermées** : le LLM tronque parfois un nom (« Moriana
   (Lauriaga ») → `_clean_value` retire le contenu entre parenthèses non
   fermées (fixture réelle testée).
9. **Zone ambigüe 0.80–0.95** : au-delà, variantes d'accents (0.96+) résolues
   par le slug déterministe — le LLM ne tranche que le doute réel.
10. **Dédup shard : id brut insuffisant** (17/08/2026, démontré) : le même
    combat vu par spider et LLM a des ids SHA-256 différents (méthode PTS vs
    UD, `weight_class`, nom tronqué « Moriana (Lauriaga ») → la fusion des
    combats LLM dans un shard passe par la **clé date + paire de boxeurs
    normalisée** (`merge_fights`, source déterministe prioritaire), pas l'id.
11. **Monitoring** : journaliser chaque appel (jamais la clé ni le prompt) ;
    alerter après 3 × 429 consécutifs (quota free tier atteint).
12. **Sortie générée** : `boxing-app/public/data/` (fights/, boxers/, llm/) est
    un dossier GÉNÉRÉ — à régénérer au déploiement.