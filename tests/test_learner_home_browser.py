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

    # the table `feedback`, in memory (self.notes): the notes and their conversations
    def list_feedback(self, status=None, limit=200):
        return [dict(r) for r in getattr(self, "notes", []) if not status or r["status"] == status]

    def my_feedback(self, email, limit=100):
        return [dict(r) for r in getattr(self, "notes", []) if r["email"] == email.lower()]

    def feedback_row(self, feedback_id):
        return next((dict(r) for r in getattr(self, "notes", []) if r["id"] == feedback_id), None)

    def review_feedback(self, feedback_id, values):
        for r in getattr(self, "notes", []):
            if r["id"] == feedback_id:
                r.update(values)

    def writings(self, token):
        return {}


def fake_supabase(email, news=False):
    session = {"access_token": email.split("@")[0], "user": {"id": f"id-{email}", "email": email}}
    seen = "" if news else """localStorage.setItem("ihki.news",'"feedback-push"');"""   # the one-time "what is new" window: only where a test asks for it
    return f"""<script>
{seen}
window.__testSession={json.dumps(session)};
window.supabase={{createClient:()=>({{auth:{{
  getSession:async()=>({{data:{{session:{json.dumps(session)}}}}}),
  onAuthStateChange:cb=>{{ window.__authCallback=cb; setTimeout(()=>cb("SIGNED_IN",window.__testSession),0); return {{data:{{subscription:{{unsubscribe(){{}}}}}}}}; }},
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
    await until(()=>ENTERED||entry());   // "practice", for an account with more screens; a student goes straight in
    out.pushCan=PUSH_CAN;
    if(!ENTERED) entry().click();
    await (async()=>{ %s })();
  }catch(e){
    out.error=String(e&&e.stack||e);
    const shown=id=>{ const e=document.getElementById(id); return e?!e.hidden:null; };   // where the page got to
    out.state={start:shown("startBtn"),login:shown("loginBtn"),roles:shown("roles"),denied:shown("denied"),
      email:typeof ME_EMAIL!=="undefined"?ME_EMAIL:null,signIn:typeof sb!=="undefined"&&!!sb,view:view(),
      dialogs:[...document.querySelectorAll("dialog[open]")].map(d=>d.id),prompt:(document.getElementById("prompt")||{}).textContent};
  }
  const pre=document.createElement("pre"); pre.id="result"; pre.textContent=JSON.stringify(out).replace(/[\u0080-\uffff]/g,c=>"\\\\u"+c.charCodeAt(0).toString(16).padStart(4,"0")); document.body.append(pre);   // escaped: the page turns a shadda in text into a span
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
    monkeypatch.setattr(pushes, "streak_loop", no_loop)
    cases, news_cases = {}, set()
    app = create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth)

    @app.get("/t/{email}/{case}")
    def harness(email: str, case: str, request: Request):
        page = index_html()   # the page as the app serves it
        page = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/@supabase/supabase-js[^"]*"></script>',
                      fake_supabase(email, case in news_cases) if auth else "", page)
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

    def run(email, script, query="", width=390, mobile=False, news=False):
        case = f"c{len(cases)}"; cases[case] = script
        if news:
            news_cases.add(case)
        url = f"http://127.0.0.1:{port}/t/{email}/{case}{query}"
        args = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                f"--user-data-dir={profile}", f"--window-size={width},860", "--virtual-time-budget=20000"]
        if mobile:
            args += ["--touch-events=enabled", "--user-agent=Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"]
        dom = subprocess.run(args + ["--dump-dom", url], capture_output=True, text=True, timeout=90).stdout
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
out.referOnHome=document.querySelector("#vids .focus-refer strong")?.textContent;
out.referNew=!!document.querySelector("#vids .focus-refer.promo");
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
    assert r["referOnHome"] == "חבר מביא חבר 🧡" and r["referNew"] is True and r["referInNav"] is False
    assert "שיעור 5" in r["where"] and "א1" in r["where"]
    assert 1 <= len(r["actions"]) <= 3
    assert json.loads(r["local"]) == {"lesson": "5", "synced": True}
    assert site.auth.rows[("dana@x.com", 7)]["current_lesson"] == "5"
    assert r["pushPrompt"] is False


AUTH_REFRESH_AFTER_ENTRY = """
await until(()=>ENTERED);
await until(()=>!document.getElementById("logo-splash"));
let authError=""; addEventListener("error",event=>{authError=String(event.error||event.message)});
window.__authCallback("TOKEN_REFRESHED",window.__testSession);
await sleep(100);
out.authError=authError;
out.stillEntered=ENTERED;
"""


def test_token_refresh_after_splash_is_removed_does_not_touch_missing_elements(site):
    result = site("dana@x.com", AUTH_REFRESH_AFTER_ENTRY)
    assert result["authError"] == "" and result["stillEntered"] is True


RETURN_VISIT = """
await sleep(1500);
out.asked=open("lessonDlg"); out.view=view();
out.actions=[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
document.querySelector("#vids .focus-path").click();
await sleep(100);
out.pathView=view();
out.rewardOnPath=getComputedStyle(document.getElementById("rewardShelf")).display!=="none";
out.pathShortcuts=[...document.querySelectorAll("#today button:not(.path-stats):not(#vidsBtn):not(#libBtn):not(#dailyBtn):not(.ps-tools button)")].map(b=>b.textContent.trim());
const stats=document.querySelector("#today .path-stats"), todayBox=document.getElementById("today");
out.stats=stats&&{shown:stats.offsetParent!==null,sticky:getComputedStyle(todayBox).position,
  parts:[...stats.children].map(c=>c.className.split(" ")[0]),note:stats.querySelector(".ps-note").textContent,
  oneLine:[...stats.querySelectorAll(".ps-streak,.ps-n,.ps-day")].every((e,_,a)=>Math.abs(e.getBoundingClientRect().top-a[0].getBoundingClientRect().top)<6),
  fits:stats.scrollWidth<=stats.clientWidth+1};
if(stats){ stats.click(); out.statsOpens=open("rewardsDlg"); document.getElementById("rewardsDlg").close(); }
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
    assert r["pathShortcuts"] == []   # daily practice and homework are no longer path shortcuts (the videos button lives in the bar)
    # instead, a sticky bar sums up the home's rewards on one line, and opens the collection
    assert r["stats"] == {"shown": True, "sticky": "sticky", "parts": ["ps-streak", "ps-n", "ps-n", "ps-n", "ps-day", "ps-note"],
                          "note": r["stats"]["note"], "oneLine": True, "fits": True}
    assert r["stats"]["note"] and r["statsOpens"] is True
    assert r["inLesson5"] and r["inView"]   # lessons 3 and 4 are unfinished too, yet the path opens at lesson 5
    assert r["pushPrompt"] is False         # not on entering: the prompt waits for a good moment


PATH_SHADDA = """
await sleep(1500);
const lesson=LESSONS[0], level=ALL_LEVELS.find(v=>v.id===lesson.level), topic=CATS.find(c=>c.lesson===lesson.id);
level.name="רַמّה"; lesson.name="דַרّס"; topic.name="כֻּלّ";
showView(""); drawPath(true);
const levelEl=document.querySelector("#path .level");
const lessonEl=document.querySelector(`#path .chapter[data-lesson="${lesson.id}"]`);
const topicEl=[...document.querySelectorAll("#path .sat")].find(b=>b.getAttribute("aria-label")?.startsWith(topic.name+":"));
const check=(el,original)=>{
  const sh=el.querySelector(".sh"), pseudo=getComputedStyle(sh,"::before");
  return {wrappers:el.querySelectorAll(".sh").length, original,
    visible:!!(el.offsetWidth&&el.offsetHeight), baseText:el.textContent.includes(original.replaceAll("ّ","")),
    mark:{content:pseudo.content,top:pseudo.top,mask:pseudo.maskImage||pseudo.webkitMaskImage}};
};
out.level=check(levelEl,level.name); out.level.label=levelEl.getAttribute("aria-label");
out.lesson=check(lessonEl,lesson.name); out.lesson.label=lessonEl.getAttribute("aria-label");
out.topic=check(topicEl,topic.name); out.topic.label=topicEl.getAttribute("aria-label");
out.controls=[lessonEl,topicEl].map(b=>({tag:b.tagName,disabled:b.disabled,tabIndex:b.tabIndex,action:typeof b.onclick==="function"}));
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
const fallback=document.createElement("style"); fallback.textContent=".sh::before{content:none!important}"; document.head.append(fallback);
out.fallback=[levelEl,lessonEl,topicEl].every((el,i)=>el.textContent.includes([level.name,lesson.name,topic.name][i].replaceAll("ّ",""))&&el.offsetWidth>0);
fallback.remove();
"""


def test_complete_path_renders_shadda_with_original_accessible_text_on_mobile(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", PATH_SHADDA, mobile=True)
    for name in ("level", "lesson", "topic"):
        item = r[name]
        assert item["wrappers"] == 1 and item["visible"] and item["baseText"]
        assert item["mark"]["content"] != "none" and item["mark"]["mask"] != "none"
        assert float(item["mark"]["top"].removesuffix("px")) < 0
        assert item["original"] in item["label"]
    assert r["controls"] == [{"tag": "BUTTON", "disabled": False, "tabIndex": 0, "action": True}] * 2
    assert r["noOverflow"] and r["fallback"] is True


EXERCISE_HEADING_SHADDA = """
await sleep(1500);
const headingTopic=CATS.find(c=>!c.story&&!c.bubble&&PHRASES.some(p=>p.cat===c.id));
const lesson=LESSONS.find(l=>l.id===headingTopic.lesson), phrase=PHRASES.find(p=>p.cat===headingTopic.id);
headingTopic.name="כֻּלّ"; lesson.name="דַרّס";
cat=headingTopic.id; home=false; document.body.classList.remove("home"); setRound(); show(phrase,true);
await until(()=>document.getElementById("catName").querySelector(".sh"));
const check=(id,original)=>{
  const el=document.getElementById(id), sh=el.querySelector(".sh"), pseudo=getComputedStyle(sh,"::before");
  return {wrappers:el.querySelectorAll(".sh").length, text:el.textContent, original,
    visible:!!(el.offsetWidth&&el.offsetHeight), mark:{content:pseudo.content,mask:pseudo.maskImage||pseudo.webkitMaskImage}};
};
out.topic=check("catName",headingTopic.name); out.lesson=check("lessonName",lesson.name);
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
"""


def test_exercise_heading_renders_shadda_on_mobile(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", EXERCISE_HEADING_SHADDA, mobile=True)
    for name in ("topic", "lesson"):
        item = r[name]
        assert item["wrappers"] == 1 and item["visible"]
        assert item["original"].replace("ّ", "") in item["text"]
        assert item["mark"]["content"] != "none" and item["mark"]["mask"] != "none"
    assert r["noOverflow"] is True


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
showView("home"); await sleep(100);
out.stillNew=!!document.querySelector("#vids .focus-refer.promo");
"""


def test_refer_a_friend_moves_from_the_nav_to_the_focused_home(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", REFER)
    assert r == {"pushCan": True, "navHidden": True, "view": "refer", "title": "חבר מביא חבר 🧡", "seen": "true",
                 "stillNew": False}   # once opened, the card is no longer marked


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


def test_managers_keep_the_path_and_a_classs_own_teacher_is_its_student(site):
    r = site("boss@x.com", PATH_FIRST)   # a manager of the whole service
    assert r == {"pushCan": True, "view": "", "asked": False, "homeTab": False}
    r = site("t@x.com", PATH_FIRST)      # a class's own teacher, a guide: asked for the lesson as its students are
    assert r["asked"] is True


TEACHER_NAV = """
await sleep(1500);
out.referTab=!document.querySelector('#nav [data-view="refer"]').hidden;
showView("games"); await sleep(100); showView(""); await sleep(100);   // drawn again
out.referTabAfter=!document.querySelector('#nav [data-view="refer"]').hidden;
"""


def test_teachers_have_no_refer_a_friend_tab(site):
    r = site("boss@x.com", TEACHER_NAV)
    assert r["referTab"] is False and r["referTabAfter"] is False


PREVIEW = """
await sleep(1500);
const pill=document.getElementById("myClass"), tab=v=>!document.querySelector(`#nav [data-view="${v}"]`).hidden;
const choices=()=>[...document.querySelectorAll("#picks button")];
out.pill=pill.textContent;
pill.click(); await until(()=>open("pickDlg"));
out.choices=choices().map(b=>b.firstChild.textContent);
choices().find(b=>b.firstChild.textContent==="כיתת א1").click();
await until(()=>open("lessonDlg"));
out.lessons=[...document.querySelectorAll("#lessonDlg input[name=lesson]")].map(r=>r.value);
document.querySelector('#lessonDlg input[value="4"]').checked=true;
document.querySelector("#lessonDlg form").requestSubmit();
await until(()=>view()==="home");
out.asStudent={pill:pill.textContent,where:document.querySelector("#vids .focus-where span").textContent,
  refer:!!document.querySelector("#vids .focus-refer"),homeTab:tab("home"),referTab:tab("refer"),lessons:LESSONS.map(l=>l.id)};
out.kept=localStorage.getItem("ihki.preview:boss@x.com");
pill.click(); await until(()=>open("pickDlg"));
choices()[0].click(); await sleep(300);   // every lesson again
out.back={pill:pill.textContent,view:view(),homeTab:tab("home"),referTab:tab("refer"),many:LESSONS.length>3};
"""


def test_a_teacher_views_the_app_as_a_classs_student_and_back(site):
    r = site("boss@x.com", PREVIEW)
    assert r["pill"] == "👁 תצוגה כתלמיד"
    assert r["choices"] == ["כל השיעורים", "כיתת א1", "כיתת אחרת"]
    assert r["lessons"] == ["3", "4", "5"]
    assert r["asStudent"] == {"pill": "👁 כתלמיד: כיתת א1", "where": r["asStudent"]["where"], "refer": True,
                              "homeTab": True, "referTab": False, "lessons": ["3", "4", "5"]}
    assert "שיעור 4" in r["asStudent"]["where"]
    assert r["kept"] == "7"
    assert ("boss@x.com", 7) not in site.auth.rows   # the teacher's choice is not a student's: the device keeps it
    assert r["back"] == {"pill": "👁 תצוגה כתלמיד", "view": "", "homeTab": False, "referTab": False, "many": True}


PREVIEW_PICK = """
await sleep(1500);
document.getElementById("myClass").click(); await until(()=>open("pickDlg"));
[...document.querySelectorAll("#picks button")].find(b=>b.firstChild.textContent==="כיתת א1").click();
await until(()=>open("lessonDlg"));
document.querySelector('#lessonDlg input[value="4"]').checked=true;
document.querySelector("#lessonDlg form").requestSubmit();
await until(()=>view()==="home");
"""
PREVIEW_KEPT = """
await sleep(1500);
out.view=view(); out.pill=document.getElementById("myClass").textContent;
"""


def test_a_teachers_view_is_kept_for_the_next_visit(site):
    site("boss@x.com", PREVIEW_PICK)
    r = site("boss@x.com", PREVIEW_KEPT)   # the same browser profile: the lesson and the view were kept on the device
    assert r == {"pushCan": True, "view": "home", "pill": "👁 כתלמיד: כיתת א1"}


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


SEVERAL_LEVELS = """
await until(()=>open("lessonDlg"));
const dlg=document.getElementById("lessonDlg");
out.groups=[...dlg.querySelectorAll(".lesson-group")].map(g=>g.textContent);
out.lessons=[...dlg.querySelectorAll("input[name=lesson]")].map(r=>r.value);
out.where=dlg.querySelector(".lesson-where").textContent;
dlg.querySelector('input[value="2.1"]').checked=true; dlg.querySelector("form").requestSubmit();
await until(()=>view()==="home");
out.home=document.querySelector("#vids .focus-where span").textContent;
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
"""


def test_a_class_of_two_levels_asks_for_a_lesson_of_either(site):
    site.auth.layout = [CLASSES[2]]   # lessons 5 and 2.1, like the guides' class
    r = site("dana@x.com", SEVERAL_LEVELS)
    assert r["lessons"] == ["5", "2.1"] and len(r["groups"]) == 2 and r["groups"][0] != r["groups"][1]
    assert r["where"] == "כיתת מעורבת"   # no one level's name: each heads its own lessons
    assert r["home"] == f"כיתת מעורבת · {r['groups'][1]} · שיעור 10"   # 2.1 is "שיעור 10", as is lesson 10 of level 1
    assert r["noOverflow"] and site.auth.rows[("dana@x.com", 9)]["current_lesson"] == "2.1"


AMBIGUOUS = """
await until(()=>open("lessonDlg"));
const dlg=document.getElementById("lessonDlg");
out.radios=dlg.querySelectorAll("input[type=radio]").length;
out.message=dlg.querySelector(".lesson-error").textContent;
dlg.querySelector("button").click();
await sleep(100);
out.view=view(); out.open=open("lessonDlg"); out.pathShown=document.querySelectorAll("#path .chapter").length>0;
"""


def test_a_class_that_names_no_lessons_asks_for_a_correction_and_keeps_the_path(site):
    site.auth.layout = [dict(CLASSES[2], lessons=[])]   # it studies every lesson: no current one can be told
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


RESUME_STARTED_TOPIC = """
await sleep(1500);
const lesson=LESSONS.find(l=>l.id===currentLesson()), topics=CATS.filter(c=>c.lesson===lesson.id&&!c.story);
const started=topics.slice(1).find(c=>PHRASES.some(p=>p.cat===c.id&&p.listed!==false));
const phrase=started&&PHRASES.find(p=>p.cat===started.id&&p.listed!==false);
if(!started||!phrase) throw new Error("the current lesson needs two practice topics");
cat=started.id; store.set("ihki.cat",cat); best[phrase.id]=60; store.set("ihki.best2",best);
drawNav();
const action=[...document.querySelectorAll("#vids .focus-action")].find(b=>b.textContent.includes("ממשיכים ב"));
out.started=started.name; out.proposed=action.querySelector(".focus-detail").textContent;
"""


def test_home_resumes_the_partly_started_topic_instead_of_the_first_unfinished_one(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", RESUME_STARTED_TOPIC)
    assert r["proposed"] == r["started"]


RESUME_OPEN_STORY = """
await sleep(1500);
const current=LESSONS.find(l=>l.id===currentLesson());
const started=CATS.find(c=>c.story&&c.lesson!==current.id&&PHRASES.some(p=>p.cat===c.id&&p.listed!==false));
if(!started) throw new Error("the class needs a story in an earlier lesson");
cat=started.id; store.set("ihki.cat",cat);   // opened, but left before the first line received a score
drawNav();
const action=[...document.querySelectorAll("#vids .focus-action")].find(b=>b.textContent.includes("ממשיכים ב"));
out.story=started.name;
out.lesson=LESSONS.find(l=>l.id===started.lesson).name;
out.proposed=action.querySelector(".focus-detail").textContent;
out.title=action.querySelector("strong").textContent;
"""


def test_home_resumes_an_open_story_before_its_first_score_even_from_an_earlier_lesson(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", RESUME_OPEN_STORY)
    assert r["proposed"] == r["story"]
    assert r["lesson"] in r["title"]


PRIORITY = """
await sleep(1500);
const titles=()=>[...document.querySelectorAll("#vids .focus-action strong")].map(s=>s.textContent);
const btn=document.getElementById("vidsBtn");
out.first=titles();
out.homework=document.querySelector("#vids .focus-action").getAttribute("aria-label");
out.btnShown=!btn.hidden&&getComputedStyle(btn).display!=="none"; out.btnNew=btn.classList.contains("new");
out.navLibrary=!!document.querySelector('#nav [data-view="library"]');
btn.click(); await sleep(200);
out.view=view(); out.headings=[...document.querySelectorAll("#vids h2,#vids h3")].map(h=>h.textContent);
document.querySelector("#vids button.now").click();   // today's video: watched now
await sleep(300);
document.querySelectorAll("dialog[open]").forEach(d=>d.close());
showView("home"); await sleep(100);
out.afterWatching=titles(); out.btnNewAfter=btn.classList.contains("new");
"""


def test_todays_video_waits_on_the_videos_button_not_the_home(site):
    from datetime import date, timedelta
    today, tomorrow = date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat()
    site.auth.layout[0] = dict(site.auth.layout[0], homeworks=[{"topic": "l4-say", "due": tomorrow}],
                               dailies=[{"video": 1, "date": today}])
    site.auth.daily_rows = [{"id": 1, "level": "1", "title": "ברוכים הבאים", "url": "https://vimeo.com/76979871"}]
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", PRIORITY)
    assert r["first"][0] == "שיעורי בית" and r["first"][1].startswith("ממשיכים ב")
    assert "הסרטון של היום" not in r["first"] and len(r["first"]) <= 3
    assert "מגישים מחר" in r["homework"]
    assert r["btnShown"] and r["btnNew"] and not r["navLibrary"]   # in the top row, marked; no longer a tab
    assert r["view"] == "daily" and r["headings"] == ["סרטון יומי"]   # the digital library has a button of its own
    assert r["btnNewAfter"] is False and r["first"] == r["afterWatching"]


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


LOG_OUT_TO_GOOGLE = """
await sleep(200);
const calls=[];
pushSignOut=async()=>calls.push("push");
sb.auth.signOut=async()=>{ calls.push("sign-out"); return {}; };
sb.auth.signInWithOAuth=async options=>{ calls.push("google"); out.oauth=options; return {}; };
document.getElementById("classExit").click();
await until(()=>calls.includes("google"));
out.calls=calls;
"""


def test_account_exit_signs_out_then_returns_to_google_authentication(site):
    r = site("dana@x.com", LOG_OUT_TO_GOOGLE)
    assert r["calls"] == ["push", "sign-out", "google"]
    assert r["oauth"]["provider"] == "google"
    assert r["oauth"]["options"]["queryParams"] == {"prompt": "select_account"}
    assert r["oauth"]["options"]["redirectTo"].startswith("http")


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


FALLING_SEARCH = """
await sleep(1500);
openFallingLetters();
await until(()=>document.querySelector(".fall-search"));
const search=()=>document.querySelector(".fall-search"), names=()=>[...document.querySelector(".fall-picker select").options].filter(o=>o.value).map(o=>o.textContent);
out.all=names(); out.groups=[...document.querySelectorAll(".fall-picker optgroup")].map(g=>g.label);
search().value="שא"; search().dispatchEvent(new Event("input"));
out.filtered=names();
search().value="זזז"; search().dispatchEvent(new Event("input"));
out.none=[...document.querySelector(".fall-picker select").options].map(o=>o.textContent);
search().value="ביקר"; search().dispatchEvent(new Event("input"));
search().dispatchEvent(new KeyboardEvent("keydown",{key:"Enter"}));
await until(()=>document.querySelector(".fall-base small")?.textContent.includes("ביקר"));
out.loaded=document.querySelector(".fall-base small").textContent; out.kept=search().value;
out.both=document.querySelector(".fall-base .tr").textContent; out.playHidden=document.querySelector(".fall-base .fall-play").hidden;
const card=document.querySelector(".fall-base .fall-reveal"); out.hidden=!card.classList.contains("revealed")&&getComputedStyle(card.firstElementChild).visibility==="hidden";
card.click(); out.shown=card.classList.contains("revealed")&&!document.querySelector(".fall-base .fall-hint"); out.play=!document.querySelector(".fall-base .fall-play").hidden;
document.querySelector(".fall-pronouns button").click(); out.stays=document.querySelector(".fall-base .fall-reveal").classList.contains("revealed");
"""


def test_falling_letters_search_filters_the_verbs(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", FALLING_SEARCH)
    assert len(r["all"]) > 3 and r["filtered"] == ["שאל"]
    assert r["none"] == ["לא נמצא פועל"]
    assert r["groups"][:3] == ["גזרת פַעַל", "גזרת ל״א", "גזרת ע״ו"]   # grouped by גזרה, in the course's order
    assert "ביקר" in r["loaded"] and "עבר ועתיד" in r["loaded"] and r["kept"] == "ביקר"
    assert " · " in r["both"] and r["playHidden"] and r["play"]   # past and future side by side; the sound button only after the reveal
    assert r["hidden"] and r["shown"] and r["stays"]   # hidden until clicked, then kept while the pronouns change


VERB_UPTO = """
await sleep(1200);
openVerbGame();
await until(()=>document.getElementById("verbUpto").options.length);
const sel=document.getElementById("verbUpto");
out.shown=!document.getElementById("verbUptoPick").hidden&&sel.offsetHeight>0;
out.options=[...sel.options].map(o=>o.value); out.picked=sel.value; out.label=sel.options[0].textContent;
const sent=[], real=window.fetch;
window.fetch=(url,opts)=>{ if(String(url).includes("/api/verb-game/question")) sent.push(JSON.parse(opts.body).upto); return real(url,opts); };
verbLevel=1; startVerbRound(); await until(()=>sent.length===1);
showVerbHub(); sel.value="5"; sel.dispatchEvent(new Event("change"));
startVerbRound(); await until(()=>sent.length===2);
out.sent=sent.map(x=>x===undefined?null:x); out.kept=JSON.parse(localStorage.getItem("ihki.verbUpto")||"null");
showVerbHub(); out.after=sel.value;
"""


def test_verb_game_lesson_picker_caps_the_round(site):
    r = site("boss@x.com", VERB_UPTO)   # a teacher: every lesson is open
    assert r["shown"] and r["options"][0] == "1" and "10" in r["options"] and r["picked"] == r["options"][-1]
    assert "שלב א" in r["label"]   # both stages are offered, so each lesson names its stage
    assert r["sent"] == [None, "5"] and r["after"] == "5"   # no cap by default; the choice is sent and kept


VERB_CONJ = """
await sleep(1200);
document.querySelector('#nav [data-view="verbs"]').click();
await until(()=>document.querySelector(".vt-row"));
const row=[...document.querySelectorAll(".vt-row")].find(r=>r.querySelector(".vt-he span").textContent==="שאל");
row.querySelector(".vt-he").click();
await until(()=>document.querySelector("#vtConjDlg .vc-table"));
out.open=open("vtConjDlg"); out.title=document.getElementById("vtConjTitle").textContent;
out.heads=[...document.querySelectorAll(".vc-table thead th")].map(t=>t.textContent);
out.rows=document.querySelectorAll(".vc-table tbody tr").length;
out.cells=[...document.querySelectorAll(".vc-table tbody td")].filter(t=>t.textContent.trim()&&t.textContent!=="—").length;
out.arabic=/[\u0600-\u06ff]/.test(document.getElementById("vtConjDlg").textContent.replace(/[\u0651]/g,""));
out.wide=document.querySelector(".vc-table").scrollWidth>document.querySelector(".vc-wrap").clientWidth+1;
document.getElementById("vtConjClose").click(); out.closed=!open("vtConjDlg");
"""


def test_a_click_on_a_verb_shows_all_its_forms(site):
    r = site("boss@x.com", VERB_CONJ)
    assert r["open"] and r["title"] == "שאל" and r["heads"] == ["", "עבר", "הווה", "עתיד"]
    assert r["rows"] == 8 and r["cells"] == 24 and not r["arabic"]   # every pronoun, three tenses, no Arabic script
    assert not r["wide"] and r["closed"]   # fits a phone's width


CONTENT_SECTIONS = """
await sleep(1200);
document.getElementById("toManage").click();
await sleep(300);
document.querySelector('#mModes [data-mode="content"]').click();
await until(()=>document.querySelector("#mContent .csec"));
const sums=sel=>[...document.querySelectorAll(sel)].map(s=>s.textContent.slice(0,s.textContent.length-(s.querySelector("small")||{textContent:""}).textContent.length).trim());
out.top=sums("#mContent > .csec > summary");
const digital=document.querySelector("#mContent > .csec");
out.sub=sums("#mContent > .csec:first-of-type .csub > summary");
digital.open=true; digital.querySelector(".csub").open=true;
out.stageOneCards=[...digital.querySelector(".csub").querySelectorAll(".card h2")].slice(0,2).map(h=>h.firstChild.textContent);
"""


def test_the_content_tab_is_split_into_the_library_sections(site):
    r = site("boss@x.com", CONTENT_SECTIONS)
    assert r["top"] == ["1. מערכת דיגיטלית", "2. בוגרים מספרים", "3. טיולים בכפר", "4. פרקי אבות עם אמיר",
                        "5. חַאחַ׳אם עידן", "6. מנחים ומתרגלים"]
    assert r["sub"] == ["שלב א", "שלב ב", "שלב ג", "שלב ד", "נִתמרַּן"]   # the page draws the shadda itself, outside the text
    assert r["stageOneCards"] == ["מערכת דיגיטלית · שלב א", "סרטונים יומיים"]   # its shelf, then the level's own content


ACCESS_TIERS = """
await sleep(1200);
document.getElementById("toManage").click();
await sleep(300);
document.querySelector('#mModes [data-mode="access"]').click();
await until(()=>document.querySelector("#mAccess .access-group"));
const groups=()=>[...document.querySelectorAll("#mAccess .access-group")];
out.groups=groups().map(g=>g.querySelector("summary b").textContent);
out.emails=groups().map(g=>[...g.querySelectorAll("tbody td:first-child small")].map(s=>s.textContent));
const row=[...document.querySelectorAll("#mAccess tbody tr")].find(r=>r.textContent.includes("dana@x.com"));
const sel=row.querySelector("select"), save=row.querySelector(".save-cell button");
out.saveBefore=save.disabled; out.options=[...sel.options].map(o=>o.value);
sel.value="admin"; sel.dispatchEvent(new Event("change")); out.saveAfter=save.disabled;
save.click();
await until(()=>groups()[0].textContent.includes("dana@x.com"));
out.moved=[...groups()[0].querySelectorAll("tbody td:first-child small")].map(s=>s.textContent);
out.selfLocked=[...document.querySelectorAll("#mAccess tbody tr")].find(r=>r.textContent.includes("boss@x.com")).querySelector("select").disabled;
// the class dialog: the library sections its students see; a section covers its sub-sections
document.querySelector('#mModes [data-mode="classes"]').click();
await until(()=>CLASS&&CLASS.library_tree);
editClass({id:1,name:"א",emails:["dana@x.com"],nicknames:{},lessons:[ALL_LESSONS[0].id],homeworks:[],dailies:[],library_sections:["digital","avot"]});
const boxes=[...document.querySelectorAll("#editLibrary input")];
out.ticked=boxes.filter(b=>b.checked).map(b=>b.value);
out.locked=boxes.filter(b=>b.disabled).map(b=>b.value);
let sent=null; const real=window.fetch;
window.fetch=async(url,opts={})=>{ if(String(url).includes("/api/classes/")){ sent=JSON.parse(opts.body); return new Response("{}",{status:200}); } return real(url,opts); };
boxes.find(b=>b.value==="digital").click(); boxes.find(b=>b.value==="d2").click();
document.getElementById("editForm").requestSubmit();
await until(()=>sent);
out.sent=sent.library_sections;
"""


def test_permissions_are_two_groups_and_a_class_picks_its_library_sections(site):
    access = {"id-dana@x.com": {"roles": ["student", "flagger"], "entitlements": []}}
    site.auth.users = lambda fresh=False: [{"id": f"id-{e}", "email": e, "name": e.split("@")[0]}
                                           for e in ("boss@x.com", "dana@x.com")]
    site.auth.access_rows = lambda: [{"user_id": k, **v} for k, v in access.items()]
    site.auth.access_for = lambda user_id, fresh=False: access.get(user_id)
    site.auth.set_access = lambda target, roles, entitlements, actor: access.__setitem__(
        target["id"], {"roles": roles, "entitlements": entitlements})
    r = site("boss@x.com", ACCESS_TIERS)
    assert r["groups"] == ["מנהלים", "תלמידים"] and r["options"] == ["admin", "student"]
    assert r["emails"] == [["boss@x.com"], ["dana@x.com"]]
    assert r["saveBefore"] and not r["saveAfter"] and "dana@x.com" in r["moved"] and r["selfLocked"]
    assert "admin" in access["id-dana@x.com"]["roles"]
    assert r["ticked"] == ["digital", "d1", "d2", "d3", "d4", "nitmaran", "avot"]
    assert r["locked"] == ["d1", "d2", "d3", "d4", "nitmaran"]
    assert r["sent"] == ["d2", "avot"]   # unticking the section frees its sub-sections; one picked alone


ADMIN_MESSAGE = """
await until(()=>open("msgDlg"));
out.title=document.getElementById("msgTitle").textContent; out.body=document.getElementById("msgText").textContent;
out.search=location.search;
document.getElementById("msgOk").click(); out.closed=!open("msgDlg");
"""


def test_an_admins_message_is_shown_again_when_its_notification_is_tapped(site):
    import urllib.parse
    from app import push as pushes
    url = pushes.message("אין שיעור מחר", "נתראה ביום ראשון")["url"]
    r = site("dana@x.com", ADMIN_MESSAGE, "?" + urllib.parse.urlparse(url).query)
    assert r["title"] == "אין שיעור מחר" and r["body"] == "נתראה ביום ראשון"
    assert r["search"] == "" and r["closed"] is True   # the address is left clean, and the window closes


PENDING = """
await sleep(1500); for(const d of document.querySelectorAll("dialog[open]")) d.close();
const closed=[], fake=(title,body,url)=>({title,body,data:{url},close(){ closed.push(title); }});
let tray=[fake("🎬 הסרטון היומי מחכה לכם","שלום","/?view=daily"),fake("אין שיעור מחר","נתראה","/?from=push-msg&title=x&body=y")];
Object.defineProperty(navigator.serviceWorker,"getRegistration",{value:async()=>({getNotifications:async()=>tray.splice(0)})});
await pendingNotices();
out.many={open:open("msgDlg"),title:document.getElementById("msgTitle").textContent,
  items:[...document.querySelectorAll("#msgList .msg-item b")].map(b=>b.textContent),closed:[...closed]};
document.querySelector("#msgList .msg-item:not(.plain)").click(); await sleep(300);
out.afterTap={open:open("msgDlg"),view:view()};
tray=[fake("🔥 רצף של 4 ימים","עוד לא תרגלתם היום.","/?from=push-streak")];
await pendingNotices();
out.one={title:document.getElementById("msgTitle").textContent,text:document.getElementById("msgText").textContent,
  go:document.getElementById("msgGo").hidden?null:document.getElementById("msgGo").textContent,list:!document.getElementById("msgList").hidden};
document.getElementById("msgOk").click();
await pendingNotices(); out.emptyStaysClosed=!open("msgDlg");
"""


def test_notifications_left_in_the_tray_are_shown_in_the_app_and_cleared(site):
    r = site("dana@x.com", PENDING)
    assert r["many"] == {"open": True, "title": "2 התראות חדשות", "items": ["אין שיעור מחר", "🎬 הסרטון היומי מחכה לכם"],
                         "closed": ["🎬 הסרטון היומי מחכה לכם", "אין שיעור מחר"]}   # newest first, and gone from the tray
    assert r["afterTap"] == {"open": False, "view": "daily"}
    assert r["one"] == {"title": "🔥 רצף של 4 ימים", "text": "עוד לא תרגלתם היום.", "go": "לתרגול", "list": False}
    assert r["emptyStaysClosed"] is True


LIBRARY_VIEW = """
await sleep(1500);
const btn=document.getElementById("libBtn");
out.shown=!btn.hidden; btn.click(); await sleep(300);
out.view=view();
out.headings=[...document.querySelectorAll("#vids h2,#vids h3")].map(h=>h.textContent);
out.first=document.querySelector("#vids .back").nextElementSibling.tagName;
out.videos=[...document.querySelectorAll("#vids button bdi")].map(b=>b.textContent);
"""


def test_the_library_button_shows_the_levels_lessons_under_their_own_names(site, monkeypatch):
    from app import library
    monkeypatch.setattr(library, "BASE_ROWS", [*library.SEED_ROWS, *library.LESSON_ROWS])
    site.auth.level_videos = lambda table, fresh=False: [] if table != "library" else [
        {"id": 1, "level": "d1", "title": "הכנה לקורס", "url": "https://vimeo.com/76979871"},
        {"id": 2, "level": "lesson-2", "title": "של שיעור 2", "url": "https://vimeo.com/76979872"}]
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", LIBRARY_VIEW)
    assert r["shown"] and r["view"] == "library"
    assert r["headings"] == [f"שיעור {n}" for n in range(1, 11)]   # no "ספרייה דיגיטלית", no "מערכת דיגיטלית · שלב א"
    assert r["first"] == "BUTTON" and r["videos"] == ["הכנה לקורס", "של שיעור 2"]   # the level's own videos first, bare


VIDEOS_VIEW = """
await sleep(1500);
const vids=document.getElementById("vidsBtn"), lib=document.getElementById("libBtn");
out.shown=!vids.hidden&&!lib.hidden; vids.click(); await sleep(300);
out.view=view();
out.headings=[...document.querySelectorAll("#vids h2,#vids h3")].map(h=>h.textContent);
out.videos=[...document.querySelectorAll("#vids button bdi")].map(b=>b.textContent);
lib.click(); await sleep(300);
out.libHeadings=[...document.querySelectorAll("#vids h2,#vids h3")].map(h=>h.textContent);
out.libVideos=[...document.querySelectorAll("#vids button bdi")].map(b=>b.textContent);
"""


def test_the_videos_button_shows_the_folders_outside_the_digital_library(site):
    site.auth.layout[0] = dict(site.auth.layout[0], library_sections=["d1", "village", "avot"])
    site.auth.level_videos = lambda table, fresh=False: [] if table != "library" else [
        {"id": 1, "level": "d1", "title": "הכנה לקורס", "url": "https://vimeo.com/76979871"},
        {"id": 2, "level": "village", "title": "טיול בכפר", "url": "https://vimeo.com/76979872"}]
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", VIDEOS_VIEW)
    assert r["shown"] and r["view"] == "daily"
    assert r["headings"] == ["העשרה", "טיולים בכפר", "פרקי אבות עם אמיר"] and r["videos"] == ["טיול בכפר"]
    assert r["libHeadings"] == ["שלב א"] and r["libVideos"] == ["הכנה לקורס"]   # מערכת דיגיטלית alone, under its own button


DAILY_BTN = """
await sleep(1500);
document.querySelector("#vids .focus-path").click(); await sleep(200);
const b=document.getElementById("dailyBtn");
out.shown=!b.hidden&&getComputedStyle(b).display!=="none"; out.text=b.textContent.trim(); out.isNew=b.classList.contains("new");
out.inTools=b.parentElement.className; out.title=b.title;
out.lib=document.getElementById("libBtn").textContent.trim();
const tools=document.querySelector("#today .ps-tools").getBoundingClientRect();
out.fits=[...document.querySelectorAll("#today .ps-tools button:not([hidden])")].every(x=>{ const r=x.getBoundingClientRect(); return r.left>=tools.left-1&&r.right<=tools.right+1; });
"""

FEEDBACK_BTN = """
await sleep(1500);
const fb=document.getElementById("feedbackBtn"), head=document.querySelector("header");
const seen=()=>{ const r=fb.getBoundingClientRect(), h=head.getBoundingClientRect(), dr=document.getElementById("driveBtn").getBoundingClientRect();
  return {text:fb.textContent.replace(/\\s+/g," ").trim(), shown:r.width>0&&getComputedStyle(fb).visibility!=="hidden", inside:r.left>=h.left-1&&r.right<=h.right+1,
    h:Math.round(r.height), font:parseFloat(getComputedStyle(fb).fontSize), headH:Math.round(h.height), w:Math.round(r.width), drive:[Math.round(dr.width),Math.round(dr.top-r.top)],
    where:Math.round(document.querySelector(".where").getBoundingClientRect().width), over:document.documentElement.scrollWidth>innerWidth}; };
out.view=view(); out.first=seen();
document.querySelector("#vids .focus-path")?.click(); await sleep(200);
await sleep(600);   // the path scrolls down to the learner's place: the header is gone, the progress bar stays
const fb2=document.getElementById("feedbackBtn2"), r2=fb2.getBoundingClientRect(), bar=document.querySelector("#today .path-stats").getBoundingClientRect(), note=document.querySelector("#today .ps-note");
const range=document.createRange(); range.selectNodeContents(note); const words=range.getBoundingClientRect(), day=document.querySelector("#today .ps-day").getBoundingClientRect();
out.path={text:fb2.textContent.trim(), scrolled:scrollY, headerOne:fb.getBoundingClientRect().width, onScreen:r2.top>=0&&r2.bottom<=innerHeight&&r2.width>0,
  inBar:r2.left>=bar.left&&r2.right<=bar.right&&r2.top>=bar.top&&r2.bottom<=bar.bottom, h:Math.round(r2.height),
  clear:(words.left>=r2.right||words.right<=r2.left)&&r2.top>=day.bottom, top:document.elementFromPoint(r2.left+r2.width/2,r2.top+r2.height/2)?.closest("button")?.id};
fb2.click(); out.pathOpens=open("feedbackDlg"); document.getElementById("feedbackClose").click();
home=false; document.body.classList.remove("home"); await sleep(100);
out.practice=seen();
fb.click(); out.opens=open("feedbackDlg"); out.title=document.getElementById("feedbackTitle").firstChild.textContent;
"""


def test_feedback_has_a_clear_button_in_the_header_on_every_screen(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", FEEDBACK_BTN)
    assert r["first"]["text"] == "💬משוב והצעות" and r["first"]["w"] > r["practice"]["w"]   # one word beside the lesson's name
    assert r["view"] == "home"   # the focused home page the learner lands on
    path = r["path"]   # on the path it is in the bar that stays on the screen, clear of the note and the day's count
    assert path["text"] == "💬משוב והצעות" and path["scrolled"] > 0 and not path["headerOne"], path
    assert path["onScreen"] and path["inBar"] and path["clear"] and path["h"] >= 28 and path["top"] == "feedbackBtn2", path
    assert r["pathOpens"]
    for where in ("first", "practice"):   # a real button, not a 12px label: seen, inside the header, nothing pushed off the screen
        seen = r[where]
        assert seen["shown"] and seen["inside"] and seen["h"] >= 34 and seen["font"] >= 15 and not seen["over"]
    assert r["opens"] and r["title"] == "משוב והצעות"


NEWS_ONCE = """
const asked=[]; window.pushAsk=async(key,where)=>{ asked.push(where); return {}; };
await until(()=>open("newsDlg"),6000);
const txt=id=>document.getElementById(id).textContent.trim(), hid=id=>document.getElementById(id).hidden;
out.title=txt("newsTitle"); out.items=[hid("newsPush"),hid("newsFb")]; out.push=txt("newsPushText"); out.why=!hid("newsPushWhy")&&txt("newsPushWhy");
out.fb=document.querySelector("#newsFb p").textContent; out.yes=!hid("newsPushYes")&&txt("newsPushYes");
out.fitsScreen=document.getElementById("newsDlg").getBoundingClientRect().bottom<=innerHeight;
document.getElementById("newsPushYes").click(); await sleep(300);
out.asked=asked; out.state=txt("newsPushState"); out.yesGone=hid("newsPushYes"); out.bellGone=hid("pushBarBtn");
document.getElementById("newsClose").click(); out.closed=!open("newsDlg"); out.kept=localStorage.getItem("ihki.news");
"""

NEWS_AGAIN = """
await sleep(6000);
out.again=open("newsDlg"); out.kept=localStorage.getItem("ihki.news");
"""

BELL = """
await sleep(1500);
out.before=open("newsDlg");   // seen already on this device: nothing comes up by itself
document.querySelector("#vids .focus-path").click(); await sleep(700);
const bell=document.getElementById("pushBarBtn"), r=bell.getBoundingClientRect(), fb=document.getElementById("feedbackBtn2").getBoundingClientRect();
const note=document.querySelector("#today .ps-note"), range=document.createRange(); range.selectNodeContents(note); const words=range.getBoundingClientRect();
out.bell={text:bell.textContent.trim(), onScreen:r.width>0&&r.top>=0&&r.bottom<=innerHeight, beside:r.right<=fb.left||r.left>=fb.right,
  clear:words.left>=Math.max(r.right,fb.right)||words.right<=Math.min(r.left,fb.left)};
bell.click(); await until(()=>open("newsDlg"),3000);
out.title=document.getElementById("newsTitle").textContent; out.fbHidden=document.getElementById("newsFb").hidden; out.yes=!document.getElementById("newsPushYes").hidden;
"""


def test_what_is_new_comes_up_once_and_turns_the_notifications_on(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", NEWS_ONCE, news=True)
    assert r["title"] == "חדש באפליקציה ✨" and r["items"] == [False, False] and r["fitsScreen"]
    assert "סרטון יומי" in r["push"] and "8:00" in r["push"] and "חשוב" in r["why"] and "משוב" not in r["push"]
    assert "ראש המסך" in r["fb"] and r["yes"] == "🔔 הפעלת התראות"
    assert r["asked"] == ["news"] and "פעילות" in r["state"] and r["yesGone"] and r["bellGone"]
    assert r["closed"] and r["kept"] == '"feedback-push"'
    again = site("dana@x.com", NEWS_AGAIN, news=True)   # the same device, the next visit
    assert again["again"] is False and again["kept"] == '"feedback-push"'


def test_the_paths_bell_opens_the_notifications_alone(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", BELL)
    assert r["before"] is False
    assert r["bell"] == {"text": "🔔התראות", "onScreen": True, "beside": True, "clear": True}
    assert r["title"] == "התראות מהאפליקציה" and r["fbHidden"] and r["yes"]


TALK = """
await until(()=>document.querySelector("#vids .focus-talk"),6000);
const card=document.querySelector("#vids .focus-talk"), says=()=>[...document.querySelectorAll("#vids .focus-talk .fb-say")].map(p=>[p.classList.contains("team")?"team":"user",p.firstChild.textContent.split(" · ")[0],p.lastChild.textContent]);
out.view=view(); out.title=card.querySelector("strong").textContent; out.mine=card.querySelector(".fb-mine").textContent; out.says=says();
out.wide=card.getBoundingClientRect().right<=innerWidth&&document.documentElement.scrollWidth<=innerWidth;
card.querySelector("textarea").value="במסלול, בטלפון"; card.querySelector("form").requestSubmit();
await until(()=>says().length===2,5000); out.after=says();
document.querySelector("#vids .focus-talk .talk-close").click(); out.closed=!document.querySelector("#vids .focus-talk"); out.away=localStorage.getItem("ihki.fbAway");
"""

INBOX = """
await sleep(800); showManage(); await sleep(300);
document.querySelector('#mModes [data-mode="flags"]').click();
await until(()=>document.querySelector(".fb-inbox .fb-item"),6000);
const item=()=>document.querySelector(".fb-inbox .fb-item");
out.before=item().querySelectorAll(".fb-say").length; out.place=item().querySelector("textarea").placeholder;
item().querySelector("textarea").value="תודה! באיזה מסך זה קרה?"; item().querySelector("form").requestSubmit();
await until(()=>item().querySelector(".fb-say"),5000);
const say=item().querySelector(".fb-say"); out.say=[say.className,say.firstChild.textContent.split(" · ")[0],say.lastChild.textContent];
out.buttons=[...item().querySelectorAll(".fb-actions button")].map(b=>b.textContent); out.empty=item().querySelector("textarea").value;
"""


def note(**more):
    return dict({"id": 1, "email": "dana@x.com", "kind": "problem", "rating": None, "message": "הכפתור לא עובד", "screen": "path",
                 "item": "", "status": "new", "created_at": "2026-11-10T08:00:00+00:00", "replies": []}, **more)


def test_the_teams_answer_is_a_conversation_on_the_learners_home(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    site.auth.notes = [note(replies=[{"from": "team", "by": "boss@x.com", "text": "באיזה מסך?", "at": "2026-11-11T09:00:00+00:00"}]),
                       note(id=2, message="בלי תשובה")]
    r = site("dana@x.com", TALK)
    assert r["view"] == "home" and "תשובה" in r["title"] and r["mine"] == "כתבתם: הכפתור לא עובד" and r["wide"]
    assert r["says"] == [["team", "הצוות", "באיזה מסך?"]]   # the answered note alone, and not who in the team wrote
    assert r["after"] == [["team", "הצוות", "באיזה מסך?"], ["user", "אתם", "במסלול, בטלפון"]]
    kept = site.auth.notes[0]
    assert kept["status"] == "new" and kept["replies"][1]["by"] == "dana@x.com"
    assert r["closed"] and r["away"] == '{"1":1}'


def test_a_teacher_answers_a_note_from_the_inbox(site):
    site.auth.notes = [note()]
    r = site("boss@x.com", INBOX)
    assert r["before"] == 0 and r["place"] == "תגובה למשתמש…"
    assert r["say"] == ["fb-say team", "boss@x.com", "תודה! באיזה מסך זה קרה?"] and r["empty"] == ""
    assert site.auth.notes[0]["status"] == "read" and "נקרא" not in r["buttons"]


def test_the_daily_video_button_is_in_the_progress_bar_even_without_a_video(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", DAILY_BTN)
    assert r["shown"] and r["inTools"] == "ps-tools" and "סרטון יומי" in r["text"] and not r["isNew"]
    assert r["title"] == "עוד אין סרטונים יומיים" and r["lib"] == "מערכת דיגיטלית" and r["fits"]


def test_the_daily_video_button_calls_for_todays_video(site):
    from datetime import date
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    site.auth.layout[0] = dict(site.auth.layout[0], dailies=[{"video": 1, "date": date.today().isoformat()}])
    site.auth.daily_rows = [{"id": 1, "level": "1", "title": "ברוכים הבאים", "url": "https://vimeo.com/76979871"}]
    r = site("dana@x.com", DAILY_BTN)
    assert r["shown"] and r["isNew"] and r["title"] == "ברוכים הבאים" and r["fits"]


PUSH_CARD = """
await until(()=>document.querySelector("#vids .focus-push"),5000);
const card=document.querySelector("#vids .focus-push");
out.title=card.querySelector("strong").textContent; out.detail=card.querySelector(".focus-detail").textContent;
out.beforeRefer=card.nextElementSibling.className.split(" ")[0];
out.reported=Object.keys(JSON.parse(localStorage.getItem("ihki.pushSeen")||"{}"));
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
let asked=0; window.pushOn=async()=>{ asked++; return {}; };   // the browser's own question, answered yes
card.click(); await sleep(300);
out.asked=asked; out.after=card.querySelector("strong").textContent;
window.pushState=async()=>({key:"k",hour:8,sub:{}});            // the device is subscribed now
showView(""); showView("home"); await sleep(400);
out.gone=!document.querySelector("#vids .focus-push");
"""


def test_the_home_keeps_a_reminder_card_until_the_device_turns_it_on(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", PUSH_CARD)
    assert "סרטון היומי" in r["title"] and "8:00" in r["detail"] and r["beforeRefer"] == "focus-refer"
    assert r["reported"] == ["shown:home"] and r["noOverflow"]
    assert r["asked"] == 1 and "פעילה" in r["after"] and r["gone"]


VIDEO_PROMPT = """
await sleep(1500);
document.getElementById("dailyBtn").click();                    // today's video
await until(()=>open("videoDlg"));
out.during=open("pushDlg");
document.getElementById("videoDlg").close();
await until(()=>open("pushDlg"),5000);
out.title=document.getElementById("pushTitle").textContent;
document.getElementById("pushNo").click();
out.closed=!open("pushDlg");
out.asked=[localStorage.getItem("ihki.pushAskedVideo")!==null,localStorage.getItem("ihki.pushAsked")];
document.getElementById("dailyBtn").click(); await until(()=>open("videoDlg"));
document.getElementById("videoDlg").close(); await sleep(2500);
out.again=open("pushDlg");                                       // put off: not on the next video
"""


def test_closing_a_daily_video_offers_tomorrows_by_notification(site):
    from datetime import date
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    site.auth.layout[0] = dict(site.auth.layout[0], dailies=[{"video": 1, "date": date.today().isoformat()}])
    site.auth.daily_rows = [{"id": 1, "level": "1", "title": "ברוכים הבאים", "url": "https://vimeo.com/76979871"}]
    r = site("dana@x.com", VIDEO_PROMPT)
    assert r["during"] is False and "הסרטון של מחר" in r["title"]
    assert r["closed"] and r["asked"] == [True, None] and r["again"] is False


EARLIER_LEVEL = """
await sleep(1500);
document.getElementById("libBtn").click(); await sleep(300);
const fold=()=>document.querySelector("#vids details.liblevel"), seen=()=>[...document.querySelectorAll("#vids button bdi")].filter(b=>b.checkVisibility()).map(b=>b.textContent);
out.button=fold()&&fold().querySelector("summary").textContent; out.closed=!fold().open;
out.before=seen();
out.headings=[...document.querySelectorAll("#vids h3")].filter(h=>h.checkVisibility()).map(h=>h.textContent);
fold().querySelector("summary").click(); await sleep(300);
out.after=seen();
drawNav(); await sleep(100);   // drawn again (as when a video's box closes): it stays open
out.kept=fold().open;
"""


def test_a_student_of_the_second_level_opens_on_it_and_the_first_waits_behind_a_button(site, monkeypatch):
    from app import library
    monkeypatch.setattr(library, "BASE_ROWS", [*library.SEED_ROWS, *library.LESSON_ROWS])
    site.auth.layout = [dict(CLASSES[4], library_sections=["d2", "d1"])]
    site.auth.level_videos = lambda table, fresh=False: [] if table != "library" else [
        {"id": 1, "level": "lesson-2", "title": "של שלב א", "url": "https://vimeo.com/76979871"},
        {"id": 2, "level": "lesson-2-1", "title": "של שלב ב", "url": "https://vimeo.com/76979872"}]
    site.auth.rows[("gil@x.com", 11)] = {"current_lesson": "2.1"}
    r = site("gil@x.com", EARLIER_LEVEL)
    assert r["button"] == "שלב א" and r["closed"]
    assert r["before"] == ["של שלב ב"] and r["headings"] == [f"שיעור {n}" for n in range(10, 19)]   # straight into שלב ב
    assert r["after"] == ["של שלב א", "של שלב ב"] and r["kept"]


WIDE_VIDEO = """
await sleep(1500);
Object.defineProperty(document,"fullscreenEnabled",{get:()=>false});   // as on an iPhone: no full screen for a frame
playVideo({id:1,title:"סרטון",embed:"about:blank"},true); await sleep(200);
const dlg=document.getElementById("videoDlg"), btn=document.getElementById("videoFull"), frame=()=>dlg.querySelector("iframe").getBoundingClientRect();
out.button=!btn.hidden&&btn.checkVisibility(); out.small=frame().height;
btn.click(); await sleep(200);
out.wide=[Math.round(dlg.getBoundingClientRect().width),Math.round(dlg.getBoundingClientRect().height)]; out.page=[innerWidth,innerHeight];
out.tall=frame().height; out.label=btn.textContent;
btn.click(); await sleep(200); out.back=frame().height;
btn.click(); await sleep(200); document.getElementById("videoClose").click(); await sleep(200);
out.cleared=!dlg.open&&!dlg.classList.contains("full");
"""


def test_where_a_frame_has_no_full_screen_the_videos_box_fills_the_page(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", WIDE_VIDEO)
    assert r["button"] and r["wide"] == r["page"] and r["tall"] > r["small"] * 2 and r["label"] == "הקטנה"
    assert r["back"] == r["small"] and r["cleared"]


# a player that answers the page as YouTube's does: its clock when it is called, and again after each seek
SUBTITLES = """
await sleep(1500);
const player="data:text/html,"+encodeURIComponent(`<script>addEventListener("message",e=>{ const d=JSON.parse(e.data), say=t=>parent.postMessage(JSON.stringify({event:"infoDelivery",info:{currentTime:t,playerState:2}}),"*"); if(d.event==="listening") say(0); if(d.func==="seekTo") say(d.args[0]); })<\\/script>`);
playVideo({title:"סרטון",embed:player,subs:[{at:1,tr:"מַרְחַבַּא!",he:"שלום!"},{at:5,tr:"כִּיף חַאלַכּ?",he:"מה שלומך?"}]},true);
await sleep(800);
const frame=document.querySelector("#videoBox iframe"), btn=document.getElementById("videoSubsBtn");
const written=()=>[...document.querySelectorAll("#videoSubs>span")].map(s=>s.textContent);
const seek=async t=>{ frame.contentWindow.postMessage(JSON.stringify({event:"command",func:"seekTo",args:[t]}),"*"); await sleep(300); return written(); };
out.before=await seek(0.2); out.first=await seek(1.5); out.second=await seek(7); out.back=await seek(2);
out.over=(()=>{ const s=document.getElementById("videoSubs").getBoundingClientRect(), f=frame.getBoundingClientRect(); return s.top>f.top&&s.bottom<f.bottom; })();
out.button=!btn.hidden&&btn.textContent; out.state=btn.ariaLabel;
const dlg=document.getElementById("videoDlg"), shownIn=id=>document.getElementById(id).checkVisibility();
out.whole=[Math.round(dlg.getBoundingClientRect().width),Math.round(dlg.getBoundingClientRect().height)]; out.page=[innerWidth,innerHeight];
out.bar=[shownIn("videoTitle"),shownIn("videoFull"),shownIn("videoClose"),document.getElementById("videoClose").textContent];
out.upright=frame.getBoundingClientRect().height>frame.getBoundingClientRect().width*1.5;
btn.click(); await sleep(200); out.none=written(); out.state2=btn.ariaLabel;
out.kept=localStorage.getItem("ihki.subs");
btn.click(); await sleep(200); out.trOnly=written();
btn.click(); await sleep(200); out.again=written();
document.getElementById("videoClose").click(); await sleep(200);
out.gone=!document.getElementById("videoSubs")&&btn.hidden;
playVideo({title:"סרטון",embed:"about:blank"},true); await sleep(200);
out.plain=!document.getElementById("videoSubs")&&btn.hidden&&!dlg.classList.contains("tall")&&document.getElementById("videoClose").textContent==="סגירה";
"""


def test_a_transcript_with_times_is_written_over_its_video(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", SUBTITLES)
    assert r["before"] == ["", ""] and r["first"] == ["מַרְחַבַּא!", "שלום!"] and r["second"] == ["כִּיף חַאלַכּ?", "מה שלומך?"]
    assert r["back"] == r["first"] and r["over"]
    assert r["button"] == "CC" and r["state"] == "כתוביות: תעתיק ותרגום" and r["state2"] == "כתוביות: בלי כתוביות"
    assert r["none"] == ["", ""] and r["kept"] == "2" and r["trOnly"] == ["מַרְחַבַּא!", ""] and r["again"] == r["first"]
    # upright, over the whole page, under a bar of the two buttons alone
    assert r["whole"] == r["page"] and r["upright"] and r["bar"] == [False, False, True, "✕"]
    assert r["gone"] and r["plain"]


SETTINGS = """
await until(()=>document.querySelector("#vids .focus-set"),6000);
const set=document.querySelector("#vids .focus-set"), box=document.getElementById("autoNextOn");
out.view=view(); out.label=set.textContent; out.order=[...document.querySelector("#vids .focus-more").children].map(c=>c.className);
set.click(); out.open=document.getElementById("setDlg").open; out.title=document.getElementById("setTitle").textContent;
out.on=box.checked; box.click(); out.kept=localStorage.getItem("ihki.autoNext");
"""


def test_the_personal_settings_open_from_the_home(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", SETTINGS)
    assert r["view"] == "home" and r["label"] == "⚙ הגדרות אישיות" and r["order"] == ["focus-path", "focus-set", "focus-help"]
    assert r["open"] and r["title"] == "הגדרות אישיות"
    assert r["on"] and r["kept"] == "false"   # moving on by itself is on until the student unticks it


# a word of the subtitles that the course teaches: its card, the student's dictionary, and the way to its topic
SUBTITLE_WORDS = """
await sleep(1500);
const topic=CATS[0], word={i:1,n:1,id:"פַתַח",tr:"פַתַח",he:"פתח",lesson:topic.lesson,lesson_name:"שיעור",topic:topic.id,topic_name:topic.name};
const far={i:0,n:1,id:"מִין",tr:"מִין",he:"מי",lesson:"99",lesson_name:"שיעור רחוק",topic:"l99-new",topic_name:"מילים"};
const asked=[]; addEventListener("message",e=>{ try{ const d=JSON.parse(e.data); if(d.event==="echo") asked.push(d.func); }catch{} });
const player="data:text/html,"+encodeURIComponent(`<script>addEventListener("message",e=>{ const d=JSON.parse(e.data), say=m=>parent.postMessage(JSON.stringify(m),"*"); if(d.event==="listening") say({event:"infoDelivery",info:{currentTime:1,playerState:2}}); if(d.func) say({event:"echo",func:d.func}); })<\\/script>`);
playVideo({title:"סרטון",embed:player,subs:[{at:0,tr:"מִין פַתַח אֵלְבַּאבּ?",he:"מי פתח את הדלת?",words:[far,word]}]},true);
await sleep(900);
const line=document.querySelector("#videoSubs>span"), words=[...line.querySelectorAll(".w")];
out.line=line.textContent; out.words=words.map(b=>b.textContent);
words[1].click(); await sleep(300);
const card=()=>document.getElementById("wordCard"), buttons=()=>[...card().querySelectorAll("button")];
out.card=[card().querySelector("strong").textContent,card().querySelector(".he").textContent,card().querySelector("small").textContent];
out.waits=asked.slice(); out.go=!buttons()[1].hidden;
buttons()[0].click(); await sleep(200);
out.saved=[buttons()[0].textContent,MY_WORDS.map(w=>w.id),JSON.parse(localStorage.getItem("ihki.words")).length];
buttons()[2].click(); await sleep(300); out.closed=!card(); out.goesOn=asked.slice(-1)[0];
words[0].click(); await sleep(300); out.farGo=buttons()[1].hidden;   // a lesson the student's path does not have
buttons()[0].click(); await sleep(100); buttons()[2].click(); await sleep(100);
document.getElementById("videoClose").click(); await sleep(200);
openWords(); await sleep(200);
const rows=()=>[...document.querySelectorAll("#wordsList li")];
out.list=rows().map(li=>li.querySelector("strong").textContent); out.listOpen=open("wordsDlg");
rows()[1].querySelectorAll("button")[1].click(); await sleep(200);   // the far word is taken out
out.afterRemove=rows().map(li=>li.querySelector("strong").textContent);
rows()[0].querySelectorAll("button")[0].click(); await sleep(600);   // to the lesson
out.went=[cat===topic.id,open("wordsDlg"),view()];
"""


def test_a_word_of_the_subtitles_opens_its_card_is_saved_and_leads_to_its_lesson(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", SUBTITLE_WORDS)
    assert r["line"] == "מִין פַתַח אֵלְבַּאבּ?" and r["words"] == ["מִין", "פַתַח"]
    assert r["card"][:2] == ["פַתַח", "פתח"] and r["card"][2].startswith("נלמדת ב: שיעור") and r["go"]
    assert r["waits"] == ["pauseVideo"] and r["closed"] and r["goesOn"] == "playVideo"
    assert r["saved"] == ["נשמר במילון שלי ✓", ["פַתַח"], 1] and r["farGo"]
    assert r["list"] == ["פַתַח", "מִין"] and r["listOpen"] and r["afterRemove"] == ["פַתַח"]
    assert r["went"] == [True, False, ""]
