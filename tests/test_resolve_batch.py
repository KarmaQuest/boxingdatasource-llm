"""Tests du batch de recoupement LLM (paires suspectes → fusions)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import LLMClient, LLMError  # noqa: E402
from llm.resolve_batch import (  # noqa: E402
    CONFIDENCE_THRESHOLD,
    _context,
    run_resolve_batch,
    save_report,
)


def make_annuaire(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def entry(name: str, country: str = "", weight_class: str = "") -> dict:
    return {"name": name, "country": country, "weight_class": weight_class,
            "birth_date": "", "orgs": [], "sources": ["wikidata"]}


class FakeClient(LLMClient):
    """Client de test : réponses prédéfinies en file (pas de réseau)."""

    def __init__(self, responses: list[dict]):
        super().__init__(key="test-key")
        self.responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        if not self.responses:
            raise LLMError("plus de réponses prévues")
        self.calls += 1
        r = self.responses.pop(0)
        return json.dumps(r)


class TestContext(unittest.TestCase):
    def test_contexte_compile_les_champs(self):
        ctx = _context({"country": "Ukraine", "weight_class": "Poids lourds",
                        "birth_date": "1987-01-17", "orgs": ["ibf"]})
        self.assertIn("Ukraine", ctx)
        self.assertIn("Poids lourds", ctx)
        self.assertIn("1987-01-17", ctx)
        self.assertIn("ibf", ctx)

    def test_contexte_vide_sans_donnees(self):
        self.assertEqual(_context({"name": "X"}), "")


class TestRunResolveBatch(unittest.TestCase):
    def test_fusions_distinctes_et_inconclusives(self):
        with tempfile.TemporaryDirectory() as tmp:
            annuaire = Path(tmp) / "merged.json"
            make_annuaire(annuaire, [
                entry("Tyson Fury", "UK"),
                entry("Tyson Fury Jr", "UK"),       # same → fusion
                entry("Aaron Pico", "USA"),
                entry("Aaron Prince", "USA"),       # different
                entry("Adilson Silva", "Brazil"),
                entry("Anderson Silva", "Brazil"),  # same mais confiance basse
            ])
            client = FakeClient([
                {"same": True, "confidence": 0.97, "reason": "même boxeur"},
                {"same": False, "confidence": 0.9, "reason": "homonymes"},
                {"same": True, "confidence": 0.6, "reason": "peut-être"},
            ])
            report = run_resolve_batch(client, str(annuaire), max_pairs=10,
                                       pause=0)
            self.assertEqual(report["paires_suspectes"], 3)
            self.assertEqual(report["paires_analysees"], 3)
            self.assertEqual(len(report["fusions"]), 1)
            self.assertEqual(report["fusions"][0]["name_a"], "Tyson Fury")
            self.assertEqual(len(report["distinctes"]), 1)
            self.assertEqual(report["distinctes"][0]["name_a"], "Aaron Pico")
            self.assertEqual(len(report["inconclusives"]), 1)

    def test_erreur_llm_ne_bloque_pas(self):
        with tempfile.TemporaryDirectory() as tmp:
            annuaire = Path(tmp) / "merged.json"
            make_annuaire(annuaire, [
                entry("Aaaa Bbbb"), entry("Aaaa Bbby"),
                entry("Cccc Dddd"), entry("Cccc Dddy"),
            ])
            client = FakeClient([])  # complete → LLMError
            report = run_resolve_batch(client, str(annuaire), max_pairs=10,
                                       pause=0)
            self.assertEqual(report["paires_analysees"], 0)
            self.assertEqual(len(report["errors"]), 2)

    def test_sans_cle_leve_erreur(self):
        with tempfile.TemporaryDirectory() as tmp:
            annuaire = Path(tmp) / "merged.json"
            make_annuaire(annuaire, [entry("Aaaa Bbbb")])
            with self.assertRaises(LLMError):
                run_resolve_batch(LLMClient(key=""), str(annuaire))

    def test_save_report_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            save_report({"fusions": [{"name_a": "X"}]}, str(path))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["fusions"][0]["name_a"], "X")


if __name__ == "__main__":
    unittest.main()
