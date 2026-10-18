"""Deterministic original beat cards, native captions and immutable exports.

This deliberately small renderer provides an inspectable first native treatment.
It does not infer rights or relevance, generate research, or certify creativity.
"""
from __future__ import annotations

from pathlib import Path
import json
import math
import shutil

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import control
from ..config import settings
from ..studio import media, tts
from ..studio.media_quality import file_hash, inspect_export
from .routes import digest


def font(size):
    for filename in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(filename).is_file(): return ImageFont.truetype(filename, size)
    raise RuntimeError("Install Arial or DejaVu Sans before rendering native cards")


def lines(draw, text, face, width):
    words, result, line = text.split(), [], ""
    for word in words:
        if draw.textlength(word, font=face) > width: raise ValueError("A word is too wide for the caption safe area")
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=face) > width:
            result.append(line); line = word
        else: line = candidate
    if line: result.append(line)
    return result


def card(path: Path, title: str, beat: dict, index: int, total: int, *, vertical=True, asset=None):
    w, h = (1080, 1920) if vertical else (1080, 1350)
    image = Image.new("RGB", (w, h), "#0d1523")
    draw = ImageDraw.Draw(image)
    accent = ("#64e6c4", "#ffc766", "#b5a0ff", "#ff929b")[index % 4]
    draw.rounded_rectangle((80, 180, 900, 196), 8, fill="#344055")
    draw.rounded_rectangle((80, 180, 80 + int(820 * (index + 1) / total), 196), 8, fill=accent)
    draw.text((90, 115), "SCREEN CENTRAL / ORIGINAL", font=font(26), fill=accent)
    title_lines = lines(draw, title, font(48), 810)
    if len(title_lines) > 3: raise ValueError("Shorten the title to fit its safe area")
    draw.multiline_text((90, 235), "\n".join(title_lines), font=font(48), fill="white", spacing=12)
    image_y = 500 if vertical else 440
    if asset:
        with Image.open(asset) as source:
            image.paste(ImageOps.fit(source.convert("RGB"), (810, 440 if vertical else 270)), (90, image_y))
    else:
        # Each beat is an original graphic, never a random borrowed movie shot.
        draw.rounded_rectangle((100 + index * 14, image_y, 650 + index * 22, image_y + (380 if vertical else 250)),
                               70, outline=accent, width=14)
        draw.ellipse((620 - index * 12, image_y + 60, 880 - index * 12, image_y + 320), fill=accent)
        draw.text((150, image_y + 65), f"0{index + 1}", font=font(120), fill="white")
    caption_y = 1040 if vertical else 790
    caption_lines = lines(draw, beat["text"], font(54), 790)
    max_lines = 5
    if len(caption_lines) > max_lines: raise ValueError("Shorten this beat: caption exceeds five safe-area lines")
    draw.rounded_rectangle((70, caption_y - 30, 920, caption_y + len(caption_lines) * 72 + 20), 24, fill="#1e2c43")
    draw.multiline_text((100, caption_y), "\n".join(caption_lines), font=font(54), fill="white", spacing=18)
    footer = f"{index + 1:02d} / {total:02d}"
    if not vertical: footer += "  /  YOUR TAKE?" if index == total - 1 else "  /  SWIPE TO CONTINUE"
    draw.text((100, h - 280 if vertical else h - 120), footer, font=font(26), fill=accent)
    image.save(path, "PNG")
    return {"file": str(path), "sha256": file_hash(path), "width": w, "height": h,
            "alt": beat["text"], "caption": beat["text"], "caption_lines": len(caption_lines),
            "safe_area": {"left": 70, "right": 160, "top": 100, "bottom": 280 if vertical else 80},
            "font_px": 54, "role": beat["role"], "source_refs": beat["source_refs"],
            "asset_id": beat.get("asset_id"), "visual": "owner_selected_asset" if asset else "original_graphic"}


def render(root: Path, brief: dict, fmt: str, assets: dict[int, Path]):
    root.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "input_sha256": digest(brief), "format": fmt, "cards": [],
                "quality": {"errors": [], "warnings": [], "status": "needs_owner_review"},
                "limitations": ["Beat-card treatment; no semantic footage search or word-aligned karaoke captions",
                                "Owner must inspect narration, relevance, originality and all rights"]}
    segments, timeline, total = [], [], 0.0
    vertical = fmt in ("short", "reel")
    for i, beat in enumerate(brief["beats"]):
        control.check()
        path = root / f"card-{i + 1}.png"
        manifest["cards"].append(card(path, brief["title"], beat, i, len(brief["beats"]), vertical=vertical,
                                      asset=assets.get(beat.get("asset_id"))))
        if vertical:
            narration = root / f"voice-{i + 1}.mp3"
            tts.synth(beat["text"], narration)
            seconds = media.duration(narration)
            if not math.isfinite(seconds) or not .7 <= seconds <= 12:
                raise ValueError("Narration duration outside 0.7–12 seconds per beat; inspect the speech")
            # Keep the complete burned-in caption readable even when the voice
            # provider returns unusually fast speech. Never trim the words.
            readable = max(seconds, len(beat["text"]) / 17, 2.5)
            tempo = seconds / readable
            filters = []
            while tempo < .5:
                filters.append("atempo=0.5"); tempo /= .5
            if tempo < .999: filters.append(f"atempo={tempo:.6f}")
            seconds = readable
            seconds = math.ceil((seconds + .15) * 30) / 30
            segment = root / f"beat-{i + 1}.mp4"
            media.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(path),
                       "-i", str(narration), "-t", str(seconds), "-vf", "scale=1080:1920,setsar=1",
                       "-af", ",".join([*filters, "apad", "alimiter=limit=0.85"]), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(segment)], timeout=180)
            segments.append(segment)
            timeline.append({"start": round(total, 3), "end": round(total + seconds, 3), "text": beat["text"], "card": path.name})
            total += seconds
    if vertical:
        if not 6 <= total <= 90: raise ValueError("Native Short/Reel must be 6–90 seconds; adjust beat length")
        listing = root / "concat.txt"
        listing.write_text("\n".join(f"file '{p.name}'" for p in segments))
        final = root / "vertical.mp4"
        media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                   "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=7", "-c:a", "aac", "-ar", "48000",
                   "-movflags", "+faststart", str(final)], timeout=180)
        quality = inspect_export(final, expected_seconds=total, aspect=9 / 16)
        if quality["errors"]: raise ValueError("Final native export blocked: " + "; ".join(quality["errors"]))
        manifest.update(video={"file": str(final), "sha256": file_hash(final)}, quality=quality, timeline=timeline)
        (root / "captions.json").write_text(json.dumps(timeline, indent=2))
    else:
        # Full image decode and native aspect are checked, not inferred from names.
        for item in manifest["cards"]:
            with Image.open(item["file"]) as image: image.load()
        manifest["quality"]["decode"] = "passed"
    cover = root / "cover.png"
    shutil.copyfile(manifest["cards"][0]["file"], cover)
    manifest["cover"] = {"file": str(cover), "sha256": file_hash(cover)}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
