"""A photographed math problem, solved for the math path: hints first, the full solution on request.

The student photographs a problem; Claude reads it, solves it, and answers in a fixed JSON shape (PHOTO_SCHEMA):
the problem as it read it (so the student can check the reading), the solution as steps, each a hint that does
not give the step away and the step itself, the final answer, and the path's skill the problem practises. The page
shows the hints one by one, and the steps and the answer only when the student asks for them.

Runs on the server's Anthropic key (the tutor's, ANTHROPIC_API_KEY or the panel's), on MATH_MODEL (default
DEFAULT_MODEL). The photo is sent to the API and not kept.
"""
from __future__ import annotations

import base64
import json
import logging
import os

log = logging.getLogger("ihki.math_photo")

DEFAULT_MODEL = "claude-opus-5-5"
DAILY_PHOTOS = 20   # photos per signed-in account per day; developers are exempt in main.py
MAX_PHOTO = 5_000_000   # bytes; the page sends a resized JPEG of a few hundred KB
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp")


class PhotoUnavailable(Exception):
    """The photo could not be solved; the message is for the student (Hebrew)."""


def photo_schema(skill_ids: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["readable", "problem", "steps", "answer", "skill", "note"],
            "properties": {
                "readable": {"type": "boolean"},
                "problem": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                     "required": ["hint", "work"],
                                                     "properties": {"hint": {"type": "string"}, "work": {"type": "string"}}}},
                "answer": {"type": "string"},
                "skill": {"type": "string", "enum": [*skill_ids, ""]},
                "note": {"type": "string"}}}


def system_prompt(skills: list[dict]) -> str:
    listed = "\n".join(f"- {s['id']}: {s['name'].replace('$', '')}" for s in skills)
    return f"""אתה מורה פרטי למתמטיקה לתלמידי כיתה ט׳ בהקבצה של 5 יחידות, בישראל. תלמיד צילם תרגיל ושלח לך את התמונה.

1. קרא את התרגיל מהתמונה בדיוק כפי שהוא כתוב, ורשום אותו ב־problem. אם בתמונה כמה תרגילים, פתור את הראשון וכתוב זאת ב־note.
2. פתור את התרגיל נכון, ובדוק את הפתרון (למשל בהצבה) לפני שאתה עונה.
3. חלק את הפתרון לצעדים קצרים, בסדר שבו תלמיד היה כותב אותם, 2 עד 6 צעדים. לכל צעד:
   - hint: רמז או שאלה מכוונת שעוזרים לתלמיד לעשות את הצעד בעצמו, בלי לגלות את התוצאה שלו.
   - work: הצעד עצמו, עם התוצאה.
4. answer: התשובה הסופית בלבד, קצרה. אם יש פתרון שאינו קביל (למשל מאפס מכנה, או אורך שלילי), כתוב רק את הקבילים.
5. skill: המזהה של המיומנות מהרשימה שהתרגיל מתרגל יותר מכל אחת אחרת, או מחרוזת ריקה אם אף אחת לא מתאימה.
6. אם התמונה לא קריאה, או שאין בה תרגיל מתמטי, readable יהיה false, ו־note יסביר בקצרה לתלמיד מה לצלם מחדש. השאר את שאר השדות ריקים.

כתוב בעברית פשוטה, כמו מורה שמדבר עם תלמיד, ופנה בלשון רבים כמו שאר האפליקציה (למשל "העבירו", "חפשו", "הציבו"). כל ביטוי מתמטי עטוף בסימני דולר, למשל $x² − 4 = 0$, וגם כל מספר בודד בתוך משפט, בעיקר מספר שלילי: "שמכפלתם $−15$". בתוך הדולרים כתוב בתווי יוניקוד: ² ³ √ · − ≤ ≥ ≠ ±, שברים כ־a/b, ובלי פקודות LaTeX.

המיומנויות במסלול:
{listed}"""


class PhotoSolver:
    def __init__(self, client, model: str = DEFAULT_MODEL, skills: list[dict] | None = None):
        self.client, self.model, self.skills = client, model, skills or []

    def solve(self, image: bytes, media_type: str) -> dict:
        import anthropic
        if media_type not in MEDIA_TYPES:
            raise PhotoUnavailable("אפשר לשלוח תמונה מסוג JPEG,‏ PNG או WebP.")
        data = base64.standard_b64encode(image).decode("ascii")
        try:
            res = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                system=system_prompt(self.skills), cache_control={"type": "ephemeral"},
                output_config={"effort": "medium",
                               "format": {"type": "json_schema", "schema": photo_schema([s["id"] for s in self.skills])}},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": "זה התרגיל שצילמתי."}]}],
            )
        except anthropic.RateLimitError as e:
            log.warning("math photo rate limited: %s", e)
            raise PhotoUnavailable("עמוס כרגע. נסו שוב בעוד דקה.") from e
        except anthropic.AuthenticationError as e:
            log.error("math photo: the API key was refused: %s", e)
            raise PhotoUnavailable("פתרון מתמונה לא זמין כרגע (מפתח ה-API לא תקין).") from e
        except anthropic.APIStatusError as e:
            log.warning("math photo: the API answered %s: %s", e.status_code, e.message)
            raise PhotoUnavailable("פתרון מתמונה לא זמין כרגע. נסו שוב.") from e
        except anthropic.APIConnectionError as e:
            log.warning("math photo: no connection: %s", e)
            raise PhotoUnavailable("אין חיבור כרגע. נסו שוב.") from e
        if res.stop_reason == "refusal":
            log.warning("math photo: the model declined (%s)", res.stop_details)
            raise PhotoUnavailable("לא הצלחנו לפתור את התמונה הזו. נסו לצלם תרגיל אחר.")
        if res.stop_reason == "max_tokens":
            raise PhotoUnavailable("הפתרון נקטע. נסו שוב.")
        text = next((b.text for b in res.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except ValueError as e:
            log.warning("math photo: an answer that is not JSON: %r", text[:300])
            raise PhotoUnavailable("לא הצלחנו לפתור את התרגיל. נסו שוב.") from e
        log.info("math photo: %s, %d+%d tokens", res.model, res.usage.input_tokens, res.usage.output_tokens)
        return out


def photo_client(key: str):
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=1)


def math_model() -> str:
    return os.environ.get("MATH_MODEL", "").strip() or DEFAULT_MODEL
