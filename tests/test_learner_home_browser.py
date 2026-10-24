"""The focused learner home in the real page (static/index.html), driven in headless Chrome against the real app.

Only Supabase is faked: the server's tables live in memory (AppAuth), and the page's Supabase client is a stub that is
signed in as the account the test names. Each case runs a short script in the page and reads its results from the DOM."""
import json
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest

from test_learner_home import CLASSES, AppAuth

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
pytestmark = pytest.mark.skipif(not CHROME, reason="Chrome is not installed")


class PageAuth(AppAuth):
    def __init__(self):
        super().__init__()
        self.teachers = frozenset({"boss@x.com"})   # a teacher of the whole service
        # by default one class for the student (lessons 3-5 of level 1), so no class has to be chosen first
        self.layout = [dict(CLASSES[0], emails=["dana@x.com", "rina@x.com", "t@x.com"]), CLASSES[3]]
        self.daily_rows = []   # the table `dailies`: the daily videos' library

    def classes(self, fresh=False):
        return [dict({"nicknames": {}, "homeworks": [], "dailies": [], "homework": "", "due": None}, **c)
                for c in self.layout]

    def _admin(self, path, method="GET", body=None, prefer="return=representation", timeout=10):
        if path.startswith("/rest/v1/dailies"):
            return self.daily_rows
        return super()._admin(path, method, body, prefer, timeout)

    def attempts(self, token):
        return []

    def writings(self, token):
        return {}


def fake_supabase(email):
    session = {"access_token": email.split("@")[0], "user": {"id": f"id-{email}", "email": email}}
    return f"""<script>
window.supabase={{createClient:()=>({{auth:{{
  getSession:async()=>({{data:{{session:{json.dumps(session)}}}}}),
  onAuthStateChange:cb=>{{ setTimeout(()=>cb("SIGNED_IN",{json.dumps(session)}),0); return {{data:{{subscription:{{unsubscribe(){{}}}}}}}}; }},
  signOut:async()=>({{}}), signInWithOAuth:async()=>({{}}), signInWithOtp:async()=>({{}})}}}})}};
</script>"""


DRIVER = """<script>
const sleep=ms=>new Promise(r=>setTimeout(r,ms)), out={};
async function until(test,ms=8000){ const end=Date.now()+ms; while(!test()){ if(Date.now()>end) throw new Error("timed out"); await sleep(50); } }
const view=()=>document.body.dataset.view||"";
const open=id=>!!document.getElementById(id).open;
// the reminder prompt, as it would come up: a server that sends reminders, to a browser that has not been asked
window.pushState=async()=>({key:"BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U",hour:8,sub:null});
try{ Object.defineProperty(Notification,"permission",{get:()=>"default"}); }catch(e){}
addEventListener("load",async()=>{
  try{
    const entry=()=>["startBtn","practiceBtn"].map(id=>document.getElementById(id)).find(b=>!b.hidden&&b.offsetParent);
    await until(()=>entry());   // the start button, or for an account with more screens, "practice"
    out.pushCan=PUSH_CAN;
    entry().click();
    await (async()=>{ %s })();
  }catch(e){
    out.error=String(e&&e.stack||e);
    const shown=id=>{ const e=document.getElementById(id); return e?!e.hidden:null; };   // where the page got to
    out.state={start:shown("startBtn"),login:shown("loginBtn"),roles:shown("roles"),denied:shown("denied"),
      email:typeof ME_EMAIL!=="undefined"?ME_EMAIL:null,signIn:typeof sb!=="undefined"&&!!sb,view:view(),
      dialogs:[...document.querySelectorAll("dialog[open]")].map(d=>d.id),prompt:(document.getElementById("prompt")||{}).textContent};
  }
  const pre=document.createElement("pre"); pre.id="result"; pre.textContent=JSON.stringify(out); document.body.append(pre);
});
</script>"""


@pytest.fixture
def site(monkeypatch, tmp_path):
    """The real app on a local port, with the test's page at /t/<email>/<case> (and ?view=daily passed through)."""
    yield from serve(PageAuth(), monkeypatch, tmp_path)


@pytest.fixture
def open_site(monkeypatch, tmp_path):
    """The same, as the open service: no sign-in at all."""
    yield from serve(None, monkeypatch, tmp_path)


def serve(auth, monkeypatch, tmp_path):
    import uvicorn
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    from test_scoring import emissions, scorer_for
    from app.main import create_app
    from app.tts import SawtakTTS
    from app import push as pushes

    async def no_loop(*a, **k):
        return None
    monkeypatch.setattr(pushes, "loop", no_loop)   # no reminders are sent while the page is driven
    cases = {}
    app = create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth)

    @app.get("/t/{email}/{case}")
    def harness(email: str, case: str, request: Request):
        page = index_html()   # the page as the app serves it
        page = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/@supabase/supabase-js[^"]*"></script>',
                      fake_supabase(email) if auth else "", page)
        return HTMLResponse(page.replace("</body>", DRIVER % cases[case] + "</body>"))

    def index_html():
        return next(r for r in app.routes if getattr(r, "path", "") == "/").endpoint().body.decode()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    while not server.started:
        time.sleep(0.05)
    profile = tmp_path / "chrome"

    def run(email, script, query="", width=390):
        case = f"c{len(cases)}"; cases[case] = script
        url = f"http://127.0.0.1:{port}/t/{email}/{case}{query}"
        dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                              f"--user-data-dir={profile}", f"--window-size={width},860", "--virtual-time-budget=20000",
                              "--dump-dom", url], capture_output=True, text=True, timeout=90).stdout
        found = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        assert found, dom[-2000:]
        result = json.loads(found.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"'))
        assert "error" not in result, (result["error"], result.get("state"))
        return result

    run.auth = auth
    yield run
    server.should_exit = True; thread.join(5)


FIRST_VISIT = """
await until(()=>open("lessonDlg"));
const dlg=document.getElementById("lessonDlg");
out.lessons=[...dlg.querySelectorAll("input[name=lesson]")].map(r=>r.value);
out.labels=[...dlg.querySelectorAll(".lesson-option")].map(l=>l.textContent);
out.focusInDialog=dlg.contains(document.activeElement);
dlg.dispatchEvent(new Event("cancel",{cancelable:true}));   // Escape: the question stays
out.openAfterEscape=open("lessonDlg");
dlg.querySelector("button[type=submit]").click();             // nothing chosen yet
await sleep(50); out.unchosen=dlg.querySelector(".lesson-status").textContent;
dlg.querySelector('input[value="5"]').checked=true;
dlg.querySelector("form").requestSubmit();
await until(()=>view()==="home");
out.focused=document.activeElement&&document.activeElement.id;
out.actions=[...document.querySelectorAll("#vids .focus-action")].map(b=>b.getAttribute("aria-label"));
out.rewardVisible=getComputedStyle(document.getElementById("rewardShelf")).display!=="none";
out.dailyPracticeOnHome=[...document.querySelectorAll("#vids button")].some(b=>b.textContent.includes("תרגול יומי"));
out.referOnHome=document.querySelector("#vids .focus-refer")?.textContent;
out.referInNav=!document.querySelector('#nav [data-view="refer"]').hidden;
out.homeTab=!document.querySelector('#nav [data-view="home"]').hidden;
out.where=document.querySelector("#vids .focus-where span").textContent;
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
out.local=localStorage.getItem("ihki.lesson.v1:arabic:dana@x.com:7");
await sleep(3000);
out.pushPrompt=open("pushDlg");
"""


def test_first_visit_asks_the_lesson_then_shows_the_home_without_a_reminder_prompt(site):
    r = site("dana@x.com", FIRST_VISIT)
    assert r["pushCan"] is True   # the prompt could come up in this browser, so its absence means something
    assert r["lessons"] == ["3", "4", "5"] and all("שיעור" in label for label in r["labels"])
    assert r["focusInDialog"] and r["openAfterEscape"] and r["unchosen"] == "בחרו שיעור כדי להמשיך."
    assert r["focused"] == "focusTitle" and r["homeTab"] and r["noOverflow"]
    assert r["rewardVisible"] is True and r["dailyPracticeOnHome"] is False
    assert r["referOnHome"] == "חבר מביא חבר" and r["referInNav"] is False
    assert "שיעור 5" in r["where"] and "א1" in r["where"]
    assert 1 <= len(r["actions"]) <= 3
    assert json.loads(r["local"]) == {"lesson": "5", "synced": True}
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "5"
    assert r["pushPrompt"] is False


RETURN_VISIT = """
await sleep(1500);
out.asked=open("lessonDlg"); out.view=view();
out.actions=[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
document.querySelector("#vids .focus-path").click();
await sleep(100);
out.pathView=view();
out.rewardOnPath=getComputedStyle(document.getElementById("rewardShelf")).display!=="none";
out.pathShortcuts=[...document.querySelectorAll("#today button")].map(b=>b.textContent.trim());
const cur=document.querySelector("#path [aria-current]"), heads=[...document.querySelectorAll("#path .chapter")];
const at=heads.findIndex(h=>h.dataset.lesson==="5");
out.inLesson5=!!cur&&at>=0&&!!(heads[at].compareDocumentPosition(cur)&Node.DOCUMENT_POSITION_FOLLOWING)
  &&(!heads[at+1]||!!(heads[at+1].compareDocumentPosition(cur)&Node.DOCUMENT_POSITION_PRECEDING));
out.lessonsOnPath=heads.map(h=>h.dataset.lesson);
const r=cur&&cur.getBoundingClientRect(); out.inView=!!r&&r.top>=0&&r.bottom<=innerHeight;
await sleep(2000);
out.pushPrompt=open("pushDlg");
"""


def test_a_later_visit_opens_the_home_and_the_path_at_the_current_lesson(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", RETURN_VISIT)
    assert r["asked"] is False and r["view"] == "home"
    assert any(a.startswith("ממשיכים ב") for a in r["actions"])
    assert r["pathView"] == "" and r["lessonsOnPath"] == ["3", "4", "5"]
    assert r["rewardOnPath"] is False
    assert r["pathShortcuts"] == []   # daily practice and homework are no longer path shortcuts
    assert r["inLesson5"] and r["inView"]   # lessons 3 and 4 are unfinished too, yet the path opens at lesson 5
    assert r["pushPrompt"] is False         # not on entering: the prompt waits for a good moment


GOOD_MOMENT = """
await sleep(1500);
out.before=open("pushDlg");
const p=PHRASES.find(p=>p.listed!==false&&CATS.some(c=>c.id===p.cat&&!c.story));
pickCat(p.cat); await sleep(200);
keepScore(p.id,92);                     // the first gold ever: a celebration, and a good moment for the prompt
out.celebrated=open("celebrateDlg");
document.getElementById("celebrateGo").click();
out.duringPractice=open("pushDlg");     // not in the middle of practice
showPath(); await sleep(2500);
out.pushPrompt=open("pushDlg");
out.title=document.getElementById("pushTitle").textContent;
"""


def test_the_reminder_is_offered_back_home_after_a_good_moment(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", GOOD_MOMENT)
    assert r["before"] is False and r["celebrated"] is True and r["duringPractice"] is False
    assert r["pushPrompt"] is True and "כל הכבוד" in r["title"]


REFER = """
await sleep(1500);
out.navHidden=document.querySelector('#nav [data-view="refer"]').hidden;
document.querySelector("#vids .focus-refer").click();
await until(()=>view()==="refer");
out.view=view(); out.title=document.querySelector("#vids .refer h2").textContent;
out.seen=localStorage.getItem("ihki.referSeen.2027");
"""


def test_refer_a_friend_moves_from_the_nav_to_the_focused_home(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", REFER)
    assert r == {"pushCan": True, "navHidden": True, "view": "refer", "title": "חבר מביא חבר 🧡", "seen": "true"}


REMINDER = """
await sleep(1500);
out.view=view(); out.asked=open("lessonDlg");
document.querySelector("#vids .back").click();
await sleep(300);
out.afterBack=view(); out.askedAfterBack=open("lessonDlg");
"""


def test_a_reminder_opens_the_daily_videos_before_the_lesson_question(site):
    r = site("dana@x.com", REMINDER, "?view=daily")
    assert r["view"] == "daily" and r["asked"] is False
    assert r["askedAfterBack"] is True


def test_a_reminder_with_a_saved_lesson_returns_to_the_home(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "4"}
    r = site("dana@x.com", REMINDER, "?view=daily")
    assert r["view"] == "daily" and r["asked"] is False
    assert r["afterBack"] == "home" and r["askedAfterBack"] is False


PATH_FIRST = """
await sleep(1500);
out.view=view(); out.asked=open("lessonDlg");
out.homeTab=!document.querySelector('#nav [data-view="home"]').hidden;
"""


def test_teachers_keep_the_path(site):
    for teacher in ("boss@x.com", "t@x.com"):   # a teacher of the whole service, and a class's own teacher
        r = site(teacher, PATH_FIRST)
        assert r == {"pushCan": True, "view": "", "asked": False, "homeTab": False}, teacher


UNREACHABLE = """
await until(()=>open("lessonDlg"));
const dlg=document.getElementById("lessonDlg");
dlg.querySelector('input[value="4"]').checked=true; dlg.querySelector("form").requestSubmit();
await until(()=>view()==="home");
out.local=localStorage.getItem("ihki.lesson.v1:arabic:dana@x.com:7");
"""


def test_a_choice_the_server_cannot_keep_stays_on_the_device_and_is_sent_later(site):
    site.auth.fail = "timed out"
    r = site("dana@x.com", UNREACHABLE)
    assert json.loads(r["local"]) == {"lesson": "4", "synced": False}
    assert site.auth.rows == {}
    site.auth.fail = None
    r = site("dana@x.com", PATH_FIRST)                     # the next visit: the device's choice, sent again
    assert r["view"] == "home" and r["asked"] is False
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "4"
    r = site("rina@x.com", PATH_FIRST)                     # another account on this device does not inherit it
    assert r["asked"] is True


SEVERAL_CLASSES = """
await until(()=>open("pickDlg"));
out.askedBeforeClass=open("lessonDlg");
[...document.querySelectorAll("#picks button")].find(b=>b.textContent.startsWith("ב2")).click();
await until(()=>open("lessonDlg"));
out.lessons=[...document.querySelectorAll("#lessonDlg input[name=lesson]")].map(r=>r.value);
document.querySelector('#lessonDlg input[value="2.2"]').checked=true;
document.querySelector("#lessonDlg form").requestSubmit();
await until(()=>view()==="home");
out.where=document.querySelector("#vids .focus-where span").textContent;
"""


def test_each_class_asks_for_its_own_lesson(site):
    site.auth.layout = [dict(CLASSES[0], teachers=[]), CLASSES[1]]   # dana studies in two classes, of levels 1 and 2
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", SEVERAL_CLASSES)
    assert r["askedBeforeClass"] is False and r["lessons"] == ["2.1", "2.2"]
    assert "ב2" in r["where"]
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "5"   # the other class keeps its own
    assert site.auth.rows[("dana@x.com", 8)]["current_lesson"] == "2.2"


AMBIGUOUS = """
await until(()=>open("lessonDlg"));
const dlg=document.getElementById("lessonDlg");
out.radios=dlg.querySelectorAll("input[type=radio]").length;
out.message=dlg.querySelector(".lesson-error").textContent;
dlg.querySelector("button").click();
await sleep(100);
out.view=view(); out.open=open("lessonDlg"); out.pathShown=document.querySelectorAll("#path .chapter").length>0;
"""


def test_a_class_of_two_levels_asks_for_a_correction_and_keeps_the_path(site):
    site.auth.layout = [CLASSES[2]]   # lessons 5 and 2.1
    r = site("dana@x.com", AMBIGUOUS)
    assert r["radios"] == 0 and "לתקן את הגדרות הכיתה" in r["message"]
    assert r["view"] == "" and r["open"] is False and r["pathShown"]


CHANGE = """
await sleep(1500);
localStorage.setItem("ihki.best2",JSON.stringify({"1":80}));
const before=localStorage.getItem("ihki.best2");
document.querySelector("#vids .focus-change").click();
await until(()=>open("lessonDlg"));
out.selected=document.querySelector("#lessonDlg input[name=lesson]:checked").value;
out.canCancel=!!document.querySelector("#lessonDlg .row button[type=button]");
document.querySelector('#lessonDlg input[value="3"]').checked=true;
document.querySelector("#lessonDlg form").requestSubmit();
await until(()=>document.querySelector("#vids .focus-where span")?.textContent.includes("שיעור 3"));
out.best=localStorage.getItem("ihki.best2")===before;
out.continueTitle=[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
"""


def test_changing_the_lesson_moves_the_home_and_keeps_progress(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", CHANGE)
    assert r["selected"] == "5" and r["canCancel"] is True and r["best"] is True
    assert "ממשיכים בשיעור 3" in r["continueTitle"]
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "3"


PRIORITY = """
await sleep(1500);
const titles=()=>[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
out.first=titles();
out.homework=document.querySelector("#vids .focus-action").getAttribute("aria-label");
document.querySelectorAll("#vids .focus-action")[1].click();   // today's video: watched now
await sleep(300);
document.querySelectorAll("dialog[open]").forEach(d=>d.close());
showView("home"); await sleep(100);
out.afterWatching=titles();
"""


def test_homework_then_todays_video_then_the_lesson(site):
    from datetime import date, timedelta
    today, tomorrow = date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat()
    site.auth.layout[0] = dict(site.auth.layout[0], homeworks=[{"topic": "l4-say", "due": tomorrow}],
                               dailies=[{"video": 1, "date": today}])
    site.auth.daily_rows = [{"id": 1, "level": "1", "title": "ברוכים הבאים", "url": "https://vimeo.com/76979871"}]
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", PRIORITY)
    assert r["first"][:2] == ["שיעורי בית", "הסרטון של היום"] and r["first"][2].startswith("ממשיכים ב")
    assert len(r["first"]) == 3   # the short activity gives way to the three before it
    assert "מגישים מחר" in r["homework"]
    assert "הסרטון של היום" not in r["afterWatching"] and len(r["afterWatching"]) == 3


SHORT = """
await sleep(1500);
const short=[...document.querySelectorAll("#vids .focus-action")].find(b=>b.textContent.includes("תרגול קצר"));
out.detail=short.querySelector(".focus-detail").textContent;
short.click();
await sleep(400);
out.counter=document.getElementById("counter").textContent;
out.size=daily.length;
out.plan=dailyPlan().slice(0,10).map(id=>CATS.find(c=>c.id===PHRASES.find(p=>p.id===id).cat).lesson);
endDaily();
await sleep(100);
out.doneTitle=document.getElementById("dailyTitle").textContent; out.more=!document.getElementById("dailyMore").hidden;
out.dailyCounted=localStorage.getItem("ihki.daily");
out.backTo=document.getElementById("dailyClose").textContent; out.view=view();
"""


def test_short_activity_shows_its_size_and_announces_its_end(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", SHORT)
    n = int(r["detail"].split()[0])
    assert 1 <= n <= 10 and r["size"] == n and "תרגול קצר" in r["counter"]
    assert r["doneTitle"] == "התרגול הקצר הסתיים" and r["more"] is False
    assert r["dailyCounted"] is None   # a short round is not the day's practice
    assert r["backTo"] == "חזרה לדף הבית" and r["view"] == "home"
    assert r["plan"] and set(r["plan"]) == {"5"}   # daily practice: new sentences of the current lesson first


def test_the_open_service_keeps_the_path(open_site):
    r = open_site("anyone@x.com", PATH_FIRST)
    assert r["view"] == "" and r["asked"] is False and r["homeTab"] is False


FINISHED = """
await sleep(1500);
for(const p of PHRASES) best[p.id]=60;   // every sentence said, none excellent: there is something to improve
for(const c of CATS.filter(c=>c.lesson==="5")){
  if(c.learn) learned[c.id]=lWords(c).map(p=>p.id);
  if(c.match) matched[c.id]={ids:lWords({id:c.match}).map(p=>p.id)};
  if(c.write) written[c.id]=Array.from({length:c.write.lines},()=>"כתבתי");
}
showView("home"); await sleep(100);
out.titles=[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
out.note=(document.querySelector("#vids .focus-note")||{}).textContent||"";
out.lesson=document.querySelector("#vids .focus-where span").textContent;
"""


def test_a_finished_lesson_offers_a_review_and_another_lesson_without_moving_on(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", FINISHED)
    assert r["titles"] == ["תרגול קצר", "בחירת שיעור אחר"]
    assert "סיימתם את שיעור 5" in r["note"] and "שיעור 5" in r["lesson"]   # the class's lesson is not advanced
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "5"
