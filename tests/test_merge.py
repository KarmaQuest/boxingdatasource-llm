"""Fusion smart des combats LLM dans un shard — dédup par clé date+paire.

Cas réels du 17/08/2026 : le même combat vu par le spider WBC et par le
LLM a des ids SHA-256 différents (méthode, weight_class, nom tronqué) ;
la fusion passe donc par la paire de boxeurs normalisée, pas par l'id.
"""

import unittest

from llm.integrate import merge_fights, merge_key


# --- combats du shard spider wbc.json (extraits réels) ---
SPIDER_THORSLUND = {
    "date": "2026-08-10", "location": "Orlando, Florida", "weight_class": "bantamweight",
    "fighter_a": "Dina Thorslund", "fighter_b": "Cherneka Johnson",
    "winner": "Dina Thorslund", "method": "PTS", "rounds": 0,
    "is_title_fight": False, "id": "a2f55dbbd1b6", "source": "spider",
}
SPIDER_ZEPEDA = {
    "date": "2026-08-02", "location": "Riverside, California", "weight_class": "lightweight",
    "fighter_a": "William Zepeda", "fighter_b": "Lamont Roach Jr",
    "winner": "William Zepeda", "method": "PTS", "rounds": 0,
    "is_title_fight": False, "id": "120b4d1f0ae4", "source": "spider",
}
SPIDER_MCHANJA = {
    "date": "2026-07-31", "location": "London", "weight_class": "featherweight",
    "fighter_a": "Mchanja Yohana", "fighter_b": "John Vincent Moriana",
    "winner": "Mchanja Yohana", "method": "PTS", "rounds": 0,
    "is_title_fight": False, "id": "e30bef11452a", "source": "spider",
}
SPIDER = [SPIDER_THORSLUND, SPIDER_ZEPEDA, SPIDER_MCHANJA]


# --- combats LLM fights-wbc.json (extraits réels) ---
LLM_THORSLUND = {
    "date": "2026-08-10", "location": "Orlando, Florida", "weight_class": "bantamweight",
    "fighter_a": "Dina Thorslund", "fighter_b": "Cherneka Johnson",
    "winner": "Dina Thorslund", "method": "UD", "rounds": 10,
    "is_title_fight": False, "source": "wbc",
}
LLM_ZEPEDA_CAMARON = {
    "date": "2026-08-04", "location": "Mexico", "weight_class": "lightweight",
    "fighter_a": "William \"Camarón\" Zepeda", "fighter_b": "Lamont Roach Jr.",
    "winner": "William \"Camarón\" Zepeda", "method": "UD", "rounds": 0,
    "is_title_fight": False, "source": "wbc",
}
LLM_ZEPEDA = {
    "date": "2026-08-02", "location": "Riverside, California", "weight_class": "lightweight",
    "fighter_a": "William Zepeda", "fighter_b": "Lamont Roach Jr.",
    "winner": "William Zepeda", "method": "UD", "rounds": 12,
    "is_title_fight": False, "source": "wbc",
}
LLM_MAFIA = {
    "date": "2026-07-31", "location": "Kampala, Uganda", "weight_class": "super featherweight",
    "fighter_a": "Ibrahim Mafia", "fighter_b": "Samuel Martei",
    "winner": "Ibrahim Mafia", "method": "PTS", "rounds": 0,
    "is_title_fight": False, "source": "wbc",
}
LLM_MCHANJA = {
    "date": "2026-07-31", "location": "London", "weight_class": "featherweight",
    "fighter_a": "Mchanja Yohana", "fighter_b": "John Vincent Moriana (Lauriaga",
    "winner": "Mchanja Yohana", "method": "PTS", "rounds": 0,
    "is_title_fight": False, "source": "wbc",
}
LLM = [LLM_THORSLUND, LLM_ZEPEDA_CAMARON, LLM_ZEPEDA, LLM_MAFIA, LLM_MCHANJA]


class MergeKeyTest(unittest.TestCase):
    def test_paire_insensible_a_l_ordre(self):
        self.assertEqual(
            merge_key(LLM_THORSLUND), merge_key(SPIDER_THORSLUND)
        )

    def test_guillemets_et_ponctuation_ignores(self):
        self.assertEqual(merge_key(LLM_ZEPEDA), merge_key(SPIDER_ZEPEDA))

    def test_parenthèse_non_fermee_tronquee(self):
        self.assertEqual(merge_key(LLM_MCHANJA), merge_key(SPIDER_MCHANJA))


class MergeFightsTest(unittest.TestCase):
    def test_doublons_ignores(self):
        merged, added = merge_fights(SPIDER, LLM)
        self.assertEqual(added, 2)  # uniquement Camarón 08-04 + Ibrahim Mafia
        self.assertEqual(len(merged), 5)

    def test_spider_prioritaire_sur_llm(self):
        merged, _ = merge_fights(SPIDER, LLM)
        thorslund = next(f for f in merged if f["date"] == "2026-08-10")
        self.assertEqual(thorslund["method"], "PTS")  # le spider gagne
        self.assertEqual(thorslund["source"], "spider")

    def test_trie_par_date_decroissante(self):
        merged, _ = merge_fights(SPIDER, LLM)
        dates = [f["date"] for f in merged]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_nouveau_combat_ajoute(self):
        merged, added = merge_fights([], LLM)
        self.assertEqual(added, 5)
        self.assertIn("Ibrahim Mafia", [f["fighter_a"] for f in merged])

    def test_shard_vide_et_nouveaux_vides(self):
        merged, added = merge_fights([], [])
        self.assertEqual(merged, [])
        self.assertEqual(added, 0)

    def test_doublon_strict_dans_la_meme_source(self):
        merged, added = merge_fights([SPIDER_THORSLUND], [SPIDER_THORSLUND])
        self.assertEqual(added, 0)
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()