---
name: gemini-free-tier-client
description: Conventions du client LLM Gemini free tier de boxingdatasource-llm (modèle, retries, pauses rate-limit, monitoring, parsing JSON strict). Utiliser quand on modifie llm/client.py, llm/batch.py, llm/resolve_batch.py ou tout appel au LLM.
risk: medium
source: custom
date_added: '2026-08-18'
---
Vous développez pour le module `boxingdatasource-llm`. Le client LLM appelle
l'API Gemini `generateContent` via `urllib` pur (zéro dépendance pip).

## Règles impératives

1. **Modèle** : toujours `gemini-flash-lite-latest`. Les autres modèles
   (`gemini-2.5-flash`, `2.0-flash`) renvoient 404 sur les nouveaux comptes ;
   `gemini-flash-latest` renvoie 429 permanent. Ne pas changer sans vérifier.
2. **Température 0** : le LLM doit être le plus déterministe possible.
3. **Retries** : sur 429/503 → backoff 8, 16, 24 s (4 tentatives max).
4. **Pauses anti-rate-limit** (free tier ~15 req/min) : 4 s entre deux appels
   en batch, 15 s en resolve-batch. Une rafale = 429 en cascade.
5. **Réponse strictement JSON** : utiliser `complete_json` / `parse_llm_json`
   (extrait le premier objet `{…}`, gère les fences markdown et les accolades
   dans les chaînes). Une réponse non-JSON → `LLMError` propre, **jamais de
   fabrication**.
6. **Monitoring** : journaliser chaque appel (horodatage, modèle, statut,
   latence — **jamais la clé ni le prompt**) ; alerter après 3 × 429
   consécutifs (quota free tier atteint).
7. **Sans clé** : `available == False` → le module est inactif mais testable
   (FakeClient). Ne jamais bloquer le reste du projet sur une absence de clé.

## Clé

`$GEMINI_API_KEY` > `boxing-app/.env.local` > `.env`. La clé ne doit jamais
être loggée ni commitée.

## Garde-fou absolu

Le LLM est un outil d'**extraction et de recoupement**, jamais une source de
vérité. Tout résultat généré doit être validé contre le schéma du pipeline
(voir le skill `pipeline-fight-contract`) avant d'être écrit.
