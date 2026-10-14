"""Screen Central studio-logo intro for countdowns (owner, 2026-10-13).

Modelled on the Marvel Studios intro the owner sent, with Screen Central branding and
the countdown's own posters (~9.4 s):
  A 0.0–3.6  poster storm — the countdown's posters flip in like pages (squash + shade),
             each washed in a colour, titles typed over them, page-flip sound
  B 3.6–4.4  flash — a fire/light burst
  C 4.4–7.4  fly-through — "SCREEN CENTRAL" letters filled with the posters, extruded
             red edges and a silver rim, the camera pulling back from inside a letter
  D 7.4–9.4  lock-up — red bar logo + "TOP N · topic", with a shine sweep
Frames are drawn with PIL and encoded with ffmpeg (motion blur, sound mix); no AI cost.

Sound: `assets/intro/page_flips.wav` is lifted from the owner's reference clip (Marvel's
official intro — copyrighted; Content ID can claim it). It is the only borrowed sound and a
single file: replace it with any royalty-free page-flip sound to remove that risk. The
flash whoosh and the logo impact are synthesised here.
"""

from __future__ import annotations

import math
import pathlib
import random
import shutil
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import media

W, H, FPS = 1920, 1080, 30
ASSETS = pathlib.Path(__file__).resolve().parents[3] / "assets" / "intro"
FONT = ASSETS / "Anton-Regular.ttf"
FLIPS = ASSETS / "page_flips.wav"
WASHES = [(229, 9, 20), (255, 120, 0), (30, 90, 255), (140, 40, 220), (0, 170, 120), (255, 40, 120)]
RED = (196, 12, 22)
A_END, B_END, C_END, D_END = 3.6, 4.4, 7.4, 9.4
LENGTH = D_END


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return media.font(size)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.4))


def _wash(img: Image.Image, color: tuple[int, int, int], amount: float = 0.55) -> Image.Image:
    """Colour-grade like the reference: luminance kept, hue pulled to the wash colour."""
    g = ImageOps.grayscale(img).convert("RGB")
    tint = Image.new("RGB", img.size, color)
    graded = ImageChops.multiply(ImageChops.screen(g, Image.new("RGB", img.size, (40, 40, 40))), tint)
    graded = ImageEnhance.Contrast(graded).enhance(1.35)
    return Image.blend(img, graded, amount)


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _load(images: Iterable[pathlib.Path]) -> list[Image.Image]:
    out = []
    for p in images:
        try:
            with Image.open(p) as im:
                out.append(im.convert("RGB").copy())
        except Exception:  # noqa: BLE001 — a missing poster just isn't used
            continue
    if not out:
        out = [Image.new("RGB", (800, 1200), (40, 8, 10))]
    return out


# --- A: poster storm ---------------------------------------------------------------------
def _storm_frames(imgs: list[Image.Image], titles: list[str]) -> Iterable[Image.Image]:
    n_frames = round(A_END * FPS)
    cards = max(6, min(10, len(imgs) * 2))
    per = n_frames / cards
    big = _font(150)
    rnd = random.Random(7)
    prepared = []
    for k in range(cards):
        src = imgs[k % len(imgs)]
        base = _wash(_cover(src, W, H), WASHES[k % len(WASHES)], 0.6)
        prepared.append((base, titles[k % len(titles)].upper() if titles else "", rnd.uniform(-0.02, 0.02)))
    for f in range(n_frames):
        k = min(int(f / per), cards - 1)
        local = (f - k * per) / per                       # 0..1 within this card
        base, title, tilt = prepared[k]
        prev = prepared[k - 1][0] if k else Image.new("RGB", (W, H), (10, 4, 4))
        z = 1.0 + 0.08 * local                            # slow push-in
        cw, ch = int(W / z), int(H / z)
        cur = base.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
        frame = prev.copy()
        turn = _ease(local / 0.35)                        # page turning in over the first third
        if turn < 1:
            pw = max(1, int(W * turn))
            page = cur.resize((pw, H), Image.BILINEAR)
            shade = Image.new("RGB", page.size, (0, 0, 0))
            page = Image.blend(page, shade, 0.45 * (1 - turn))
            frame.paste(page, (W - pw, 0))
            d = ImageDraw.Draw(frame)                     # the page edge
            d.rectangle((W - pw - 6, 0, W - pw, H), fill=(245, 240, 230))
        else:
            frame = cur
        if title:                                         # typed title, like the names in the reference
            shown = title[: max(1, int(len(title) * _ease(local / 0.8)))]
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((90, H - 260), shown, font=big, fill=(255, 255, 255, 150))
            frame = Image.alpha_composite(frame.convert("RGBA"), layer.rotate(math.degrees(tilt), resample=Image.BILINEAR)).convert("RGB")
        yield frame


# --- B: flash -------------------------------------------------------------------------------
def _flash_frames(last: Image.Image) -> Iterable[Image.Image]:
    """A white-hot core with orange fire and light streaks bursting outward, then down to deep red."""
    n = round((B_END - A_END) * FPS)
    rnd = random.Random(11)
    rays = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(0.6, 1.0)) for _ in range(28)]
    for f in range(n):
        t = f / max(1, n - 1)
        e = _ease(t)
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(glow)
        for r, col in ((int(200 + 1300 * e), (255, 90, 10)), (int(120 + 900 * e), (255, 170, 60)), (int(40 + 500 * e), (255, 250, 235))):
            d.ellipse((W // 2 - r, H // 2 - r * 0.7, W // 2 + r, H // 2 + r * 0.7), fill=col)
        for ang, ln in rays:                              # streaks
            L = (300 + 1500 * e) * ln
            d.line((W // 2, H // 2, W // 2 + L * math.cos(ang), H // 2 + L * math.sin(ang) * 0.7), fill=(255, 220, 160), width=10)
        glow = glow.filter(ImageFilter.GaussianBlur(40 + 40 * e))
        frame = ImageChops.screen(last, glow)
        if t > 0.55:                                      # fade to the deep red the letters sit on
            frame = Image.blend(frame, Image.new("RGB", (W, H), (60, 4, 8)), (t - 0.55) / 0.45)
        yield frame


# --- C + D: letters, then the lock-up ----------------------------------------------------------
def _letters_layers(imgs: list[Image.Image], scale: int) -> tuple[Image.Image, Image.Image, Image.Image]:
    """At `scale`× resolution: the poster strip, the letter mask and the extruded-edge layer."""
    cw, ch = W * scale, H * scale
    font = _font(int(255 * scale))                        # fits the frame with a margin at the end
    text = "SCREEN CENTRAL"
    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
    x0, y0, x1, y1 = tmp.textbbox((0, 0), text, font=font)
    tw, th = x1 - x0, y1 - y0
    ox, oy = (cw - tw) // 2 - x0, (ch - th) // 2 - y0
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).text((ox, oy), text, font=font, fill=255)
    edge = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    depth = int(28 * scale)
    for i in range(depth, 0, -2):                         # extrusion: stacked darker reds down-right
        shade = int(90 - 60 * i / depth)
        layer = Image.new("RGBA", (cw, ch), (shade + 40, 6, 10, 255))
        edge.paste(layer, (i, i), mask)
    rim = mask.filter(ImageFilter.MaxFilter(5)).point(lambda v: 255 if v > 0 else 0)
    rim = ImageChops.subtract(rim, mask)
    silver = Image.new("RGBA", (cw, ch), (225, 228, 235, 255))
    edge.paste(silver, (0, 0), rim)
    strip_h = ch
    tiles = [_wash(_cover(im, int(strip_h * 0.68), strip_h), WASHES[i % len(WASHES)], 0.25) for i, im in enumerate(imgs)]
    strip = Image.new("RGB", (sum(t.width for t in tiles) * 2 + cw, strip_h), (40, 6, 8))
    x = 0
    while x < strip.width:
        for t in tiles:
            strip.paste(t, (x, 0))
            x += t.width
            if x >= strip.width:
                break
    return strip, mask, edge


def _letters_frames(imgs: list[Image.Image]) -> Iterable[Image.Image]:
    scale = 2
    strip, mask, edge = _letters_layers(imgs, scale)
    cw, ch = W * scale, H * scale
    bg = Image.new("RGB", (cw, ch), (50, 4, 8))
    n = round((C_END - B_END) * FPS)
    travel = max(1, strip.width - cw)
    for f in range(n):
        t = f / max(1, n - 1)
        fill = strip.crop((int(travel * 0.35 * t), 0, int(travel * 0.35 * t) + cw, ch))
        full = bg.copy()
        full.paste(edge, (0, 0), edge)
        full.paste(fill, (0, 0), mask)
        zoom = 5.0 - 4.0 * _ease(t)                       # from inside a letter back to the whole word
        vw, vh = cw / zoom, ch / zoom
        cx, cy = cw * (0.30 + 0.20 * _ease(t)), ch * 0.5
        box = (int(cx - vw / 2), int(cy - vh / 2), int(cx + vw / 2), int(cy + vh / 2))
        frame = full.crop(box).resize((W, H), Image.BILINEAR)
        yield frame.rotate(-6 * (1 - _ease(t)), resample=Image.BILINEAR, fillcolor=(50, 4, 8))


def _lockup(subtitle: str) -> Image.Image:
    img = Image.new("RGB", (W, H), RED)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-300, -200, W + 300, H + 200), fill=255)
    img = Image.composite(img, Image.new("RGB", (W, H), (70, 3, 8)), glow.filter(ImageFilter.GaussianBlur(260)))
    d = ImageDraw.Draw(img)
    f1 = _font(250)
    a, b = "SCREEN", "CENTRAL"
    wa = d.textlength(a, font=f1)
    wb = d.textlength(b, font=f1)
    gap = 40
    total = wa + wb + gap + 80
    x = (W - total) / 2
    y = H / 2 - 190
    # "SCREEN" in a red block with a silver frame (the Marvel-style plate), "CENTRAL" in silver
    d.rectangle((x - 24, y - 6, x + wa + 44, y + 312), fill=(150, 8, 16), outline=(230, 232, 238), width=12)
    d.text((x + 10, y), a, font=f1, fill=(255, 255, 255))
    xb = x + wa + 60 + gap
    d.text((xb + 6, y + 6), b, font=f1, fill=(90, 4, 10))
    d.text((xb, y), b, font=f1, fill=(232, 234, 240))
    d.line((xb, y + 318, xb + wb, y + 318), fill=(232, 234, 240), width=8)
    if subtitle:
        f2 = _font(64)
        sub_txt = subtitle.upper()
        while d.textlength(sub_txt, font=f2) > W - 200 and len(sub_txt) > 10:
            sub_txt = sub_txt[:-4].rstrip() + "…"
        sw = d.textlength(sub_txt, font=f2)
        d.text(((W - sw) / 2, y + 370), sub_txt, font=f2, fill=(255, 225, 225))
    return img


def _lockup_frames(last: Image.Image, subtitle: str) -> Iterable[Image.Image]:
    logo = _lockup(subtitle)
    n = round((D_END - C_END) * FPS)
    for f in range(n):
        t = f / max(1, n - 1)
        z = 1.0 + 0.04 * t
        cw, ch = int(W / z), int(H / z)
        frame = logo.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
        if t < 0.25:
            frame = Image.blend(last, frame, t / 0.25)
        sx = int(-600 + (W + 1200) * min(1.0, t / 0.7))     # shine sweep
        shine = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shine).polygon([(sx, 0), (sx + 140, 0), (sx - 160, H), (sx - 300, H)], fill=70)
        frame = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), frame, shine.filter(ImageFilter.GaussianBlur(30)))
        yield frame


# --- sound ----------------------------------------------------------------------------------------
def _sound(dest: pathlib.Path) -> pathlib.Path:
    """Page flips (A) + synthesised whoosh (B) and impact (D), 48 kHz stereo."""
    flips = ["-i", str(FLIPS)] if FLIPS.exists() else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=4"]
    graph = (
        "[0:a]atrim=0:4.2,adelay=500|500,volume=1.0[fl];"
        "anoisesrc=d=1.2:c=pink:r=48000:a=0.5,bandpass=f=1200:w=900,afade=t=in:d=0.5,afade=t=out:st=0.6:d=0.6,"
        f"adelay={int((A_END - 0.4) * 1000)}|{int((A_END - 0.4) * 1000)},volume=0.8[wh];"
        "sine=f=48:r=48000:d=2.2,afade=t=out:st=0.05:d=2.1,volume=2.5[bo];"
        "anoisesrc=d=0.4:c=brown:r=48000:a=0.6,lowpass=f=300,afade=t=out:d=0.4[hit];"
        f"[bo][hit]amix=inputs=2:normalize=0,adelay={int(C_END * 1000)}|{int(C_END * 1000)}[im];"
        f"[fl][wh][im]amix=inputs=3:normalize=0:duration=longest,apad,atrim=0:{LENGTH},"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=I=-16:TP=-2[a]"
    )
    media.run(["ffmpeg", "-v", "error", "-y", *flips, "-filter_complex", graph, "-map", "[a]",
               "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(dest)])
    return dest


def make_sting(images: list[pathlib.Path], titles: list[str], subtitle: str, dest: pathlib.Path,
               enc: list[str], norm: str) -> tuple[pathlib.Path, float]:
    """Render the intro to dest (1920×1080, 30 fps, 48 kHz stereo). Returns (path, seconds)."""
    from ... import control
    work = dest.parent / "_sting_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    imgs = _load(images)
    i = 0

    def save(frames: Iterable[Image.Image]) -> Image.Image | None:
        nonlocal i
        last = None
        for fr in frames:
            fr.save(work / f"f{i:04d}.jpg", quality=90)
            last = fr
            i += 1
        return last

    control.progress("intro: poster storm")
    last = save(_storm_frames(imgs, titles))
    control.check()
    control.progress("intro: flash")
    save(_flash_frames(last))
    control.check()
    control.progress("intro: Screen Central letters")
    last = save(_letters_frames(imgs))
    control.check()
    control.progress("intro: logo")
    save(_lockup_frames(last, subtitle))
    audio = _sound(work / "sting.m4a")
    media.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", str(work / "f%04d.jpg"), "-i", str(audio),
               "-vf", f"tmix=frames=3:weights='1 2 1',{norm}",                # motion blur
               "-map", "0:v", "-map", "1:a", "-t", f"{LENGTH:.3f}", *enc,
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dest)])
    shutil.rmtree(work, ignore_errors=True)
    return dest, LENGTH
