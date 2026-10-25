"""The short promo: the app's core in ~30 seconds, inside a 3D iPhone. The student says a verb, the app hears
which consonant went wrong (the ح a Hebrew speaker swallows), marks it and says how to fix it; then the retry.

    python scripts/demo_video/promo.py --takes scripts/demo_video/takes --out scripts/demo_video/ihki-promo.mp4

Two passes:
1. the practice screen is recorded at an iPhone's size (393x852), as frames with their times, and the moments
   that matter (the score, the red letter, the tip) are noted with where they are on the screen;
2. promo.html places those frames on a CSS 3D iPhone, with the headlines around it and a camera that moves in on
   the red letter, and is rendered frame by frame (so the timing is exact, whatever the machine's speed).

The app should run with sign-in off (empty SUPABASE_* variables); make_takes.py makes the takes.
"""
import argparse, asyncio, base64, json, shutil, subprocess, tempfile, time
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
SW, SH, SDPR = 393, 852, 2          # the phone's screen: an iPhone 15's CSS size
OW, OH, FPS = 1080, 1920, 30        # the promo


HOME = """(()=>{
  const box=$("vids"); box.replaceChildren(); document.body.dataset.view="home";
  const nav=$("nav"); nav.querySelector('[data-view="home"]').hidden=false; nav.querySelector('[data-view="refer"]').hidden=true;
  nav.querySelectorAll("button").forEach(b=>{ if(b.dataset.view==="home") b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current"); });
  drawRewardShelf();
  LH.renderHome(box,{className:"א1",lessonName:"שיעור 4",done:false,
    actions:[{kind:"continue",title:"ממשיכים בשיעור 4",detail:"הטיות בעבר",go:"המשיכו",minutes:LH.minutesFor(32),run(){}},
             {kind:"short",title:"תרגול קצר",detail:"10 משפטים חדשים מהשיעור",minutes:LH.minutesFor(10),run(){}}],
    onChange(){},onPath(){},onRefer(){}});
  scrollTo(0,0);
})()"""


async def record_screen(url, takes, work):
    """Pass 1: the practice screen's frames, [{t, file}], and the noted moments {name: {t, box?}}."""
    frames, marks = [], {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(viewport={"width": SW, "height": SH}, device_scale_factor=SDPR, is_mobile=True,
                                        has_touch=True, locale="he-IL", color_scheme="light", permissions=["microphone"])
        await ctx.add_init_script(path=str(HERE / "overlay.js"))
        await ctx.route("**/__demo/*.wav", lambda r: r.fulfill(
            path=str(Path(takes) / r.request.url.split("/")[-1]), content_type="audio/wav"))
        pg = await ctx.new_page()
        cdp = await ctx.new_cdp_session(pg)
        wait = pg.wait_for_timeout
        t0, paused = [None], {"on": False, "at": 0.0, "skipped": 0.0}   # the skipped stretches leave the clock

        async def frame(f):
            await cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})
            if paused["on"]:
                return
            ts = f["metadata"]["timestamp"]
            if t0[0] is None:
                t0[0] = ts
            name = f"s{len(frames):05d}.jpg"
            (work / name).write_bytes(base64.b64decode(f["data"]))
            frames.append({"t": round(max(ts - t0[0] - paused["skipped"], frames[-1]["t"] if frames else 0), 4),
                           "file": name})

        def pause():
            paused.update(on=True, at=time.time())

        async def resume():
            paused["skipped"] += time.time() - paused["at"]; paused["on"] = False
            # a still screen sends no frames: an invisible change makes Chrome send the one now on screen
            await pg.evaluate("""(()=>{ let k=document.getElementById('demoKick'); if(!k){ k=document.createElement('div'); k.id='demoKick';
              k.style.cssText='position:fixed;left:0;top:0;width:2px;height:2px;pointer-events:none;z-index:999999'; document.body.append(k); }
              k.style.background=k.style.background==='rgba(0, 0, 0, 0.01)'?'rgba(0, 0, 0, 0.02)':'rgba(0, 0, 0, 0.01)'; })()""")
            await asyncio.sleep(0.12)

        async def mark(name, sel=None):
            m = {"t": round(time.time() - t0[0] - paused["skipped"], 3)}
            if sel:
                b = await pg.locator(sel).first.bounding_box()
                m["box"] = [b["x"] / SW, b["y"] / SH, b["width"] / SW, b["height"] / SH]
            marks[name] = m

        async def tap(sel, pause=300):
            loc = pg.locator(sel).first
            b = await loc.bounding_box()
            await pg.evaluate("a=>__demo.tap(...a)", [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2])
            await wait(180); await loc.click(); await wait(pause)

        recording = lambda: pg.evaluate("typeof rec!=='undefined'&&rec&&rec.state==='recording'")

        async def say(take, fresh=False):
            """fresh: drop a recording the app started by itself (it would hold silence before the take)."""
            await pg.evaluate("document.getElementById('result').hidden=true")
            if fresh and await recording():
                await pg.evaluate("recCancel=true; rec.stop()"); await wait(400)
            for _ in range(80):                  # the app may be counting down ("מקליט בעוד 3…") to record by itself
                if await recording() or not (await pg.inner_text("#status")).startswith("מקליט בעוד"):
                    break
                await wait(50)
            if await recording():
                b = await pg.locator("#mic").bounding_box()       # the app is already listening by itself
                await pg.evaluate("a=>__demo.tap(...a)", [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2])
            else:
                await tap("#mic", 0)
            for _ in range(40):
                if await recording():
                    break
                await wait(50)
            await wait(150)
            print(f"  {take}: recording={await recording()} status={await pg.inner_text('#status')!r}")
            await pg.evaluate("t=>__demo.play(t)", take)
            try:
                await pg.wait_for_selector("#result:not([hidden])", timeout=30000)
            except Exception:
                await pg.screenshot(path=str(work / "stuck.png"))
                raise SystemExit(f"no score for {take}: status {await pg.inner_text('#status')!r}, see {work / 'stuck.png'}")

        # off camera: in, and the focused home a signed-in student of class א1 on lesson 4 would get (with sign-in
        # off the app shows the path instead, so the app's own home component is drawn here with that content)
        await pg.goto(url, wait_until="networkidle"); await wait(1500)
        await pg.evaluate("ME_PERMISSIONS=new Set(['practice.use','exercises.use','path.use','games.use'])")
        await pg.evaluate("store.set('ihki.firstGold',true)")    # the 100 stays on screen (no first-gold window over it)
        await pg.click("#practiceBtn"); await wait(800)
        if await pg.locator("#guideTipClose").is_visible():
            await pg.click("#guideTipClose")
        await pg.evaluate(HOME); await wait(700)

        cdp.on("Page.screencastFrame", lambda f: asyncio.ensure_future(frame(f)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90,
                                                "maxWidth": SW * SDPR, "maxHeight": SH * SDPR})
        while t0[0] is None:                    # the first frame sets the clock
            await asyncio.sleep(0.05)
        await mark("home", ".focus-action.hero")
        await mark("short", ".focus-action:not(.hero)")
        await wait(2600)
        await mark("homeTap", ".focus-action.hero")
        await tap(".focus-action.hero", 200)

        # off camera: the hero card's topic, "הטיות בעבר" of lesson 4, on "אנחנו דיברנו"
        pause()
        await pg.evaluate("pickCat('l4-past')"); await wait(800)
        for _ in range(40):
            if (await pg.inner_text("#prompt")).strip() == "אנחנו דיברנו":
                break
            await pg.click("#nextBtn"); await wait(250)
        await pg.evaluate("scrollTo(0,0)"); await wait(200)
        await resume()
        await mark("start")
        await wait(900)
        await mark("mic", "#mic")
        await say("m892")
        await mark("score", "#result")
        bad = '#wordReveals .bad.why[title^="ח (ح) נשמעה כמו ח\'"]'
        await mark("letter", bad)
        print("  score:", await pg.inner_text("#score"))
        await wait(2600)
        await mark("tap", bad)
        await tap(bad, 0)
        await wait(400)
        await mark("tip", "#whyTip")
        await wait(3600)
        await pg.mouse.click(5, 5)               # closes the tip
        await wait(400)
        await mark("retry", "#mic")
        await say("c892", fresh=True)
        await pg.evaluate("cancelAutoNext()")    # keeps the score on screen
        await mark("great", "#result")
        print("  retry:", await pg.inner_text("#score"))
        await wait(5200)                         # the 100, then the beads headline over the gold bead

        # driving mode on the same verb, fresh: the app reads it out (most of that wait is skipped), says "תורכם"
        # and listens, then says the verdict
        pause()
        await pg.evaluate("store.set('ihki.driveSeen',true)")      # past the first-time explanation
        # still on "אנחנו דיברנו" (re-opening the topic would start a round without it: it was just passed)
        await pg.evaluate("cancelAutoNext(); show(cur,true); scrollTo(0,0)")   # fresh: no old score, no countdown to the next verb
        await wait(400)
        await pg.click("#driveBtn"); await wait(150)
        await resume()
        await mark("drive")
        await wait(1800)
        pause()
        status = lambda: pg.inner_text("#status")
        for _ in range(300):
            if (await status()).startswith("תורכם"):
                break
            await wait(50)
        for _ in range(200):                     # after its beep the app starts recording: only then the answer
            if await pg.evaluate("typeof rec!=='undefined'&&rec&&rec.state==='recording'"):
                break
            await wait(50)
        await resume()
        await mark("turn")
        await wait(250)
        await pg.evaluate("t=>__demo.play(t)", "c892")
        for _ in range(400):
            if (await status()).startswith("ציון"):
                break
            await wait(50)
        await mark("verdict")
        print("  driving:", await status(), "·", await pg.inner_text("#prompt"))
        await wait(1500)
        await mark("driveEnd")
        pause()
        await pg.evaluate("stopDrive()")

        # the grid of other ways to practice, lesson 4: four short examples, each with a still half second at its
        # start and end (the camera moves in and out on those)
        async def path():
            await pg.locator("#nav button", has_text="מסלול").first.click(); await wait(500)

        pause()                                                    # new words: a card, revealed, "ידעתי"
        await pg.locator("button", has_text="חזרה למסלול").first.click(); await wait(600)
        sat = pg.locator("button.sat", has_text="מילים חדשות").nth(3)
        await sat.scroll_into_view_if_needed(); await sat.click(); await wait(1200)
        await resume(); await mark("w0")
        await wait(2000)                                           # the whole grid first, then the camera moves in
        await tap("#learnShow", 1000)
        await tap("#learnKnow", 300)
        await wait(300); await mark("w1")
        pause(); await pg.click("#learnClose"); await wait(500)

        sat = pg.locator("button.sat", has_text="התאימו זוגות").nth(3)        # pairs: two matched
        await sat.scroll_into_view_if_needed(); await sat.click(); await wait(1300)
        await resume(); await mark("p0")
        await wait(350)
        ids = await pg.evaluate("[...document.querySelectorAll('.mtile[data-side=he]')].map(t=>t.dataset.id)")
        for i in ids[:2]:
            await tap(f'.mtile[data-side="he"][data-id="{i}"]', 180)
            await tap(f'.mtile[data-side="tr"][data-id="{i}"]', 500)
        await wait(300); await mark("p1")
        pause(); await pg.click("#matchClose"); await wait(500)

        sat = pg.locator("button.sat", has_text="סיפור: שכני החדש").first      # a story, read aloud line by line
        await sat.scroll_into_view_if_needed(); await sat.click(); await wait(1100)
        await pg.evaluate("scrollTo(0,0)"); await wait(300)
        await resume(); await mark("s0")
        await wait(400)
        await tap("#hearBtn", 0)
        await wait(2200); await mark("s1")
        pause(); await pg.click("#hearBtn"); await wait(300)
        await pg.locator("button", has_text="חזרה למסלול").first.click(); await wait(600)

        await pg.locator("#nav button", has_text="משחקים").first.click(); await wait(600)   # "הפועל זכרון": two pairs
        await pg.locator("#vids button", has_text="הפועל זכרון").first.click(); await wait(1200)
        await resume(); await mark("m0")
        await wait(350)
        for he, ar in [("פתח", "פַתַח"), ("אכל", "אַכַּל")]:
            await tap(f'.mg-bead.he:has-text("{he}")', 200)
            await tap(f'.mg-bead.ar:has-text("{ar}")', 480)
        await wait(750); await mark("m1")                        # the camera moves back out to the grid

        # the summary: "the Arabic that's already yours", what this session collected
        pause()
        await pg.locator(".mg-close").first.click(); await wait(500)
        await pg.evaluate(HOME); await wait(600)          # the home again, its card now counting this session's beads
        await pg.click("#rewardShelf"); await wait(900)
        await resume()
        await mark("shelf")
        await wait(4600)
        await mark("end")
        await cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.3)
        await browser.close()
    return frames, marks


async def render(frames, marks, work, out):
    """Pass 2: promo.html, frame by frame."""
    out_frames = work / "out"; out_frames.mkdir()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(viewport={"width": OW, "height": OH})

        async def serve(route):
            path = route.request.url.split("promo.local/", 1)[1].split("?")[0]
            f = HERE / path if path == "promo.html" or path.startswith("assets/") else work / path
            if path == "logo-intro.svg":
                f = HERE.parents[1] / "static" / "logo-intro.svg"
            await route.fulfill(path=str(f))
        await pg.route("http://promo.local/**", serve)
        await pg.goto("http://promo.local/promo.html")
        await pg.evaluate("async d=>await setup(d)", {"frames": frames, "marks": marks})
        await pg.evaluate("document.fonts.ready")
        total = await pg.evaluate("duration()")
        n = int(total * FPS)
        for i in range(n):
            await pg.evaluate("t=>seek(t)", i / FPS)
            await pg.screenshot(path=str(out_frames / f"{i:05d}.jpg"), type="jpeg", quality=92)
            if i % 150 == 0:
                print(f"  {i}/{n}")
        await browser.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(out_frames / "%05d.jpg"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(out)], check=True)


async def main(a):
    if a.again:                                  # pass 2 only, on the frames of a kept run
        work = Path(a.again)
        frames, marks = json.loads((work / "frames.json").read_text()), json.loads((work / "marks.json").read_text())
        shutil.rmtree(work / "out", ignore_errors=True)
        a.keep = True
    else:
        work = Path(tempfile.mkdtemp(prefix="promo_"))
    try:
        if not a.again:
            frames, marks = await record_screen(a.url, a.takes, work)
            print(f"pass 1: {len(frames)} frames, {marks['end']['t']:.1f}s")
            (work / "marks.json").write_text(json.dumps(marks, indent=1))
            (work / "frames.json").write_text(json.dumps(frames))
        await render(frames, marks, work, a.out)
        print(a.out)
    finally:
        if a.keep:
            print("work kept in", work)
        else:
            shutil.rmtree(work)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:7861/")
    ap.add_argument("--takes")
    ap.add_argument("--out", default="ihki-promo.mp4")
    ap.add_argument("--keep", action="store_true", help="keep the frames (to work on promo.html)")
    ap.add_argument("--again", metavar="WORK_DIR", help="re-render pass 2 from a kept run's frames")
    asyncio.run(main(ap.parse_args()))
