from app.arabic_diacritics import point_arabic


def test_points_short_vowels_sukun_and_long_vowel():
    assert point_arabic("مرحبا", "m a r ħ a b a") == "مَرْحَبَا"


def test_points_geminate_and_keeps_punctuation():
    assert point_arabic("يلّا!", "j a l l a") == "يَلَّا!"


def test_points_each_word_of_a_phrase():
    assert point_arabic("شو اسمك", "ʃ u | ʔ i s m a k") == "شُو اِسْمَكْ"


def test_public_phrase_has_a_separate_pointed_answer():
    from app.phrases import public_phrases

    answer = next(phrase for phrase in public_phrases() if phrase["he"] == "אני")
    assert answer["ar"] == "انا"
    assert answer["ar_pointed"] == "اَنَا"


def test_sawtak_keeps_pronunciation_enhancement_on_for_pointed_input(tmp_path):
    from app.tts import SawtakTTS

    request = {}

    class Response:
        headers = {"X-Sample-Rate": "24000"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        sent = False

        def read(self, _n=-1):  # the audio once, then the end of the stream
            if self.sent:
                return b""
            self.sent = True
            return b"\0" * 5000

    tts = SawtakTTS("key", cache_dir=tmp_path)

    def fake_request(path, body=None):
        request.update(path=path, body=body)
        return Response()

    tts._request = fake_request
    tts.wav("اَكَلْ، يُاكُلْ", "voice")
    assert request["path"] == "/audio/speech"
    assert request["body"]["input"] == "اَكَلْ، يُاكُلْ"
    assert request["body"]["enhance_pronunciation"] is True
    assert "temperature" not in request["body"]


def test_raef_recording_is_cached_and_cache_can_be_cleared(tmp_path):
    from app.tts import RAEF_VOICE_ID, SawtakTTS

    generated, requests = 0, []

    class Response:
        headers = {"X-Sample-Rate": "24000"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        sent = False

        def read(self, _n=-1):  # the audio once, then the end of the stream
            if self.sent:
                return b""
            self.sent = True
            return b"\0" * 5000

    tts = SawtakTTS("key", cache_dir=tmp_path)

    def fake_request(_path, _body=None):
        nonlocal generated
        generated += 1
        requests.append(_body)
        return Response()

    tts._request = fake_request
    first, voice = tts.wav("مَرْحَبَا", "ignored-voice")
    second, second_voice = tts.wav("مَرْحَبَا")
    assert first == second
    assert voice == second_voice == RAEF_VOICE_ID
    assert generated == 1
    assert next(tmp_path.rglob("*.wav")).parent.name == RAEF_VOICE_ID
    assert all("temperature" not in body and body["enhance_pronunciation"] is True for body in requests)

    (tmp_path / "keep.txt").write_text("not audio")
    assert tts.clear_cache() == 1
    assert (tmp_path / "keep.txt").exists()
    assert not list(tmp_path.rglob("*.wav"))


def test_flat_sawtak_cache_is_migrated_into_voice_folder(tmp_path):
    from app.tts import RAEF_VOICE_ID, SawtakTTS

    tts = SawtakTTS("key", cache_dir=tmp_path)
    nested = tts._path("مَرْحَبَا", RAEF_VOICE_ID)
    legacy = tmp_path / nested.name
    legacy.write_bytes(b"old recording")
    tts._generate = lambda *_args: (_ for _ in ()).throw(AssertionError("must use cache"))

    data, _ = tts.wav("مَرْحَبَا")

    assert data == b"old recording" and nested.exists() and not legacy.exists()


def test_sawtak_voice_id_configures_sentence_playback(monkeypatch):
    from app.tts import RAEF_VOICE_ID, VOICES, load_tts

    monkeypatch.setenv("SAWTAK_API_KEY", "key")
    monkeypatch.setenv("SAWTAK_VOICE_ID", "a-different-voice")
    tts = load_tts()
    assert VOICES == {"רַאאִף": RAEF_VOICE_ID}
    assert tts.voice_id == "a-different-voice"
    tts._wav = lambda _text, voice, **_kwargs: (b"audio", voice)
    assert tts.wav("مَرْحَبَا") == (b"audio", "a-different-voice")




def test_a_cut_off_audio_download_is_a_retryable_tts_error(tmp_path, monkeypatch):
    import http.client

    import pytest

    from app.tts import SawtakTTS, TTSUnavailable

    class Broken:
        headers = {"X-Sample-Rate": "24000"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, _n=-1): raise http.client.IncompleteRead(b"x" * 6640)

    tts = SawtakTTS("key", cache_dir=tmp_path)
    monkeypatch.setattr(tts, "_request", lambda path, body=None: Broken())
    with pytest.raises(TTSUnavailable) as e:
        tts.wav("مرحبا")
    assert e.value.transient
    assert TTSUnavailable("x", 429).transient and TTSUnavailable("x", 502).transient
    assert not TTSUnavailable("x", 400).transient and not TTSUnavailable("no audio").transient


def test_an_endless_audio_stream_is_abandoned_and_retryable(tmp_path, monkeypatch):
    import pytest

    import app.tts as tts_module
    from app.tts import SawtakTTS, TTSUnavailable

    class Endless:  # Sawtak's failure mode: minutes of silence, still streaming
        headers = {"X-Sample-Rate": "24000"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"\0" * 65536

    monkeypatch.setattr(tts_module, "MAX_AUDIO_SECONDS", 5)
    tts = SawtakTTS("key", cache_dir=tmp_path)
    monkeypatch.setattr(tts, "_request", lambda path, body=None: Endless())
    with pytest.raises(TTSUnavailable) as e:
        tts.wav("مرحبا")
    assert e.value.transient and not list(tmp_path.rglob("*.wav"))


def test_the_voice_gets_huwwe_and_hiyye_respelled_but_the_page_does_not():
    from app.arabic_diacritics import point_arabic, tts_arabic

    ar, ipa = "هوّ اكل وهيّ اكلت", "h u w w e | ʔ a k a l | u h i j j e | ʔ a k l a t"
    shown, said = point_arabic(ar, ipa), tts_arabic(ar, ipa)
    assert said.split()[0] == "هوّة" and shown.split()[0] != "هوّة"
    assert said.split()[1:] == shown.split()[1:2] + ["وُهِيِّي"] + shown.split()[3:]   # "and she" too
    assert tts_arabic("هوّ؟", "h u w w e") == "هوّة؟"
    assert tts_arabic("هيّ", "h i j j e") == "هِيِّي"
    assert tts_arabic("هوّ", "h u") == point_arabic("هوّ", "h u")   # another reading: untouched
