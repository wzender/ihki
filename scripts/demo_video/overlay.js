// Injected into every page of the demo recording. Two things the real app doesn't have:
// 1. a fake microphone: getUserMedia streams whatever __demo.play(take) plays (a WAV served by the recorder);
// 2. an overlay for captions and tap ripples, drawn by the page so Hebrew renders with the app's own fonts.
(() => {
  // a fake microphone: each request gets its own audio line (as a real mic would), and record.py "speaks"
  // with __demo.play(take) after tapping the mic; the take plays into every line the page holds
  const realGUM = navigator.mediaDevices && navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  const lines = [];
  navigator.mediaDevices.getUserMedia = async (c) => {
    if (!c || !c.audio) return realGUM(c);
    const ctx = new AudioContext({ sampleRate: 48000 }), dest = ctx.createMediaStreamDestination();
    lines.push({ ctx, dest });
    return dest.stream;
  };
  const playTake = async (take) => {
    const data = await (await fetch(`/__demo/${take}.wav`)).arrayBuffer();
    for (const { ctx, dest } of lines.filter(l => l.dest.stream.getAudioTracks().some(t => t.readyState === "live"))) {
      await ctx.resume();
      const src = ctx.createBufferSource(); src.buffer = await ctx.decodeAudioData(data.slice(0));
      src.connect(dest); src.start();
    }
  };

  const css = `
  #demoCap{position:fixed;left:14px;right:14px;z-index:100000;pointer-events:none;direction:rtl;text-align:center;
    font:700 25px/1.3 "Secular One","Assistant",system-ui,sans-serif;color:#fff;background:rgba(28,36,29,.92);
    border-radius:20px;padding:14px 18px;box-shadow:0 8px 24px rgba(0,0,0,.25);
    opacity:0;transform:translateY(12px) scale(.97);transition:opacity .35s,transform .35s}
  #demoCap.on{opacity:1;transform:none}
  #demoCap small{display:block;font:600 17px/1.35 "Assistant",system-ui,sans-serif;color:#E3EAD6;margin-top:4px}
  #demoCap.top{top:18px}#demoCap.bottom{bottom:22px}#demoCap.mid{top:40%}
  .demoTap{position:fixed;z-index:100001;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;pointer-events:none;
    background:rgba(163,48,63,.35);border:3px solid rgba(163,48,63,.85);animation:demoTap .6s ease-out forwards}
  @keyframes demoTap{from{transform:scale(.4);opacity:1}to{transform:scale(1.5);opacity:0}}
  #demoCard{position:fixed;inset:0;z-index:100002;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;
    background:#FAFBF6;color:#1F2156;direction:rtl;text-align:center;padding:24px;opacity:0;transition:opacity .5s;pointer-events:none}
  #demoCard.on{opacity:1}
  #demoCard img{width:82%}
  #demoCard h2{margin:0;font:400 38px/1.2 "Secular One","Assistant",system-ui,sans-serif}
  #demoCard p{margin:0;font:600 21px/1.4 "Assistant",system-ui,sans-serif;color:#5E685C}`;
  const boot = () => {
    if (document.getElementById("demoCap")) return;
    const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    const cap = document.createElement("div"); cap.id = "demoCap"; document.body.appendChild(cap);
    const card = document.createElement("div"); card.id = "demoCard"; document.body.appendChild(card);
  };
  window.__demo = {
    play: playTake,
    caption(text, sub = "", pos = "bottom") {
      boot(); const c = document.getElementById("demoCap");
      if (!text) { c.classList.remove("on"); return; }
      const show = () => { c.className = pos; c.innerHTML = text + (sub ? `<small>${sub}</small>` : ""); requestAnimationFrame(() => c.classList.add("on")); };
      if (c.classList.contains("on")) { c.classList.remove("on"); setTimeout(show, 300); } else show();
    },
    tap(x, y) { boot(); const d = document.createElement("div"); d.className = "demoTap"; d.style.left = x + "px"; d.style.top = y + "px";
      document.body.appendChild(d); setTimeout(() => d.remove(), 700); },
    card(html) { boot(); const c = document.getElementById("demoCard"); if (html == null) { c.classList.remove("on"); return; }
      c.innerHTML = html; c.classList.add("on"); },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
