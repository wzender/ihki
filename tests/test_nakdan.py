"""Niqqud before TTS: Nakdan's reply is faked, no network."""
from app import nakdan


def reply(*words):
    out = []
    for k, (word, options) in enumerate(words):
        if k:
            out.append({"word": " ", "sep": True, "options": []})
        out.append({"word": word, "sep": False, "options": options})
    return out


def setup(monkeypatch, tmp_path, answer):
    monkeypatch.setenv("HEBREW_NIQQUD", "1")
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(nakdan, "_cache", None)
    calls = []

    def fetch(text):
        calls.append(text)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(nakdan, "_fetch", fetch)
    return calls


def test_points_unpointed_words_and_keeps_hand_pointed_ones(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path, reply(("אני", ["אֲנִי"]), ("בבית", ["בְּ|בַיִת"]), ("אָבִיךָ", ["אָבִיךְ"])))
    assert nakdan.point("אני בבית אָבִיךָ") == ("אֲנִי בְּבַיִת אָבִיךָ", True)
    assert nakdan.point("אני בבית אָבִיךָ") == ("אֲנִי בְּבַיִת אָבִיךָ", True) and len(calls) == 1   # cached
    monkeypatch.setattr(nakdan, "_cache", None)
    assert nakdan.point("אני בבית אָבִיךָ")[0] == "אֲנִי בְּבַיִת אָבִיךָ" and len(calls) == 1      # ... on disk


def test_said_to_a_man_or_a_woman(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, reply(("מה", ["מָה"]), ("שלומך", ["שְׁלוֹמֵךְ", "שְׁלוֹמְךָ"])))
    assert nakdan.point("מה שלומך", "m")[0] == "מָה שְׁלוֹמְךָ"
    assert nakdan.point("מה שלומך", "f")[0] == "מָה שְׁלוֹמֵךְ"
    assert nakdan.point("מה שלומך")[0] == "מָה שְׁלוֹמֵךְ"


def test_unreachable_or_odd_reply_reads_it_as_it_is(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, OSError("down"))
    assert nakdan.point("לא שמעתי") == ("לא שמעתי", False)          # not final: tried again next time
    setup(monkeypatch, tmp_path, reply(("לא", ["לֹא"]), ("שמעתי", ["שָׁמְעוּ"])))
    assert nakdan.point("לא שמעתי") == ("לֹא שמעתי", True)           # a different spelling is never taken
    monkeypatch.setenv("HEBREW_NIQQUD", "0")
    assert nakdan.point("שלום") == ("שלום", True)
