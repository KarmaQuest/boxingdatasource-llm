"""Tests du client LLM (Gemini free tier)."""

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
    LLMClient,
    LLMError,
    api_key,
)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", code, "erreur", {}, io.BytesIO(b"")
    )


class TestApiKey(unittest.TestCase):
    def test_retourne_vide_sans_cle(self):
        # pas de GEMINI_API_KEY en env, pas de .env dans les chemins attendus
        import os
        os.environ.pop("GEMINI_API_KEY", None)
        self.assertIsInstance(api_key(), str)

    def test_cle_depuis_environnement(self):
        import os
        os.environ["GEMINI_API_KEY"] = "test-cle-123"
        try:
            self.assertEqual(api_key(), "test-cle-123")
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


class TestLLMClient(unittest.TestCase):
    def test_disponible_avec_cle(self):
        self.assertTrue(LLMClient(key="cle").available)

    def test_inactif_sans_cle(self):
        self.assertFalse(LLMClient(key="").available)

    def test_complete_sans_cle_leve_erreur_propre(self):
        with self.assertRaises(LLMError):
            LLMClient(key="").complete("prompt")


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _ok_response(text: str = "bonjour") -> _FakeResponse:
    body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return _FakeResponse(json.dumps(body).encode("utf-8"))


class TestMonitoring(unittest.TestCase):
    def test_success_logge_ok_et_latence(self):
        client = LLMClient(key="cle")
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()), \
                mock.patch("time.sleep"), \
                self.assertLogs("llm.client", level="INFO") as cm:
            text = client.complete("salut")
        self.assertEqual(text, "bonjour")
        self.assertTrue(any("gemini ok" in r and "latency" in r for r in cm.output))

    def test_retry_429_puis_success(self):
        calls = {"n": 0}

        def fake(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _http_error(429)
            return _ok_response()

        client = LLMClient(key="cle")
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
                mock.patch("time.sleep"), \
                self.assertLogs("llm.client", level="INFO") as cm:
            client.complete("salut")
        output = "\n".join(cm.output)
        self.assertIn("gemini retry", output)
        self.assertIn("gemini ok", output)
        self.assertEqual(client._consecutive_429, 0)

    def test_alerte_quota_apres_3_x_429_consecutifs(self):
        def fake(*_args, **_kwargs):
            raise _http_error(429)

        client = LLMClient(key="cle")
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
                mock.patch("time.sleep"), \
                self.assertLogs("llm.client", level="INFO") as cm:
            with self.assertRaises(LLMError):
                client.complete("salut")
        output = "\n".join(cm.output)
        self.assertIn("quota free tier atteint", output)
        self.assertGreaterEqual(client._consecutive_429, ALERT_429_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
