"""Tests du recoupement d'entités par LLM."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import LLMClient, LLMError  # noqa: E402
from llm.resolve import (  # noqa: E402
    _parse_verdict,
    build_prompt,
    decide_same_person,
    suspicious_pairs,
)


class FakeClient(LLMClient):
    def __init__(self, response: str):
        super().__init__(key="test-key")
        self.response = response
        self.last_prompt = ""

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        self.last_prompt = prompt
        return self.response


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contient_les_deux_mentions_et_contextes(self):
        p = build_prompt("O. Usyk", "Ukraine, poids lourds",
                         "Oleksandr Usyk", "Ukraine, 1987")
        self.assertIn("O. Usyk", p)
        self.assertIn("Oleksandr Usyk", p)
        self.assertIn("Ukraine", p)


class TestParseVerdict(unittest.TestCase):
    def test_verdict_same(self):
        v = _parse_verdict(
            '{"same": true, "confidence": 0.97, "reason": "même boxeur"}'
        )
        self.assertTrue(v["same"])
        self.assertEqual(v["confidence"], 0.97)

    def test_verdict_different(self):
        v = _parse_verdict('{"same": false, "confidence": 0.9, "reason": "x"}')
        self.assertFalse(v["same"])

    def test_confidence_invalide_a_zero(self):
        v = _parse_verdict('{"same": true, "confidence": "bof"}')
        self.assertEqual(v["confidence"], 0.0)

    def test_pas_de_json_leve_erreur(self):
        with self.assertRaises(LLMError):
            _parse_verdict("je ne sais pas")


class TestDecideSamePerson(unittest.TestCase):
    def test_meme_personne(self):
        client = FakeClient(
            '{"same": true, "confidence": 0.95, "reason": "surnom + nom"}'
        )
        v = decide_same_person(client, "O. Usyk", "", "Oleksandr Usyk", "")
        self.assertTrue(v["same"])
        self.assertIn("O. Usyk", client.last_prompt)

    def test_sans_cle_leve_erreur(self):
        client = LLMClient(key="")
        with self.assertRaises(LLMError):
            decide_same_person(client, "A", "", "B", "")


class TestSuspiciousPairs(unittest.TestCase):
    def test_paires_ambigues_detectees(self):
        names = [
            ("Tyson Fury", "UK"),
            ("Tyson Fury Jr", "UK"),     # Jr. → ambigüe (0.87)
            ("Oleksandr Usyk", "Ukraine"),
            ("Oleksandr Uzyk", "Ukraine"),  # orthographe → ambigüe (0.93)
            ("Canelo Álvarez", "Mexico"),
        ]
        pairs = suspicious_pairs(names)
        texts = [(a, b) for a, b, _ in pairs]
        self.assertIn(("Tyson Fury", "Tyson Fury Jr"), texts)
        self.assertIn(("Oleksandr Usyk", "Oleksandr Uzyk"), texts)
        # les paires trop différentes ne sont pas suspectes
        self.assertNotIn(("Tyson Fury", "Canelo Álvarez"), texts)

    def test_variante_daccent_exclue(self):
        # « Álvarez » vs « Alvarez » (0.96) : le slug normalisé du
        # déterministe les résout déjà → pas besoin du LLM
        names = [("Canelo Álvarez", "Mexico"), ("Canelo Alvarez", "Mexico")]
        self.assertEqual(suspicious_pairs(names), [])

    def test_paires_identiques_ignorees(self):
        names = [("Usyk", ""), ("Usyk", ""), ("Fury", "")]
        self.assertEqual(suspicious_pairs(names), [])

    def test_seuils_personnalisables(self):
        names = [("AAAA BBBB", ""), ("AAAA BBBX", "")]  # ratio ~0.89
        self.assertEqual(len(suspicious_pairs(names)), 1)
        self.assertEqual(
            suspicious_pairs(names, lower=0.95, upper=1.0), []
        )


if __name__ == "__main__":
    unittest.main()
