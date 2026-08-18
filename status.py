"""État du module LLM — rapport de synthèse consommé par boxing-ops.

`python main.py status [--json]` produit un rapport lisible (humain) ou un
objet JSON stable :
    {
      "generated_at": "…",
      "output_dir": "…",
      "llm_available": true,
      "outputs": {chemin_relatif: {size, mtime}},     # fichiers boxers/llm/
      "verification": {                                # fights-upcoming-verification.json
        "present": true, "generated_at": "…",
        "total": 21, "confirmed": 20, "flagged": 1,
        "llm_calls": 1, "llm_available": true
      },
      "config": {"model": "…", "pause_batch": 4, "pause_resolve": 15}
    }

Lecture 100 % locale (fichiers générés + config) — aucun appel LLM ni réseau.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT.parent / "boxing-app" / "public" / "data"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _file_info(path: Path) -> dict:
    try:
        st = path.stat()
        return {
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except OSError:
        return {"size": 0, "mtime": None}


def collect_status(output_dir: Optional[Path] = None) -> dict:
    output = output_dir or DEFAULT_OUTPUT

    # disponibilité du client (lit la clé sans l'afficher)
    try:
        from llm.client import get_default_client

        client = get_default_client()
        llm_available = bool(client.available)
    except Exception:
        llm_available = False

    # fichiers générés (dossier boxers/llm/ = sortie du batch/integrate)
    outputs: dict[str, dict] = {}
    llm_dir = output / "llm"
    if llm_dir.exists():
        for path in sorted(llm_dir.glob("*.json")):
            outputs[str(path.relative_to(output))] = _file_info(path)

    # rapport de vérification des combats programmés
    verification: dict = {"present": False}
    verif_path = output / "fights-upcoming-verification.json"
    if verif_path.exists():
        data = _read_json(verif_path)
        verification = {
            "present": True,
            "generated_at": data.get("generated_at"),
            "total": data.get("total", 0),
            "confirmed": data.get("confirmed", 0),
            "flagged": data.get("flagged", 0),
            "llm_calls": data.get("llm_calls", 0),
            "llm_available": data.get("llm_available", False),
        }

    # configuration visible (sans secrets)
    config: dict = {"model": "gemini-flash-lite-latest", "pause_resolve": 15}
    try:
        from llm.batch import LLM_PAUSE_S

        config["pause_batch"] = LLM_PAUSE_S
    except Exception:
        pass

    return {
        "generated_at": _now_iso(),
        "output_dir": str(output),
        "llm_available": llm_available,
        "outputs": outputs,
        "verification": verification,
        "config": config,
    }


def render_human(status: dict) -> str:
    lines: list[str] = []
    lines.append(f"Module boxingdatasource-llm — {status['generated_at']}")
    lines.append(f"Sortie : {status['output_dir']}")
    lines.append("")
    lines.append(f"LLM : {'actif' if status['llm_available'] else 'inactif (GEMINI_API_KEY absente)'}")
    lines.append("")
    lines.append("Sorties générées (boxers/llm/) :")
    if not status["outputs"]:
        lines.append("   (aucune — lancer `python main.py batch` puis `integrate`)")
    for path, info in sorted(status["outputs"].items()):
        lines.append(f"   {path}  {info['size']} octets  ({info.get('mtime')})")
    lines.append("")
    lines.append("Vérification des combats programmés :")
    v = status["verification"]
    if not v.get("present"):
        lines.append("   (absent — lancer `python main.py verify …`)")
    else:
        lines.append(f"   généré : {v['generated_at']}")
        lines.append(f"   total {v['total']} — {v['confirmed']} confirmés / "
                     f"{v['flagged']} à revoir ({v['llm_calls']} appel(s) LLM)")
    return "\n".join(lines)