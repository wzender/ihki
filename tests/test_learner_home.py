"""The learner's current lesson in each class (openspec/changes/add-focused-learner-home): storage and API."""
import urllib.parse

import pytest

from app.auth import CREATE_LEARNER_CLASS_STATE, AuthUnavailable, SupabaseAuth


class StateAuth(SupabaseAuth):
    """SupabaseAuth with PostgREST faked in memory: rows of learner_class_state keyed by (email, class_id)."""

    def __init__(self, secret="sb_secret_x", fail=None):
        super().__init__("https://proj.supabase.co", "sb_publishable_x", secret=secret)
        self.rows, self.fail, self.calls = {}, fail, []

    def _admin(self, path, method="GET", body=None, prefer="return=representation", timeout=10):
        self.calls.append((method, path, body, prefer))
        if self.fail:
            raise AuthUnavailable(self.fail)
        if method == "GET":
            email = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["email"][0].removeprefix("eq.")
            return [{"class_id": k[1], "current_lesson": v["current_lesson"]} for k, v in self.rows.items() if k[0] == email]
        assert method == "POST" and "on_conflict=email,class_id" in path and "merge-duplicates" in prefer
        self.rows[(body["email"], body["class_id"])] = body
        return None


def test_saves_and_reads_only_the_accounts_own_lessons():
    a = StateAuth()
    a.set_current_lesson(" Dana@X.com ", "7", " 5 ")
    a.set_current_lesson("dana@x.com", 8, "2.1")
    a.set_current_lesson("other@x.com", 7, "3")
    a.set_current_lesson("dana@x.com", 7, "6")   # a new choice replaces the old one in the same class
    assert a.current_lessons("DANA@x.com") == {7: "6", 8: "2.1"}
    assert a.current_lessons("other@x.com") == {7: "3"}
    saved = a.rows[("dana@x.com", 7)]
    assert saved["current_lesson"] == "6" and saved["updated_at"]


def test_without_the_secret_key_nothing_is_read_and_a_save_is_refused():
    a = StateAuth(secret="")
    assert a.current_lessons("dana@x.com") == {}
    with pytest.raises(AuthUnavailable, match="SUPABASE_SECRET_KEY"):
        SupabaseAuth("https://proj.supabase.co", "k").set_current_lesson("dana@x.com", 7, "5")


def test_a_missing_table_reads_as_none_and_a_save_says_how_to_make_it():
    a = StateAuth(fail="supabase answered 404: relation does not exist")
    assert a.current_lessons("dana@x.com") == {}
    with pytest.raises(AuthUnavailable) as e:
        a.set_current_lesson("dana@x.com", 7, "5")
    assert CREATE_LEARNER_CLASS_STATE in str(e.value)


def test_supabase_down_is_raised():
    a = StateAuth(fail="timed out")
    with pytest.raises(AuthUnavailable):
        a.current_lessons("dana@x.com")
    with pytest.raises(AuthUnavailable, match="timed out"):
        a.set_current_lesson("dana@x.com", 7, "5")


def test_migration_keeps_rows_private_and_unique():
    sql = CREATE_LEARNER_CLASS_STATE
    assert "primary key (email, class_id)" in sql
    assert "enable row level security" in sql and "revoke all on public.learner_class_state from anon, authenticated" in sql
    assert "references public.classes (id) on delete cascade" in sql


# through the app
CLASSES = [
    {"id": 7, "name": "א1", "emails": ["dana@x.com", "t@x.com"], "teachers": ["t@x.com"], "lessons": ["3", "4", "5"]},
    {"id": 8, "name": "ב2", "emails": ["dana@x.com"], "lessons": ["2.1", "2.2"]},
    {"id": 9, "name": "מעורבת", "emails": ["dana@x.com"], "lessons": ["5", "2.1"]},
    {"id": 10, "name": "אחרת", "emails": ["other@x.com"], "lessons": ["1"]},
]


class AppAuth(StateAuth):
    def _admin(self, path, method="GET", body=None, prefer="return=representation", timeout=10):
        if "learner_class_state" in path:
            return super()._admin(path, method, body, prefer, timeout)
        return []   # every other table is empty

    def user(self, token):
        return {"id": f"id-{token}", "email": f"{token}@x.com"}

    def classes(self, fresh=False):
        return [dict(c, nicknames={}, homeworks=[], dailies=[], homework="", due=None) for c in CLASSES]


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from test_scoring import emissions, scorer_for
    from app.main import create_app
    from app.tts import SawtakTTS
    auth = AppAuth()
    with TestClient(create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth)) as c:
        c.fake = auth
        yield c


def as_(name):
    return {"Authorization": f"Bearer {name}"}


def classes_of(c, name):
    return {k["id"]: k for k in c.get("/api/me", headers=as_(name)).json()["classes"]}


def test_me_has_class_ids_levels_and_no_lesson_before_a_choice(client):
    mine = classes_of(client, "dana")
    assert set(mine) == {7, 8, 9}
    assert [(mine[i]["level"], mine[i]["current_lesson"]) for i in (7, 8, 9)] == [("1", None), ("2", None), (None, None)]
    assert "teacher" not in mine[7]
    assert classes_of(client, "t")[7]["teacher"] is True


def test_choices_are_kept_apart_for_each_class(client):
    assert client.put("/api/me/classes/7/current-lesson", headers=as_("dana"), json={"lesson": "5"}).json() == \
        {"class_id": 7, "current_lesson": "5"}
    mine = classes_of(client, "dana")
    assert (mine[7]["current_lesson"], mine[8]["current_lesson"]) == ("5", None)
    client.put("/api/me/classes/8/current-lesson", headers=as_("dana"), json={"lesson": "2.2"})
    client.put("/api/me/classes/7/current-lesson", headers=as_("dana"), json={"lesson": "4"})
    mine = classes_of(client, "dana")
    assert (mine[7]["current_lesson"], mine[8]["current_lesson"]) == ("4", "2.2")
    assert set(client.fake.rows) == {("dana@x.com", 7), ("dana@x.com", 8)}


def test_a_saved_lesson_the_class_no_longer_has_is_not_offered(client):
    client.fake.rows[("dana@x.com", 7)] = {"current_lesson": "9"}
    assert classes_of(client, "dana")[7]["current_lesson"] is None


def test_update_is_refused_outside_the_students_own_valid_choice(client):
    put = lambda who, cls, lesson, **k: client.put(f"/api/me/classes/{cls}/current-lesson", json={"lesson": lesson},
                                                   headers=as_(who) if who else {}, **k).status_code
    assert put(None, 7, "5") == 401                 # not signed in
    assert put("dana", 10, "1") == 403              # someone else's class
    assert put("t", 7, "5") == 403                  # the class's teacher keeps the path
    assert put("dana", 9, "5") == 409               # lessons of two levels: the class must be fixed first
    assert put("dana", 7, "2.1") == 400             # a lesson of another class
    assert put("dana", 7, "") == 400
    assert client.fake.rows == {}


def test_unavailable_storage_keeps_me_working_and_refuses_the_save(client):
    client.fake.fail = "timed out"
    assert classes_of(client, "dana")[7]["current_lesson"] is None
    assert client.put("/api/me/classes/7/current-lesson", headers=as_("dana"), json={"lesson": "5"}).status_code == 503


# the page's logic (static/learner_home.js), run in Node
import json
import shutil
import subprocess
from pathlib import Path

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent.parent / "static" / "learner_home.js"


def js(expr):
    """The JSON value of `expr`, evaluated with LearnerHome loaded."""
    if not NODE:
        pytest.skip("node is not installed")
    code = f"{SCRIPT.read_text(encoding='utf-8')}\nconst LH=globalThis.LearnerHome;\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def test_device_keys_are_per_subject_account_and_class():
    keys = js('[LH.storageKey("arabic"," Dana@X.com ",7),LH.storageKey("arabic","other@x.com",7),'
              'LH.storageKey("arabic","dana@x.com",8),LH.storageKey("math","dana@x.com",7),LH.storageKey("","dana@x.com",7)]')
    assert keys[0] == "ihki.lesson.v1:arabic:dana@x.com:7" == keys[4]
    assert len(set(keys[:4])) == 4


def test_the_servers_lesson_wins_and_a_devices_one_is_sent_again():
    r = js('[LH.resolveLesson("5",{lesson:"4",synced:false},["4","5"]),'
           ' LH.resolveLesson(null,{lesson:"4",synced:false},["4","5"]),'
           ' LH.resolveLesson(null,{lesson:"9",synced:true},["4","5"]),'
           ' LH.resolveLesson("9",null,["4","5"]),'
           ' LH.resolveLesson(null,null,["4","5"])]')
    assert r == [{"lesson": "5", "retry": False}, {"lesson": "4", "retry": True}, {"lesson": None, "retry": False},
                 {"lesson": None, "retry": False}, {"lesson": None, "retry": False}]


def test_who_is_asked_for_a_lesson():
    assert js('[LH.classState({level:"1"}),LH.classState({level:null}),LH.classState({level:"1",teacher:true}),'
              'LH.classState({subject:"math",level:null}),LH.classState(null)]') == \
        ["ok", "ambiguous", "teacher", "math", "none"]


def test_actions_keep_priority_drop_duplicates_and_what_cannot_start():
    picked = js('LH.pickActions([{target:"topic:a",ready:true,kind:"homework"},null,'
                '{target:"video:1",ready:true,kind:"video"},{target:"topic:a",ready:true,kind:"continue"},'
                '{target:"x",ready:false,kind:"broken"},{target:"short",ready:true,kind:"short"},'
                '{target:"change",ready:true,kind:"change"}]).map(a=>a.kind)')
    assert picked == ["homework", "video", "short"]   # never more than three; the same topic once
    all_three = js('LH.pickActions([{target:"h",ready:true,kind:"homework"},{target:"v",ready:true,kind:"video"},'
                   '{target:"c",ready:true,kind:"continue"},{target:"s",ready:true,kind:"short"}]).map(a=>a.kind)')
    assert all_three == ["homework", "video", "continue"]   # the short activity gives way
    assert js('LH.pickActions([{target:"c",ready:true,kind:"continue"},{target:"s",ready:true,kind:"short"}])'
              '.map(a=>a.kind)') == ["continue", "short"]   # no homework, no video today


def test_short_activity_is_at_most_ten_weak_or_else_new_sentences():
    assert js("LH.SHORT") == 10
    weak = js('LH.shortPlan([1,2,3,4,5,6,7,8,9,10,11,12].map(id=>({id})),[],{1:40,2:90,3:10,4:60,5:20,6:50,7:30,8:15,9:35,10:25,11:45,12:55},75)')
    assert weak == {"kind": "review", "ids": [3, 8, 5, 10, 7, 9, 1, 11, 6, 12]}
    fresh = js('LH.shortPlan([{id:1}],[1,8,9,10,11,12,13,14,15,16,17,18].map(id=>({id})),{1:95},75)')
    assert fresh == {"kind": "new", "ids": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17]}
    assert js('LH.shortPlan([{id:1}],[{id:1}],{1:95},75)') is None   # nothing to offer: the action is left out


def test_daily_practice_takes_homework_then_current_lesson_then_first_unfinished():
    assert js('LH.lessonOrder("hw","5",null,["1","2","5","hw","6"])') == ["hw", "5", "1", "2", "6"]
    assert js('LH.lessonOrder(null,"5","2",["1","2","5","6"])') == ["5", "2", "1", "6"]
    assert js('LH.lessonOrder(null,null,"2",["1","2","5"])') == ["2", "1", "5"]   # no current lesson: as before
