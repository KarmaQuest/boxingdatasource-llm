"""Tests de llm/profiles.py — candidats, schéma et application déterministe
(sans LLM ni réseau)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.profiles import (  # noqa: E402
    _FIELD_VALIDATORS,
    _infobox_excerpt,
    _normalize_response,
    apply_report,
    load_candidates,
)


def _boxer(**overrides) -> dict:
    base = {
        "slug": "rico-verhoeven",
        "name": "Rico Verhoeven",
        "wikidata_id": "Q7332289",
        "stance": "",
        "height_cm": 0,
        "reach_cm": 0,
        "record": [0, 0, 0, 0],
    }
    base.update(overrides)
    return base


class TestLoadCandidates(unittest.TestCase):
    def test_retient_les_fiches_incompletes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "merged.json"
            p.write_text(json.dumps([
                _boxer(),  # tout vide → candidat
                _boxer(slug="usyk", wikidata_id="Q1718523",
                       stance="Southpaw", height_cm=191, reach_cm=198),
                _boxer(slug="noqid", wikidata_id=""),
                _boxer(slug="half", height_cm=0, reach_cm=198),  # reach seul → candidat
            ]), encoding="utf-8")
            cands = load_candidates(p)
            slugs = {c["slug"] for c in cands}
            self.assertEqual(slugs, {"rico-verhoeven", "half"})

    def test_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "merged.json"
            p.write_text(json.dumps([_boxer(), _boxer(slug="b"), _boxer(slug="c")]),
                         encoding="utf-8")
            self.assertEqual(len(load_candidates(p, limit=2)), 2)

    def test_annuaire_absent(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_candidates("/absent.json")


class TestSchema(unittest.TestCase):
    def test_validators(self) -> None:
        self.assertTrue(_FIELD_VALIDATORS["stance"]("Southpaw"))
        self.assertTrue(_FIELD_VALIDATORS["stance"]("Orthodoxe"))
        self.assertFalse(_FIELD_VALIDATORS["stance"]("Gaucher"))
        self.assertTrue(_FIELD_VALIDATORS["height_cm"](191))
        self.assertFalse(_FIELD_VALIDATORS["height_cm"](0))
        self.assertFalse(_FIELD_VALIDATORS["reach_cm"](-5))
        self.assertFalse(_FIELD_VALIDATORS["reach_cm"](True))
        self.assertTrue(_FIELD_VALIDATORS["record"](
            {"wins": 1, "losses": 0, "draws": 0, "ko": 1}))
        self.assertFalse(_FIELD_VALIDATORS["record"](
            {"wins": -1, "losses": 0, "draws": 0, "ko": 1}))


class TestNormalize(unittest.TestCase):
    def test_normalise_la_garde(self) -> None:
        out = _normalize_response({
            "stance": {"value": "Gaucher", "confidence": "0.99", "quote": "Gaucher"},
        })
        self.assertEqual(out["stance"]["value"], "Southpaw")
        self.assertEqual(out["stance"]["confidence"], 0.99)

    def test_champ_non_conforme_force_a_null(self) -> None:
        out = _normalize_response({
            "stance": {"value": "Ambidextre", "confidence": 1.0, "quote": ""},
            "height_cm": {"value": "grand", "confidence": 0.9, "quote": ""},
        })
        self.assertEqual(out["stance"]["value"], "Switch")  # ambidextre → Switch
        self.assertEqual(out["height_cm"]["value"], None)
        self.assertEqual(out["height_cm"]["confidence"], 0.0)

    def test_confiance_bornee(self) -> None:
        out = _normalize_response({
            "height_cm": {"value": 191, "confidence": 3.0, "quote": ""},
        })
        self.assertEqual(out["height_cm"]["confidence"], 1.0)


class TestApply(unittest.TestCase):
    def test_apply_seuil_et_schema(self) -> None:
        report = {
            "profiles": [{
                "slug": "usyk", "name": "Oleksandr Usyk",
                "fields": {
                    "stance": {"value": "Southpaw", "confidence": 0.98, "quote": ""},
                    "height_cm": {"value": 191, "confidence": 0.95, "quote": ""},
                    "reach_cm": {"value": None, "confidence": 0.0, "quote": ""},
                },
            }],
        }
        corr = apply_report(report, threshold=0.9)
        self.assertEqual(corr, [{
            "slug": "usyk", "name": "Oleksandr Usyk",
            "corrections": {"stance": "Southpaw", "height_cm": 191},
        }])

    def test_apply_seuil_abaissable(self) -> None:
        report = {
            "profiles": [{
                "slug": "x", "name": "X",
                "fields": {"stance": {"value": "Southpaw", "confidence": 0.85, "quote": ""}},
            }],
        }
        self.assertEqual(apply_report(report, threshold=0.9), [])
        self.assertEqual(len(apply_report(report, threshold=0.8)), 1)


class TestInfoboxExcerpt(unittest.TestCase):
    def test_commence_a_linfobox(self) -> None:
        wt = "préambule...\n{{Infobox boxeur\n| taille = 191\n}}"
        self.assertTrue(_infobox_excerpt(wt).startswith("{{Infobox"))

    def test_limite(self) -> None:
        wt = "{{Infobox}} " + "x" * 5000
        self.assertLessEqual(len(_infobox_excerpt(wt)), 3500)


if __name__ == "__main__":
    unittest.main()