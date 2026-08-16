"""Tests du pont d'intégration — combats LLM → format pipeline (Fight)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.integrate import (  # noqa: E402
    fights_to_pipeline_format,
    to_pipeline_fight,
)

BATCH = {
    "combats": [
        {
            "title": "OPBF Results from Bangkok",
            "date": "2026-08-12",
            "fights": [
                {"winner": "Ping Tai Ng", "loser": "Somphot Seesa",
                 "method": "TKO", "rounds": 5,
                 "weight_class": "Light Heavyweight",
                 "is_title_fight": True, "location": "BANGKOK, THAILAND"},
                {"winner": "Hayate Hanada", "loser": "Vanlalenkawitera",
                 "method": "SD", "rounds": 10,
                 "weight_class": "Super Flyweight",
                 "is_title_fight": True, "location": "BANGKOK, THAILAND"},
            ],
        },
    ]
}


class TestToPipelineFight(unittest.TestCase):
    def test_combat_valide_converti(self):
        f = to_pipeline_fight(BATCH["combats"][0]["fights"][0],
                              "wbc", date="2026-08-12")
        self.assertEqual(f["date"], "2026-08-12")
        self.assertEqual(f["fighter_a"], "Ping Tai Ng")
        self.assertEqual(f["winner"], "Ping Tai Ng")
        self.assertEqual(f["method"], "TKO")
        self.assertEqual(f["rounds"], 5)
        self.assertTrue(f["is_title_fight"])
        self.assertEqual(f["source"], "wbc")

    def test_date_invalide_rejetee(self):
        self.assertIsNone(to_pipeline_fight(
            {"winner": "A", "loser": "B", "method": "UD"},
            "wbc", date="12/08/2026"))

    def test_methode_inconnue_rejetee(self):
        self.assertIsNone(to_pipeline_fight(
            {"winner": "A", "loser": "B", "method": "DECISION", "rounds": 0},
            "wbc", date="2026-08-12"))

    def test_meme_personne_rejetee(self):
        self.assertIsNone(to_pipeline_fight(
            {"winner": "Canelo", "loser": "canelo", "method": "UD"},
            "wbc", date="2026-08-12"))

    def test_rounds_invalides_a_zero(self):
        f = to_pipeline_fight(
            {"winner": "A", "loser": "B", "method": "UD", "rounds": "abc"},
            "wbc", date="2026-08-12")
        self.assertEqual(f["rounds"], 0)


class TestFightsToPipelineFormat(unittest.TestCase):
    def test_batch_converti_en_liste_pipeline(self):
        fights = fights_to_pipeline_format(BATCH, source="wbc")
        self.assertEqual(len(fights), 2)
        self.assertEqual(fights[0]["date"], "2026-08-12")
        self.assertEqual(fights[1]["fighter_a"], "Hayate Hanada")

    def test_batch_vide_liste_vide(self):
        self.assertEqual(fights_to_pipeline_format({"combats": []}, "wbc"), [])

    def test_les_champs_du_contrat_sont_presents(self):
        fights = fights_to_pipeline_format(BATCH, source="wbc")
        for key in ("date", "location", "weight_class", "fighter_a",
                    "fighter_b", "winner", "method", "rounds",
                    "is_title_fight"):
            self.assertIn(key, fights[0])


if __name__ == "__main__":
    unittest.main()