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

        def resume():
            paused["skipped"] += time.time() - paused["at"]; paused["on"] = False

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

        async def say(take):
            await pg.evaluate("document.getElementById('result').hidden=true")
            if await pg.evaluate("typeof rec!=='undefined'&&rec&&rec.state==='recording'"):
                b = await pg.locator("#mic").bounding_box()       # the app is already listening by itself
                await pg.evaluate("a=>__demo.tap(...a)", [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2])
            else:
                await tap("#mic", 0)
            await wait(150)
            await pg.evaluate("t=>__demo.play(t)", take)
            await pg.wait_for_selector("#result:not([hidden])", timeout=30000)

        # off camera: into the topic "פעלים" of lesson 4, on "דיבר (עבר, עתיד)"
        await pg.goto(url, wait_until="networkidle"); await wait(1500)
        await pg.evaluate("ME_PERMISSIONS=new Set(['practice.use','exercises.use','path.use','games.use'])")
        await pg.click("#practiceBtn"); await wait(800)
        if await pg.locator("#guideTipClose").is_visible():
            await pg.click("#guideTipClose")
        await pg.evaluate("pickCat('l4-verbs')"); await wait(800)
        for _ in range(8):
            if (await pg.inner_text("#prompt")).strip().startswith("דיבר"):
                break
            await pg.click("#nextBtn"); await wait(300)
        await pg.evaluate("scrollTo(0,0)"); await wait(600)

        cdp.on("Page.screencastFrame", lambda f: asyncio.ensure_future(frame(f)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90,
                                                "maxWidth": SW * SDPR, "maxHeight": SH * SDPR})
        while t0[0] is None:                    # the first frame sets the clock
            await asyncio.sleep(0.05)
        await mark("start")
        await wait(1600)
        await mark("mic", "#mic")
        await say("m876")
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
        await say("c876")
        await pg.evaluate("cancelAutoNext()")    # keeps the score on screen
        await mark("great", "#result")
        print("  retry:", await pg.inner_text("#score"))
        await wait(5200)                         # the 100, then the beads headline over the gold bead

        # driving mode on the same verb: the app reads it out, says "תורכם" and listens, then says the verdict
        pause()
        await pg.evaluate("store.set('ihki.driveSeen',true)")      # past the first-time explanation
        await pg.click("#driveBtn"); await wait(150)
        resume()
        await mark("drive", "#driveBtn")
        status = lambda: pg.inner_text("#status")
        for _ in range(200):
            if (await status()).startswith("תורכם"):
                break
            await wait(50)
        await mark("turn")
        await wait(500)
        await pg.evaluate("t=>__demo.play(t)", "c876")
        for _ in range(400):
            if (await status()).startswith("ציון"):
                break
            await wait(50)
        await mark("verdict")
        print("  driving:", await status())
        await wait(2300)
        await mark("driveEnd")
        pause()
        await pg.evaluate("stopDrive()")

        # off camera: to the games, into "הפועל זכרון" (lesson 1's four verbs)
        await pg.locator("button", has_text="חזרה למסלול").first.click(); await wait(600)
        await pg.locator("#nav button", has_text="משחקים").first.click(); await wait(600)
        await pg.locator("#vids button", has_text="הפועל זכרון").first.click(); await wait(1200)
        resume()
        await mark("memory")
        await wait(400)
        for he, ar in [("פתח", "פַתַח"), ("אכל", "אַכַּל")]:   # two pairs: a glimpse, not the whole game
            await tap(f'.mg-bead.he:has-text("{he}")', 200)
            await tap(f'.mg-bead.ar:has-text("{ar}")', 480)
        await wait(900)
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
            f = HERE / path if path in ("promo.html", "logo-intro.svg") else work / path
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
