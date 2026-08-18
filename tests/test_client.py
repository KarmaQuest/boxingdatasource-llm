"""Tests du client LLM multi-provider (Gemini + Groq + Mistral + fallback)."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import (  # noqa: E402
    ALERT_429_THRESHOLD,
    GeminiProvider,
    GroqProvider,
    LLMClient,
    LLMError,
    MistralProvider,
    ProviderError,
    _resolve_api_key,
)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", code, "erreur", {}, io.BytesIO(b"")
    )


# ---------------------------------------------------------------------------
# Résolution de clé API
# ---------------------------------------------------------------------------

class TestResolveApiKey(unittest.TestCase):
    def test_retourne_vide_sans_cle(self):
        import os
        os.environ.pop("GEMINI_API_KEY", None)
        self.assertIsInstance(_resolve_api_key("GEMINI_API_KEY"), str)

    def test_cle_depuis_environnement(self):
        import os
        os.environ["GEMINI_API_KEY"] = "test-cle-123"
        try:
            self.assertEqual(_resolve_api_key("GEMINI_API_KEY"), "test-cle-123")
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


# ---------------------------------------------------------------------------
# Providers individuels
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _ok_gemini_response(text: str = "bonjour") -> _FakeResponse:
    body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return _FakeResponse(json.dumps(body).encode("utf-8"))


def _ok_openai_response(text: str = "bonjour") -> _FakeResponse:
    body = {"choices": [{"message": {"content": text}}]}
    return _FakeResponse(json.dumps(body).encode("utf-8"))


class TestGeminiProvider(unittest.TestCase):
    def test_disponible_avec_cle(self):
        self.assertTrue(GeminiProvider(key="cle").available)

    def test_inactif_sans_cle(self):
        with mock.patch("llm.client._resolve_api_key", return_value=""):
            self.assertFalse(GeminiProvider(key="").available)

    def test_complete_sans_cle_leve_provider_error(self):
        with mock.patch("llm.client._resolve_api_key", return_value=""), \
             self.assertRaises(ProviderError) as ctx:
            GeminiProvider(key="").complete("prompt")
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_complete_ok(self):
        p = GeminiProvider(key="cle")
        with mock.patch("urllib.request.urlopen", return_value=_ok_gemini_response("hello")):
            text = p.complete("test")
        self.assertEqual(text, "hello")

    def test_retry_429_puis_ok(self):
        calls = {"n": 0}

        def fake(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _http_error(429)
            return _ok_gemini_response()

        p = GeminiProvider(key="cle")
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
                mock.patch("time.sleep"):
            text = p.complete("test")
        self.assertEqual(text, "bonjour")


class TestGroqProvider(unittest.TestCase):
    def test_disponible_avec_cle(self):
        self.assertTrue(GroqProvider(key="cle").available)

    def test_inactif_sans_cle(self):
        self.assertFalse(GroqProvider(key="").available)

    def test_complete_ok(self):
        p = GroqProvider(key="cle")
        with mock.patch("urllib.request.urlopen", return_value=_ok_openai_response("hello")):
            text = p.complete("test")
        self.assertEqual(text, "hello")

    def test_retry_429_puis_ok(self):
        calls = {"n": 0}

        def fake(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _http_error(429)
            return _ok_openai_response()

        p = GroqProvider(key="cle")
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
                mock.patch("time.sleep"):
            text = p.complete("test")
        self.assertEqual(text, "bonjour")


class TestMistralProvider(unittest.TestCase):
    def test_disponible_avec_cle(self):
        self.assertTrue(MistralProvider(key="cle").available)

    def test_inactif_sans_cle(self):
        self.assertFalse(MistralProvider(key="").available)

    def test_complete_ok(self):
        p = MistralProvider(key="cle")
        with mock.patch("urllib.request.urlopen", return_value=_ok_openai_response("hello")):
            text = p.complete("test")
        self.assertEqual(text, "hello")


# ---------------------------------------------------------------------------
# Client multi-provider + fallback
# ---------------------------------------------------------------------------

class TestLLMClient(unittest.TestCase):
    def test_disponible_avec_au_moins_une_cle(self):
        c = LLMClient(providers=[GeminiProvider(key="k1"), GroqProvider(key="")])
        self.assertTrue(c.available)

    def test_inactif_sans_aucune_cle(self):
        with mock.patch("llm.client._resolve_api_key", return_value=""):
            c = LLMClient(providers=[GeminiProvider(key=""), GroqProvider(key="")])
            self.assertFalse(c.available)

    def test_fallback_gemini_vers_groq(self):
        """Gemini échoue → Groq prend le relais."""
        gemini = GeminiProvider(key="k1")
        groq = GroqProvider(key="k2")

        def gemini_fail(prompt, max_tokens=2000):
            raise ProviderError("gemini", "HTTP 429")

        def groq_ok(prompt, max_tokens=2000):
            return "from groq"

        c = LLMClient(providers=[gemini, groq])
        with mock.patch.object(gemini, "complete", side_effect=gemini_fail), \
             mock.patch.object(groq, "complete", side_effect=groq_ok):
            text = c.complete("test")
        self.assertEqual(text, "from groq")
        self.assertEqual(c.provider_name, "groq")

    def test_fallback_gemini_vers_mistral(self):
        """Gemini échoue, Groq indisponible → Mistral prend le relais."""
        gemini = GeminiProvider(key="k1")
        groq = GroqProvider(key="")  # indisponible
        mistral = MistralProvider(key="k3")

        def gemini_fail(prompt, max_tokens=2000):
            raise ProviderError("gemini", "HTTP 404")

        def mistral_ok(prompt, max_tokens=2000):
            return "from mistral"

        c = LLMClient(providers=[gemini, groq, mistral])
        with mock.patch.object(gemini, "complete", side_effect=gemini_fail), \
             mock.patch("llm.client._resolve_api_key", return_value=""), \
             mock.patch.object(mistral, "complete", side_effect=mistral_ok):
            text = c.complete("test")
        self.assertEqual(text, "from mistral")
        self.assertEqual(c.provider_name, "mistral")

    def test_tous_providers_echouent(self):
        """Tous les providers échouent → LLMError."""
        gemini = GeminiProvider(key="k1")
        groq = GroqProvider(key="k2")

        def fail(prompt, max_tokens=2000):
            raise ProviderError("test", "fail")

        c = LLMClient(providers=[gemini, groq])
        with mock.patch.object(gemini, "complete", side_effect=fail), \
             mock.patch.object(groq, "complete", side_effect=fail):
            with self.assertRaises(LLMError) as ctx:
                c.complete("test")
        self.assertIn("tous les providers", str(ctx.exception))

    def test_aucun_provider_disponible(self):
        """Aucun provider avec clé → LLMError."""
        with mock.patch("llm.client._resolve_api_key", return_value=""):
            c = LLMClient(providers=[GeminiProvider(key=""), GroqProvider(key="")])
            with self.assertRaises(LLMError) as ctx:
                c.complete("test")
            self.assertIn("aucun provider", str(ctx.exception))

    def test_complete_json_extrait_objet(self):
        """complete_json extrait le premier objet {…} de la réponse."""
        gemini = GeminiProvider(key="k1")

        def gemini_ok(prompt, max_tokens=2000):
            return 'Voici le résultat : {"fights": []} et du texte.'

        c = LLMClient(providers=[gemini])
        with mock.patch.object(gemini, "complete", side_effect=gemini_ok):
            data = c.complete_json("test")
        self.assertEqual(data, {"fights": []})

    def test_complete_json_rejete_si_pas_json(self):
        gemini = GeminiProvider(key="k1")

        def gemini_ok(prompt, max_tokens=2000):
            return "pas de json ici"

        c = LLMClient(providers=[gemini])
        with mock.patch.object(gemini, "complete", side_effect=gemini_ok):
            with self.assertRaises(LLMError):
                c.complete_json("test")

    def test_provider_name_suivi(self):
        """provider_name reflète le dernier provider utilisé avec succès."""
        gemini = GeminiProvider(key="k1")
        groq = GroqProvider(key="k2")

        c = LLMClient(providers=[gemini, groq])
        with mock.patch.object(gemini, "complete", return_value="ok"):
            c.complete("test")
        self.assertEqual(c.provider_name, "gemini")

        def gemini_fail(prompt, max_tokens=2000):
            raise ProviderError("gemini", "429")

        def groq_ok(prompt, max_tokens=2000):
            return "ok2"

        with mock.patch.object(gemini, "complete", side_effect=gemini_fail), \
             mock.patch.object(groq, "complete", side_effect=groq_ok):
            c.complete("test")
        self.assertEqual(c.provider_name, "groq")


if __name__ == "__main__":
    unittest.main()
