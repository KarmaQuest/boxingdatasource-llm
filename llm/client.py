"""Client LLM optionnel — Gemini free tier (aucune dépendance pip).

Le LLM est un OUTIL d'extraction, jamais une source de vérité : tout ce
qu'il produit passe la validation stricte du schéma (`Fight.make`) avant
d'exister. Sans clé, le module est simplement inactif (`is_available()
== False`) et le pipeline reste 100 % déterministe.

Choix : Gemini 2.5 Flash — free tier ~1 500 req/jour (le plus généreux),
zéro coût, REST pur via urllib (pas de SDK à installer).

Garde-fous :
- réponse tronquée / non-JSON → erreur propre, jamais de fabrication ;
- température 0 (déterministe) ;
- le prompt exige du JSON STRICT et interdit d'inventer quoi que ce soit
  qui ne soit pas dans le texte source ;
- la validation finale appartient au schéma (extract.py).

Usage :
    from llm.client import llm_client
    if llm_client.is_available():
        fights = extract_fights_llm(llm_client, text, date)
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Clé partagée : $GEMINI_API_KEY > boxing-app/.env.local > .env pipeline
ENV_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "boxing-app" / ".env.local",
    Path(__file__).resolve().parents[1] / ".env",
)

# ⚠️ gemini-2.5-flash / 2.0-flash renvoient 404 pour les nouveaux comptes
# (16/08/2026) : « no longer available to new users ». L'alias
# gemini-flash-latest est résolu par Google vers le modèle courant
# (actuellement gemini-3.7-flash) — c'est lui qu'on utilise.
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


def _read_env_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def api_key() -> str:
    """Clé Gemini : $GEMINI_API_KEY > boxing-app/.env.local > .env pipeline."""
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env
    for candidate in ENV_CANDIDATES:
        value = _read_env_file(candidate, "GEMINI_API_KEY")
        if value:
            return value
    return ""


class LLMClient:
    """Client Gemini minimal (generateContent, température 0)."""

    def __init__(self, key: Optional[str] = None) -> None:
        self._key = (key if key is not None else api_key()).strip()
        self.timeout = 60.0

    @property
    def available(self) -> bool:
        return bool(self._key)

    # ------------------------------------------------------------------
    # Appel
    # ------------------------------------------------------------------
    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        """Réponse texte du LLM. Lève LLMError en cas d'échec.

        Retries avec backoff sur 429/503 (pic de charge Gemini — message
        officiel « high demand … try again later »)."""
        if not self.available:
            raise LLMError("pas de clé GEMINI_API_KEY — LLM inactif")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
            },
        }
        url = f"{GEMINI_ENDPOINT}?key={urllib.parse.quote(self._key)}"

        import time as _time
        for attempt in range(1, 4):  # 3 tentatives max
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 3:
                    _time.sleep(3.0 * attempt)  # backoff : 3 s, 6 s
                    continue
                raise LLMError(f"appel Gemini échoué : {exc}") from exc
            except (OSError, TimeoutError) as exc:
                raise LLMError(f"appel Gemini échoué : {exc}") from exc

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"réponse Gemini inattendue : {json.dumps(data)[:300]}"
            ) from exc
        return text

    def complete_json(self, prompt: str, max_tokens: int = 2000) -> dict:
        """Réponse JSON STRICT : extrait le premier objet {…} de la réponse."""
        text = self.complete(prompt, max_tokens=max_tokens)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise LLMError(f"pas d'objet JSON dans la réponse LLM : {text[:200]}")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON LLM invalide : {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("le JSON LLM doit être un objet")
        return data


class LLMError(Exception):
    """Échec du LLM (pas de clé, réseau, JSON invalide…)."""


def llm_client() -> LLMClient:
    """Client partagé — `available` False sans clé."""
    return LLMClient()


# Instances singleton pour l'usage courant
_default_client: Optional[LLMClient] = None


def get_default_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
