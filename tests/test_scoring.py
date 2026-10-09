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
        assert len(ph["phrases"]) == len(PHRASES) and "ipa" not in ph["phrases"][0]
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
    from app.phrases import public_phrases

    day2 = [p for p in public_phrases() if p["cat"] == "day2"]
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

    assert [l["id"] for l in LESSONS] == ["1", "2", "free"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "1"] == ["l1-pron", "l1-verbs", "l1-past", "l1-say", "l1-read"]
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


def test_workbook_lesson_2():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l2-dual", "l2-family", "l2-poss", "l2-say", "l2-say2", "l2-read", "l2-talk", "l2-meet"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "2"] == cats
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


def test_sign_in_guards_the_practice_endpoints():
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeAuth:
        url, key, allowed = "https://proj.supabase.co", "sb_publishable_x", frozenset()

        def __init__(self):
            self.rows = []

        def user(self, token):
            return {"id": "u1", "email": "A@b.c"} if token == "good" else None

        def record(self, token, row):
            self.rows.append((token, row))

    with TestClient(create_app(scorer_for(emissions(["w"])), auth=FakeAuth())) as c:
        page = c.get("/").text
        assert 'AUTH_URL="https://proj.supabase.co"' in page and 'AUTH_KEY="sb_publishable_x"' in page
        assert c.get("/api/phrases").status_code == 200   # the phrase list stays public
        assert c.get("/api/prompt-audio/0").status_code == 401
        assert c.get("/api/tts/0", headers={"Authorization": "Bearer bad"}).status_code == 401
        assert c.get("/api/tts/0", headers={"Authorization": "Bearer good"}).status_code == 503   # in, but no TTS key
        good = {"Authorization": "Bearer good"}
        assert c.get("/api/me", headers=good).json() == {"email": "A@b.c"}
        c.app.state.auth.allowed = frozenset({"other@b.c"})   # a list without this account
        assert c.get("/api/me", headers=good).status_code == 403 and c.get("/api/tts/0", headers=good).status_code == 403
        c.app.state.auth.allowed = frozenset({"a@b.c"})   # case does not matter
        assert c.get("/api/me", headers=good).status_code == 200
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
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:   # not configured: open, as before
        assert 'AUTH_URL=""' in c.get("/").text and c.get("/api/tts/0").status_code == 503
