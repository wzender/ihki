"""Records the students' feature video: a phone-sized (1080x1920), silent walkthrough of the app with Hebrew captions.

    python scripts/demo_video/record.py --takes <dir> --out demo.mp4 [--sound] [--voice <dir>] [--music 0.22 | --music-file f]

--sound (optional): the app's own sounds (the student's takes, the native answer, the word cards, the story).

The app should run with sign-in off (empty SUPABASE_* variables) so no demo progress reaches the real database.
--takes: m449.wav / c449.wav from make_takes.py. The "student" is the TTS voice: m449 says فتح، يفتح with ه
for ح (the ח swallowed into a soft ה), c449 says it right. The scores in the video are the real model's.
--voice (optional): a voiceover from make_voice.py (one WAV per line of script.py, and durations.json); each scene
then waits for its line. Without it, each caption stays up long enough to read.
--music / --music-file (optional): music.py's loop at that level, or your own track, ducked under speech.

Frames come from Chrome's screencast (sharper than Playwright's built-in video), then ffmpeg encodes them and
mixes the sound.
"""
import argparse, asyncio, base64, json, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from script import LINES

W, H, DPR, FPS = 432, 768, 2.5, 30          # 9:16 viewport -> 1080x1920 frames
LOGO_SECONDS = 7.4                          # static/logo-intro.svg's animation, to the dots' last bounce


def real_library():
    """The real library's list (titles and players), read from Supabase with the keys in .env, so the video shows
    what students see; the local app, with sign-in off, has none. None when it can't be read."""
    try:
        sys.path.insert(0, str(HERE.parents[1]))
        from dotenv import load_dotenv
        load_dotenv(HERE.parents[1] / ".env")
        from app.auth import load_auth
        from app.main import video_embed
        rows = load_auth().level_videos("library")
        return [{"id": v["id"], "title": v["title"], "embed": video_embed(v["url"])} for v in rows]
    except Exception as e:
        print("library: not read:", e)
        return None


def hold(text, sub):
    """How long a caption stays up without a voice: enough to read it."""
    return max(1.8, 0.5 + 0.045 * len(text + sub)) if text else 1.0


class Recorder:
    def __init__(self, cdp, frames_dir):
        self.cdp, self.dir = cdp, frames_dir
        self.frames = []                # (path, chrome timestamp, cut_before)
        self.cues = []                  # (wall time, wav path, gain): sounds to lay on the video
        self.paused, self.cut = False, False
        self.paused_at = {}             # frame index after a pause -> when the pause began (wall clock)

    async def start(self):
        self.cdp.on("Page.screencastFrame", lambda f: asyncio.ensure_future(self._frame(f)))
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92,
                                                     "maxWidth": int(W * DPR), "maxHeight": int(H * DPR)})

    async def stop(self):
        await self.cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.3)

    async def _frame(self, f):
        await self.cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})
        if self.paused:
            return
        p = self.dir / f"{len(self.frames):06d}.jpg"
        p.write_bytes(base64.b64decode(f["data"]))
        if self.cut:
            self.paused_at[len(self.frames)] = self.pause_began
        self.frames.append((p, f["metadata"]["timestamp"], self.cut))
        self.cut = False

    def pause(self):
        self.paused, self.pause_began = True, time.time()

    def resume(self):
        self.paused, self.cut = False, True

    def cue(self, wav, gain=1.0):
        self.cues.append((time.time(), Path(wav), gain))

    def video_time(self, t):
        """A wall-clock moment on the video's timeline: from the first frame, minus the paused stretches."""
        v = t - self.frames[0][1]
        for i, (_, ts, cut) in enumerate(self.frames):
            began = self.paused_at.get(i, ts)
            if cut and i and t > began:          # a sound just after the resume lands at the cut, not in the gap
                v -= max(0.0, min(t, ts) - began)
        return max(0.0, v)

    def encode(self, out):
        # Chrome sends a frame only when the screen changes; each frame lasts until the next one.
        # Across a pause the gap is dropped, so the skipped part doesn't show as a freeze.
        lines = ["ffconcat version 1.0"]
        for i, (p, ts, _) in enumerate(self.frames):
            nxt = self.frames[i + 1] if i + 1 < len(self.frames) else None
            # before a pause, the frame lasts until the pause began (a still screen sends no new frames)
            end = None if nxt is None else (self.paused_at[i + 1] if nxt[2] else nxt[1])
            dur = 1.5 if end is None else max(end - ts, 0.001)
            lines += [f"file '{p}'", f"duration {dur:.4f}"]
        lines.append(f"file '{self.frames[-1][0]}'")
        lst = self.dir / "list.txt"
        lst.write_text("\n".join(lines))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", f"fps={FPS},scale={int(W * DPR)}:{int(H * DPR)}:flags=lanczos,format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "18", str(out)], check=True)

    def mix(self, silent, out, music_gain, music_file=None):
        length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                       "csv=p=0", str(silent)], capture_output=True, text=True).stdout)
        music = self.dir / "music.wav"
        if not music_gain and not music_file:
            music = None
        elif music_file:      # your own track: looped to the video's length, faded out at the end
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(music_file),
                            "-t", f"{length:.2f}", "-af", f"afade=t=out:st={max(0, length - 3):.2f}:d=3",
                            "-ar", "44100", "-ac", "2", str(music)], check=True)
        else:
            subprocess.run([sys.executable, str(HERE / "music.py"), str(music), f"{length:.2f}"], check=True,
                           stdout=subprocess.DEVNULL)
        args, chains = ["-i", str(silent), "-i", str(music or silent)], []
        for k, (t, wav, gain) in enumerate(self.cues):
            ms = int(self.video_time(t) * 1000)
            print(f"  {ms / 1000:5.1f}s on the video: {wav.name}")
            args += ["-i", str(wav)]
            chains.append(f"[{k + 2}:a]aformat=sample_rates=48000:channel_layouts=mono,"
                          f"adelay={ms}:all=1,volume={gain}[c{k}]")
        n = len(self.cues)
        graph = ";".join((chains + [
            "".join(f"[c{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,"
            f"apad=whole_dur={length:.2f},aformat=channel_layouts=stereo,asplit=2[voice][key]",
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={music_gain or 1}[bed]",
            "[bed][key]sidechaincompress=threshold=0.015:ratio=6:attack=30:release=450[ducked]",
            "[voice][ducked]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"]
            if music else chains + [
            "".join(f"[c{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,"
            f"apad=whole_dur={length:.2f},aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"]))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", graph,
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-ac", "2", "-shortest", "-movflags", "+faststart", str(out)], check=True)


async def run(url, takes, voice, out, music_gain, music_file, sound, keep_frames):
    frames_dir = Path(tempfile.mkdtemp(prefix="demo_frames_"))
    durations = json.loads((Path(voice) / "durations.json").read_text()) if voice else {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DPR, is_mobile=True,
                                        has_touch=True, locale="he-IL", color_scheme="light", permissions=["microphone"])
        await ctx.add_init_script(path=str(HERE / "overlay.js"))
        await ctx.route("**/__demo/*.wav", lambda r: r.fulfill(
            path=str(Path(takes) / r.request.url.split("/")[-1]), content_type="audio/wav"))
        pg = await ctx.new_page()
        rec = Recorder(await ctx.new_cdp_session(pg), frames_dir)
        wait = pg.wait_for_timeout
        t0, speech_end = time.monotonic(), [0.0]

        def app_sound(key, url):
            """The page started playing one of its sounds (the native answer, a word, a story line): laid on
            the video at this moment. Its data comes right after (some are blob: URLs, readable only in the page)."""
            rec.cues.append((time.time(), frames_dir / f"sound{key}.audio", 0.9))
            print(f"{time.monotonic() - t0:5.1f}s  ♪ {url.split('/', 3)[-1][:40]}")

        def app_sound_data(key, data_url):
            (frames_dir / f"sound{key}.audio").write_bytes(base64.b64decode(data_url.split(",", 1)[1]))

        await pg.expose_function("__demoSound", app_sound)
        await pg.expose_function("__demoSoundData", app_sound_data)
        await pg.add_init_script("""(()=>{ const play=HTMLMediaElement.prototype.play; let n=0;
          HTMLMediaElement.prototype.play=function(){
            const src=this.currentSrc||this.src;
            if(src&&!src.includes("__demo")){ const key=n++; window.__demoSound(key, src);
              fetch(src).then(r=>r.blob()).then(b=>new Promise(ok=>{ const f=new FileReader(); f.onload=()=>ok(f.result); f.readAsDataURL(b); }))
                .then(d=>window.__demoSoundData(key, d)); }
            return play.apply(this, arguments); }; })()""")

        async def line(key):
            """Shows the scene's caption and starts its voiceover line, if there is a voice."""
            text, sub, pos, _ = LINES[key]
            print(f"{time.monotonic() - t0:5.1f}s  {key}: {text}")
            if text:
                await pg.evaluate("a=>__demo.caption(...a)", [text, sub, pos])
            if voice:
                rec.cue(Path(voice) / f"{key}.wav")
                speech_end[0] = time.monotonic() + durations[key]
            else:                                # time to read the caption
                speech_end[0] = time.monotonic() + hold(text, sub)

        async def spoken(pad=0.3):
            """Waits for the voiceover line to end (without a voice: for the caption to be read)."""
            left = speech_end[0] + pad - time.monotonic()
            if left > 0:
                await asyncio.sleep(left)

        async def tap(loc, pause=350):
            loc = pg.locator(loc).first if isinstance(loc, str) else loc
            await loc.scroll_into_view_if_needed()
            box = await loc.bounding_box()
            await pg.evaluate("a=>__demo.tap(...a)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
            await wait(180)
            await loc.click()
            await wait(pause)

        async def point(sel):
            """A tap ripple without the tap: draws the eye to a button."""
            box = await pg.locator(sel).first.bounding_box()
            await pg.evaluate("a=>__demo.tap(...a)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])

        async def say(take):
            """Taps the mic with this take as the student's voice, and waits for the score."""
            await pg.evaluate("document.getElementById('result').hidden=true")
            # the app may already be listening by itself (it does after "next" and after a retry prompt)
            if await pg.evaluate("typeof rec!=='undefined'&&rec&&rec.state==='recording'"):
                await point("#mic"); await wait(150)
            else:
                await tap("#mic", 150)
            await pg.evaluate("t=>__demo.play(t)", take)
            rec.cue(Path(takes) / f"{take}.wav")
            try:
                await pg.wait_for_selector("#result:not([hidden])", timeout=30000)
            except Exception:
                await pg.screenshot(path=str(frames_dir / "stuck.png"))
                sys.exit(f"no score for {take}; status: {await pg.inner_text('#status')!r}; see {frames_dir / 'stuck.png'}")

        PERMS = "ME_PERMISSIONS=new Set(['practice.use','exercises.use','path.use','games.use','tutor.use','library.view'])"
        library = real_library()                 # the page loads it only when signed in, so it is handed over below

        await pg.goto(url, wait_until="networkidle")
        await rec.start()

        # 1 · the opening logo
        await wait(1200)
        await line("intro"); await spoken(0.5)
        await pg.evaluate(PERMS)                 # a student's permissions (the page loads them only on sign-in)
        await tap("#practiceBtn", 700)
        if await pg.locator("#guideTipClose").is_visible():
            await pg.click("#guideTipClose")

        # 2 · beads: what they are
        await line("beads")
        await wait(600); await point("#rewardShelf"); await wait(1200); await point("#rewardShelf")
        await spoken()

        # 3 · practicing a topic: a sentence, the mistake and its tip, the retry, a gold bead
        rec.pause()
        await pg.evaluate("__demo.caption('')")
        await pg.locator("button.sat", has_text="פעלים").first.click()
        await wait(800)
        for _ in range(40):
            if (await pg.inner_text("#prompt")).strip().startswith("פתח"):
                break
            await pg.click("#nextBtn"); await wait(300)
        await pg.evaluate("scrollTo(0,0)"); await wait(500)
        rec.resume()
        await line("say"); await spoken(0.2)
        await say("m449")
        print("  score:", await pg.inner_text("#score"), "|", (await pg.inner_text("#wordReveals"))[:80].replace("\n", " "))
        await wait(400)
        await line("colors"); await spoken(0.2)
        await line("tip")
        await wait(1200)
        await tap(pg.locator("#wordReveals .bad.why", has_text="ח").first, 0)
        await spoken()
        await wait(2400)                         # time to read the tip
        await pg.mouse.click(5, 5)               # closes the letter's bubble
        await line("listen"); await spoken(0.1)
        await tap("#speakBtn", 1900)             # the native answer
        await line("again"); await spoken(0.1)
        await say("c449")
        await pg.evaluate("cancelAutoNext()")    # keeps the 100 and its gold bead on screen
        await wait(300)
        await line("gold"); await spoken(0.6)

        # 4 · a story: the whole text, read aloud line by line
        rec.pause()
        await pg.evaluate("__demo.caption('')")
        await pg.locator("button", has_text="חזרה למסלול").first.click(); await wait(600)
        await pg.locator("button.sat", has_text="סיפור: חברתי רובה").first.click(); await wait(900)
        await pg.evaluate("scrollTo(0,0)"); await wait(400)
        rec.resume()
        await line("story")
        await wait(700)
        await tap("#hearBtn", 0)
        await spoken(); await wait(1500)
        await tap("#hearBtn", 300)               # pauses the reading

        # 5 · "the Arabic that's already yours": streak, weekly goal, the beads collected
        rec.pause()
        await pg.evaluate("__demo.caption('')")
        await pg.locator("button", has_text="חזרה למסלול").first.click(); await wait(600)
        await pg.evaluate("scrollTo({top:0})"); await wait(300)
        rec.resume()
        await line("shelf")
        await tap("#rewardShelf", 0)
        await spoken(); await wait(1500)
        await tap(pg.locator("dialog[open] button", has_text="סגירה").first, 500)

        # 6 · daily practice: a round of ten sentences
        await line("daily")
        await tap(pg.locator("#today button", has_text="תרגול יומי").first, 0)
        await spoken(); await wait(800)

        # 7 · the digital library (its own door on the opening screen)
        rec.pause()
        await pg.evaluate("__demo.caption('')")
        await pg.goto(url, wait_until="networkidle"); await wait(1500)
        await pg.evaluate(PERMS)
        if library:
            await pg.evaluate("l=>{LIBRARY=l}", library)
        await pg.click("#libraryBtn"); await wait(2500)
        rec.resume()
        await line("library")
        await spoken(); await wait(1500)

        # 8 · games: a round of the memory game (on lesson 1's four verbs)
        await line("memory")
        await pg.evaluate(PERMS)
        await tap(pg.locator("#nav button", has_text="משחקים").first, 500)
        await tap(pg.locator("#vids button", has_text="הפועל זכרון").first, 900)
        pairs = [("פתח", "פַתַח"), ("אכל", "אַכַּל"), ("שאל", "סַאַל"), ("ישב", "קַעַד")]
        await tap(pg.locator(".mg-bead.he", has_text=pairs[0][0]).first, 350)      # a wrong pair first
        await tap(pg.locator(".mg-bead.ar", has_text=pairs[1][1]).first, 1100)
        for he, ar in pairs:
            await tap(pg.locator(".mg-bead.he", has_text=he).first, 300)
            await tap(pg.locator(".mg-bead.ar", has_text=ar).first, 650)
        await spoken(); await wait(1500)

        # 9 · closing card
        await pg.evaluate("__demo.caption('')")
        await pg.evaluate("""__demo.card(`<img src="/static/logo-intro.svg?v=${Date.now()}" alt="">
            <h2>יאללה, מתחילים!</h2><p>פותחים את האפליקציה<br>ומתרגלים כמה דקות ביום</p>`)""")
        await wait(700)
        await line("outro"); await spoken()
        await wait(2400)

        await rec.stop()
        await wait(500)                          # the last sounds' data
        rec.cues = [c for c in rec.cues if c[1].exists()]
        await browser.close()

    silent = frames_dir / "silent.mp4"
    rec.encode(silent)
    if sound or voice or music_gain or music_file:
        rec.mix(silent, out, music_gain, music_file)
    else:                                        # the default: a silent video
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-c", "copy",
                        "-movflags", "+faststart", str(out)], check=True)
    print(f"{out}: {len(rec.frames)} frames, {len(rec.cues)} sounds")
    if keep_frames:
        print("frames kept in", frames_dir)
    else:
        shutil.rmtree(frames_dir)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:7861/")
    ap.add_argument("--takes", required=True)
    ap.add_argument("--sound", action="store_true", help="the app's own sounds (default: a silent video)")
    ap.add_argument("--voice", help="a voiceover from make_voice.py (default: captions only)")
    ap.add_argument("--out", default="demo.mp4")
    ap.add_argument("--music", type=float, default=0, help="music.py's loop at this level, e.g. 0.22 (default: none)")
    ap.add_argument("--music-file", help="your own music instead of music.py's (looped and faded to fit)")
    ap.add_argument("--keep-frames", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.url, a.takes, a.voice, a.out, a.music, a.music_file, a.sound, a.keep_frames))
