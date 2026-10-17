"""יאללה נדבר: short spoken conversations with an AI tutor, on scenario cards, one for each lesson of the course.

The tutor writes; the student talks. The page turns the student's speech into text (the browser's own recognition)
and sends the text; the tutor's answer comes back as text too, so the student sees what the tutor understood.
The students do not read Arabic script, so what the page shows is Arabic in Hebrew letters as the course writes it:
the tutor's message, and the student's own sentence as the tutor read it. The Arabic script is kept for the teacher.

Each scenario belongs to a lesson (SCENARIOS) and plays out that lesson's theme: its story, its words and its grammar.
The tutor speaks with the words of that lesson and of the lessons before it (`vocabulary`); the page shows each word
of the tutor's message with its Hebrew translation, and marks a word the student has not learnt yet (`is_known`).
A sentence of the student's that is wrong in grammar or wording gets a correction, explained in Hebrew.
A conversation lasts MAX_TURNS sentences of the student's and ends with a short summary in Hebrew.

The model is Claude Haiku (DEFAULT_MODEL, or TUTOR_MODEL), called with the server's key; the browser never sees it. A conversation lives in
this process's memory while it runs (a restart ends it) and is kept, turn by turn, in the Supabase table
`tutor_chats` (CREATE_TUTOR_CHATS), so the teacher can read what was said and how the tutor answered.

Env:  ANTHROPIC_API_KEY (or CLAUDE_API_KEY)  required; without it the game is not offered
      Access is granted per account in the management panel; an open service remains open.
      TUTOR_MODEL  optional: the Claude model the tutor runs on (default DEFAULT_MODEL), e.g. claude-sonnet-5-5
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from .phrases import CATEGORIES, LESSONS, PHRASES
from .workbook import LEX

log = logging.getLogger("ihki.tutor")
DEFAULT_MODEL = "claude-haiku-5-5"
NO_FIX = re.compile(r"אין (?:כאן )?(?:צורך|תיקון|טעות)|(?:המשפט|הדוגמה|התשובה) (?:שלך |שלכם )?נכונ")   # a correction whose note says there is none
MAX_TURNS = 10   # the student's sentences in one conversation
DAILY_LIMIT = 5   # conversations per signed-in account per day; developers are exempt in main.py
MAX_TEXT = 300   # characters of one sentence of the student's
TTL = 2 * 3600   # seconds a conversation nobody continues is kept in memory
MAX_LIVE = 2000   # conversations in memory at once
CREATE_TUTOR_CHATS = ("create table public.tutor_chats (id uuid primary key, user_id uuid, email text not null default '', "
                      "scenario text not null, gender text not null default '', messages jsonb not null default '[]'::jsonb, "
                      "turns int not null default 0, finished boolean not null default false, model text not null default '', "
                      "input_tokens int not null default 0, output_tokens int not null default 0, "
                      "created_at timestamptz not null default now(), updated_at timestamptz not null default now()); "
                      "alter table public.tutor_chats enable row level security;")

# A scenario for each lesson the path shows: who the tutor plays (`who`, `ar`: the name on the chat's avatar, `she`:
# the character is a woman), the card (`title`, `desc`), and for the model the conversation's aim (`goal`) and the
# lesson's grammar to bring in (`focus`). `story`: the lesson's story or dialogue, the model's example of its words.
SCENARIOS = [
    {"id": "l1", "lesson": "1", "who": "ראמי", "ar": "رامي", "she": False, "story": "l1-say",
     "title": "מי, איפה ולמה?", "desc": "ראמי שואל אתכם מה עשיתם אתמול: מי, איפה, מתי ולמה",
     "goal": "a friend asks the student simple questions about yesterday: who sat in the car, what they ate, who "
             "opened the door, where their friend lives, and asks why and when",
     "focus": "the question words مين، وين، ليش، ايمتى and the past tense of سأل، فتح، اكل، قعد"},
    {"id": "l2", "lesson": "2", "who": "ח'ליל", "ar": "خليل", "she": False, "story": "l2-talk",
     "title": "היכרות", "desc": "ח'ליל פוגש אתכם בפעם הראשונה: שם, גיל, מאיפה אתם, משפחה ועבודה",
     "goal": "Khalil, from Tur'an in the north, now living in Jerusalem, meets the student for the first time and "
             "gets to know them: their name, how they are, their age, where they are from and live, whether they are "
             "married and have children, and their work",
     "focus": "greetings and the possessive endings (اسمي، اسمك، بيتي)"},
    {"id": "l3", "lesson": "3", "who": "רובה", "ar": "روبا", "she": True, "story": "l3-story",
     "title": "השכנה רובה", "desc": "רובה עברה לשכונה שלכם: מספר הבית, הטלפון, הגיל והמשפחה",
     "goal": "Ruba, eighteen, has just moved to the student's neighbourhood. She asks for their house number and "
             "phone number, their age, and how many brothers and sisters they have, and tells about her own family",
     "focus": "numbers (house numbers, phone numbers, ages) and the past tense"},
    {"id": "l4", "lesson": "4", "who": "סמיר", "ar": "سمير", "she": False, "story": "l4-story",
     "title": "השכן החדש", "desc": "באתם עם עוגה לשכן החדש סמיר. הוא מציע קפה או תה",
     "goal": "the student visits Samir Atallah, the new neighbour, bringing him a cake. Samir welcomes them, offers "
             "coffee or tea (with sugar or without), asks whether they are tired or hungry, and they talk about "
             "their families",
     "focus": "بدّي (I want), بلا، كل، and the past tense"},
    {"id": "l5", "lesson": "5", "who": "אבו מחמד", "ar": "ابو محمد", "she": False, "story": "l5-story",
     "title": "בשוק", "desc": "קונים ירקות ופירות אצל אבו מחמד: מחיר, קילו וחצי",
     "goal": "Abu Mohammad sells vegetables and fruit in the market. The student buys: he asks what they want, "
             "tells prices in shekels per kilo, and they bargain a little",
     "focus": "prices and quantities (كيلو، نصّ، شيكل) and the negation ما"},
    {"id": "l6", "lesson": "6", "who": "קפטן איוב", "ar": "ايّوب", "she": False, "story": "l6-story",
     "title": "במונית של קפטן איוב", "desc": "נוסעים במונית של קפטן איוב, והוא שואל מה תעשו מחר",
     "goal": "Captain Ayyub, a talkative taxi driver, drives the student. He asks where they are going, what they "
             "will do tomorrow, whether they are going to a party, and talks about himself",
     "focus": "the future tense (بروح، باكل) next to the past"},
    {"id": "l7", "lesson": "7", "who": "הדודה אם סמיר", "ar": "امّ سمير", "she": True, "story": "l7-say",
     "title": "ביקור אצל הדודה", "desc": "הדודה אם סמיר מארחת אתכם ושואלת על הילדים, האחים והמכוניות",
     "goal": "the student visits their aunt Umm Samir. She serves maqluba and asks how many children, brothers, "
             "sisters and cars they have, and how old everyone is",
     "focus": "masculine and feminine, the dual (ولدين، بنتين) and numbers with nouns"},
    {"id": "l8", "lesson": "8", "who": "סוהא", "ar": "سهى", "she": True, "story": "l8-story",
     "title": "בחנות הבגדים", "desc": "סוהא המוכרת עוזרת לכם לבחור מכנסיים ושמלה: איזה צבע וכמה זה עולה",
     "goal": "Suha works in a clothes shop. She asks what the student is looking for, which colour they like, "
             "and tells how much each item costs; they ask how many there are",
     "focus": "colours, قدّيش (how much) and كم (how many)"},
    {"id": "l9", "lesson": "9", "who": "אבו נדאל", "ar": "ابو نضال", "she": False, "story": "l9-story",
     "title": "קפה אצל אבו נדאל", "desc": "אבו נדאל הבדואי מארח אתכם לקפה ושואל מה תרצו לעשות בשבוע הבא",
     "goal": "Abu Nidal, a Bedouin near Arad, welcomes the student, serves coffee (bitter, without sugar or milk), "
             "asks about their family and work, and what they want to do next week",
     "focus": "the future with بدّ (بدّي، بدّك) and hospitality phrases"},
    {"id": "l10", "lesson": "10", "who": "הדוד סעיד", "ar": "سعيد", "she": False, "story": "l10-story",
     "title": "הדוד סעיד בא לבקר", "desc": "הדוד סעיד הגיע לבקר. משווים: מה יותר גדול, יותר יפה, הכי טוב",
     "goal": "uncle Said has come to visit. He asks the student about their life, football, the beach and the "
             "train, and they compare things: which is bigger, nicer, the best",
     "focus": "the comparative and superlative (أكبر، أحلى، أحسن) and هاد، هاي، هدول"},
    {"id": "l2.1", "lesson": "2.1", "who": "אבו חסן", "ar": "ابو حسن", "she": False, "story": "l2.1-story",
     "title": "במכולת של אבו חסן", "desc": "אורחים באים היום: קונים במכולת של אבו חסן לפי הרשימה",
     "goal": "Abu Hassan runs the village grocery. Guests are coming to the student today; they buy what is on "
             "their list. He asks what they need, who is coming and what they will cook",
     "focus": "the future with رح and must with لازم"},
    {"id": "l2.2", "lesson": "2.2", "who": "עלי", "ar": "علي", "she": False, "story": "l2.2-story",
     "title": "בדוכן הפלאפל", "desc": "מזמינים מנת פלאפל אצל עלי, ומספרים מה אתם עושים בדרך כלל",
     "goal": "Ali owns a falafel restaurant. He takes the student's order (which salads and sauces in the pita) and "
             "chats about what they usually do on each day of the week",
     "focus": "the present tense with بـ (باكل، بتروح) and the days of the week"},
    {"id": "l2.3", "lesson": "2.3", "who": "אבו ג'מיל", "ar": "ابو جميل", "she": False, "story": "l2.3-talk",
     "title": "סליחה, מה השעה?", "desc": "אבו ג'מיל ברחוב שואל מה השעה, ומספר על יום ההולדת של הערב",
     "goal": "Abu Jamil stops the student in the street to ask the time, because his phone is broken. They talk "
             "about the time of tonight's birthday party and how to get there",
     "focus": "telling the time (ربع، ثلث، نصّ) and form II verbs (فعّل)"},
    {"id": "l2.4", "lesson": "2.4", "who": "שאדי", "ar": "شادي", "she": False, "story": "l2.4-talk2",
     "title": "שיחת טלפון", "desc": "שאדי מתקשר: קובעים להיפגש מחר, באיזו שעה ואיפה",
     "goal": "Shadi phones the student. He asks how they are and arranges to meet tomorrow: at what time, where, "
             "and asks for a phone number",
     "focus": "phone phrases (ألو، مين معي) and the relative words اللي، انّو"},
    {"id": "l2.5", "lesson": "2.5", "who": "אבו ויסאם", "ar": "ابو وسام", "she": False, "story": "l2.5-story",
     "title": "במוסך", "desc": "האוטו עושה רעש: אבו ויסאם במוסך שואל מה קרה",
     "goal": "Abu Wisam owns a garage. The student's car is making a noise and the engine light is red. He asks "
             "what happened and when, checks the oil, and tells them how long it will take",
     "focus": "صار (happened, became) and verbs like جاب (brought)"},
    {"id": "l2.6", "lesson": "2.6", "who": "פואד", "ar": "فؤاد", "she": False, "story": "l2.6-story",
     "title": "הכד העתיק", "desc": "מצאתם כד עתיק. מר פואד רוצה לקנות אותו: יקר או זול?",
     "goal": "the student found an old jar and brings it to Mr Fuad Arafat, who buys antiques. He asks where they "
             "found it, and they bargain over the price in dinars: expensive or cheap",
     "focus": "form III verbs (ساعد، لاقى) and the object suffix after a vowel"},
    {"id": "l2.7", "lesson": "2.7", "who": "יוסף", "ar": "يوسف", "she": False, "story": "l2.7-story",
     "title": "בתחנת האוטובוס", "desc": "התחנה מלאה. יוסף מחכה לקו לטבריה ונותן לכם עצות",
     "goal": "Yusuf is waiting at a crowded bus stop at the Furaydis junction. He talks with the student about "
             "which line goes to Tiberias and when it comes, and tells them what to do",
     "focus": "the imperative (استنّى، تعال، قول) next to the past, present and future"},
    {"id": "l2.8", "lesson": "2.8", "who": "ד\"ר לילא", "ar": "ليلى", "she": True, "story": "l2.8-story",
     "title": "אצל הרופאה", "desc": "הרופאה לילא שואלת מה כואב ומה אתם עושים עכשיו",
     "goal": "Dr Layla, a doctor, asks the student what hurts, since when, and what they are doing for it right now, "
             "and gives advice (rest, aspirin, health)",
     "focus": "عم for an action happening now and doubled verbs (حبّ، ضلّ)"},
    {"id": "l2.9", "lesson": "2.9", "who": "המוכתר", "ar": "المختار", "she": False, "story": "l2.9-story",
     "title": "המוכתר של הכפר", "desc": "המוכתר פוגש אתכם בכפר: לאן הולכים, מאיפה באים ומה אתם חושבים",
     "goal": "the village mukhtar meets the student on the road and asks where they are going and coming from, "
             "whether they live here, and tells the story of the father, the son and the donkey",
     "focus": "the active participle (رايح، جاي، ساكن، عارف)"},
]
BY_ID = {s["id"]: s for s in SCENARIOS}
READY = [l["id"] for l in LESSONS if not l.get("soon")]
LESSON_OF = {c["id"]: c["lesson"] for c in CATEGORIES}
LEARN = {c["id"] for c in CATEGORIES if c.get("learn")}
TASHKEEL = re.compile(r"[ً-ْٰـ]")
PUNCT = "،؟!.,:;\"'()«»…-"
ARABIC = re.compile(r"[ء-ي]")
PREFIXES = ("وال", "بال", "فال", "لل", "ال", "و", "ب", "ف", "ل", "ع", "م")
SUFFIXES = ("كم", "هم", "ها", "نا", "ني", "تي", "ين", "ات", "ي", "ك", "ه", "و", "ت", "ة")


def norm(word: str) -> str:
    """An Arabic word as it is compared: no tag, vowel signs or punctuation; one alef; ى as ي and ة as ه."""
    w = TASHKEEL.sub("", word.split("#")[0]).strip(PUNCT)
    return w.translate(str.maketrans("أإآٱى", "ااااي")).replace("ة", "ه")


TR_OF = {norm(k): v[0] for k, v in LEX.items()}   # a word's reading in Hebrew letters, as the course writes it


@functools.lru_cache(maxsize=None)
def vocabulary(lesson: str) -> tuple[list[tuple[str, str, str]], frozenset[str]]:
    """What a student knows by the end of this lesson: the new words of it and of the lessons before it, as
    (Arabic, Hebrew letters, Hebrew), and every word form their sentences used (normalised, see `norm`)."""
    upto = set(READY[:READY.index(lesson) + 1])
    learnt, forms = [], set()
    for p in PHRASES:
        if LESSON_OF.get(p["cat"]) not in upto:
            continue
        if p["cat"] in LEARN:
            learnt.append((p["variants"][0][0], p["tr"], p["he"]))
        for ar, _ in p["variants"]:
            forms.update(n for n in map(norm, ar.split()) if n)
    return learnt, frozenset(forms)


def is_known(word: str, forms: frozenset[str]) -> bool:
    """Whether the student has met this word: as it is, or without a prefix (و، ال، بـ...) or an ending (ي، ك، ها...)
    that they learnt as such. Lenient on purpose: a word marked new must be one the lessons never had."""
    w = norm(word)
    if not w or w.isdigit() or not re.search(r"[ء-ي]", w):
        return True
    stems = {w} | {w[len(p):] for p in PREFIXES if w.startswith(p) and len(w) - len(p) >= 2}
    stems |= {s[:-len(x)] for s in list(stems) for x in SUFFIXES if s.endswith(x) and len(s) - len(x) >= 2}
    return any(s in forms or s + "ه" in forms for s in stems)


def _lines(cat: str) -> list[str]:
    """A topic of the course, line by line (Arabic | Hebrew letters | Hebrew): the model's example of the lesson."""
    return [f"{p['variants'][0][0]} | {p['tr']} | {p['he']}" for p in PHRASES if p["cat"] == cat]


# The tutor's every answer, as JSON (structured output). heard_tr: the student's sentence as the tutor read it, in
# Hebrew letters; words: the tutor's message word by word; fix_*: the student's last sentence said right, empty when
# it was right; summary: only in the last answer.
WORD_SCHEMA = {
    "type": "object",
    "properties": {"ar": {"type": "string"}, "tr": {"type": "string"}, "he": {"type": "string"},
                   "name": {"type": "boolean"}},
    "required": ["ar", "tr", "he", "name"],
    "additionalProperties": False,
}
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "heard_tr": {"type": "string"},
        "words": {"type": "array", "items": WORD_SCHEMA},
        "he": {"type": "string"},
        "fix_ar": {"type": "string"}, "fix_tr": {"type": "string"}, "fix_note": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["heard_tr", "words", "he", "fix_ar", "fix_tr", "fix_note", "summary"],
    "additionalProperties": False,
}
OPEN = "(The student has opened the chat. Start the conversation: greet them and begin the scenario.)"


def system_prompt(sc: dict, gender: str) -> str:
    """The tutor's instructions: the character, the scenario, the words it may use, the answer's fields."""
    student = "a woman: speak to her in the feminine (حالِك، اسمِك، انتِ، بتشتغلي)" if gender == "f" else \
        "a man: speak to him in the masculine (حالَك، اسمَك، انتَ، بتشتغل)"
    who = f"{sc['ar']}, a Palestinian {'woman' if sc['she'] else 'man'} (speak of yourself in the " \
          f"{'feminine' if sc['she'] else 'masculine'})"
    learnt, forms = vocabulary(sc["lesson"])
    words = "\n".join(f"{a} | {t} | {h}" for a, t, h in learnt)
    lesson = next(l["name"] for l in LESSONS if l["id"] == sc["lesson"])
    return f"""You play {who} in a short conversation with a Hebrew-speaking beginner who is learning spoken Palestinian
Arabic. The student is {student}.

The scenario: {sc["goal"]}. Ask one thing at a time, answer the student's questions in your character, and keep
the conversation going. Read each message to its end before you answer: never ask for something the student has
already told you. If the student goes off the topic, answer in one short sentence and bring the talk back.

The student has studied up to {lesson} of the course. This lesson's grammar, to bring in naturally: {sc["focus"]}.
Speak ONLY with the words the student knows: the word list and the word forms below (with their usual prefixes and
endings: و، ال، بـ، the possessive and verb endings). Prefer the words of this lesson's story. Use a word outside the
lists only when there is no natural way to say it with known words, and then keep it simple.

The student cannot read Arabic script: they read Arabic only in Hebrew letters. They mostly speak, and the browser's
speech recognition writes down what it heard in Arabic script, so a message may be misspelled, missing words or partly
wrong; a student who types writes the Arabic in Hebrew letters. Answer what you understood. If you understood nothing,
say so kindly and ask again in simpler words. Never use Modern Standard Arabic: speak the spoken Palestinian dialect
of the course (شو، وين، كيف، قدّيش، بدّي، كتير، هلّق). Keep each message to one or two short sentences.

Arabic in Hebrew letters ("Hebrew letters" below) is written exactly as the course writes it, as in the lesson lines
below: pointed Hebrew; ח' for خ, ע' for غ, ג' for ج, ד' for ض, ט' for ظ, ת' for ث; ح is ח, ع is ע, ص is צ, ط is ט,
ق is ק; the shadda ّ after a doubled letter; a hyphen before a pronoun suffix (אִסְמַ-כּ, חַאלֵ-כּ, עִנְד-י).

Every answer has these fields:
- heard_tr: the student's last message exactly as you understood it, in Hebrew letters. Write what they said, mistakes
  and all: do not correct it here (corrections go in fix_*). Empty when there is no student message yet.
- words: your message, word by word in order. For each word: ar (the word in Arabic script, as the course writes it,
  no vowel signs except shadda ّ), tr (the word in Hebrew letters, with the punctuation that follows it, e.g. "חַאלַ-כּ?"),
  he (its meaning here in Hebrew, one to three words), name (true only for a person's or a place's name).
- he: a natural Hebrew translation of your whole message.
- fix_ar, fix_tr, fix_note: only for a clear mistake in the student's last sentence: a wrong verb form, gender or
  person, a wrong word, or Modern Standard Arabic or Hebrew where the dialect has its own word (ثلاثون for تلاتين).
  Then give the sentence as they should have said it (in Arabic script and in Hebrew letters), and in fix_note one
  short, simple line in Hebrew naming exactly what changed (e.g. "בניב אומרים תַלַאתִין ולא ת'לַאת'וּן"). Most
  sentences of a student are fine: then leave all three empty. Leave them empty too when you are not sure, when the
  difference is only spelling or vowels (that came from the speech recognition, not from the student), or when the
  sentence is a correct way to say it even if you would say it differently. Never correct a correct sentence.
- summary: empty, except in your very last message (you will be told when it is). Then write in Hebrew, addressed to
  the student, two or three sentences on what went well and the two or three things most worth practising.
fix_note and summary are in Hebrew, and any Arabic word in them is written in Hebrew letters, never in Arabic script.

Lines of this lesson (Arabic | Hebrew letters | Hebrew):
{chr(10).join(_lines(sc["story"]))}

The words the student has learnt (Arabic | Hebrew letters | Hebrew):
{words}

Every word form the lessons so far have used:
{" ".join(sorted(forms))}

YOUR ROLE NOW: you are {sc["ar"]}. {sc["goal"]}. From your very first message, be this character in this situation
and lead the conversation toward it; every answer of yours moves the scenario on. Speak to the student as
{student.split(":")[0]}."""


class TutorUnavailable(Exception):
    """No key, or the model failed. The message is safe to show the student (Hebrew)."""


class TutorLimit(Exception):
    """The student has started their conversations for today."""


@dataclass
class Chat:
    id: str
    scenario: str
    gender: str
    user_id: str | None
    email: str
    system: str
    messages: list[dict] = field(default_factory=list)   # as the model reads them: user text, assistant JSON
    log: list[dict] = field(default_factory=list)   # as the page and the teacher read them
    turns: int = 0
    done: bool = False
    seen: float = field(default_factory=time.monotonic)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = DEFAULT_MODEL

    def row(self) -> dict:
        """The conversation as the table `tutor_chats` keeps it."""
        return {"id": self.id, "user_id": self.user_id, "email": self.email, "scenario": self.scenario,
                "gender": self.gender, "messages": self.log, "turns": self.turns, "finished": self.done,
                "model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def thinking_for(model: str) -> dict:
    """No thinking for a chat answer: Haiku turns it off with "disabled", Sonnet 5.5 (which refuses that) with
    "between_tools"."""
    return {"type": "between_tools"} if model.startswith("claude-sonnet-5-5") else {"type": "disabled"}


class Tutor:
    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client   # an anthropic.Anthropic
        self.model = model
        self._chats: dict[str, Chat] = {}
        self._started: dict[tuple[str, str], int] = {}   # (student, day): conversations started
        self._lock = threading.Lock()

    def started(self, student: str, day: str, seed=None) -> int:
        """How many conversations the student started on this day. `seed` reads the number kept in Supabase the first
        time it is asked (a restart forgets the count in memory)."""
        key = (student, day)
        with self._lock:
            if key in self._started:
                return self._started[key]
        n = seed() if seed else 0
        with self._lock:
            return self._started.setdefault(key, n)

    def start(self, scenario: str, gender: str, user_id: str | None, email: str, student: str, day: str,
              limit: int | None, seed=None) -> tuple[Chat, dict]:
        """A new conversation and the tutor's opening message (see `message`). Raises TutorLimit past the day's
        `limit` (None: none), and TutorUnavailable when the model fails (the conversation is then not counted)."""
        sc = BY_ID[scenario]
        if limit is not None and self.started(student, day, seed) >= limit:
            raise TutorLimit()
        chat = Chat(id=str(uuid.uuid4()), scenario=scenario, gender=gender, user_id=user_id, email=email,
                    system=system_prompt(sc, gender), model=self.model)
        messages = [{"role": "user", "content": OPEN}]
        data = self._answer(chat, messages)
        opening = self.message(data, sc)
        chat.log.append({"from": "tutor", **opening})
        with self._lock:
            self._started[(student, day)] = self._started.get((student, day), 0) + 1
            self._sweep()
            self._chats[chat.id] = chat
        return chat, opening

    def get(self, chat_id: str, user_id: str | None) -> Chat | None:
        with self._lock:
            chat = self._chats.get(chat_id)
        return chat if chat and chat.user_id == user_id else None

    @staticmethod
    def message(data: dict, sc: dict) -> dict:
        """The tutor's message as the page shows it: {"ar", "tr", "he", "words": [{tr, he, new}]}. A word is new when
        the lessons up to the scenario's never had it (a name never is)."""
        _, forms = vocabulary(sc["lesson"])
        words: list[dict] = []
        for w in data["words"]:
            tr, ar = w["tr"].strip(), w["ar"].strip()
            if not tr:
                continue
            if not re.search(r"[א-ת]", tr):   # punctuation the model gave as a word of its own
                if words:
                    words[-1]["tr"] += tr.strip()
                    words[-1]["ar"] += ar.strip()
                continue
            if ARABIC.search(tr):   # Arabic letters in the Hebrew ones: the course's own reading of the word, if it has it
                tr = TR_OF.get(norm(ar), re.sub(r"[؀-ۿ]+", "", tr).strip() or "…") + tr[len(tr.rstrip(PUNCT + "?")):]
            words.append({"ar": ar, "tr": tr, "he": w["he"].strip(), "new": not w["name"] and not is_known(ar, forms)})
        return {"ar": " ".join(w["ar"] for w in words), "tr": " ".join(w["tr"] for w in words),
                "he": data["he"].strip(), "words": [{k: w[k] for k in ("tr", "he", "new")} for w in words]}

    def turn(self, chat: Chat, text: str) -> dict:
        """The student's sentence, and the tutor's answer to it: {"heard": the sentence as the tutor read it, in Hebrew
        letters, "reply": the tutor's message (see `message`), "fix": {ar, tr, note} or None, "summary": text or None,
        "turn", "turns", "done"}. Raises TutorUnavailable when the model fails; the student's sentence is then not
        counted, and can be said again."""
        if chat.done:
            raise ValueError("the conversation has ended")
        last = chat.turns + 1 >= MAX_TURNS
        messages = chat.messages + [{"role": "user", "content": text}]
        if last:
            messages.append({"role": "system", "content": "This is the student's last sentence. Answer it and say "
                             "goodbye warmly, and write the summary."})
        data = self._answer(chat, messages)
        chat.turns += 1
        chat.done = last
        fix = {"ar": data["fix_ar"].strip(), "tr": data["fix_tr"].strip(), "note": data["fix_note"].strip()}
        if not fix["ar"] or not fix["note"] or NO_FIX.search(fix["note"]) or \
                [norm(w) for w in fix["ar"].split()] == [norm(w) for w in text.split()]:
            fix = None   # no correction, or one that changes nothing: the model is not to correct a correct sentence
        reply = self.message(data, BY_ID[chat.scenario])
        summary = data["summary"].strip() if last else ""
        heard = data["heard_tr"].strip() or text
        chat.log.append({"from": "student", "text": text, "tr": heard, **({"fix": fix} if fix else {})})
        chat.log.append({"from": "tutor", **reply, **({"summary": summary} if summary else {})})
        return {"heard": heard, "reply": reply, "fix": fix, "summary": summary or None, "turn": chat.turns,
                "turns": MAX_TURNS, "done": chat.done}

    def _answer(self, chat: Chat, messages: list[dict]) -> dict:
        """Asks the model and keeps the exchange in the conversation's history (only once it has answered)."""
        data, usage = self._ask(chat.system, messages)
        chat.messages = messages + [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}]
        chat.seen = time.monotonic()
        chat.input_tokens += usage[0]
        chat.output_tokens += usage[1]
        return data

    def _ask(self, system: str, messages: list[dict]) -> tuple[dict, tuple[int, int]]:
        import anthropic
        try:
            res = self.client.messages.create(
                model=self.model, max_tokens=4000, system=system, messages=messages,
                thinking=thinking_for(self.model),
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
                cache_control={"type": "ephemeral"},
            )
        except anthropic.RateLimitError as e:
            log.warning("tutor rate limited: %s", e)
            raise TutorUnavailable("המורה עמוס כרגע. נסו שוב בעוד דקה.") from e
        except anthropic.AuthenticationError as e:
            log.error("tutor: the API key was refused: %s", e)
            raise TutorUnavailable("המורה לא זמין כרגע (מפתח ה-API לא תקין).") from e
        except anthropic.APIStatusError as e:
            log.warning("tutor: the API answered %s: %s", e.status_code, e.message)
            raise TutorUnavailable("המורה לא זמין כרגע. נסו שוב.") from e
        except anthropic.APIConnectionError as e:
            log.warning("tutor: no connection: %s", e)
            raise TutorUnavailable("אין חיבור למורה כרגע. נסו שוב.") from e
        if res.stop_reason == "refusal":
            log.warning("tutor: the model declined (%s)", res.stop_details)
            raise TutorUnavailable("המורה לא יכול לענות על זה. נסו לומר משהו אחר.")
        if res.stop_reason == "max_tokens":
            raise TutorUnavailable("התשובה נקטעה. נסו שוב.")
        text = next((b.text for b in res.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError as e:
            log.warning("tutor: an answer that is not JSON: %r", text[:300])
            raise TutorUnavailable("המורה לא הצליח לענות. נסו שוב.") from e
        return data, (res.usage.input_tokens + (res.usage.cache_read_input_tokens or 0)
                      + (res.usage.cache_creation_input_tokens or 0), res.usage.output_tokens)

    def _sweep(self) -> None:
        """Drops conversations nobody continued for TTL, and the oldest past MAX_LIVE. Called with the lock held."""
        now = time.monotonic()
        for k in [k for k, c in self._chats.items() if now - c.seen > TTL]:
            del self._chats[k]
        while len(self._chats) >= MAX_LIVE:
            del self._chats[min(self._chats, key=lambda k: self._chats[k].seen)]
        if len(self._started) > 10 * MAX_LIVE:
            self._started.clear()


def load_tutor() -> Tutor | None:
    key = (os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("CLAUDE_API_KEY", "")).strip()
    if not key:
        log.info("no ANTHROPIC_API_KEY: the tutor game (יאללה נדבר) is off")
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("ANTHROPIC_API_KEY is set but the anthropic package is not installed: the tutor game is off")
        return None
    model = os.environ.get("TUTOR_MODEL", "").strip() or DEFAULT_MODEL
    log.info("the tutor game (יאללה נדבר) runs on %s", model)
    return Tutor(anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=2), model)


def tutor_open() -> bool:
    """Whether every student gets the game, and not only the teachers."""
    return os.environ.get("TUTOR_OPEN", "").strip().lower() in ("1", "true", "yes", "on")
