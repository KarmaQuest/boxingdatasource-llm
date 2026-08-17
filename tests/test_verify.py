"""Tests de llm/verify.py — règles déterministes (sans LLM)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.verify import (  # noqa: E402
    Annuaire,
    _norm,
    deterministic_checks,
    verify_schedule,
)


def _shard(tmp: Path, org: str, fights: list[dict]) -> Path:
    shards = tmp / "fights-upcoming"
    shards.mkdir(parents=True, exist_ok=True)
    (shards / f"{org}.json").write_text(
        json.dumps(fights, ensure_ascii=False), encoding="utf-8"
    )
    return shards


def _fight(**overrides) -> dict:
    base = {
        "id": "x",
        "date": "2026-10-10",
        "location": "Las Vegas",
        "weight_class": "Poids lourds",
        "fighter_a": "Oleksandr Usyk",
        "fighter_b": "Tyson Fury",
        "is_title_fight": True,
    }
    base.update(overrides)
    return base


class TestAnnuaire(unittest.TestCase):
    def test_charge_et_connait(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ann = Path(td) / "merged.json"
            ann.write_text(json.dumps([
                {"name": "Oleksandr Usyk", "aliases": ["Usyk"]},
                {"name": "Naoya Inoue"},
            ]), encoding="utf-8")
            a = Annuaire(ann)
            self.assertTrue(a.loaded)
            self.assertTrue(a.knows("oleksandr usyk"))
            self.assertTrue(a.knows("usyk"))  # nom de famille
            self.assertFalse(a.knows("Personne Inconnue"))

    def test_annuaire_absent(self) -> None:
        a = Annuaire(Path("/absent"))
        self.assertFalse(a.loaded)
        self.assertFalse(a.knows("n'importe qui"))


class TestDeterministicChecks(unittest.TestCase):
    def test_combat_valide(self) -> None:
        checks = deterministic_checks(
            _fight(),
            today=date(2026, 8, 17),
            annuaire=Annuaire(Path("/absent")),
            pair_orgs={"oleksandr usyk|tyson fury": {"wbc"}},
        )
        self.assertTrue(checks["future_date"])
        self.assertTrue(checks["plausible_date"])
        self.assertTrue(checks["names_ok"])
        self.assertFalse(checks["annuaire"])  # annuaire absent → non confirmable
        self.assertTrue(checks["no_dup_across_orgs"])

    def test_date_passee_echoue(self) -> None:
        checks = deterministic_checks(
            _fight(date="2026-01-01"),
            today=date(2026, 8, 17),
            annuaire=Annuaire(Path("/absent")),
            pair_orgs={},
        )
        self.assertFalse(checks["future_date"])

    def test_date_trop_lointaine_echoue(self) -> None:
        checks = deterministic_checks(
            _fight(date="2029-01-01"),
            today=date(2026, 8, 17),
            annuaire=Annuaire(Path("/absent")),
            pair_orgs={},
        )
        self.assertFalse(checks["plausible_date"])

    def test_doublon_inter_orgs_signale(self) -> None:
        pair = {"oleksandr usyk|tyson fury": {"wbc", "ibf"}}
        checks = deterministic_checks(
            _fight(),
            today=date(2026, 8, 17),
            annuaire=Annuaire(Path("/absent")),
            pair_orgs=pair,
        )
        self.assertFalse(checks["no_dup_across_orgs"])


class TestVerifySchedule(unittest.TestCase):
    def test_rapport_confirme_et_signale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fights = [
                _fight(id="a", date="2026-10-10"),   # tout bon
                _fight(id="b", date="2025-01-01"),   # date passée
            ]
            shards = _shard(Path(td), "wbc", fights)
            report = verify_schedule(
                shards, use_llm=False, today=date(2026, 8, 17)
            )
            self.assertEqual(report["total"], 2)
            statuses = {i["id"]: i["status"] for i in report["items"]}
            self.assertEqual(statuses, {"a": "confirmed", "b": "flagged"})
            flagged = next(i for i in report["items"] if i["id"] == "b")
            self.assertIn("future_date", flagged["failed_checks"])

    def test_meme_affiche_deux_orgs_signale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = _fight(id="x")
            shards = _shard(Path(td), "wbc", [f])
            (shards / "ibf.json").write_text(
                json.dumps([{**f, "id": "y", "org": "IBF"}], ensure_ascii=False),
                encoding="utf-8",
            )
            report = verify_schedule(
                shards, use_llm=False, today=date(2026, 8, 17)
            )
            items = {i["fighter_a"]: i for i in report["items"]}
            self.assertFalse(items["Oleksandr Usyk"]["checks"]["no_dup_across_orgs"])
            self.assertEqual(items["Oleksandr Usyk"]["status"], "flagged")


class TestNorm(unittest.TestCase):
    def test_normalise_accents_et_casse(self) -> None:
        self.assertEqual(_norm("Canelo Álvarez"), _norm("canelo alvarez"))
        self.assertEqual(_norm("D'Golden Boy"), "d golden boy")


if __name__ == "__main__":
    unittest.main()
