"""Tests de l'extraction prose par LLM (articles WBC/WBO)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.client import LLMClient, LLMError  # noqa: E402
from llm.extract import (  # noqa: E402
    _first_json_object,
    _name_in_text,
    _surname,
    build_prompt,
    extract_fights_llm,
    filter_plausible,
    parse_llm_json,
    _validate_fight,
)

# La phrase à sujet pronominal que le parser regex WBC saute
PROSE = (
    "BANGKOK, THAILAND - Hong Kong's Ping Tai Ng put on a dominant "
    "performance, stopping local favorite Somphot Seesa via technical "
    "knockout at 1:32 of the fifth round. Levale Whittington was dominated "
    "all 6 rounds by the Japanese who won by unanimous decision."
)

GOOD_JSON = """
```json
{"fights": [
  {"winner": "Ping Tai Ng", "loser": "Somphot Seesa", "method": "TKO",
   "rounds": 5, "weight_class": "Light Heavyweight",
   "is_title_fight": true, "location": "BANGKOK, THAILAND"},
  {"winner": "Retio Tsutsumi", "loser": "Japanese challenger", "method": "UD",
   "rounds": 0, "weight_class": "", "is_title_fight": false, "location": ""}
]}
```
"""


class FakeClient(LLMClient):
    """Client de test : réponse prédéfinie (pas de réseau, pas de clé)."""

    def __init__(self, response: str = GOOD_JSON):
        super().__init__(key="test-key")  # available = True
        self.response = response
        self.last_prompt = ""

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        self.last_prompt = prompt
        return self.response


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contient_contexte(self):
        p = build_prompt("Texte…", "2026-08-12", "wbc")
        self.assertIn("2026-08-12", p)
        self.assertIn("wbc", p)
        self.assertIn("Texte…", p)
        self.assertIn("JAMAIS inventer", p)


class TestValidateFight(unittest.TestCase):
    def test_combat_valide_nettoie(self):
        f = _validate_fight({
            "winner": "  Ping Tai   Ng ", "loser": "Somphot Seesa",
            "method": "tko", "rounds": "5",
            "weight_class": "Light Heavyweight",
            "is_title_fight": "true", "location": "BANGKOK, THAILAND",
        })
        self.assertEqual(f["winner"], "Ping Tai Ng")
        self.assertEqual(f["method"], "TKO")
        self.assertEqual(f["rounds"], 5)
        self.assertTrue(f["is_title_fight"])

    def test_vainqueur_manquant_rejete(self):
        self.assertIsNone(_validate_fight({"loser": "X", "method": "UD"}))

    def test_meme_personne_rejetee(self):
        self.assertIsNone(_validate_fight({
            "winner": "Canelo", "loser": "canelo", "method": "UD",
        }))

    def test_methode_inconnue_videe(self):
        f = _validate_fight({
            "winner": "A", "loser": "B", "method": "DECISION", "rounds": 0,
        })
        self.assertEqual(f["method"], "")  # le schéma rejettera si requis

    def test_rounds_invalides_a_zero(self):
        f = _validate_fight({
            "winner": "A", "loser": "B", "method": "UD", "rounds": "abc",
        })
        self.assertEqual(f["rounds"], 0)

    def test_parenthèse_non_fermee_retiree_du_nom(self):
        f = _validate_fight({
            "winner": "John Vincent Moriana (Lauriaga", "loser": "B",
            "method": "PTS", "rounds": 0,
        })
        self.assertEqual(f["winner"], "John Vincent Moriana")


class TestParseLlmJson(unittest.TestCase):
    def test_parse_json_avec_fences_markdown(self):
        fights = parse_llm_json(GOOD_JSON)
        self.assertEqual(len(fights), 2)
        self.assertEqual(fights[0]["winner"], "Ping Tai Ng")
        self.assertEqual(fights[1]["loser"], "Japanese challenger")

    def test_aucun_combat(self):
        self.assertEqual(parse_llm_json('{"fights": []}'), [])

    def test_pas_de_json_leve_erreur(self):
        with self.assertRaises(LLMError):
            parse_llm_json("Je n'ai trouvé aucun combat.")

    def test_json_invalide_leve_erreur(self):
        with self.assertRaises(LLMError):
            parse_llm_json('{"fights": [}')

    def test_reponse_bavarde_deux_objets(self):
        # le modèle répond du texte + le JSON + du texte
        fights = parse_llm_json(
            'Voici le résultat : ' + GOOD_JSON + ' Fin du rapport.'
        )
        self.assertEqual(len(fights), 2)

    def test_accolades_dans_les_chaines(self):
        # un surnom avec accolade ne casse pas l'extraction
        obj = _first_json_object('{"name": "Bob {le} rouge", "x": 1}')
        self.assertEqual(obj, '{"name": "Bob {le} rouge", "x": 1}')


class TestExtractFightsLlm(unittest.TestCase):
    def test_extraction_valide(self):
        client = FakeClient()
        fights = extract_fights_llm(client, PROSE, "2026-08-12", "wbc")
        # « Ping Tai Ng » est cité dans l'article → gardé.
        # « Retio Tsutsumi » n'apparaît PAS dans le texte → hallucination
        # probable → écarté par le garde-fou.
        self.assertEqual(len(fights), 1)
        self.assertEqual(fights[0]["method"], "TKO")
        self.assertEqual(fights[0]["winner"], "Ping Tai Ng")
        # la date de publication est passée dans le prompt
        self.assertIn("2026-08-12", client.last_prompt)

    def test_sans_cle_leve_erreur_propre(self):
        client = LLMClient(key="")  # available = False
        with self.assertRaises(LLMError):
            extract_fights_llm(client, PROSE, "2026-08-12")


class TestGardeFouAntiHallucination(unittest.TestCase):
    def test_vainqueur_absent_du_texte_ecarte(self):
        fights = [_validate_fight({
            "winner": "Terence Crawford", "loser": "Saul Canelo Alvarez",
            "method": "UD", "rounds": 12,
        })]
        kept, dropped = filter_plausible(fights, "Un article sans rapport.")
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_vainqueur_cite_par_nom_de_famille_garde(self):
        text = "Espinoza retained his title against Khegai."
        fights = [_validate_fight({
            "winner": "Rafael Espinoza", "loser": "Arnold Khegai",
            "method": "TKO", "rounds": 11,
        })]
        kept, dropped = filter_plausible(fights, text)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_nom_complet_cite_garde(self):
        self.assertTrue(_name_in_text("Ping Tai Ng",
                                      "BANGKOK - Ping Tai Ng stopped Seesa."))
        self.assertFalse(_name_in_text("Retio Tsutsumi",
                                       "BANGKOK - the Japanese won by UD."))

    def test_accents_et_casse_ignores(self):
        self.assertTrue(_name_in_text("Café Axé", "Un texte sur cafe axe."))

    def test_surname_retourne_dernier_mot(self):
        self.assertEqual(_surname("Rafael Espinoza"), "espinoza")


if __name__ == "__main__":
    unittest.main()
