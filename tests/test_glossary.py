"""The glossary (the course's words that stand alone), the words it finds in a subtitles' line, and a student's own dictionary."""
from app import glossary
from tests.test_library_sections import STUDENT, SectionAuth, run


def found(line):
    return [(w["i"], w["n"], w["tr"], w["he"], w["topic"]) for w in glossary.annotate(line)]


def test_a_word_is_found_in_the_topic_that_teaches_it():
    assert found("מִין פַתַח אֵלְבַּאבּ?") == [(0, 1, "מִין", "מי", "l1-new"), (1, 1, "פַתַח", "פתח", "l1-verbs"),
                                             (2, 1, "בַּאבּ", "הדלת", "l1-new")]   # the article: found without it
    assert found("אַנַא קַעַדֵת פִי אֵלְבֵּית, וּאִנְתִי?")[-1] == (4, 1, "אִנְתִי", "את", "l1-pron")   # "and" written on it
    days = glossary.annotate("בֻּכְּרַא יוֹם אֵלְאַחַד")[-1]
    assert (days["i"], days["n"], days["he"], days["lesson"], days["topic"]) == (1, 2, "יום ראשון", "2.2", "l2.2-days")
    assert days["lesson_name"] and days["topic_name"]
    future = glossary.annotate("יִפְתַח")[0]
    assert future["he"] == "פתח (עתיד)" and future["topic"] == "l1-verbs"
    assert found("סַמִיר - 3") == []   # a name, a dash, a number


def test_saved_words_keep_their_order_and_drop_what_the_course_lacks():
    assert [w["id"] for w in glossary.saved(["מִין", "אין כזה", "בַּאבּ", "מִין"])] == ["מִין", "בַּאבּ"]


def test_a_student_keeps_their_own_dictionary_and_the_subtitles_mark_its_words(monkeypatch):
    class WordsAuth(SectionAuth):
        def __init__(self):
            super().__init__(["d1"])
            self.kept = {}

        def texts(self, fresh=False):
            return [{"id": 2, "lesson": "2", "title": "היכרות", "voices": [], "pos": 11, "ord": 0,
                     "video": "https://www.youtube.com/watch?v=abcdefghijk",
                     "lines": [["מי פתח את הדלת?", "מִין פַתַח אֵלְבַּאבּ?", 0.1], ["סמיר", "סַמִיר", 3]]}]

        def writings(self, token):
            return dict(self.kept)

        def save_writing(self, token, user_id, topic, lines):
            self.kept[topic] = list(lines)

    auth = WordsAuth()

    async def steps(client):
        out = {"texts": (await client.get("/api/texts", headers=STUDENT)).json(),
               "empty": (await client.get("/api/words", headers=STUDENT)).json(),
               "put": (await client.put("/api/words", headers=STUDENT, json={"words": ["בַּאבּ", "אין כזה", "מִין"]})).json(),
               "bad": (await client.put("/api/words", headers=STUDENT, json={"words": "בַּאבּ"})).status_code,
               "mine": (await client.get("/api/words", headers=STUDENT)).json(),
               "writing": (await client.get("/api/writing", headers=STUDENT)).json()}
        return out

    out = run(auth, steps, monkeypatch)
    first, second = [p for p in out["texts"]["phrases"] if p["cat"] == "t2"]
    assert [(w["i"], w["tr"]) for w in first["words"]] == [(0, "מִין"), (1, "פַתַח"), (2, "בַּאבּ")] and "words" not in second
    assert out["empty"] == {"words": []} and out["bad"] == 400
    assert [w["id"] for w in out["put"]["words"]] == ["בַּאבּ", "מִין"] == [w["id"] for w in out["mine"]["words"]]
    assert out["mine"]["words"][0]["he"] == "דלת" and out["mine"]["words"][0]["topic"] == "l1-new"
    assert auth.kept == {"~words": ["בַּאבּ", "מִין"]} and out["writing"] == {"writing": {}}   # not a written homework
