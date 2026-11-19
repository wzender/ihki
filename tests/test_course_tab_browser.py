"""The panel's חוברת tab in a real browser: a topic opened from the tree, a sentence edited in the draft with its
rule-read words shown, the draft bar's counts, preview as a learner, publishing, and publishing version 1 again."""
import pytest

from test_learner_home_browser import CHROME, PageAuth, serve
from tests.test_course_draft import DraftAuth, topic
from tests.test_course_store import served, store  # noqa: F401 (served: a fixture)

pytestmark = pytest.mark.skipif(not CHROME, reason="Chrome is not installed")


@pytest.fixture
def course_site(monkeypatch, tmp_path, served):
    auth = DraftAuth()
    s = store(auth, tmp_path)
    s.load()
    for run in serve(PageAuth(), monkeypatch, tmp_path, course_store=s):
        run.versions, run.store = auth, s
        yield run


OPEN = """
window.confirm=()=>true;
await sleep(800); showManage(); await sleep(300);
const tab=document.querySelector('#mModes [data-mode="course"]'); out.tabShown=!tab.hidden; tab.click();
await until(()=>document.querySelector("#mCourse .course-tree"),8000);
out.levels=[...document.querySelectorAll("#mCourse .course-tree>details>summary")].length;
out.closed=[...document.querySelectorAll("#mCourse .course-tree>details")].every(d=>!d.open);
document.querySelector('#mCourse .course-topic-btn[data-cat="l1-say"]').click();
await until(()=>document.querySelector("#mCourse .course-items li .course-item-actions"),8000);
const first=()=>document.querySelector("#mCourse .course-items li");
"""

EDIT = OPEN + """
out.before=first().querySelector("b").textContent;
first().querySelector('.course-item-actions button').click();   // ✏️
const form=first().querySelector("form"); out.focused=form.contains(document.activeElement);
form.querySelector("[name=he]").value="משפט מתוקן"; form.querySelector("[name=ar]").value="אַנַא זַרַאפֵה";
form.requestSubmit();
await until(()=>first()&&first().querySelector("b")&&first().querySelector("b").textContent==="משפט מתוקן",8000);
await until(()=>document.querySelector("#mCourse .course-guessed"),5000);
out.guessed=document.querySelector("#mCourse .course-guessed").textContent;
await until(()=>/שונו: 1/.test(document.querySelector("#mCourse .course-diff").textContent),8000);
out.bar=document.querySelector("#mCourse .course-diff").textContent;
out.buttons=[...document.querySelectorAll("#mCourse .course-bar-actions button")].map(b=>b.textContent);
// "תצוגה כתלמיד" asks for the draft on this device (the reload itself is not driven here)
window.setCoursePreview=on=>{ out.preview=on; };
[...document.querySelectorAll("#mCourse .course-bar-actions button")].find(b=>b.textContent==="תצוגה כתלמיד").click();
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
"""


def test_an_edit_is_saved_to_the_draft_and_its_rule_read_words_are_shown(course_site):
    auth = course_site.versions
    before = topic(auth.rows[1]["document"], "l1-say")["items"][0]
    r = course_site("boss@x.com", EDIT)
    assert r["tabShown"] and r["levels"] >= 2 and r["closed"]
    assert r["before"] == before["he"] and r["focused"]
    assert "זַרַאפֵה" in r["guessed"] and "שונו: 1" in r["bar"]
    assert r["buttons"] == ["תצוגה כתלמיד", "פרסום"] and r["preview"] is True and r["noOverflow"]
    after = topic(auth.rows[2]["document"], "l1-say")["items"][0]
    assert auth.rows[2]["status"] == "draft" and auth.rows[1]["status"] == "published"
    assert (after["id"], after["key"], after["he"]) == (before["id"], before["key"], "משפט מתוקן")


PUBLISH = OPEN + """
const pub=()=>[...document.querySelectorAll("#mCourse .course-bar-actions button")].find(b=>b.textContent==="פרסום");
await until(()=>pub(),8000); pub().click();
await until(()=>[...document.querySelectorAll("#mCourse .course-version-list li")].some(li=>/גרסה 2 · מפורסמת/.test(li.textContent)),8000);
out.afterPublish=[...document.querySelectorAll("#mCourse .course-version-list li")].map(li=>li.textContent);
const again=[...document.querySelectorAll("#mCourse .course-version-list li")].find(li=>/גרסה 1/.test(li.textContent)).querySelector("button");
out.again=again.textContent; again.click();
await until(()=>[...document.querySelectorAll("#mCourse .course-version-list li")].some(li=>/גרסה 1 · מפורסמת/.test(li.textContent)),8000);
out.afterRollback=[...document.querySelectorAll("#mCourse .course-version-list li")].map(li=>li.textContent);
"""


def test_publishing_the_draft_and_rolling_back_to_version_1(course_site):
    auth, s = course_site.versions, course_site.store
    s.change({"op": "delete_items", "cat": "l1-say", "ids": [topic(auth.rows[1]["document"], "l1-say")["items"][0]["id"]]},
             "boss@x.com")
    r = course_site("boss@x.com", PUBLISH)
    assert any(t.startswith("גרסה 2 · מפורסמת") for t in r["afterPublish"])
    assert r["again"] == "פרסום מחדש"
    assert any(t.startswith("גרסה 1 · מפורסמת") for t in r["afterRollback"])
    assert auth.rows[1]["status"] == "published" and auth.rows[2]["status"] == "history" and s.number == 1


PREVIEW = """
await sleep(800);
const d=await (await api("/api/phrases")).json();
out.preview=d.preview||""; out.banner=!!document.querySelector(".course-preview-banner");
out.text=(document.querySelector(".course-preview-banner")||{}).textContent||"";
out.has=d.phrases.some(p=>p.he==="שלום לכם");
"""


def test_a_manager_in_preview_sees_the_draft_and_a_banner_and_a_learner_does_not(course_site):
    s = course_site.store
    s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "שלום לכם", "ar": "مرحبا"}]}, "boss@x.com")
    flag = 'localStorage.setItem("ihki.coursePreview","true");'
    boss = course_site("boss@x.com", PREVIEW, preload=flag)
    dana = course_site("dana@x.com", PREVIEW, preload=flag)
    assert boss["preview"] == "draft" and boss["banner"] and "תצוגת הטיוטה" in boss["text"] and boss["has"]
    assert dana["preview"] == "" and not dana["banner"] and not dana["has"]


LEVEL = OPEN + """
const form=document.querySelector("#mCourse .course-level-add form");
form.closest("details").open=true;
form.querySelector("input").value="שלב ג"; form.querySelector("input[type=number]").value="4";
form.requestSubmit();
const levels=()=>[...document.querySelectorAll("#mCourse .course-tree>details.sec>summary")].map(s=>s.textContent);
await until(()=>levels().includes("שלב 3 · שלב ג"),8000);
out.levels=levels();
await until(()=>/שלבים נוספו: 1/.test(document.querySelector("#mCourse .course-diff").textContent),8000);
const third=[...document.querySelectorAll("#mCourse .course-tree>details.sec")].find(d=>d.querySelector("summary").textContent==="שלב 3 · שלב ג");
out.planned=[...third.querySelectorAll(".course-missing button")].map(b=>b.textContent);
third.querySelector(".course-missing button").click();   // כתיבת שיעור 1
await until(()=>[...document.querySelectorAll("#mCourse .course-tree>details.sec")].some(d=>/שלב ג/.test(d.querySelector("summary").textContent)&&d.querySelector(".course-lesson")),8000);
const again=[...document.querySelectorAll("#mCourse .course-tree>details.sec")].find(d=>/שלב ג/.test(d.querySelector("summary").textContent));
out.lesson=again.querySelector(".course-lesson summary").textContent;
"""


def test_a_level_is_added_from_the_panel_and_its_first_lesson_written(course_site):
    auth = course_site.versions
    r = course_site("boss@x.com", LEVEL, width=1280)
    assert r["levels"][-1] == "שלב 3 · שלב ג"
    assert r["planned"] == ["כתיבת שיעור 1", "כתיבת שיעור 2", "כתיבת שיעור 3", "כתיבת שיעור 4"]
    assert r["lesson"].startswith("שיעור 3.1")
    lv = auth.rows[2]["document"]["levels"][-1]
    assert (lv["name"], lv["count"], lv["first"], [l["n"] for l in lv["lessons"]]) == ("שלב ג", 4, 19, [1])


PATH = """
await until(()=>ALL_LESSONS.length&&ALL_LEVELS.length,8000); await sleep(800);
out.levels=ALL_LEVELS.map(l=>l.name);
if(typeof showView==="function") showView("home"); await sleep(800);
out.heads=[...document.querySelectorAll("#path h2.level")].map(h=>h.getAttribute("aria-label"));
out.lessons=ALL_LESSONS.filter(l=>l.level==="3").map(l=>l.name);
"""


def test_a_published_level_is_on_the_path(course_site):
    s = course_site.store
    s.change({"op": "add_level", "name": "שלב ג", "count": 2}, "boss@x.com")
    for op in ({"op": "add_lesson", "level": 3, "n": 1},
               {"op": "add_topic", "level": 3, "n": 1, "tid": "say", "name": "בשוק", "kind": "say"},
               {"op": "add_items", "cat": "l3.1-say", "items": [{"he": "שלום", "ar": "مرحبا"}]}):
        s.change(op, "boss@x.com", course_site.versions.course_draft()["updated_at"])
    s.publish(2, "boss@x.com")
    r = course_site("boss@x.com", PATH, width=1280)
    assert r["lessons"] == ["שיעור 19", "שיעור 20"] and r["levels"][-1] == "שלב ג"
    assert any("שלב ג" in h for h in r["heads"]), r["heads"]
