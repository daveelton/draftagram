# /// script
# dependencies = ["playwright"]
# ///
"""Records the README's animations as GIFs, using the Chrome installed on this Mac and ffmpeg.

    uv run docs/animations/record.py chat
    uv run docs/animations/record.py page http://127.0.0.1:PORT/

`chat` plays chat.html. `page` drives a draft's page: it expects the "bypass" draft of the
tone control example (example/), opened with `draft show`.
"""
import pathlib
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent

CURSOR_JS = """
() => {
  if (document.getElementById('rec-cursor')) return;
  const c = document.createElement('div');
  c.id = 'rec-cursor';
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 20 20"><path d="M3 2l13 7-6 1.6L7.6 17z" fill="#fff" stroke="#000" stroke-width="1.2"/></svg>';
  c.style.cssText = 'position:fixed;left:0;top:0;z-index:99999;pointer-events:none;transform:translate(700px,500px)';
  document.body.appendChild(c);
  window.addEventListener('mousemove', e => {
    c.style.transform = `translate(${e.clientX - 3}px,${e.clientY - 2}px)`;
  }, true);
}
"""


class Recorder:
    """Takes screenshots of a page as fast as it can, then joins them into a GIF with their real timings."""

    def __init__(self, page, width):
        self.page = page
        self.width = width
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="draftagram-rec-"))
        self.frames = []

    def shot(self):
        path = self.dir / f"{len(self.frames):05d}.png"
        self.page.screenshot(path=str(path))
        self.frames.append((path, time.monotonic()))

    def hold(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.shot()

    def glide(self, x, y, seconds=0.7):
        """Moves the mouse to x, y smoothly, recording as it goes."""
        start = getattr(self, "at", (700, 500))
        t0 = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - t0) / seconds)
            e = f * f * (3 - 2 * f)
            self.page.mouse.move(start[0] + (x - start[0]) * e, start[1] + (y - start[1]) * e)
            self.shot()
            if f >= 1.0:
                break
        self.at = (x, y)

    def click(self, locator, after=2.0):
        box = locator.bounding_box()
        self.glide(box["x"] + min(box["width"] / 2, 60), box["y"] + box["height"] / 2)
        self.hold(0.25)
        self.page.mouse.click(*self.at)
        self.hold(after)

    def write_gif(self, out):
        listing = self.dir / "frames.txt"
        lines = []
        for i, (path, t) in enumerate(self.frames):
            nxt = self.frames[i + 1][1] if i + 1 < len(self.frames) else t + 0.1
            lines += [f"file '{path}'", f"duration {nxt - t:.3f}"]
        lines.append(f"file '{self.frames[-1][0]}'")
        listing.write_text("\n".join(lines))
        vf = (f"fps=12,scale={self.width}:-1:flags=lanczos,split[a][b];"
              "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-vf", vf, str(out)], check=True)
        print(f"{out}: {out.stat().st_size // 1024} KB, {len(self.frames)} frames")


class ChatRecording:
    def run(self, browser):
        page = browser.new_page(viewport={"width": 900, "height": 520}, device_scale_factor=2)
        page.goto((HERE / "chat.html").as_uri())
        rec = Recorder(page, 900)
        page.evaluate("() => { window.startAnimation(); }")
        while not page.evaluate("window.animationDone === true"):
            rec.shot()
        rec.write_gif(HERE / "chat.gif")


class PageRecording:
    def __init__(self, url):
        self.url = url

    def run(self, browser):
        page = browser.new_page(viewport={"width": 960, "height": 860}, device_scale_factor=2)
        page.goto(self.url)
        # For the recording only: bring the divider up to the diagram, so the side panel gets the room.
        page.add_style_tag(content="#diagram { flex:none !important; width:232px; } #zoom .hint { display:none; }")
        page.wait_for_selector("svg text", timeout=20000)
        page.wait_for_timeout(1500)
        page.evaluate(CURSOR_JS)
        rec = Recorder(page, 960)
        rec.hold(2.0)
        rec.click(page.locator("svg text", has_text="setBypassed").first, after=2.5)
        rec.click(page.locator("svg text", has_text="process(double *").first, after=2.5)
        rec.click(page.locator("svg text", has_text="setCutoff").first, after=2.5)
        rec.click(page.get_by_text("Overview", exact=True).first, after=2.5)
        rec.write_gif(HERE / "page.gif")


if __name__ == "__main__":
    what = sys.argv[1]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        (ChatRecording() if what == "chat" else PageRecording(sys.argv[2])).run(browser)
        browser.close()
