"""The scorer's settings a developer tunes per model from the panel's "מודלים" tab (stored in model_settings).

A model starts from ScorerConfig's defaults and FLAG_BELOW_BY_MODEL (scorer.py). A stored row overrides some of them:
  {"flag_below": 45, "gop.leniency": 0.4, "flag_below_sound": {"ħ": 30, "ʕ": null}}
`null` for a sound means "the general threshold", which also clears a built-in one (the base model's ħ at 20).
Settings belong to the model they were tuned on: a threshold means something else under another model's scores.
"""
from __future__ import annotations

from dataclasses import replace

from .align import POOLS
from .scorer import ScorerConfig, scorer_config

CREATE_MODEL_SETTINGS = ("create table public.model_settings (model text primary key, "
                         "settings jsonb not null default '{}'::jsonb, updated_by text not null default '', "
                         "updated_at timestamptz not null default now()); "
                         "alter table public.model_settings enable row level security;")

# key: a ScorerConfig field, or gop.<GopConfig field>. kind: number | int | choice | window (an int, or None: wide)
FIELDS: list[dict] = [
    {"key": "flag_below", "group": "flag", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "סף סימון כללי",
     "help": "הגה שקיבל ציון מתחת לסף מסומן כטעות ומקבל טיפ. הגים עם סף משלהם (למטה) משתמשים בו במקום זה."},
    {"key": "issue_below", "group": "flag", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "סף \"לא מדויק\"",
     "help": "הגה מתחת לסף נרשם ברשימת ההגים כלא מדויק, גם בלי טיפ. גם תנועה ארוכה שנאמרה קצרה נמדדת מולו."},
    {"key": "max_issues", "group": "flag", "kind": "int", "min": 1, "max": 20, "step": 1,
     "label": "הכי הרבה הגים ברשימה", "help": "כמה הגים לא מדויקים מוצגים לתלמיד לכל היותר בכל הקלטה."},
    {"key": "unrelated_ratio", "group": "unrelated", "kind": "number", "min": 0.1, "max": 1, "step": 0.05,
     "label": "סף \"משפט אחר\"",
     "help": "כמה רחוק מה שנשמע מהמשפט הצפוי עד שהמערכת אומרת שנאמר משפט אחר. גבוה יותר: סלחני יותר."},
    {"key": "unrelated_veto_share", "group": "unrelated", "kind": "number", "min": 0, "max": 1, "step": 0.05,
     "label": "וטו: חלק העיצורים שנמצאו",
     "help": "אם לפחות החלק הזה של העיצורים נמצא בבירור ביישור, המשפט לא ייחשב אחר גם כשהמרחק גדול."},
    {"key": "unrelated_veto_mean", "group": "unrelated", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "וטו: ציון ממוצע מינימלי", "help": "וטו \"משפט אחר\" חל רק כשהציון הממוצע של ההגים לפחות כזה."},
    {"key": "near_vowel_floor", "group": "floors", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "תנועה שנשמעה כשכנתה",
     "help": "ציון מינימום לתנועה שנשמעה כתנועה קרובה (i/e, u/o, קצרה/ארוכה): הבדל שלא כדאי להעניש עליו."},
    {"key": "ayin_deleted_floor", "group": "floors", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "ע שלא נשמעה",
     "help": "ציון מינימום ל־ע שהמודל לא שמע בכלל: המודל המקורי מפיל לעתים ע שנאמרה נכון."},
    {"key": "onset_transient_floor", "group": "floors", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "עיצור ראשון מול רעש פתיחה", "help": "ציון מינימום לעיצור ראשון שהפסיד לקפיצה של פריים אחד בתחילת ההקלטה."},
    {"key": "ref_noise_floor", "group": "floors", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "הגה שהמודל לא מודד במשפט",
     "help": "אם הקול הילידי (TTS) קיבל על הגה ציון מתחת לזה, ההגה לא נמדד במשפט הזה והתלמיד מקבל עליו ציון מלא."},
    {"key": "ref_min_mean", "group": "floors", "kind": "number", "min": 0, "max": 100, "step": 1,
     "label": "אמינות הקול הילידי",
     "help": "קול ילידי שהממוצע שלו מתחת לזה לא משמש בסיס להשוואה."},
    {"key": "word_min_weight", "group": "floors", "kind": "number", "min": 0, "max": 1, "step": 0.05,
     "label": "משקל ההגה החלש במילה", "help": "כמה הגה אחד חלש מוריד את ציון המילה כולה (0: לא משפיע, 1: קובע לבד)."},
    {"key": "gop.leniency", "group": "gop", "kind": "number", "min": 0, "max": 1, "step": 0.05,
     "label": "רכות הציון",
     "help": "כמה הציון סלחני כשהמודל מתלבט בין ההגה הנכון לאחר. 0: ליניארי וקשוח; גבוה יותר: מתחרה כפול עדיין מקבל ציון סביר."},
    {"key": "gop.confident_peak", "group": "gop", "kind": "number", "min": 0.05, "max": 0.95, "step": 0.05,
     "label": "ביטחון מספיק", "help": "מאיזו הסתברות של ההגה הנכון השיא כבר לא מוריד את הציון."},
    {"key": "gop.ratio_pool", "group": "gop", "kind": "choice", "choices": list(POOLS),
     "label": "איחוד הפריימים", "help": "איך מצרפים את הפריימים של הגה לציון אחד (docs/gop-pooling.md). gated: רק הפריימים הרועשים."},
    {"key": "gop.energy_gate", "group": "gop", "kind": "number", "min": 0, "max": 1, "step": 0.05,
     "label": "סף עוצמה (gated)", "help": "פריימים שקטים מהחלק הזה של הפריים החזק ביותר לא נספרים."},
    {"key": "gop.vowel_window", "group": "gop", "kind": "window", "min": 0, "max": 10,
     "label": "חלון התנועה", "help": "מכמה פריימים סביב תנועה קוראים את המתחרים שלה. רחב: כמו עיצורים."},
    {"key": "gop.consonant_window", "group": "gop", "kind": "window", "min": 1, "max": 12,
     "label": "חלון העיצור", "help": "כמה פריימים של שקט סביב עיצור נכללים בקריאה שלו. רחב: עד ההגה הבא."},
]
GROUPS = {"flag": "סימון טעויות", "unrelated": "זיהוי \"משפט אחר\"", "floors": "רצפות והקלות",
          "gop": "ניקוד ההגה (מתקדם)"}
FIELD = {f["key"]: f for f in FIELDS}

# Per-sound thresholds: the sounds tokens stand for (TargetToken.sound), grouped as a Hebrew speaker meets them.
SOUND_GROUPS = [
    ("גרוניים", [("ħ", "ח (ح)"), ("ʕ", "ע (ع)"), ("h", "ה (ه)"), ("ʔ", "א (ء)"), ("x", "ח׳ (خ)"), ("ɣ", "ע׳ (غ)")]),
    ("מודגשים", [("tˤ", "ט (ط)"), ("sˤ", "צ (ص)"), ("dˤ", "ד׳ (ض)"), ("ZH", "ט׳ (ظ)")]),
    ("אותיות עם כמה הגיות", [("Q", "ק (ق)"), ("K", "כ (ك)"), ("J", "ג׳ (ج)"), ("TH", "ת׳ (ث)"), ("DH", "ד״ (ذ)")]),
    ("עיצורים נוספים", [("r", "ר (ر)"), ("l", "ל (ل)"), ("ʃ", "ש (ش)"), ("s", "ס (س)"), ("z", "ז (ز)"), ("t", "ת (ت)"),
                        ("d", "ד (د)"), ("k", "כ (ك)"), ("b", "ב (ب)"), ("f", "פ (ف)"), ("m", "מ (م)"), ("n", "נ (ن)"),
                        ("w", "ו (و)"), ("j", "י (ي)")]),
]
SOUNDS = {s for _, group in SOUND_GROUPS for s, _ in group}


def _get(cfg: ScorerConfig, key: str):
    return getattr(cfg.gop, key[4:]) if key.startswith("gop.") else getattr(cfg, key)


def defaults(model_id: str) -> dict:
    """What a model runs with before any stored setting: the fields, and the sounds with a built-in threshold."""
    cfg = scorer_config(model_id)
    return {"fields": {f["key"]: _get(cfg, f["key"]) for f in FIELDS}, "sounds": dict(cfg.flag_below_sound)}


def clean(raw: dict) -> dict:
    """A stored or submitted settings object, checked field by field. Raises ValueError (in Hebrew) on a bad one."""
    if not isinstance(raw, dict):
        raise ValueError("ההגדרות צריכות להיות אובייקט")
    out: dict = {}
    for key, value in raw.items():
        if key == "flag_below_sound":
            if not isinstance(value, dict):
                raise ValueError("ספי ההגים צריכים להיות אובייקט")
            sounds = {}
            for sound, t in value.items():
                if sound not in SOUNDS:
                    raise ValueError(f"הגה לא מוכר: {sound}")
                if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 100):
                    raise ValueError(f"סף ל־{sound} צריך להיות בין 0 ל־100")
                sounds[sound] = None if t is None else float(t)
            out[key] = sounds
            continue
        f = FIELD.get(key)
        if f is None:
            raise ValueError(f"הגדרה לא מוכרת: {key}")
        kind = f["kind"]
        if kind == "choice":
            if value not in f["choices"]:
                raise ValueError(f"{f['label']}: ערך לא מוכר")
        elif kind == "window":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not f["min"] <= value <= f["max"]):
                raise ValueError(f"{f['label']}: רחב, או מספר פריימים בין {f['min']} ל־{f['max']}")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not f["min"] <= value <= f["max"]:
                raise ValueError(f"{f['label']}: ערך בין {f['min']} ל־{f['max']}")
            if kind == "int" and int(value) != value:
                raise ValueError(f"{f['label']}: מספר שלם")
            value = int(value) if kind == "int" else float(value)
        out[key] = value
    return out


def build(model_id: str, settings: dict | None) -> ScorerConfig:
    """The model's ScorerConfig with its stored settings applied."""
    cfg = scorer_config(model_id)
    settings = clean(settings or {})
    gop = {k[4:]: v for k, v in settings.items() if k.startswith("gop.")}
    top = {k: v for k, v in settings.items() if not k.startswith("gop.") and k != "flag_below_sound"}
    sounds = dict(cfg.flag_below_sound)
    for sound, t in settings.get("flag_below_sound", {}).items():
        if t is None:
            sounds.pop(sound, None)
        else:
            sounds[sound] = t
    return replace(cfg, **top, flag_below_sound=sounds, gop=replace(cfg.gop, **gop))


def describe(model_id: str, settings: dict | None, sound_counts: dict[str, int]) -> dict:
    """Everything the tab draws: each field with its default and value, each sound with its threshold."""
    base, cfg = defaults(model_id), build(model_id, settings)
    stored = clean(settings or {})
    fields = [{**f, "group_label": GROUPS[f["group"]], "default": base["fields"][f["key"]], "value": _get(cfg, f["key"]),
               "changed": f["key"] in stored} for f in FIELDS]
    stored_sounds = stored.get("flag_below_sound", {})
    sounds = [{"group": name, "sounds": [{"sound": s, "label": label, "count": sound_counts.get(s, 0),
                                          "default": base["sounds"].get(s), "value": cfg.flag_below_sound.get(s),
                                          "changed": s in stored_sounds} for s, label in group]}
              for name, group in SOUND_GROUPS]
    return {"fields": fields, "sounds": sounds, "general": cfg.flag_below}
