"""The course workbook (שלב א') lesson by lesson: the student picks a lesson, then one of its topics.

A topic is a list of items, each written once as (Hebrew, Arabic), and is one of two kinds:
  "say":  Hebrew -> Arabic. The usual flow: Hebrew prompt, the student says it in Arabic.
  "read": Arabic -> Hebrew. The page shows the transliteration, the student reads it aloud (and is
          scored on it) and checks the translation with "הצג תרגום".
Single words (the pronouns, a verb's past and future) and conjugations are "say" items like the
sentences. An ordered topic is shown in the workbook's order; the others are shuffled.

Each Arabic word is looked up in LEX, which gives its transliteration as the workbook writes it and
its accepted pronunciations. A word spelled like another form carries a tag after "#"
(اكلت = I ate, اكلت#f = she ate); the tag is not shown. A leading و ("and") is split off when the
whole word is not in LEX. Punctuation stays on its word.
"""
from __future__ import annotations

import itertools

from .grammar import MAX_VARIANTS, PRONOUNS, WORDS

# arabic[#tag] -> (workbook transliteration, IPA readings, main one first)
LEX: dict[str, tuple[str, list[str]]] = {
    **WORDS,
    **{alts[0][0]: (tr, [ipa for _, ipa in alts]) for _, _, tr, alts in PRONOUNS},
    # ---- verbs: past tense, then the future's base form ----
    "اكل": ("אַכַּל", ["ʔ a k a l"]), "اكلت": ("אַכַּלֵת", ["ʔ a k a l t"]), "اكلت#f": ("אַכְּלַת", ["ʔ a k l a t"]),
    "اكلتي": ("אַכַּלְתִי", ["ʔ a k a l t i"]),
    "اكلنا": ("אַכַּלְנַא", ["ʔ a k a l n a"]), "اكلتو": ("אַכַּלְתוּ", ["ʔ a k a l t u"]),
    "اكلو": ("אַכַּלוּ", ["ʔ a k a l u", "ʔ a k l u"]),
    "قعد": ("קַעַד", ["ʔ a ʕ a d", "q a ʕ a d"]), "قعدت": ("קַעַדֵת", ["ʔ a ʕ a d t", "q a ʕ a d t"]),
    "قعدت#f": ("קַעְדַת", ["ʔ a ʕ d a t", "q a ʕ d a t"]),
    "قعدتي": ("קַעַדְתִי", ["ʔ a ʕ a d t i", "q a ʕ a d t i"]), "قعدنا": ("קַעַדְנַא", ["ʔ a ʕ a d n a", "q a ʕ a d n a"]),
    "قعدتو": ("קַעַדְתוּ", ["ʔ a ʕ a d t u", "q a ʕ a d t u"]), "قعدو": ("קַעַדוּ", ["ʔ a ʕ a d u", "q a ʕ a d u"]),
    "فتح": ("פַתַח", ["f a t a ħ"]), "فتحت": ("פַתַחֵת", ["f a t a ħ t"]), "فتحتي": ("פַתַחְתִי", ["f a t a ħ t i"]),
    "فتحت#f": ("פַתְחַת", ["f a t ħ a t"]), "فتحنا": ("פַתַחְנַא", ["f a t a ħ n a"]),
    "فتحتو": ("פַתַחְתוּ", ["f a t a ħ t u"]), "فتحو": ("פַתַחוּ", ["f a t a ħ u", "f a t ħ u"]),
    "سأل": ("סַאַל", ["s a ʔ a l"]), "سألت": ("סַאַלֵת", ["s a ʔ a l t", "s a ʔ a l i t"]),
    "سألت#f": ("סַאְלַת", ["s a ʔ l a t"]), "سألنا": ("סַאַלְנַא", ["s a ʔ a l n a"]),
    "سألتي": ("סַאַלְתִי", ["s a ʔ a l t i"]), "سألتو": ("סַאַלְתוּ", ["s a ʔ a l t u"]), "سألو": ("סַאַלוּ", ["s a ʔ a l u", "s a ʔ l u"]),
    "يفتح": ("יִפְתַח", ["j i f t a ħ"]), "ياكل": ("יֹאכֹּל", ["j oː k u l", "j oː k i l", "j aː k u l"]),
    "يسأل": ("יִסְאַל", ["j i s ʔ a l"]), "يقعد": ("יֻקְעֹד", ["j u ʔ ʕ u d", "j u q ʕ u d"]),
    # ... with an object suffix
    "سألنا#us": ("סַאַל-נַא", ["s a ʔ a l n a"]), "سألته": ("סַאַלְת-וֹ", ["s a ʔ a l t o"]),
    "سألتك": ("סַאַלְתַ-כּ", ["s a ʔ a l t a k"]), "سألتك#f": ("סַאְלַתֵ-כּ", ["s a ʔ l a t i k"]),
    "سألتها": ("סַאַלְת-הַא", ["s a ʔ a l t h a"]), "سألتوني": ("סַאַלְתוּ-נִי", ["s a ʔ a l t uː n i"]),
    "سألتيني": ("סַאַלְתִי-נִי", ["s a ʔ a l t iː n i"]),
    # ---- everything else ----
    "لا": ("לַא", ["l aː"]), "شو": ("שוּ", ["ʃ u"]), "ليش": ("לֵיש", ["l eː ʃ"]), "مين": ("מִין", ["m iː n"]),
    "وين": ("וֵין", ["w eː n"]), "كمان": ("כַּמַאן", ["k a m aː n"]), "يا": ("יַא", ["j a"]),
    "مفتوح": ("מַפְתוּח", ["m a f t uː ħ"]), "باب": ("בַּאבּ", ["b aː b"]), "ساكن": ("סַאכֵּן", ["s aː k i n"]),
    "الحمد": ("אֵלְחַמְדֻ", ["i l ħ a m d u", "i l ħ a m d"]), "للّه": ("לִלַّה", ["l i l l a", "i l l a"]),
    "صاحبي": ("צַאחְבּ-י", ["sˤ aː ħ b i"]), "صاحبه": ("צַאחְבּ-וֹ", ["sˤ aː ħ b o"]),
    "بنتي": ("בִּנְת-י", ["b i n t i"]), "البنت": ("אֵלְבִּנְת", ["i l b i n t"]), "معها": ("מַע-הַא", ["m a ʕ h a"]),
    "بيتي": ("בֵּית-י", ["b eː t i"]), "بيته": ("בֵּית-וֹ", ["b eː t o"]),
    "اخوي": ("אַח'וּי", ["ʔ a x uː j"]), "اختي": ("אֹחְ'ת-י", ["ʔ u x t i"]), "امّي": ("אִמّ-י", ["ʔ i m m i"]),
    "فاطمة": ("פַאטְמֵה", ["f aː tˤ m e"]), "شاطرة": ("שַאטְרַה", ["ʃ aː tˤ r a"]),
    "النّجّار": ("אֵ(ל)נַّגַّ'אר", ["i n n a dʒ dʒ aː r"]),
    "الدّكتورة": ("אֵ(ל)דַّכְּתוֹרַה", ["i d d a k t oː r a", "i d d u k t oː r a"]),
    "المعلّمة": ("אֵלְמְעַלְّמֵה", ["i l m ʕ a l l m e"]),
    "القدس": ("אֵלְקֻדְס", ["i l ʔ u d s", "i l q u d s"]),
    "شاورما": ("שַוַארְמַה", ["ʃ a w aː r m a"]), "بيتسا": ("בִּיתְצַה", ["b iː t s a"]), "خيار": ("חְ'יַאר", ["x j aː r"]),
    "كنافة": ("כְּנַאפֵה", ["k n aː f e"]), "بقلاوة": ("בַּקְלַאוַה", ["b a ʔ l aː w a", "b a q l aː w a"]),
}
PUNCT = {"؟": "?", "،": ",", ".": ".", ":": ":", "!": "!"}

HE_PRONOUNS = [he for _, he, _, _ in PRONOUNS]
AR_PRONOUNS = [alts[0][0] for _, _, _, alts in PRONOUNS]
# lesson 1's verbs (בניין ראשון, פַעַל): Hebrew past forms and LEX keys, one per pronoun in PRONOUNS order, then the future
VERBS_1 = [
    ("פתחתי פתחת פתחת פתח פתחה פתחנו פתחתם פתחו", "فتحت فتحت فتحتي فتح فتحت#f فتحنا فتحتو فتحو", "يفتح"),
    ("אכלתי אכלת אכלת אכל אכלה אכלנו אכלתם אכלו", "اكلت اكلت اكلتي اكل اكلت#f اكلنا اكلتو اكلو", "ياكل"),
    ("שאלתי שאלת שאלת שאל שאלה שאלנו שאלתם שאלו", "سألت سألت سألتي سأل سألت#f سألنا سألتو سألو", "يسأل"),
    ("ישבתי ישבת ישבת ישב ישבה ישבנו ישבתם ישבו", "قعدت قعدت قعدتي قعد قعدت#f قعدنا قعدتو قعدو", "يقعد"),
]
HUWWE = 3  # the pronoun whose past form names the verb


def _base_forms(verbs):
    """A verb as the workbook lists it: past and future of "he"."""
    return [(f"{he.split()[HUWWE]} (עבר, עתיד)", f"{ar.split()[HUWWE]}، {future}") for he, ar, future in verbs]


def _past(verbs):
    """Every pronoun with its past form: אני פתחתי -> انا فتحت."""
    return [(f"{hp} {h}", f"{ap} {a}") for he, ar, _ in verbs
            for hp, ap, h, a in zip(HE_PRONOUNS, AR_PRONOUNS, he.split(), ar.split())]


# lesson -> its topics: (id, name, kind, shown in order, [(Hebrew, Arabic)]); sentence numbering is the workbook's
LESSONS = {
    1: [
        ("pron", "הגופים", "say", True, list(zip(["אני", "אתה", "את", "הוא", "היא", "אנחנו", "אתם", "הם / הן"], AR_PRONOUNS))),
        ("verbs", "פעלים", "say", True, _base_forms(VERBS_1)),
        ("past", "הטיות בעבר", "say", False, _past(VERBS_1)),
        ("say", "משפטים: עברית ← ערבית", "say", True, [
        ("אני שאלתי מדוע היא פתחה את הדלת היום?", "انا سألت ليش هيّ فتحت#f الباب اليوم؟"),
        ("אנחנו שאלנו את הרופאה איפה הנגר גר, הוא גר בירושלים.", "احنا سألنا الدّكتورة وين النّجّار ساكن، هوّ ساكن في القدس."),
        ("אני אכלתי עגבניות, אתם אכלתם מלפפונים.", "انا اكلت بندورة، انتو اكلتو خيار."),
        ("מי שאל שאלה? אני שאלתי שאלה. אני שאלתי את חברי שאלה.", "مين سأل سؤال؟ انا سألت سؤال. انا سألت صاحبي سؤال."),
        ("אנחנו פתחנו את דלת המכונית, הם פתחו את דלת המטוס.", "احنا فتحنا باب السّيّارة، همّ فتحو باب الطّيّارة."),
        ("הרופא ישב בביתו עם הכלב, הילד שאל מדוע.", "الدّكتور قعد في بيته مع الكلب، الولد سأل ليش."),
        ("מי פתח את הדלת? היא פתחה את דלת הבית ושאלה שאלה.", "مين فتح الباب؟ هيّ فتحت#f باب البيت وسألت#f سؤال."),
        ("אמי שאלה את חברי: מי ישב במכונית? אנחנו ישבנו במכונית.", "امّي سألت#f صاحبي: مين قعد في السّيّارة؟ احنا قعدنا في السّيّارة."),
        ("חברו שאל את חברי מי ישב ואכל בביתו פלאפל?", "صاحبه سأل صاحبي مين قعد واكل في بيته فلافل؟"),
        ("אנחנו אכלנו כנאפה, היא אכלה בקלאווה, מה אתם אכלתם?", "احنا اكلنا كنافة، هيّ اكلت#f بقلاوة، شو انتو اكلتو؟"),
        ]),
        ("read", "משפטים: ערבית ← עברית", "read", True, [
        ("אתם אכלתם עם חבר שלי? לא, אנחנו אכלנו עם אחמד.", "انتو اكلتو مع صاحبي؟ لا، احنا اكلنا مع احمد."),
        ("מה אתה שאלת את הילד? אני שאלתי אותו למה הוא ישב בשכונה.", "شو انت سألت الولد؟ انا سألته ليش هوّ قعد في الحارة."),
        ("הדלת פתוחה, מי פתח את הדלת? אתם פתחתם את הדלת?", "الباب مفتوح، مين فتح الباب؟ انتو فتحتو الباب؟"),
        ("אני שאלתי אותך שאלה ואתה שאלת את חבר שלי שאלה.", "انا سألتك سؤال وانت سألت صاحبي سؤال."),
        ("אנחנו פתחנו את דלת המטוס, דלת המטוס פתוחה.", "احنا فتحنا باب الطّيّارة، باب الطّيّارة مفتوح."),
        ("אתם שאלתם אותי שאלה? לא, אנחנו שאלנו את המורה שאלה.", "انتو سألتوني سؤال؟ لا، احنا سألنا المعلّم سؤال."),
        ("מי אכל את האוכל? אנחנו אכלנו, ברוך השם.", "مين اكل الأكل؟ احنا اكلنا الحمد للّه."),
        ("היא שאלה את פאטמה שאלה ואני שאלתי אותה שאלה גם.", "هيّ سألت#f فاطمة سؤال وانا سألتها سؤال كمان."),
        ("הוא אכל פלאפל ואני אכלתי שווארמה. מה אתה אכלת היום?", "هوّ اكل فلافل وانا اكلت شاورما. شو انت اكلت اليوم؟"),
        ("הילדה ישבה עם הילד, הם ישבו בבית ואכלו.", "البنت قعدت#f مع الولد، همّ قعدو في البيت واكلو."),
        ("המורה ישב ליד השולחן ושאל אותנו שאלה.", "المعلّم قعد على الطّاولة وسألنا#us سؤال."),
        ("אני שאלתי: למה הכלב ישב בבית?", "انا سألت: ليش الكلب قعد في البيت؟"),
        ("הרופא שאל את חבר שלי: מי ישב על הכיסא?", "الدّكتور سأل صاحبي: مين قعد على الكرسي؟"),
        ("אנחנו ישבנו בשכונה עם המורה.", "احنا قعدنا في الحارة مع المعلّم."),
        ("היא שאלה את הנגר למה הוא פתח את דלת המכונית.", "هيّ سألت#f النّجّار ليش هوّ فتح باب السّيّارة."),
        ("אתם ישבתם עם הבת שלי במטוס ואכלתם איתה?", "انتو قعدتو مع بنتي في الطّيّارة واكلتو معها؟"),
        ("את ישבת על הכיסא ושאלת אותי שאלה גדולה.", "انتي قعدتي على الكرسي وسألتيني سؤال كبير."),
        ("את, חבר שלי ואני ישבנו בבית שלי ואכלנו פיצה.", "انتي، صاحبي وانا قعدنا في بيتي واكلنا بيتسا."),
        ("הרופאה שאלה אותך שאלה: למה את ישבת על השולחן?", "الدّكتورة سألتك#f سؤال: ليش انتي قعدتي على الطّاولة؟"),
        ("אחי פתח את דלת הבית ושאל את אחותי שאלה.", "اخوي فتح باب البيت وسأل اختي سؤال."),
        ("אני שאלתי את אבא שלי מי אכל את האוכל? אבא שלי: \"הכלב אכל את האוכל\".", "انا سألت ابوي مين اكل الأكل؟ ابوي: الكلب اكل الأكل."),
        ("המורה שאלה את הילדה: \"חרוצה, למה ישבת על השולחן?\"", "المعلّمة سألت#f البنت: يا شاطرة ليش قعدتي على الطّاولة؟"),
        ]),
    ],
}
LESSON_TABS = [{"id": str(n), "name": f"שיעור {n}"} for n in LESSONS]
CATEGORIES = [{"id": f"l{n}-{tid}", "name": name, "lesson": str(n)}
              for n, topics in LESSONS.items() for tid, name, *_ in topics]


def _word(token: str) -> tuple[str, str, list[str]]:
    """(Arabic as shown, transliteration, IPA readings) of one word of a sentence."""
    key = token.rstrip("".join(PUNCT))
    tail = token[len(key):]
    tr_tail = "".join(PUNCT[c] for c in tail)
    if key in LEX:
        tr, ipas = LEX[key]
    elif key.startswith("و") and key[1:] in LEX:   # "and" written on the word
        tr, ipas = LEX[key[1:]]
        tr, ipas = "וּ" + tr, ["u " + i for i in ipas]
    else:
        raise KeyError(f"workbook word not in LEX: {key}")
    return key.partition("#")[0] + tail, tr + tr_tail, ipas


def _variants(words: list[tuple[str, str, list[str]]]) -> list[tuple[str, str]]:
    """Every combination of the words' readings; when that is too many, the main reading throughout
    and then each word's n-th reading together (a speaker who says ق as q says it in every word)."""
    ar = " ".join(a for a, _, _ in words)
    readings = [ipas for _, _, ipas in words]
    count = 1
    for r in readings:
        count *= len(r)
    if count <= MAX_VARIANTS:
        combos = itertools.product(*readings)
    else:
        combos = dict.fromkeys(tuple(r[min(n, len(r) - 1)] for r in readings)
                               for n in range(max(map(len, readings))))
    return [(ar, " | ".join(c)) for c in combos]


def workbook_phrases() -> list[dict]:
    """The lessons' items as phrases (see phrases.py). `seq` keeps an ordered topic's order and
    `read` marks the Arabic -> Hebrew ones, where the page shows `tr` and hides `he`."""
    out = []
    for n, topics in LESSONS.items():
        for tid, _, kind, in_order, items in topics:
            for seq, (he, sentence) in enumerate(items, 1):
                words = [_word(t) for t in sentence.split()]
                phrase = dict(cat=f"l{n}-{tid}", he=he, tr=" ".join(t for _, t, _ in words),
                              variants=_variants(words), read=kind == "read")
                if in_order:
                    phrase["seq"] = seq
                out.append(phrase)
    return out
