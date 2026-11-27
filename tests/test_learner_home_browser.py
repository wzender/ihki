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

    def add_feedback(self, row):
        notes = self.__dict__.setdefault("notes", [])
        notes.append({**row, "id": len(notes) + 1, "status": "new", "replies": [], "created_at": "2026-11-18T08:00:00+00:00"})

    def put_feedback_image(self, name, data, mime):
        self.__dict__.setdefault("images", {})[name] = (data, mime)
        return True

    def feedback_image(self, name):
        return self.images[name]

    def writings(self, token):
        return {}

    def texts(self, fresh=False):   # the teachers' texts (self.text_rows), as the table gives them
        return [dict(r) for r in getattr(self, "text_rows", [])]


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
    if(!window.__stayOut){   // a case about an account that must not get in drives the opening screen itself
      await until(()=>ENTERED||entry());   // "practice", for an account with more screens; a student goes straight in
      out.pushCan=PUSH_CAN;
      if(!ENTERED) entry().click();
    }
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


def serve(auth, monkeypatch, tmp_path, course_store=None):
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
    cases, news_cases, out_cases, preloads = {}, set(), set(), {}
    app = create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth, course_store=course_store)

    @app.get("/t/{email}/{case}")
    def harness(email: str, case: str, request: Request):
        page = index_html()   # the page as the app serves it
        page = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/@supabase/supabase-js[^"]*"></script>',
                      fake_supabase(email, case in news_cases) if auth else "", page)
        stay = "<script>window.__stayOut=true</script>" if case in out_cases else ""
        if case in preloads:   # run before the page's own scripts (what the device kept from an earlier visit)
            page = page.replace("<head>", f"<head><script>{preloads[case]}</script>", 1)
        return HTMLResponse(page.replace("</body>", stay + DRIVER % cases[case] + "</body>"))

    def index_html():
        return next(r for r in app.routes if getattr(r, "path", "") == "/").endpoint().body.decode()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    while not server.started:
        time.sleep(0.05)
    profile = tmp_path / "chrome"

    def run(email, script, query="", width=390, mobile=False, news=False, stay_out=False, preload=""):
        case = f"c{len(cases)}"; cases[case] = script
        if preload:
            preloads[case] = preload
        if news:
            news_cases.add(case)
        if stay_out:
            out_cases.add(case)
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


LOOKUP = """
await sleep(1200);
document.getElementById("toManage").click(); await sleep(300);
document.querySelector('#mModes [data-mode="access"]').click();
await until(()=>document.querySelector("#mAccess .lookup"));
const card=document.querySelector("#mAccess .lookup"); out.closed=!card.open; card.open=true;
card.querySelector("input").value="Dana@x.com"; card.querySelector("form").requestSubmit();
await until(()=>card.querySelector(".lookup-item"),5000);
out.items=[...card.querySelectorAll(".lookup-item")].map(i=>[i.querySelector("b").textContent,!!i.querySelector(".access-badge"),
  [...i.querySelectorAll("li")].map(l=>[l.textContent,l.className])]);
document.querySelector('#mModes [data-mode="classes"]').click(); await sleep(200);
document.querySelector('#mModes [data-mode="access"]').click(); await until(()=>document.querySelector("#mAccess .lookup .lookup-item"),5000);
out.kept=document.querySelector("#mAccess .lookup").open&&document.querySelector("#mAccess .lookup input").value;
"""


def test_a_manager_looks_up_why_a_student_cannot_sign_in(site):
    site.auth.users = lambda fresh=False: [{"id": f"id-{e}", "email": e, "name": n} for e, n in
                                           (("boss@x.com", "Boss"), ("dana@x.com", "דנה"), ("dena@x.com", ""))]
    site.auth.signins = lambda fresh=False: {"id-dana@x.com": {"providers": ["google"], "last_sign_in": "2026-11-16T08:00:00Z", "created": ""},
                                             "id-dena@x.com": {"providers": ["email"], "last_sign_in": "", "created": ""}}
    site.auth.access_rows = lambda: []
    r = site("boss@x.com", LOOKUP)
    assert r["closed"]
    (dana, dena) = r["items"]
    assert dana[0] == "dana@x.com" and not dana[1]
    assert [t for t, _ in dana[2]][:3] == ["שם: דנה", "קבוצה: תלמידים", "נכנס/ת עם: גוגל"] and any(t.startswith("כיתות: ") for t, _ in dana[2])
    assert dena[0] == "dena@x.com" and dena[1]   # a letter away: perhaps a typo
    assert ["נכנס/ת עם: קישור במייל (אף פעם לא עם גוגל)", "warn"] in dena[2]
    assert ["לא רשום/ה בשום כיתה: אחרי הכניסה יופיע שאין כיתה.", "warn"] in dena[2]
    assert r["kept"] == "Dana@x.com"


IN_APP = """
await sleep(300);
const ua={whatsapp:"Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0 Mobile Safari/537.36 WhatsApp/2.23",
  webview:"Mozilla/5.0 (Linux; Android 13; Pixel 7; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0 Mobile Safari/537.36",
  chrome:"Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
  instagram:"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 300.0",
  gmailIos:"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
  safari:"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  chromeIos:"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0 Mobile/15E148 Safari/604.1"};
out.is=Object.fromEntries(Object.entries(ua).map(([k,v])=>[k,inAppBrowser(v)]));
out.before=document.getElementById("inApp").hidden;
Object.defineProperty(navigator,"userAgent",{get:()=>ua.whatsapp}); inAppNotice();
const box=document.getElementById("inApp"), open=document.getElementById("inAppOpen");
out.shown=!box.hidden&&box.checkVisibility(); out.text=box.textContent; out.chrome=!open.hidden&&open.href;
"""


def test_a_page_opened_inside_another_app_says_to_open_it_in_the_browser(site):
    r = site("dana@x.com", IN_APP)
    assert r["is"] == {"whatsapp": True, "webview": True, "chrome": False, "instagram": True, "gmailIos": True,
                       "safari": False, "chromeIos": False}
    assert r["shown"] and "האתר נפתח בתוך אפליקציה" in r["text"] and "פתחו את האתר ב-Chrome" in r["text"]
    assert r["chrome"].startswith("intent://") and "package=com.android.chrome" in r["chrome"]

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
const shownVids=()=>[...document.querySelectorAll("#vids button bdi")].filter(b=>b.checkVisibility()).map(b=>b.textContent);
out.before=shownVids(); out.folds=[...document.querySelectorAll("#vids details.liblevel summary")].map(s=>s.textContent);
document.querySelector("#vids details.liblevel summary").click(); await sleep(300);
out.after=shownVids();
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
    assert r["folds"] == ["טיולים בכפר", "פרקי אבות עם אמיר"] and r["before"] == [] and r["after"] == ["טיול בכפר"]   # closed until its title is clicked
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
document.querySelector('#mModes [data-mode="feedback"]').click();
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


BOARD = """
await sleep(800); showManage(); await sleep(300);
document.querySelector('#mModes [data-mode="feedback"]').click();
await until(()=>document.querySelector("#mFeedback .fb-board"),6000);
const cols=()=>[...document.querySelectorAll("#mFeedback .fb-col")].map(c=>[c.dataset.status,c.querySelector(".fb-col-head").textContent,
  [...c.querySelectorAll(".fb-msg")].map(m=>m.textContent)]);
out.before=cols(); out.talks=[...document.querySelectorAll("#mFeedback .fb-talks .fb-msg")].map(m=>m.textContent);
out.draggable=document.querySelector("#mFeedback .fb-board .fb-item").draggable;
out.replyFolded=!document.querySelector("#mFeedback .fb-board .fb-reply-fold").open;
document.querySelector('.fb-col[data-status="new"] .fb-actions button').click();   // "נקרא": the card moves to its column
await until(()=>cols()[0][2].length===0,5000); out.moved=cols();
// dragged from נקראו to טופלו
const card=[...document.querySelectorAll('.fb-col[data-status="read"] .fb-item')].find(i=>i.textContent.includes("עוד נקרא"));
const dt=new DataTransfer(); card.dispatchEvent(new DragEvent("dragstart",{dataTransfer:dt,bubbles:true}));
const done=document.querySelector('.fb-col[data-status="done"]');
done.dispatchEvent(new DragEvent("dragover",{dataTransfer:dt,bubbles:true,cancelable:true})); out.over=done.classList.contains("over");
done.dispatchEvent(new DragEvent("drop",{dataTransfer:dt,bubbles:true,cancelable:true}));
await until(()=>cols()[2][2].length===2,5000); out.dropped=cols();
"""


def test_general_feedback_is_a_board_and_the_tutor_notes_stand_apart(site):
    site.auth.notes = [note(id=1, message="חדש"), note(id=2, message="נקרא", status="read"),
                       note(id=3, message="טופל", status="done"), note(id=4, message="עוד נקרא", status="read"),
                       note(id=5, message="על השיחה", kind="tutor", screen="tutor", rating=4,
                            item="בשוק · ערבית מדוברת 3/5 · claude-haiku-5-5")]
    r = site("boss@x.com", BOARD)
    assert r["before"] == [["new", "חדשים · 1", ["חדש"]], ["read", "נקראו · 2", ["נקרא", "עוד נקרא"]],
                           ["done", "טופלו · 1", ["טופל"]]]
    assert r["talks"] == ["על השיחה"]   # the conversation's note is not on the board
    assert r["draggable"] and r["replyFolded"]
    assert r["moved"] == [["new", "חדשים · 0", []], ["read", "נקראו · 3", ["חדש", "נקרא", "עוד נקרא"]],
                          ["done", "טופלו · 1", ["טופל"]]]
    assert r["over"] and r["dropped"][2] == ["done", "טופלו · 2", ["טופל", "עוד נקרא"]]
    assert [n["status"] for n in site.auth.notes] == ["read", "read", "done", "done", "new"]


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
const lesson=(n,inA)=>[...document.querySelectorAll("#vids details.liblevel")].find(d=>d!==fold()&&fold().contains(d)===inA&&d.querySelector("summary").textContent===n);   // both levels have a שיעור 10
out.folded=[...document.querySelectorAll("#vids details.liblevel")].every(d=>!d.open);
lesson("שיעור 10",false).querySelector("summary").click(); await sleep(300); out.lesson=seen();
fold().querySelector("summary").click(); await sleep(300);
out.level=seen(); lesson("שיעור 2",true).querySelector("summary").click(); await sleep(300);
out.after=seen();
drawNav(); await sleep(100);   // drawn again (as when a video's box closes): what was opened stays open
out.kept=fold().open&&lesson("שיעור 2",true).open&&lesson("שיעור 10",false).open;
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
    # straight into שלב ב, its lessons folded under their names until one is opened
    assert r["before"] == [] and r["folded"] and r["headings"] == [f"שיעור {n}" for n in range(10, 19)]
    assert r["lesson"] == ["של שלב ב"] and r["level"] == ["של שלב ב"]   # שלב א opened: its lessons folded too
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


PATH_VIDEO = """
await until(()=>[...document.querySelectorAll("#path .sat")].some(b=>b.textContent.includes("היכרות")),8000);
const station=()=>[...document.querySelectorAll("#path .sat")].find(b=>b.textContent.includes("היכרות"));
out.station=[station().classList.contains("video"),station().querySelector(".label").textContent];
out.texts=[...document.querySelectorAll("#path .sat")].filter(b=>b.textContent.includes("בית הקפה")).length;
const asked=[]; Element.prototype.requestFullscreen=function(){ asked.push(this.id); return Promise.resolve(); };   // a scripted click is not a person's: the browser would refuse
station().click(); await sleep(400);
const dlg=document.getElementById("videoDlg"), r=dlg.getBoundingClientRect();
out.open=dlg.open; out.tall=dlg.classList.contains("tall"); out.whole=[Math.round(r.width),Math.round(r.height)]; out.page=[innerWidth,innerHeight];
out.src=document.querySelector("#videoBox iframe").src; out.subs=!!document.getElementById("videoSubs");
out.story=document.body.dataset.view||""; out.full=asked;
document.getElementById("videoClose").click(); await sleep(400);
out.closed=!dlg.open&&!document.fullscreenElement; out.seen=station().querySelector(".label").textContent;
"""


def test_a_paths_video_with_its_transcript_plays_at_once_over_the_whole_screen(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    site.auth.text_rows = [{"id": 2, "lesson": "5", "title": "היכרות", "voices": [], "pos": 99, "ord": 0,
                            "video": "https://www.youtube.com/watch?v=abcdefghijk",
                            "lines": [["שלום!", "מַרְחַבַּא!", 0.1], ["מה שלומך?", "כִּיף חַאלַכּ?", 3.1]]},
                           {"id": 4, "lesson": "5", "title": "בית הקפה", "voices": [], "pos": 99, "ord": 1,
                            "lines": [["שלום!", "מַרְחַבַּא!"]]}]
    r = site("dana@x.com", PATH_VIDEO)
    assert r["station"] == [True, "היכרותסרטון"] and r["texts"] == 1   # a video's station; a plain text is still a topic
    assert r["open"] and r["tall"] and r["whole"] == r["page"] and r["subs"]   # the video, its lines over it, the whole page
    assert "youtube-nocookie.com/embed/abcdefghijk" in r["src"] and r["story"] != "practice"   # not the dialogue to practise
    assert r["full"] == ["videoDlg"] and r["closed"] and r["seen"] == "היכרות✓ נצפה"

CAMEL_PICKS = """
await sleep(1000); openCamelRace();
await until(()=>document.querySelectorAll(".cr-setup .cr-pick").length===2,6000);
const rows=()=>[...document.querySelectorAll(".cr-setup .cr-pick")], chosen=()=>rows().map(r=>r.querySelector("button.on").textContent);
const press=(row,text)=>[...rows()[row].querySelectorAll("button")].find(b=>b.textContent===text).click();
out.start=chosen();
press(0,"4"); press(1,"15"); await sleep(100); out.picked=chosen();
press(1,"6"); press(1,"10"); press(0,"3"); await sleep(100); out.again=chosen();
out.state=[cr.teams,cr.steps]; out.pressed=[...rows()[1].querySelectorAll("button")].map(b=>b.getAttribute("aria-pressed"));
out.named=rows().map(r=>document.getElementById(r.parentElement.getAttribute("aria-labelledby")).textContent);
"""


def test_the_camel_races_team_and_step_counts_can_be_chosen(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", CAMEL_PICKS)
    assert r["start"] == ["2", "10"]
    assert r["picked"] == ["4", "15"]   # it used to jump back to the first, 2 and 6
    assert r["again"] == ["3", "10"] and r["state"] == [3, 10] and r["pressed"] == ["false", "true", "false"]
    assert r["named"] == ["כמה קבוצות?", "כמה צעדים עד לקו הסיום?"]

STAY_OUT = """
await until(()=>!document.getElementById("denied").hidden||!document.getElementById("meFail").hidden,25000);
const shown=id=>!document.getElementById(id).hidden;
out.denied=shown("denied"); out.fail=shown("meFail"); out.text=document.getElementById(out.denied?"deniedText":"meFailText").textContent;
out.entered=ENTERED; out.start=shown("startBtn"); out.view=document.body.dataset.view||"";
"""


def test_an_account_outside_every_class_is_told_its_address_and_does_not_get_in(site):
    r = site("typo@x.com", STAY_OUT, stay_out=True)
    assert r["denied"] and not r["fail"] and "typo@x.com" in r["text"] and "אינה באף כיתה" in r["text"]
    assert not r["entered"] and not r["start"] and r["view"] == ""


def test_a_server_that_cannot_say_who_the_account_is_lets_no_one_in(site, monkeypatch):
    from app.auth import AuthUnavailable
    def down(*a, **k):
        raise AuthUnavailable("supabase is waking up")
    monkeypatch.setattr(site.auth, "classes", down)   # every /api/me answers 503
    r = site("dana@x.com", STAY_OUT, stay_out=True)
    assert r["fail"] and not r["denied"] and "לא הצלחנו לבדוק את החשבון" in r["text"]
    assert not r["entered"] and not r["start"] and r["view"] == ""   # it used to open with every lesson on the path

MEMORY_DONE = """
await sleep(800);
const sent=[]; const real=window.fetch;
window.fetch=(url,opts={})=>{ if(String(url).includes("/api/games/")) sent.push([String(url),opts.method||"GET"]); return real(url,opts); };
openMemoryGame(); await until(()=>typeof mg!=="undefined"&&mg&&mg.n,6000);
out.before=sent.length; mgWin(); await sleep(300);
out.sent=sent.map(([u,m])=>[u.replace(location.origin,""),m]);
"""


def test_a_finished_memory_board_is_told_to_the_server(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", MEMORY_DONE)
    assert r["before"] == 0 and r["sent"] == [["/api/games/verb-memory/finished", "POST"]]

SEND_SHOT = """
await sleep(800);
document.getElementById("feedbackBtn2")?.click(); if(!open("feedbackDlg")) document.getElementById("feedbackBtn").click();
await until(()=>open("feedbackDlg"));
const c=document.createElement("canvas"); c.width=3000; c.height=1500; const g=c.getContext("2d"); g.fillStyle="#c33"; g.fillRect(0,0,3000,1500);
const blob=await new Promise(r=>c.toBlob(r,"image/png")), dt=new DataTransfer(); dt.items.add(new File([blob],"shot.png",{type:"image/png"}));
const input=document.getElementById("feedbackShot"); input.files=dt.files; input.dispatchEvent(new Event("change"));
await until(()=>!document.getElementById("feedbackShotView").hidden,5000);
const img=document.getElementById("feedbackShotImg"); out.small=img.src.startsWith("data:image/jpeg;base64,");
await until(()=>img.naturalWidth>0); out.size=[img.naturalWidth,img.naturalHeight];
document.getElementById("feedbackText").value="המסך נתקע"; document.getElementById("feedbackForm").requestSubmit();
await until(()=>document.getElementById("feedbackNote").textContent.includes("נשלח"),5000);
out.note=document.getElementById("feedbackNote").textContent; out.cleared=document.getElementById("feedbackShotView").hidden;
"""

SEE_SHOT = """
await sleep(1200);
document.getElementById("toManage").click(); await sleep(300);
await until(()=>document.getElementById("mFeedbackCount").textContent!=="הודעות מהמשתמשים",5000); out.count=document.getElementById("mFeedbackCount").textContent;
document.querySelector('#mModes [data-mode="feedback"]').click();
await until(()=>document.querySelector("#mFeedback .fb-image img"),6000);
const img=document.querySelector("#mFeedback .fb-image img"); await until(()=>img.naturalWidth>0);
out.shot=[img.naturalWidth,img.naturalHeight,img.closest("a").href.startsWith("blob:")];
out.inFlags=!!document.querySelector("#mFlags .fb-inbox");
"""


def test_a_student_attaches_a_screenshot_and_the_feedback_tab_shows_it(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    sent = site("dana@x.com", SEND_SHOT)
    assert sent["small"] and sent["size"] == [1600, 800]   # shrunk on the device, as a JPEG
    assert sent["note"] == "תודה! המשוב נשלח 🙏" and sent["cleared"]
    (note,) = site.auth.notes
    data, mime = site.auth.images[note["image"]]
    assert note["message"] == "המסך נתקע" and mime == "image/jpeg" and data[:3] == b"\xff\xd8\xff"
    seen = site("boss@x.com", SEE_SHOT)
    assert seen["count"] == "חדש אחד" and seen["shot"] == [1600, 800, True] and not seen["inFlags"]

TUTOR_USAGE_CARD = """
await sleep(1200);
document.getElementById("toManage").click(); await sleep(300);
document.querySelector('#mModes [data-mode="flags"]').click();
await until(()=>document.querySelector("#mFlags .tutor-usage .usage-tile"),8000);
const card=document.querySelector("#mFlags .tutor-usage");
out.title=card.querySelector("h2").textContent;
out.tiles=[...card.querySelectorAll(".usage-tile")].map(t=>[t.querySelector("small").textContent,t.querySelector("b").textContent]);
out.models=[...card.querySelectorAll(".usage-models li")].map(l=>l.textContent);
out.rows=card.querySelectorAll(".usage-days tr").length;
"""


def test_monitoring_shows_what_the_tutor_cost(site):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    site.auth.tutor_usage_rows = lambda since, limit=20_000: [
        {"created_at": now, "model": "claude-sonnet-5-5", "input_tokens": 100_000, "output_tokens": 20_000, "turns": 10},
        {"created_at": now, "model": "claude-haiku-5-5", "input_tokens": 50_000, "output_tokens": 5_000, "turns": 4}]
    r = site("boss@x.com", TUTOR_USAGE_CARD)
    assert r["title"] == "💬 יאללה נדבר: שימוש ועלות"
    # Sonnet: 0.2 + 0.2 = $0.40; Haiku: 0.005 + 0.0025 = $0.0075, so $0.41 in all
    assert r["tiles"][:2] == [["עלות משוערת", "$0.41"], ["היום", "$0.41"]] and r["tiles"][2:] == [["שיחות", "2"], ["משפטים", "14"]]
    assert r["models"][0].startswith("claude-sonnet-5-5: שיחה אחת") and r["models"][0].endswith("$0.40")
    assert r["rows"] == 2   # the header and today

TUTOR_FEEDBACK_FORM = """
await sleep(800);
tc={id:"chat-1"}; const form=tcFeedback(); document.body.append(form);
out.intro=form.querySelector("p").textContent;
out.questions=[...form.querySelectorAll(".tc-fb-q b")].map(b=>b.textContent);
const sent=[]; window.fetch=async(url,opts={})=>{ sent.push([String(url).replace(location.origin,""),JSON.parse(opts.body)]); return new Response('{"ok":true}',{status:200}); };
form.requestSubmit(); await sleep(100); out.unanswered=form.querySelector(".tc-fb-note").textContent; out.before=sent.length;
const [q,sp]=form.querySelectorAll(".tc-fb-scale");
q.children[3].click(); sp.children[1].click(); out.pressed=[...sp.children].map(b=>b.getAttribute("aria-pressed"));
form.querySelector("textarea").value="קצת פורמלי"; form.requestSubmit();
await until(()=>form.textContent.includes("תודה"),3000);
out.sent=sent; out.after=form.textContent;
"""


def test_the_conversations_end_asks_how_it_went_and_sends_it(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", TUTOR_FEEDBACK_FORM)
    assert "פיצ'ר בהרצה" in r["intro"]
    assert r["questions"] == ["איך הייתה השיחה?", "כמה זו הייתה ערבית מדוברת אמיתית?"]
    assert r["unanswered"] == "בחרו ציון בשתי השאלות." and r["before"] == 0
    assert r["pressed"] == ["false", "true", "false", "false", "false"]
    assert r["sent"] == [["/api/tutor/feedback", {"id": "chat-1", "quality": 4, "spoken": 2, "message": "קצת פורמלי"}]]
    assert r["after"] == "תודה! המשוב נשלח 🙏"

TUTOR_NOTE_MODEL = """
await sleep(1200);
document.getElementById("toManage").click(); await sleep(300);
document.querySelector('#mModes [data-mode="feedback"]').click();
await until(()=>document.querySelector("#mFeedback .fb-item"),6000);
out.chips=[...document.querySelectorAll("#mFeedback .fb-item")].map(i=>i.querySelector(".fb-model")?.textContent||null);
out.kind=document.querySelector("#mFeedback .fb-item .flag-kind").textContent;
"""


def test_a_note_on_a_tutor_conversation_shows_its_model(site):
    site.auth.notes = [note(id=1, kind="tutor", screen="tutor", rating=4, message="טבעי",
                            item="בבית הקפה · ערבית מדוברת 5/5 · claude-sonnet-5-5"),
                       note(id=2, created_at="2026-11-09T08:00:00+00:00")]
    r = site("boss@x.com", TUTOR_NOTE_MODEL)
    assert r["chips"] == [None, "מודל: claude-sonnet-5-5"]   # the board first, then the conversations' notes
    assert r["kind"] == "🐞 משהו לא עובד"

TALKS_SUMMARY = """
await sleep(800); showManage(); await sleep(300);
document.querySelector('#mModes [data-mode="feedback"]').click();
await until(()=>document.querySelector("#mFeedback .talks-summary"),6000);
out.rows=[...document.querySelectorAll("#mFeedback .talks-summary tr")].map(tr=>[...tr.children].map(c=>c.textContent));
"""


def test_the_tutor_notes_are_summed_up_by_model(site):
    talk = lambda i, model, quality, spoken: note(id=i, kind="tutor", screen="tutor", rating=quality, message="",
                                                  item=f"בשוק · ערבית מדוברת {spoken}/5 · {model}")
    site.auth.notes = [talk(1, "claude-haiku-5-5", 4, 2), talk(2, "claude-haiku-5-5", 3, 3), talk(3, "claude-haiku-5-5", 5, 4),
                       talk(4, "claude-sonnet-5-5", 5, 5), note(id=5, message="כללי")]
    r = site("boss@x.com", TALKS_SUMMARY)
    assert r["rows"] == [["מודל", "משובים", "איכות השיחה", "ערבית מדוברת"],
                         ["claude-haiku-5-5", "3", "4.0 / 5", "3.0 / 5"],
                         ["claude-sonnet-5-5", "1", "5.0 / 5", "5.0 / 5"]]

KEPT_SCORE = """
await until(()=>typeof PHRASES!=="undefined"&&PHRASES.length&&Object.keys(best).length,8000);
const id=window.__secondId;
out.he=phraseOf(id)?.he; out.best=best[id]; out.newSeq=phraseOf(window.__newId)?.seq; out.oldSeq=phraseOf(id)?.seq;
"""


def test_a_score_stays_on_its_sentence_when_one_is_inserted_before_it(site):
    """The page names a sentence by its id: with a sentence inserted at the head of its topic, the learner's score is
    still on the sentence they said, and the page lists the new one first."""
    import copy
    from app import course, phrases
    doc = copy.deepcopy(course.document())
    say = next(t for t in doc["levels"][0]["lessons"][4]["topics"] if t["tid"] == "say")   # lesson 5
    second = say["items"][1]
    say["items"].insert(0, {"id": doc["next_item_id"], "key": "שלום לכם", "he": "שלום לכם", "ar": "مرحبا"})
    doc["next_item_id"] += 1
    original = course.current()
    phrases.serve(course.compile_course(doc))
    try:
        site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
        site.auth.attempts = lambda token: [{"cat": "l5-say", "phrase": second["key"], "score": 91,
                                             "created_at": "2026-11-18T08:00:00+00:00"}]
        script = f"window.__secondId={second['id']}; window.__newId={doc['next_item_id'] - 1};" + KEPT_SCORE
        r = site("dana@x.com", script)
    finally:
        phrases.serve(original)
    assert r["he"] == second["he"] and r["best"] == 91
    assert r["newSeq"] == 1 and r["oldSeq"] == 3

COURSE_CARD = """
await sleep(1200);
document.getElementById("toManage").click(); await sleep(300);
document.querySelector('#mModes [data-mode="flags"]').click();
await until(()=>document.querySelector("#mFlags .course-status .meta, #mFlags .course-status code"),8000);
await until(()=>!document.querySelector("#mFlags .course-status").textContent.includes("טוען"),8000);
const card=document.querySelector("#mFlags .course-status");
out.text=card.textContent; out.sql=card.querySelector("code")?.textContent||"";
"""


def test_monitoring_offers_the_sql_for_the_course_versions_until_it_is_run(monkeypatch, tmp_path):
    """Without the course tables the card says the code workbook is served and gives the SQL."""
    from app import course_store
    from tests.test_course_store import VersionsAuth
    store = course_store.CourseStore(VersionsAuth(missing=True), 441, cache=tmp_path / "c.json")
    store.load()
    import tests.test_learner_home_browser as me
    gen = me.serve(me.PageAuth(), monkeypatch, tmp_path)
    import app.main as main
    real = main.create_app
    monkeypatch.setattr(main, "create_app", lambda *a, **k: real(*a, **{**k, "course_store": store}))
    run = next(gen)
    try:
        r = run("boss@x.com", COURSE_CARD)
    finally:
        next(gen, None)
    assert "מקור: החוברת שבקוד" in r["text"] and "create table public.course_versions" in r["sql"]

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
const topic=CATS.find(c=>!c.learn&&!c.match&&!c.write&&!c.story), word={i:1,n:1,id:"פַתַח",tr:"פַתַח",he:"פתח",lesson:topic.lesson,lesson_name:"שיעור",topic:topic.id,topic_name:topic.name};
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
showPath(); await sleep(200); goWordTopic({topic:CATS.find(c=>c.learn).id}); await sleep(400);
out.newWords=[open("learnDlg"),home];   // a topic of new words opens as on the path: in its own box
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
    assert r["newWords"] == [True, True]


# from a word of a video to its lesson, and back to the video at the sentence it was left at
VIDEO_AND_BACK = """
await sleep(1500);
const topic=CATS.find(c=>!c.learn&&!c.match&&!c.write&&!c.story), news=CATS.find(c=>c.learn);
const word=t=>({i:0,n:1,id:"פַתַח",tr:"פַתַח",he:"פתח",lesson:t.lesson,lesson_name:"שיעור",topic:t.id,topic_name:t.name});
const player="data:text/html,"+encodeURIComponent(`<script>addEventListener("message",e=>{ const d=JSON.parse(e.data), say=m=>parent.postMessage(JSON.stringify(m),"*"); if(d.event==="listening") say({event:"infoDelivery",info:{currentTime:8,playerState:1}}); })<\\/script>`);
const video=t=>({id:"t1",title:"סרטון",tall:true,embed:player+"?x=1",subs:[{at:0,tr:"מַרְחַבַּא",he:"שלום"},{at:7.5,tr:"פַתַח אֵלְבַּאבּ",he:"פתח את הדלת",words:[word(t)]}]});
const back=()=>document.getElementById("videoBack"), frame=()=>document.querySelector("#videoBox iframe");
const toLesson=async()=>{ document.querySelector("#videoSubs .w").click(); await sleep(300); document.querySelectorAll("#wordCard button")[1].click(); await sleep(500); };
showView("library"); await sleep(200);
playVideo(video(topic)); await sleep(900);
out.close=document.getElementById("videoClose").textContent;
await toLesson();
out.lesson=[cat===topic.id,home,open("videoDlg"),back()&&back().textContent];
back().click(); await sleep(900);
out.returned=[open("videoDlg"),view(),home,/start=7$/.test(frame().src),!back(),document.querySelector("#videoSubs>span").textContent];
await toLesson(); document.getElementById("homeBtn").click(); await sleep(300);
out.leftAlone=!back();   // the lesson was left by its own way out
showView("library"); playVideo(video(news)); await sleep(900); await toLesson();
out.newWords=[open("learnDlg"),document.getElementById("learnDlg").contains(back())];
back().click(); await sleep(900);
out.fromNewWords=[open("learnDlg"),open("videoDlg"),view()];
document.getElementById("videoClose").click(); await sleep(200);
pickCat(topic.id); await sleep(400); const was=cur.id;
playVideo(video(news),true); await sleep(900);
out.fromPractice=document.getElementById("videoClose").textContent;
await toLesson(); back().click(); await sleep(900);
out.backToPractice=[open("videoDlg"),home,cat===topic.id,cur.id===was];
"""


def test_a_lesson_opened_from_a_videos_word_leads_back_to_the_video(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", VIDEO_AND_BACK)
    assert r["close"] == "✕" and r["lesson"] == [True, False, False, "▶ חזרה לסרטון"]
    assert r["returned"] == [True, "library", True, True, True, "פַתַח אֵלְבַּאבּ"]
    assert r["leftAlone"] and r["newWords"] == [True, True] and r["fromNewWords"] == [False, True, "library"]
    assert r["fromPractice"] == "✕ חזרה לתרגול" and r["backToPractice"] == [True, False, True, True]


# a word of a practised sentence's answer that is said in a video: pressing it plays the video from that sentence
ANSWER_WORD = """
await sleep(1500);
for(const c of CATS.filter(c=>!c.story&&!c.learn&&!c.match&&!c.write&&!c.text)){ pickCat(c.id); await sleep(400); if(cur&&!cur.read&&document.getElementById("wordReveals").firstElementChild) break; }
out.sentence=!!cur&&!cur.read;
const asked=[]; addEventListener("message",e=>{ try{ const d=JSON.parse(e.data); if(d.event==="echo") asked.push([d.func,d.args]); }catch{} });
const player="data:text/html,"+encodeURIComponent(`<script>addEventListener("message",e=>{ const d=JSON.parse(e.data), say=m=>parent.postMessage(JSON.stringify(m),"*"); if(d.event==="listening") say({event:"infoDelivery",info:{currentTime:0,playerState:1}}); if(d.func) say({event:"echo",func:d.func,args:d.args}); })<\\/script>`);
const first=cur.tr.trim().split(/\\s+/)[0];
LIB_TEXTS=[{id:"t1",title:"סרטון",tall:true,embed:player,subs:[{at:0,tr:"סַמִיר",he:"סמיר"},{at:12.4,tr:first.replace(/[?!.,]/g,""),he:"מילה"}]}]; VIDEO_WORDS=null;
drawWordReveals();
const card=document.getElementById("wordReveals").firstElementChild, word=card.querySelector(".vword");
out.marked=!!word&&word.textContent===first; out.answer=card.firstElementChild.textContent===cur.tr.trim();
const was=view(); word.click(); await sleep(300);
out.firstPress=[card.classList.contains("revealed"),open("videoDlg")];   // the first press only shows the answer
word.click(); await sleep(1200);
const frame=document.querySelector("#videoBox iframe");
out.plays=[open("videoDlg"),/autoplay=1&start=12$/.test(frame.src),document.getElementById("videoDlg").classList.contains("tall")];
out.asked=asked.map(([f,a])=>[f,a.map(x=>typeof x==="number"?Math.round(x*10)/10:x)]); out.line=document.querySelector("#videoSubs>span").textContent;
document.getElementById("videoClose").click(); await sleep(200);
out.back=[open("videoDlg"),view()===was,card.isConnected];   // back on the same card
"""


def test_a_word_of_the_answer_plays_its_sentence_in_the_video(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", ANSWER_WORD)
    assert r["sentence"] and r["marked"] and r["answer"]
    assert r["firstPress"] == [True, False] and r["plays"] == [True, True, True]
    assert r["asked"] == [["seekTo", [12.2, True]], ["playVideo", []]] and r["line"]
    assert r["back"] == [False, True, True]


# Vimeo's player says its time only when asked (its "timeupdate" does not always come): the subtitles follow the answers
VIMEO_SUBTITLES = """
await sleep(1500);
const player="data:text/html,"+encodeURIComponent(`<!-- vimeo --><script>let at=0; const asked=[]; addEventListener("message",e=>{ const d=JSON.parse(e.data), say=m=>parent.postMessage(JSON.stringify(m),"*"); if(d.method==="getCurrentTime") say({method:"getCurrentTime",value:at}); else if(d.method==="setCurrentTime"){ at=d.value; say({method:"setCurrentTime",value:at}); } else if(d.method==="tell") say({event:"told",asked}); else asked.push(d.method||d.event); })<\\/script>`);
playVideo({title:"סרטון",embed:player,subs:[{at:0.1,tr:"מַרְחַבַּא!",he:"שלום!"},{at:5,tr:"כִּיף חַאלַכּ?",he:"מה שלומך?"}]},true);
const frame=document.querySelector("#videoBox iframe"), written=()=>[...document.querySelectorAll("#videoSubs>span")].map(s=>s.textContent);
const tell=m=>frame.contentWindow.postMessage(JSON.stringify(m),"*");
await sleep(1200); out.before=written();
tell({method:"setCurrentTime",value:1}); await sleep(900); out.start=written();
tell({method:"setCurrentTime",value:7}); await sleep(900); out.later=written();
tell({method:"setCurrentTime",value:2}); await sleep(900); out.back=written();
let told=null; addEventListener("message",e=>{ try{ const d=JSON.parse(e.data); if(d.event==="told") told=d.asked; }catch{} });
tell({method:"tell"}); await until(()=>told);
out.asked=[...new Set(told)].sort();
"""


def test_the_subtitles_follow_vimeos_clock_by_asking_for_it(site):
    site.auth.rows[("dana@x.com", 7)] = {"current_lesson": "5"}
    r = site("dana@x.com", VIMEO_SUBTITLES)
    assert r["before"] == ["", ""] and r["start"] == ["מַרְחַבַּא!", "שלום!"] and r["later"] == ["כִּיף חַאלַכּ?", "מה שלומך?"] and r["back"] == r["start"]
    assert r["asked"] == ["addEventListener"]   # never YouTube's "listening" call


LOGIN_HELP = STAY_OUT + """
const btn=document.getElementById("loginHelpBtn");
out.shown=!btn.hidden; out.label=btn.textContent;
btn.click(); await until(()=>open("loginHelpDlg"));
const form=document.getElementById("loginHelpForm");
out.reason=form.elements.lhReason.value; out.email=document.getElementById("loginHelpEmail").value;
out.fits=document.documentElement.scrollWidth<=innerWidth;
document.getElementById("loginHelpText").value="ניסיתי כמה פעמים";
form.requestSubmit();
await until(()=>/תודה/.test(document.getElementById("loginHelpNote").textContent),8000);
out.note=document.getElementById("loginHelpNote").textContent; out.sendHidden=document.getElementById("loginHelpSend").hidden;
"""


def test_someone_who_cannot_get_in_tells_the_team_from_the_opening_screen(site):
    r = site("typo@x.com", LOGIN_HELP, stay_out=True)
    assert r["denied"] and r["shown"] and "לא מצליחים להיכנס" in r["label"]
    assert r["reason"] == "denied" and r["email"] == "typo@x.com" and r["fits"]
    assert "typo@x.com" in r["note"] and r["sendHidden"]
    note = site.auth.notes[-1]
    assert (note["screen"], note["email"], note["user_id"], note["message"]) == ("login", "typo@x.com", None, "ניסיתי כמה פעמים")
    assert note["item"].startswith("כתוב שהכתובת אינה באף כיתה · denied")


INSIDE = """
await sleep(1000); const b=document.getElementById("loginHelpBtn"); out.hidden=!b||b.hidden||!b.offsetParent; out.entered=ENTERED;
"""


def test_the_button_is_not_there_once_inside(site):
    r = site("dana@x.com", INSIDE)
    assert r["entered"] and r["hidden"]


JOIN = STAY_OUT + """
document.getElementById("joinBtn").click(); await until(()=>open("joinDlg"));
out.who=document.getElementById("joinWho").textContent; out.focused=document.activeElement.id;
document.getElementById("joinForm").requestSubmit(); await sleep(100);
out.empty=document.getElementById("joinNote").textContent;
document.getElementById("joinName").value="דנה כהן"; document.getElementById("joinPhone").value="050-1234567";
document.getElementById("joinClass").value="שלב א, שלישי";
document.getElementById("joinForm").requestSubmit();
await until(()=>/נשלחה/.test(document.getElementById("joinNote").textContent),8000);
out.note=document.getElementById("joinNote").textContent; out.fits=document.documentElement.scrollWidth<=innerWidth;
"""


def test_an_account_in_no_class_asks_to_join_one(site):
    r = site("typo@x.com", JOIN, stay_out=True)
    assert "typo@x.com" in r["who"] and r["focused"] == "joinName" and "שם מלא" in r["empty"]
    assert "typo@x.com" in r["note"] and r["fits"]
    note = site.auth.notes[-1]
    assert (note["screen"], note["email"]) == ("join", "typo@x.com") and "דנה כהן" in note["item"]


MAIL = STAY_OUT + """
const opened=[]; window.openMail=url=>opened.push(url);
out.onPage=document.documentElement.outerHTML.includes("office"+String.fromCharCode(64));
document.getElementById("joinBtn").click(); await until(()=>open("joinDlg"));
document.getElementById("joinName").value="דנה כהן"; document.getElementById("joinClass").value="שלב א";
document.getElementById("joinMail").click(); document.getElementById("joinDlg").close();
document.getElementById("loginHelpBtn").click(); await until(()=>open("loginHelpDlg"));
document.getElementById("loginHelpMail").click();
out.opened=opened.map(decodeURIComponent);
out.onPageAfter=document.documentElement.outerHTML.includes("office"+String.fromCharCode(64));
"""


def test_the_forms_offer_a_mail_to_the_office_without_showing_its_address(site):
    r = site("typo@x.com", MAIL, stay_out=True)
    assert not r["onPage"] and not r["onPageAfter"]
    join, login = r["opened"]
    assert join.startswith("mailto:office@ahalawasahala.com?subject=בקשת הצטרפות לכיתה&body=")
    assert "שם: דנה כהן" in join and "typo@x.com" in join and "כיתה / מורה: שלב א" in join and "טלפון" not in join
    assert login.startswith("mailto:office@ahalawasahala.com?subject=בעיה בכניסה לאפליקציה")
    assert "כתוב שהכתובת אינה באף כיתה" in login and "typo@x.com" in login


def test_with_sign_in_the_start_button_is_sent_hidden_until_the_account_is_checked():
    """A slow load showed "יאללה נבלש" alone, a button that did nothing yet: with sign-in it comes hidden, "טוען…" in
    its place; the open service still shows it at once."""
    import re as _re
    from app.main import create_app
    from test_scoring import emissions, scorer_for
    from fastapi.testclient import TestClient
    with TestClient(create_app(scorer_for(emissions(["ħ"])), auth=PageAuth())) as c:
        signed = c.get("/").text
    with TestClient(create_app(scorer_for(emissions(["ħ"])), auth=None)) as c:
        open_page = c.get("/").text
    assert _re.search(r'<button id="startBtn" hidden>', signed) and _re.search(r'<p id="splashWait" role="status">טוען', signed)
    assert '<button id="startBtn">' in open_page and '<p id="splashWait" role="status" hidden>' in open_page
    assert "__START_HIDDEN__" not in signed + open_page and "__WAIT_HIDDEN__" not in signed + open_page


TUTOR_VOICE = """
await sleep(800);
const asked=[]; const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[{tr:"כִּיף",he:"איך",new:false},{tr:"חַאלַכּ?",he:"מצבך",new:false}]}}),{status:200}));
  if(u.includes("/api/tutor/say/")){ asked.push(u.replace(location.origin,"")); return Promise.resolve(new Response(new Blob(["RIFF"],{type:"audio/wav"}),{status:200})); }
  return real(url,opts); };
const played=[]; window.Audio=function(src){ this.src=src; this.play=()=>{ played.push(src); return Promise.resolve(); }; this.pause=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:true,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000); await sleep(300);
const auto=document.querySelector(".tc .tc-auto");
out.autoShown=!auto.hidden; out.autoOn=auto.getAttribute("aria-pressed");
out.hear=!document.querySelector(".tc .tc-msg.in .tc-hear").hidden;
out.asked=[...asked]; out.played=played.length;
auto.click(); out.autoAfter=auto.getAttribute("aria-pressed"); out.kept=localStorage.getItem("ihki.tutorAutoplay");
document.querySelector(".tc .tc-msg.in .tc-hear").click(); await sleep(300);
out.askedAgain=asked.length; out.playedAgain=played.length;
closeTutorChat();
TUTOR_INFO={available:true,voice:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000);
out.silentAuto=document.querySelector(".tc .tc-auto").hidden;
const mute=document.querySelector(".tc .tc-msg.in .tc-hear"); out.silentHear=mute.textContent; mute.click(); await sleep(100);
out.why=[...document.querySelectorAll(".tc .tc-note.bad")].map(n=>n.textContent).pop()||"";
"""


# a conversation with the tutor's voice first asks how it goes (a chat or a voice dialogue): these tests talk in the chat
PICK_CHAT = """addEventListener("DOMContentLoaded",()=>new MutationObserver(()=>{
  const b=document.querySelector('.tc .tc-mode button[data-value="chat"], .tc .tc-mode button[data-value="browser"]'); if(b) b.click();
}).observe(document.body,{childList:true,subtree:true}));"""


def test_the_tutor_speaks_his_messages_once_and_the_device_keeps_the_choice(site):
    r = site("boss@x.com", TUTOR_VOICE, preload=PICK_CHAT)
    assert r["autoShown"] and r["autoOn"] == "true" and r["hear"]
    assert r["asked"] == ["/api/tutor/say/chat1/0"] and r["played"] == 1   # said on its own when it came
    assert r["autoAfter"] == "false" and r["kept"] == "false"
    assert r["askedAgain"] == 2 and r["playedAgain"] == 2   # 🔊 says it anew: no cache in יאללה נדבר
    assert r["silentAuto"] and r["silentHear"] == "🔇"   # no voice chosen: 🔇, and a tap says why
    assert "לא נבחר קול ליאללה נדבר" in r["why"]


TUTOR_HEAR = """
await sleep(800);
const sent=[]; const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/hear")){ sent.push(["hear",opts.body.get("id"),opts.body.get("audio").size]); return Promise.resolve(new Response(JSON.stringify({text:"منيح الحمد لله",tr:"מְנִיח אֵלְחַמְדֻ לִלַّה"}),{status:200})); }
  if(u.includes("/api/tutor/turn")){ sent.push(["turn",JSON.parse(opts.body).text]); return Promise.resolve(new Response(JSON.stringify({heard:"מְנִיח",reply:{ar:"تمام",tr:"תַמַאם",he:"מצוין",words:[]},fix:null,summary:null,turn:1,turns:10,done:false}),{status:200})); }
  return real(url,opts); };
let browserHeard=0; window.webkitSpeechRecognition=window.SpeechRecognition=function(){ browserHeard++; this.start=()=>{}; this.stop=()=>{}; };
navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]});
window.MediaRecorder=function(stream,o){ this.state="inactive"; this.mimeType=(o&&o.mimeType)||"audio/webm";
  this.start=()=>{ this.state="recording"; }; this.stop=()=>{ this.state="inactive"; this.ondataavailable({data:new Blob([new Uint8Array(4000)])}); this.onstop(); }; };
MediaRecorder.isTypeSupported=t=>t==="audio/webm";
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:false,stt:true,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000);
const mic=document.querySelector(".tc .tc-mic");
mic.click(); await sleep(100); out.live=document.querySelector(".tc .tc-live").textContent; out.recording=mic.classList.contains("live");
mic.click(); await until(()=>document.querySelector(".tc .tc-type input").value,5000);
out.box=document.querySelector(".tc .tc-type input").value; out.beforeSend=sent.map(s=>s[0]);
out.hint=document.querySelector(".tc .tc-live").textContent; out.arrow=mic.getAttribute("aria-label");
const box=document.querySelector(".tc .tc-type input"); box.value=box.value.replace("מְנִיח","מְנִיחַה"); box.dispatchEvent(new Event("input"));
document.querySelector(".tc .tc-type").requestSubmit(); await until(()=>sent.some(s=>s[0]==="turn"),5000);
out.hintAfter=document.querySelector(".tc .tc-live").hidden;
await until(()=>!mic.disabled,5000); mic.click(); await sleep(100); mic.click();   // again, sent as it was heard
await until(()=>box.value,5000); document.querySelector(".tc .tc-type").requestSubmit();
await until(()=>sent.filter(s=>s[0]==="turn").length===2,5000);
out.sent=sent; out.browserHeard=browserHeard;
"""


NO_BROWSER_RECOGNITION = "delete window.SpeechRecognition; delete window.webkitSpeechRecognition;"


def test_with_the_switch_on_the_tutor_hears_through_elevenlabs_not_the_browser(site):
    r = site("boss@x.com", TUTOR_HEAR, preload=NO_BROWSER_RECOGNITION)   # as on an iPhone: ElevenLabs hears
    assert r["recording"] and "מקליט" in r["live"]
    assert r["box"] == "מְנִיח אֵלְחַמְדֻ לִלַה" and r["beforeSend"] == ["hear"]   # in Hebrew letters (no shadda a box cannot draw)
    assert "תקנו" in r["hint"] and r["arrow"] == "שליחה" and r["hintAfter"]
    assert r["sent"] == [["hear", "chat1", 4000], ["turn", "מְנִיחַה אֵלְחַמְדֻ לִלַה"],   # as the student fixed it
                         ["hear", "chat1", 4000], ["turn", "منيح الحمد لله"]]   # unchanged: the recognizer's Arabic
    assert r["browserHeard"] == 0


TUTOR_TRIAL = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["RIFF"]),{status:200}));
  return real(url,opts); };
window.Audio=function(){ this.play=()=>Promise.resolve(); this.pause=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
const notes=async(info,s)=>{ TUTOR_INFO=info; openTutorChat(s); await until(()=>document.querySelector(".tc .tc-note.trial"),5000);
  const t=document.querySelector(".tc .tc-note.trial"); const r=[t.textContent,t.classList.contains("bad")]; closeTutorChat(); return r; };
const man={id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false}, woman={id:"l3",lesson:"3",title:"סיפור",who:"רובה",ar:"روبا",she:true};
out.noKey=await notes({available:true,voice:false,stt:false,voice_missing:["ELEVEN_LABS_API_KEY","ELEVEN_LABS_TUTOR_VOICE_ID","ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"]},man);
out.noVoice=await notes({available:true,voice:false,stt:false,voice_missing:["ELEVEN_LABS_TUTOR_VOICE_ID","ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"]},man);
out.noFemale=await notes({available:true,voice:true,stt:true,voice_missing:["ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"]},woman);
out.all=await notes({available:true,voice:true,stt:false,voice_missing:[]},man);
"""


# (test_the_trial_note_says_what_is_missing_and_what_hears: the trial's controls were taken out of the conversation)


TUTOR_BLOCKED = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/0")||u.includes("/say/chat1/0")) return Promise.resolve(new Response(new Blob(["RIFF"]),{status:200}));
  return real(url,opts); };
window.Audio=function(){ this.play=()=>Promise.reject(Object.assign(new Error("blocked"),{name:"NotAllowedError"})); this.pause=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:true,stt:false,voice_missing:[],turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>[...document.querySelectorAll(".tc .tc-note.bad")].some(n=>/חסם/.test(n.textContent)),5000);
out.blocked=[...document.querySelectorAll(".tc .tc-note.bad")].map(n=>n.textContent).find(t=>/חסם/.test(t));
closeTutorChat();
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat2",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(JSON.stringify({detail:"הדובר לא מושמע: הקול שנבחר לא נמצא בחשבון ElevenLabs"}),{status:503}));
  return real(url,opts); };
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>[...document.querySelectorAll(".tc .tc-note.bad")].some(n=>/לא נמצא/.test(n.textContent)),5000);
out.server=[...document.querySelectorAll(".tc .tc-note.bad")].map(n=>n.textContent).find(t=>/לא נמצא/.test(t));
"""


def test_a_tutor_that_cannot_be_heard_says_why(site):
    r = site("boss@x.com", TUTOR_BLOCKED, preload=PICK_CHAT)
    assert "לחצו 🔊" in r["blocked"]
    assert r["server"].startswith("🔇 הדובר לא מושמע: הקול שנבחר לא נמצא")


TUTOR_COMPOSE = """
await sleep(800);
const real=window.fetch, sent=[];
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/turn")){ sent.push(JSON.parse(opts.body).text); return Promise.resolve(new Response(JSON.stringify({heard:"מְנִיח",reply:{ar:"تمام",tr:"תַמַאם",he:"מצוין",words:[]},fix:null,summary:null,turn:1,turns:10,done:false}),{status:200})); }
  return real(url,opts); };
window.webkitSpeechRecognition=window.SpeechRecognition=function(){ this.start=()=>{}; this.stop=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:false,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000); await sleep(100);
const input=document.querySelector(".tc .tc-type input"), btn=document.querySelector(".tc .tc-type .tc-mic");
out.visible=!document.querySelector(".tc .tc-type").hidden&&input.offsetWidth>0;
out.focused=document.activeElement===input; out.empty=btn.getAttribute("aria-label");
out.kb=!!document.querySelector(".tc .tc-kb");
input.value="מְנִיח"; input.dispatchEvent(new Event("input")); out.typed=btn.getAttribute("aria-label");
input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true})); document.querySelector(".tc .tc-type").requestSubmit();
await until(()=>sent.length&&!btn.disabled,5000); await sleep(100);
out.sent=sent; out.after=btn.getAttribute("aria-label"); out.cleared=input.value; out.refocused=document.activeElement===input;
const foot=document.querySelector(".tc .tc-foot").getBoundingClientRect(); out.footInView=foot.bottom<=innerHeight+1;
"""


def test_the_tutor_chat_types_like_whatsapp(site):
    r = site("boss@x.com", TUTOR_COMPOSE)
    assert r["visible"] and r["focused"] and not r["kb"]
    assert r["empty"] == "לחצו ודברו בערבית" and r["typed"] == "שליחה"
    assert r["sent"] == ["מְנִיח"] and r["cleared"] == "" and r["after"] == "לחצו ודברו בערבית"
    assert r["refocused"] and r["footInView"]


TUTOR_CALL = """
await sleep(800);
const real=window.fetch, sent=[];
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.endsWith("/api/tutor/live")){ sent.push(JSON.parse(opts.body)); return Promise.resolve(new Response(JSON.stringify({signed_url:"wss://x/y",voice_id:"him",
    first_message:"كيف حالك؟",chat_id:"chat1",since:0,keywords:["مرحبا"],sdk:"/nowhere.js"}),{status:200})); }
  if(u.includes("/api/tutor/live/chat1")) return Promise.resolve(new Response(JSON.stringify({done:false,turns:[
    {heard:"מְנִיח",fix:null,reply:{ar:"تمام",tr:"תַמַאם",he:"מצוין",words:[]},summary:null,turn:1,turns:10,done:false}]}),{status:200}));
  return real(url,opts); };
let session=null, ended=0;
window.ElevenLabsClient={Conversation:{startSession:async o=>{ session=o; return {endSession(){ ended++; }}; }}};
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:false,stt:false,live:true,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000);
const btn=document.querySelector(".tc .tc-call"); out.shown=!btn.hidden;
btn.click(); await until(()=>session&&btn.textContent==="📵",5000);
out.sent=sent; out.opts={signedUrl:session.signedUrl,first:session.overrides.agent.firstMessage,lang:session.overrides.agent.language,
  voice:session.overrides.tts.voiceId,keywords:session.overrides.asr.keywords,extra:session.customLlmExtraBody,vars:session.dynamicVariables};
out.locked=document.querySelector(".tc .tc-type .tc-mic").disabled; out.live=document.querySelector(".tc .tc-live").textContent;
session.onMessage({role:"agent",source:"ai",message:"تمام"});
await until(()=>document.querySelectorAll(".tc .tc-msg.in").length===2,5000);
out.heard=document.querySelector(".tc .tc-msg.out .heard").textContent;
out.count=document.querySelector(".tc .tc-count").textContent;
btn.click(); await sleep(200);
out.ended=ended; out.after=btn.textContent; out.unlocked=!document.querySelector(".tc .tc-type .tc-mic").disabled;
"""


def test_the_tutor_live_call_shows_each_turn_and_hangs_up(site):
    r = site("boss@x.com", TUTOR_CALL)
    assert r["shown"] and r["sent"] == [{"id": "chat1"}]
    assert r["opts"] == {"signedUrl": "wss://x/y", "first": "كيف حالك؟", "lang": "ar", "voice": "him",
                         "keywords": ["مرحبا"], "extra": {"chat_id": "chat1"}, "vars": {"chat_id": "chat1"}}
    assert r["locked"] and "בשיחה" in r["live"]
    assert r["heard"] == "מְנִיח" and "9" in r["count"]
    assert r["ended"] == 1 and r["after"] == "📞" and r["unlocked"]


REFRESH_REFUSED = """
await sleep(800);
const real=window.fetch; let outs=0;
window.fetch=(url,opts={})=>String(url).includes("/api/probe")?Promise.resolve(new Response("{}",{status:401})):real(url,opts);
sb.auth.signOut=async()=>{ outs++; return {}; };
sb.auth.refreshSession=async()=>({data:{session:null},error:{name:"AuthRetryableFetchError",status:0}});
await api("/api/probe"); out.afterNetwork=outs;
sb.auth.refreshSession=async()=>({data:{session:null},error:{name:"AuthApiError",status:400,code:"refresh_token_not_found"}});
const r=await api("/api/probe"); out.afterRefused=outs; out.status=r.status;
"""


def test_a_refused_refresh_signs_out_on_this_device_and_a_network_hiccup_does_not(site):
    r = site("dana@x.com", REFRESH_REFUSED)
    assert r["afterNetwork"] == 0 and r["afterRefused"] == 1 and r["status"] == 401


VOICE_PICKER = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>String(url).includes("/api/admin/eleven-voices")?Promise.resolve(new Response(JSON.stringify({
  current:"pal00001",currents:{ELEVEN_LABS_VOICE_ID:"pal00001",ELEVEN_LABS_TUTOR_VOICE_ID:"pal00001",ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID:"pal00002"},
  mine:[{voice_id:"adam0001",name:"Adam",gender:"male",rank:3,language:"en",accent:"american"},
        {voice_id:"copy0001",name:"Salma copy",gender:"female",rank:0,original_id:"pal00009"}],
  library:[{voice_id:"pal00009",owner_id:"o9",name:"Salma original",gender:"female",rank:0},{voice_id:"pal00001",owner_id:"o1",name:"Yousef",gender:"male",rank:0},{voice_id:"pal00002",owner_id:"o2",name:"Salma",gender:"female",rank:0},
                   {voice_id:"lev00001",owner_id:"o3",name:"Rami",gender:"male",rank:1},{voice_id:"egy00001",owner_id:"o4",name:"Omar",gender:"male",rank:3,accent:"egyptian"}]}),{status:200})):real(url,opts);
const row=document.createElement("div"); document.body.append(row);
await elevenVoicePicker(row,()=>{},"ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID","tutor-female");
const items=[...row.querySelectorAll(".eleven-voice-list li")];
out.names=items.map(li=>li.querySelector("b").textContent);
out.inUse=items.filter(li=>li.querySelector(".pill")).map(li=>li.querySelector("b").textContent);
out.disabled=items.filter(li=>li.querySelector(".btn.primary").disabled).map(li=>li.querySelector("b").textContent);
const [g,a]=row.querySelectorAll(".eleven-voice-filters select"); out.accent=a.value; out.accents=[...a.options].map(o=>o.textContent);
out.heads=[...row.querySelectorAll(".eleven-voice-accent")].map(h=>h.textContent);
a.value="פלסטיני"; a.onchange(); out.palestinian=[...row.querySelectorAll(".eleven-voice-list li b")].map(b=>b.textContent);
a.value=""; a.onchange();
g.value="male"; g.onchange(); out.men=[...row.querySelectorAll(".eleven-voice-list li b")].map(b=>b.textContent);
"""


def test_the_tutors_voice_picker_offers_every_palestinian_voice_for_any_part(site):
    r = site("boss@x.com", VOICE_PICKER)
    # every Arabic voice (not the account's English one), each once (the library's copy in the account stands for it),
    # Palestinian first and the part's gender first in each accent
    assert r["names"] == ["Salma copy", "Salma", "Yousef", "Rami", "Omar"]
    assert r["inUse"] == ["Salma"] and r["disabled"] == ["Salma"]   # this part's voice, not playback's
    assert r["accent"] == "" and r["accents"] == ["כל המבטאים", "פלסטיני", "לבנטיני", "מצרי"]
    assert r["heads"] == ["פלסטיני (1)", "פלסטיני (2)", "לבנטיני (1)", "מצרי (1)"]   # the account's, then the library's
    assert r["palestinian"] == ["Salma copy", "Salma", "Yousef"]
    assert r["men"] == ["Yousef", "Rami", "Omar"]


MONITOR_VOICES = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>String(url).includes("/api/eleven-voices-in-use")?Promise.resolve(new Response(JSON.stringify({key:true,voices:[
  {setting:"ELEVEN_LABS_VOICE_ID",purpose:"playback",voice_id:"",name:"כבוי"},
  {setting:"ELEVEN_LABS_TUTOR_VOICE_ID",purpose:"tutor-male",voice_id:"added001",name:"Yousef"},
  {setting:"ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID",purpose:"tutor-female",voice_id:"",name:""}]}),{status:200}))
  :String(url).includes("/api/admin/stt-compare")?Promise.resolve(new Response(JSON.stringify({total:4,chosen:{eleven:3,browser:1,same:0,neither:0},
    by_engine:{"Android Chrome":{eleven:3,browser:1,same:0,neither:0}},by_model:{scribe_v3:{eleven:3,browser:1,same:0,neither:0}},latest:[]}),{status:200})):real(url,opts);
TUTOR_USAGE={total:{cost:0.1,conversations:1,turns:2},daily:[],models:[],model_now:"claude-haiku-5-5",note:""};
const box=document.createElement("div"); document.body.append(box); drawTutorUsage(box);
await until(()=>box.querySelector(".tutor-voices li")&&/משפטים ב־30/.test(box.textContent),5000);
out.compare=box.textContent.match(/4 משפטים[^·]*·[^·]*/)?.[0]||"";
const v=box.querySelector(".tutor-usage .tutor-voices"); out.lines=[...v.querySelectorAll("li")].map(li=>li.textContent);
out.title=v.querySelector("b[title]")?.title; out.after=!!v.previousElementSibling&&/המודל עכשיו/.test(v.previousElementSibling.textContent);
const mon=document.createElement("div"); document.body.append(mon); MONITOR={live:{},tts:{},build:{},errors:{},supabase:null};
drawMonitorSummary(mon); out.inSnapshot=/קולות ElevenLabs|ElevenLabs/.test(mon.textContent);
"""


def test_the_tutors_card_shows_the_voice_of_each_setting(site):
    r = site("boss@x.com", MONITOR_VOICES)
    assert r["lines"] == ["דמות גבר: Yousef", "דמות אישה: לא נבחר", "השמעה בחוברת (גיבוי): כבוי"]
    assert r["title"] == "added001" and r["after"] and not r["inSnapshot"]   # under the model, not in the snapshot
    assert "ElevenLabs 3 (75%)" in r["compare"]   # the trial's comparison of speech recognition


KEYS_CARD_VOICES = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.endsWith("/api/admin/keys")) return Promise.resolve(new Response(JSON.stringify({writable:true,keys:[
    {name:"ELEVEN_LABS_VOICE_ID",label:"ElevenLabs · הקול",source:"db",value:"off",voice_picker:"playback"},
    {name:"ELEVEN_LABS_TUTOR_VOICE_ID",label:"דמות גבר",source:"db",value:"NeokBGcPJaElDOj380Qt",voice_picker:"tutor-male"},
    {name:"ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID",label:"דמות אישה",source:"none",value:"",voice_picker:"tutor-female"}]}),{status:200}));
  if(u.includes("/api/eleven-voices-in-use")) return Promise.resolve(new Response(JSON.stringify({key:true,voices:[
    {setting:"ELEVEN_LABS_TUTOR_VOICE_ID",purpose:"tutor-male",voice_id:"NeokBGcPJaElDOj380Qt",name:"Bader"}]}),{status:200}));
  return real(url,opts); };
ME_PERMISSIONS=new Set(["*"]); API_KEYS=null;
const box=document.createElement("div"); document.body.append(box); drawApiKeys(box);
await until(()=>/Bader/.test(box.textContent),5000);
const rows=[...box.querySelectorAll(".key-row")];
out.values=rows.map(r=>r.querySelector("form input")?.value??null);
out.male=rows[1].querySelector("small").textContent;
"""


def test_a_chosen_voice_stays_in_its_field_with_its_name(site):
    r = site("boss@x.com", KEYS_CARD_VOICES)
    assert r["values"] == ["", "NeokBGcPJaElDOj380Qt", ""]   # the voice in use; not "off", and nothing when none is chosen
    assert "בשימוש: Bader (NeokBGcPJaElDOj380Qt)" in r["male"]


TUTOR_ONE_VOICE = """
await sleep(800);
const real=window.fetch; let says=0;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/")){ says++; return new Promise(r=>setTimeout(()=>r(new Response(new Blob(["RIFF"]),{status:200})),400)); }   // slow
  return real(url,opts); };
let playing=0, most=0;
window.Audio=function(){ this.play=()=>{ playing++; most=Math.max(most,playing); return Promise.resolve(); }; this.pause=()=>{ playing--; }; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in .tc-hear"),5000);
const hear=document.querySelector(".tc .tc-msg.in .tc-hear");
hear.click(); hear.click();   // tapped while autoplay is still loading it
await sleep(1200);
out.says=says; out.most=most; out.playing=playing;
"""


def test_a_tutor_message_never_plays_twice_at_once(site):
    r = site("boss@x.com", TUTOR_ONE_VOICE, preload=PICK_CHAT)
    assert r["says"] == 1 and r["most"] == 1 and r["playing"] == 1   # fetched once, one sound at a time (no echo)


TUTOR_HYBRID = """
await sleep(800);
const real=window.fetch, sent=[];
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/hear")){ sent.push("hear"); return Promise.resolve(new Response(JSON.stringify({text:"منيح",tr:"מְנִיח"}),{status:200})); }
  if(u.includes("/api/tutor/letters")) return Promise.resolve(new Response(JSON.stringify({tr:"מְנִיח"}),{status:200}));
  return real(url,opts); };
navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]});
window.MediaRecorder=function(stream,o){ this.state="inactive"; this.mimeType="audio/webm";
  this.start=()=>{ this.state="recording"; }; this.stop=()=>{ this.state="inactive"; this.ondataavailable({data:new Blob([new Uint8Array(4000)])}); this.onstop(); }; };
MediaRecorder.isTypeSupported=t=>t==="audio/webm";
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:false,stt:true,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-msg.in"),5000);
out.note=document.querySelector(".tc .tc-note.trial")?.textContent||"";
const mic=document.querySelector(".tc .tc-mic");
mic.click(); await sleep(50); out.first=window.__browser.length; out.sentFirst=sent.length;
window.__browser.at(-1).fail("network"); await sleep(100);   // the browser cannot hear here
out.switched=document.querySelector(".tc").textContent.includes("עוברים לזיהוי של ElevenLabs");
mic.click(); await sleep(100); mic.click(); await until(()=>sent.length,5000);
out.sent=sent; out.browserAfter=window.__browser.length;
"""

FAKE_BROWSER_RECOGNITION = """addEventListener("DOMContentLoaded",()=>new MutationObserver(()=>{   // the wizard: the choice it marks
  const b=document.querySelector(".tc .tc-mode button.primary"); if(b&&!window.__wizardOwn) b.click();
}).observe(document.body,{childList:true,subtree:true})); window.__browser=[]; window.webkitSpeechRecognition=window.SpeechRecognition=function(){
  window.__browser.push(this); this.start=()=>{ setTimeout(()=>this.onstart&&this.onstart(),0); }; this.stop=()=>{}; this.abort=()=>{};
  this.fail=e=>{ this.onerror&&this.onerror({error:e}); this.onend&&this.onend(); }; };"""


def test_the_browser_hears_where_it_can_and_elevenlabs_where_it_cannot(site):
    r = site("boss@x.com", TUTOR_HYBRID, preload=FAKE_BROWSER_RECOGNITION+'localStorage.setItem("ihki.tutorHears",JSON.stringify("browser"));')
    assert r["first"] == 1 and r["sentFirst"] == 0   # Chrome: its own recognition, nothing sent to ElevenLabs
    assert r["switched"] and r["sent"] == ["hear"] and r["browserAfter"] == 1   # it failed: ElevenLabs from then on


TUTOR_CHOOSE = TUTOR_HYBRID.split('out.note=')[0] + """
const note=()=>document.querySelector(".tc .tc-note.trial"), pick=()=>note().querySelector("select.tc-engine");
out.before=[pick().value,[...pick().options].map(o=>o.textContent)];
pick().value="eleven"; pick().onchange();
out.chosen=[note().textContent.includes("זיהוי הדיבור: ElevenLabs"),localStorage.getItem("ihki.tutorHears")];
const mic=document.querySelector(".tc .tc-mic");
mic.click(); await sleep(100); mic.click(); await until(()=>sent.length,5000);
out.sent=sent.slice(); out.browser=window.__browser.length;
pick().value="browser"; pick().onchange(); out.back=localStorage.getItem("ihki.tutorHears");
const box=document.querySelector(".tc .tc-type input"); box.value=""; box.dispatchEvent(new Event("input"));   // the heard sentence cleared
mic.click(); await sleep(50); out.browserAfter=window.__browser.length;
"""


# (test_the_student_chooses_who_hears_and_the_device_keeps_it: the trial's controls were taken out of the conversation)



MANAGE_FAILS = """
await sleep(800); showManage(); await sleep(500);
out.wrongKind=store.get("ihki.speed",1);   // "fast" was kept where a number belongs: the default instead
const orig=CLASS; CLASS={get classes(){ throw new Error("a kept value no screen knows"); }};
drawManage(); await sleep(100);   // a screen that fails to draw
const card=document.querySelector("#manage .manage-failed");
out.card=card&&card.textContent; out.buttons=card&&[...card.querySelectorAll("button")].map(b=>b.textContent);
CLASS=orig;
out.resetsClass=UI_KEYS.includes("ihki.class"); out.resetsProgress=UI_KEYS.some(k=>/best|learned|written|practiceDays|words/.test(k));
"""


def test_a_screen_of_manage_that_fails_says_so_and_offers_the_reset(site):
    r = site("boss@x.com", MANAGE_FAILS, preload='localStorage.setItem("ihki.speed",JSON.stringify("fast"));')
    assert r["wrongKind"] == 1
    assert "המסך לא נטען" in r["card"] and "a kept value no screen knows" in r["card"]
    assert r["buttons"] == ["איפוס ההגדרות השמורות", "נסו שוב"]
    assert r["resetsClass"] and not r["resetsProgress"]   # the reset leaves the sign-in and a learner's progress alone


MANAGE_RETRY = """
await sleep(800);
const real=window.fetch; let tries=0;
window.fetch=(url,opts={})=>{ if(String(url).endsWith("/api/class")){ tries++; return tries===1?Promise.reject(new TypeError("Failed to fetch"))
  :Promise.resolve(new Response(JSON.stringify({detail:"Supabase איטי"}),{status:503})); } return real(url,opts); };
showManage(); await until(()=>/השרת לא זמין כרגע/.test(document.getElementById("mState").textContent),8000);
const state=document.getElementById("mState"); out.failed=state.textContent; out.button=!!state.querySelector("button");
state.querySelector("button").click();
await until(()=>/Supabase איטי/.test(state.textContent),8000); out.tries=tries; out.after=state.textContent; out.again=!!state.querySelector("button");
"""


def test_a_failed_load_of_manage_offers_to_try_again(site):
    r = site("boss@x.com", MANAGE_RETRY)
    assert "השרת לא זמין כרגע" in r["failed"] and r["button"]
    assert r["tries"] == 2 and "לא זמינים כרגע (Supabase איטי)" in r["after"] and r["again"]   # asked again, and says why


FEEDBACK_OFFICE = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>String(url).endsWith("/api/feedback")?Promise.resolve(new Response(JSON.stringify({tutor_setup:"alter table public.feedback add constraint x check (kind in ('tutor')); notify pgrst, 'reload schema';",feedback:[
  {id:1,kind:"problem",status:"new",email:"dana@x.com",screen:"login",item:"Android",message:"לא מצליחה להיכנס",created_at:"2026-11-23T05:00:00Z",replies:[{from:"team",by:"boss",text:"בודקים"}]},
  {id:2,kind:"idea",status:"read",email:"rami@x.com",screen:"",item:"",message:"רעיון",created_at:"2026-11-22T05:00:00Z"}]}),{status:200})):real(url,opts);
let mailed=""; openMail=u=>{ mailed=u; };
showManage(); await sleep(300); document.querySelector('#mModes [data-mode="feedback"]').click();
await until(()=>document.querySelector(".fb-board .fb-item"),8000);
const cols=[...document.querySelectorAll(".fb-board .fb-col")].map(c=>c.getBoundingClientRect());
out.stacked=cols.length===3&&cols[1].top>=cols[0].bottom-1&&cols.every(r=>r.width>innerWidth*0.7);
out.noOverflow=document.documentElement.scrollWidth<=innerWidth;
const sql=document.querySelector("#mFeedback .sql-block code"); out.sql=sql&&[sql.dir,getComputedStyle(sql).direction,!!sql.parentElement.querySelector("button")];
const card=document.querySelector('.fb-board .fb-item[data-id="1"]'); const btn=[...card.querySelectorAll(".fb-actions button")].find(b=>b.textContent.includes("למשרד"));
const exit=document.getElementById("logoutBtn"), acts=card.querySelector(".fb-actions").getBoundingClientRect(), ex=exit.getBoundingClientRect();
out.exitFloats=getComputedStyle(exit).position==="fixed"&&!(ex.bottom<acts.top||ex.top>acts.bottom||ex.right<acts.left||ex.left>acts.right);
btn.click(); out.mailed=decodeURIComponent(mailed); out.inPage=document.body.innerText.includes(["office","ahalawasahala"].join("@"))||[...document.querySelectorAll("a[href]")].some(a=>a.href.includes("ahalawasahala"));
"""


def test_feedback_stacks_on_a_phone_and_a_note_goes_to_the_office(site):
    r = site("boss@x.com", FEEDBACK_OFFICE, mobile=True)
    assert r["stacked"] and r["noOverflow"]   # the columns one under another
    assert not r["exitFloats"]   # "יציאה" is at the page's end in ניהול, not over a card's buttons
    assert r["sql"] == ["ltr", "ltr", True]   # the SQL to run reads left to right, with its copy button
    assert r["mailed"].startswith("mailto:office@ahalawasahala.com?subject=")
    assert "בעיית כניסה" in r["mailed"] and "dana@x.com" in r["mailed"] and "לא מצליחה להיכנס" in r["mailed"] and "בודקים" in r["mailed"]
    assert not r["inPage"]   # the address is not on the page


TUTOR_COMPARE = TUTOR_HYBRID.split('out.note=')[0].replace('return Promise.resolve(new Response(JSON.stringify({text:"منيح",tr:"מְנִיח"}),{status:200}));',
  'return Promise.resolve(new Response(JSON.stringify({text:"منيح الحمد لله",tr:"מְנִיח אֵלְחַמְדֻ לִלַّה",model:"scribe_v3",language:"ajp",hints:57}),{status:200}));') + """
const choices=[]; const real2=window.fetch;
window.fetch=(url,opts={})=>{ if(String(url).includes("/api/tutor/stt-choice")){ choices.push(JSON.parse(opts.body)); return Promise.resolve(new Response('{"ok":true}',{status:200})); }
  if(String(url).includes("/api/tutor/letters")) return Promise.resolve(new Response(JSON.stringify({tr:"מְנִיח"}),{status:200})); return real2(url,opts); };
const pickBoth=[...document.querySelectorAll(".tc .tc-note.trial select")].find(x=>x.getAttribute("aria-label")==="מי מזהה את הדיבור");
pickBoth.value="both"; pickBoth.onchange();   // the comparison: the trial note's choice (the wizard offers the two alone)
out.note=document.querySelector(".tc .tc-note.trial").textContent;
const mic=document.querySelector(".tc .tc-mic");
mic.click(); await sleep(100);
out.both=window.__browser.length===1;   // the browser listens beside the recording
const b=window.__browser[0]; b.onresult({results:[[{transcript:"منيح"}]]});
mic.click(); b.onend();
await until(()=>document.querySelector(".tc .tc-cmp"),5000);
const picks=[...document.querySelectorAll(".tc .tc-cmp button")]; out.picks=picks.map(p=>p.textContent);
picks[1].click(); await until(()=>choices.length,5000);
out.choice=choices[0]; out.box=document.querySelector(".tc .tc-type input").value; out.gone=!document.querySelector(".tc .tc-cmp");
"""


# (test_the_trial_compares_both_recognizers_and_keeps_the_students_choice: the trial's controls were taken out of the conversation)


ACCESS_MANY = """
await sleep(800);
const users=[...Array(120)].map((_,i)=>({id:"u"+i,email:`s${i}@x.com`,name:"",tier:"student",opens:{levels:["1"],overrides:{}},fixed:[]}));
const real=window.fetch;
window.fetch=(url,opts={})=>String(url).includes("/api/access/users")&&!opts.method?Promise.resolve(new Response(JSON.stringify({users,me:"boss"}),{status:200})):real(url,opts);
showManage(); await sleep(300); document.querySelector('#mModes [data-mode="access"]').click();
await until(()=>document.querySelector("#mAccess .access-group tbody tr"),8000);
const rowsNow=()=>document.querySelectorAll("#mAccess tbody tr").length, more=()=>[...document.querySelectorAll("#mAccess .access-group > button")].find(b=>!b.hidden);
out.first=rowsNow(); out.moreText=more()&&more().textContent;
const sc=document.querySelector("#mAccess .access-scroll"); out.fits=document.documentElement.scrollWidth<=innerWidth&&sc.scrollWidth>sc.clientWidth;   // the table scrolls in its box
more().click(); out.second=rowsNow();
const find=document.querySelector("#mAccess .access-find"); find.value="s117@"; find.oninput();
out.found=[...document.querySelectorAll("#mAccess tbody tr")].map(r=>r.querySelector("small").textContent);
find.value=""; find.oninput(); out.cleared=rowsNow();
more().click(); redrawAccess(); out.kept=rowsNow();
"""


def test_access_draws_its_rows_a_page_at_a_time_and_searches_them_all(site):
    r = site("boss@x.com", ACCESS_MANY, mobile=True)
    assert r["first"] == 40 and "40 מתוך 80" in r["moreText"]
    assert r["fits"]   # the wide table scrolls in its own box: the page stays the phone's width (no dark, empty screen)
    assert r["second"] == 80 and r["found"] == ["s117@x.com"]   # an account not drawn yet is found
    assert r["cleared"] == 40 and r["kept"] == 80   # a redraw keeps the rows shown


ARABIC_MARKS = """
await sleep(500);
const el=document.createElement("p"); document.body.append(el);
setTr(el,"ע\\u064eל\\u064eי\\u0652כ\\u064fם ס\\u05b7\\u0651לאם");
out.text=el.textContent; out.sh=!!el.querySelector(".sh");
"""


def test_arabic_vowel_signs_on_hebrew_letters_are_drawn_as_hebrew_points(site):
    r = site("boss@x.com", ARABIC_MARKS)
    assert r["text"] == "עַלַיְכֻם סַלאם" and r["sh"]   # no Arabic sign left (no box); the shadda drawn by its span


TUTOR_SAMPLE = """
await sleep(800);
const real=window.fetch; let askedShe=null;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/voice-sample")){ askedShe=u.includes("she=true"); return Promise.resolve(new Response(JSON.stringify({voice_id:"v1",name:"Bader",preview_url:"https://x/bader.mp3"}),{status:200})); }
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["ID3"]),{status:200}));
  return real(url,opts); };
const played=[]; window.Audio=function(src){ this.src=src; this.play=()=>{ played.push(src); return Promise.resolve(); }; this.pause=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m")); localStorage.setItem("ihki.tutorAutoplay",JSON.stringify(false));
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l3",lesson:"3",title:"השכנה",who:"רובה",ar:"روبا",she:true});
await until(()=>document.querySelector(".tc .tc-note.trial"),5000);
const b=[...document.querySelectorAll(".tc .tc-note.trial button")].find(x=>x.textContent.includes("דוגמית"));
out.button=!!b; b.click(); await until(()=>played.length,5000);
out.played=played.slice(); out.she=askedShe;
const says=[]; const real2=window.fetch; window.fetch=(url,opts={})=>{ if(String(url).includes("/api/tutor/say/")) says.push(String(url)); return real2(url,opts); };
const sel=[...document.querySelectorAll(".tc .tc-note.trial select")].find(x=>x.getAttribute("aria-label")==="מודל הקול");
out.models=[...sel.options].map(o=>o.value); sel.value="eleven_v4"; sel.onchange();
await until(()=>says.length,5000); out.say=says[0]; out.kept=localStorage.getItem("ihki.tutorVoiceModel");
"""


# (test_the_tutor_plays_the_chosen_voices_own_sample: the trial's controls were taken out of the conversation)


TUTOR_DIALOG = """
await sleep(800);
const real=window.fetch, sent=[];
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["ID3"]),{status:200}));
  if(u.includes("/api/tutor/turn")){ sent.push(JSON.parse(opts.body).text); const last=sent.length>=2;
    return Promise.resolve(new Response(JSON.stringify({heard:"מְנִיח",reply:{ar:"تمام",tr:"תַמַאם",he:"מצוין",words:[]},fix:null,
      summary:last?"כל הכבוד":null,turn:sent.length,turns:10,done:last}),{status:200})); }
  if(u.includes("/api/tutor/letters")) return Promise.resolve(new Response(JSON.stringify({tr:"מְנִיח"}),{status:200}));
  return real(url,opts); };
const played=[]; window.Audio=function(){ this.play=()=>{ played.push(1); setTimeout(()=>this.onended&&this.onended(),50); return Promise.resolve(); }; this.pause=()=>{}; };
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-mode"),5000);
out.modes=[...document.querySelectorAll(".tc .tc-mode button")].map(b=>b.textContent);
[...document.querySelectorAll(".tc .tc-mode button")].find(b=>b.textContent.includes("דו־שיח")).click();
await until(()=>window.__browser.length===1,5000);   // the opening was said, and the student's turn began
out.box=document.querySelector(".tc .tc-type").hidden; out.state=document.querySelector(".tc .tc-dialog-state").textContent;
const heard=()=>document.querySelector(".tc .tc-dialog-heard").textContent, dlg=document.querySelector(".tc .tc-dialog");
out.waiting=heard(); window.__browser[0].onspeechstart(); out.hearing=[heard(),dlg.classList.contains("hearing")];
window.__browser[0].onresult({results:[[{transcript:"منيح"}]]}); await until(()=>heard()==="מְנִיח",5000);   // the words so far
out.words=heard(); window.__browser[0].onend();   // the student spoke
await until(()=>window.__browser.length===2,5000);   // the answer was said, and it listens again
window.__browser[1].onresult({results:[[{transcript:"شكرا"}]]}); window.__browser[1].onend();
await until(()=>/הסתיימה/.test(document.querySelector(".tc .tc-dialog-state").textContent),5000);
out.sent=sent; out.played=played.length; out.listened=window.__browser.length; out.boxAtEnd=document.querySelector(".tc .tc-type input").value;
"""


def test_the_voice_dialogue_says_listens_and_answers_without_the_text_box(site):
    r = site("boss@x.com", TUTOR_DIALOG, preload="window.__wizardOwn=true;"+FAKE_BROWSER_RECOGNITION)
    assert r["modes"][0].startswith("💬") and "דו־שיח" in r["modes"][1]   # the last choice first (none yet: the chat)
    assert r["box"] and "תורכם" in r["state"]   # no text box; the student's turn
    assert r["waiting"] == "מחכה שתדברו…" and r["hearing"] == ["שומע אתכם…", True]   # it shows that it hears
    assert r["words"] == "מְנִיח"   # and what it heard so far, in Hebrew letters (not Arabic)
    assert r["sent"] == ["منيح", "شكرا"] and r["boxAtEnd"] == ""   # what was heard went straight to the tutor
    assert r["played"] == 3 and r["listened"] == 2   # the opening and both answers said; no listening after the end


TUTOR_WIZARD = """
await sleep(800);
const real=window.fetch; let started=0;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")){ started++; return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200})); }
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["ID3"]),{status:200}));
  return real(url,opts); };
window.Audio=function(){ this.play=()=>Promise.resolve(); this.pause=()=>{}; };
navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]});
window.MediaRecorder=function(){ this.state="inactive"; this.start=()=>{ this.state="recording"; }; this.stop=()=>{}; }; MediaRecorder.isTypeSupported=()=>true;
localStorage.setItem("ihki.tutorGender",JSON.stringify("m")); localStorage.setItem("ihki.tutorAutoplay",JSON.stringify(false));
TUTOR_INFO={available:true,voice:true,stt:true,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
const step=()=>document.querySelector(".tc .tc-mode");
await until(()=>step(),5000);
out.one=[step().querySelector(".tc-step").textContent,step().querySelector("p").textContent,[...step().querySelectorAll("button")].map(b=>b.dataset.value)];
out.startedEarly=started;
step().querySelector('button[data-value="chat"]').click();
await until(()=>step()&&step().dataset.step==="hears",5000);
out.two=[step().querySelector(".tc-step").textContent,step().querySelector("p").textContent,[...step().querySelectorAll("button")].map(b=>b.dataset.value)];
step().querySelector('button[data-value="eleven"]').click();
await until(()=>started,5000);
out.kept=[localStorage.getItem("ihki.tutorMode"),localStorage.getItem("ihki.tutorHears")];
out.note=(document.querySelector(".tc .tc-note.trial")||{}).textContent||"";
"""


def test_the_conversation_starts_with_a_wizard_how_it_goes_and_who_hears(site):
    r = site("boss@x.com", TUTOR_WIZARD, preload="window.__wizardOwn=true;"+FAKE_BROWSER_RECOGNITION)
    assert r["one"] == ["שלב 1 מתוך 2", "איך מדברים הפעם?", ["chat", "dialog"]] and r["startedEarly"] == 0
    assert r["two"] == ["שלב 2 מתוך 2", "מי מזהה את הדיבור שלכם?", ["browser", "eleven"]]   # the device first by default
    assert r["kept"] == ['"chat"', '"eleven"']   # the conversation, as chosen


TUTOR_DIALOG_STUCK = """
await sleep(800);
const real=window.fetch, sent=[];
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/start")) return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"كيف حالك؟",tr:"כִּיף חַאלַכּ?",he:"מה שלומך?",words:[]}}),{status:200}));
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["ID3"]),{status:200}));
  return real(url,opts); };
let blockOnce=true, plays=0;
window.Audio=function(){ this.play=()=>{ plays++; if(blockOnce){ blockOnce=false; const e=new Error("blocked"); e.name="NotAllowedError"; return Promise.reject(e); }
  setTimeout(()=>this.onended&&this.onended(),30); return Promise.resolve(); }; this.pause=()=>{}; };
window.__silent=[]; window.webkitSpeechRecognition=window.SpeechRecognition;   // (kept by the page as SR)
localStorage.setItem("ihki.tutorGender",JSON.stringify("m"));
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
await until(()=>document.querySelector(".tc .tc-mode"),5000);
document.querySelector('.tc .tc-mode button[data-value="dialog"]').click();
await until(()=>/חסם/.test(document.querySelector(".tc").textContent),5000);
const state=()=>document.querySelector(".tc .tc-dialog-state").textContent, btn=()=>document.querySelector(".tc .tc-dialog button");
out.blocked=state(); out.button=btn().getAttribute("aria-label");
window.__noStart=true;   // the device's recognition will not start (as Safari without a tap)
btn().click();   // ▶ המשך: says the held-back message again, then listens
await until(()=>/לא התחיל להקשיב/.test(document.querySelector(".tc").textContent),8000);
out.plays=plays; out.notStarted=state();
window.__noStart=false; btn().click(); await until(()=>window.__browser.length>=2&&/תורכם/.test(state()),5000);
out.resumed=state();
"""


def test_the_voice_dialogue_says_why_it_stopped_and_goes_on_from_play(site):
    stall = FAKE_BROWSER_RECOGNITION.replace("this.start=()=>{ setTimeout(()=>this.onstart&&this.onstart(),0); };",
                                             "this.start=()=>{ if(!window.__noStart) setTimeout(()=>this.onstart&&this.onstart(),0); };")
    r = site("boss@x.com", TUTOR_DIALOG_STUCK, preload="window.__wizardOwn=true;" + stall)
    assert "עצור" in r["blocked"] and r["button"] == "▶ המשך"   # the voice was blocked: it says so, and waits
    assert r["plays"] == 2 and "עצור" in r["notStarted"]   # ▶ said it again; then listening did not start: said, and waits
    assert "תורכם" in r["resumed"]   # ▶ again: it listens


TUTOR_ALL_SCENARIOS = """
await sleep(800);
const [one,two]=LESSONS.map(l=>l.id);
TUTOR_INFO={available:true,scenarios:[{id:"a",lesson:one},{id:"b",lesson:two},{id:"c",lesson:"no-such-lesson"}]};
const ids=()=>tutorScenarios().map(s=>s.id).join(",");
FREE=false; ME_TEACHES=false; ME_PERMISSIONS=new Set(); for(const k in best) delete best[k];
out.student=ids();
ME_TEACHES=true; out.teacher=ids(); ME_TEACHES=false;
ME_PERMISSIONS=new Set(["tutor.all_scenarios"]); out.granted=ids();
"""


def test_a_class_teacher_or_an_account_given_it_has_every_tutor_scenario(site):
    r = site("dana@x.com", TUTOR_ALL_SCENARIOS)
    assert r["student"] == ""   # no lesson begun: no scenario yet
    assert r["teacher"] == "a,b" and r["granted"] == "a,b"   # every scenario of their lessons


MORNING = """
await sleep(800);
const sent=[]; const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/morning")) return Promise.resolve(new Response(JSON.stringify({lesson:"2",who:"ח'ליל",ar:"خليل",she:false,knows:true,done:false,
    weak:[{tr:"אַנַא בַּדִּי",he:"אני רוצה"}],fixes:[{tr:"תַלַאתִין",note:"בניב אומרים תַלַאתִין"}],new:[{tr:"פֻסְטַאן",he:"שמלה"}]}),{status:200}));
  if(u.includes("/api/tutor/start")){ sent.push(JSON.parse(opts.body)); return Promise.resolve(new Response(JSON.stringify({id:"chat1",turns:10,left:null,
    message:{ar:"صباح الخير",tr:"צַבַּאח אֵלְח'יר",he:"בוקר טוב",words:[]}}),{status:200})); }
  return real(url,opts); };
TUTOR_INFO={available:true,voice:false,stt:false,turns:10,left:null,morning:true,scenarios:[]};
ME_PERMISSIONS=new Set([...ME_PERMISSIONS,"tutor.use","tutor.morning"]);
const page=document.createElement("div"); page.className="focus-home"; document.body.append(page);
await drawMorningCard(page,"2");
const card=page.querySelector(".morning-card");
out.title=card.querySelector(".mc-title").textContent; out.chips=[...card.querySelectorAll(".mc-chip")].map(c=>c.textContent);
out.arabic=/[\\u0600-\\u06FF]/.test(card.textContent);
localStorage.setItem("ihki.tutorGender",JSON.stringify("f"));
card.querySelector(".mc-go").click();
await until(()=>sent.length&&document.querySelector(".tc .tc-msg.in"),5000);
out.sent=sent[0]; out.plan=[...document.querySelectorAll(".tc .tc-note")].map(n=>n.textContent).find(t=>t.includes("היום נתרגל"))||"";
"""


def test_the_morning_talk_card_opens_a_talk_built_for_the_student(site):
    r = site("boss@x.com", MORNING, preload=PICK_CHAT)
    assert r["title"] == "ח'ליל מחכה לך" and not r["arabic"]   # Hebrew letters only
    assert r["chips"] == ["חוזרים: אַנַא בַּדִּי", "תיקון משיחה קודמת", "מילה חדשה אחת"]
    assert r["sent"]["scenario"] == "morning" and r["sent"]["lesson"] == "2"
    assert "אַנַא בַּדִּי" in r["plan"] and "פֻסְטַאן" in r["plan"]


MORNING_LIST = """
await sleep(800);
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/morning")) return Promise.resolve(new Response(JSON.stringify({lesson:"2",who:"ח'ליל",ar:"خليل",she:false,knows:false,done:false,weak:[],fixes:[],new:[{tr:"פֻסְטַאן",he:"שמלה"}]}),{status:200}));
  return real(url,opts); };
TUTOR_INFO={available:true,voice:false,stt:false,turns:10,left:null,morning:true,scenarios:[{id:"l1",lesson:"1",title:"x",desc:"y",who:"ראמי",ar:"رامي",she:false}]};
tutorList=true; showView("games"); await sleep(500);
out.card=!!document.querySelector(".morning-card"); out.view=document.body.dataset.view; out.perm=can("tutor.morning");
"""


def test_the_morning_card_is_on_the_scenario_list(site):
    r = site("boss@x.com", MORNING_LIST)
    assert r["card"], r


PREVIEW_ASK = """
await until(()=>PREVIEW_CLASSES.length,8000);
const c=PREVIEW_CLASSES.find(k=>k.name==="מעורבת"); out.c={lessons:c.lessons,levels:c.levels,level:c.level};
setPreview(c); await sleep(500);
out.state=LH.classState(MY_CLASS); out.mine=MY_CLASS&&MY_CLASS.lessons;
await askLesson(true); await sleep(300);
out.options=[...document.querySelectorAll("#lessonDlg input[name=lesson]")].map(r=>r.value);
out.err=(document.querySelector("#lessonDlg .lesson-error")||{}).textContent||"";
"""


def test_a_teacher_viewing_a_class_as_its_student_can_pick_its_lesson(site):
    site.auth.layout = site.auth.layout + [{"id": 31, "name": "מעורבת", "emails": [], "lessons": ["1", "2", "3", "2.1", "2.2"]}]
    r = site("boss@x.com", PREVIEW_ASK)
    assert r["options"] or r["err"], r


NO_LESSONS_ASK = """
await until(()=>PREVIEW_CLASSES.length,8000);
const c=PREVIEW_CLASSES.find(k=>k.name==="רפאים"); setPreview(c); await sleep(500);
MY_CLASS.lessons=["77","78"];   // lessons the course on this page does not have, the class's level still known
await askLesson(true); await sleep(300);
out.options=[...document.querySelectorAll("#lessonDlg input[name=lesson]")].length;
out.err=(document.querySelector("#lessonDlg .lesson-error")||{}).textContent||"";
out.way=!![...document.querySelectorAll("#lessonDlg button")].find(b=>b.textContent.includes("למסלול המלא"));
"""


def test_a_lesson_question_with_no_lesson_to_offer_has_a_way_out(site):
    # a class whose lessons the app's course does not have: no question without an answer
    site.auth.layout = site.auth.layout + [{"id": 32, "name": "רפאים", "emails": [], "lessons": ["1", "2"]}]
    r = site("boss@x.com", NO_LESSONS_ASK)
    assert r["options"] == 0 and "לא נבחרו שיעורים" in r["err"] and r["way"], r


IPHONE_VOICE = """
await sleep(800);
const made=[], played=[];
window.Audio=function(src){ made.push(this); this.src=src||""; this.play=()=>{ played.push(this.src.slice(0,15)); setTimeout(()=>{ this.onplaying&&this.onplaying(); setTimeout(()=>this.onended&&this.onended(),30); },10); return Promise.resolve(); }; this.pause=()=>{}; };
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/say/")) return Promise.resolve(new Response(new Blob(["ID3"]),{status:200}));
  return real(url,opts); };
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
tc.id="chat1"; tc.root.querySelector(".tc-log").dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));   // a tap in the conversation
await sleep(100);
await tcSay(0); await tcSay(0);
out.made=made.length; out.played=played;
"""


def test_on_an_iphone_the_tutor_speaks_in_the_player_a_tap_allowed(site):
    r = site("boss@x.com", IPHONE_VOICE, preload="Object.defineProperty(navigator,'userAgent',{get:()=>'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'});")
    assert r["made"] == 1   # one player for the whole conversation
    assert r["played"][0].startswith("data:audio/wav") and all(p.startswith("blob:") for p in r["played"][1:]) and len(r["played"]) == 3


MORNING_AT_ONCE = MORNING.replace('TUTOR_INFO={available:true,voice:false,', 'TUTOR_INFO={available:true,voice:true,').replace(
    'out.sent=sent[0];', 'out.sent=sent[0]; out.asked=!!document.querySelector(".tc .tc-mode"); out.dialog=!document.querySelector(".tc .tc-dialog").hidden; out.hears=tc.hears; out.box=tc.type.hidden;')


def test_the_morning_talk_starts_at_once_as_a_voice_dialogue_the_device_hearing(site):
    r = site("boss@x.com", MORNING_AT_ONCE, preload=FAKE_BROWSER_RECOGNITION)
    assert not r["asked"] and r["dialog"] and r["hears"] == "browser" and r["box"]   # no wizard; ⌨ goes to the chat
    assert r["sent"]["scenario"] == "morning"


SAID_RIGHT = """
await sleep(800);
TUTOR_INFO={available:true,voice:false,stt:false,turns:10,left:null,scenarios:[]};
localStorage.setItem("ihki.tutorMode",JSON.stringify("chat"));
openTutorChat({id:"l2",lesson:"2",title:"היכרות",who:"ח'ליל",ar:"خليل",she:false});
const mine=tcStudent(); setTr(mine.querySelector(".heard"),"אִחְנַא אַכַּלְתוּ שַׁאוּרְמַה");
tcFix(mine,{tr:"אִחְנַא אַכַּלְנַא שַׁאוּרְמַה",note:"על עצמנו אומרים אַכַּלְנַא."});
tcTutor({tr:"וֵין אַכַּלְתוּ?",he:"איפה אכלתם?",words:[],fix:{tr:"אִחְנַא אַכַּלְנַא שַׁאוּרְמַה"}});
const fix=document.querySelector(".tc .tc-fix");
out.wrong=fix.querySelector(".tc-fix-line.wrong").textContent; out.right=fix.querySelector(".tc-fix-line.right").textContent;
out.colors=[getComputedStyle(fix.querySelector(".wrong")).color,getComputedStyle(fix.querySelector(".right")).color];
out.again=document.querySelector(".tc .tc-msg.in:last-child").textContent.includes("אַכַּלְנַא");
"""


def test_a_correction_shows_once_what_was_said_in_red_and_the_right_sentence_in_green(site):
    r = site("boss@x.com", SAID_RIGHT)
    assert "אמרתם:" in r["wrong"] and "אַכַּלְתוּ" in r["wrong"] and "נכון:" in r["right"] and "אַכַּלְנַא" in r["right"]
    assert r["colors"][0] != r["colors"][1] and not r["again"]   # two colours; the tutor's message does not repeat it


MORNING_FIX_SAID = """
await sleep(800);
const asked=[], played=[];
window.Audio=function(src){ this.src=src||""; this.duration=1; this.currentTime=0; this.play=()=>{ played.push(this.src.slice(0,10)); setTimeout(()=>{ this.onplaying&&this.onplaying();
  setTimeout(()=>{ this.currentTime=1; this.ended=true; this.onpause&&this.onpause({type:"pause"}); this.onended&&this.onended({type:"ended"}); },20); },10); return Promise.resolve(); }; this.pause=()=>{}; };   // as Chrome: "pause", then "ended"
const real=window.fetch;
window.fetch=(url,opts={})=>{ const u=String(url);
  if(u.includes("/api/tutor/say/")){ asked.push(u.split("/api/tutor/say/")[1]); return Promise.resolve(new Response(new Blob(["ID3"]),{status:200})); }
  return real(url,opts); };
TUTOR_INFO={available:true,voice:true,stt:false,turns:10,left:null,scenarios:[]};
localStorage.setItem("ihki.tutorMode",JSON.stringify("chat"));
openTutorChat({id:"morning",lesson:"1",title:"השיחה של הבוקר",who:"ראמי",ar:"رامي",she:false,morning:{weak:[],new:[],fixes:[]}});
tc.id="chat1"; tc.said=1;
tcTutor({tr:"מַרַּה תַאנְיֵה!",he:"עוד פעם!",words:[],fix:{ar:"احنا اكلنا شاورما",tr:"אִחְנַא אַכַּלְנַא שַׁאוּרְמַה",note:"..."}});
await until(()=>played.length>=3,5000); await sleep(100);   // said on its own as it came
out.asked=asked; out.played=played;
"""


def test_the_morning_teachers_correction_is_a_sound_of_its_own(site):
    r = site("boss@x.com", MORNING_FIX_SAID, preload="window.__wizardOwn=true;")
    assert r["asked"] == ["chat1/1?part=fix", "chat1/1?part=reply"]
    assert r["played"][0].startswith("data:audio") and len(r["played"]) == 3   # the chime, the correction, the message
