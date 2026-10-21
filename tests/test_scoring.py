"""Tests with synthetic CTC emissions: no model download needed."""
import io
import re
import wave
from dataclasses import replace

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


def test_teacher_diagnostics_include_compact_phoneme_heatmap():
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    scorer = scorer_for(logp)
    assert "phoneme_heatmap" not in scorer.score(None, TOILET)
    heatmap = scorer.score_logp(logp, TOILET, diagnostics=True, duration_s=2.5)["phoneme_heatmap"]
    assert heatmap["duration_ms"] == 2500
    assert heatmap["frame_bins"] == len(logp)
    assert {"ħ", "χ", "blank"} <= {row["phoneme"] for row in heatmap["rows"]}
    assert all(len(row["values"]) == heatmap["frame_bins"] for row in heatmap["rows"])
    assert [p["phoneme"] for p in heatmap["tracks"]["expected"]] == [
        "w", "eː", "n", "i", "l", "ħ", "a", "m", "aː", "m"]
    assert [p["phoneme"] for p in heatmap["tracks"]["heard"]] == [
        "w", "e", "n", "i", "l", "χ", "a", "m", "a", "m"]


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


def test_applied_phrase_calibration_raises_only_the_matching_confusion():
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    scorer = scorer_for(logp)
    key = TOILET.get("key", TOILET["he"])
    scorer.set_calibrations([{"expected": "ħ", "heard": "χ", "floor": 70,
                              "scope": "phrase", "phrase": key}])
    r = scorer.score(None, TOILET)
    h = next(p for p in r["words"][1]["phonemes"] if p["ipa"] == "ħ")
    assert h["score"] == 70 and not h["flagged"]
    assert r["calibrated"] == [{"expected": "ħ", "heard": "χ", "from": 2, "to": 70}]


def test_deleted_ayin_gets_conservative_builtin_calibration():
    phrase = {"variants": [("مع", "m a ʕ")]}
    scorer = scorer_for(emissions(["m", "a"]))
    r = scorer.score(None, phrase, diagnostics=True)
    ayin = next(p for p in r["words"][0]["phonemes"] if p["ipa"] == "ʕ")
    trace = next(p for p in r["trace"]["tokens"] if p["ipa"] == "ʕ")
    calibration = next(s for s in trace["stages"] if s["stage"] == "calibration")
    assert ayin["score"] == 60 and not ayin["flagged"]
    assert calibration == {"stage": "calibration", "score": 60, "floor": 60, "applied": True}
    applied = r["calibrated"][0]
    assert applied["expected"] == "ʕ" and applied["heard"] == "_"
    assert applied["from"] < 50 and applied["to"] == 60
    scorer.set_calibrations([{"expected": "ʕ", "heard": "_", "floor": 75, "scope": "global"}])
    overridden = scorer.score(None, phrase)
    assert next(p for p in overridden["words"][0]["phonemes"] if p["ipa"] == "ʕ")["score"] == 75


def test_h_instead_of_pharyngeal():
    logp = emissions(["w", "eː", "n", "i", "l", "h", "a", "m", "aː", "m"])
    r = scorer_for(logp).score(None, TOILET)
    assert r["tips"][0]["key"] == "ħ>h"


def test_k_for_qaf_is_rural_and_correct():
    # learner says "kaddeesh" for قديش: rural Palestinian, so ق is accepted as k; the answer stays the urban one
    logp = emissions(["k", "a", "d", "eː", "ʃ", "i", "s", "aː", "ʕ", "a"])
    r = scorer_for(logp).score(None, TIME)
    q = r["words"][0]["phonemes"][0]
    assert r["variant"]["ipa"].startswith("ʔ") and q["sound"] == "Q" and q["realised"] == "k"
    assert q["score"] >= 90 and not q["flagged"] and not r["tips"] and r["score"] >= 90
    assert r["words"][0]["heard_tr"].startswith("כּ")      # written as said, not coloured as a mistake


# ---------- dialects and Hebrew-speaker rules (docs/scoring-dialects-plan.md) ----------

def custom_scorer(extra, phones, frames=3, gap=2, peak=0.9):
    """A scorer over SYMS plus `extra`, hearing `phones` cleanly."""
    syms = SYMS + [s for s in extra if s not in SYMS]
    vocab = {s: i for i, s in enumerate(syms)}

    def row(sym, p):
        r = np.full(len(syms), (1 - p) / (len(syms) - 1))
        r[vocab[sym]] = p
        return r

    rows = [row("<pad>", 0.95)] * gap
    for ph in phones:
        rows += [row(ph, peak)] * frames + [row("<pad>", 0.95)] * gap
    return Scorer(lambda wav: np.log(np.array(rows)), Vocab(vocab))


COFFEE = {"variants": [("قهوة", "ʔ a h w e")]}


def test_q_for_qaf_is_accepted():
    r = scorer_for(emissions(["q", "a", "d", "eː", "ʃ", "i", "s", "aː", "ʕ", "a"])).score(None, TIME)
    assert r["words"][0]["phonemes"][0]["realised"] == "q" and not r["tips"] and r["score"] >= 90


def test_dropped_qaf_between_vowels_is_not_an_error():
    r = scorer_for(emissions(["a", "h", "w", "e"])).score(None, COFFEE)
    assert r["score"] >= 90 and not r["tips"] and not any(i["missing"] for i in r["issues"])


def test_hamza_is_not_a_qaf():
    # اهلا is written with ء: kahla is not a dialect form, it is a mistake
    phrase = {"variants": [("اهلا", "ʔ a h l a")]}
    r = scorer_for(emissions(["k", "a", "h", "l", "a"])).score(None, phrase)
    p = r["words"][0]["phonemes"][0]
    assert "sound" not in p and p["flagged"] and p["heard"] == "k"
    assert [t["key"] for t in r["tips"]] == ["ʔ>k"]


def test_reference_is_matched_by_letter_not_by_realisation():
    ref = scorer_for(emissions(["q", "a", "h", "w", "e"])).reference(None, COFFEE)   # the voice says ق as q
    assert ref.sounds[0] == (0, "Q")
    r = scorer_for(emissions(["ʔ", "a", "h", "w", "e"])).score(None, COFFEE, [ref])
    assert r["score"] >= 95 and not r["tips"] and r["references"] == 1


def test_mixed_dialect_is_noted_but_not_marked_down():
    phrase = {"variants": [("قديش قهوة", "ʔ a d d eː ʃ | ʔ a h w e")]}
    mixed = scorer_for(emissions(["ʔ", "a", "d", "eː", "ʃ", "k", "a", "h", "w", "e"])).score(None, phrase)
    assert mixed["mixed_dialect"] and mixed["score"] >= 90 and not mixed["tips"]
    rural = scorer_for(emissions(["k", "a", "d", "eː", "ʃ", "k", "a", "h", "w", "e"])).score(None, phrase)
    assert rural["dialect"] == "rural" and "mixed_dialect" not in rural


def test_rural_kaf_and_tha():
    phrase = {"variants": [("كثير", "k t iː r")]}
    r = custom_scorer(["tʃ", "θ", "r", "iː", "t"], ["tʃ", "θ", "iː", "r"]).score(None, phrase)
    assert [p.get("realised") for p in r["words"][0]["phonemes"][:2]] == ["tʃ", "θ"]
    assert r["score"] >= 90 and not r["tips"] and r["dialect"] == "rural"
    r = custom_scorer(["tʃ", "θ", "r", "iː", "t"], ["tʃ", "t", "iː", "r"]).score(None, phrase)
    assert r["mixed_dialect"] and r["score"] >= 90          # rural ك with urban ث


def test_final_ta_marbuta_vowel_may_be_a_e_or_i():
    for vowel in ("a", "e", "i"):
        r = scorer_for(emissions(["ʔ", "a", "h", "w", vowel])).score(None, COFFEE)
        assert r["words"][0]["phonemes"][-1]["score"] >= 95, vowel


def test_diphthong_for_long_vowel():
    # بيت said as bayt (rural / older speech) for beet
    phrase = {"variants": [("بيت", "b eː t")]}
    r = custom_scorer(["b", "t", "j"], ["b", "a", "j", "t"]).score(None, phrase)
    assert r["score"] >= 90 and not r["tips"] and r["variant"]["ipa"] == "b a j t"


def test_archify_places_each_letter():
    from app.archi import archify, unresolved
    assert archify("قديش الساعة", "ʔ a d d eː ʃ | i s s aː ʕ a") == "Q:ʔ a d d eː ʃ | i s s aː ʕ E:a"
    assert archify("انا من القدس", "ʔ a n a | m i n | i l q u d s") == "ʔ a n a | m i n | i l Q:q u d s"
    assert archify("شكرا كثير", "ʃ u k r a n | k t iː r") == "ʃ u K:k r a n | K:k TH:t iː r"
    assert archify("قهوة", "Q:k a h w e") == "Q:k a h w e"                  # written by hand: left alone
    assert unresolved("بعثت", "b a ʕ a t t") == [("بعثت", "ث")]             # ث and ت said as one geminate


def test_every_qaf_in_the_bank_is_placed():
    from app.archi import unresolved
    bad = {(w, ipa) for p in PHRASES for ar, ipa in p["variants"] for w, letter in unresolved(ar, ipa) if letter == "ق"}
    assert not bad, f"mark these ق by hand in the phrase (Q:ʔ): {sorted(bad)[:10]}"


def test_emphatic_heard_plain_is_a_mistake_with_the_rules_feedback():
    from app.rules import load
    phrase = {"variants": [("ظهر", "ðˤ u h r")]}
    r = custom_scorer(["ðˤ", "dˤ", "zˤ", "z", "u", "r"], ["z", "u", "h", "r"]).score(None, phrase)
    assert [t["key"] for t in r["tips"]] == ["ZH>z"]
    assert r["tips"][0]["text"] == load().check("ZH", "z").feedback and r["tips"][0]["severity"] == 2


def margin_scorer(competitor_ratio):
    """طين where tˤ is weak (p 0.07) and plain t is `competitor_ratio` times likelier in the same frames."""
    syms = SYMS + ["tˤ", "t"]
    vocab = {s: i for i, s in enumerate(syms)}
    V = len(syms)

    def row(probs):
        r = np.full(V, 1e-4)
        for sym, p in probs.items():
            r[vocab[sym]] = p
        return r / r.sum()

    rows = [row({"<pad>": 0.95})] * 2
    rows += [row({"tˤ": 0.07, "t": 0.07 * competitor_ratio, "<pad>": 0.8})] * 3 + [row({"<pad>": 0.95})] * 2
    for ph in ("i", "n"):
        rows += [row({ph: 0.9})] * 3 + [row({"<pad>": 0.95})] * 2
    return Scorer(lambda wav: np.log(np.array(rows)), Vocab(vocab))


def test_emphatic_needs_a_clear_win_before_it_is_flagged():
    phrase = {"variants": [("طين", "tˤ i n")]}
    under = margin_scorer(1.8).score(None, phrase)        # plain t under twice as likely: the model's doubt
    p = under["words"][0]["phonemes"][0]
    assert p["score"] < 50 and not p["flagged"] and not under["tips"]
    over = margin_scorer(2.5).score(None, phrase)
    assert over["words"][0]["phonemes"][0]["flagged"] and [t["key"] for t in over["tips"]] == ["tˤ>t"]


@pytest.mark.parametrize("ar,ipa,extra,said,key,heard_letter", [
    ("فنجان", "f i n dʒ aː n", ["f", "p", "dʒ"], ["p", "i", "n", "dʒ", "aː", "n"], "f>p", "פּ"),
    ("وين", "w eː n", ["v"], ["v", "eː", "n"], "w>v", "ב"),
    ("بس", "b a s", ["b", "v"], ["v", "a", "s"], "b>v", "ב"),
    ("صباح", "sˤ a b aː ħ", ["sˤ", "ts", "b"], ["ts", "a", "b", "aː", "ħ"], "sˤ>ts", "טס"),
    ("مرة", "m a r r a", ["r", "ʁ"], ["m", "a", "ʁ", "a"], "r>ʁ", None),
])
def test_hebrew_speaker_substitutions(ar, ipa, extra, said, key, heard_letter):
    from app.rules import load
    r = custom_scorer(extra, said).score(None, {"variants": [(ar, ipa)]})
    tip = next(t for t in r["tips"] if t["key"] == key)
    expected, heard = key.split(">")
    assert tip["text"] == load().check(expected, heard).feedback
    if heard_letter:                    # a sound Arabic does not have is still written in the heard line
        assert heard_letter in r["heard_tr"].replace("\u05b0", "").replace("\u05b7", "").replace("\u05b4", "")


def test_a_sound_outside_arabic_and_hebrew_is_not_named():
    # the multilingual model offers a click for ħ: reported as unclear, not as "heard as ǀ"
    r = custom_scorer(["ǀ"], ["w", "eː", "n", "i", "l", "ǀ", "a", "m", "aː", "m"]).score(None, TOILET)
    h = next(p for p in r["words"][1]["phonemes"] if p["ipa"] == "ħ")
    assert h["flagged"] and h["heard"] is None


def test_meaning_changing_mistakes_lead_the_tips():
    # w said as v (severity 2) and ħ as Hebrew ח (severity 3): ħ leads even though v scored lower
    r = custom_scorer(["v"], ["v", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"]).score(None, TOILET)
    assert [t["key"] for t in r["tips"]][:2] == ["ħ>x", "w>v"]


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
        assert TOILET["cat"] not in shown and [l["id"] for l in ph["lessons"] if not l.get("soon")] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9"]
        assert len(ph["lessons"]) == 20 and [v["name"] for v in ph["levels"]] == ["שלב א", "שלב ב"]
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


@pytest.mark.parametrize("competitor", ["f", "s", "χ"])
def test_one_frame_onset_spike_does_not_become_a_glottal_substitution(competitor):
    """A generic microphone/breath transient must not be reported as a consonant the learner produced."""
    syms = SYMS + ([competitor] if competitor not in SYMS else [])
    vocab = {s: i for i, s in enumerate(syms)}
    V = len(syms)

    def row(sym, peak):
        p = np.full(V, (1 - peak) / (V - 1))
        p[vocab[sym]] = peak
        return p

    # No glottal stop: one weak arbitrary-phone spike, then the clean vowel-initial word.
    rows = [row("<pad>", .95), row(competitor, .23)]
    for ph in ["a", "h", "l", "a"]:
        rows += [row(ph, .9)] * 3 + [row("<pad>", .95)] * 2
    logp = np.log(np.array(rows))
    phrase = {"variants": [("اهلا", "ʔ a h l a")]}
    r = Scorer(lambda wav: logp, Vocab(vocab)).score_logp(logp, phrase, diagnostics=True)
    onset = r["trace"]["tokens"][0]
    assert onset["competitor_frames"] == 1
    assert onset["stages"][1]["applied"] == "glottal_dropped"
    assert r["words"][0]["score"] >= 90 and not r["tips"]


def test_sustained_initial_consonant_is_still_a_glottal_substitution():
    phrase = {"variants": [("اهلا", "ʔ a h l a")]}
    r = scorer_for(emissions(["s", "a", "h", "l", "a"])).score(None, phrase, diagnostics=True)
    onset = r["trace"]["tokens"][0]
    assert onset["competitor_frames"] >= 2
    assert onset["stages"][1]["applied"] is None
    assert r["words"][0]["phonemes"][0]["heard"] == "s"
    assert r["tips"][0]["key"] == "ʔ>s"


TAWLITNA = {"variants": [("طاولتنا", "tˤ aː w l i t n a")]}


def onset_logp(lead, competitor_frames=1, target_peak=.10, competitor="j"):
    """طاولتنا whose first consonant (after the `lead` phones) peaks weakly, behind `competitor_frames` frames of a
    competitor at 23%: the shape of a breath or microphone onset in front of a word-initial stop."""
    syms = ["<pad>", "tˤ", "aː", "w", "l", "i", "t", "n", "a", "j", "o"]
    vocab = {s: i for i, s in enumerate(syms)}

    def row(sym, peak):
        p = np.full(len(syms), (1 - peak) / (len(syms) - 1))
        p[vocab[sym]] = peak
        return p

    rows = [row("<pad>", .95)]
    for ph in lead:
        rows += [row(ph, .9)] * 3 + [row("<pad>", .95)] * 2
    rows += [row(competitor, .23)] * competitor_frames + [row("tˤ", target_peak), row("<pad>", .95)]
    for ph in ["aː", "w", "l", "i", "t", "n", "a"]:
        rows += [row(ph, .9)] * 3 + [row("<pad>", .95)] * 2
    logp = np.log(np.array(rows))
    return Scorer(lambda wav: logp, Vocab(vocab)), logp


def test_one_frame_spike_before_the_first_consonant_is_not_a_substitution():
    """Rule 31: tˤ said but weak at onset, outscored by one j frame (the reviewed طاولتنا recording)."""
    scorer, logp = onset_logp([])
    r = scorer.score_logp(logp, TAWLITNA, diagnostics=True)
    onset = r["trace"]["tokens"][0]
    assert onset["competitor_frames"] == 1 and onset["gop"] < 0
    assert onset["stages"][1]["applied"] == "onset_transient"
    assert onset["final"] >= 90 and not onset["flagged"]
    assert not r["tips"] and all(i["heard"] is None for i in r["issues"])   # listed as the weakest, j not named


def test_two_frame_competitor_before_the_first_consonant_is_still_a_substitution():
    scorer, logp = onset_logp([], competitor_frames=2)
    r = scorer.score_logp(logp, TAWLITNA, diagnostics=True)
    onset = r["trace"]["tokens"][0]
    assert onset["competitor_frames"] >= 2
    assert onset["stages"][1]["applied"] is None
    assert onset["flagged"] and r["tips"][0]["key"] == "tˤ>j"


def test_one_frame_spike_inside_the_phrase_is_not_an_onset_transient():
    scorer, logp = onset_logp(["a"])
    phrase = {"variants": [("اطاولتنا", "a tˤ aː w l i t n a")]}
    r = scorer.score_logp(logp, phrase, diagnostics=True)
    tok = next(t for t in r["trace"]["tokens"] if t["ipa"] == "tˤ")
    assert tok["i"] > 0 and tok["stages"][1]["applied"] != "onset_transient"
    assert tok["flagged"]


def test_onset_transient_toggle_switches_the_exemption_off():
    scorer, logp = onset_logp([])
    scorer.disabled = {"onset_transient"}
    r = scorer.score_logp(logp, TAWLITNA, diagnostics=True)
    assert r["trace"]["tokens"][0]["stages"][1]["applied"] is None
    assert r["trace"]["tokens"][0]["flagged"]


def test_ana_explicitly_accepts_the_natural_vowell_initial_form():
    from app.grammar import PRONOUNS

    ana = next(p for p in PRONOUNS if p[0] == "ana")
    assert ("انا", "a n a") in ana[3]
    phrase = {"variants": ana[3]}
    r = scorer_for(emissions(["a", "n", "a"])).score(None, phrase)
    assert r["variant"]["ipa"] == "a n a" and r["score"] >= 90
    assert not r["tips"] and not r["issues"]


def test_tts_endpoint_serves_phrase_audio_and_503_without_key():
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeTTS:
        from app.tts import RAEF_VOICE_ID
        voice_id, voice_name = RAEF_VOICE_ID, "רַאאִף"

        def wav(self, text, voice=""):
            from app.arabic_diacritics import point_arabic
            assert text == point_arabic(*PHRASES[0]["variants"][0])
            assert voice == self.voice_id
            return b"RIFFfake", self.voice_id

    with TestClient(create_app(scorer_for(emissions(["w"])), FakeTTS())) as c:
        r = c.get("/api/tts/0")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content == b"RIFFfake"
        assert r.headers["x-tts-provider"] == "sawtakarabi.ai" and r.headers["x-tts-voice-id"] == FakeTTS.voice_id

        from app.tts import VOICES
        voices = c.get("/api/phrases").json()["voices"]
        assert [v["name"] for v in voices] == list(VOICES)
        assert voices == [{"id": FakeTTS.voice_id, "name": "רַאאִף"}]
        assert c.get("/api/tts/9999").status_code == 404
        health = c.get("/api/health").json()
        assert health["tts"] is True and health["rules"]["pharyngeal_h"]["detectable"]


def test_monitoring_can_warm_the_unique_tts_bank(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main
    from app.main import create_app
    from app.tts import RAEF_VOICE_ID

    phrases = [
        {"variants": [("مرحبا", "m a r ħ a b a")]},
        {"variants": [("مرحبا", "m a r ħ a b a")]},  # duplicate text and voice is generated once
        {"variants": [("اهلا", "ʔ a h l a")], "voice": "חנאן"},
    ]
    monkeypatch.setattr(app.main, "PHRASES", phrases)
    monkeypatch.setattr(app.main, "load_auth", lambda: None)

    class FakeTTS:
        def __init__(self): self.calls = []
        def cached(self, text, voice): return text.startswith("مَ")
        def wav_for_voice(self, text, voice): self.calls.append((text, voice)); return b"RIFF", voice
        def cache_stats(self): return {"files": len(self.calls), "bytes": 0, "hits": 0, "misses": 0, "hit_rate": None}

    tts = FakeTTS()
    with TestClient(create_app(scorer_for(emissions(["w"])), tts, tutor=object())) as c:
        started = c.post("/api/tts-cache/warm", json={"voice_id": RAEF_VOICE_ID})
        assert started.status_code == 200 and started.json()["total"] == 2
        for _ in range(100):
            state = c.get("/api/tts-cache").json()["warm"]
            if not state["running"]: break
            import time; time.sleep(.01)
        assert state == {"running": False, "voice_id": RAEF_VOICE_ID, "total": 2, "done": 2, "generated": 1,
                         "skipped": 1, "failed": 0, "current": "", "waiting": 0, "error": "",
                         "bucket": "", "uploaded": 0, "upload_failed": 0, "upload_error": "", "stopped": False}
        assert [voice for _text, voice in tts.calls] == [RAEF_VOICE_ID]  # the cached one is not read again


def test_warm_sweep_uploads_every_cached_wav_to_the_bucket(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main
    import app.tts
    from app.main import create_app
    from app.tts import RAEF_VOICE_ID, BucketMirror, SawtakTTS

    phrases = [{"variants": [("مرحبا", "m a r ħ a b a")]}, {"variants": [("اهلا", "ʔ a h l a")]}]
    monkeypatch.setattr(app.main, "PHRASES", phrases)
    monkeypatch.setattr(app.main, "load_auth", lambda: None)

    tts = SawtakTTS("key", cache_dir=tmp_path)
    monkeypatch.setattr(tts, "_generate", lambda text, voice: b"RIFF" + text.encode())
    (tmp_path / "legacy.wav").write_bytes(b"old")            # cached before the per-voice layout
    other = tmp_path / "other-voice"; other.mkdir(); (other / "x.wav").write_bytes(b"xx")

    class FakeMirror(BucketMirror):
        def __init__(self):
            super().__init__("user/bucket", "ihki-tts")
            self.uploads = []
        def remote_sizes(self):
            return {"ihki-tts/other-voice/x.wav": 2}           # already in the bucket: not sent again
        def upload(self, files):
            self.uploads += [(src if isinstance(src, bytes) else src.read_bytes(), dest) for src, dest in files]

    tts.mirror = mirror = FakeMirror()
    with TestClient(create_app(scorer_for(emissions(["w"])), tts, tutor=object())) as c:
        assert c.post("/api/tts-cache/warm", json={"voice_id": RAEF_VOICE_ID}).status_code == 200
        for _ in range(200):
            state = c.get("/api/tts-cache").json()["warm"]
            if not state["running"]: break
            import time; time.sleep(.01)
    dests = sorted(dest for _data, dest in mirror.uploads)
    keys = sorted(f"ihki-tts/{tts.cache_key(app.main.point_arabic(*ph['variants'][0]), RAEF_VOICE_ID)}" for ph in phrases)
    assert dests == sorted(keys + ["ihki-tts/legacy.wav"])
    assert all(dest.startswith(f"ihki-tts/{RAEF_VOICE_ID}/") for dest in keys)
    assert state["bucket"] == "user/bucket" and state["uploaded"] == 3 and state["upload_failed"] == 0


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


def test_rule_28_exempts_final_b_heard_as_p_only_before_a_voiceless_onset():
    phrase = {"variants": [("حليب طارة", "ħ a l iː b | tˤ aː r e")]}
    extra = ["iː", "b", "p", "tˤ", "r"]
    phones = ["ħ", "a", "l", "iː", "p", "tˤ", "aː", "r", "e"]
    scorer = custom_scorer(extra, phones)
    result = scorer.score(None, phrase, diagnostics=True)
    b = next(t for t in result["trace"]["tokens"] if t["ipa"] == "b")
    exemption = next(stage for stage in b["stages"] if stage["stage"] == "exemption")
    assert b["competitor"] == "p" and exemption == {
        "stage": "exemption", "score": 100.0, "applied": "final_b_devoicing"
    }
    assert result["words"][0]["phonemes"][-1]["score"] == 100

    scorer.set_disabled({"final_b_devoicing"})
    without_rule = scorer.score(None, phrase)
    assert without_rule["words"][0]["phonemes"][-1]["score"] < 50

    # The same model confusion before a voiced onset is not covered by the narrow contextual rule.
    voiced = custom_scorer(extra, ["ħ", "a", "l", "iː", "p", "d", "a", "r"])
    no_exemption = voiced.score(None, {"variants": [("حليب دار", "ħ a l iː b | d a r")]})
    assert no_exemption["words"][0]["phonemes"][-1]["score"] < 50


def test_rule_29_floors_a_deleted_final_short_vowel_before_a_vowel_initial_word():
    phrase = {"variants": [("على إيدك", "ʕ a l a | ʔ iː d e k")]}
    scorer = custom_scorer(["iː"], ["ʕ", "a", "l", "ʔ", "iː", "d", "e", "k"])
    result = scorer.score(None, phrase, diagnostics=True)
    final_a = [t for t in result["trace"]["tokens"] if t["word"] == 0][-1]
    exemption = next(stage for stage in final_a["stages"] if stage["stage"] == "exemption")
    assert final_a["ipa"] == "a" and final_a["deleted"]
    assert exemption == {"stage": "exemption", "score": 90.0, "applied": "word_final_vowel_linking"}

    scorer.set_disabled({"word_final_vowel_linking"})
    without_rule = scorer.score(None, phrase, diagnostics=True)
    final_a = [t for t in without_rule["trace"]["tokens"] if t["word"] == 0][-1]
    exemption = next(stage for stage in final_a["stages"] if stage["stage"] == "exemption")
    assert exemption == {"stage": "exemption", "score": 90.0, "applied": "unmarked_short_vowel"}

    # A deleted final vowel before a consonant is not a hiatus and remains penalised.
    consonant = custom_scorer(["r"], ["ʕ", "a", "l", "d", "a", "r"])
    no_exemption = consonant.score(None, {"variants": [("على دار", "ʕ a l a | d a r")]}, diagnostics=True)
    final_a = [t for t in no_exemption["trace"]["tokens"] if t["word"] == 0][-1]
    assert next(s for s in final_a["stages"] if s["stage"] == "exemption")["applied"] != "word_final_vowel_linking"


def test_rule_30_floors_an_unmarked_short_vowel_but_not_checked_internal_u():
    # The conjunction's word-initial u is not a learner check; a native u mislabelled v cannot collapse the word.
    scorer = custom_scorer(["u", "v"], ["a", "v", "ʕ"])
    result = scorer.score(None, {"variants": [("ا و", "a | u ʕ")]}, diagnostics=True)
    u = next(t for t in result["trace"]["tokens"] if t["ipa"] == "u")
    exemption = next(stage for stage in u["stages"] if stage["stage"] == "exemption")
    assert u["competitor"] == "v"
    assert exemption == {"stage": "exemption", "score": 90.0, "applied": "unmarked_short_vowel"}
    assert result["words"][1]["phonemes"][0]["score"] == 90

    # u after a consonant in the same word remains checked by the kubutz rule (3.11).
    checked = custom_scorer(["u", "v"], ["k", "v", "l"])
    result = checked.score(None, {"variants": [("كل", "k u l")]}, diagnostics=True)
    u = next(t for t in result["trace"]["tokens"] if t["ipa"] == "u")
    exemption = next(stage for stage in u["stages"] if stage["stage"] == "exemption")
    assert exemption["applied"] is None and exemption["score"] < 50


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
        assert refs.invalidate(7, "first") == 1
        assert len(refs.get(7, ALA)) == 2 and calls == ["first", "played", "first"]
        tts.down = True
        assert refs.get(8, ALA) == [] and refs.get(8, ALA) == [] and len(calls) == 5   # failures are not retried at once
    finally:
        reference.decode_audio = orig


WORD_TOPICS = lambda c: c.get("learn") or c.get("match")   # a lesson's new words and their game (test_workbook_new_words)


def test_workbook_lesson_1():
    from app.phrases import CATEGORIES, GAMES, LESSONS, public_phrases

    assert [l["id"] for l in LESSONS if not l.get("soon")] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "1"] == ["l1-new", "l1-pairs", "l1-pron","l1-verbs", "l1-past", "l1-say", "l1-read", "l1-hw"]
    assert GAMES == [{"id": "game-yalla-verb", "name": "יאללה הפועל", "lesson": "1", "bubble": True, "hidden": True,
                      "unit": "פועל", "task": "אמרו את הפועל בערבית מדוברת: בעבר ואז בעתיד, לפני שהבועה נעלמת"}]
    hw = next(c for c in CATEGORIES if c["id"] == "l1-hw")["write"]   # the workbook's homework: a task and five sentences to write, with nothing to say
    assert hw["lines"] == 5 and hw["task"].startswith("כתבו חמישה משפטים") and "מִין - מי" in hw["task"]
    pub = public_phrases()
    count = lambda cat: sum(p["cat"] == cat for p in pub)
    assert [count(c) for c in ("l1-pron", "l1-verbs", "game-yalla-verb", "l1-past", "l1-say", "l1-read")] == [8, 4, 4, 32, 10, 22]
    assert all(p["cat"] in {c["id"] for c in CATEGORIES + GAMES} for p in pub)
    assert all(p["read"] == (p["cat"] == "l1-read") and "#" not in p["ar"] for p in pub if p["cat"].startswith("l1-"))
    assert all("seq" not in p for p in pub if p["cat"] == "l1-past")   # conjugations are drilled in random order
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l1-pron", "אנחנו"]["tr"] == "אִחְנַא"
    assert by["l1-verbs", "ישב (עבר, עתיד)"]["tr"] == "קַעַד, יֻקְעֹד"
    assert by["game-yalla-verb", "ישב (עבר, עתיד)"]["tr"] == "קַעַד, יֻקְעֹד"
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


def test_workbook_new_words():
    from app.phrases import CATEGORIES, LESSONS, public_phrases

    # every lesson opens with its new words, learned before anything is said, then a game on them; lesson 7, a review,
    # brings too few new words for either
    for l in (l["id"] for l in LESSONS if not l.get("soon")):
        mine = [c for c in CATEGORIES if c["lesson"] == l]
        if l == "7":
            assert not any(WORD_TOPICS(c) for c in mine)
            continue
        words, pairs = mine[:2]
        assert (words["id"], words["name"], words["learn"]) == (f"l{l}-new", "מילים חדשות", True)
        assert (pairs["id"], pairs["name"], pairs["match"]) == (f"l{l}-pairs", "התאימו זוגות", f"l{l}-new")   # it deals the words' cards
        assert not any(WORD_TOPICS(c) for c in mine[2:])
    cards = [p for p in public_phrases() if p["cat"].endswith("-new")]
    assert all(p["listed"] is False and not p["read"] for p in cards)   # no tally, daily round or teacher figure counts them
    assert not any(p["cat"].endswith("-pairs") for p in PHRASES)
    count = lambda l: sum(p["cat"] == f"l{l}-new" for p in cards)
    assert [count(l) for l in ("1", "2", "6", "2.8")] == [19, 20, 10, 10] and min(map(count, ["3", "4", "5", "8", "9", "10"])) >= 13
    # added at the bank's end: the ids of the phrases before them, which recorded attempts name, do not move
    ids = [i for i, p in enumerate(PHRASES) if p["cat"].endswith("-new")]
    assert ids == list(range(len(PHRASES) - len(ids), len(PHRASES)))
    by = {(p["cat"], p["he"]): p for p in cards}
    assert (by["l1-new", "דלת"]["ar"], by["l1-new", "דלת"]["tr"]) == ("باب", "בַּאבּ") and by["l1-new", "מתי"]["tr"] == "אֵימְתַא"
    assert by["l5-new", "בבקשה"]["ar"] == "من فضلك" and by["l2.1-new", "מוקדם"]["ar"] == "بكّير"


def test_levels_plan_every_lesson():
    from app.phrases import CATEGORIES, LESSONS, LEVELS
    from app.workbook import LESSONS as WRITTEN, LEVELS as PLAN, lesson_id

    # every lesson names its level, and each level lists all of its planned lessons, the unwritten ones as soon
    assert [v["id"] for v in LEVELS] == ["1", "2"] and len(LESSONS) == 20
    assert all(isinstance(key, tuple) and key[0] in PLAN for key in WRITTEN)
    # level 2's workbook goes on counting from level 1's: its first lesson is shown as the tenth, and keeps the id "2.1"
    assert [(l["level"], l["n"]) for l in LESSONS] == [("1", n) for n in range(1, 11)] + [("2", n) for n in range(10, 20)]
    assert [l["name"] for l in LESSONS if l["level"] == "2"][:2] == ["שיעור 10", "שיעור 11"] and LESSONS[0]["name"] == "שיעור 1"
    assert [l["id"] for l in LESSONS if l["level"] == "2"][:2] == ["2.1", "2.2"]   # level 1 keeps "1", "2": attempts name them
    assert {l["id"] for l in LESSONS if not l.get("soon")} == {lesson_id(*k) for k in WRITTEN} == {c["lesson"] for c in CATEGORIES}
    assert lesson_id(2, 1) == "2.1" and lesson_id(1, 3) == "3"


def test_workbook_lesson_2():
    from app.phrases import CATEGORIES, public_phrases

    cats = ["l2-dual", "l2-family", "l2-poss", "l2-say", "l2-say2", "l2-read", "l2-talk", "l2-meet"]
    assert [c["id"] for c in CATEGORIES if c["lesson"] == "2" and not WORD_TOPICS(c)] == cats + ["l2-hw"]   # the lesson ends with its written homework
    hw = next(c for c in CATEGORIES if c["id"] == "l2-hw")["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השני" in hw["task"] and "קַעַד / יֻקְעֹד – ישב" in hw["task"]
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [17, 24, 48, 23, 25, 13, 27, 24]
    assert all(p["read"] == (p["cat"] == "l2-read") and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2-"))
    assert all("seq" not in p for p in pub if p["cat"] == "l2-poss")   # drilled in random order, like the conjugations
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2-dual", "שולחן, שני שולחנות"]["tr"] == "טַאוְלֵה, טַאוְלְתֵין"   # singular and dual said together
    grandpa = by["l2-family", "סבא (שתי מילים)"]
    grandma = by["l2-family", "סבתא (שתי מילים)"]
    assert grandpa["variants"] == [("سيد، جدّ", "s iː d | dʒ i d d"), ("سيد", "s iː d"), ("جدّ", "dʒ i d d")]
    assert grandma["variants"] == [("ستّ، جدّة", "s i t t | dʒ i d d e"), ("ستّ", "s i t t"), ("جدّة", "dʒ i d d e")]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "3" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "4" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "5" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "6" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l6-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פתרו את כל השיעור השישי" in hw["task"] and "פעם אחת בעבר ופעם אחת בעתיד" in hw["task"]
    story = mine[cats.index("l6-story")]
    assert story["story"] and story["voices"] == ["איוב", "נסרין"]
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
    # a dialogue: the speaker is written before a turn's first line but neither said nor scored;
    # נסרין's turns are in Hanan's voice, איוב's in Raef's
    lines = [p for p in PHRASES if p["cat"] == "l6-story"]
    assert not any(":" in p["he"] or ":" in p["variants"][0][0] for p in lines)
    assert [(p["seq"], p["speaker"]) for p in lines if "speaker" in p] == [(1, "איוב"), (2, "נסרין"), (3, "איוב"), (10, "נסרין")]
    assert [p["seq"] for p in lines if p.get("voice") == "חנאן"] == [2, 10, 11, 12]
    p = next(p for p in PHRASES if p["cat"] == "l6-story" and p["seq"] == 9)
    assert p["tr"] == "לַוְ כַּאן עִנְד-י מַרַה זַיֵّ-כּ..." and p["variants"][0][0].endswith("زيّك...")


def test_workbook_lesson_7():
    from app.phrases import CATEGORIES, public_phrases

    # a review lesson: no new verbs and no story
    cats = ["l7-fem", "l7-dual", "l7-nums", "l7-count", "l7-verbs", "l7-read", "l7-say", "l7-say2"]
    mine = [c for c in CATEGORIES if c["lesson"] == "7" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "8" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "9" and not WORD_TOPICS(c)]
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
    mine = [c for c in CATEGORIES if c["lesson"] == "10" and not WORD_TOPICS(c)]
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


def test_workbook_level_2_lesson_1():
    from app.phrases import CATEGORIES, public_phrases

    # the second workbook (שלב ב') opens with the lesson it numbers the tenth: five verbs, and the future after لازم and رح
    cats = ["l2.1-verbs", "l2.1-past", "l2.1-must", "l2.1-will", "l2.1-read", "l2.1-read2", "l2.1-say", "l2.1-say2", "l2.1-story"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.1" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.1-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "רַח ולַאזֵם" in hw["task"] and "רַבַּט / יֻרְבֹּט – קשר" in hw["task"]
    story = mine[cats.index("l2.1-story")]
    assert story["name"] == "סיפור המכולת" and story["story"] and "voices" not in story
    assert mine[0]["unit"] == "פועל" and mine[0]["task"].endswith("בעבר ואז בעתיד")
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [5, 40, 24, 24, 12, 11, 12, 11, 29]
    reads = ("l2.1-read", "l2.1-read2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.1-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # a verb is said in the past and the future together
    assert by["l2.1-verbs", "רץ (עבר, עתיד)"]["tr"] == "רַכַּדֿ, יֻרְכֹּדֿ" and by["l2.1-verbs", "שלח (עבר, עתיד)"]["tr"] == "בַּעַת', יִבְּעַת'"
    assert by["l2.1-past", "אני רצתי"]["tr"] == "אַנַא רַכַּדֵֿת" and by["l2.1-past", "היא שילמה"]["tr"] == "הִיֵّ דַפְעַת"
    assert by["l2.1-past", "הם קשרו"]["variants"][0][1] == "h u m m e | r a b a tˤ u"
    # لازم and رح stay as they are; the verb after them is in the future
    assert by["l2.1-must", "את צריכה לראות"]["tr"] == "אִנְתִי לַאזֵם תְשוּפִי" and by["l2.1-must", "הם צריכים לקשור"]["tr"] == "הֹםّ לַאזֵם יֻרֻבְּטוּ"
    assert by["l2.1-will", "אני הולך לרוץ"]["tr"] == "אַנַא רַח אַרְכֹּדֿ" and by["l2.1-will", "אנחנו הולכים לשתות"]["tr"] == "אִחְנַא רַח נִשְרַבּ"
    p = next(p for p in PHRASES if p["cat"] == "l2.1-story" and p["seq"] == 10)
    assert "אֻרְבֹּט (א)לִחְמַאר מַטְרַח מַא יֵקוּל-לַّכּ צַאחְבּ-וֹ" in p["tr"] and "קְשׁוֹר" in p["he_say"]


def test_workbook_level_2_lesson_2():
    from app.phrases import CATEGORIES, public_phrases
    from app.workbook import _present_of

    # the workbook's eleventh lesson: the days of the week and the present, which is بـ on the future form
    cats = ["l2.2-days", "l2.2-pres", "l2.2-read", "l2.2-read2", "l2.2-read3", "l2.2-say", "l2.2-say2", "l2.2-story", "l2.2-fread"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.2" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.2-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "ואז בהוו'ה" in hw["task"]
    story = mine[cats.index("l2.2-story")]
    assert story["name"] == "סיפור הפלאפל" and story["story"] and "voices" not in story
    assert mine[0]["unit"] == "מילה"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [10, 24, 8, 8, 8, 8, 8, 19, 11]
    reads = ("l2.2-read", "l2.2-read2", "l2.2-read3", "l2.2-fread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.2-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2.2-days", "יום שלישי"]["tr"] == "יוֹם אֵ(ל)תַّלַאתַא" and by["l2.2-days", "חודש"]["tr"] == "שַהֵר"
    # the prefix takes the vowel the form needs, and "we" may take مـ
    assert by["l2.2-pres", "אני שואל"]["tr"] == "אַנַא בַּסְאַל" and by["l2.2-pres", "אתה שואל"]["tr"] == "אִנְתֵ בְּתִסְאַל"
    assert by["l2.2-pres", "הוא רואה"]["tr"] == "הוּّ בִּישוּף" and by["l2.2-pres", "אנחנו רואים"]["tr"] == "אִחְנַא בִּנְשוּף"
    assert by["l2.2-pres", "הם שותים"]["variants"][0][1] == "h u m m e | b i ʃ r a b u"
    assert _present_of("منيجي") == ("מְנִיגִ'י", ["m n iː dʒ i"]) and _present_of("بيشوفني")[0] == "בִּישוּפ-נִי"
    assert _present_of("باكل")[0] == "בַּאכֹּל" and _present_of("بيت") is None and _present_of("بدّي") is None
    p = next(p for p in PHRASES if p["cat"] == "l2.2-fread" and p["seq"] == 5)
    assert p["tr"].endswith("וּבְּנִשְרַבּ שַאיְ מַע בַּעַדֿ.")   # "and" on a present form


def test_workbook_level_2_lesson_3():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's twelfth lesson: the clock, and בניין שני (פַעַّל) beside the פִעֵל verbs it is made from
    cats = ["l2.3-hours", "l2.3-clock", "l2.3-talk", "l2.3-read", "l2.3-read2", "l2.3-say", "l2.3-say2", "l2.3-verbs", "l2.3-past",
            "l2.3-vread", "l2.3-vread2", "l2.3-vsay", "l2.3-vsay2", "l2.3-story"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.3" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.3-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "שַרַّבּ – השקה" in hw["task"] and "עבר – עתיד – הווה" in hw["task"]
    talk, song = mine[cats.index("l2.3-talk")], mine[cats.index("l2.3-story")]
    assert talk["story"] and talk["voices"] and song["story"] and "voices" not in song   # a talk has two speakers, a song none
    assert mine[0]["unit"] == "שעה" and mine[cats.index("l2.3-verbs")]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [12, 15, 6, 7, 7, 9, 8, 9, 16, 12, 12, 12, 12, 14]
    reads = ("l2.3-read", "l2.3-read2", "l2.3-vread", "l2.3-vread2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.3-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # an hour is feminine, and the minutes come after it: a quarter to ten is "ten less a quarter"
    assert by["l2.3-hours", "השעה שתיים"]["tr"] == "אֵ(ל)סֵّיעַה תִנְתֵין" and by["l2.3-hours", "השעה אחת עשרה"]["tr"] == "אֵ(ל)סֵّיעַה חְדַעַש"
    assert by["l2.3-clock", "השעה רבע לעשר"]["tr"] == "אֵ(ל)סֵّיעַה עַשַרַה אִלַّא רֻבֵּע"
    assert by["l2.3-clock", "השעה אחת עשרה שלושים וחמש"]["variants"][0][1] == "i s s eː ʕ a | ħ d a ʕ a ʃ | u n u sˤ sˤ | u x a m s e"
    # a verb is said in the past and the future together; لبس beside لبّس in the past
    assert by["l2.3-verbs", "הוריד (עבר, עתיד)"]["tr"] == "נַזַّל, יְנַזֵّל" and by["l2.3-verbs", "השקה (עבר, עתיד)"]["tr"] == "שַרַّבּ, יְשַרֵّבּ"
    assert by["l2.3-past", "היא לבשה"]["tr"] == "הִיֵّ לִבְּסַת" and by["l2.3-past", "היא הלבישה"]["tr"] == "הִיֵّ לַבַּّסַת"
    assert by["l2.3-past", "אנחנו הלבשנו"]["variants"][0][1] == "ʔ i ħ n a | l a b b a s n a"
    # the song asks (שַרֵّבּ), where the exercises tell (שַרַّבּ)
    first = next(p for p in PHRASES if p["cat"] == "l2.3-story" and p["seq"] == 1)
    assert first["tr"] == "שַרֵّבְּ-נִי קַהְוֵה פַתֵّח לִי רַאס-י!"
    told = next(p for p in PHRASES if p["cat"] == "l2.3-vread2" and "קפה קר" in p["he"])
    assert "וּשַרַّבְּ-נִי קַהְוֵה בַּארְדֵה" in told["tr"]


def test_workbook_level_2_lesson_4():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's thirteenth lesson: five more of בניין שני, the three words for "that" (انّه, ما, الّي), and a phone call
    cats = ["l2.4-verbs", "l2.4-past", "l2.4-vread", "l2.4-vread2", "l2.4-vsay", "l2.4-vsay2", "l2.4-rel", "l2.4-rread", "l2.4-rread2",
            "l2.4-rsay", "l2.4-rsay2", "l2.4-words", "l2.4-talk", "l2.4-talk2"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.4" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.4-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "פַכַּّר / יְפַכֵּّר – חשב" in hw["task"]
    assert all(mine[cats.index(c)]["story"] and mine[cats.index(c)]["voices"] for c in ("l2.4-talk", "l2.4-talk2"))   # two speakers each
    assert mine[0]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [5, 16, 13, 13, 13, 13, 14, 7, 7, 7, 7, 12, 14, 11]
    reads = ("l2.4-vread", "l2.4-vread2", "l2.4-rread", "l2.4-rread2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.4-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2.4-verbs", "שר (עבר, עתיד)"]["tr"] == "עַ'נַّא, יְעַ'נִّי" and by["l2.4-verbs", "חשב (עבר, עתיד)"]["tr"] == "פַכַּّר, יְפַכֵּّר"
    # غنّى ends in a vowel: its past takes ـيت, and "she" and "they" drop it
    assert by["l2.4-past", "אני שרתי"]["tr"] == "אַנַא עַ'נֵّית" and by["l2.4-past", "היא שרה"]["tr"] == "הִיֵّ עַ'נַّת"
    assert by["l2.4-past", "הם חשבו"]["variants"][0][1] == "h u m m e | f a k k a r u"
    assert by["l2.4-rel", "הילד שהלך"]["tr"] == "אֵלְוַלַד אִלִّי רַאח" and by["l2.4-rel", "לפני שאצא מהבית שלו"]["tr"] == "קַבֵּל מַא אַטְלַע מִן בֵּית-וֹ"
    # the present of a פַעַّל verb: בִּי on "he", בַּ on "I"
    son = next(p for p in PHRASES if p["cat"] == "l2.4-rread" and "עברתי לבית חדש" in p["he"])
    assert "אִבְּנ-י בִּיפַכֵּّר יֻנְקֹל" in son["tr"]
    call = next(p for p in PHRASES if p["cat"] == "l2.4-talk2" and p["seq"] == 7)
    assert call["tr"].startswith("טַבַּעַן בַּחַ'בְּّרַ-כּ") and "אוֹתְךָ" in call["he_say"]
    lunch = next(p for p in PHRASES if p["cat"] == "l2.4-rread" and "לבדך" in p["he"])
    assert "כֻּנֵת בַּתְעַ'דַّא מַעַ-כּ" in lunch["tr"]   # "I", where بـ on تتغدّى would be "you"


def test_workbook_level_2_lesson_5():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's fourteenth lesson: גזרת ע"י, what صار does, and the garage story
    cats = ["l2.5-verbs", "l2.5-past", "l2.5-pres", "l2.5-sar", "l2.5-vread", "l2.5-vread2", "l2.5-vsay", "l2.5-words", "l2.5-story",
            "l2.5-ask", "l2.5-sread", "l2.5-ssay", "l2.5-adj"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.5" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.5-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "צַאר / יְצִיר – נהיה" in hw["task"]
    story = mine[cats.index("l2.5-story")]
    assert story["name"] == "סיפור המוסך" and story["story"] and "voices" not in story
    assert mine[0]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [6, 16, 16, 10, 8, 7, 7, 12, 14, 10, 12, 12, 10]
    reads = ("l2.5-vread", "l2.5-vread2", "l2.5-ask", "l2.5-sread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.5-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # the past's ا is the future's ي, and the past of "I" loses it: גַ'אבּ, יְגִ'יבּ, גִ'בֵּת
    assert by["l2.5-verbs", "הביא (עבר, עתיד)"]["tr"] == "גַ'אבּ, יְגִ'יבּ" and by["l2.5-verbs", "חיכה (עבר, עתיד)"]["tr"] == "אִסְתַנַّא, יִסְתַנַّא"
    assert by["l2.5-past", "אני הבאתי"]["tr"] == "אַנַא גִ'בֵּת" and by["l2.5-past", "היא נהיתה"]["tr"] == "הִיֵّ צַארַת"
    assert by["l2.5-pres", "הוא מביא"]["tr"] == "הוּّ בִּיגִ'יבּ" and by["l2.5-pres", "אנחנו נהיים"]["variants"][0][1] == "ʔ i ħ n a | b i n sˤ iː r"
    assert by["l2.5-sar", "אתה כבר חצי שעה פה?"]["tr"] == "צַאר-לַכּ נֻצّ סֵיעַה הוֹן?"
    # استنّى is the past of "he"; the present of "I" is made from the future, which is written the same
    line = next(p for p in PHRASES if p["cat"] == "l2.5-story" and p["seq"] == 7)
    assert "צַאר-לִי נֻצّ סֵיעַה בַּסְתַנַّא-כּ הוֹן!" in line["tr"] and "לְךָ" in line["he_say"]
    assert next(p for p in PHRASES if p["cat"] == "l2.5-story" and p["seq"] == 8)["tr"].startswith("טַיֵّבּ וֵינַ-כּ חַבִּיבּ-י?")
    assert by["l2.5-adj", "גדול, גדולה, גדולים"]["tr"] == "כְּבִּיר, כְּבִּירֵה, כְּבַּאר"


def test_workbook_level_2_lesson_6():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's fifteenth lesson: בניין שלישי, the old jar's story, and "him" after a vowel
    cats = ["l2.6-verbs", "l2.6-past", "l2.6-pres", "l2.6-vread", "l2.6-vread2", "l2.6-vread3", "l2.6-vsay", "l2.6-words", "l2.6-story",
            "l2.6-ask", "l2.6-him"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.6" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.6-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "לַאקַא / יְלַאקִי – מצא" in hw["task"]
    story = mine[cats.index("l2.6-story")]
    assert story["name"] == "סיפור הכד העתיק" and story["story"] and "voices" not in story
    assert mine[0]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [5, 16, 16, 8, 8, 7, 7, 14, 20, 10, 12]
    reads = ("l2.6-vread", "l2.6-vread2", "l2.6-vread3", "l2.6-ask")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.6-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2.6-verbs", "עזר (עבר, עתיד)"]["tr"] == "סַאעַד, יְסַאעֵד" and by["l2.6-verbs", "מצא, פגש (עבר, עתיד)"]["tr"] == "לַאקַא, יְלַאקִי"
    assert by["l2.6-past", "אני מצאתי"]["tr"] == "אַנַא לַאקֵית" and by["l2.6-past", "היא עזרה"]["tr"] == "הִיֵّ סַאעַדַת"
    assert by["l2.6-pres", "הוא עוזר"]["tr"] == "הוּّ בִּיסַאעֵד" and by["l2.6-pres", "אנחנו מוצאים"]["variants"][0][1] == "ʔ i ħ n a | b i n l aː ʔ i"
    # after a vowel "him" is a ه that is hardly heard, and "her" is whole
    assert by["l2.6-him", "אנחנו פתחנו אותו"]["tr"] == "אִחְנַא פַתַחְנַא-ה" and by["l2.6-him", "אנחנו פתחנו אותה"]["tr"] == "אִחְנַא פַתַחְנַא-הַא"
    assert by["l2.6-him", "ה' ישמור אותו"]["variants"][0][1] == "ʔ a l l a | j x a l l iː h"
    line = next(p for p in PHRASES if p["cat"] == "l2.6-story" and p["seq"] == 8)
    assert "תַעַאל סַאעֵד-נִי וִחְיַאתַ-כּ" in line["tr"] and "בְּחַיֶּיךָ" in line["he_say"]   # a request: סַאעֵד, not the past's סַאעַד


def test_workbook_level_2_lesson_7():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's sixteenth lesson: the imperative, more of פִעֵל, and Yusef's bus
    cats = ["l2.7-imp", "l2.7-verbs", "l2.7-past", "l2.7-pres", "l2.7-ex", "l2.7-fill", "l2.7-fill2", "l2.7-isay", "l2.7-words", "l2.7-pre",
            "l2.7-story", "l2.7-ask", "l2.7-sread", "l2.7-ssay"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.7" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.7-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "ואז בציווי" in hw["task"] and "וִקֵע / יוֹקַע – נפל" in hw["task"]
    story = mine[cats.index("l2.7-story")]
    assert story["name"] == "סיפור יוסף והאוטובוס" and story["story"] and "voices" not in story
    assert mine[cats.index("l2.7-verbs")]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [19, 9, 16, 16, 5, 6, 6, 7, 14, 8, 17, 10, 9, 12]
    reads = ("l2.7-fill", "l2.7-fill2", "l2.7-ask", "l2.7-sread")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.7-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # the future without its first letter; two consonants at the start take a helping ا with the future's vowel
    assert by["l2.7-imp", "חשוב!"]["tr"] == "פַכֵּّר!" and by["l2.7-imp", "שתה!"]["tr"] == "אִשְרַבּ!" and by["l2.7-imp", "שב!"]["tr"] == "אֻקְעֹד!"
    assert by["l2.7-imp", "קח! קחי! קחו!"]["variants"][0][1] == "x u d | x u d i | x u d u"
    assert by["l2.7-imp", "אל תשתה קולה ואל תשתה זירו"]["tr"] == "לַא תִשְרַבּ כּוֹלַה וּמַא תִשְרַבּ זִירוֹ"   # "don't" is the future
    assert by["l2.7-verbs", "שכח (עבר, עתיד)"]["tr"] == "נִסִי, יִנְסַא" and by["l2.7-verbs", "נפל (עבר, עתיד)"]["tr"] == "וִקֵע, יוֹקַע"
    assert by["l2.7-past", "אני שכחתי"]["tr"] == "אַנַא נְסִית" and by["l2.7-past", "היא כעסה"]["tr"] == "הִיֵّ זִעְלַת"
    assert by["l2.7-pres", "הוא רץ"]["tr"] == "הוּّ בְּיֻרְכֹּדֿ" and by["l2.7-pres", "הוא כועס"]["tr"] == "הוּّ בִּיזְעַל"
    line = next(p for p in PHRASES if p["cat"] == "l2.7-story" and p["seq"] == 12)
    assert line["tr"].startswith("יַא שַבַּאבּ, סַאעְדוּ-נִי, וַאללַّה וְקִעֵת")
    go = next(p for p in PHRASES if p["cat"] == "l2.7-fill" and "לכי" in p["he"])
    assert "יַאללַّה רוּחִי וּבַּעַדֵין תַעַאלִי" in go["tr"]


def test_workbook_level_2_lesson_8():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's seventeenth lesson: גזרת הכפולים, عم for what is happening now, and the song شاطر شاطر
    cats = ["l2.8-verbs", "l2.8-past", "l2.8-pres", "l2.8-vread", "l2.8-vread2", "l2.8-vsay", "l2.8-vsay2", "l2.8-am", "l2.8-amsay",
            "l2.8-amsay2", "l2.8-words", "l2.8-story"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.8" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.8-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "דַֿלّ / יְדַֿלّ – נשאר" in hw["task"] and "ורק אז בציווי" in hw["task"]
    song = mine[cats.index("l2.8-story")]
    assert song["name"] == "השיר: שַאטֵר שַאטֵר" and song["story"] and "voices" not in song
    assert mine[0]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [7, 16, 16, 10, 9, 10, 9, 9, 9, 8, 12, 10]
    reads = ("l2.8-vread", "l2.8-vread2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.8-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    assert by["l2.8-verbs", "שם, הניח (עבר, עתיד)"]["tr"] == "חַטّ, יְחֻטّ" and by["l2.8-verbs", "נשאר, המשיך (עבר, עתיד)"]["tr"] == "דַֿלّ, יְדַֿלّ"
    # "I" and "you" take ـيت in the past, "she" and "they" only their ending
    assert by["l2.8-past", "אני נשארתי"]["tr"] == "אַנַא דַֿלֵّית" and by["l2.8-past", "היא נשארה"]["tr"] == "הִיֵّ דַֿלַّת"
    assert by["l2.8-past", "הם שמו"]["variants"][0][1] == "h u m m e | ħ a tˤ tˤ u"
    assert by["l2.8-pres", "אתה נשאר"]["tr"] == "אִנְתֵ בִּתְדַֿלّ" and by["l2.8-pres", "אנחנו אוהבים"]["tr"] == "אִחְנַא בִּנְחִבّ"
    assert by["l2.8-am", "אני מדבר איתך כעת"]["tr"] == "אַנַא עַםְ בַּחְכִּי מַעַ-כּ" and by["l2.8-am", "אני עובד כעת"]["tr"] == "אַנַא עַםְ בַּשְתְעֵ'ל"
    # a request (חֻטّ) beside the past (חַטّ), and the song's "we": מִנְ before the future
    order = next(p for p in PHRASES if p["cat"] == "l2.8-vread" and "צ'יפס" in p["he"])
    assert "חֻטّ-לִי כַּמַאן טְחִינֵה" in order["tr"] and "הוּّ חַטّ-לִי זַיّ מַא" in order["tr"]
    assert next(p for p in PHRASES if p["cat"] == "l2.8-story" and p["seq"] == 6)["tr"] == "מִנְחִבّ-וֹ וּמִנְדַֿלّ מִנְקוּל-לّוֹ שַאטֵר שַאטֵר"


def test_workbook_level_2_lesson_9():
    from app.phrases import CATEGORIES, public_phrases

    # the workbook's eighteenth and last lesson: the active participle, a song from Egypt, and the father, the son and the donkey
    cats = ["l2.9-part", "l2.9-part2", "l2.9-verbs", "l2.9-ex", "l2.9-pread", "l2.9-pread2", "l2.9-psay", "l2.9-psay2", "l2.9-psay3",
            "l2.9-words", "l2.9-song", "l2.9-story"]
    mine = [c for c in CATEGORIES if c["lesson"] == "2.9" and not WORD_TOPICS(c)]
    assert [c["id"] for c in mine] == cats + ["l2.9-hw"]
    hw = mine[-1]["write"]
    assert hw["lines"] == 5 and "בבינוני הפועל" in hw["task"] and "עַאש / יְעִיש – חי" in hw["task"]
    for c in ("l2.9-song", "l2.9-story"):
        assert mine[cats.index(c)]["story"] and "voices" not in mine[cats.index(c)]
    assert mine[cats.index("l2.9-verbs")]["unit"] == "פועל"
    pub = public_phrases()
    assert [sum(p["cat"] == c for p in pub) for c in cats] == [8, 16, 5, 17, 10, 10, 10, 9, 9, 14, 8, 14]
    reads = ("l2.9-pread", "l2.9-pread2")
    assert all(p["read"] == (p["cat"] in reads) and "#" not in p["ar"] for p in pub if p["cat"].startswith("l2.9-"))
    by = {(p["cat"], p["he"]): p for p in PHRASES}
    # the participle's four forms, and where the middle letter is weak a י comes in (رايح, نايم)
    assert by["l2.9-part", "גר, גרה, גרים, גרות"]["tr"] == "סַאכֵּן, סַאכְּנֵה, סַאכְּנִין, סַאכְּנַאת"
    assert by["l2.9-part", "ישן, ישנה, ישנים, ישנות"]["tr"] == "נַאיֵם, נַאיְמֵה, נַאיְמִין, נַאיְמַאת"
    assert by["l2.9-part2", "ישב – יושב"]["tr"] == "קַעַד, קַאעֵד" and by["l2.9-part2", "אהב, רצה – אוהב, רוצה"]["tr"] == "חַבّ, חַאבֵּבּ"
    assert by["l2.9-verbs", "ישן (עבר, עתיד)"]["tr"] == "נַאם, יְנַאם" and by["l2.9-verbs", "חי (עבר, עתיד)"]["tr"] == "עַאש, יְעִיש"
    # the habit is the present (בַּרוּח), this moment the participle (רַאיֵח)
    assert by["l2.9-ex", "כל יום שני אני הולך לחבר שלי"]["tr"].endswith("אַנַא בַּרוּח לַעִנְד צַאחְבּ-י")
    assert by["l2.9-ex", "אני הולך לחבר שלי עכשיו"]["tr"] == "אַנַא רַאיֵח לַעִנְד צַאחְבּ-י הַלַّק"
    song = [p for p in PHRASES if p["cat"] == "l2.9-song"]
    assert song[2]["tr"].endswith("עַלַא בֻּעְדַ-כּ") and song[3]["tr"].endswith("חַבִּיבּ בַּעְדַ-כּ")   # your distance, and after you
    assert "מַא בֵּינַא זַמַאן" in song[5]["tr"]
    last = next(p for p in PHRASES if p["cat"] == "l2.9-story" and p["seq"] == 14)
    assert last["tr"].startswith("אִנְתֵ עַאיֵש וִלַّא מַאיֵת") and "בְּךָ" in last["he_say"]


def test_sign_in_guards_the_practice_endpoints():
    from fastapi.testclient import TestClient

    from app.main import create_app

    VERB = next(p for p in PHRASES if p["cat"] == "l1-verbs")

    class FakeAuth:
        url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset(), frozenset()

        def __init__(self):
            self.rows, self.cls, self.vids, self.txts, self.days, self.wrote = [], [], [], [], {"dailies": [], "library": []}, {}
            self.rank = frozenset()

        def writings(self, token):
            return {t: lines for (u, t), lines in self.wrote.items() if u == "u1"}

        def save_writing(self, token, user_id, topic, lines):
            self.wrote[user_id, topic] = lines

        def all_writings(self):
            return [{"user_id": u, "topic": t, "lines": lines} for (u, t), lines in self.wrote.items()]

        def classes(self, fresh=False):
            return self.cls

        def ranked(self, fresh=False):
            return self.rank

        def set_tutor(self, email, on, by):
            if self.rank is None:
                raise AuthUnavailable("supabase answered 404: no table")
            self.rank = self.rank | {email} if on else self.rank - {email}
            self.by = by

        def save_class(self, id, name, emails, nicknames, lessons, homeworks=(), dailies=None, subject=""):
            row = {"name": name, "emails": emails, "nicknames": nicknames, "lessons": lessons, "homeworks": list(homeworks),
                   **({} if dailies is None else {"dailies": list(dailies)}), **({"subject": subject} if id is None and subject else {})}
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

        def rename_level_video(self, table, id, title):
            next(v for v in self.days[table] if v["id"] == id)["title"] = title

        def order_level_videos(self, table, ids):
            self.days[table].sort(key=lambda v: ids.index(v["id"]))

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

        def all_attempts(self, limit=200_000):
            return [{**r, "user_id": "u2"} for r in self.attempts("")[:2]][:limit]

        def activity(self, hours=24, bucket_minutes=15):
            self.activity_args = hours, bucket_minutes
            return [{"bucket": "2026-10-13T10:00:00+00:00", "attempts": 4, "active_students": 2}]

        def all_attempt_issues(self):
            return []

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
        assert c.get("/api/me", headers=good).json() == {"email": "A@b.c", "teacher": True, "tutor": True, "classes": [], "lessons": ALL, "dailies": []}
        activity = c.get("/api/activity?hours=12&bucket_minutes=30", headers=good)
        assert activity.json() == {"hours": 12, "bucket_minutes": 30, "buckets": [
            {"bucket": "2026-10-13T10:00:00+00:00", "attempts": 4, "active_students": 2}]}
        assert c.app.state.auth.activity_args == (12, 30)
        assert c.get("/api/activity?hours=0", headers=good).status_code == 422
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
        assert line1 == {"id": 1_001_000, "cat": "t1", "he": "שלום", "tr": "מַרְחַבַּא", "ar": "مرحبا",
                         "ar_pointed": "مَرْحَبَا", "seq": 1}
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
        # a video of the library is renamed, and the library is put in the teacher's order
        rename = lambda id, **body: c.put(f"/api/library/{id}", json=body, headers=good).status_code
        order = lambda ids: c.put("/api/library/order", json={"order": ids}, headers=good).status_code
        assert rename(1, title="  the  first ") == 200 and rename(1, title="") == 400 and rename(1, title="x" * 200) == 400 and rename(9, title="x") == 404
        assert order([2]) == 409 and order([2, 2]) == 409 and order([2, 1, 9]) == 409 and order("x") == 400 and order(["2", "1"]) == 400
        assert order([2, 1]) == 200
        shelves = c.get("/api/library", headers=good).json()["library"]
        assert [(v["id"], v["title"]) for v in shelves] == [(2, "one"), (1, "the first")]
        c.app.state.auth.teachers = frozenset()
        assert c.get("/api/library", headers=good).json()["library"] == [{k: v for k, v in s.items() if k != "url"} for s in shelves]   # all of it, without the links
        assert shelf(title="x", url=link) == 403 and c.delete("/api/library/1", headers=good).status_code == 403
        assert rename(1, title="x") == 403 and order([1, 2]) == 403
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
        # a recording with speech completes the exercise even when it did not match; it is also counted diagnostically
        from app.main import summary
        off = summary([{"cat": VERB["cat"], "phrase": VERB["he"], "score": 12, "unrelated": True, "created_at": "2026-10-09T11:00:00+00:00"},
                       {"cat": VERB["cat"], "phrase": VERB["he"], "score": 70, "unrelated": False, "created_at": "2026-10-09T10:00:00+00:00"}])
        assert (off["best"], off["tries"], off["missed"]) == ({PHRASES.index(VERB): 70}, {PHRASES.index(VERB): 2}, {PHRASES.index(VERB): 1})
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
        progress = c.get("/api/progress", headers={"Authorization": "Bearer good"}).json()
        assert {k: progress[k] for k in ("best", "tries", "recordings", "days")} == {
            "best": {str(PHRASES.index(VERB)): 88}, "tries": {str(PHRASES.index(VERB)): 2}, "recordings": 3, "days": 2}
        assert 0 <= progress["week_days"] <= 7
        assert set(progress["streak"]) == {"current", "best", "last"}
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:   # no sign-in: nothing to show
        assert c.get("/api/progress").json() == {"best": {}, "tries": {}, "recordings": 0, "days": 0,
                                                  "week_days": 0, "streak": {"current": 0, "best": 0, "last": None}}
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


def test_a_tutor_flags_a_recording_scored_wrong(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SAWTAK_API_KEY", "")   # no native baseline from a local .env: the score is the stub's own

    from app.auth import AuthUnavailable
    from app.main import MODEL_ID, create_app

    class FakeAuth:
        url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset(), frozenset({"t@b.c"})

        def __init__(self):
            self.cls, self.flags, self.table, self.rank, self.calibration_rows = [], [], True, frozenset(), []
            self.rule_rows = {}

        def user(self, token):
            return {"t": {"id": "u1", "email": "T@b.c"}, "s": {"id": "u2", "email": "s@b.c"}}.get(token)

        def classes(self, fresh=False):
            return self.cls

        def ranked(self, fresh=False):
            return self.rank

        def set_tutor(self, email, on, by):
            if self.rank is None:
                raise AuthUnavailable("supabase answered 404: no table")
            self.rank = self.rank | {email} if on else self.rank - {email}
            self.by = by

        def level_videos(self, table, fresh=False):
            return []

        def texts(self, fresh=False):
            return []

        def add_flag(self, row, audio, mime, name):
            if not self.table:
                raise AuthUnavailable("supabase answered 404: no table")
            self.flag_recordings = getattr(self, "flag_recordings", {})
            self.flag_recordings[len(self.flags) + 1] = (audio, mime)
            self.flags.append({**row, "audio": name})
            return True

        def list_flags(self, status="pending"):
            if status != "pending":
                return []
            return [{"id": i + 1, **row, "status": status} for i, row in enumerate(self.flags)]

        def review_flag(self, flag_id, values):
            self.review = (flag_id, values)

        def flag_audio(self, flag_id):
            return getattr(self, "flag_recordings", {}).get(flag_id)

        def calibrations(self, status=None):
            return [r for r in self.calibration_rows if not status or r["status"] == status]

        def add_calibration(self, row):
            made = {"id": len(self.calibration_rows) + 1, **row}
            self.calibration_rows.append(made)
            return made

        def update_calibration(self, calibration_id, values):
            next(r for r in self.calibration_rows if r["id"] == calibration_id).update(values)

        def delete_calibration(self, calibration_id):
            before = len(self.calibration_rows)
            self.calibration_rows = [r for r in self.calibration_rows if r["id"] != calibration_id]
            return len(self.calibration_rows) < before

        def scoring_rules(self):
            if self.rule_rows is None:
                raise AuthUnavailable("supabase answered 404: no table")
            return list(self.rule_rows.values())

        def set_scoring_rule(self, rule_id, active, by):
            if self.rule_rows is None:
                raise AuthUnavailable("supabase answered 404: no table")
            self.rule_rows[rule_id] = {"id": rule_id, "active": active, "updated_by": by}

        def all_attempt_issues(self):
            return [{"user_id": f"u{i}", "phrase": TOILET["he"], "score": 60,
                     "issues": [{"expected": "ħ", "heard": "χ", "score": 20}],
                     "model": MODEL_ID, "version": "v"} for i in range(3)]

    t = np.arange(16000) / 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes())
    pid = PHRASES.index(TOILET)
    send = lambda c, token, **data: c.post("/api/flags", data={"phrase_id": pid, **data}, headers={"Authorization": f"Bearer {token}"},
                                           files={"audio": ("rec", buf.getvalue(), "audio/wav")})
    # said with a Hebrew chet, as the model hears it: the tutor says it was right
    with TestClient(create_app(scorer_for(emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])), auth=FakeAuth())) as c:
        a = c.app.state.auth
        assert c.get("/api/me", headers={"Authorization": "Bearer t"}).json()["tutor"] is True
        assert "tutor" not in c.get("/api/me", headers={"Authorization": "Bearer s"}).json()
        assert c.post("/api/flags", data={"phrase_id": pid}, files={"audio": ("rec", b"x", "audio/wav")}).status_code == 401
        assert send(c, "s").status_code == 403 and not a.flags   # tutors only
        r = send(c, "t", note="  אמרתי  ח נכונה ")
        row, = a.flags
        assert r.status_code == 200 and r.json() == {"ok": True, "audio": True, "flag": row}, r.text
        assert row["email"] == "t@b.c" and row["cat"] == TOILET["cat"] and row["he"] == TOILET["he"] and row["note"] == "אמרתי ח נכונה"
        assert row["ipa"] == "w eː n | i l ħ a m m aː m" and "χ" in row["heard_ipa"] and row["score"] == row["result"]["score"] < 100
        assert row["result"]["tips"][0]["key"] == "ħ>x" and row["result"]["trace"]["tokens"]
        assert "phoneme_heatmap" not in row["result"]
        assert send(c, "t", phrase_id=10**6).status_code == 404
        a.teachers = frozenset({"s@b.c"})   # a teacher flags too
        assert c.get("/api/me", headers={"Authorization": "Bearer s"}).json()["tutor"] is True and send(c, "s").status_code == 200
        hs = {"Authorization": "Bearer s"}
        listed = c.get("/api/flags", headers=hs)
        assert listed.status_code == 200 and len(listed.json()["flags"]) == 2
        played = c.get("/api/flags/1/audio", headers=hs)
        assert played.status_code == 200 and played.content == buf.getvalue()
        assert c.get("/api/flags/999/audio", headers=hs).status_code == 404
        review = c.put("/api/flags/1", headers=hs, json={"status": "confirmed", "category": "recognition",
                       "false_issues": [{"word": 1, "expected": "ħ", "heard": "χ"}], "review_note": "  תקין  "})
        assert review.status_code == 200
        assert a.review[0] == 1 and a.review[1]["false_issues"] == [{"word": 1, "expected": "ħ", "heard": "χ"}]
        assert a.review[1]["review_note"] == "תקין" and a.review[1]["reviewed_by"] == "s@b.c"
        assert c.put("/api/flags/1", headers=hs, json={"status": "pending", "category": "other"}).status_code == 400
        patterns = c.get("/api/attempt-patterns", headers=hs).json()["patterns"]
        assert patterns[0]["expected"] == "ħ" and patterns[0]["heard"] == "χ"
        assert patterns[0]["students"] == 3 and patterns[0]["student_rate"] == 1
        proposal = c.post("/api/calibrations", headers=hs, json={"expected": "ħ", "heard": "χ",
                          "evidence": {"reports": 5, "reporters": 2, "phrases": [TOILET["he"]]}})
        assert proposal.status_code == 200 and proposal.json()["calibration"]["status"] == "draft"
        applied = c.put("/api/calibrations/1", headers=hs, json={"status": "applied", "floor": 70,
                        "scope": "phrase", "phrase": TOILET["he"]})
        assert applied.status_code == 200 and applied.json()["active"] == 1
        assert c.app.state.scorer.calibrations[("ħ", "χ", TOILET["he"])] == 70
        deleted = c.delete("/api/calibrations/1", headers=hs)
        assert deleted.status_code == 200 and deleted.json()["active"] == 0
        assert not c.app.state.scorer.calibrations and c.delete("/api/calibrations/1", headers=hs).status_code == 404
        dismissed = c.post("/api/calibrations", headers=hs, json={"expected": "r", "heard": "ɣ",
                           "evidence": {"dismissed": True, "source": "confirmed_flags"}})
        assert dismissed.status_code == 200 and dismissed.json()["calibration"]["status"] == "draft"
        assert dismissed.json()["calibration"]["evidence"] == {"dismissed": True, "source": "confirmed_flags"}
        # the rules of pronunciation-rules.md: all on, a teacher switches one off, and the scorer follows at once
        listed = c.get("/api/scoring-rules", headers=hs).json()
        assert listed["stored"] and all(r["active"] for r in listed["rules"]) and len(listed["rules"]) >= 20
        assert all(isinstance(r["detectable"], bool) and r["title"] and r["description"] for r in listed["rules"])
        assert c.put("/api/scoring-rules/dialect_Q", headers=hs, json={"active": False}).json()["disabled"] == ["dialect_Q"]
        assert c.app.state.scorer.disabled == {"dialect_Q"} and a.rule_rows["dialect_Q"]["updated_by"] == "s@b.c"
        assert not next(r for r in c.get("/api/scoring-rules", headers=hs).json()["rules"] if r["id"] == "dialect_Q")["active"]
        assert c.put("/api/scoring-rules/nope", headers=hs, json={"active": False}).status_code == 400
        assert c.put("/api/scoring-rules/margins", headers=hs, json={"active": "no"}).status_code == 400
        assert c.put("/api/scoring-rules/margins", headers={"Authorization": "Bearer t"}, json={"active": False}).status_code == 403
        assert c.put("/api/scoring-rules/dialect_Q", headers=hs, json={"active": True}).json()["disabled"] == []
        a.rule_rows = None   # no table yet: the list still shows, all on, with the SQL to create it
        missing = c.get("/api/scoring-rules", headers=hs).json()
        assert not missing["stored"] and "create table public.scoring_rules" in missing["create_sql"]
        assert c.put("/api/scoring-rules/margins", headers=hs, json={"active": False}).status_code == 503
        a.teachers = frozenset()
        a.table = False   # before the table is created: the command to create it
        r = send(c, "t")
        assert r.status_code == 503 and "create table public.flags" in r.json()["detail"]
        # a tutor is let in without a class, a student is not
        a.cls = [{"id": 1, "name": "B", "emails": ["other@b.c"], "nicknames": {}, "lessons": []}]
        assert c.get("/api/me", headers={"Authorization": "Bearer t"}).status_code == 200
        assert c.get("/api/me", headers={"Authorization": "Bearer s"}).status_code == 403
        # the rank the teacher gives a student in the panel: theirs in every class, and it lets nobody in by itself
        tok = lambda t: {"Authorization": f"Bearer {t}"}
        rank = lambda t, **body: c.put("/api/tutors", json=body, headers=tok(t))
        a.users, a.all_attempts, a.all_writings = lambda: [{"id": "u2", "email": "s@b.c", "name": ""}], lambda: [], lambda: []
        assert rank("s", email="s@b.c", tutor=True).status_code == 403   # teachers only (s is not in a class either)
        a.cls[0]["emails"].append("s@b.c"); a.teachers = frozenset({"t@b.c"})
        assert rank("s", email="s@b.c", tutor=True).status_code == 403 and "tutor" not in c.get("/api/me", headers=tok("s")).json()
        assert rank("t", email="nobody", tutor=True).status_code == 400 and rank("t", email="s@b.c", tutor="yes").status_code == 400
        assert rank("t", email=" S@b.c ", tutor=True).status_code == 200 and a.rank == {"s@b.c"} and a.by == "t@b.c"
        assert c.get("/api/me", headers=tok("s")).json()["tutor"] is True and send(c, "s").status_code == 503   # may flag (the table is still missing)
        panel = c.get("/api/class", headers=tok("t")).json()
        assert [(s["email"], s["tutor"], s.get("tutor_fixed")) for s in panel["students"]] == [("s@b.c", True, None), ("other@b.c", False, None)]
        assert rank("t", email="s@b.c", tutor=False).status_code == 200 and "tutor" not in c.get("/api/me", headers=tok("s")).json()
        a.rank = frozenset({"out@b.c"})   # ranked, but in no class: not let in
        known = a.user
        a.user = lambda token: {"id": "u3", "email": "out@b.c"} if token == "x" else known(token)
        assert c.get("/api/me", headers=tok("x")).status_code == 403
        a.rank = None   # before the table is created: the panel says what to run, and so does giving the rank
        assert "create table public.tutors" in c.get("/api/class", headers=tok("t")).json()["setup"][-1]["sql"]
        r = rank("t", email="s@b.c", tutor=True)
        assert r.status_code == 503 and "create table public.tutors" in r.json()["detail"]


def test_switched_off_rules_bring_back_the_old_behaviour():
    kaddeesh = emissions(["k", "a", "d", "eː", "ʃ", "i", "s", "aː", "ʕ", "a"])
    s = scorer_for(kaddeesh)
    assert not s.score(None, TIME)["tips"]                                    # rural ق accepted
    s.set_disabled({"dialect_Q", "not-a-rule"})
    assert s.disabled == {"dialect_Q"}
    assert [t["key"] for t in s.score(None, TIME)["tips"]] == ["ʔ>k"]          # as before the document
    # the emphatic margin: off, plain t winning by less than twice is a mistake again
    phrase = {"variants": [("طين", "tˤ i n")]}
    s = margin_scorer(1.8)
    assert not s.score(None, phrase)["tips"]
    s.set_disabled({"margins"})
    assert [t["key"] for t in s.score(None, phrase)["tips"]] == ["tˤ>t"]
    # a section-3 rule off: the general tip and severity instead of the document's
    s = custom_scorer(["p", "f", "dʒ"], ["p", "i", "n", "dʒ", "aː", "n"])
    fin = {"variants": [("فنجان", "f i n dʒ aː n")]}
    assert s.score(None, fin)["tips"][0]["text"].startswith("נהגתה p")
    s.set_disabled({"rule_f"})
    tip = s.score(None, fin)["tips"][0]
    assert tip["key"] == "f>p" and not tip["text"].startswith("נהגתה p") and tip["severity"] == 2
    # the final vowel of ة: off, i for e is marked down like any other vowel
    s = scorer_for(emissions(["ʔ", "a", "h", "w", "i"]))
    assert s.score(None, COFFEE)["words"][0]["phonemes"][-1]["score"] >= 95
    s.set_disabled({"final_vowel", "near_vowel"})
    assert s.score(None, COFFEE)["words"][0]["phonemes"][-1]["score"] < 50


THANKS = {"variants": [("شكرا كتير", "ʃ u k r a n | k t iː r")]}
CLUSTER_SYMS = ["r", "t", "iː", "u", "ʃ"]


def test_vowel_between_two_consonants_is_a_mistake_mid_sentence():
    clean = custom_scorer(CLUSTER_SYMS, ["ʃ", "u", "k", "r", "a", "n", "k", "t", "iː", "r"]).score(None, THANKS)
    assert not clean["tips"] and clean["score"] >= 95
    s = custom_scorer(CLUSTER_SYMS, ["ʃ", "u", "k", "r", "a", "n", "k", "a", "t", "iː", "r"])   # katiir
    r = s.score(None, THANKS)
    t = next(p for p in r["words"][1]["phonemes"] if p["ipa"] == "t")
    assert t["inserted"] == "a" and t["score"] == 60 and [x["key"] for x in r["tips"]] == ["cluster"]
    assert r["tips"][0]["severity"] == 1 and "ktiir" in r["tips"][0]["text"] and r["score"] < clean["score"]
    letter = next(l for l in r["words"][1]["letters"] if "ת" in l["t"])
    assert letter["why"].startswith("נוספה") and "ktiir" in letter["tip"]
    s.set_disabled({"rule_cluster"})                       # switched off: not checked, as before
    assert not s.score(None, THANKS)["tips"]


def test_vowel_in_the_cluster_that_opens_the_sentence_is_fine():
    # ketiir at the start of a sentence (6.3 #16), and the way into a glide (shuway)
    r = custom_scorer(["t", "iː", "r"], ["k", "e", "t", "iː", "r"]).score(None, {"variants": [("كتير", "k t iː r")]})
    assert not r["tips"] and r["score"] >= 95
    phrase = {"variants": [("شو شوي", "ʃ u | ʃ w a j j")]}
    r = custom_scorer(["ʃ", "u", "j"], ["ʃ", "u", "ʃ", "u", "w", "a", "j"]).score(None, phrase)
    assert not any(t["key"] == "cluster" for t in r["tips"])


LAWYER = {"variants": [("محامي", "m u ħ aː m i")]}


@pytest.mark.parametrize("said,flagged", [
    (["m", "u", "ħ", "aː", "m", "i"], False),
    (["m", "ħ", "aː", "m", "i"], True),            # mħaami: the u swallowed
    (["m", "e", "ħ", "aː", "m", "i"], True),       # said as e
    (["m", "o", "ħ", "aː", "m", "i"], False),      # o is a near vowel
])
def test_kubutz_is_a_distinct_vowel(said, flagged):
    r = custom_scorer(["u", "o"], said).score(None, LAWYER)
    u = r["words"][0]["phonemes"][1]
    assert u["ipa"] == "u" and u["flagged"] == flagged, u
    if flagged:
        assert r["tips"][0]["text"].startswith("ה־u") and r["tips"][0]["severity"] == 1


def test_only_short_u_after_a_consonant_is_flagged():
    s = custom_scorer(["u", "o"], ["m", "ħ", "aː", "m", "i"])
    s.set_disabled({"rule_kubutz"})                       # switched off: like any other short vowel
    assert not s.score(None, LAWYER)["words"][0]["phonemes"][1]["flagged"]
    # the و of "and" (u m a) and a long uː (ʃ uː f) are not the kubutz rule's
    r = custom_scorer(["u"], ["m", "a"]).score(None, {"variants": [("وما", "u m a")]})
    assert not r["words"][0]["phonemes"][0]["flagged"]
    r = custom_scorer(["u", "uː", "f", "ʃ"], ["ʃ", "e", "f"]).score(None, {"variants": [("شوف", "ʃ uː f")]})
    assert not r["words"][0]["phonemes"][1]["flagged"] and not r["tips"]


def test_teacher_trace_follows_every_stage():
    # ħ said as χ, then forgiven by a teacher floor: each stage of the trace shows where the score came from
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    scorer = scorer_for(logp)
    assert "trace" not in scorer.score(None, TOILET)                     # students do not get it
    scorer.set_calibrations([{"expected": "ħ", "heard": "χ", "floor": 60}])
    t = scorer.score_logp(logp, TOILET, diagnostics=True)["trace"]
    assert t["config"]["flag_below"] == 50 and t["config"]["leniency"] == 0.35 and t["variants"][0]["chosen"]
    assert t["config"]["word_min_weight"] == 0.35
    h = next(x for x in t["tokens"] if x["ipa"] == "ħ")
    assert [s["stage"] for s in h["stages"]] == ["gop", "exemption", "insertion", "baseline", "calibration"]
    assert h["stages"][0]["score"] < 50 and h["stages"][4]["applied"] and h["final"] == 60
    assert h["competitor"] == "χ" and h["rule"]["severity"] == 3 and not h["flagged"]
    assert t["sentence"]["final"] == round(0.5 * t["sentence"]["phone_mean"] + 0.5 * t["sentence"]["word_mean"])


def test_trace_carries_frames_curves_and_paths():
    from app.scorer import frame_energy

    r = scorer_for(emissions(["w", "eː", "n", "i", "l", "ħ", "a", "m", "aː", "m"])).score(None, TOILET, diagnostics=True)
    t = r["trace"]
    assert t["frames"]["count"] > 0 and t["frames"]["energy"] is None                 # no audio: no energy
    tok = t["tokens"][0]
    assert len(tok["curves"]["target"]) == len(tok["curves"]["blank"]) and tok["curves"]["prev"] is None
    assert all(v["tokens"] for v in t["variants"]) and sum(v["chosen"] for v in t["variants"]) == 1
    e = frame_energy(np.r_[np.zeros(320), np.ones(320)], 2)
    assert np.allclose(e, [0, 1])


def test_flag_threshold_per_sound():
    # ħ said as χ: flagged at the global 50, not once its own threshold is under the score; other sounds unchanged
    logp = emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"])
    scorer = scorer_for(logp)
    h = lambda r: next(p for w in r["words"] for p in w["phonemes"] if p["ipa"] == "ħ")
    before = h(scorer.score(None, TOILET))
    assert before["flagged"] and before["heard"] == "χ"
    scorer.cfg = replace(scorer.cfg, flag_below_sound={"ħ": before["score"]})
    r = scorer.score_logp(logp, TOILET, diagnostics=True)
    assert not h(r)["flagged"] and h(r)["score"] == before["score"]
    assert r["trace"]["config"]["flag_below_sound"] == {"ħ": before["score"]}


def test_flag_thresholds_belong_to_their_model():
    from app.scorer import scorer_config
    assert scorer_config("facebook/wav2vec2-xlsr-53-espeak-cv-ft").flag_below_sound == {"ħ": 20.0}
    assert scorer_config("checkpoints/ihki-ft").flag_below_sound == {}
    assert scorer_config("checkpoints/ihki-ft").flag_below == 50


def test_each_class_topic_opens_on_its_own_screen():
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:
        for path in ("/", "/arabic"):   # "/" stays Arabic
            page = c.get(path).text
            assert 'data-subject="arabic"' in page and "/static/logo-intro.svg" in page and "<title>אהלא וסהלא</title>" in page
        math = c.get("/math").text
        assert 'data-subject="math"' in math and "/static/logo-math.svg" in math and "<title>מתמטיקה בכיף</title>" in math
        assert "__SUBJECT__" not in math and "__START_LABEL__" not in math
        assert c.get("/history").status_code == 404
        assert c.get("/static/logo-math.svg").status_code == 200
        # its installed app has its own name, colour and start page; Arabic keeps the static manifest
        assert '<meta name="theme-color" content="#000000">' in math and 'href="/math/manifest.webmanifest"' in math
        assert '"__' not in math and '<meta name="theme-color" content="#4B6A2C">' in c.get("/").text
        assert re.search(r'<script src="/static/math\.js\?v=[0-9a-f]{7}" defer>', math)   # the path's script, stamped
        assert 'href="/static/manifest.webmanifest"' in c.get("/").text
        installed = c.get("/math/manifest.webmanifest")
        assert installed.headers["content-type"].startswith("application/manifest+json")
        assert {k: installed.json()[k] for k in ("id", "name", "start_url", "scope")} == {
            "id": "/math", "name": "מתמטיקה בכיף", "start_url": "/math", "scope": "/"}
        assert c.get("/arabic/manifest.webmanifest").status_code == 404


def test_an_account_is_sent_to_its_subjects_pages():
    from fastapi.testclient import TestClient
    from app.main import create_app, subject_code

    assert subject_code("מתמטיקה") == "math" and subject_code(" ערבית ") == "arabic"
    assert subject_code("חשבון") == subject_code("") == subject_code(None) == ""

    class FakeAuth:
        url, key, teachers, tutors, developers = "https://proj.supabase.co", "sb_publishable_x", frozenset(), frozenset(), {"d@b.c"}
        users_ = {"s": {"id": "u1", "email": "s@b.c"}, "t": {"id": "u2", "email": "t@b.c", "name": "T"},
                  "d": {"id": "u3", "email": "d@b.c"}}

        def __init__(self):
            self.cls, self.access = [], {"u2": {"user_id": "u2", "roles": ["admin"], "subject": "מתמטיקה"}}

        def user(self, token):
            return self.users_.get(token)

        def users(self):
            return list(self.users_.values())

        def classes(self, fresh=False):
            return self.cls

        def access_for(self, user_id):
            return self.access.get(user_id)

        def access_rows(self):
            return list(self.access.values())

        def set_admin_subject(self, target, subject, actor):
            self.access[target["id"]]["subject"] = subject

        def ranked(self, fresh=False):
            return frozenset()

        def __getattr__(self, name):   # the content tables /api/me reads: empty
            return lambda *a, **k: []

    tok = lambda t: {"Authorization": f"Bearer {t}"}
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=FakeAuth())) as c:
        auth = c.app.state.auth
        auth.cls = [{"id": 1, "name": "ט1", "emails": ["s@b.c"], "nicknames": {}, "lessons": [], "subject": "מתמטיקה"}]
        me = c.get("/api/me", headers=tok("s")).json()   # a student goes where their class is
        assert me["subject"] == "math" and me["classes"][0]["subject"] == "math"
        assert c.get("/api/me", headers=tok("t")).json()["subject"] == "math"   # an admin, where their own subject is
        assert "subject" not in c.get("/api/me", headers=tok("d")).json()   # a developer stays on the page they opened
        auth.cls[0]["subject"] = "חשבון"   # a subject outside the list sends nowhere
        assert "subject" not in c.get("/api/me", headers=tok("s")).json()
        # the developer picks an admin's subject from the list; free text is refused
        listed = c.get("/api/admin-subjects", headers=tok("d")).json()
        assert listed["subjects"] == [{"code": "arabic", "label": "ערבית"}, {"code": "math", "label": "מתמטיקה"}]
        assert listed["admins"] == [{"id": "u2", "email": "t@b.c", "name": "T", "subject": "מתמטיקה"}]
        put = lambda subject: c.put("/api/admin-subjects/u2", json={"subject": subject}, headers=tok("d"))
        assert put("חשבון").status_code == 400 and auth.access["u2"]["subject"] == "מתמטיקה"
        assert put(" ערבית ").json() == {"ok": True, "subject": "ערבית"} and auth.access["u2"]["subject"] == "ערבית"
        assert c.get("/api/me", headers=tok("t")).json()["subject"] == "arabic"


def test_the_tts_cache_downloads_as_an_offline_zip_with_a_manifest(monkeypatch, tmp_path):
    import io
    import zipfile

    from fastapi.testclient import TestClient

    import app.main
    from app.main import create_app
    from app.tts import RAEF_VOICE_ID, SawtakTTS

    phrases = [{"he": "שלום", "tr": "מרחבא", "variants": [("مرحبا", "m a r ħ a b a")]}]
    monkeypatch.setattr(app.main, "PHRASES", phrases)
    monkeypatch.setattr(app.main, "load_auth", lambda: None)
    tts = SawtakTTS("key", cache_dir=tmp_path)
    monkeypatch.setattr(tts, "_generate", lambda text, voice: b"RIFF-" + text.encode())
    tts.wav(app.main.point_arabic(*phrases[0]["variants"][0]))
    (tmp_path / "stray.wav").write_bytes(b"old")  # a file no phrase names is still included
    with TestClient(create_app(scorer_for(emissions(["w"])), tts, tutor=object())) as c:
        res = c.get("/api/tts-cache/download")
    assert res.status_code == 200 and res.headers["x-file-count"] == "2"
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = sorted(zf.namelist())
    key = "ihki-tts/" + tts.cache_key(app.main.point_arabic(*phrases[0]["variants"][0]), RAEF_VOICE_ID)
    assert names == sorted([key, "ihki-tts/stray.wav", "manifest.csv"])
    assert zf.read(key).startswith(b"RIFF-")
    manifest = zf.read("manifest.csv").decode("utf-8-sig")
    assert "שלום" in manifest and RAEF_VOICE_ID in manifest and "ihki-tts/stray.wav" in manifest


def test_the_warm_sweep_stops_on_request_and_keeps_what_it_made(monkeypatch):
    import threading
    import time

    from fastapi.testclient import TestClient

    import app.main
    from app.main import create_app
    from app.tts import RAEF_VOICE_ID

    phrases = [{"variants": [(word, "a")]} for word in ("واحد", "اثنين", "ثلاثة", "اربعة")]
    monkeypatch.setattr(app.main, "PHRASES", phrases)
    monkeypatch.setattr(app.main, "load_auth", lambda: None)
    first_started, release = threading.Event(), threading.Event()

    class SlowTTS:
        def __init__(self): self.calls = []
        def cached(self, text, voice): return False
        def wav_for_voice(self, text, voice):
            self.calls.append(text); first_started.set(); release.wait(5); return b"RIFF", voice
        def cache_stats(self): return {"files": 0, "bytes": 0, "hits": 0, "misses": 0, "hit_rate": None}

    tts = SlowTTS()
    with TestClient(create_app(scorer_for(emissions(["w"])), tts, tutor=object())) as c:
        assert c.post("/api/tts-cache/warm", json={"voice_id": RAEF_VOICE_ID}).json()["total"] == 4
        assert first_started.wait(5)
        assert c.post("/api/tts-cache/warm/stop").status_code == 200
        release.set()  # the phrase in progress finishes and is kept; no further phrase starts
        for _ in range(200):
            state = c.get("/api/tts-cache").json()["warm"]
            if not state["running"]: break
            time.sleep(.01)
    assert state["stopped"] is True and state["done"] == state["generated"] == 1 and len(tts.calls) == 1
