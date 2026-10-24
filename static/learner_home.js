/* The focused learner home (openspec/changes/add-focused-learner-home): which lesson a student's class is on now,
   and the few actions the home offers in its place of the whole path. Pure helpers, and the two small screens (the
   lesson question and the home). index.html gives them the course, the class and the student's progress. */
(function(root){
"use strict";
const KEY="ihki.lesson.v1", MAX_ACTIONS=3, SHORT=10;

/* Where the device keeps a class's lesson for one account: another account, subject or class never reads it. */
function storageKey(subject,email,classId){
  return `${KEY}:${subject||"arabic"}:${String(email||"").trim().toLowerCase()}:${classId}`;
}

/* The lesson a class is on now. `server`: the one /api/me gave (null when none); `local`: what the device kept,
   {lesson, synced}, or null; `lessons`: the class's lesson ids. The server's wins; a device's choice the server does not
   have (saved while it was unreachable) is used and sent again. A lesson the class no longer has is none. */
function resolveLesson(server,local,lessons){
  const ok=id=>typeof id==="string"&&id!==""&&lessons.includes(id);
  if(ok(server)) return {lesson:server,retry:false};
  if(local&&ok(local.lesson)) return {lesson:local.lesson,retry:true};
  return {lesson:null,retry:false};
}

/* Whether a class's current lesson can be asked: "ok"; "math" (its own path, not asked); "teacher" (they teach it:
   the path stays); "ambiguous" (its lessons are not of one level, so the class must be corrected); "none". */
function classState(cls){
  if(!cls) return "none";
  if(cls.subject==="math") return "math";
  if(cls.teacher) return "teacher";
  return cls.level?"ok":"ambiguous";
}

/* The lessons daily practice takes its new sentences from, in turn: the homework's, the current one, the first
   unfinished, then every other in the course's order; each once. */
function lessonOrder(homework,current,unfinished,all){
  const out=[];
  for(const l of [homework,current,unfinished,...all]) if(l&&!out.includes(l)) out.push(l);
  return out;
}

/* The home's actions: of the candidates, in their order, the first MAX_ACTIONS that can start, each target once. */
function pickActions(candidates){
  const seen=new Set(), out=[];
  for(const a of candidates){
    if(!a||!a.ready||seen.has(a.target)) continue;
    seen.add(a.target); out.push(a);
    if(out.length===MAX_ACTIONS) break;
  }
  return out;
}

/* The short activity, at most SHORT sentences: the weakest ones the student said and can say better (under
   `excellent`), or else the first unsaid ones of the current lesson. null when there are none. */
function shortPlan(practised,lesson,best,excellent){
  const weak=practised.filter(p=>best[p.id]!=null&&best[p.id]<excellent).sort((a,b)=>best[a.id]-best[b.id]);
  if(weak.length) return {kind:"review",ids:weak.slice(0,SHORT).map(p=>p.id)};
  const fresh=lesson.filter(p=>best[p.id]==null);
  return fresh.length?{kind:"new",ids:fresh.slice(0,SHORT).map(p=>p.id)}:null;
}

function el(tag,cls,text){
  const e=document.createElement(tag);
  if(cls) e.className=cls;
  if(text!=null) e.textContent=text;
  return e;
}

/* The home. `o`: {className, lessonName, actions: [{kind, title, detail, go, state, run}], done: the current lesson is
   finished, onChange, onPath, onRefer}. Its heading takes the focus when `focus` is set. */
function renderHome(box,o,focus){
  const page=el("section","focus"), head=el("header"), where=el("p","focus-where"), title=el("h2","","מה עושים עכשיו?");
  page.setAttribute("aria-labelledby","focusTitle"); title.id="focusTitle"; title.tabIndex=-1;
  const change=el("button","focus-change","שינוי"); change.type="button";
  change.setAttribute("aria-label",`שינוי השיעור הנוכחי (${o.lessonName})`); change.onclick=o.onChange;
  where.append(el("span","",`כיתת ${o.className} · ${o.lessonName}`),change);
  head.append(where,title); page.append(head);
  if(o.done) page.append(el("p","focus-note",`סיימתם את ${o.lessonName}. אפשר לחזור על משפטים, או לבחור את השיעור שהכיתה לומדת עכשיו.`));
  const list=el("ol","focus-actions"); list.setAttribute("aria-label","הפעילויות המומלצות");
  for(const a of o.actions){
    const li=el("li"), b=el("button","focus-action"+(a.state?" "+a.state:"")); b.type="button";
    b.append(el("strong","",a.title)); if(a.detail) b.append(el("span","focus-detail",a.detail)); b.append(el("span","focus-go",a.go||"התחילו"));
    b.setAttribute("aria-label",[a.title,a.detail,a.go||"התחילו"].filter(Boolean).join(", "));
    b.onclick=a.run; li.append(b); list.append(li);
  }
  if(o.actions.length) page.append(list);
  else page.append(el("p","focus-note","אין כרגע פעילות מומלצת. אפשר להמשיך מהמסלול המלא."));
  const more=el("div","focus-more"), refer=el("button","focus-refer","חבר מביא חבר"), path=el("button","focus-path","צפייה במסלול המלא");
  refer.type=path.type="button"; refer.onclick=o.onRefer; path.onclick=o.onPath;
  more.append(refer,path); page.append(more);
  box.append(page);
  if(focus) title.focus({preventScroll:true});
  return page;
}

/* The question "which lesson is the class on now?" in `dlg`. `o`: {className, levelName, lessons: [{id, name}],
   selected, required: Escape does not close it, error: a message in place of the lessons, onConfirm(id) (may be async;
   a thrown error is shown and the question stays), after(id): once it has closed, onPath}. */
function openLessonDialog(dlg,o){
  dlg.replaceChildren();
  dlg.setAttribute("aria-labelledby","lessonTitle"); dlg.setAttribute("aria-describedby","lessonWhere");
  const title=el("h2","","באיזה שיעור הכיתה עכשיו?"); title.id="lessonTitle"; title.tabIndex=-1;
  const where=el("p","lesson-where",[`כיתת ${o.className}`,o.levelName].filter(Boolean).join(" · ")); where.id="lessonWhere";
  const status=el("p","lesson-status"); status.setAttribute("role","status"); status.setAttribute("aria-live","polite");
  dlg.append(title,where);
  dlg.oncancel=e=>{ if(o.required&&!o.error) e.preventDefault(); };
  if(o.error){
    dlg.append(el("p","lesson-error",o.error));
    const go=el("button","btn primary","למסלול המלא"); go.type="button";
    go.onclick=()=>{ dlg.close(); if(o.onPath) o.onPath(); };
    const row=el("div","row"); row.append(go); dlg.append(row);
    dlg.showModal(); go.focus(); return;
  }
  const form=el("form","lesson-form"), set=el("fieldset"), legend=el("legend","sr-only","השיעור הנוכחי");
  form.method="dialog"; set.append(legend);
  let first=null;
  for(const l of o.lessons){
    const label=el("label","lesson-option"), radio=el("input");
    radio.type="radio"; radio.name="lesson"; radio.value=l.id; radio.checked=l.id===o.selected;
    label.append(radio,el("span","",l.name)); set.append(label);
    if(!first||radio.checked) first=radio;
  }
  const ok=el("button","btn primary","ממשיכים"); ok.type="submit";
  const row=el("div","row"); row.append(ok);
  if(!o.required){ const cancel=el("button","btn","ביטול"); cancel.type="button"; cancel.onclick=()=>dlg.close(); row.append(cancel); }
  form.append(set,status,row); dlg.append(form);
  form.onsubmit=async e=>{
    e.preventDefault();
    const picked=form.querySelector("input[name=lesson]:checked");
    if(!picked){ status.textContent="בחרו שיעור כדי להמשיך."; (first||ok).focus(); return; }
    ok.disabled=true;
    try{ await o.onConfirm(picked.value); }
    catch(err){ status.textContent=err&&err.message?err.message:"לא הצלחנו לשמור. נסו שוב."; ok.disabled=false; return; }
    ok.disabled=false; dlg.close();
    if(o.after) o.after(picked.value);   // after closing, which gives the focus back to where it was
  };
  dlg.showModal(); (first||ok).focus();
}

root.LearnerHome=Object.freeze({KEY,MAX_ACTIONS,SHORT,storageKey,resolveLesson,classState,lessonOrder,pickActions,
  shortPlan,renderHome,openLessonDialog});
})(typeof globalThis!=="undefined"?globalThis:this);
