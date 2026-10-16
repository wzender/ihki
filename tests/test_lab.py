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
    url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset({"t@b.c"}), frozenset()

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
        return {"id": "u1", "email": "T@b.c" if token == "teacher" else "s@b.c"}

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

        r = c.post("/api/lab/score", data={"item": "pair-ħaal", "mistake": 0, "speaker": " מרב "}, files=audio,
                   headers=teacher)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["saved"] and "trace" in d["result"]                              # the teacher's stage-by-stage view
        row = auth.samples[-1]
        assert row["label"] == "mistake" and row["mistake"] == {"index": 0, "expected": "ħ"} and row["speaker"] == "מרב"
        assert row["flagged"] == ["ħ>χ"] and row["email"] == "t@b.c"

        # said right (the default), and not kept when saving is off
        c.post("/api/lab/score", data={"item": "pair-ħaal"}, files=audio, headers=teacher)
        assert auth.samples[-1]["label"] == "correct" and auth.samples[-1]["mistake"] is None
        d = c.post("/api/lab/score", data={"item": "pair-ħaal", "save": "false"}, files=audio, headers=teacher).json()
        assert not d["saved"] and len(auth.samples) == 2
        assert c.get("/api/lab", headers=teacher).json()["counts"] == {"pair-ħaal": {"correct": 1, "mistake": 1}}
        assert c.get("/api/lab?speaker=מרב", headers=teacher).json()["mine"] == {"pair-ħaal": {"correct": 0, "mistake": 1}}
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
        for speaker in ("א", "ב"):
            c.post("/api/lab/score", data={"item": "pair-ħaal", "speaker": speaker}, files=audio, headers=teacher)
        d = c.get("/api/lab/results", headers=teacher).json()
        assert d["speakers"] == ["א", "ב"] and d["totals"]["recordings"] == 2
        assert {r["sound"]: r for r in d["sounds"]}["ħ"]["false_flags"] == 2 and d["sounds"][0]["reports"] == 1
        assert c.get("/api/lab/results?speaker=א", headers=teacher).json()["totals"]["recordings"] == 1
        assert c.get("/api/lab/results", headers={"Authorization": "Bearer student"}).status_code == 403

        d = c.post("/api/lab/flags/7/rescore", headers=teacher).json()
        assert d["old"] == {"score": 40, "flagged": ["ħ>χ"]} and d["issues"][0]["still"] and "trace" in d["result"]
        assert c.post("/api/lab/flags/8/rescore", headers=teacher).status_code == 404


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
