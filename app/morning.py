"""יאללה נדבר's morning conversation: once a day, a talk with a tutor who knows the student. It is built for the
student from what the server already keeps: their recordings (the sentences they find hard), their earlier
conversations with the tutor (the corrections they got, what they told about themselves, the summaries), and the
lesson they are on (a few words of it they have not practised yet, to learn in the talk).

`plan` makes the day's plan out of those rows; `scenario` turns it into a scenario as tutor.system_prompt reads one
(its `goal`, `focus` and the `extra` the morning adds). Nothing here calls a model or the database."""
from __future__ import annotations

from . import tutor
from .phrases import CATEGORIES, LESSONS, PHRASES

ID = "morning"   # the scenario's id: one conversation a day
WEAK_BELOW = 75   # a sentence whose best score is under this is one to practise again
MAX_WEAK = 4
MAX_FIXES = 3
MAX_NEW = 3
MAX_SAID = 25   # the student's own earlier sentences the tutor reads (what they told about themselves)
MAX_SUMMARIES = 3
RECENT_CHATS = 12


def _key(p: dict) -> tuple[str, str]:
    return p["cat"], p.get("key", p["he"])


def _lesson_of_cat() -> dict[str, str]:
    return {c["id"]: c["lesson"] for c in CATEGORIES}


def current_lesson(asked: str | None, attempts: list[dict]) -> str:
    """The lesson the student is on: the one the page says (the class's lesson the student chose), else that of their
    latest recording, else the course's first."""
    ready = [l["id"] for l in LESSONS if not l.get("soon")]
    if asked in ready:
        return asked
    of = _lesson_of_cat()
    for r in attempts:   # newest first
        if of.get(r.get("cat")) in ready:
            return of[r["cat"]]
    return ready[0] if ready else ""


def weak_sentences(attempts: list[dict], lesson: str) -> list[dict]:
    """The sentences of the student's last lesson that they have tried and not yet said well: the lowest best score
    first, then the most tries."""
    upto = {lesson}
    of = _lesson_of_cat()
    best: dict[tuple, int] = {}
    tries: dict[tuple, int] = {}
    for r in attempts:
        k = (r.get("cat"), r.get("phrase"))
        if of.get(k[0]) not in upto or r.get("score") is None:
            continue
        best[k] = max(best.get(k, 0), int(r["score"]))
        tries[k] = tries.get(k, 0) + 1
    by_key = {_key(p): p for p in PHRASES}
    weak = [k for k, b in best.items() if b < WEAK_BELOW and k in by_key]
    weak.sort(key=lambda k: (best[k], -tries[k]))
    return [{"ar": by_key[k]["variants"][0][0], "tr": by_key[k]["tr"], "he": by_key[k]["he"], "best": best[k]}
            for k in weak[:MAX_WEAK]]


def new_words(attempts: list[dict], lesson: str) -> list[dict]:
    """A few words of the lesson's word lists the student has not practised yet; never a later lesson's (none, once
    they have practised them all)."""
    tried = {(r.get("cat"), r.get("phrase")) for r in attempts}
    learn = {c["id"]: c["lesson"] for c in CATEGORIES if c.get("learn")}
    words = [p for p in PHRASES if learn.get(p["cat"]) == lesson and _key(p) not in tried and len(p["tr"].split()) <= 3]
    return [{"ar": p["variants"][0][0], "tr": p["tr"], "he": p["he"]} for p in words[:MAX_NEW]]


def memory(chats: list[dict]) -> dict:
    """What the earlier conversations (newest first: their `messages` as the page shows them) tell the tutor: the
    corrections the student got (each once), their own sentences, and the tutor's summaries."""
    fixes, said, summaries, seen = [], [], [], set()
    for chat in chats[:RECENT_CHATS]:
        for m in reversed(chat.get("messages") or []):
            if not isinstance(m, dict):
                continue
            if m.get("from") == "student":
                fix = m.get("fix") or {}
                note = str(fix.get("note") or "").strip()
                if note and note not in seen and len(fixes) < MAX_FIXES:
                    seen.add(note)
                    fixes.append({"said": str(m.get("tr") or m.get("text") or "").strip(),
                                  "tr": str(fix.get("tr") or "").strip(), "ar": str(fix.get("ar") or "").strip(),
                                  "note": note})
                text = str(m.get("text") or "").strip()
                if text and len(said) < MAX_SAID:
                    said.append(text)
            elif m.get("from") == "tutor" and m.get("summary") and len(summaries) < MAX_SUMMARIES:
                summaries.append(str(m["summary"]).strip())
    return {"fixes": fixes, "said": said, "summaries": summaries}


def plan(attempts: list[dict], chats: list[dict], lesson: str | None = None) -> dict:
    """The day's plan: the lesson, the character (that lesson's scenario), and what the talk practises."""
    at = current_lesson(lesson, attempts)
    sc = next((s for s in tutor.SCENARIOS if s["lesson"] == at), None) or \
        next((s for s in tutor.SCENARIOS if s["lesson"] in {l["id"] for l in LESSONS}), tutor.SCENARIOS[0])
    mem = memory(chats)
    return {"lesson": at, "base": sc["id"], "who": sc["who"], "ar": sc["ar"], "she": bool(sc["she"]),
            "weak": weak_sentences(attempts, at), "fixes": mem["fixes"], "new": new_words(attempts, at),
            "said": mem["said"], "summaries": mem["summaries"], "knows": bool(mem["said"])}


def shown(p: dict) -> dict:
    """The plan as the page shows it on the card (nothing of what was said before, nor the Arabic script)."""
    return {"lesson": p["lesson"], "who": p["who"], "ar": p["ar"], "she": p["she"], "knows": p["knows"],
            "weak": [{"tr": w["tr"], "he": w["he"]} for w in p["weak"]],
            "fixes": [{"tr": f["tr"], "note": f["note"]} for f in p["fixes"]],
            "new": [{"tr": w["tr"], "he": w["he"]} for w in p["new"]]}


def scenario(p: dict) -> dict:
    """The plan as a scenario for tutor.system_prompt: the character of the student's lesson, meeting them for the
    day's talk, with what to practise woven into it."""
    base = tutor.BY_ID[p["base"]]
    she = "she" if p["she"] else "he"
    lines = lambda xs: "\n".join(f"- {x['ar']} | {x['tr']} | {x['he']}" for x in xs) or "- (none)"
    fixes = "\n".join(f"- they said \"{f['said']}\"; right: {f['ar']} | {f['tr']} ({f['note']})" for f in p["fixes"]) or "- (none)"
    said = "\n".join(f"- {s}" for s in p["said"]) or "- (nothing yet: this is your first talk; get to know them)"
    summaries = "\n".join(f"- {s}" for s in p["summaries"]) or "- (none)"
    extra = f"""THIS IS THE STUDENT'S MORNING LESSON WITH YOU: you are their Arabic TEACHER, not their friend. You know them and
you are kind, but demanding: the point is that they speak correctly. They mostly listen and speak (often without looking
at the screen), so everything that matters must be in what you say. Open by greeting them for the morning and, if they
told you something about themselves before (below), ask about it (their family, work, plans); never invent facts about
them. Then, one at a time and woven naturally into the
conversation, practise:
1. These sentences they find hard to say (from their practice): ask questions whose natural answer uses them, and
   when they say one right, praise it in one word.
{lines(p["weak"])}
2. The mistakes they made in earlier talks: give them a chance to say it right this time.
{fixes}
3. These words of their lesson, which they have not practised yet: bring in one at a time and get them to use it.
{lines(p["new"])}
Stay within their last lesson ({p["lesson"]}) and the lessons before it: never use a word of a later lesson.
Do not list the plan to the student; just talk. By the last sentence try to have touched each item.

CORRECT EVERY MISTAKE, OUT LOUD, IN ARABIC: you speak Arabic only (never Hebrew), with the words they have learnt as
far as you can. When the student's sentence has a mistake (a wrong word, verb form, gender or person, a missing word,
Hebrew or Modern Standard Arabic instead of the dialect), your message says so in simple Arabic, says the right sentence,
and asks them to say it again (e.g. "لا. احنا اكلنا شاورما. مرة تانية!"). It does not move on: move on once they said it
right, or after their second try. Also fill fix_ar, fix_tr and fix_note: fix_note is shown on the screen only, one or two
short Hebrew sentences naming the mistake and the rule (e.g. "אמרת אַכַּלְתוּ, אבל זה אתם. על עצמנו אומרים אַכַּלְנַא.").
Spelling and vowels that came from the speech recognition are not mistakes, and a correct sentence is never corrected.

What the student said in your earlier talks (their own words, as the speech recognition wrote them):
{said}

What you told them at the end of earlier talks:
{summaries}"""
    return {"id": ID, "lesson": p["lesson"], "who": p["who"], "ar": p["ar"], "she": p["she"], "story": base["story"],
            "title": "השיחה של הבוקר", "desc": "שיחה יומית עם מורה שמכיר אתכם",
            "goal": f"{p['ar']}, the student's Arabic teacher, gives them their morning speaking lesson: {she} talks with "
                    f"them about their life and their day, makes them practise what they find hard and a few words of "
                    f"their lesson, and corrects every mistake",
            "focus": base["focus"], "extra": extra}
