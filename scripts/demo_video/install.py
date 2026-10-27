"""Two short videos on installing the app on the phone: an iPhone (Safari) and an Android phone (Chrome).

    python scripts/demo_video/install.py --out-dir scripts/demo_video

The promo's look (install.html: the 3D phone, the headlines, the logo): the browser's menus are drawn, in Hebrew,
around a real screenshot of the app's first screen, taken here from --url at an iPhone's size.
--frames 3,8.5,12 saves those moments as PNGs only (to check the design without rendering the whole video).
"""
import argparse, asyncio, shutil, subprocess, tempfile
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
STATIC = HERE.parents[1] / "static"
OW, OH, FPS = 1080, 1920, 30


async def shoot(url, out):
    """The app's first screen, at an iPhone 15's CSS size (393x852), twice the pixels."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2, is_mobile=True,
                                        has_touch=True, locale="he-IL", color_scheme="light")
        pg = await ctx.new_page()
        await pg.goto(url, wait_until="networkidle"); await pg.wait_for_timeout(2500)
        await pg.screenshot(path=str(out))
        await browser.close()


async def render(os_, work, out, frames=None):
    files = {"install.html": HERE / "install.html", "app.png": work / "app.png", "logo-intro.svg": STATIC / "logo-intro.svg",
             "icon.png": STATIC / "icons" / "icon-512.png", "icon-maskable.png": STATIC / "icons" / "icon-maskable-512.png"}
    shots = work / f"out-{os_}"; shutil.rmtree(shots, ignore_errors=True); shots.mkdir()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(viewport={"width": OW, "height": OH})

        async def serve(route):
            await route.fulfill(path=str(files[route.request.url.split("install.local/", 1)[1].split("?")[0]]))
        await pg.route("http://install.local/**", serve)
        await pg.goto(f"http://install.local/install.html?os={os_}")
        await pg.evaluate("document.fonts.ready")
        await pg.evaluate("async()=>await setup()")
        if frames:
            for t in frames:
                await pg.evaluate("t=>seek(t)", t)
                await pg.screenshot(path=str(out.with_name(f"{out.stem}-{t:05.2f}.png")))
            await browser.close()
            return
        n = int(await pg.evaluate("duration()") * FPS)
        for i in range(n):
            await pg.evaluate("t=>seek(t)", i / FPS)
            await pg.screenshot(path=str(shots / f"{i:05d}.jpg"), type="jpeg", quality=92)
            if i % 150 == 0:
                print(f"  {os_} {i}/{n}")
        await browser.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(shots / "%05d.jpg"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(out)], check=True)


async def main(a):
    work = Path(tempfile.mkdtemp(prefix="install_"))
    try:
        await shoot(a.url, work / "app.png")
        frames = [float(x) for x in a.frames.split(",")] if a.frames else None
        for os_ in a.os.split(","):
            out = Path(a.out_dir) / f"ihki-install-{'iphone' if os_ == 'ios' else 'android'}.mp4"
            await render(os_, work, out, frames)
            print(out)
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://wzender-ihki.hf.space/")
    ap.add_argument("--os", default="ios,android")
    ap.add_argument("--out-dir", default=str(HERE))
    ap.add_argument("--frames", help="only these moments (seconds), as PNGs")
    asyncio.run(main(ap.parse_args()))
