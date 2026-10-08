"""Day 2 sentences as grammar, so the page can ask for the same sentence negated or with another subject.

A sentence is a subject pronoun, one or two past-tense verbs and fixed words. From each sentence
pattern this module builds every pronoun x {positive, negative} phrase in the format of phrases.py.
Only the forms taught in the course are "listed" (shown in the tab); the rest are reached through
the page's "שלילה" and "הטיית פעלים" buttons.

Negation is ما before the verb (ما اكلت); the -ش suffix is not accepted yet.
Transliterations follow the course material: אֵל for the article, (ל) for its silent ל, ّ for doubling.
"""
from __future__ import annotations

import itertools

# subject pronouns, in the order used by every verb table below
#            key       Hebrew    course tr   (arabic, ipa) readings, main one first
PRONOUNS = [
    ("ana",   "אני",    "אַנַא",    [("انا", "ʔ a n a")]),
    ("inte",  "אתה",    "אִנְתֵ",    [("انت", "ʔ i n t e"), ("انت", "ʔ i n t a")]),
    ("inti",  "את",     "אִנְתִי",   [("انتي", "ʔ i n t i")]),
    ("huwwe", "הוא",    "הוּّ",     [("هوّ", "h u w w e"), ("هو", "h uː")]),
    ("hiyye", "היא",    "הִיֵّ",    [("هيّ", "h i j j e"), ("هي", "h iː")]),
    ("ihna",  "אנחנו",  "אִחְנַא",   [("احنا", "ʔ i ħ n a")]),
    ("intu",  "אתם",    "אִנְתוּ",   [("انتو", "ʔ i n t u")]),
    ("humme", "הם",     "הֹםّ",     [("همّ", "h u m m e"), ("هم", "h o m")]),
]
P = [p[0] for p in PRONOUNS]

# past tense, one row per pronoun (PRONOUNS order): Hebrew, course tr, Arabic, IPA readings
VERBS = {
    "akal": [
        ("אכלתי",  "אַכַּלְת",    "اكلت",  ["ʔ a k a l t"]),
        ("אכלת",   "אַכַּלְת",    "اكلت",  ["ʔ a k a l t"]),
        ("אכלת",   "אַכַּלְתִי",   "اكلتي", ["ʔ a k a l t i"]),
        ("אכל",    "אַכַּל",      "اكل",   ["ʔ a k a l"]),
        ("אכלה",   "אַכְּלַת",    "اكلت",  ["ʔ a k l a t"]),
        ("אכלנו",  "אַכַּלְנַא",   "اكلنا", ["ʔ a k a l n a"]),
        ("אכלתם",  "אַכַּלְתוּ",   "اكلتو", ["ʔ a k a l t u"]),
        ("אכלו",   "אַכְּלוּ",    "اكلو",  ["ʔ a k l u", "ʔ a k a l u"]),
    ],
    "qaad": [
        ("ישבתי",  "קַעַדְת",    "قعدت",  ["ʔ a ʕ a d t", "q a ʕ a d t"]),
        ("ישבת",   "קַעַדְת",    "قعدت",  ["ʔ a ʕ a d t", "q a ʕ a d t"]),
        ("ישבת",   "קַעַדְתִי",   "قعدتي", ["ʔ a ʕ a d t i", "q a ʕ a d t i"]),
        ("ישב",    "קַעַד",      "قعد",   ["ʔ a ʕ a d", "q a ʕ a d"]),
        ("ישבה",   "קַעְדַת",    "قعدت",  ["ʔ a ʕ d a t", "q a ʕ d a t"]),
        ("ישבנו",  "קַעַדְנַא",   "قعدنا", ["ʔ a ʕ a d n a", "q a ʕ a d n a"]),
        ("ישבתם",  "קַעַדְתוּ",   "قعدتو", ["ʔ a ʕ a d t u", "q a ʕ a d t u"]),
        ("ישבו",   "קַעַדוּ",    "قعدو",  ["ʔ a ʕ a d u", "q a ʕ a d u", "ʔ a ʕ d u", "q a ʕ d u"]),
    ],
    "fatah": [
        ("פתחתי",  "פַתַחְת",    "فتحت",  ["f a t a ħ t"]),
        ("פתחת",   "פַתַחְת",    "فتحت",  ["f a t a ħ t"]),
        ("פתחת",   "פַתַחְתִי",   "فتحتي", ["f a t a ħ t i"]),
        ("פתח",    "פַתַח",      "فتح",   ["f a t a ħ"]),
        ("פתחה",   "פַתְחַת",    "فتحت",  ["f a t ħ a t"]),
        ("פתחנו",  "פַתַחְנַא",   "فتحنا", ["f a t a ħ n a"]),
        ("פתחתם",  "פַתַחְתוּ",   "فتحتو", ["f a t a ħ t u"]),
        ("פתחו",   "פַתַחוּ",    "فتحو",  ["f a t a ħ u", "f a t ħ u"]),
    ],
    "saal": [
        ("שאלתי",  "סַאַלֵת",    "سألت",  ["s a ʔ a l t", "s a ʔ a l i t"]),
        ("שאלת",   "סַאַלְת",    "سألت",  ["s a ʔ a l t", "s a ʔ a l i t"]),
        ("שאלת",   "סַאַלְתִי",   "سألتي", ["s a ʔ a l t i"]),
        ("שאל",    "סַאַל",      "سأل",   ["s a ʔ a l"]),
        ("שאלה",   "סַאְלַת",    "سألت",  ["s a ʔ l a t"]),
        ("שאלנו",  "סַאַלְנַא",   "سألنا", ["s a ʔ a l n a"]),
        ("שאלתם",  "סַאַלְתוּ",   "سألتو", ["s a ʔ a l t u"]),
        ("שאלו",   "סַאַלוּ",    "سألو",  ["s a ʔ a l u", "s a ʔ l u"]),
    ],
}

# every other word: arabic -> (course tr, IPA readings)
WORDS = {
    "بندورة": ("בַּנְדוֹרַה", ["b a n d oː r a"]), "على": ("עַלַא", ["ʕ a l a"]),
    "الطّاولة": ("אֵ(ל)טַّאוְלֵה", ["i tˤ tˤ aː w l e"]), "في": ("פִי", ["f i"]),
    "الحارة": ("אֵלְחַארַה", ["i l ħ aː r a"]), "الكرسي": ("אֵלְכֻּרְסִי", ["i l k u r s i"]),
    "فلافل": ("פַלַאפֵל", ["f a l aː f i l"]), "الشّبّاك": ("אֵ(ל)שֻّבַּّאכּ", ["i ʃ ʃ u b b aː k"]),
    "الدّكتور": ("אֵ(ל)דַّכְּתוֹר", ["i d d a k t oː r", "i d d u k t oː r"]), "الباب": ("אֵלְבַּאבּ", ["i l b aː b"]),
    "المعلّم": ("אֵלְמְעַלֵّם", ["i l m ʕ a l l i m"]), "الأكل": ("אֵלְאַכֵּל", ["i l ʔ a k i l", "i l ʔ a k l"]),
    "مع": ("מַע", ["m a ʕ"]), "اخوها": ("אַחַ'וּהַא", ["ʔ a x uː h a"]),
    "الصّاحب": ("אֵ(ל)צַّאחֵבּ", ["i sˤ sˤ aː ħ i b"]), "السّيّارة": ("אֵ(ל)סַّיַّארַה", ["i s s a j j aː r a"]),
    "البيت": ("אֵלְבֵּית", ["i l b eː t"]), "اليوم": ("אֵלְיוֹם", ["i l j oː m"]),
    "الطّيّارة": ("אֵ(ל)טַّיַّארַה", ["i tˤ tˤ a j j aː r a"]), "الولد": ("אֵלְוַלַד", ["i l w a l a d"]),
    "سؤال": ("סֻאַאל", ["s u ʔ aː l"]), "للكلب": ("לַלְכַּלְבּ", ["l a l k a l b"]),
    "الكلب": ("אֵלְכַּלְבּ", ["i l k a l b"]), "احمد": ("אַחְמַד", ["ʔ a ħ m a d"]),
    "الكبيرة": ("אֵלְכְּבִּירֵה", ["i l k b iː r e"]), "كبير": ("כְּבִּיר", ["k b iː r"]),
    "ابوي": ("אַבּוּי", ["ʔ a b uː j"]), "من": ("מִן", ["m i n"]),
}
NOT = ("ما", "מַא", "m a")        # before a verb
AND_NOT = ("وما", "וּמַא", "u m a")  # before a second verb

# The course's sentences: subject taught, Hebrew pattern, Arabic pattern.
# S = subject pronoun, V:verb = conjugated verb, W:verb = "and" + conjugated verb ({S} {V} {W} in the Hebrew).
SENTENCES = [
    ("inti",  "{S} {V} עגבנייה ליד השולחן",      "S V:akal بندورة على الطّاولة"),
    ("inte",  "{S} {V} בשכונה",                   "S V:qaad في الحارة"),
    ("huwwe", "{S} {V} על הכיסא ו{W} פלאפל",      "S V:qaad على الكرسي W:akal فلافل"),
    ("hiyye", "{S} {V} את החלון",                 "S V:fatah الشّبّاك"),
    ("ana",   "{S} {V} את הרופא",                 "S V:saal الدّكتور"),
    ("ihna",  "{S} {V} את הדלת",                  "S V:fatah الباب"),
    ("intu",  "{S} {V} ליד השולחן",               "S V:qaad على الطّاولة"),
    ("humme", "{S} {V} את המורה",                 "S V:saal المعلّم"),
    ("huwwe", "{S} {V} את האוכל על החלון",        "S V:akal الأكل على الشّبّاك"),
    ("hiyye", "{S} {V} עם אחיה",                  "S V:qaad مع اخوها"),
    ("inti",  "{S} {V} את החבר",                  "S V:saal الصّاحب"),
    ("humme", "{S} {V} את החלון",                 "S V:fatah الشّبّاك"),
    ("intu",  "{S} {V} במכונית",                  "S V:akal في السّيّارة"),
    ("ana",   "{S} {V} בבית היום",                "S V:qaad في البيت اليوم"),
    ("inte",  "{S} {V} את המטוס",                 "S V:fatah الطّيّارة"),
    ("ihna",  "{S} {V} את הילד שאלה",             "S V:saal الولد سؤال"),
    ("huwwe", "{S} {V} את הדלת לכלב",             "S V:fatah الباب للكلب"),
    ("hiyye", "{S} {V} את הדלת ו{W} עם אחמד",     "S V:fatah الباب W:akal مع احمد"),
    ("inti",  "{S} {V} את השולחן הגדול",          "S V:fatah الطّاولة الكبيرة"),
    ("humme", "{S} {V} את החבר שאלה גדולה",       "S V:saal الصّاحب سؤال كبير"),
    ("inte",  "{S} {V} פלאפל ליד השולחן",         "S V:akal فلافل على الطّاولة"),
    ("intu",  "{S} {V} את הדלת היום",             "S V:fatah الباب اليوم"),
    ("hiyye", "{S} {V} את הרופא",                 "S V:saal الدّكتور"),
    ("ana",   "{S} {V} עגבנייה עם אבא שלי",       "S V:akal بندورة مع ابوي"),
    ("ihna",  "{S} {V} בשכונה",                   "S V:qaad في الحارة"),
    ("huwwe", "{S} {V} את המורה שאלה",            "S V:saal المعلّم سؤال"),
    ("inte",  "{S} {V} עם הכלב",                  "S V:qaad مع الكلب"),
    ("hiyye", "{S} {V} את האוכל על החלון",        "S V:akal الأكل على الشّبّاك"),
    ("inti",  "{S} {V} את המטוס",                 "S V:fatah الطّيّارة"),
    ("ihna",  "{S} {V} את החבר ו{W}",             "S V:saal الصّاحب W:qaad"),
    ("humme", "{S} {V} עגבנייה מהשולחן",          "S V:akal بندورة من الطّاولة"),
]
MAX_VARIANTS = 16


def _build(he_pattern: str, pattern: str, pron: int, neg: bool) -> dict:
    """One phrase: lists of alternative (arabic, ipa) per word, then every combination of them."""
    _, he_pron, tr_pron, pron_alts = PRONOUNS[pron]
    slots: list[list[tuple[str, str]]] = []  # per word: its (arabic, ipa) readings
    tr: list[str] = []
    he = {"S": he_pron}
    for tok in pattern.split():
        kind, _, verb = tok.partition(":")
        if tok == "S":
            slots.append(pron_alts)
            tr.append(tr_pron)
        elif kind in ("V", "W") and verb:
            he_verb, tr_verb, ar, ipas = VERBS[verb][pron]
            he[kind] = ("לא " if neg else "") + he_verb
            if kind == "W" and neg:        # ... وما اكل
                slots.append([AND_NOT[::2]])
                tr.append(AND_NOT[1])
            elif kind == "W":              # ... واكل: the و is part of the word
                ar, ipas, tr_verb = "و" + ar, ["u " + i for i in ipas], "וּ" + tr_verb
            elif neg:                      # ما اكل ...
                slots.append([NOT[::2]])
                tr.append(NOT[1])
            slots.append([(ar, i) for i in ipas])
            tr.append(tr_verb)
        else:
            tr_word, ipas = WORDS[tok]
            slots.append([(tok, i) for i in ipas])
            tr.append(tr_word)
    variants = [(" ".join(a for a, _ in combo), " | ".join(i for _, i in combo))
                for combo in itertools.islice(itertools.product(*slots), MAX_VARIANTS)]
    return dict(cat="day2", he=he_pattern.format(**he), tr=" ".join(tr), variants=variants)


def day2_phrases() -> list[dict]:
    """Every pattern x pronoun x polarity. `grp`/`pron`/`neg` let the page move between them;
    `listed` marks the course's own sentences, which are the ones the tab starts from."""
    groups: dict[tuple[str, str], int] = {}
    taught: set[tuple[int, int]] = set()
    for pron, he_pattern, pattern in SENTENCES:
        g = groups.setdefault((he_pattern, pattern), len(groups))
        taught.add((g, P.index(pron)))
    out = []
    for (he_pattern, pattern), g in groups.items():
        for pron in range(len(PRONOUNS)):
            for neg in (False, True):
                phrase = _build(he_pattern, pattern, pron, neg)
                phrase.update(grp=g, pron=pron, neg=neg, listed=(g, pron) in taught and not neg)
                out.append(phrase)
    return out
