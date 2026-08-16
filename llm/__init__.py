"""Recherche et recoupement par LLM sur les données sources du pipeline.

- `client`  : client Gemini free tier (urllib pur, optionnel sans clé)
- `extract` : extraction de combats depuis la prose des articles WBC/WBO
- `resolve` : résolution d'entités (le même boxeur vu par plusieurs sources)
"""

from llm.client import LLMClient, LLMError, get_default_client, llm_client

__all__ = ["LLMClient", "LLMError", "get_default_client", "llm_client"]
