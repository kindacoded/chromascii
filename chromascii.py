"""Play any video or GIF as full-color ASCII art in your terminal.

    python chromascii.py clip.mp4
    python chromascii.py dance.gif --mode blocks --loop
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

CHARSETS = {
    "classic": " .:-=+*#%@",
    "dense": " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "blocks": " ░▒▓█",
}
CHAR_ASPECT = 0.5  # terminal cells are about twice as tall as they are wide


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"Can't read {path}: {out.stderr.strip() or 'ffprobe failed'}")
    s = json.loads(out.stdout)["streams"][0]
    num, den = (s.get("avg_frame_rate") or "0/0").split("/")
    fps = float(num) / float(den) if float(den or 0) else 0
    return s["width"], s["height"], fps or 24.0


def frames(path, w, h, fps):
    """Yield (h, w, 3) uint8 frames, already scaled by ffmpeg."""
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        stdout=subprocess.PIPE)
    size = w * h * 4
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                return
            px = np.frombuffer(buf, np.uint8).reshape(h, w, 4).astype(np.uint16)
            # transparent areas (GIFs) go black instead of whatever color hides behind them
            yield (px[..., :3] * px[..., 3:] // 255).astype(np.uint8)
    finally:
        proc.kill()


def render_ascii(img, charset, boost=1.0, bg=0.0):
    """bg > 0 fills each cell's background with a dimmed copy of its color, so gaps between characters aren't black."""
    lum = img @ np.array([0.299, 0.587, 0.114])
    lo, hi = np.percentile(lum, [2, 98])  # stretch contrast per frame so dull footage still uses the whole charset
    norm = np.clip((lum - lo) / max(hi - lo, 1) * boost, 0, 1)
    chars = np.array(list(charset))[(norm * (len(charset) - 1)).round().astype(int)]
    lines = []
    if bg > 0:
        back = (img * bg).astype(np.uint8)
        for row_c, row_px, row_bg in zip(chars, img.tolist(), back.tolist()):
            lines.append("".join(f"\x1b[38;2;{r};{g};{b}m\x1b[48;2;{x};{y};{z}m{c}"
                                 for c, (r, g, b), (x, y, z) in zip(row_c, row_px, row_bg)))
    else:
        for row_c, row_px in zip(chars, img.tolist()):
            lines.append("".join(f"\x1b[38;2;{r};{g};{b}m{c}" for c, (r, g, b) in zip(row_c, row_px)))
    return "\x1b[0m\n".join(lines) + "\x1b[0m"


def render_blocks(img):
    """Two pixels per cell: top half is the text color, bottom half the background."""
    if img.shape[0] % 2:
        img = img[:-1]
    lines = []
    for top, bot in zip(img[0::2].tolist(), img[1::2].tolist()):
        lines.append("".join(f"\x1b[38;2;{a};{b};{c}m\x1b[48;2;{d};{e};{f}m▀"
                             for (a, b, c), (d, e, f) in zip(top, bot)))
    return "\x1b[0m\n".join(lines) + "\x1b[0m"


def main():
    ap = argparse.ArgumentParser(description="Play a video or GIF as colored ASCII art.")
    ap.add_argument("input", help="video or GIF file (anything ffmpeg can read)")
    ap.add_argument("-w", "--width", type=int, help="width in characters (default: fit the terminal)")
    ap.add_argument("-m", "--mode", choices=["ascii", "blocks"], default="ascii",
                    help="ascii = colored characters, blocks = half-block pixels (sharper)")
    ap.add_argument("-c", "--charset", default="classic",
                    help=f"one of {', '.join(CHARSETS)}, or your own characters from dark to bright")
    ap.add_argument("--fps", type=float, help="playback frame rate (default: the video's, max 30)")
    ap.add_argument("--loop", action="store_true", help="loop until Ctrl+C")
    ap.add_argument("--boost", type=float, default=1.0, help="brighten the character choice, e.g. 1.5")
    ap.add_argument("--bg", type=float, default=0.35,
                    help="ascii mode: background fill strength, 0 = black background, 1 = full color")
    a = ap.parse_args()

    if not os.path.isfile(a.input):
        sys.exit(f"No such file: {a.input}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg is required: https://ffmpeg.org/download.html (or `winget install Gyan.FFmpeg`)")
    charset = CHARSETS.get(a.charset, a.charset)
    if len(charset) < 2:
        sys.exit("--charset needs at least 2 characters")

    src_w, src_h, src_fps = probe(a.input)
    fps = a.fps or min(src_fps, 30)
    cols, rows = shutil.get_terminal_size((100, 30))
    w = a.width or cols
    # pixel rows per text row: 1 for ascii, 2 for blocks
    per_row = 2 if a.mode == "blocks" else 1
    h = max(per_row, round(src_h / src_w * w * CHAR_ASPECT * per_row))
    if not a.width and h / per_row > rows - 1:  # fit the window height too
        h = (rows - 1) * per_row
        w = max(1, round(h / per_row / CHAR_ASPECT * src_w / src_h))
    h -= h % per_row

    if os.name == "nt":
        os.system("")  # turn on ANSI colors in the classic Windows console
    out = sys.stdout
    out.reconfigure(encoding="utf-8")  # block characters fail on legacy code pages
    out.write("\x1b[?25l\x1b[2J")  # hide cursor, clear screen
    try:
        while True:
            start, n = time.perf_counter(), 0
            for img in frames(a.input, w, h, fps):
                text = render_blocks(img) if a.mode == "blocks" else render_ascii(img, charset, a.boost, a.bg)
                out.write("\x1b[H" + text)
                out.flush()
                n += 1
                delay = start + n / fps - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            if not a.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\x1b[0m\x1b[?25h\n")  # reset colors, show cursor


if __name__ == "__main__":
    main()
