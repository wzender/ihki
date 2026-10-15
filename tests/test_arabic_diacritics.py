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

        def read(self):
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

        def read(self):
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
    assert all("temperature" not in body and body["enhance_pronunciation"] is True for body in requests)

    (tmp_path / "keep.txt").write_text("not audio")
    assert tts.clear_cache() == 1
    assert (tmp_path / "keep.txt").exists()
    assert not list(tmp_path.glob("*.wav"))


def test_only_raef_is_offered_and_is_the_configured_default(monkeypatch):
    from app.tts import RAEF_VOICE_ID, VOICES, load_tts

    monkeypatch.setenv("SAWTAK_API_KEY", "key")
    monkeypatch.setenv("SAWTAK_VOICE_ID", "an-old-different-voice")
    tts = load_tts()
    assert VOICES == {"רַאאִף": RAEF_VOICE_ID}
    assert tts.voice_id == RAEF_VOICE_ID


