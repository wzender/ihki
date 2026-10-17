import io
import wave

import numpy as np
from test_scoring import emissions, scorer_for

from app.auth import AuthUnavailable
from app.lab import ITEMS, benchmark, public_items, still_flagged
from app.translit import to_hebrew


def tone_wav():
    t = np.arange(16000) / 16000
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return buf.getvalue()


class NoTTS:
    """No native baseline: the stub model would score the voice as it scores the learner, and forgive everything."""

    def wav(self, text, voice):
        raise OSError("no TTS in tests")

    def cached(self, text, voice):
        return False


class FakeAuth:
    url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset({"t@b.c", "u@b.c"}), frozenset()
    developers = frozenset({"t@b.c", "u@b.c"})

    def __init__(self):
        self.samples, self.missing = [], False
        self.flags = [{"id": 7, "phrase": "nope", "he": "מצב", "ar": "حال", "ipa": "ħ aː l", "score": 40, "audio": "x.wav",
                       "result": {"words": [{"phonemes": [{"ipa": "ħ", "score": 1, "flagged": True, "heard": "χ"}]}]},
                       "false_issues": [{"word": 0, "expected": "ħ", "heard": "χ"}]}]

    def list_flags(self, status="pending"):
        return self.flags if status == "confirmed" else []

    def flag(self, flag_id):
        return next((f for f in self.flags if f["id"] == flag_id), None)

    def flag_audio(self, flag_id):
        return (tone_wav(), "audio/wav") if self.flag(flag_id) else None

    def user(self, token):
        return {"id": "u1", "email": {"teacher": "T@b.c", "teacher2": "u@b.c"}.get(token, "s@b.c")}

    def classes(self, fresh=False):
        return []

    def ranked(self, fresh=False):
        return frozenset()

    def lab_samples(self, full=False):
        if self.missing:
            raise AuthUnavailable("supabase answered 404: no table")
        return self.samples

    def add_lab_sample(self, row, audio, mime, name):
        if self.missing:
            raise AuthUnavailable("supabase answered 404: no table")
        self.samples.append({**row, "id": len(self.samples) + 1, "audio": name})
        return True

    def lab_sample(self, sample_id):
        return next((s for s in self.samples if s["id"] == sample_id), None)

    def lab_audio(self, sample_id):
        return (tone_wav(), "audio/wav") if self.lab_sample(sample_id) else None

    def update_lab_result(self, sample_id, result):
        self.lab_sample(sample_id)["result"] = result

    def delete_lab_samples(self, speaker=None):
        gone = [s for s in self.samples if speaker is None or (s.get("speaker") or "") == speaker]
        self.samples = [s for s in self.samples if s not in gone]
        return len(gone)


def test_lab_deletes_one_speaker_or_everyone():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    with TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        for token in ("teacher", "teacher", "teacher2"):   # the speaker is who is signed in
            c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers={"Authorization": f"Bearer {token}"})
        assert c.delete("/api/lab/samples", headers=teacher).status_code == 400          # never all by omission
        assert c.delete("/api/lab/samples?speaker=t@b.c&everyone=true", headers=teacher).status_code == 400
        assert c.delete("/api/lab/samples?everyone=true", headers={"Authorization": "Bearer student"}).status_code == 403
        assert c.delete("/api/lab/samples?speaker=%20t@b.c%20", headers=teacher).json() == {"deleted": 2}
        assert c.get("/api/lab/results", headers=teacher).json()["speakers"] == ["u@b.c"]
        assert c.delete("/api/lab/samples?everyone=true", headers=teacher).json() == {"deleted": 1}
        assert c.get("/api/lab/results", headers=teacher).json()["totals"]["recordings"] == 0


def test_items_are_well_formed():
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)
    for item in ITEMS:
        for ar, ipa in item["variants"]:
            assert len(ar.split()) == len(ipa.split("|")), item["id"]
            assert item["focus"] in ipa.split(), item["id"]     # the item says the sound it tests
            assert all(to_hebrew(ipa, ar)), item["id"]
    pairs = {}
    for item in ITEMS:
        if item["pair"]:
            pairs.setdefault(item["pair"], []).append(item)
    assert all(len(words) >= 2 for words in pairs.values())      # a minimal pair is two words (or three)
    assert all(it["sounds"] for it in public_items())


def test_lab_scores_and_keeps_labelled_recordings():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth = FakeAuth()
    teacher, student = {"Authorization": "Bearer teacher"}, {"Authorization": "Bearer student"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    # حال said with a Hebrew ח: the model hears χ for ħ
    with TestClient(create_app(scorer_for(emissions(["χ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        assert c.get("/api/lab", headers=student).status_code == 403              # teachers only
        assert c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=student).status_code == 403
        d = c.get("/api/lab", headers=teacher).json()
        assert d["setup"] == "" and d["counts"] == {} and d["items"][0]["id"] == ITEMS[0]["id"]

        r = c.post("/api/lab/score", data={"item": "pair-ħaal", "mistake": 0, "speaker": "מרב"}, files=audio,
                   headers=teacher)   # a speaker sent by the page is ignored: it is the signed-in teacher
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["saved"] and "trace" in d["result"]                              # the teacher's stage-by-stage view
        row = auth.samples[-1]
        assert row["label"] == "mistake" and row["mistake"] == {"index": 0, "expected": "ħ"} and row["speaker"] == "t@b.c"
        assert row["flagged"] == ["ħ>χ"] and row["email"] == "t@b.c"

        # said right (the default), and not kept when saving is off
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher)
        assert auth.samples[-1]["label"] == "correct" and auth.samples[-1]["mistake"] is None
        d = c.post("/api/lab/score", data={"item": "pair-ħaal", "save": "false"}, files=audio, headers=teacher).json()
        assert not d["saved"] and len(auth.samples) == 2
        assert c.get("/api/lab", headers=teacher).json()["counts"] == {"pair-ħaal": {"correct": 1, "mistake": 1}}
        assert c.get("/api/lab", headers=teacher).json()["mine"] == {"pair-ħaal": {"correct": 1, "mistake": 1}}
        assert c.get("/api/lab", headers={"Authorization": "Bearer teacher2"}).json()["mine"] == {}
        assert c.get("/api/lab?speaker=t@b.c", headers={"Authorization": "Bearer teacher2"}).json()["mine"] \
            == {"pair-ħaal": {"correct": 1, "mistake": 1}}
        assert c.get("/api/lab?speaker=אחר", headers=teacher).json()["mine"] == {}

        assert c.post("/api/lab/score", data={"item": "nope"}, files=audio, headers=teacher).status_code == 404
        assert c.post("/api/lab/score", data={"item": "pair-ħaal", "mistake": 3}, files=audio,
                      headers=teacher).status_code == 400                         # حال has three sounds

        # before the table is created: the list still shows and scores, with the SQL to run
        auth.missing = True
        d = c.get("/api/lab", headers=teacher).json()
        assert "create table public.lab_samples" in d["setup"]
        d = c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher).json()
        assert not d["saved"] and "lab_samples" in d["error"] and d["result"]["score"] is not None


def phones(*ps):
    return {"score": 90, "words": [{"phonemes": [{"ipa": i, "score": sc, "flagged": f, "heard": h} for i, sc, f, h in ps]}]}


def test_benchmark_counts_false_rejections_and_caught_mistakes():
    samples = [
        {"id": 1, "label": "correct", "result": phones(("ħ", 20, True, "χ"), ("aː", 100, False, None), ("l", 95, False, None))},
        {"id": 2, "label": "correct", "result": phones(("ħ", 90, False, None), ("aː", 100, False, None), ("l", 95, False, None))},
        # ħ said wrong on purpose and caught; and one not caught
        {"id": 3, "label": "mistake", "mistake": {"index": 0, "expected": "ħ"},
         "result": phones(("ħ", 5, True, "h"), ("aː", 100, False, None), ("l", 95, False, None))},
        {"id": 4, "label": "mistake", "mistake": {"index": 0, "expected": "ħ"},
         "result": phones(("ħ", 70, False, None), ("aː", 100, False, None), ("l", 30, True, "r"))},
        {"id": 5, "label": "correct", "result": {**phones(("ħ", 0, True, "_")), "unrelated": True}},   # tells nothing
    ]
    b = benchmark(samples, reports=[{"false_issues": [{"word": 0, "expected": "ħ", "heard": "χ"}]}])
    by = {r["sound"]: r for r in b["sounds"]}
    assert by["ħ"]["right"] == 2 and by["ħ"]["false_flags"] == 1 and by["ħ"]["frr"] == 0.5
    assert by["ħ"]["planted"] == 2 and by["ħ"]["caught"] == 1 and by["ħ"]["detection"] == 0.5
    assert by["ħ"]["confusions"] == {"χ": 1} and by["ħ"]["reports"] == 1
    assert by["l"]["right"] == 4 and by["l"]["false_flags"] == 1          # said right in a mistake recording too
    assert b["sounds"][0]["sound"] == "ħ"                                  # the worst first
    t = b["totals"]
    assert t["recordings"] == 5 and t["unrelated"] == 1 and t["planted"] == 2 and t["good_share"] == 1.0
    # a rescore replaces the stored result
    fixed = benchmark(samples, {1: phones(("ħ", 95, False, None), ("aː", 100, False, None), ("l", 95, False, None))})
    assert {r["sound"]: r for r in fixed["sounds"]}["ħ"]["frr"] == 0.0


def test_still_flagged():
    r = phones(("ħ", 1, True, "χ"), ("aː", 100, False, None))
    assert still_flagged(r, {"word": 0, "expected": "ħ"}) and not still_flagged(r, {"word": 0, "expected": "aː"})
    assert not still_flagged(r, {"word": 3, "expected": "ħ"})


def test_lab_results_and_flag_rescore():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    with TestClient(create_app(scorer_for(emissions(["χ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        for token in ("teacher", "teacher2"):
            c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers={"Authorization": f"Bearer {token}"})
        d = c.get("/api/lab/results", headers=teacher).json()
        assert d["speakers"] == ["t@b.c", "u@b.c"] and d["totals"]["recordings"] == 2
        assert {r["sound"]: r for r in d["sounds"]}["ħ"]["false_flags"] == 2 and d["sounds"][0]["reports"] == 1
        assert c.get("/api/lab/results?speaker=t@b.c", headers=teacher).json()["totals"]["recordings"] == 1
        assert c.get("/api/lab/results", headers={"Authorization": "Bearer student"}).status_code == 403

        d = c.post("/api/lab/flags/7/rescore", headers=teacher).json()
        assert d["old"] == {"score": 40, "flagged": ["ħ>χ"]} and d["issues"][0]["still"] and "trace" in d["result"]
        assert c.post("/api/lab/flags/8/rescore", headers=teacher).status_code == 404


def test_minerva_lab_groups_imported_segments_by_video():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    auth.samples = [
        {"id": 2, "item": "minerva:U8aBRd3-wlM:02", "speaker": "מינרווה", "device": "minerva:U8aBRd3-wlM:4.2-7.8",
         "ar": "مرحبا", "ipa": "m a r ħ a b a", "score": 81, "heard_ipa": "m a r ħ a b a", "flagged": [],
         "result": {}, "audio": "two.webm"},
        {"id": 1, "item": "minerva:U8aBRd3-wlM:01", "speaker": "מינרווה", "device": "minerva:U8aBRd3-wlM:1.0-4.0",
         "ar": "أهلا", "ipa": "ʔ a h l a", "score": 70, "heard_ipa": "ʔ a h l a", "flagged": ["h>x"],
         "result": {"unrelated": False, "words": [{"ar": "أهلا", "score": 70, "phonemes": []}]}, "audio": "one.webm"},
        {"id": 3, "item": "pair-ħaal", "speaker": "אחר", "device": "", "audio": "other.wav"},
    ]
    with TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        d = c.get("/api/lab/minerva", headers=teacher).json()
        assert len(d["videos"]) == 1 and d["videos"][0]["id"] == "U8aBRd3-wlM"
        assert [s["line"] for s in d["videos"][0]["samples"]] == [1, 2]
        # Sentence data comes from data/minerva_metadata.json, not the deliberately different stored row above.
        first = d["videos"][0]["samples"][0]
        assert first["start"] == 9.0 and first["end"] == 10.8
        assert first["ar"] == "فيه عندك حليب طارة؟"
        assert first["ipa"] == "f iː h | ʕ i n d a k | ħ a l iː b | tˤ aː r e"
        assert first["tr"] == "פִיה עִנְדַכּ חַלִיב טַארֶה?" and first["he"] == "יש לך חלב טרי?"
        audio = c.get("/api/lab/samples/1/audio", headers=teacher)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
        assert c.get("/api/lab/samples/99/audio", headers=teacher).status_code == 404
        excluded = c.post("/api/lab/samples/1/exclude-word",
                          json={"word": 0, "excluded": True, "reason": "חיתוך פגום"}, headers=teacher).json()
        assert excluded["excluded_words"] == {"0": "חיתוך פגום"}
        assert c.get("/api/lab/minerva", headers=teacher).json()["videos"][0]["samples"][0]["excluded_words"] == excluded["excluded_words"]
        assert c.post("/api/lab/samples/1/exclude-word", json={"word": 2, "excluded": True}, headers=teacher).status_code == 400
        assert c.get("/api/lab/minerva", headers={"Authorization": "Bearer student"}).status_code == 403


def test_lab_rescores_its_recordings_and_compares_with_the_stored_scores():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    with TestClient(create_app(scorer_for(emissions(["χ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher)   # ħ said as χ: flagged
        d = c.get("/api/lab/results", headers=teacher).json()
        assert d["samples"] == [1] and d["version"]
        # the scorer changed since: now it hears ħ
        c.app.state.scorer = scorer_for(emissions(["ħ", "aː", "l"]))
        r = c.post("/api/lab/samples/1/rescore", headers=teacher).json()
        assert r["id"] == 1 and not any(p["flagged"] for w in r["result"]["words"] for p in w["phonemes"])
        assert "trace" not in r["result"]
        d = c.post("/api/lab/results", json={"results": {"1": r["result"]}}, headers=teacher).json()
        assert d["pool"] == c.app.state.scorer.cfg.gop.ratio_pool
        before, after = ({x["sound"]: x for x in d[k]["sounds"]}["ħ"] for k in ("stored", "now"))
        assert before["frr"] == 1.0 and after["frr"] == 0.0
        assert c.post("/api/lab/samples/9/rescore", headers=teacher).status_code == 404
        assert c.post("/api/lab/results", json={"results": []}, headers=teacher).status_code == 400
        assert c.post("/api/lab/samples/1/rescore", headers={"Authorization": "Bearer student"}).status_code == 403


class FakeTTS:
    """Says anything as the test tone; counts what it was asked."""

    def __init__(self):
        self.said = []

    def wav(self, text, voice=""):
        self.said.append((text, voice))
        return tone_wav(), voice

    def cached(self, text, voice):
        return True


def test_lab_automatic_test_with_tts():
    from fastapi.testclient import TestClient

    from app.main import LAB_TTS_VOICES, create_app

    auth, tts, teacher = FakeAuth(), FakeTTS(), {"Authorization": "Bearer teacher"}
    with TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), tts, auth=auth)) as c:
        assert c.get("/api/lab/tts", headers=teacher).json()["voices"] == list(LAB_TTS_VOICES)
        tts.said.clear()   # (nothing else may ask the voice: the TTS run is scored without the native baseline)
        d = c.post("/api/lab/tts", data={"item": "pair-ħaal", "voice": "ראאף"}, headers=teacher).json()
        assert d["saved"] and d["speaker"] == "TTS ראאף" and d["result"]["references"] == 0
        assert len(tts.said) == 1 and "ح" in tts.said[0][0]
        row = auth.samples[-1]
        assert row["speaker"] == "TTS ראאף" and row["label"] == "correct" and row["device"].startswith("tts:")
        assert row["audio"].endswith(".wav")
        assert c.get("/api/lab?speaker=TTS ראאף", headers=teacher).json()["mine"] == {"pair-ħaal": {"correct": 1, "mistake": 0}}
        r = c.get("/api/lab/tts/pair-ħaal?voice=ראאף", headers=teacher)
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert c.post("/api/lab/tts", data={"item": "pair-ħaal", "voice": "x"}, headers=teacher).status_code == 400
        assert c.post("/api/lab/tts", data={"item": "nope", "voice": "ראאף"}, headers=teacher).status_code == 404
        assert c.post("/api/lab/tts", data={"item": "pair-ħaal", "voice": "ראאף"},
                      headers={"Authorization": "Bearer student"}).status_code == 403
    with TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), NoTTS(), auth=FakeAuth())) as c:
        c.app.state.tts = None
        assert c.get("/api/lab/tts", headers=teacher).json()["voices"] == []
        assert c.post("/api/lab/tts", data={"item": "pair-ħaal", "voice": "ראאף"}, headers=teacher).status_code == 503


def test_lab_sweeps_every_gop_setting():
    from fastapi.testclient import TestClient

    from app import lab
    from app.main import create_app

    assert len(lab.SWEEP) == 24 and len(lab.SWEEP_KEYS) == 24
    assert lab.quality({"frr": .1, "detection": .5}) == 70.0 and lab.quality({"frr": .1, "detection": None}) == 90.0
    assert lab.quality({"frr": None}) is None and lab.quality_partial({"frr": .1, "detection": None})

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    with TestClient(create_app(scorer_for(emissions(["χ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher)          # ħ heard as χ
        c.post("/api/lab/score", data={"item": "pair-ħaal", "mistake": "0"}, files=audio,
               headers=teacher)
        before = c.app.state.scorer.cfg.gop
        r = c.post("/api/lab/samples/1/sweep", headers=teacher).json()
        assert r["id"] == 1 and set(r["results"]) == set(lab.SWEEP_KEYS)
        assert c.app.state.scorer.cfg.gop == before                     # the server's own setting is untouched
        r2 = c.post("/api/lab/samples/2/sweep", headers=teacher).json()
        d = c.post("/api/lab/sweep", json={"results": {"1": r["results"], "2": r2["results"]}}, headers=teacher).json()
        assert len(d["settings"]) == 24 and d["recordings"] == 2
        assert d["current"] == lab.sweep_key(before.ratio_pool, before.vowel_window)
        s = d["settings"][0]
        assert {"key", "pool", "window", "quality", "partial", "totals"} <= set(s)
        qs = [x["quality"] for x in d["settings"]]
        assert qs == sorted(qs, reverse=True)
        assert c.post("/api/lab/samples/9/sweep", headers=teacher).status_code == 404
        assert c.post("/api/lab/sweep", json={"results": []}, headers=teacher).status_code == 400
        assert c.post("/api/lab/samples/1/sweep", headers={"Authorization": "Bearer student"}).status_code == 403


def test_vowel_stats_score_vowels_not_flags():
    from app.lab import vowel_stats

    def sample(i, phones, mistake=None):
        return {"id": i, "label": "mistake" if mistake else "correct",
                "mistake": {"index": 0, "expected": mistake} if mistake else None,
                "result": {"words": [{"phonemes": [{"ipa": p, "score": sc} for p, sc in phones]}]}}

    rows = [sample(1, [("k", 40), ("a", 95), ("i", 70)]), sample(2, [("a", 30), ("l", 100)], mistake="a"),
            {**sample(3, [("a", 10)]), "result": {"unrelated": True, "words": [{"phonemes": [{"ipa": "a", "score": 10}]}]}}]
    v = vowel_stats(rows)
    assert v == {"right": 2, "mean": 82.5, "weak": 0.5, "planted": 1, "auc": 1.0}   # k is no vowel, #3 is unrelated
    assert vowel_stats(rows[:1])["auc"] is None
    assert vowel_stats(rows, {2: {"words": [{"phonemes": [{"ipa": "a", "score": 70}]}]}})["auc"] == 0.75


def test_the_sweep_scores_every_setting_whatever_the_env(monkeypatch):
    """The sweep passes each setting to the scorer itself: the env only picks the server's own one."""
    from fastapi.testclient import TestClient

    import app.scorer as scorer_module
    from app import lab
    from app.main import create_app

    monkeypatch.setenv("GOP_RATIO_POOL", "max")
    monkeypatch.setenv("GOP_VOWEL_WINDOW", "wide")
    seen, real = [], scorer_module.score_tokens
    monkeypatch.setattr(scorer_module, "score_tokens",
                        lambda logp, al, ids, blank, cfg, *a, **k: seen.append((cfg.ratio_pool, cfg.vowel_window))
                        or real(logp, al, ids, blank, cfg, *a, **k))
    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    with TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files={"audio": ("a.wav", tone_wav(), "audio/wav")},
               headers=teacher)
        assert c.app.state.scorer.cfg.gop.ratio_pool == "max"
        seen.clear()
        r = c.post("/api/lab/samples/1/sweep", headers=teacher).json()
        assert sorted(seen, key=str) == sorted(lab.SWEEP, key=str)
        d = c.post("/api/lab/sweep", json={"results": {"1": r["results"]}}, headers=teacher).json()
        assert all({"right", "mean", "weak", "planted", "auc"} <= set(s["vowels"]) for s in d["settings"])
        base = next(s for s in d["settings"] if s["key"] == "max|wide")
        assert base["changed"] == 0 and base["sounds"] == 3
        assert d["current"] == "max|wide"


def test_the_sweep_shows_which_settings_changed_a_score():
    """A long vowel with an /i/ spike inside its window: max|wide marks it down, the pooled settings do not."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from test_scoring import SYMS, VOCAB

    lp = emissions(["ħ", "aː", "l"], gap=5)
    p = np.exp(lp)
    vowel = [t for t in range(len(p)) if lp[t].argmax() == VOCAB["aː"]]
    for t in vowel:
        p[t] = (1 - .45) / (len(SYMS) - 1); p[t, VOCAB["aː"]] = .45
    p[vowel[-1] + 3] = .1 / (len(SYMS) - 1); p[vowel[-1] + 3, VOCAB["i"]] = .9
    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    with TestClient(create_app(scorer_for(np.log(p / p.sum(1, keepdims=True))), NoTTS(), auth=auth)) as c:
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files={"audio": ("a.wav", tone_wav(), "audio/wav")},
               headers=teacher)
        r = c.post("/api/lab/samples/1/sweep", headers=teacher).json()
        d = {s["key"]: s for s in c.post("/api/lab/sweep", json={"results": {"1": r["results"]}}, headers=teacher).json()["settings"]}
        assert d["max|wide"]["changed"] == 0 and d["max|wide"]["vowels"]["mean"] < 80
        for key in ("mean|wide", "gated|wide", "topk|wide", "max|1"):
            assert d[key]["changed"] == 1 and d[key]["vowels"]["mean"] > 95, key


def test_lab_opens_the_trace_of_any_saved_recording():
    from fastapi.testclient import TestClient

    from app.main import create_app

    auth, teacher = FakeAuth(), {"Authorization": "Bearer teacher"}
    audio = {"audio": ("a.wav", tone_wav(), "audio/wav")}
    with TestClient(create_app(scorer_for(emissions(["χ", "aː", "l"])), NoTTS(), auth=auth)) as c:
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher)
        recs = c.get("/api/lab/results", headers=teacher).json()["recordings"]
        assert [(r["id"], r["item"], r["speaker"], r["flagged"]) for r in recs] == [(1, "pair-ħaal", "t@b.c", ["ħ>χ"])]
        assert recs[0]["unrelated"] is False
        d = c.post("/api/lab/samples/1/rescore?trace=true", headers=teacher).json()
        assert "trace" in d["result"] and d["result"]["trace"]["tokens"] and d["sample"]["item"] == "pair-ħaal"
        g = c.app.state.scorer.cfg.gop
        assert d["setting"] == f"{g.ratio_pool}|{'wide' if g.vowel_window is None else g.vowel_window}"
        d = c.post("/api/lab/samples/1/rescore?trace=true&setting=max|1", headers=teacher).json()
        assert d["setting"] == "max|1" and "trace" in d["result"]
        assert "trace" not in c.post("/api/lab/samples/1/rescore", headers=teacher).json()["result"]
        assert c.post("/api/lab/samples/1/rescore?trace=true&setting=nope", headers=teacher).status_code == 400


def test_imported_lab_line_reconstructs_its_phrase_outside_the_fixed_list():
    from app.lab import sample_phrase

    reviewed = sample_phrase({"id": 6, "item": "minerva:U8aBRd3-wlM:01", "ar": "wrong", "ipa": "x"})
    assert reviewed["variants"] == [("فيه عندك حليب طارة؟",
                                      "f iː h | ʕ i n d a k | ħ a l iː b | tˤ aː r e")]

    phrase = sample_phrase({"id": 7, "item": "minerva:video:01", "ar": "حال", "ipa": "ħ aː l",
                            "speaker": "מינרווה"})
    assert phrase == {"cat": "lab", "key": "minerva:video:01", "he": "מינרווה",
                      "variants": [("حال", "ħ aː l")]}
    assert sample_phrase({"id": 8, "item": "removed-item"}) is None
