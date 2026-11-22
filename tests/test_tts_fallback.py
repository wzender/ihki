"""ElevenLabs as the fallback of Sawtak for students' playback (tts.ElevenLabsTTS, SawtakTTS.fallback): a recording
either one made is played again; a new sentence goes to ElevenLabs when Sawtak fails, and Sawtak is left alone for a
while; the cache-warm sweep never spends ElevenLabs' credits."""
import io
import json
import wave

import pytest

from app import tts
from app.tts import ELEVEN_PREFIX, LINE_VOICES, RAEF_VOICE_ID, ElevenLabsTTS, SawtakTTS, TTSUnavailable

HANAN = next(iter(LINE_VOICES.values()))


def wav_of(tag: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(tag * 4800)
    return buf.getvalue()


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def calls(monkeypatch):
    """ElevenLabs' API, faked: the requests it got, and what it says."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.full_url, req.headers.get("Xi-api-key"), json.loads(req.data) if req.data else None))
        if req.full_url.endswith("/voices"):
            return Answer(json.dumps({"voices": [{"voice_id": "premade1", "category": "premade", "name": "Adam"},
                                                 {"voice_id": "mine1", "category": "cloned", "name": "Teacher"}]}).encode())
        return Answer(b"\x01\x02" * 12000)   # half a second of PCM
    monkeypatch.setattr(tts.urllib.request, "urlopen", urlopen)
    return seen


def voice(tmp_path, sawtak=None, eleven=True, female=""):
    """A SawtakTTS whose Sawtak answers with `sawtak` (bytes, or an exception to raise)."""
    t = SawtakTTS("k", RAEF_VOICE_ID, tmp_path / "cache")
    t.sawtak_calls = 0

    def generate(text, v, source=""):
        t.sawtak_calls += 1
        if isinstance(sawtak, Exception):
            raise sawtak
        return sawtak or wav_of(b"\x05\x05")
    t._generate = generate
    if eleven:
        t.fallback = ElevenLabsTTS("el-key", female_voice_id=female)
    return t


def test_sawtak_still_says_what_it_can_and_its_cache_is_played_first(tmp_path, calls):
    t = voice(tmp_path)
    data, used = t.wav("مرحبا")
    assert used == RAEF_VOICE_ID and t.sawtak_calls == 1 and calls == []
    again, used = t.wav("مرحبا")
    assert again == data and t.sawtak_calls == 1   # from the cache


def test_when_sawtak_fails_elevenlabs_says_it_and_its_recording_is_kept(tmp_path, calls):
    t = voice(tmp_path, sawtak=TTSUnavailable("tts provider error 402: no credit", 402))
    data, used = t.wav("مرحبا")
    assert used == f"{ELEVEN_PREFIX}eleven_v4_turbo-mine1"   # the account's own voice, not a premade one
    url, key, body = calls[-1]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/mine1?output_format=pcm_24000"
    assert key == "el-key" and body == {"text": "مرحبا", "model_id": "eleven_v4_turbo"}
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 12000)
    n = len(calls)
    again, used2 = t.wav("مرحبا")
    assert again == data and used2 == used and len(calls) == n   # kept: no second request
    assert t.sawtak_calls == 1
    assert t.activity.snapshot()["events"][0]["kind"] == "elevenlabs"   # on the monitoring panel


def test_after_a_failure_sawtak_is_left_alone_for_a_while(tmp_path, calls, monkeypatch):
    t = voice(tmp_path, sawtak=TTSUnavailable("tts provider unreachable", transient=True))
    clock = [1000.0]
    monkeypatch.setattr(tts.time, "monotonic", lambda: clock[0])
    t.wav("واحد")
    t.wav("اتنين")
    assert t.sawtak_calls == 1   # the second went straight to ElevenLabs
    clock[0] += tts.SAWTAK_REST + 1
    t.wav("تلاتة")
    assert t.sawtak_calls == 2   # tried again after the rest


def test_hanans_lines_take_the_female_voice_when_one_is_set(tmp_path, calls):
    t = voice(tmp_path, sawtak=TTSUnavailable("down", 503), female="her1")
    _, used = t.wav("اهلا", HANAN)
    assert used.endswith("-her1") and "/text-to-speech/her1?" in calls[-1][0]
    _, used = t.wav("اهلا")
    assert used.endswith("-mine1")


def test_without_elevenlabs_a_sawtak_failure_is_the_students_503_as_before(tmp_path, calls):
    t = voice(tmp_path, sawtak=TTSUnavailable("down", 503), eleven=False)
    with pytest.raises(TTSUnavailable):
        t.wav("مرحبا")
    assert calls == []


def test_the_cache_warm_sweep_never_falls_back(tmp_path, calls):
    t = voice(tmp_path, sawtak=TTSUnavailable("down", 503))
    with pytest.raises(TTSUnavailable):
        t.wav_for_voice("مرحبا", RAEF_VOICE_ID)
    assert calls == []


def test_an_elevenlabs_refusal_is_a_503_with_its_reason(tmp_path, monkeypatch):
    t = voice(tmp_path, sawtak=TTSUnavailable("down", 503))
    t.fallback.voice_id = "v1"

    def refuse(req, timeout=None):
        raise tts.urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"detail": {"message": "invalid api key"}}'))
    monkeypatch.setattr(tts.urllib.request, "urlopen", refuse)
    with pytest.raises(TTSUnavailable, match="elevenlabs error 401: invalid api key"):
        t.wav("مرحبا")


def test_load_tts_takes_elevenlabs_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("TTS_BUCKET", raising=False)
    monkeypatch.setenv("ELEVEN_LABS_API_KEY", "el-key")
    monkeypatch.setenv("ELEVEN_LABS_VOICE_ID", "v9")
    monkeypatch.delenv("ELEVEN_LABS_MODEL", raising=False)
    only_eleven = tts.load_tts("")
    assert only_eleven is not None and only_eleven.fallback.voice_id == "v9"
    assert only_eleven.fallback.model == "eleven_v4_turbo"
    with pytest.raises(TTSUnavailable, match="no Sawtak key"):
        only_eleven._generate_now("x", RAEF_VOICE_ID)   # no Sawtak key: straight to the fallback
    monkeypatch.delenv("ELEVEN_LABS_API_KEY")
    assert tts.load_tts("") is None and tts.load_tts("sk").fallback is None
