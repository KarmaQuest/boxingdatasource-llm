"""Tests du batch d'extraction et de la lecture des sources (hors réseau)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.batch import (  # noqa: E402
    _is_result_article,
    run_batch,
    save_results,
)
from llm.client import LLMClient, LLMError  # noqa: E402
from llm.sources import (  # noqa: E402
    _RSS_ITEM_RE,
    _decode_body,
    _strip_html,
    fetch_wbc_articles,
    fetch_wbo_articles,
    _get,
)

GOOD_JSON = """
{"fights": [
  {"winner": "Ping Tai Ng", "loser": "Somphot Seesa", "method": "TKO",
   "rounds": 5, "weight_class": "Light Heavyweight",
   "is_title_fight": true, "location": "BANGKOK, THAILAND"}
]}
"""


class FakeClient(LLMClient):
    def __init__(self, response: str = GOOD_JSON):
        super().__init__(key="test-key")
        self.response = response
        self.calls = 0

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        self.calls += 1
        return self.response


class FakeSources:
    """Remplace llm.sources.fetch_* pour le test (pas de réseau)."""

    def __init__(self, articles: list[dict]):
        self.articles = articles

    def fetch_wbc(self, year: int = 2026):
        return self.articles

    def fetch_wbo(self, max_items: int = 30):
        return self.articles


ARTICLE = {
    "title": "OPBF Results from Bangkok",
    "date": "2026-08-12",
    "content": (
        "BANGKOK, THAILAND - Hong Kong's Ping Tai Ng put on a dominant "
        "performance, stopping local favorite Somphot Seesa via technical "
        "knockout at 1:32 of the fifth round."
    ),
}


class TestStripHtml(unittest.TestCase):
    def test_html_retire(self):
        self.assertEqual(
            _strip_html("<p>Hello <b>World</b> &amp; co</p>"), "Hello World & co"
        )


class TestDecodeBody(unittest.TestCase):
    def test_utf8_valide(self):
        self.assertEqual(_decode_body("Rafael Espinoza".encode("utf-8")),
                         "Rafael Espinoza")

    def test_guillemets_windows1252_plus_de_mojibake(self):
        # « „El Divino“ » encodé en Windows-1252 (0x84 … 0x94) : UTF-8 strict
        # échoue → fallback Windows-1252 → guillemets corrects, pas de « �? ».
        raw = "Rafael „El Divino“ Espinoza".encode("windows-1252")
        self.assertEqual(_decode_body(raw), "Rafael „El Divino“ Espinoza")
        self.assertNotIn("\ufffd", _decode_body(raw))


class TestSourcesParsing(unittest.TestCase):
    def test_rss_items_extraits(self):
        feed = (
            "<rss><channel>"
            "<item><title>Article 1</title><link>https://a/1</link></item>"
            "<item><title>Article 2</title><link>https://a/2</link></item>"
            "</channel></rss>"
        )
        items = _RSS_ITEM_RE.findall(feed)
        self.assertEqual(len(items), 2)

    def test_strip_html_encodage(self):
        self.assertIn("bout", _strip_html("<p>un bout de texte</p>"))


class TestIsResultArticle(unittest.TestCase):
    def test_titres_resultats(self):
        for title in (
            "OPBF Results from Bangkok",
            "Great OPBF Bouts in South Korea",
            "Ping Tai Ng Crowned Light Heavyweight Champion",
        ):
            self.assertTrue(_is_result_article(title), title)

    def test_titres_exclus(self):
        for title in (
            "Scott and Shields Make Weight",
            "Veyre Very Ready to Defend Her Title Against Ferreira",
            "Camila Zamorano to Defend Her WBC World Championship",
            "Behind The Brilliance: The Untold Story",
"WBC Issues Essential Athlete Hydration Guidelines",
              "On This Day in Boxing... August 14",
              "Isaac Cruz Brings Hope and Solidarity to Children",
              # promos d'affiches à venir, pas des résultats
              "Brian Norman Jr. Defends Crown June 19 Against Jin Sasaki LIVE on ESPN+",
              "Re: WBO Female Middleweight Champion, Claressa Shields",
              "Taylor vs Serrano 3 LIVE on Netflix Friday, July 11",
              "Canelo Will Face Crawford in September",
          ):
              self.assertFalse(_is_result_article(title), title)


class TestRunBatch(unittest.TestCase):
    def test_batch_wbc_avec_source_mockee(self):
        client = FakeClient()
        # on mocke llm.sources.fetch_wbc_articles via run_batch ? Non :
        # run_batch appelle fetch_wbc_articles directement. On teste le
        # cas réel sans réseau en patchant le module.
        import llm.batch as batch_mod

        batch_mod.fetch_wbc_articles = lambda year=2026: [ARTICLE]
        results = run_batch(client, "wbc", year=2026, pause=0)
        self.assertEqual(results["articles"], 1)
        self.assertEqual(results["articles_ok"], 1)
        self.assertEqual(results["total_fights"], 1)
        self.assertEqual(results["combats"][0]["fights"][0]["winner"],
                         "Ping Tai Ng")
        self.assertEqual(results["errors"], [])

    def test_batch_article_echoue_ne_bloque_pas(self):
        import llm.batch as batch_mod

        class BoomClient(FakeClient):
            def complete(self, prompt, max_tokens=2000):
                raise LLMError("quota épuisé")

        batch_mod.fetch_wbc_articles = lambda year=2026: [ARTICLE, ARTICLE]
        results = run_batch(BoomClient(), "wbc", pause=0)
        self.assertEqual(results["articles_ok"], 0)
        self.assertEqual(len(results["errors"]), 2)

    def test_batch_source_inconnue(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            run_batch(client, "wba")

    def test_batch_wbo_avec_source_mockee(self):
        client = FakeClient()
        import llm.batch as batch_mod

        batch_mod.fetch_wbo_articles = lambda max_items=30: [ARTICLE]
        results = run_batch(client, "wbo", pause=0)
        self.assertEqual(results["articles"], 1)
        self.assertEqual(results["articles_ok"], 1)
        self.assertEqual(results["total_fights"], 1)
        self.assertEqual(results["combats"][0]["fights"][0]["winner"],
                         "Ping Tai Ng")
        self.assertEqual(results["errors"], [])

    def test_batch_sans_cle(self):
        with self.assertRaises(LLMError):
            run_batch(LLMClient(key=""), "wbc")

    def test_save_results_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            save_results({"total_fights": 1, "combats": []}, str(path))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["total_fights"], 1)


if __name__ == "__main__":
    unittest.main()
