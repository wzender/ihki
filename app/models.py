"""The phoneme models the management panel's "מודלים" tab offers, with what each was measured to do.

Numbers: Minerva test split (255 native lines), greedy CTC mapped onto the labels' phonemes, measured 2026-10-25
with scripts/zero_shot_phone_models.py (docs/zero-shot-phone-models.md). Speeds: one 2.7 s recording, on this
project's laptop GPU and on 2 CPU threads (about what the free Space has). POWSM runs padded to 8 s (app/powsm.py):
its PER, consonant PER and long vowels below are measured that way; its other sounds at the 20 s they were first
measured at (8 s changed PER by 0.4 points). The short-padding fine-tune runs unpadded, measured that way throughout.
"""
from __future__ import annotations

from pathlib import Path

import os

from .powsm import DEPS, ONNX_PREFIX, parse_model_id

# the Hub repo the exported POWSM is uploaded to (scripts/export_powsm_onnx.py; README "POWSM on the Space")
POWSM_ONNX_REPO = os.environ.get("POWSM_ONNX_REPO", "wzender/ihki-powsm-ft").strip()

ROOT = Path(__file__).resolve().parent.parent

# Per model: recall of each sound (None: the model has no symbol for it), PER and consonant PER.
CATALOG: list[dict] = [
    {"id": "facebook/wav2vec2-xlsr-53-espeak-cv-ft", "name": "Facebook (מקורי)", "family": "wav2vec2",
     "summary": "המודל הרב־לשוני שהאתר נבנה עליו. לא אומן על ערבית פלסטינית: כמעט לא שומע ח ו־ע.",
     "trained": False, "free_space": True,
     "metrics": {"per": .38, "consonant_per": .29, "ħ": .09, "ʕ": .02, "tˤ": None, "sˤ": None, "dˤ": 0.0,
                 "long_vowels": .45},
     "speed": {"gpu": .05, "cpu": .8}, "fps": 50},
    {"id": "checkpoints/ihki-ft-minerva", "name": "wav2vec2 מאומן", "family": "wav2vec2",
     "summary": "אותו מודל, מאומן על הקלטות Minerva. מחליף ישיר: אותו גודל ואותה מהירות, גם על השרת החינמי.",
     "trained": True, "free_space": True,
     "metrics": {"per": .23, "consonant_per": .15, "ħ": .94, "ʕ": .69, "tˤ": None, "sˤ": None, "dˤ": .04,
                 "long_vowels": .74},
     "speed": {"gpu": .05, "cpu": .8}, "fps": 50},
    {"id": "checkpoints/powsm-ft-minerva/powsm_ft.pt", "name": "POWSM מאומן", "family": "POWSM-CTC",
     "summary": "מודל פונטי חדש (2026), מאומן על הקלטות Minerva. הכי מדויק, והיחיד ששומע את העיצורים המודגשים (ط ص ض).",
     "trained": True, "free_space": False,
     "metrics": {"per": .16, "consonant_per": .10, "ħ": .94, "ʕ": .89, "tˤ": .82, "sˤ": .77, "dˤ": .46,
                 "long_vowels": .68},
     "speed": {"gpu": .29, "cpu": 1.8}, "fps": 25},
    {"id": "checkpoints/powsm-ft-shortpad/powsm_ft.pt", "name": "POWSM מאומן ומהיר", "family": "POWSM-CTC",
     "summary": "אותו POWSM מאומן, שאומן שוב גם על הקלטות קצרות בלי ריפוד: מדויק כמו המקורי ומהיר פי 5 במעבד.",
     "trained": True, "free_space": True,
     "metrics": {"per": .16, "consonant_per": .105, "ħ": .92, "ʕ": .90, "tˤ": .81, "sˤ": .68, "dˤ": .64,
                 "long_vowels": .69},
     "speed": {"gpu": .06, "cpu": .8}, "fps": 25},
    {"id": f"{ONNX_PREFIX}{POWSM_ONNX_REPO}", "name": "POWSM מאומן ומהיר (לשרת)", "family": "POWSM-CTC · ONNX",
     "summary": "אותו מודל, מיוצא ל־ONNX: רץ בלי ESPnet, גם על השרת החינמי, ומהיר מהמודל הנוכחי. נטען מ־Hugging Face.",
     "trained": True, "free_space": True,
     "metrics": {"per": .165, "consonant_per": .105, "ħ": .92, "ʕ": .90, "tˤ": .81, "sˤ": .68, "dˤ": .64,
                 "long_vowels": .69},
     "speed": {"gpu": None, "cpu": .57}, "fps": 25},
    {"id": "exports/powsm-ft-shortpad", "name": "POWSM מאומן ומהיר (ONNX מקומי)", "family": "POWSM-CTC · ONNX",
     "summary": "הייצוא ל־ONNX מהתיקייה המקומית, לפני ההעלאה ל־Hugging Face.",
     "trained": True, "free_space": True,
     "metrics": {"per": .165, "consonant_per": .105, "ħ": .92, "ʕ": .90, "tˤ": .81, "sˤ": .68, "dˤ": .64,
                 "long_vowels": .69},
     "speed": {"gpu": None, "cpu": .57}, "fps": 25},
    {"id": "powsm:<ara>", "name": "POWSM (מקורי)", "family": "POWSM-CTC",
     "summary": "POWSM כפי שפורסם, עם הנחיית ערבית. שומע ח ו־ע היטב בלי אימון, אבל לא כותב תנועות ארוכות.",
     "trained": False, "free_space": False,
     "metrics": {"per": .37, "consonant_per": .22, "ħ": .87, "ʕ": .85, "tˤ": .66, "sˤ": .72, "dˤ": .54,
                 "long_vowels": 0.0},
     "speed": {"gpu": .29, "cpu": 1.8}, "fps": 25},
]


def availability(model_id: str) -> tuple[bool, str]:
    """(can this server load it, why not). A Hub id counts as loadable: it downloads on first use."""
    if model_id.startswith(ONNX_PREFIX) or (Path(model_id) / "powsm_onnx.json").is_file():
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return False, "חסרה החבילה onnxruntime בשרת (requirements.txt)"
        return True, ""
    if model_id.startswith("exports/"):
        return False, "הייצוא ל־ONNX לא נמצא בשרת (scripts/export_powsm_onnx.py)"
    powsm = parse_model_id(model_id)
    if powsm:
        if not (DEPS / "pkgs").exists():
            return False, "חסרות חבילות ההרצה של POWSM בשרת (scripts/setup_zero_shot_deps.sh)"
        if powsm[1] and not Path(powsm[1]).is_file():
            return False, "קובץ המודל לא נמצא בשרת"
        return True, ""
    local = Path(model_id)
    if model_id.startswith(("checkpoints/", "./", "/")) or local.exists():
        if not (local / "config.json").is_file():
            return False, "תיקיית המודל לא נמצאה בשרת"
        return True, ""
    return True, ""


def catalog(active: str) -> list[dict]:
    """Every catalog model, plus the active one when it isn't in the catalog (a MODEL_ID of its own)."""
    rows = [dict(m) for m in CATALOG]
    if not any(m["id"] == active for m in rows):
        rows.insert(0, {"id": active, "name": "מודל מותאם", "family": "", "summary": "הוגדר ב־MODEL_ID; לא נמדד כאן.",
                        "trained": None, "free_space": None, "metrics": None, "speed": None, "fps": None})
    for m in rows:
        m["available"], m["why"] = availability(m["id"])
        m["active"] = m["id"] == active
    return rows


def known(model_id: str) -> bool:
    return any(m["id"] == model_id for m in CATALOG)
