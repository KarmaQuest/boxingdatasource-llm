"""Client LLM multi-provider avec fallback automatique.

Le LLM est un OUTIL d'extraction, jamais une source de vérité : tout ce
qu'il produit passe la validation stricte du schéma (`Fight.make`) avant
d'exister. Sans clé, le module est simplement inactif (`is_available()
== False`) et le pipeline reste 100 % déterministe.

Providers supportés (gratuits, sans CB) :
- **Gemini** : free tier ~1 500 req/jour (le plus généreux)
- **Groq** : free tier ~1 000 req/jour (ultra-rapide, LPU)
- **Mistral** : free tier ~500K tokens/min (qualité)

Fallback automatique : si un provider renvoie 429/404/erreur, le client
essaie le provider suivant. Zéro intervention humaine requise.

Garde-fous :
- réponse tronquée / non-JSON → erreur propre, jamais de fabrication ;
- température 0 (déterministe) ;
- le prompt exige du JSON STRICT et interdit d'inventer quoi que ce soit
  qui ne soit pas dans le texte source ;
- la validation finale appartient au schéma (extract.py).

Usage :
    from llm.client import get_default_client
    client = get_default_client()
    if client.available:
        fights = extract_fights_llm(client, text, date)
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Journalisation des appels (jamais la clé ni le prompt complet).
logger = logging.getLogger("llm.client")

# Nombre d'échecs 429 CONSECUTIFS qui déclenche une alerte « quota atteint ».
ALERT_429_THRESHOLD = 3

# Clés partagées : $*_API_KEY > boxing-app/.env.local > .env pipeline
ENV_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "boxing-app" / ".env.local",
    Path(__file__).resolve().parents[1] / ".env",
)


def _read_env_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def _resolve_api_key(env_var: str) -> str:
    """Résout une clé API : env > .env.local > .env pipeline."""
    env = os.environ.get(env_var)
    if env:
        return env
    for candidate in ENV_CANDIDATES:
        value = _read_env_file(candidate, env_var)
        if value:
            return value
    return ""


# ---------------------------------------------------------------------------
# Provider : classe de base
# ---------------------------------------------------------------------------

class _Provider:
    """Un provider LLM (Gemini, Groq, Mistral…)."""

    name: str = "unknown"

    def __init__(self, key: str) -> None:
        self._key = key.strip()
        self.timeout = 60.0
        self._consecutive_429 = 0

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        """Appel LLM. Lève ProviderError en cas d'échec."""
        raise NotImplementedError


class ProviderError(Exception):
    """Échec d'un provider spécifique (pas de clé, réseau, quota…)."""

    def __init__(self, provider: str, message: str):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiProvider(_Provider):
    """Client Gemini free tier (generateContent, température 0)."""

    name = "gemini"

    # ⚠️ gemini-2.5-flash / 2.0-flash renvoient 404 pour les nouveaux comptes.
    # Modèle par défaut : gemini-flash-lite-latest — le SEUL qui répondait
    # sans 429 initialement. Depuis août 2026, gemini-3.7-flash et 3.6-flash
    # sont aussi disponibles en free tier.
    MODEL = "gemini-flash-lite-latest"
    ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent"
    )

    def __init__(self, key: Optional[str] = None) -> None:
        resolved = key if key is not None else _resolve_api_key("GEMINI_API_KEY")
        super().__init__(resolved)

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise ProviderError(self.name, "pas de clé GEMINI_API_KEY")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
            },
        }
        url = f"{self.ENDPOINT}?key={urllib.parse.quote(self._key)}"

        import time as _time
        start = _time.time()
        data = None
        for attempt in range(1, 5):
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._consecutive_429 = 0
                logger.info(
                    "gemini ok : model=%s latency=%.2fs",
                    self.MODEL, _time.time() - start,
                )
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 4:
                    if exc.code == 429:
                        self._consecutive_429 += 1
                        if self._consecutive_429 >= ALERT_429_THRESHOLD:
                            logger.warning(
                                "gemini quota atteint : %d x 429 consécutifs",
                                self._consecutive_429,
                            )
                    logger.warning(
                        "gemini retry : attempt=%d status=%s backoff=%ds",
                        attempt, exc.code, 8.0 * attempt,
                    )
                    _time.sleep(8.0 * attempt)
                    continue
                if exc.code == 429:
                    self._consecutive_429 += 1
                else:
                    self._consecutive_429 = 0
                raise ProviderError(self.name, f"HTTP {exc.code}") from exc
            except (OSError, TimeoutError) as exc:
                raise ProviderError(self.name, str(exc)) from exc

        if data is None:
            raise ProviderError(self.name, "échec après 4 tentatives")

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                self.name, f"réponse inattendue : {json.dumps(data)[:300]}"
            ) from exc


# ---------------------------------------------------------------------------
# Groq (API compatible OpenAI)
# ---------------------------------------------------------------------------

class GroqProvider(_Provider):
    """Client Groq free tier (API OpenAI-compatible, ultra-rapide LPU)."""

    name = "groq"
    MODEL = "llama-3.3-70b-versatile"
    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, key: Optional[str] = None) -> None:
        resolved = key if key is not None else _resolve_api_key("GROQ_API_KEY")
        super().__init__(resolved)

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise ProviderError(self.name, "pas de clé GROQ_API_KEY")

        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }

        import time as _time
        start = _time.time()
        for attempt in range(1, 4):
            req = urllib.request.Request(
                self.ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._consecutive_429 = 0
                logger.info(
                    "groq ok : model=%s latency=%.2fs",
                    self.MODEL, _time.time() - start,
                )
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 3:
                    if exc.code == 429:
                        self._consecutive_429 += 1
                    _time.sleep(2.0 * attempt)
                    continue
                raise ProviderError(self.name, f"HTTP {exc.code}") from exc
            except (OSError, TimeoutError) as exc:
                raise ProviderError(self.name, str(exc)) from exc

        raise ProviderError(self.name, "échec après 3 tentatives")


# ---------------------------------------------------------------------------
# Mistral (API compatible OpenAI)
# ---------------------------------------------------------------------------

class MistralProvider(_Provider):
    """Client Mistral free tier (API OpenAI-compatible, ~1B tokens/mois)."""

    name = "mistral"
    MODEL = "mistral-small-latest"
    ENDPOINT = "https://api.mistral.ai/v1/chat/completions"

    def __init__(self, key: Optional[str] = None) -> None:
        resolved = key if key is not None else _resolve_api_key("MISTRAL_API_KEY")
        super().__init__(resolved)

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise ProviderError(self.name, "pas de clé MISTRAL_API_KEY")

        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }

        import time as _time
        start = _time.time()
        for attempt in range(1, 4):
            req = urllib.request.Request(
                self.ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._consecutive_429 = 0
                logger.info(
                    "mistral ok : model=%s latency=%.2fs",
                    self.MODEL, _time.time() - start,
                )
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 3:
                    if exc.code == 429:
                        self._consecutive_429 += 1
                    _time.sleep(2.0 * attempt)
                    continue
                raise ProviderError(self.name, f"HTTP {exc.code}") from exc
            except (OSError, TimeoutError) as exc:
                raise ProviderError(self.name, str(exc)) from exc

        raise ProviderError(self.name, "échec après 3 tentatives")


# ---------------------------------------------------------------------------
# Client multi-provider avec fallback
# ---------------------------------------------------------------------------

class LLMClient:
    """Client LLM avec fallback automatique entre providers.

    Essaie les providers dans l'ordre : Gemini → Groq → Mistral.
    Si un provider échoue (429, 404, réseau), le suivant est essayé.

    Rétrocompatible : `LLMClient(key="cle")` crée un client avec
    uniquement Gemini (comportement ancien).
    """

    def __init__(self, providers: Optional[list[_Provider]] = None,
                 key: Optional[str] = None) -> None:
        if providers is not None:
            self._providers = providers
        elif key is not None:
            # Rétrocompatibilité : un seul provider Gemini avec clé fixe
            self._providers = [GeminiProvider(key=key)]
        else:
            self._providers = [
                GeminiProvider(),
                GroqProvider(),
                MistralProvider(),
            ]
        self._active_provider: _Provider | None = None

    @property
    def available(self) -> bool:
        return any(p.available for p in self._providers)

    @property
    def provider_name(self) -> str:
        return self._active_provider.name if self._active_provider else "none"

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        """Appel LLM avec fallback automatique entre providers."""
        errors: list[str] = []
        for provider in self._providers:
            if not provider.available:
                continue
            try:
                result = provider.complete(prompt, max_tokens)
                self._active_provider = provider
                return result
            except ProviderError as exc:
                errors.append(str(exc))
                logger.warning(
                    "provider %s échoué : %s — essai du suivant",
                    provider.name, exc,
                )
                continue

        if not errors:
            raise LLMError("aucun provider disponible (pas de clé API)")
        raise LLMError(
            f"tous les providers ont échoué : {'; '.join(errors)}"
        )

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


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def llm_client() -> LLMClient:
    """Client partagé — `available` False sans aucune clé."""
    return LLMClient()


# Instances singleton pour l'usage courant
_default_client: Optional[LLMClient] = None


def get_default_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
