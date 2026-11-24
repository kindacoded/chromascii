"""Play any video or GIF as full-color ASCII art in your terminal, or export it as a video.

    python chromascii.py clip.mp4
    python chromascii.py dance.gif --mode blocks --loop
    python chromascii.py clip.mp4 -o clip_ascii.mp4
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


def char_index(img, n, boost=1.0):
    """Index into an n-character charset for every pixel, by brightness."""
    lum = img @ np.array([0.299, 0.587, 0.114])
    lo, hi = np.percentile(lum, [2, 98])  # stretch contrast per frame so dull footage still uses the whole charset
    norm = np.clip((lum - lo) / max(hi - lo, 1) * boost, 0, 1)
    return (norm * (n - 1)).round().astype(int)


def render_ascii(img, charset, boost=1.0, bg=0.0):
    """bg > 0 fills each cell's background with a dimmed copy of its color, so gaps between characters aren't black."""
    chars = np.array(list(charset))[char_index(img, len(charset), boost)]
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


FONTS = ["consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc", "cour.ttf"]


def glyph_atlas(charset, size=14):
    """Pre-draw every character once as a (len, cell_h, cell_w) coverage mask, so frames are pure numpy."""
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for name in FONTS:
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default(size)
    cw, ch = round(font.getlength("M")), round(size * 1.2) // 2 * 2  # even height so blocks split cleanly
    ascent, descent = font.getmetrics()
    atlas = np.zeros((len(charset), ch, cw), np.float32)
    for i, c in enumerate(charset):
        im = Image.new("L", (cw, ch))
        ImageDraw.Draw(im).text((0, (ch - ascent - descent) // 2), c, font=font, fill=255)
        atlas[i] = np.asarray(im, np.float32) / 255
    return atlas, cw, ch


def image_ascii(img, atlas, boost=1.0, bg=0.0):
    """Frame -> RGB picture of colored characters, (h*cell_h, w*cell_w, 3)."""
    h, w = img.shape[:2]
    _, ch, cw = atlas.shape
    mask = atlas[char_index(img, len(atlas), boost)][..., None]  # (h, w, ch, cw, 1)
    fg = img[:, :, None, None, :].astype(np.float32)
    out = fg * bg + fg * (1 - bg) * mask
    return out.transpose(0, 2, 1, 3, 4).reshape(h * ch, w * cw, 3).astype(np.uint8)


def image_blocks(img, cw, ch):
    """Frame -> RGB picture where every pixel is half a character cell."""
    return np.repeat(np.repeat(img, ch // 2, 0), cw, 1)


def export(a, charset, src_w, src_h, fps):
    atlas, cw, ch = glyph_atlas(charset if a.mode == "ascii" else " ", a.font_size)
    per_row = 2 if a.mode == "blocks" else 1
    w = a.width or max(20, src_w // cw)  # default: about as wide as the source video
    h = max(per_row, round(src_h / src_w * w * cw / ch * per_row))
    h -= h % per_row
    out_w, out_h = w * cw, h // per_row * ch
    gif = a.output.lower().endswith(".gif")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}",
           "-r", str(fps), "-i", "-"]
    if gif:
        cmd += ["-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse", a.output]
    else:
        cmd += ["-i", a.input, "-map", "0:v", "-map", "1:a?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-shortest", a.output]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    try:
        for img in frames(a.input, w, h, fps):
            pic = image_blocks(img, cw, ch) if a.mode == "blocks" else image_ascii(img, atlas, a.boost, a.bg)
            enc.stdin.write(pic.tobytes())
            n += 1
            print(f"\rframe {n}", end="", file=sys.stderr, flush=True)
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0 or n == 0:
        sys.exit("\nExport failed")
    print(f"\nSaved {a.output} ({out_w}x{out_h}, {n} frames, {w} characters wide)", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Play a video or GIF as colored ASCII art.")
    ap.add_argument("input", help="video or GIF file (anything ffmpeg can read)")
    ap.add_argument("-w", "--width", type=int, help="width in characters (default: fit the terminal)")
    ap.add_argument("-m", "--mode", choices=["ascii", "blocks"], default="ascii",
                    help="ascii = colored characters, blocks = half-block pixels (sharper)")
    ap.add_argument("-c", "--charset", default="classic",
                    help=f"one of {', '.join(CHARSETS)}, or your own characters from dark to bright")
    ap.add_argument("--fps", type=float, help="frame rate (default: the video's, max 30)")
    ap.add_argument("-o", "--output", help="save as .mp4 (with the original audio) or .gif instead of playing")
    ap.add_argument("--font-size", type=int, default=14, help="export only: character size in pixels")
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
    if a.output:
        return export(a, charset, src_w, src_h, fps)
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
