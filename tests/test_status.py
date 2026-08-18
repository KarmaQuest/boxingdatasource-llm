"""Tests de status.py — rapport d'état local (aucun LLM, aucun réseau)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from status import collect_status, render_human  # noqa: E402


def _tree() -> Path:
    td = tempfile.mkdtemp()
    out = Path(td) / "out"
    (out / "llm").mkdir(parents=True)
    (out / "llm" / "batch-wbc.json").write_text(
        json.dumps([{"id": "a"}]), encoding="utf-8")
    (out / "fights-upcoming-verification.json").write_text(
        json.dumps({
            "generated_at": "2026-08-18T10:00:00Z",
            "total": 21, "confirmed": 20, "flagged": 1,
            "llm_calls": 1, "llm_available": True,
        }), encoding="utf-8")
    return out


def _empty() -> Path:
    return Path(tempfile.mkdtemp())


def _cleanup(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class TestCollectStatus(unittest.TestCase):
    def test_rapport_plein(self) -> None:
        out = _tree()
        self.addCleanup(_cleanup, out)
        s = collect_status(out)
        self.assertTrue(s["generated_at"])
        self.assertEqual(len(s["outputs"]), 1)
        key = os.path.join("llm", "batch-wbc.json")
        self.assertIn(key, s["outputs"])
        v = s["verification"]
        self.assertTrue(v["present"])
        self.assertEqual(v["total"], 21)
        self.assertEqual(v["confirmed"], 20)
        self.assertEqual(v["flagged"], 1)
        self.assertEqual(v["llm_calls"], 1)

    def test_verification_absente(self) -> None:
        out = _empty()
        self.addCleanup(_cleanup, out)
        s = collect_status(out)
        self.assertFalse(s["verification"]["present"])
        self.assertEqual(s["outputs"], {})

    def test_config_sans_secret(self) -> None:
        out = _tree()
        self.addCleanup(_cleanup, out)
        s = collect_status(out)
        self.assertEqual(s["config"]["model"], "gemini-flash-lite-latest")
        self.assertNotIn("api_key", json.dumps(s))
        self.assertNotIn("GEMINI", json.dumps(s))


class TestRenderHuman(unittest.TestCase):
    def test_rendu_lisible(self) -> None:
        out = _tree()
        self.addCleanup(_cleanup, out)
        text = render_human(collect_status(out))
        self.assertIn("boxingdatasource-llm", text)
        self.assertIn("batch-wbc.json", text)
        self.assertIn("21", text)
        self.assertIn("confirmés", text)

    def test_rendu_sans_verification(self) -> None:
        out = _empty()
        self.addCleanup(_cleanup, out)
        text = render_human(collect_status(out))
        self.assertIn("absent", text)


if __name__ == "__main__":
    unittest.main()