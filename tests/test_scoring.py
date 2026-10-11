"""Tests with synthetic CTC emissions: no model download needed."""
import io
import wave

import numpy as np
import pytest

from app.align import ctc_viterbi, greedy_decode
from app.phrases import PHRASES
from app.scorer import Scorer, Vocab, tokenize

SYMS = ["<pad>", "w", "eː", "n", "i", "l", "ħ", "a", "m", "aː", "χ", "h", "k", "ʔ", "d", "ʃ", "s", "ʕ", "q", "e"]
VOCAB = {s: i for i, s in enumerate(SYMS)}


def emissions(phones, frames=3, gap=2, peak=0.9):
    """Log-posteriors where each phone peaks for `frames` frames, separated by blanks."""
    V = len(SYMS)
    rows = []

    def row(sym, p):
        r = np.full(V, (1 - p) / (V - 1))
        r[VOCAB[sym]] = p
        return r

    for _ in range(gap):
        rows.append(row("<pad>", 0.95))
    for ph in phones:
        for _ in range(frames):
            rows.append(row(ph, peak))
        for _ in range(gap):
            rows.append(row("<pad>", 0.95))
    return np.log(np.array(rows))


def find(he):
    return next(p for p in PHRASES if p["he"] == he)


def scorer_for(logp):
    return Scorer(lambda wav: logp, Vocab(VOCAB))


TOILET = find("איפה השירותים?")          # وين الحمام  w eː n | i l ħ a m m aː m
TIME = find("מה השעה?")                   # قديش الساعة


def test_viterbi_spans_cover_every_token():
    toks = [VOCAB[s] for s in ["w", "eː", "n"]]
    logp = emissions(["w", "eː", "n"])
    al = ctc_viterbi(logp, toks, 0)
    assert al is not None
    assert all(0 <= a < b for a, b in al.spans)
    assert [a for a, _ in al.spans] == sorted(a for a, _ in al.spans)
    assert greedy_decode(logp, 0) == toks


def test_too_short_returns_none():
    logp = emissions(["w"], frames=1, gap=0)
    assert ctc_viterbi(logp, [VOCAB["w"], VOCAB["eː"], VOCAB["n"]], 0) is None


def test_geminates_collapse():
    toks = tokenize("i l ħ a m m aː m", Vocab(VOCAB))
    assert [t.phoneme for t in toks] == ["i", "l", "ħ", "a", "m", "aː", "m"]


def test_correct_pronunciation_scores_high():
    logp = emissions(["w", "eː", "n", "i", "l", "ħ", "a", "m", "aː", "m"])
    r = scorer_for(logp).score(None, TOILET)
    assert r["score"] >= 90
    assert r["tips"] == []
    assert not r["unrelated"]


def test_hebrew_chet_flagged_on_hammam():
    # ħ said as the Hebrew uvular χ
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    r = scorer_for(logp).score(None, TOILET)
    word = r["words"][1]
    h = next(p for p in word["phonemes"] if p["ipa"] == "ħ")
    assert h["flagged"] and h["heard"] == "χ"
    assert r["words"][0]["score"] > 90          # وين is fine
    assert r["tips"][0]["key"] == "ħ>x"
    assert r["tips"][0]["word"] == "الحمّام"
    assert 50 <= r["score"] < 85              # one wrong consonant: "almost", not "excellent"


def test_h_instead_of_pharyngeal():
    logp = emissions(["w", "eː", "n", "i", "l", "h", "a", "m", "aː", "m"])
    r = scorer_for(logp).score(None, TOILET)
    assert r["tips"][0]["key"] == "ħ>h"


def test_k_for_urban_qaf_picks_closest_variant_and_tips():
    # learner says "kaddeesh" for قديش: closest variant is the glottal-stop one, ʔ flagged as k
    logp = emissions(["k", "a", "d", "eː", "ʃ", "i", "s", "aː", "ʕ", "a"])
    r = scorer_for(logp).score(None, TIME)
    keys = [t["key"] for t in r["tips"]]
    assert r["variant"]["ipa"].startswith("ʔ")
    assert "ʔ>k" in keys


def test_unrelated_sentence():
    logp = emissions(["ʃ", "a", "l", "a", "m"])
    r = scorer_for(logp).score(None, TOILET)
    assert r["unrelated"] and r["score"] <= 30


@pytest.mark.parametrize("p", PHRASES, ids=lambda p: p["he"])
def test_phrase_bank_word_counts_match(p):
    for ar, ipa in p["variants"]:
        assert len(ar.split()) == len(ipa.split("|")), (ar, ipa)


def test_api_end_to_end_with_stub_model():
    from fastapi.testclient import TestClient
    from app.main import create_app

    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    app = create_app(scorer_for(logp))
    # 1 s of a 220 Hz tone as a WAV upload, so ffmpeg decoding is exercised
    t = np.arange(16000) / 16000
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)

    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        ph = c.get("/api/phrases").json()
        shown = {c["id"] for c in ph["categories"]}   # the phrases of no lesson are in the bank, not on the page
        assert len(ph["phrases"]) == sum(p["cat"] in shown for p in PHRASES) and "ipa" not in ph["phrases"][0]
        assert TOILET["cat"] not in shown and [l["id"] for l in ph["lessons"] if not l.get("soon")] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        assert len(ph["lessons"]) == 20 and [v["name"] for v in ph["levels"]] == ["רמה 1", "רמה 2"]
        pid = PHRASES.index(TOILET)
        r = c.post("/api/score", data={"phrase_id": pid}, files={"audio": ("a.wav", buf.getvalue(), "audio/wav")})
        assert r.status_code == 200, r.text
        assert r.json()["tips"][0]["key"] == "ħ>x"


def test_anything_below_100_says_what_was_wrong():
    for phones in (["w", "eː", "n", "i", "l", "ħ", "a", "m", "aː", "m"],      # clean
                   ["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"],      # wrong consonant
                   ["w", "e", "n", "i", "l", "ħ", "a", "m", "a", "m"]):       # short vowels
        r = scorer_for(emissions(phones)).score(None, TOILET)
        assert r["score"] == 100 or r["unrelated"] or r["tips"] or r["issues"], phones
        for i in r["issues"]:
            assert i["word_tr"] and i["sound"] and not i["sound"].isascii()


def test_dropped_glottal_stop_is_not_an_error():
    # اهلا said without the initial ʔ, as natives do
    phrase = {"variants": [("اهلا", "ʔ a h l a")]}
    r = scorer_for(emissions(["a", "h", "l", "a"])).score(None, phrase)
    assert r["words"][0]["score"] >= 90 and r["score"] >= 90
    assert not r["tips"] and not any("עצירה" in i["sound"] for i in r["issues"])


def test_tts_endpoint_serves_phrase_audio_and_503_without_key():
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeTTS:
        voice_id, voice_name = "v-123", "ليلى"

        def wav(self, text, voice=""):
            assert text == PHRASES[0]["variants"][0][0]
            return b"RIFFfake", voice or self.voice_id

    with TestClient(create_app(scorer_for(emissions(["w"])), FakeTTS())) as c:
        r = c.get("/api/tts/0")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content == b"RIFFfake"
        assert r.headers["x-tts-provider"] == "sawtakarabi.ai" and r.headers["x-tts-voice-id"] == "v-123"
        from urllib.parse import unquote

        from app.tts import VOICES
        voices = c.get("/api/phrases").json()["voices"]
        assert [v["name"] for v in voices] == list(VOICES)
        r = c.get("/api/tts/0?voice=" + voices[1]["id"])
        assert r.headers["x-tts-voice-id"] == voices[1]["id"] and unquote(r.headers["x-tts-voice-name"]) == voices[1]["name"]
        assert c.get("/api/tts/0?voice=not-in-the-list").status_code == 400
        assert c.get("/api/tts/9999").status_code == 404
        assert c.get("/api/health").json()["tts"] is True


def test_spelling_variants_of_a_sound_are_not_errors():
    # the multilingual vocab spells one sound several ways; the model saying t̪ / ɾ / æ for t / r / a is not a mistake
    syms = SYMS + ["t", "t̪", "tː", "r", "ɾ", "æ", "b"]
    vocab = {s: i for i, s in enumerate(syms)}
    V = len(syms)

    def row(sym, p):
        r = np.full(V, (1 - p) / (V - 1))
        r[vocab[sym]] = p
        return r

    rows = [row("<pad>", 0.95)] * 2
    for ph in ["m", "æ", "ɾ", "ħ", "æ", "b", "æ"]:   # مرحبا as the model may spell it
        rows += [row(ph, 0.9)] * 3 + [row("<pad>", 0.95)] * 2
    phrase = {"variants": [("مرحبا", "m a r ħ a b a")]}
    r = Scorer(lambda wav: np.log(np.array(rows)), Vocab(vocab)).score(None, phrase)
    assert r["score"] >= 95 and not r["unrelated"] and not r["tips"]
    import unicodedata
    assert unicodedata.normalize("NFC", r["heard_tr"]) == unicodedata.normalize("NFC", "מַרְחַבַּא")
    assert r["words"][0]["heard_tr"] == r["heard_tr"]


def test_heard_is_reported_per_word_in_hebrew():
    # ħ said as χ: the second word shows the ח' the model heard
    r = scorer_for(emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])).score(None, TOILET)
    from app.translit import to_hebrew
    assert r["words"][0]["heard_tr"] == r["words"][0]["tr"]
    assert [w["heard_tr"] for w in r["words"]] == to_hebrew("w eː n | i l x a m m aː m")
    assert r["words"][1]["tr"] != r["words"][1]["heard_tr"]


def test_day2_grammar_reaches_every_pronoun_and_negation():
    from app.grammar import PRONOUNS, SENTENCES
    # day 2 has no tab on the page, so the bank is read directly, in the page's shape
    day2 = [{**p, "id": i, "ar": p["variants"][0][0]} for i, p in enumerate(PHRASES) if p["cat"] == "day2"]
    listed = [p for p in day2 if p["listed"]]
    assert len(listed) == len(SENTENCES) and not any(p["neg"] for p in listed)
    for p in listed:  # each taught sentence has its negation and the other seven subjects
        same = [q for q in day2 if q["grp"] == p["grp"]]
        assert len(same) == 2 * len(PRONOUNS)
        neg = next(q for q in same if q["pron"] == p["pron"] and q["neg"])
        assert " לא " in neg["he"] and " ما " in neg["ar"] and " מַא " in neg["tr"]
    she = next(p for p in day2 if p["he"] == "היא לא פתחה את הדלת ולא אכלה עם אחמד")
    assert she["ar"] == "هيّ ما فتحت الباب وما اكلت مع احمد"
    assert PHRASES[she["id"]]["variants"][0][1] == "h i j j e | m a | f a t ħ a t | i l b aː b | u m a | ʔ a k l a t | m a ʕ | ʔ a ħ m a d"


def test_hebrew_prompt_audio_uses_requested_engine_and_falls_back(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import prompt_tts
    from app.main import create_app

    said = []

    def broken(text):
        raise OSError("blocked")

    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.setitem(prompt_tts._SYNTH, "edge", lambda text: said.append(("edge", text)) or b"EDGE")
    monkeypatch.setitem(prompt_tts._SYNTH, "gtts", broken)
    pid = PHRASES.index(find("מה שלומך? (לגבר)"))
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:
        r = c.get(f"/api/prompt-audio/{pid}?engine=edge")
        assert r.status_code == 200 and r.content == b"EDGE" and r.headers["x-tts-engine"] == "edge"
        assert said == [("edge", "מה שלומך?")]                       # the bracketed note is not read out
        r = c.get(f"/api/prompt-audio/{pid}?engine=gtts")             # gTTS down: served by the other engine
        assert r.status_code == 200 and r.headers["x-tts-engine"] == "edge" and len(said) == 1  # ... from cache
        assert c.get(f"/api/prompt-audio/{pid}?engine=other").status_code == 400
        r = c.get("/api/say", params={"text": "ציון 82. כמעט.", "engine": "edge"})   # driving mode's spoken assessment
        assert r.status_code == 200 and said[-1] == ("edge", "ציון 82. כמעט.") and not list(tmp_path.rglob("*82*"))
        assert len(list((tmp_path / "he").glob("*.mp3"))) == 1                         # ... is not cached
        assert c.get("/api/say", params={"text": "hello"}).status_code == 400
        monkeypatch.setitem(prompt_tts._SYNTH, "edge", broken)
        assert c.get("/api/prompt-audio/0?engine=edge").status_code == 503


def test_tips_come_most_crucial_first():
    # ħ badly wrong (χ), ʕ only swallowed into the vowel... both flagged: the worse sound leads
    phrase = {"variants": [("ساعة الحمام", "s aː ʕ a | i l ħ a m aː m")]}
    r = scorer_for(emissions(["s", "aː", "ʔ", "a", "i", "l", "χ", "a", "m", "aː", "m"])).score(None, phrase)
    assert len(r["tips"]) == 2
    assert [t["score"] for t in r["tips"]] == sorted(t["score"] for t in r["tips"])
    assert all("idx" not in t for t in r["tips"])



def test_raised_a_is_a_near_vowel():
    # اكلتي said with e for a, as the voices do: not marked down
    phrase = {"variants": [("اكلتي", "ʔ a k a l t i")]}
    syms = SYMS + ["t"]
    vocab = {s: i for i, s in enumerate(syms)}
    rows = [np.full(len(syms), 0.05 / (len(syms) - 1)) for _ in range(2)]
    for r in rows:
        r[0] = 0.95
    for ph in ["ʔ", "e", "k", "e", "l", "t", "i"]:
        for p, sym in [(0.9, ph)] * 3 + [(0.95, "<pad>")] * 2:
            r = np.full(len(syms), (1 - p) / (len(syms) - 1)); r[vocab[sym]] = p
            rows.append(r)
    r = Scorer(lambda wav: np.log(np.array(rows)), Vocab(vocab)).score(None, phrase)
    assert r["score"] >= 90 and not r["tips"]


def test_article_vowel_after_a_vowel_may_be_dropped():
    phrase = {"variants": [("على الحمام", "ʕ a l a | i l ħ a m m aː m")]}
    r = scorer_for(emissions(["ʕ", "a", "l", "a", "l", "ħ", "a", "m", "aː", "m"])).score(None, phrase)
    assert r["score"] >= 95 and not r["tips"] and not any(i["missing"] for i in r["issues"])
    # ... but not after a consonant: وين الحمام without the i is still noted
    r = scorer_for(emissions(["w", "eː", "n", "l", "ħ", "a", "m", "aː", "m"])).score(None, TOILET)
    assert any(i["missing"] for i in r["issues"])


ALA = {"variants": [("على الحمام", "ʕ a l a | i l ħ a m m aː m")]}
MISHEARD = ["ʔ", "a", "l", "a", "i", "l", "ħ", "a", "m", "aː", "m"]   # the model's ʕ in على: a glottal stop


def test_native_reference_forgives_what_the_model_mishears():
    s = scorer_for(emissions(MISHEARD))
    plain = s.score(None, ALA)
    assert [t["key"] for t in plain["tips"]] == ["ʕ>ʔ"] and plain["words"][0]["heard_tr"] != plain["words"][0]["tr"]
    ref = s.reference(None, ALA)                       # the native voice is heard the same way
    r = s.score(None, ALA, [ref])
    assert r["score"] == 100 and not r["tips"] and not r["issues"] and r["references"] == 1
    assert r["words"][0]["heard_tr"] == r["words"][0]["tr"]


def test_native_reference_keeps_real_errors():
    ref = scorer_for(emissions(MISHEARD)).reference(None, ALA)
    # the learner also says χ for ħ, which the voice did not
    r = scorer_for(emissions(["ʔ", "a", "l", "a", "i", "l", "χ", "a", "m", "aː", "m"])).score(None, ALA, [ref])
    assert [t["key"] for t in r["tips"]] == ["ħ>x"] and r["score"] < 85
    # a reference in which the model does not recognise the phrase is not a baseline
    assert scorer_for(emissions(["ʃ", "e", "k", "e", "d"])).reference(None, ALA) is None
    assert scorer_for(emissions(["ʕ", "χ", "l", "χ", "i", "l", "χ", "a", "χ", "aː", "χ"])).reference(None, ALA) is None


def test_references_use_one_generated_voice_and_any_cached_one():
    from app.reference import References
    from app.tts import TTSUnavailable

    calls = []

    class FakeTTS:
        down = False

        def cached(self, text, voice):
            return voice == "played"

        def wav(self, text, voice=""):
            calls.append(voice)
            if self.down:
                raise TTSUnavailable("down")
            return b"wav", voice

    import app.reference as reference
    tts, s = FakeTTS(), scorer_for(emissions(MISHEARD))
    orig, reference.decode_audio = reference.decode_audio, lambda data: None
    try:
        refs = References(s, tts, ["first", "never-played", "played"])
        assert len(refs.get(7, ALA)) == 2 and calls == ["first", "played"]
        assert len(refs.get(7, ALA)) == 2 and len(calls) == 2            # kept in memory
        tts.down = True
        assert refs.get(8, ALA) == [] and refs.get(8, ALA) == [] and len(calls) == 4   # failures are not retried at once
    finally:
        reference.decode_audio = orig


def test_workbook_lesson_1():
    from app.phrases import CATEGORIES, LESSONS, public_phrases

    assert [l["id"] for l in LESSONS if not l.get("soon")] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "1"] == ["l1-pron", "l1-verbs", "l1-past", "l1-say", "l1-read", "l1-hw"]
    hw = CATEGORIES[5]["write"]   # the workbook's homework: a task and five sentences to write, with nothing to say
    assert hw["lines"] == 5 and hw["task"].startswith("כתבו חמישה משפטים") and "מִין - מי" in hw["task"]
    pub = public_phrases()
    count = lambda cat: sum(p["cat"] == cat for p in pub)
    assert [count(c) for c in ("l1-pron", "l1-verbs", "l1-past", "l1-say", "l1-read")] == [8, 4, 32, 10, 22]
    assert all(p["cat"] in {c["id"] for c in CATEGORIES} for p in pub)
    assert all(p["read"] == (p["cat"] == "l1-read") and "#" not in p["ar"] for p in pub if p["cat"].startswith("l1-"))
    assert all("seq" not in p for p in pub if p["cat"] == "l1-past")   # conjugations are drilled in random order
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l1-pron", "אנחנו"]["tr"] == "אִחְנַא"
    assert by["l1-verbs", "ישב (עבר, עתיד)"]["tr"] == "קַעַד, יֻקְעֹד"
    assert by["l1-past", "היא שאלה"]["tr"] == "הִיֵّ סַאְלַת"
    # the prompt voice gets pointed Hebrew (פתח is otherwise read "petach"); the page shows it plain
    assert by["l1-verbs", "פתח (עבר, עתיד)"]["he_say"] == "פָּתַח"
    assert by["l1-past", "את ישבת"]["he_say"] == "אַתְּ יָשַׁבְתְּ" and by["l1-pron", "הם / הן"]["he_say"] == "הֵם"
    assert by["l1-past", "אתה פתחת"]["variants"] == [("انت فتحت", "ʔ i n t e | f a t a ħ t"), ("انت فتحت", "ʔ i n t a | f a t a ħ t")]
    p = next(p for p in PHRASES if p["cat"] == "l1-read" and p["seq"] == 11)
    assert p["tr"] == "אֵלְמְעַלֵّם קַעַד עַלַא אֵ(ל)טַّאוְלֵה וּסַאַל-נַא סֻאַאל."
    ar, ipa = p["variants"][0]
    assert ar.split()[-2] == "وسألنا" and ipa.split(" | ")[-2] == "u s a ʔ a l n a"
    assert any(i.startswith("i l m ʕ a l l i m | q a ʕ a d") for _, i in p["variants"])   # rural ق accepted


def test_levels_plan_every_lesson():
    from app.phrases import CATEGORIES, LESSONS, LEVELS
    from app.workbook import LESSONS as WRITTEN, LEVELS as PLAN, lesson_id

    # every lesson names its level, and each level lists all of its planned lessons, the unwritten ones as soon
    assert [v["id"] for v in LEVELS] == ["1", "2"] and len(LESSONS) == 20
    assert all(isinstance(key, tuple) and key[0] in PLAN for key in WRITTEN)
    assert [(l["level"], l["n"]) for l in LESSONS] == [(str(v), n) for v in (1, 2) for n in range(1, 11)]
    assert [l["id"] for l in LESSONS if l["level"] == "2"][:2] == ["2.1", "2.2"]   # level 1 keeps "1", "2": attempts name them
    assert {l["id"] for l in LESSONS if not l.get("soon")} == {lesson_id(*k) for k in WRITTEN} == {c["lesson"] for c in CATEGORIES}
    assert lesson_id(2, 1) == "2.1" and lesson_id(1, 3) == "3"


def test_workbook_lesson_2():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l2-dual", "l2-family", "l2-poss", "l2-say", "l2-say2", "l2-read", "l2-talk", "l2-meet"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "2"] == cats + ["l2-hw"]   # the lesson ends with its written homework
    hw = next(c for c in CATEGORIES if c["id"] == "l2-hw")["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השני" in hw["task"] and "קַעַד / יֻקְעֹד – ישב" in hw["task"]
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [17, 24, 48, 23, 25, 13, 27, 24]
    assert all(p["read"] == (p["cat"] == "l2-read") and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2-"))
    assert all("seq" not in p for p in pub if p["cat"] == "l2-poss")   # drilled in random order, like the conjugations
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2-dual", "שולחן, שני שולחנות"]["tr"] == "טַאוְלֵה, טַאוְלְתֵין"   # singular and dual said together
    # כינויי הקניין: the stem changes before a consonant suffix, and שלך is pointed for the prompt voice
    assert by["l2-poss", "השולחן שלי"]["tr"] == "טַאוְלְת-י" and by["l2-poss", "השולחן שלנו"]["tr"] == "טַאוְלֵת-נַא"
    you = by["l2-poss", "הבת שלך (לאישה)"]
    assert you["tr"] == "בִּנְתֵ-כּ" and you["he_say"] == "הבת שֶׁלָּךְ" and you["variants"] == [("بنتك", "b i n t i k")]
    assert by["l2-poss", "הכלב שלכם"]["variants"] == [("كلبكم", "k a l b k o m"), ("كلبكم", "k a l b k u")]
    assert ("جدّي", "s iː d i") in by["l2-poss", "הסבא שלי"]["variants"]   # the other word for סבא is accepted
    # דוד is pointed for the prompt voice, which otherwise reads it "David"; דודה is read correctly as it is
    assert by["l2-family", "דוד (מצד האב)"]["he_say"] == "דּוֹד (מצד האב)" and "he_say" not in by["l2-family", "דודה (מצד האב)"]
    assert by["l2-say", "איפה כלבכם? הוא אצל דודי (מצד אמא)."]["he_say"] == "איפה כלבכם? הוא אצל דּוֹדִי (מצד אמא)."
    assert by["l2-talk", "ה' ישמור אותם!"]["he_say"] == "אלוהים ישמור אותם!"   # the voice says אלוהים for ה'
    p = next(p for p in PHRASES if p["cat"] == "l2-talk" and p["seq"] == 2)
    assert p["tr"] == "וּעַלֵיכֹּם אֵ(ל)סַّלַאםֻ" and p["variants"][0][1] == "u ʕ a l eː k u m | i s s a l aː m u"
    p = next(p for p in PHRASES if p["cat"] == "l2-say" and p["seq"] == 22)
    assert p["tr"].endswith("אִמّ-י סַאְלַת-וֹ: לֵיש פַתַחְת-וֹ?")
    # חברי is pointed for the prompt voice, which otherwise reads it "chaverei" (my friends)
    assert by["l2-say", "אני ישבתי עם חברי לפני יומיים ואכלנו פיצה בסלון."]["he_say"].split()[3] == "חֲבֵרִי"
    assert "וַחֲבֵרִי" in by["l2-say", "מי ישב בבית השיח'? אני וחברי ישבנו בבית השיח'."]["he_say"]
    assert by["l2-dual", "שבוע, שבועיים"]["he_say"] == "שָׁבוּעַ, שבועיים"   # not "shvua"


def test_workbook_lesson_3():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l3-verbs", "l3-past", "l3-read", "l3-say", "l3-say2", "l3-nums", "l3-numread", "l3-numsay", "l3-story"]
    mine = [c for c in CATEGORIES if c["lesson"] == "3"]
    assert [c["id"] for c in mine] == cats + ["l3-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השלישי" in hw["task"] and "נַקַל / יֻנְקֹל – עבר (דירה, עבודה)" in hw["task"]
    # the story is laid out like the conversation, but told in the student's own voice: it names none
    assert mine[-2]["story"] and "voices" not in mine[-2] and CATEGORIES[[c["id"] for c in CATEGORIES].index("l2-talk")]["voices"] == ["אמיר", "ראאף"]
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [4, 32, 26, 12, 14, 37, 11, 11, 10]
    assert all(p["read"] == (p["cat"] in ("l3-read", "l3-numread")) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l3-"))
    assert all("seq" not in p for p in pub if p["cat"] == "l3-past")
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # a verb is said in the past and the future together; עבר alone would read as "past", so it names what is moved
    assert by["l3-verbs", "בישל (עבר, עתיד)"]["tr"] == "טַבַּח', יֻטְבֹּח'" and by["l3-verbs", "בישל (עבר, עתיד)"]["he_say"] == "בִּישֵּׁל"
    assert by["l3-verbs", "עבר דירה (עבר, עתיד)"]["variants"][:2] == [("نقل، ينقل", "n a ʔ a l | j u n ʔ u l"), ("نقل، ينقل", "n a ʔ a l | j u n q u l")]
    assert by["l3-past", "היא שחתה"]["tr"] == "הִיֵّ סַבְּחַת" and by["l3-past", "אנחנו בישלנו"]["tr"] == "אִחְנַא טַבַּחְ'נַא"
    # a number shows its digits; the prompt voice says the Hebrew word only
    assert by["l3-nums", "שבע עשרה (17)"]["tr"] == "סַבְּעְתַעַש" and by["l3-nums", "שבע עשרה (17)"]["he_say"] == "שבע עשרה"
    assert by["l3-nums", "עשרת אלפים (10,000)"]["tr"] == "עַשְרַת אַלַאף" and by["l3-nums", "שלוש מאות (300)"]["tr"] == "תַלַאת מִיֵّה"
    p = next(p for p in PHRASES if p["cat"] == "l3-read" and p["seq"] == 9)
    assert "הִיֵّ טַבְּחַ'ת-לוֹ מַקְלוּבֵּה טַיְّבֵּה." in p["tr"]
    p = next(p for p in PHRASES if p["cat"] == "l3-numsay" and p["seq"] == 6)
    assert p["tr"] == "עִנְד-נַא חְדַעְשַר עַםّ וּסַבְּעְתַעְשַר עַמֵّה."   # 11-19 gain a ר before a noun, which stays singular
    p = next(p for p in PHRASES if p["cat"] == "l3-story" and p["seq"] == 1)
    assert p["tr"] == "צַאחִבְּת-י רוּבַּה נַקְלַת עַלַא בֵּית גְ'דִיד פִי חַארֵת-נַא."


def test_workbook_lesson_4():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l4-verbs", "l4-past", "l4-read", "l4-read2", "l4-say", "l4-say2", "l4-story", "l4-meet", "l4-meetread"]
    mine = [c for c in CATEGORIES if c["lesson"] == "4"]
    assert [c["id"] for c in mine] == cats + ["l4-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור הרביעי" in hw["task"] and "אִשְתַרַא / יִשְתְרִי – קנה" in hw["task"]
    story = mine[cats.index("l4-story")]
    assert story["story"] and "voices" not in story
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [4, 32, 12, 14, 12, 12, 20, 18, 6]
    reads = ("l4-read", "l4-read2", "l4-meetread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l4-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # verbs that end in ا: the ا becomes ي before the endings of "I", "you" and "we"
    assert by["l4-verbs", "בא (עבר, עתיד)"]["tr"] == "אַגַ'א, יִגִ'י" and by["l4-verbs", "נתן (עבר, עתיד)"]["tr"] == "אַעְטַא, יֵעַטִי"
    assert by["l4-past", "אנחנו דיברנו"]["tr"] == "אִחְנַא חַכֵּינַא" and by["l4-past", "היא קנתה"]["tr"] == "הִיֵّ אִשְתַרַת"
    assert by["l4-past", "הם באו"]["variants"][0] == ("همّ اجو", "h u m m e | ʔ a dʒ u")
    p = next(p for p in PHRASES if p["cat"] == "l4-read" and p["seq"] == 4)
    assert p["tr"].startswith("גַ'אר-י אַגַ'א וּאַעַטַא-נִי תִסְעַה וּתַמַאנִין שֵיכֵּל")
    p = next(p for p in PHRASES if p["cat"] == "l4-story" and p["seq"] == 1)
    assert p["tr"] == "קַבֵּל אֻסְבּוּע אַגַ'א עַלַא חַארֵת-נַא גַ'אר גְ'דִיד."
    assert by["l4-story", "הוא אמר: \"ה' ישמור אותם!\""]["he_say"] == "הוא אמר: \"אלוהים ישמור אותם!\""
    assert by["l4-meet", "אני רוצה קפה עם סוכר, תודה!"]["tr"] == "בִּדّ-י קַהְוֵה מַע סֻכַּّר, שֻכְּרַן!"


def test_workbook_lesson_5():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's order: more of lesson 4's story, the negation, then the new verbs and the market story
    cats = ["l5-meet", "l5-meetread", "l5-neg", "l5-neg2", "l5-verbs", "l5-past", "l5-read", "l5-story", "l5-aread",
            "l5-say", "l5-say2", "l5-say3"]
    mine = [c for c in CATEGORIES if c["lesson"] == "5"]
    assert [c["id"] for c in mine] == cats + ["l5-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור החמישי" in hw["task"] and "זַאר / יִזוּר – ביקר" in hw["task"]
    story = mine[cats.index("l5-story")]
    assert story["story"] and "voices" not in story
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [13, 12, 13, 13, 4, 32, 23, 15, 12, 13, 11, 12]
    reads = ("l5-meetread", "l5-neg", "l5-neg2", "l5-read", "l5-aread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l5-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l5-verbs", "היה (עבר, עתיד)"]["tr"] == "כַּאן, יִכּוּן" and by["l5-past", "אנחנו היינו"]["tr"] == "אִחְנַא כֻּנַّא"
    assert ("انا قلت", "ʔ a n a | q u l t") in by["l5-past", "אני אמרתי"]["variants"]   # rural ق accepted
    # the negation: مش before a noun or an adjective, ما before a verb, which may also end in ش
    p = next(p for p in PHRASES if p["cat"] == "l5-neg" and p["seq"] == 9)
    assert p["tr"].startswith("אַנַא מַא קַעַדְתֵש עִנְד גַ'אר-י") and p["he"].startswith("אני לא ישבתי")
    p = next(p for p in PHRASES if p["cat"] == "l5-neg2" and p["seq"] == 8)
    assert p["tr"].endswith("קֻלְתִ-לّוֹ: וַאללַّה מַא אִשְתַרֵינַא אִשִי!")   # والله is one word, not "and" + الله
    p = next(p for p in PHRASES if p["cat"] == "l5-story" and p["seq"] == 5)
    assert p["tr"].startswith("לַמַّא וְצִלְנַא עַלַא אֵ(ל)סّוּק") and p["variants"][0][1].split(" | ")[1] == "w sˤ i l n a"
    assert by["l5-story", "ה' ייתן לך בריאות!"]["he_say"] == "אלוהים ייתן לך בריאות!"


def test_workbook_lesson_6():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l6-fut", "l6-futsay", "l6-verbs", "l6-past", "l6-read", "l6-read2", "l6-story", "l6-meet", "l6-meetread"]
    mine = [c for c in CATEGORIES if c["lesson"] == "6"]
    assert [c["id"] for c in mine] == cats + ["l6-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השישי" in hw["task"] and "פעם אחת בעבר ופעם אחת בעתיד" in hw["task"]
    story = mine[cats.index("l6-story")]
    assert story["story"] and "voices" not in story
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [24, 13, 5, 40, 11, 13, 12, 12, 12]
    reads = ("l6-read", "l6-read2", "l6-meetread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l6-"))
    assert all("seq" not in p for p in pub if p["cat"] in ("l6-fut", "l6-past"))   # conjugations are drilled in random order
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # the future: the Hebrew letters אית"ן on the future's base form, and אַ for "I" whatever the base form's vowel
    assert by["l6-fut", "אני אשב"]["tr"] == "אַנַא אַקְעֹד" and by["l6-fut", "אנחנו נשב"]["tr"] == "אִחְנַא נֻקְעֹד"
    assert by["l6-fut", "את תבקרי"]["tr"] == "אִנְתִי תְזוּרִי" and by["l6-fut", "הם ישמעו"]["he_say"] == "הֵם יִשְׁמְעוּ"
    # فات is "entered" like lesson 3's دخل, so its card says more; اخد is the homework's fifth verb
    assert by["l6-verbs", "נכנס ועבר (עבר, עתיד)"]["tr"] == "פַאת, יִפוּת" and by["l6-verbs", "לקח (עבר, עתיד)"]["tr"] == "אַחַ'ד, יֹאחֵ'ד"
    assert by["l6-past", "אתם נכנסתם"]["tr"] == "אִנְתוּ פֻתّוּ" and by["l6-past", "אני ראיתי"]["tr"] == "אַנַא שֻפֵת"
    p = next(p for p in PHRASES if p["cat"] == "l6-futsay" and p["seq"] == 3)
    assert p["tr"] == "אִחְנַא סַבַּחְנַא פִי אֵלְבַּחַר, בֻּכְּרַא נִסְבַּח פִי אֵלְבִּרְכֵּה."
    p = next(p for p in PHRASES if p["cat"] == "l6-story" and p["seq"] == 9)
    assert p["tr"] == "לַוְ כַּאן עִנְד-י מַרַה זַיֵّ-כּ..." and p["variants"][0][0].endswith("زيّك...")


def test_workbook_lesson_7():
    from app.phrases import CATEGORIES, public_phrases

    # a review lesson: no new verbs and no story
    cats = ["l7-fem", "l7-dual", "l7-nums", "l7-count", "l7-verbs", "l7-read", "l7-say", "l7-say2"]
    mine = [c for c in CATEGORIES if c["lesson"] == "7"]
    assert [c["id"] for c in mine] == cats + ["l7-hw"] and not any(c.get("story") for c in mine)
    assert mine[0]["task"] == "אמרו בערבית מדוברת: בזכר ואז בנקבה" and mine[1]["task"].endswith("ביחיד ואז בזוגי")
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "בשיעור השביעי" in hw["task"] and "פעם אחת בעבר ופעם אחת בעתיד" in hw["task"]
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [8, 13, 12, 15, 21, 23, 14, 13]
    assert all(p["read"] == (p["cat"] == "l7-read") and "#" not in p["ar"] for p in pub if p["cat"].startswith("l7-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l7-fem", "רווק, רווקה"]["tr"] == "עַזַّאבִּי, עַזַّאבִּיֵّה" and by["l7-dual", "מיטה, שתי מיטות"]["tr"] == "תַחְ'ת, תַחְ'תֵין"
    assert by["l7-nums", "מאה שבעים וחמש (175)"]["tr"] == "מִיֵّה חַ'מְסֵה וּסַבְּעִין"   # units before tens
    # 3-10: the noun in the plural and the number without its ة; from 11: the noun in the singular, and 11-19 with a ר
    assert by["l7-count", "שבעה ילדים"]["tr"] == "סַבְּע אַוְלַאד" and by["l7-count", "שתים עשרה בנות"]["tr"] == "תְנַעְשַר בִּנְת"
    # every verb of lessons 1-6, each still said in the past and the future together
    verbs = [p for p in PHRASES if p["cat"] == "l7-verbs"]
    assert [p["tr"] for p in verbs][:2] == ["פַתַח, יִפְתַח", "אַכַּל, יֹאכֹּל"] and verbs[-1]["tr"] == "פַאת, יִפוּת"
    assert all(" | " in p["variants"][0][1] and p["he"].endswith("(עבר, עתיד)") for p in verbs)


def test_workbook_lesson_8():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l8-howmany", "l8-verbs", "l8-past", "l8-read", "l8-read2", "l8-read3", "l8-colors", "l8-colread", "l8-story",
            "l8-colsay", "l8-colsay2"]
    mine = [c for c in CATEGORIES if c["lesson"] == "8"]
    assert [c["id"] for c in mine] == cats + ["l8-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השמיני" in hw["task"] and "פעלים וצבעים" in hw["task"]
    song = mine[cats.index("l8-story")]
    assert song["name"] == "שיר הצבעים" and song["story"] and "voices" not in song
    assert mine[cats.index("l8-colors")]["task"].endswith("בזכר ואז בנקבה")
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [13, 5, 40, 12, 12, 13, 17, 13, 25, 10, 12]
    reads = ("l8-read", "l8-read2", "l8-read3", "l8-colread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l8-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # גזרת פִעֵל: the first vowel is lost before the endings of "I", "you" and "we"
    assert by["l8-verbs", "הבין (עבר, עתיד)"]["tr"] == "פִהֵם, יִפְהַם" and by["l8-verbs", "יצא ועלה (עבר, עתיד)"]["tr"] == "טִלֵע, יִטְלַע"
    assert by["l8-past", "אני שתיתי"]["tr"] == "אַנַא שְרִבֵּת" and by["l8-past", "היא חזרה"]["tr"] == "הִיֵّ רִגְ'עַת"
    # a colour is said in the masculine and the feminine together
    assert by["l8-colors", "לבן, לבנה"]["tr"] == "אַבְּיַדֿ, בֵּידַֿא" and by["l8-colors", "לבן, לבנה"]["variants"][0][1] == "ʔ a b j a dˤ | b eː dˤ a"
    assert by["l8-colors", "אפור, אפורה (מהמילה אפר)"]["tr"] == "רַמַאדִי, רַמַאדִיֵّה"
    # how many: اكم and a singular noun for what is counted, قدّيش for what is not
    p = next(p for p in PHRASES if p["cat"] == "l8-howmany" and p["seq"] == 9)
    assert p["tr"].startswith("קַדֵّיש מַצַארִי בִּדّ-וֹ?")
    p = next(p for p in PHRASES if p["cat"] == "l8-howmany" and p["seq"] == 2)
    assert p["tr"].startswith("אַכַּם וַאחַד כַּאן פִי אֵ(ל)סַّיַّארַה?")
    assert by["l8-story", "אני בשמיים נמצא"]["tr"] == "אַנַא פִי אֵ(ל)סַّמַאִי מַוְג'וּד"


def test_workbook_lesson_9():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l9-story", "l9-aread", "l9-asay", "l9-verbs", "l9-past", "l9-want", "l9-read", "l9-read2", "l9-say", "l9-mix",
            "l9-mixread"]
    mine = [c for c in CATEGORIES if c["lesson"] == "9"]
    assert [c["id"] for c in mine] == cats + ["l9-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "השיעור התשיעי" in hw["task"] and "אחד בעבר ואחד בעתיד" in hw["task"]
    story = mine[cats.index("l9-story")]
    assert story["name"] == "סיפור הקפה" and story["story"] and "voices" not in story
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [15, 13, 12, 2, 16, 24, 13, 13, 12, 6, 6]
    reads = ("l9-aread", "l9-read", "l9-read2", "l9-mixread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l9-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l9-verbs", "הודה (עבר, עתיד)"]["tr"] == "שַכַּר, יֻשְכֹּר"
    # "to lunch" is one verb in Arabic: the Hebrew prompt names the meal, once
    assert by["l9-verbs", "אכל צהריים (עבר, עתיד)"]["tr"] == "תְעַ'דַّא, יִתְעַ'דַّא"
    assert by["l9-past", "אנחנו אכלנו צהריים"]["tr"] == "אִחְנַא תְעַ'דֵّינַא"
    # wanting: بدّ with the person's suffix, then the verb in the future (Arabic has no infinitive)
    assert by["l9-want", "אני רוצה לצאת"]["tr"] == "בִּדّ-י אַטְלַע" and by["l9-want", "את רוצה לצאת"]["tr"] == "בִּדֵّ-כּ תִטְלַעִי"
    assert by["l9-want", "הם רוצים לאכול צהריים"]["tr"] == "בִּדّ-הֹם יִתְעַ'דּוּ"
    assert by["l9-want", "אתה רוצה לשמוע"]["variants"][0][1] == "b i d d a k | t i s m a ʕ"
    p = next(p for p in PHRASES if p["cat"] == "l9-story" and p["seq"] == 8)
    assert p["tr"].startswith("שְרִבְּנַא קַהְוֵה סַאדַא")


def test_workbook_lesson_10():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l10-adj", "l10-compread", "l10-compread2", "l10-verbs", "l10-past", "l10-read", "l10-read2", "l10-say", "l10-say2",
            "l10-dem", "l10-story", "l10-phrases"]
    mine = [c for c in CATEGORIES if c["lesson"] == "10"]
    assert [c["id"] for c in mine] == cats + ["l10-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "השיעור העשירי" in hw["task"] and "אחד בעבר ואחד בעתיד" in hw["task"]
    story = mine[cats.index("l10-story")]
    assert story["name"] == "סיפור דודי סעיד" and story["story"] and "voices" not in story
    assert mine[0]["unit"] == "תואר" and mine[0]["task"].endswith("ערך היתרון שלו")
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [33, 8, 8, 5, 40, 12, 9, 12, 9, 12, 25, 19]
    reads = ("l10-compread", "l10-compread2", "l10-read", "l10-read2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l10-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # an adjective is said with its comparative (אַפְעַל)
    assert by["l10-adj", "גדול, יותר גדול"]["tr"] == "כְּבִּיר, אַכְּבַּר"
    assert by["l10-adj", "מתוק, יותר מתוק"]["tr"] == "חִלוּ, אַחְלַא" and by["l10-adj", "חשוב, יותר חשוב"]["tr"] == "מֻהִםّ, אַהַםّ"
    assert by["l10-adj", "טעים, יותר טעים (לאוכל בלבד)"]["tr"] == "זַאכִּי, אַזְכַּא"
    assert by["l10-adj", "נקי, יותר נקי"]["variants"][0][1] == "n dˤ iː f | ʔ a n dˤ a f"
    # גזרת פִעֵל again
    assert by["l10-verbs", "שיחק (עבר, עתיד)"]["tr"] == "לִעֵבּ, יִלְעַבּ" and by["l10-verbs", "הגיע (עבר, עתיד)"]["tr"] == "וִצֵל, יוֹצַל"
    assert by["l10-past", "אני שיחקתי"]["tr"] == "אַנַא לְעִבֵּת" and by["l10-past", "היא צחקה"]["tr"] == "הִיֵّ דִֿחְכַּת"
    assert by["l10-past", "אנחנו הגענו"]["tr"] == "אִחְנַא וְצִלְנַא"
    assert by["l10-dem", "אלה"]["tr"] == "הַדוֹל" and by["l10-dem", "הפעם הזאת"]["tr"] == "הַאדִי אֵלְמַרַّה"
    p = next(p for p in PHRASES if p["cat"] == "l10-story" and p["seq"] == 8)
    assert p["tr"].startswith("הוּّ קַאלִّ-י: וַלַא יְהִמֵّ-כּ")


def test_sign_in_guards_the_practice_endpoints():
    from fastapi.testclient import TestClient

    from app.main import create_app

    VERB = next(p for p in PHRASES if p["cat"] == "l1-verbs")

    class FakeAuth:
        url, key, teachers = "https://proj.supabase.co", "sb_publishable_x", frozenset()

        def __init__(self):
            self.rows, self.cls, self.vids, self.txts, self.days, self.wrote = [], [], [], [], {"dailies": [], "library": []}, {}

        def writings(self, token):
            return {t: lines for (u, t), lines in self.wrote.items() if u == "u1"}

        def save_writing(self, token, user_id, topic, lines):
            self.wrote[user_id, topic] = lines

        def all_writings(self):
            return [{"user_id": u, "topic": t, "lines": lines} for (u, t), lines in self.wrote.items()]

        def classes(self, fresh=False):
            return self.cls

        def save_class(self, id, name, emails, nicknames, lessons, homeworks=(), dailies=None):
            row = {"name": name, "emails": emails, "nicknames": nicknames, "lessons": lessons, "homeworks": list(homeworks),
                   **({} if dailies is None else {"dailies": list(dailies)})}
            if id is None:
                self.cls.insert(0, {"id": len(self.cls) + 1, **row})
            elif c := next((c for c in self.cls if c["id"] == id), None):
                c.update(row)
            else:
                raise KeyError(id)

        def delete_class(self, id):
            self.cls = [c for c in self.cls if c["id"] != id]

        def videos(self, fresh=False):
            return self.vids

        def level_videos(self, table, fresh=False):
            return self.days[table]

        def add_level_video(self, table, level, title, url):
            self.days[table].append({"id": len(self.days[table]) + 1, "level": level, "title": title, "url": url})

        def delete_level_video(self, table, id):
            self.days[table] = [v for v in self.days[table] if v["id"] != id]

        def add_video(self, lesson, title, url, pos, ord):
            self.vids.append({"id": len(self.vids) + 1, "lesson": lesson, "title": title, "url": url, "pos": pos, "ord": ord})

        def place(self, places):
            for table, id, pos, ord in places:
                next(v for v in {"videos": self.vids, "texts": self.txts}[table] if v["id"] == id).update(pos=pos, ord=ord)

        def texts(self, fresh=False):
            return self.txts

        def add_text(self, lesson, title, voices, lines, pos, ord):
            self.txts.append({"id": len(self.txts) + 1, "lesson": lesson, "title": title, "voices": voices, "lines": lines, "pos": pos, "ord": ord})

        def delete_text(self, id):
            self.txts = [t for t in self.txts if t["id"] != id]

        def delete_video(self, id):
            self.vids = [v for v in self.vids if v["id"] != id]

        def user(self, token):
            return {"id": "u1", "email": "A@b.c"} if token == "good" else None

        def record(self, token, row):
            self.rows.append((token, row))

        def attempts(self, token):   # two tries of one sentence on one Israeli day (across midnight UTC), one unlisted sentence
            return [{"cat": VERB["cat"], "phrase": VERB["he"], "score": 62, "created_at": "2026-10-08T21:30:00.5+00:00"},
                    {"cat": VERB["cat"], "phrase": VERB["he"], "score": 88, "created_at": "2026-10-09T10:00:00+00:00"},
                    {"cat": TOILET["cat"], "phrase": TOILET["he"], "score": 90, "created_at": "2026-10-01T10:00:00.123456+00:00"}]

        def users(self):   # the teacher, a student, and an account that is not on the list
            return [{"id": "u1", "email": "A@b.c", "name": "Alef"}, {"id": "u2", "email": "s@b.c", "name": ""},
                    {"id": "u3", "email": "out@b.c", "name": ""}]

        def all_attempts(self):
            return [{**r, "user_id": "u2"} for r in self.attempts("")[:2]]

    with TestClient(create_app(scorer_for(emissions(["w"])), auth=FakeAuth())) as c:
        page = c.get("/").text
        assert 'AUTH_URL="https://proj.supabase.co"' in page and 'AUTH_KEY="sb_publishable_x"' in page
        assert c.get("/api/phrases").status_code == 200   # the phrase list stays public
        assert c.get("/api/prompt-audio/0").status_code == 401
        assert c.get("/api/tts/0", headers={"Authorization": "Bearer bad"}).status_code == 401
        assert c.get("/api/tts/0", headers={"Authorization": "Bearer good"}).status_code == 503   # in, but no TTS key
        good = {"Authorization": "Bearer good"}
        from app.phrases import CATEGORIES, LESSONS
        ALL = [l["id"] for l in LESSONS]
        NO_HW = {"homework": "", "due": None, "homeworks": [], "dailies": []}
        T1, T2 = (next(k["id"] for k in CATEGORIES if k["lesson"] == l) for l in ALL[:2])   # a topic of each of the first two lessons
        # written homework is kept for the student, a topic's lines in place of the ones before, and read back anywhere
        assert c.get("/api/writing").status_code == 401 and c.get("/api/writing", headers=good).json() == {"writing": {}}
        put = lambda topic, lines: c.put(f"/api/writing/{topic}", json={"lines": lines}, headers=good).status_code
        assert put("l1-hw", ["אַנַא  אַכַּלֵת ", ""]) == 200 and put("l1-hw", ["אַנַא אַכַּלֵת", "", "הִיֵّ פַתְחַת"]) == 200
        assert c.get("/api/writing", headers=good).json() == {"writing": {"l1-hw": ["אַנַא אַכַּלֵת", "", "הִיֵّ פַתְחַת"]}}
        assert put(T1, ["x"]) == 404 and put("l1-hw", ["x"] * 6) == 400 and put("l1-hw", ["x" * 301]) == 400 and put("l1-hw", "x") == 400
        assert c.put("/api/writing/l1-hw", json={"lines": ["x"]}).status_code == 401
        # no class yet: any account is let in, to every lesson
        assert c.get("/api/me", headers=good).json() == {"email": "A@b.c", "teacher": False, "classes": [], "lessons": ALL}
        assert c.get("/api/class").status_code == 401 and c.get("/api/class", headers=good).status_code == 403
        assert c.post("/api/classes", json={"name": "x", "emails": "", "lessons": ALL}, headers=good).status_code == 403   # teachers only
        assert c.get("/api/classmates", headers=good).json() == {"classes": []}
        beginners = {"id": 1, "name": "Beginners", "emails": ["other@b.c"], "nicknames": {}, "lessons": ALL[:1]}
        c.app.state.auth.cls = [beginners]   # a class without this account
        assert c.get("/api/me", headers=good).status_code == 403 and c.get("/api/tts/0", headers=good).status_code == 403
        beginners["emails"].append("a@b.c")   # case does not matter; the student studies their class's lessons
        assert c.get("/api/me", headers=good).json() == {"email": "A@b.c", "teacher": False, "lessons": ALL[:1],
                                                         "classes": [{"name": "Beginners", "lessons": ALL[:1], **NO_HW}]}
        # the class's homework reaches its students: a topic of its lessons, and the day it is due
        beginners.update(homework=T1, due="2026-10-15")
        assert c.get("/api/me", headers=good).json()["classes"] == [{"name": "Beginners", "lessons": ALL[:1], "homework": T1, "due": "2026-10-15",
                                                                             "homeworks": [{"topic": T1, "due": "2026-10-15"}], "dailies": []}]
        beginners.update(homework=T2)   # a topic of a lesson the class does not study is no homework
        assert c.get("/api/me", headers=good).json()["classes"] == [{"name": "Beginners", "lessons": ALL[:1], **NO_HW}]
        del beginners["homework"], beginners["due"]
        # in a second class too: the lessons of both, in the course's order
        c.app.state.auth.cls = [{"id": 2, "name": "Advanced", "emails": ["a@b.c"], "nicknames": {}, "lessons": ALL[1:]}, beginners]
        me = c.get("/api/me", headers=good).json()   # each class with its own lessons: the student chooses one to practise in
        assert me["lessons"] == ALL and me["classes"] == [{"name": "Advanced", "lessons": ALL[1:], **NO_HW}, {"name": "Beginners", "lessons": ALL[:1], **NO_HW}]
        c.app.state.auth.cls = [{**beginners, "lessons": []}]   # a class saved before classes had lessons has every one
        assert c.get("/api/me", headers=good).json()["lessons"] == ALL
        # a teacher is let in without a class, studies every lesson, and opens classes in the panel
        c.app.state.auth.cls, c.app.state.auth.teachers = [], frozenset({"a@b.c"})
        assert c.post("/api/classes", json={"name": "September 2026", "emails": "x@b.c", "lessons": ALL[:1]},
                      headers=good).status_code == 200
        assert c.get("/api/me", headers=good).json() == {"email": "A@b.c", "teacher": True, "classes": [], "lessons": ALL, "dailies": []}
        # the content part: a teacher adds a video to a ready lesson by its Vimeo link, kept without the sharing parameters
        READY = [l["id"] for l in LESSONS if not l.get("soon")]
        SOON = next(l["id"] for l in LESSONS if l.get("soon"))
        link = "https://vimeo.com/1233611297/d46ac373c8?share=copy&fl=sv&fe=ci"
        assert c.get("/api/videos").status_code == 401 and c.get("/api/videos", headers=good).json() == {"videos": []}
        for body, code in (({"lesson": READY[0], "title": "  פתיחה   לשיעור ", "url": link}, 200),
                           ({"lesson": READY[1], "title": "public", "url": "vimeo.com/76979871"}, 200),
                           ({"lesson": READY[0], "title": "", "url": link}, 400), ({"lesson": READY[0], "title": "x" * 81, "url": link}, 400),
                           ({"lesson": SOON, "title": "x", "url": link}, 400), ({"lesson": "no-such-lesson", "title": "x", "url": link}, 400),
                           ({"lesson": READY[0], "title": "x", "url": "https://youtube.com/watch?v=1233611297"}, 400),
                           ({"lesson": READY[0], "title": "x", "url": "https://www.youtube.com/playlist?list=PLdQw4w9WgXcQ"}, 400),
                           ({"lesson": READY[0], "title": "x", "url": "https://evil.example/watch?v=dQw4w9WgXcQ"}, 400),
                           ({"lesson": READY[0], "title": "x", "url": "https://evil.example/?u=https://vimeo.com/1233611297"}, 400),
                           ({"lesson": READY[0], "title": "x", "url": "https://vimeo.com/channels/staffpicks"}, 400)):
            r = c.post("/api/videos", json=body, headers=good)
            assert r.status_code == code, (body, r.text)
        first, second = c.get("/api/videos", headers=good).json()["videos"]
        N1 = sum(k["lesson"] == READY[0] for k in CATEGORIES)   # a new video stands after its lesson's topics
        assert first == {"id": 1, "lesson": READY[0], "title": "פתיחה לשיעור", "url": "https://vimeo.com/1233611297/d46ac373c8",
                         "embed": "https://player.vimeo.com/video/1233611297?h=d46ac373c8&dnt=1&vimeo_logo=0&title=0&byline=0&portrait=0", "pos": N1, "ord": 1}
        assert (second["url"], second["embed"]) == ("https://vimeo.com/76979871", "https://player.vimeo.com/video/76979871?dnt=1&vimeo_logo=0&title=0&byline=0&portrait=0")
        from app.main import vimeo_link, video_embed, video_link
        assert vimeo_link("https://player.vimeo.com/video/1233611297?h=d46ac373c8") == first["url"]
        # a YouTube link, in any of its forms, is kept as the watch link and played without cookies
        for yt in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s", "https://youtube.com/watch?list=PL1&v=dQw4w9WgXcQ", "youtu.be/dQw4w9WgXcQ?si=abc",
                   "https://m.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
                   "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"):
            assert video_link(yt) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ", yt
        assert video_embed(video_link("youtu.be/dQw4w9WgXcQ")) == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"
        assert video_link("https://www.youtube.com/watch?v=dQw4w9WgXcQQ") is None
        # the teacher moves a lesson's videos among its topics: another one is added, and the two are placed in a new order
        assert c.post("/api/videos", json={"lesson": READY[0], "title": "b", "url": link}, headers=good).status_code == 200
        seen = lambda: [(v["id"], v["pos"]) for v in c.get("/api/videos", headers=good).json()["videos"] if v["lesson"] == READY[0]]
        assert seen() == [(1, N1), (3, N1)]
        order = lambda *o: c.put("/api/content/order", json={"lesson": READY[0], "order": [{"kind": "video", "id": i, "pos": p} for i, p in o]}, headers=good).status_code
        assert order((3, 0), (1, 0)) == 200 and seen() == [(3, 0), (1, 0)]   # both before the first topic, the new one first
        assert order((3, 0), (1, 2)) == 200 and seen() == [(3, 0), (1, 2)]
        assert order((3, 0)) == 409 and order((3, 0), (2, 0)) == 409   # every video of the lesson, and no other's
        assert order((3, 0), (1, N1 + 1)) == 400 and order((3, -1), (1, 0)) == 400 and seen() == [(3, 0), (1, 2)]
        assert c.put("/api/content/order", json={"lesson": SOON, "order": []}, headers=good).status_code == 400
        c.app.state.auth.vids[0].update(pos=None)   # kept before videos had places: after the topics
        assert seen() == [(3, 0), (1, N1)]
        assert c.delete("/api/videos/3", headers=good).status_code == 200
        first.update(pos=N1, ord=1)
        second = c.get("/api/videos", headers=good).json()["videos"][1]
        # a text: its lines in Hebrew and in Arabic written in Hebrew letters, the same number of each; a dialogue names two voices
        he, tr = ["שלום", "מה שלומך?", "שלום"], ["מַרְחַבַּא", "כִּיף חַאלַכּ?", "מַרְחַבַּא יַא זַלַמֶה"]
        talk = {"lesson": READY[0], "title": " בשוק ", "he": he, "tr": tr, "voices": ["אמיר", "ראאף"]}
        assert c.get("/api/texts").status_code == 401 and c.get("/api/texts", headers=good).json() == {"categories": [], "phrases": []}
        for body, code in ((talk, 200), ({**talk, "title": "story", "voices": []}, 200),
                           ({**talk, "tr": tr[:2]}, 400), ({**talk, "he": [], "tr": []}, 400), ({**talk, "tr": ["x", "1", "-"]}, 400),   # not Arabic in Hebrew letters
                           ({**talk, "voices": ["אמיר"]}, 400), ({**talk, "voices": ["אמיר", "nobody"]}, 400),
                           ({**talk, "lesson": SOON}, 400), ({**talk, "title": ""}, 400), ({**talk, "he": he * 80, "tr": tr * 80}, 400)):
            r = c.post("/api/texts", json=body, headers=good)
            assert r.status_code == code, (body, r.text)
        d = c.get("/api/texts", headers=good).json()
        assert d["categories"][0] == {"id": "t1", "name": "בשוק", "lesson": READY[0], "story": True, "text": 1, "voices": ["אמיר", "ראאף"],
                                      "pos": N1, "ord": 2, "guessed": ["זַלַמֶה"]}   # the course has the other words
        assert "voices" not in d["categories"][1] and d["categories"][1]["ord"] == 3
        line1, line2, line3 = d["phrases"][:3]
        assert line1 == {"id": 1_001_000, "cat": "t1", "he": "שלום", "tr": "מַרְחַבַּא", "ar": "مرحبا", "seq": 1}
        assert (line2["id"], line2["ar"], line3["ar"]) == (1_001_001, "كيف حالك؟", "مرحبا يا زلمة") and len(d["phrases"]) == 6
        # a line is a phrase like the bank's: it has a prompt and an answer to play, and its recordings count (two lines
        # with the same Hebrew stay apart)
        assert c.get("/api/prompt-audio/1001999", headers=good).status_code == 404 and c.get("/api/tts/1001001", headers=good).status_code == 503
        from app.main import built, summary as sums, text_bank
        bank = text_bank(c.app.state.auth.txts)
        assert bank[1_001_002]["key"] == "שלום (2)" and bank[1_001_001]["variants"][0] == ("كيف حالك؟", "k iː f | ħ aː l a k")
        got = sums([{"cat": "t1", "phrase": "שלום (2)", "score": 80, "created_at": "2026-10-09T10:00:00+00:00"},
                    {"cat": "t1", "phrase": "שלום", "score": 60, "created_at": "2026-10-09T10:00:00+00:00"},
                    {"cat": "t9", "phrase": "שלום", "score": 99, "created_at": "2026-10-09T10:00:00+00:00"}], bank)   # a deleted text's
        assert got["best"] == {1_001_002: 80, 1_001_000: 60} and got["recordings"] == 3
        for ar, ipa in (v for p in bank.values() for v in p["variants"]):
            assert len(ar.split()) == len(ipa.split("|"))
        # a text is placed among the topics like a video, and may be a class's homework
        mixed = lambda *o: c.put("/api/content/order", json={"lesson": READY[0], "order": [{"kind": k, "id": i, "pos": p} for k, i, p in o]}, headers=good).status_code
        assert mixed(("text", 2, 0), ("video", 1, 1), ("text", 1, 1)) == 200 and mixed(("text", 2, 0), ("text", 1, 1)) == 409
        assert [(k["id"], k["pos"], k["ord"]) for k in c.get("/api/texts", headers=good).json()["categories"]] == [("t2", 0, 0), ("t1", 1, 2)]
        assert c.get("/api/videos", headers=good).json()["videos"][0]["ord"] == 1
        first.update(pos=1, ord=1)
        assert c.post("/api/classes", json={"name": "T", "emails": "a@b.c", "lessons": READY[:1], "homework": "t1"}, headers=good).status_code == 200
        assert c.get("/api/me", headers=good).json()["classes"][-1]["homework"] == "t1"
        assert c.delete("/api/texts/1", headers=good).status_code == 200   # with its text gone, the homework is none
        assert c.get("/api/me", headers=good).json()["classes"][-1]["homework"] == ""
        assert c.delete("/api/texts/2", headers=good).status_code == 200
        c.app.state.auth.cls = [k for k in c.app.state.auth.cls if k["name"] != "T"]
        # a student gets the videos of their classes' lessons only, and adds or deletes none
        kept, c.app.state.auth.teachers = c.app.state.auth.cls, frozenset()
        c.app.state.auth.cls = [{"id": 7, "name": "V", "emails": ["a@b.c"], "nicknames": {}, "lessons": READY[1:2]}]
        assert c.get("/api/videos", headers=good).json()["videos"] == [{k: v for k, v in second.items() if k != "url"}]   # without the link
        assert c.post("/api/videos", json={"lesson": READY[0], "title": "x", "url": link}, headers=good).status_code == 403
        assert c.delete("/api/videos/1", headers=good).status_code == 403
        assert c.put("/api/content/order", json={"lesson": READY[0], "order": []}, headers=good).status_code == 403
        assert c.post("/api/texts", json={}, headers=good).status_code == 403 and c.delete("/api/texts/1", headers=good).status_code == 403
        c.app.state.auth.cls, c.app.state.auth.teachers = kept, frozenset({"a@b.c"})
        assert c.delete("/api/videos/1", headers=good).status_code == 200
        assert c.get("/api/videos", headers=good).json()["videos"] == [second]
        # the daily videos: a library by level; a class gives a video its day, and its students get it with that day
        from app.phrases import LEVELS
        L1, L2 = LEVELS[0]["id"], LEVELS[1]["id"]
        daily = lambda **body: c.post("/api/dailies", json=body, headers=good).status_code
        assert c.get("/api/dailies").status_code == 401 and c.get("/api/dailies", headers=good).json() == {"dailies": []}
        assert daily(level=L1, title=" יום  1 ", url=link + "?share=copy") == 200 and daily(level=L2, title="other", url="https://youtu.be/dQw4w9WgXcQ") == 200
        assert daily(level="9", title="x", url=link) == 400 and daily(level=L1, title="", url=link) == 400 and daily(level=L1, title="x", url="https://example.com/1") == 400
        lib = c.get("/api/dailies", headers=good).json()["dailies"]
        assert [(v["id"], v["level"], v["title"]) for v in lib] == [(1, L1, "יום 1"), (2, L2, "other")] and lib[0]["embed"].startswith("https://player.vimeo.com/video/")
        D = {"name": "D", "emails": "a@b.c", "lessons": [l["id"] for l in LESSONS if l["level"] == L1][:1]}
        save = lambda days: c.post("/api/classes", json={**D, "dailies": days}, headers=good).status_code
        assert save([{"video": 9, "date": "2026-10-12"}]) == 400 and save([{"video": 1, "date": "12.10"}]) == 400
        assert save([{"video": 1, "date": "2026-10-12"}, {"video": 1, "date": "2026-10-13"}]) == 400 and save("x") == 400
        assert save([{"video": 1, "date": "2026-10-12"}, {"video": 2, "date": "2026-10-11"}, {"video": 9, "date": ""}]) == 200   # one without a day is dropped
        d = c.app.state.auth.cls[0]
        assert d["dailies"] == [{"video": 1, "date": "2026-10-12"}, {"video": 2, "date": "2026-10-11"}]
        mine = lambda: next(k for k in c.get("/api/me", headers=good).json()["classes"] if k["name"] == "D")["dailies"]
        assert mine() == [{"id": 1, "title": "יום 1", "embed": lib[0]["embed"], "date": "2026-10-12"}]   # the other level's is not the class's
        assert c.get("/api/me", headers=good).json()["dailies"] == mine()   # a teacher gets every class's
        # saved without the days they stay; saved with none they are gone
        assert c.put(f"/api/classes/{d['id']}", json=D, headers=good).status_code == 200 and len(mine()) == 1
        assert c.put(f"/api/classes/{d['id']}", json={**D, "dailies": []}, headers=good).status_code == 200 and mine() == [] and d["dailies"] == []
        assert c.put(f"/api/classes/{d['id']}", json={**D, "dailies": [{"video": 1, "date": "2026-10-12"}]}, headers=good).status_code == 200
        assert c.delete("/api/dailies/1", headers=good).status_code == 200 and mine() == []   # gone from the library, gone from the class
        # the library: videos for every student, whatever the level and the class
        shelf = lambda **body: c.post("/api/library", json=body, headers=good).status_code
        assert c.get("/api/library").status_code == 401 and c.get("/api/library", headers=good).json() == {"library": []}
        assert shelf(title="two", url=link) == 200 and shelf(level=L1, title=" one ", url=link) == 200 and shelf(title="gone", url=link) == 200
        assert shelf(title="", url=link) == 400 and shelf(title="x", url="nope") == 400
        assert c.delete("/api/library/3", headers=good).status_code == 200
        shelves = c.get("/api/library", headers=good).json()["library"]
        assert [(v["id"], v["title"], v["url"]) for v in shelves] == [(1, "two", link.split("?")[0]), (2, "one", link.split("?")[0])]
        c.app.state.auth.teachers = frozenset()
        assert c.get("/api/library", headers=good).json()["library"] == [{k: v for k, v in s.items() if k != "url"} for s in shelves]   # all of it, without the links
        assert shelf(title="x", url=link) == 403 and c.delete("/api/library/1", headers=good).status_code == 403
        assert c.get("/api/dailies", headers=good).status_code == 403 and daily(level=L1, title="x", url=link) == 403 and c.delete("/api/dailies/2", headers=good).status_code == 403
        c.app.state.auth.cls, c.app.state.auth.teachers = [k for k in c.app.state.auth.cls if k["name"] != "D"], frozenset({"a@b.c"})
        new_class = {"name": " October 2026 ", "emails": "S@b.c, new@b.c\nnew@b.c  דנה  \nx@b.c\n",   # an optional nickname
                     "lessons": ALL[::-1],   # x is in September's class too: a student may be in several
                     "homework": T2, "due": "2026-10-15"}
        assert c.post("/api/classes", json=new_class, headers=good).status_code == 200
        assert c.app.state.auth.cls[0] == {"id": 2, "name": "October 2026", "emails": ["new@b.c", "s@b.c", "x@b.c"],
                                           "nicknames": {"new@b.c": "דנה"}, "lessons": ALL, "homeworks": [{"topic": T2, "due": "2026-10-15"}]}
        for body, code in (({"name": "", "emails": "", "lessons": ALL}, 400), ({"name": "y", "emails": "not-an-email", "lessons": ALL}, 400),
                           ({"name": "y", "emails": "z@b.c w@b.c Dana", "lessons": ALL}, 400),   # whose nickname?
                           ({"name": "y", "emails": "", "lessons": []}, 400), ({"name": "y", "emails": ""}, 400),   # no lesson
                           ({"name": "y", "emails": "", "lessons": ["no-such-lesson"]}, 400),
                           ({"name": "y", "emails": "", "lessons": ALL[:1], "homework": T2}, 400),   # not of the class's lessons
                           ({"name": "y", "emails": "", "lessons": ALL, "homework": ALL[0]}, 400),   # a lesson is not a topic
                           ({"name": "y", "emails": "", "lessons": ALL, "homework": T1, "due": "15/10"}, 400),
                           ({"name": "October 2026", "emails": "", "lessons": ALL}, 409),   # the name is taken,
                           ({"name": " october   2026", "emails": "", "lessons": ALL}, 409)):   # in any letter case and spacing
            r = c.post("/api/classes", json=body, headers=good)
            assert r.status_code == code, (body, r.text)
        assert "October 2026" in r.json()["detail"]
        # renaming a class to another's name is refused too; keeping its own name is not
        assert c.put("/api/classes/1", json={"name": "OCTOBER 2026", "emails": "", "lessons": ALL}, headers=good).status_code == 409
        assert c.put("/api/classes/9", json={"name": "z", "emails": "", "lessons": ALL}, headers=good).status_code == 404
        assert c.put("/api/classes/1", json={"name": "September 2026", "emails": "x@b.c y@b.c", "lessons": ALL[:1]},
                     headers=good).status_code == 200
        # the panel: the classes with their lessons; the classes' accounts, each with {class: nickname}
        d = c.get("/api/class", headers=good).json()
        assert [(k["name"], k["lessons"]) for k in d["classes"]] == [("October 2026", ALL), ("September 2026", ALL[:1])]
        assert [(k["homework"], k["due"]) for k in d["classes"]] == [(T2, "2026-10-15"), ("", None)]
        # a date without homework is dropped; saving without the two clears them
        assert c.put("/api/classes/2", json={**new_class, "homework": ""}, headers=good).status_code == 200
        assert c.app.state.auth.cls[0]["homeworks"] == []
        # a class may have several homeworks, a topic each with its own day: they come back in the order of its lessons
        several = {**new_class, "homework": "", "homeworks": [{"topic": T2, "due": ""}, {"topic": T1, "due": "2026-10-20"}, {"topic": "", "due": "2026-10-21"}]}
        assert c.put("/api/classes/2", json=several, headers=good).status_code == 200
        k = c.get("/api/class", headers=good).json()["classes"][0]
        assert k["homeworks"] == [{"topic": T1, "due": "2026-10-20"}, {"topic": T2, "due": None}] and (k["homework"], k["due"]) == (T1, "2026-10-20")
        for bad in ([{"topic": T1}, {"topic": T1}], [{"topic": "no-such-topic"}], [{"topic": T1, "due": "soon"}], "x", ["x"]):
            assert c.put("/api/classes/2", json={**several, "homeworks": bad}, headers=good).status_code == 400, bad
        assert c.put("/api/classes/2", json={**new_class, "homework": ""}, headers=good).status_code == 200
        student, new, x, y = d["students"]   # the teacher, in no class, is not a student
        assert student["best"] == {str(PHRASES.index(VERB)): 88} and student["tries"] == {str(PHRASES.index(VERB)): 2}
        assert (student["recordings"], student["days"], student["last"]) == (2, 1, "2026-10-09T10:00:00+00:00")
        assert (new["email"], new["joined"], new["recordings"]) == ("new@b.c", False, 0) and student["joined"]
        c.app.state.auth.wrote["u2", "l1-hw"] = ["הִיֵّ פַתְחַת"]   # the panel reads what each student wrote
        assert [k["written"] for k in c.get("/api/class", headers=good).json()["students"][:2]] == [{"l1-hw": ["הִיֵّ פַתְחַת"]}, {}]
        assert (student["classes"], new["classes"]) == ({"October 2026": ""}, {"October 2026": "דנה"})
        assert x["classes"] == {"October 2026": "", "September 2026": ""} and y["classes"] == {"September 2026": ""}
        # a class counts what was practised since it was opened: a new one starts clean (the student's own summary stays whole)
        assert student["since"]["October 2026"]["recordings"] == 2   # a class without its opening time counts everything
        c.app.state.auth.cls[0]["created_at"] = "2026-10-09T00:00:00.123+00:00"   # between the student's two tries
        student = c.get("/api/class", headers=good).json()["students"][0]
        assert student["recordings"] == 2 and student["since"] == {"October 2026": {
            "best": {str(PHRASES.index(VERB)): 88}, "tries": {str(PHRASES.index(VERB)): 1}, "missed": {}, "unmatched": 0,
            "recordings": 1, "days": 1,
            "week": student["since"]["October 2026"]["week"], "last": "2026-10-09T10:00:00+00:00"}}
        # a recording that did not match its sentence is counted apart: a recording, but no try and no score
        from app.main import summary
        off = summary([{"cat": VERB["cat"], "phrase": VERB["he"], "score": 12, "unrelated": True, "created_at": "2026-10-09T11:00:00+00:00"},
                       {"cat": VERB["cat"], "phrase": VERB["he"], "score": 70, "unrelated": False, "created_at": "2026-10-09T10:00:00+00:00"}])
        assert (off["best"], off["tries"], off["missed"]) == ({PHRASES.index(VERB): 70}, {PHRASES.index(VERB): 1}, {PHRASES.index(VERB): 1})
        assert (off["unmatched"], off["recordings"], off["last"]) == (1, 2, "2026-10-09T11:00:00+00:00")
        c.app.state.auth.cls[0]["created_at"] = "2026-10-10T00:00:00+00:00"   # opened after both
        since = c.get("/api/class", headers=good).json()["students"][0]["since"]["October 2026"]
        assert (since["best"], since["recordings"], since["last"]) == ({}, 0, None)
        assert c.delete("/api/classes/1", headers=good).status_code == 200 and len(c.app.state.auth.cls) == 1
        # a student sees each of their classes: by nickname, else the Google name; one with neither is only counted
        c.app.state.auth.cls[0]["emails"].append("a@b.c")
        assert c.get("/api/classmates", headers=good).json() == {"classes": [{"name": "October 2026", "unnamed": 2, "students": [
            {"name": "Alef", "me": True}, {"name": "דנה", "me": False}]}]}
        c.app.state.auth.cls[0]["emails"].remove("a@b.c")
        c.app.state.auth.cls = []   # no class: every account that signed in
        assert [s["email"] for s in c.get("/api/class", headers=good).json()["students"]] == ["A@b.c", "s@b.c", "out@b.c"]
    # a scored recording is recorded as the student's attempt
    auth, pid = FakeAuth(), PHRASES.index(TOILET)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000) * 32767).astype("<i2").tobytes())
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    with TestClient(create_app(scorer_for(logp), auth=auth)) as c:
        files = {"audio": ("a.wav", buf.getvalue(), "audio/wav")}
        assert c.post("/api/score", data={"phrase_id": pid}, files=files).status_code == 401 and not auth.rows
        r = c.post("/api/score", data={"phrase_id": pid}, files=files, headers={"Authorization": "Bearer good"})
        assert r.status_code == 200, r.text
        (token, row), = auth.rows
        assert token == "good" and row["phrase"] == TOILET["he"] and row["cat"] == TOILET["cat"]
        assert row["score"] == round(r.json()["score"]) and row["unrelated"] is False
        assert c.get("/api/progress").status_code == 401
        assert c.get("/api/progress", headers={"Authorization": "Bearer good"}).json() == {
            "best": {str(PHRASES.index(VERB)): 88}, "tries": {str(PHRASES.index(VERB)): 2}, "recordings": 3, "days": 2}
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:   # no sign-in: nothing to show
        assert c.get("/api/progress").json() == {"best": {}, "tries": {}, "recordings": 0, "days": 0}
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:   # not configured: open, as before
        assert 'AUTH_URL=""' in c.get("/").text and c.get("/api/tts/0").status_code == 503


@pytest.mark.parametrize("p", PHRASES, ids=lambda p: p["he"])
def test_letters_cover_the_word_and_its_phonemes(p):
    from app.translit import word_letters, word_to_hebrew

    for ar, ipa in p["variants"]:
        for a, w in zip(ar.split(), ipa.split("|")):
            letters = word_letters(w, a)
            assert "".join(t for t, _ in letters) == word_to_hebrew(w, a)
            assert [j for _, at in letters for j in at] == list(range(len(w.split())))   # each phoneme once, in order


def test_result_scores_each_letter():
    # ħ said as χ: of الحمام, the ח is the weak letter and the rest are fine
    s = scorer_for(emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"]))
    for _, ipa in TOILET["variants"]:   # every target token is some phoneme's, the doubled م sharing one
        carried = [i for word in s._carriers(ipa) for idx in word for i in idx]
        assert sorted(set(carried)) == list(range(len(s._targets(ipa))))
    r = s.score(None, TOILET)
    for w in r["words"]:
        assert "".join(l["t"] for l in w["letters"]) == w["tr"]
    by = {l["t"][0]: l["score"] for l in r["words"][1]["letters"] if l["score"] is not None}
    assert by["ח"] < 50 and by["מ"] > 75
    # the coloured letter says why, with the advice for that mistake; a letter that was fine says nothing
    het, mem = (next(l for l in r["words"][1]["letters"] if l["t"][0] == c) for c in "חמ")
    assert het["why"] == "ח (ح) נשמעה כמו ח' (خ)" and het["tip"] == r["tips"][0]["text"] and "why" not in mem
