/* The math path, on /math only: after the opening screen it takes the whole screen in place of the Arabic path.
   The skill graph comes from /api/math/skills (app/math_skills.py). A skill is open once every skill it needs is
   done, and the first open one is where the student goes on. A student may still open a locked skill: without a
   placement test, one who already knows the foundations should not have to answer them first.
   For now each skill has one sample question, and answering it right marks the skill done. Progress is kept on
   this device, per account (ihki.math.v1:<email>), until the server keeps attempts. */
(()=>{
if(document.documentElement.dataset.subject!=="math") return;
const css=document.createElement("link"); css.rel="stylesheet"; css.href=document.currentScript.src.replace("math.js","math.css");
document.head.append(css);

const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=s=>esc(s).replace(/\$([^$]+)\$/g,'<span class="m">$1</span>');   // $…$ is math: set left to right
const plain=s=>String(s).replace(/\$/g,"");
// the model's text: as fmt, and a signed number it left outside $…$ is set left to right too, or its minus lands after it
const fmtLoose=s=>String(s??"").split("$").map((part,i)=>i%2?`<span class="m">${esc(part)}</span>`
  :esc(part).replace(/(^|[\s(־])([−-]\d+(?:[.,]\d+)?)/g,'$1<span class="m">$2</span>')).join("");
const key=()=>"ihki.math.v1:"+(ME_EMAIL||"guest");
const load=()=>{ try{ return JSON.parse(localStorage.getItem(key())||"{}"); }catch{ return {}; } };
const save=()=>{ try{ localStorage.setItem(key(),JSON.stringify(P)); }catch{} };

let G=null, BY={}, POST={}, DEPTH={}, ORDER=[], P={done:{},tries:{}}, root=null, path=null, sheet=null, cur=null, back=null;
let picker=null, PH=null;   // the photo file picker, and the photographed problem on the sheet (see photo())

function index(graph){
  G=graph; BY=Object.fromEntries(G.skills.map(s=>[s.id,s])); POST=Object.fromEntries(G.skills.map(s=>[s.id,[]]));
  G.skills.forEach(s=>s.prereqs.forEach(p=>POST[p].push(s.id)));
  const depth=id=>DEPTH[id]??(DEPTH[id]=BY[id].prereqs.length?1+Math.max(...BY[id].prereqs.map(depth)):0);
  G.skills.forEach(s=>depth(s.id));
  // the path's order: unit by unit, and in a unit each skill after the ones it needs
  ORDER=G.units.flatMap(u=>G.skills.filter(s=>s.unit===u.id).sort((a,b)=>DEPTH[a.id]-DEPTH[b.id]).map(s=>s.id));
}
// the units the student's class studies (its "lessons"); a teacher, or an account in no class, has every unit.
// A skill of a unit the class does not study counts as known: it does not lock the skills that need it.
const shownUnits=()=>{
  const mine=typeof MY_CLASSES==="undefined"?[]:MY_CLASS?[MY_CLASS]:MY_CLASSES, ids=new Set(mine.flatMap(c=>c.lessons||[]));
  const units=G.units.filter(u=>ids.has(u.id)); return units.length?units:G.units;
};
let SHOWN=new Set();
const status=id=>P.done[id]?"done":BY[id].prereqs.every(p=>P.done[p]||!SHOWN.has(BY[p].unit))?"open":"locked";
const next=()=>ORDER.find(id=>SHOWN.has(BY[id].unit)&&status(id)==="open")||null;

async function show(){
  document.documentElement.classList.add("math-on");
  if(!root){
    root=document.createElement("section"); root.id="mathView"; root.setAttribute("aria-label","מסלול המתמטיקה");
    path=document.createElement("div"); path.className="mx-wrap";
    sheet=document.createElement("div"); sheet.className="mx-layer"; sheet.hidden=true;
    // no capture attribute: the phone offers the camera and the gallery both
    picker=document.createElement("input"); picker.type="file"; picker.accept="image/*"; picker.hidden=true;
    picker.onchange=()=>{ const f=picker.files[0]; picker.value=""; if(f) photo(f); };
    root.append(path,sheet,picker); document.body.append(root);
    root.addEventListener("click",onClick);
    document.addEventListener("keydown",e=>{ if(e.key==="Escape"&&cur) closeSheet(); });
  }
  root.hidden=false;
  if(!G){
    path.innerHTML='<p class="mx-note">טוענים את המסלול…</p>';
    try{ const r=await fetch("/api/math/skills"); if(!r.ok) throw new Error(`השרת ענה ${r.status}`); index(await r.json()); }
    catch(e){ path.innerHTML=`<p class="mx-note">לא הצלחנו לטעון את המסלול: ${esc(e.message)}. רעננו את העמוד ונסו שוב.</p>`; return; }
  }
  P=Object.assign({done:{},tries:{}},load()); SHOWN=new Set(shownUnits().map(u=>u.id)); draw();
}
function hide(){ if(root) root.hidden=true; document.documentElement.classList.remove("math-on"); }

function draw(){
  const UNITS=G.units.filter(u=>SHOWN.has(u.id)), mine=G.skills.filter(s=>SHOWN.has(s.unit));
  const total=mine.length, doneN=mine.filter(s=>P.done[s.id]).length, nxt=next();
  const cls=typeof MY_CLASS!=="undefined"&&MY_CLASS?` · כיתת ${esc(MY_CLASS.name)}`:"";
  const manage=AUTH_URL&&can("management.access"), pct=Math.round(100*doneN/total);
  const units=UNITS.map(u=>{
    const ids=ORDER.filter(id=>BY[id].unit===u.id), d=ids.filter(id=>P.done[id]).length;
    return `<div class="mx-su u-${u.id}"><b>${esc(u.name)}</b><span class="mx-bar"><i style="width:${Math.round(100*d/ids.length)}%"></i></span><small>${d} מתוך ${ids.length}</small></div>`;
  }).join("");
  const trail=UNITS.map(u=>{
    const ids=ORDER.filter(id=>BY[id].unit===u.id);
    return `<section class="mx-unit u-${u.id}"><div class="mx-uhead"><h2>${esc(u.name)}</h2><span>${esc(u.source)}${u.when?" · "+esc(u.when):""}</span></div>
      <div class="mx-trail">${ids.map((id,i)=>{
        const s=BY[id], st=status(id), here=id===nxt;
        return `<button class="mx-node st-${st}${here?" here":""}" data-id="${id}" aria-label="${esc(plain(s.name))}, ${st==="done"?"הושלמה":st==="open"?"פתוחה":"נעולה"}">
          <span class="mx-dot">${st==="done"?"✓":i+1}</span>
          <span class="mx-lbl"><b>${fmt(s.name)}</b><small>${s.core?'<span class="mx-core">ליבה 5 יח״ל</span>':""}${here?'<span class="mx-go">ממשיכים כאן</span>':""}</small></span></button>`;
      }).join("")}</div></section>`;
  }).join("");
  path.innerHTML=`<header class="mx-top"><div><h1>המסלול שלי</h1><p class="mx-sub">אלגברה כיתה ט׳ · 5 יח״ל${cls}</p></div>
      <div class="mx-acts">${manage?'<button class="mx-btn" data-act="manage">ניהול</button>':""}${AUTH_URL?'<button class="mx-btn" data-act="out">יציאה</button>':""}</div></header>
    <section class="mx-progress" aria-label="ההתקדמות שלי">
      <div class="mx-ring" style="--p:${pct}"><b>${doneN}</b><small>מתוך ${total}</small></div>
      <div class="mx-units">${units}</div></section>
    ${nxt?`<button class="mx-next" data-id="${nxt}"><small>ממשיכים מכאן</small><b>${fmt(BY[nxt].name)}</b></button>`:'<p class="mx-note">סיימתם את כל המסלול. כל הכבוד!</p>'}
    <button class="mx-photo" data-act="photo"><b>צילום תרגיל</b><small>צלמו תרגיל מהמחברת או מהדף, ונעזור לכם לפתור אותו צעד אחר צעד</small></button>
    ${trail}
    <p class="mx-note">בשלב הזה יש שאלה אחת לדוגמה לכל מיומנות. ההתקדמות נשמרת במכשיר הזה.</p>`;
}

function chips(ids){
  return ids.length?ids.map(id=>`<button class="mx-chip u-${BY[id].unit}${P.done[id]?" is-done":""}" data-id="${id}">${P.done[id]?"✓ ":""}${esc(BY[id].short)}</button>`).join(""):'<em>אין</em>';
}
function open(id){
  const s=BY[id], st=status(id), unit=G.units.find(u=>u.id===s.unit), rot=ORDER.indexOf(id)%s.sample.choices.length;
  const choices=s.sample.choices.slice(rot).concat(s.sample.choices.slice(0,rot)), missing=s.prereqs.filter(p=>!P.done[p]&&SHOWN.has(BY[p].unit));
  if(!cur) back=document.activeElement;
  cur=id;
  sheet.innerHTML=`<div class="mx-scrim" data-act="close"></div>
    <div class="mx-sheet u-${s.unit}" role="dialog" aria-modal="true" aria-labelledby="mxTitle">
      <div class="mx-shtop"><div><span class="mx-uchip">${esc(unit.name)}</span>${s.core?' <span class="mx-core">ליבה 5 יח״ל</span>':""}
        <h3 id="mxTitle">${fmt(s.name)}</h3><small class="mx-src">${s.month?`בפריסה: ${esc(s.month)}`:esc(unit.source)}${st==="done"?" · הושלמה ✓":""}</small></div>
        <button class="mx-x" data-act="close" aria-label="סגירה">✕</button></div>
      <p>${fmt(s.description)}</p>
      ${st==="locked"?`<div class="mx-hint">מומלץ לעבור קודם על: <span class="mx-chips">${chips(missing)}</span></div>`:""}
      <div class="mx-q"><p>${fmt(s.sample.question)}</p>${s.sample.graph?plot(s.sample.graph):""}
        <div class="mx-choices">${choices.map((c,i)=>`<button data-choice="${i}" data-m="${c.correct?"":esc(c.mistake)}">${fmt(c.text)}</button>`).join("")}</div>
        <div class="mx-fb" aria-live="polite"></div></div>
      <div class="mx-sec"><h4>דורשת</h4><div class="mx-chips">${chips(s.prereqs)}</div></div>
      <div class="mx-sec"><h4>פותחת</h4><div class="mx-chips">${chips(POST[id])}</div></div>
    </div>`;
  sheet.hidden=false; sheet.querySelector(".mx-sheet").scrollTop=0;
  sheet.querySelector(".mx-x").focus({preventScroll:true});
}
function closeSheet(){ sheet.hidden=true; cur=null; back?.focus?.({preventScroll:true}); back=null; }

function answer(btn){
  const id=cur, m=btn.dataset.m, fb=sheet.querySelector(".mx-fb");
  sheet.querySelectorAll("[data-choice]").forEach(b=>b.classList.remove("right","wrong"));
  btn.classList.add(m?"wrong":"right");
  P.tries[id]=(P.tries[id]||0)+1;
  if(!m){
    const first=!P.done[id]; P.done[id]=P.done[id]||new Date().toISOString(); save(); draw();
    const nxt=next();
    fb.innerHTML=`<div class="mx-ok"><b>נכון!</b>${first?"<span>המיומנות סומנה כהושלמה.</span>":""}
      ${nxt&&nxt!==id?`<button class="mx-btn solid" data-id="${nxt}">למיומנות הבאה: ${esc(BY[nxt].short)}</button>`:""}</div>`;
    return;
  }
  save();
  const M=G.mistakes[m], to=M.back&&M.back!==id?M.back:null;
  fb.innerHTML=`<div class="mx-bad"><b>${fmt(M.name)}</b><span>${fmt(M.tip)}</span>
    ${to?`<button class="mx-btn" data-id="${to}">חזרה אל: ${esc(BY[to].short)}</button>`:"<span class=\"mx-small\">אפשר לבחור תשובה אחרת.</span>"}</div>`;
}

function onClick(e){
  const t=e.target.closest("button,[data-act]"); if(!t||!root.contains(t)) return;
  if(t.dataset.choice!=null) return answer(t);
  if(t.dataset.id) return open(t.dataset.id);
  switch(t.dataset.act){
    case "photo": case "retake": return picker.click();
    case "hint": PH.shown++; return drawPhoto();
    case "work": PH.works.add(+t.dataset.i); return drawPhoto();
    case "all": PH.all=true; return drawPhoto();
    case "close": return closeSheet();
    case "manage": hide(); return showManage();
    case "out": return document.getElementById("logoutBtn").click();
  }
}

/* A photographed problem (/api/math/photo, app/math_photo.py): hints first. The sheet shows the problem as it was
   read (a wrong reading is the student's cue to photograph again), then one hint; each further hint, each step and
   the whole solution with its answer come only when the student asks for them. */
async function shrink(file){   // a phone photo is several MB: about 1600 px on its long side is enough to read it
  const bmp=await createImageBitmap(file,{imageOrientation:"from-image"}), k=Math.min(1,1600/Math.max(bmp.width,bmp.height));
  const c=document.createElement("canvas"); c.width=Math.round(bmp.width*k); c.height=Math.round(bmp.height*k);
  c.getContext("2d").drawImage(bmp,0,0,c.width,c.height); bmp.close?.();
  return new Promise((ok,no)=>c.toBlob(b=>b?ok(b):no(new Error("toBlob")),"image/jpeg",.85));
}
async function photo(file){
  let blob; try{ blob=await shrink(file); }catch{ blob=file; }   // a format the browser cannot draw goes as it is
  if(PH?.url) URL.revokeObjectURL(PH.url);
  if(cur!=="photo"){ if(!cur) back=document.activeElement; cur="photo"; }
  const mine=PH={url:URL.createObjectURL(blob),data:null,error:"",busy:true,shown:1,works:new Set(),all:false,fresh:true};
  drawPhoto();
  try{
    const form=new FormData(); form.append("photo",blob,"problem.jpg");
    const res=await api("/api/math/photo",{method:"POST",body:form}), data=await res.json().catch(()=>({}));
    if(!res.ok) throw new Error(typeof data.detail==="string"?data.detail:`השרת ענה ${res.status}`);
    mine.data=data;
  }catch(e){ mine.error=e.message; }
  mine.busy=false;
  if(PH===mine&&cur==="photo") drawPhoto();   // a newer photo, or a closed sheet, keeps its own
}
function drawPhoto(){
  const d=PH.data;
  let body;
  if(PH.busy) body='<div class="mx-wait"><span class="mx-spin" aria-hidden="true"></span><b>קוראים את התרגיל ופותרים…</b><small>זה לוקח כמה שניות.</small></div>';
  else if(PH.error) body=`<div class="mx-bad"><b>לא הצלחנו</b><span>${esc(PH.error)}</span><button class="mx-btn" data-act="retake">צילום מחדש</button></div>`;
  else if(!d.readable) body=`<div class="mx-bad"><b>לא הצלחנו לקרוא את התרגיל</b><span>${fmtLoose(d.note||"צלמו שוב, קרוב יותר ובאור טוב.")}</span><button class="mx-btn" data-act="retake">צילום מחדש</button></div>`;
  else{
    const n=d.steps.length, shown=PH.all?n:Math.min(PH.shown,n);
    body=`<div class="mx-read"><small>זה התרגיל שקראנו:</small><p>${fmtLoose(d.problem)}</p><button class="mx-link" data-act="retake">לא נכון? צלמו שוב</button></div>
      ${d.note?`<p class="mx-small">${fmtLoose(d.note)}</p>`:""}
      <ol class="mx-steps">${d.steps.slice(0,shown).map((s,i)=>`<li><span>${fmtLoose(s.hint)}</span>
        ${PH.all||PH.works.has(i)?`<div class="mx-work">${fmtLoose(s.work)}</div>`:`<button class="mx-link" data-act="work" data-i="${i}">הראו את הצעד</button>`}</li>`).join("")}</ol>
      ${PH.all?`<div class="mx-ok"><b>התשובה</b><span>${fmtLoose(d.answer)}</span></div>`:`<div class="mx-row">
        ${shown<n?'<button class="mx-btn solid" data-act="hint">רמז הבא</button>':""}<button class="mx-btn" data-act="all">הפתרון המלא</button></div>`}
      ${d.skill&&BY[d.skill]?`<div class="mx-sec"><h4>המיומנות שהתרגיל מתרגל</h4><div class="mx-chips">${chips([d.skill])}</div></div>`:""}
      ${d.left!=null?`<p class="mx-small">נשארו לכם היום ${d.left} צילומים.</p>`:""}`;
  }
  sheet.innerHTML=`<div class="mx-scrim" data-act="close"></div>
    <div class="mx-sheet" role="dialog" aria-modal="true" aria-labelledby="mxTitle">
      <div class="mx-shtop"><div><h3 id="mxTitle">פתרון מתמונה</h3><small class="mx-src">קודם רמזים, והפתרון המלא כשתבקשו.</small></div>
        <button class="mx-x" data-act="close" aria-label="סגירה">✕</button></div>
      <img class="mx-thumb" src="${PH.url}" alt="התמונה ששלחתם">
      <div class="mx-pbody" aria-live="polite">${body}</div></div>`;
  sheet.hidden=false;
  if(PH.fresh){ PH.fresh=false; sheet.querySelector(".mx-x").focus({preventScroll:true}); }
}

// a function's graph through the given points: monotone cubic (Fritsch-Carlson), so it never passes the marked extrema
function plot(g){
  const W=320, H=210, PAD=24, [x0,x1]=g.x, [y0,y1]=g.y;
  const sx=x=>PAD+(x-x0)/(x1-x0)*(W-2*PAD), sy=y=>H-PAD-(y-y0)/(y1-y0)*(H-2*PAD);
  const xs=g.points.map(p=>p[0]), ys=g.points.map(p=>p[1]), k=xs.length;
  const d=xs.slice(0,-1).map((x,i)=>(ys[i+1]-ys[i])/(xs[i+1]-x));
  const m=xs.map((_,i)=>i===0?d[0]:i===k-1?d[k-2]:d[i-1]*d[i]<=0?0:(d[i-1]+d[i])/2);
  d.forEach((di,i)=>{ if(di===0){ m[i]=m[i+1]=0; return; } const a=m[i]/di, b=m[i+1]/di, s=a*a+b*b;
    if(s>9){ const t=3/Math.sqrt(s); m[i]=t*a*di; m[i+1]=t*b*di; } });
  let line="";
  for(let i=0;i<k-1;i++){ const h=xs[i+1]-xs[i];
    for(let j=0;j<=20;j++){ const t=j/20, t2=t*t, t3=t2*t;
      const y=(2*t3-3*t2+1)*ys[i]+(t3-2*t2+t)*h*m[i]+(-2*t3+3*t2)*ys[i+1]+(t3-t2)*h*m[i+1];
      line+=`${line?"L":"M"}${sx(xs[i]+t*h).toFixed(1)},${sy(y).toFixed(1)}`; } }
  let grid="", ticks="";
  for(let x=Math.ceil(x0);x<=x1;x++){ grid+=`<line class="g" x1="${sx(x)}" x2="${sx(x)}" y1="${sy(y0)}" y2="${sy(y1)}"/>`;
    if(x) ticks+=`<text x="${sx(x)}" y="${Math.min(sy(0)+13,H-4)}" text-anchor="middle">${x}</text>`; }
  for(let y=Math.ceil(y0);y<=y1;y++){ grid+=`<line class="g" x1="${sx(x0)}" x2="${sx(x1)}" y1="${sy(y)}" y2="${sy(y)}"/>`;
    if(y) ticks+=`<text x="${Math.max(sx(0)-5,10)}" y="${sy(y)+4}" text-anchor="end">${y}</text>`; }
  const dots=g.points.map(([x,y])=>`<circle cx="${sx(x)}" cy="${sy(y)}" r="3.5"/>`).join("");
  return `<div class="mx-plot"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="גרף הפונקציה">${grid}
    <line class="a" x1="${sx(x0)}" x2="${sx(x1)}" y1="${sy(0)}" y2="${sy(0)}"/><line class="a" x1="${sx(0)}" x2="${sx(0)}" y1="${sy(y0)}" y2="${sy(y1)}"/>
    ${ticks}<path class="c" d="${line}"/>${dots}</svg></div>`;
}

// the start button (and a teacher's "תרגול") opens the math path once the opening screen is gone
const arabicEnter=enter;
const enterMath=()=>{ arabicEnter(); if(!AUTH_URL||can("path.use")) show(); };
document.getElementById("startBtn").onclick=document.getElementById("practiceBtn").onclick=enterMath;

// the manage panel: "לתרגול" goes to the math path, and "תוכן" lists the math path (units and their skills) in
// place of the Arabic levels. The graph is a file in the repo (app/math_algebra9.json), so here it is only shown.
const toPractice=document.getElementById("mPractice"), arabicPractice=toPractice.onclick;
const practice=async id=>{ arabicPractice(); await show(); if(id&&G) open(id); };
toPractice.onclick=()=>practice();
const contentTab=document.querySelector('#mModes [data-mode="content"] small'); if(contentTab) contentTab.textContent="יחידות ומיומנויות";
async function drawContent(){
  const box=document.getElementById("mContent");
  if(!G){
    box.innerHTML='<p class="mx-note">טוענים את המסלול…</p>';
    try{ const r=await fetch("/api/math/skills"); if(!r.ok) throw new Error(`השרת ענה ${r.status}`); index(await r.json()); }
    catch(e){ box.innerHTML=`<p>לא הצלחנו לטעון את המסלול: ${esc(e.message)}. לחצו „רענון” ונסו שוב.</p>`; return; }
  }
  const wide=(()=>{ try{ return JSON.parse(localStorage.getItem("ihki.math.copen")||"{}"); }catch{ return {}; } })();
  box.innerHTML=`<p class="sub">המסלול של כיתה ט׳, 5 יח״ל: ${G.skills.length} מיומנויות ב־${G.units.length} יחידות, לפי סדר המסלול. לכל מיומנות שאלה לדוגמה, וכל תשובה שגויה בה היא טעות מוכרת עם הסבר. ב„תרגול” רואים את השאלה כמו שהתלמידים רואים אותה.</p>`+
    G.units.map(u=>{
      const mine=ORDER.filter(id=>BY[id].unit===u.id).map(id=>BY[id]);
      const rows=mine.map((s,k)=>{
        const needs=s.prereqs.map(p=>plain(BY[p].short)).join(", "), wrong=s.sample.choices.filter(c=>!c.correct).length;
        const meta=[needs?`דורשת: ${esc(needs)}`:"בלי דרישות קודמות",s.month?`בפריסה: ${esc(s.month)}`:"",s.core?"ליבה 5 יח״ל":"",`${wrong} טעויות מוכרות`].filter(Boolean).join(" · ");
        return `<li class="crow"><i>${k+1}</i><div class="two"><b>${fmt(s.name)}</b><small>${meta}</small></div><div class="acts"><button class="btn" data-mxpractice="${s.id}">תרגול</button></div></li>`;
      }).join("");
      return `<details class="csec" data-unit="${u.id}"${wide[u.id]?" open":""}><summary class="clevel">${esc(u.name)}<small>${mine.length} מיומנויות${u.when?` · ${esc(u.when)}`:""}</small></summary>
        <div class="card"><div class="chead"><h2>${esc(u.name)}<small>${esc(u.source||"")}</small></h2></div><ol class="clist">${rows}</ol></div></details>`;
    }).join("");
  box.querySelectorAll("details.csec").forEach(d=>d.ontoggle=()=>{ wide[d.dataset.unit]=d.open?1:0; try{ localStorage.setItem("ihki.math.copen",JSON.stringify(wide)); }catch{} });
  box.querySelectorAll("[data-mxpractice]").forEach(b=>b.onclick=()=>practice(b.dataset.mxpractice));
}
window.drawContent=drawContent;
})();
