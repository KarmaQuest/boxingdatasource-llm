"""Tests du client LLM (Gemini free tier)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import LLMClient, LLMError, api_key  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
