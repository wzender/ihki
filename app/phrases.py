"""Phrase bank.

Each phrase: Hebrew prompt, Hebrew transliteration hint, category, and accepted variants.
A variant is (arabic, ipa). IPA phonemes are space-separated and words are separated by " | ".
The Arabic text and the IPA must have the same number of words (checked in tests).

Conventions (Palestinian urban speech as the main form):
  ق -> ʔ (urban); a variant with q or k is listed where the rural form is common.
  ث -> t, ذ -> d (normal in the dialect).
  ج -> dʒ.
  Emphatics are written with ˤ (sˤ tˤ dˤ ðˤ), long vowels with ː.
  Geminates are written doubled; the tokenizer collapses them for CTC.
"""
from .grammar import day2_phrases
from .workbook import CATEGORIES as WORKBOOK_CATEGORIES, LESSON_TABS, workbook_phrases

PHRASES = [
    # ---- היכרות ----
    dict(cat="meet", he="שלום", tr="מַרְחַבַּא", variants=[
        ("مرحبا", "m a r ħ a b a"),
        ("اهلا", "ʔ a h l a"),
        ("اهلين", "ʔ a h l eː n"),
    ]),
    dict(cat="meet", he="מה שלומך? (לגבר)", tr="כִּיפַכּ", variants=[
        ("كيفك", "k iː f a k"),
        ("كيف حالك", "k iː f | ħ aː l a k"),
    ]),
    dict(cat="meet", he="אני בסדר, ברוך השם", tr="מְנִיח, אִלְחַמְדִלַّה", variants=[
        ("منيح الحمد للّه", "m n iː ħ | i l ħ a m d | i l l a"),
        ("منيح والحمد للّه", "m n iː ħ | w i l ħ a m d | i l l a"),
        ("تمام الحمد للّه", "t a m aː m | i l ħ a m d | i l l a"),
    ]),
    dict(cat="meet", he="איך קוראים לך? (לגבר)", tr="שוּ אִסְמַכּ", variants=[
        ("شو اسمك", "ʃ u | ʔ i s m a k"),
        ("شو اسمك", "ʃ u | s m a k"),
        ("ايش اسمك", "ʔ eː ʃ | ʔ i s m a k"),
    ]),
    dict(cat="meet", he="אני מירושלים", tr="אַנַא מִן אִלְקֻדְס", variants=[
        ("انا من القدس", "ʔ a n a | m i n | i l ʔ u d s"),
        ("انا من القدس", "ʔ a n a | m i n | i l q u d s"),
    ]),
    dict(cat="meet", he="בוקר טוב", tr="צַבַּאח אִלְח'ֵיר", variants=[
        ("صباح الخير", "sˤ a b aː ħ | i l x eː r"),
    ]),
    dict(cat="meet", he="לילה טוב (לגבר)", tr="תִצְבַּח עַלַא ח'ֵיר", variants=[
        ("تصبح على خير", "t i sˤ b a ħ | ʕ a l a | x eː r"),
    ]),
    dict(cat="meet", he="תודה רבה", tr="שֻכְּרַן כְּתִיר", variants=[
        ("شكرا كتير", "ʃ u k r a n | k t iː r"),
        ("مشكور كتير", "m a ʃ k uː r | k t iː r"),
    ]),
    dict(cat="meet", he="להתראות", tr="מַע אִ(ל)סַّלַאמֶה", variants=[
        ("مع السّلامة", "m a ʕ | i s s a l aː m e"),
        ("يلّا باي", "j a l l a | b aː j"),
    ]),
    dict(cat="meet", he="סליחה, תסלח לי", tr="סַאמְחְנִי", variants=[
        ("سامحني", "s aː m ħ i n i"),
        ("اسف", "ʔ aː s i f"),
    ]),
    # ---- שיחה ----
    dict(cat="talk", he="מה אתה עושה? (עכשיו)", tr="שוּ בְּתִעְמַל", variants=[
        ("شو بتعمل", "ʃ u | b t i ʕ m a l"),
        ("شو بتسوّي", "ʃ u | b t s a w w i"),
    ]),
    dict(cat="talk", he="אני עובד בבית חולים", tr="בַּשְתְע'ִל בִּלְמֻסְתַשְפַא", variants=[
        ("بشتغل بالمستشفى", "b a ʃ t ɣ i l | b i l m u s t a ʃ f a"),
        ("بشتغل في المستشفى", "b a ʃ t ɣ i l | f i | l m u s t a ʃ f a"),
    ]),
    dict(cat="talk", he="אני לא מבין (גבר)", tr="מִש פַאהֵם", variants=[
        ("مش فاهم", "m i ʃ | f aː h i m"),
        ("ما فهمت", "m aː | f h i m t"),
    ]),
    dict(cat="talk", he="אתה יכול לדבר לאט?", tr="מֻמְכִּן תִחְכִּי שְוַיّ שְוַיّ", variants=[
        ("ممكن تحكي شويّ شويّ", "m u m k i n | t i ħ k i | ʃ w a j j | ʃ w a j j"),
        ("ممكن تحكي شويّ", "m u m k i n | t i ħ k i | ʃ w a j j"),
    ]),
    dict(cat="talk", he="אין בעיה", tr="מִש מֻשְכִּלֶה", variants=[
        ("مش مشكلة", "m i ʃ | m u ʃ k i l e"),
        ("ما في مشكلة", "m aː | f i | m u ʃ k i l e"),
    ]),
    dict(cat="talk", he="נתראה מחר", tr="בְּנְשוּף בַּעְד' בֻּכְּרַה", variants=[
        ("بنشوف بعض بكرة", "b n ʃ uː f | b a ʕ dˤ | b u k r a"),
        ("منشوف بعض بكرة", "m n ʃ uː f | b a ʕ dˤ | b u k r a"),
        ("بنشوفك بكرة", "b n ʃ uː f a k | b u k r a"),
    ]),
    dict(cat="talk", he="היום חם מאוד", tr="אִלְיוֹם שוֹבּ כְּתִיר", variants=[
        ("اليوم شوب كتير", "i l j oː m | ʃ oː b | k t iː r"),
        ("اليوم حرّ كتير", "i l j oː m | ħ a r r | k t iː r"),
        ("الجوّ حرّ كتير", "i l dʒ a w w | ħ a r r | k t iː r"),
    ]),
    # ---- בעיר ----
    dict(cat="city", he="איפה התחנה?", tr="וֵין אִלְמַחַטַّה", variants=[
        ("وين المحطّة", "w eː n | i l m a ħ a tˤ tˤ a"),
    ]),
    dict(cat="city", he="איפה השירותים?", tr="וֵין אִלְחַמַّאם", variants=[
        ("وين الحمّام", "w eː n | i l ħ a m m aː m"),
    ]),
    dict(cat="city", he="מה השעה?", tr="קַדֵّיש אִ(ל)סַّאעַה", variants=[
        ("قدّيش السّاعة", "ʔ a d d eː ʃ | i s s aː ʕ a"),
        ("قدّيش السّاعة", "q a d d eː ʃ | i s s aː ʕ a"),
        ("كم السّاعة", "k a m | i s s aː ʕ a"),
    ]),
    # ---- אוכל וקניות ----
    dict(cat="food", he="אני רוצה כוס קפה", tr="בִּדִّי פִנְג'אן קַהְוֶה", variants=[
        ("بدّي فنجان قهوة", "b i d d i | f i n dʒ aː n | ʔ a h w e"),
        ("بدّي فنجان قهوة", "b i d d i | f i n dʒ aː n | q a h w e"),
        ("بدّي كاسة قهوة", "b i d d i | k aː s e t | ʔ a h w e"),
    ]),
    dict(cat="food", he="תן לי מים, בבקשה", tr="אַעְטִינִי מַיّ לַוּ סַמַחְת", variants=[
        ("اعطيني ميّ لو سمحت", "ʔ a ʕ tˤ iː n i | m a j j | l a w | s a m a ħ t"),
        ("عطيني ميّ لو سمحت", "ʕ tˤ iː n i | m a j j | l a w | s a m a ħ t"),
    ]),
    dict(cat="food", he="אני אוהב אוכל ערבי", tr="בַּחִבּّ אִלְאַכְּל אִלְעַרַבִּי", variants=[
        ("بحبّ الأكل العربي", "b a ħ i b b | i l ʔ a k l | i l ʕ a r a b i"),
    ]),
    dict(cat="food", he="כמה זה עולה?", tr="קַדֵّיש חַקّוֹ", variants=[
        ("قدّيش حقّه", "ʔ a d d eː ʃ | ħ a ʔ ʔ o"),
        ("قدّيش حقّه", "q a d d eː ʃ | ħ a q q o"),
        ("بقدّيش", "b i ʔ a d d eː ʃ"),
    ]),
    dict(cat="food", he="החשבון, בבקשה", tr="אִלְחְסַאבּ לַוּ סַמַחְת", variants=[
        ("الحساب لو سمحت", "i l ħ s aː b | l a w | s a m a ħ t"),
        ("الحساب لو سمحت", "i l ħ i s aː b | l a w | s a m a ħ t"),
    ]),
]
# ---- יום 2 ---- generated: every sentence pattern x pronoun x positive/negative (see grammar.py)
PHRASES += day2_phrases()
# ---- שיעורים ---- the workbook's translation exercises, lesson by lesson (see workbook.py)
PHRASES += workbook_phrases()

# The page asks for a lesson first, then one of its categories: the course's lessons (workbook.py)
# and, last, the phrases that belong to no lesson.
FREE = "free"
LESSONS = [*LESSON_TABS, {"id": FREE, "name": "תרגול חופשי"}]
CATEGORIES = [
    *WORKBOOK_CATEGORIES,
    {"id": "meet", "name": "היכרות", "lesson": FREE},
    {"id": "talk", "name": "שיחה", "lesson": FREE},
    {"id": "city", "name": "בעיר", "lesson": FREE},
    {"id": "food", "name": "אוכל וקניות", "lesson": FREE},
    {"id": "day2", "name": "יום 2", "lesson": FREE},
]


def public_phrases():
    """What the frontend needs: no IPA, first variant's Arabic as the model answer.

    Grammar-built phrases also carry grp / pron / neg / listed / seq (see grammar.py); the page lists a
    category's `listed` phrases, in `seq` order, and reaches the others with its negation and
    conjugation buttons. Workbook phrases carry seq and read (see workbook.py).
    """
    return [
        {"id": i, "cat": p["cat"], "he": p["he"], "tr": p["tr"], "ar": p["variants"][0][0],
         **{k: p[k] for k in ("grp", "pron", "neg", "listed", "seq", "read") if k in p}}
        for i, p in enumerate(PHRASES)
    ]
