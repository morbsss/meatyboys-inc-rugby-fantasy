#!/usr/bin/env python3
"""Build the site favicons from api/static/img/animal.gif.

    python tools/generate_favicon.py

Source is a 200x200, 61-frame animated GIF (~186 KB) — fine as artwork, wrong as
a favicon, which browsers draw at 16-32 px. Downscaling keeps the animation and
cuts the bytes by well over an order of magnitude, which matters on a 1 vCPU VM
with a ~0.83 GB/month bandwidth allowance.

Outputs, all beside the source in api/static/img/:

    favicon.gif           32x32 animated — the one modern browsers use
    favicon.png           32x32 static, first frame — PNG-preferring clients
    favicon.ico           16+32+48 px static — the /favicon.ico browsers probe
                          for on their own, and what crawlers and bookmark
                          services expect
    apple-touch-icon.png  180x180 static — iOS home screen, which ignores
                          `rel=icon` and screenshots the page without this
    favicon-sprite.png    every animation frame on one paletted sheet
    favicon-sprite.json   its grid and timing, read by favicon.js

Making it MOVE everywhere
-------------------------
Only Firefox animates a GIF favicon. Chrome, Edge and Safari draw its first
frame and stop, and no markup changes that — so api/static/js/favicon.js
repaints the icon itself, slicing the sprite sheet onto a canvas and swapping the
<link rel=icon> href frame by frame.

That is why the sheet exists alongside the GIF, and why the loop is rotated onto
its clearest frame (see _best_frame): that frame is what Firefox's GIF shows
first, what the stills export, and what every visitor sees before the script
runs or if it never does — reduced-motion, no JS, a blocked request. All four
paths therefore show the same picture, and the only difference is whether it
moves.
"""
import json
import os
import sys

from PIL import Image, ImageSequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, 'api', 'static', 'img')
SOURCE = os.path.join(IMG_DIR, 'animal.gif')

GIF_SIZE = (32, 32)
PNG_SIZE = (32, 32)
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]
APPLE_SIZE = (180, 180)
# iOS composites a transparent touch icon onto black, so give it the site's own
# background instead — the same colour as base.html's <meta name="theme-color">.
APPLE_BG = (11, 59, 46)

# Sprite sheet for the JS-driven animation (api/static/js/favicon.js), which is
# what makes the icon move in Chrome, Edge and Safari.
#
# Take every 2nd frame: the source is 25 fps, and ~12.5 fps is plenty for
# something drawn at 16 px while halving both the sheet and the number of icon
# swaps per second. The sheet is one request instead of 30.
SPRITE_STEP = 2
SPRITE_COLS = 6


MARGIN = 0.04       # breathing room around the artwork, as a fraction of its size


def _frames(im):
    """Every frame as full RGBA, already composited onto the canvas.

    ImageSequence hands back each frame with the GIF's disposal method applied,
    so converting per frame is safe; going via RGBA also keeps the source's
    transparency instead of flattening it to the palette's background colour.
    """
    out = []
    for frame in ImageSequence.Iterator(im):
        out.append((frame.convert('RGBA'), frame.info.get('duration', 80)))
    return out


def _best_frame(frames):
    """Index of the frame that best represents the animation as a still.

    This matters more than it sounds. Chrome, Edge and Safari don't animate a GIF
    favicon — they draw its FIRST frame — and in this source frame 0 catches the
    animal at one extreme of its motion, a narrow sliver off to one side. So the
    loop is rotated to start here instead.

    Rotating a loop is perceptually free: it plays continuously, so where it
    begins is invisible in Firefox, while every static client gets a frame where
    the animal is actually recognisable.

    "Best" = most opaque pixels (most of the animal in shot), tie-broken by how
    centred that ink is.
    """
    def score(item):
        frame, _ = item
        mask = frame.getchannel('A').point(lambda a: 255 if a > 127 else 0)
        ink = sum(1 for p in mask.getdata() if p)
        bb = mask.getbbox()
        if not bb:
            return (0, 0)
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        w, h = frame.size
        offset = abs(cx - w / 2) + abs(cy - h / 2)
        return (ink, -offset)

    return max(range(len(frames)), key=lambda i: score(frames[i]))


def _content_box(frames, canvas):
    """The square crop that holds the artwork across EVERY frame.

    The source animal occupies a centred ~110x110 of its 200x200 canvas, so
    resizing the full canvas to 32 px drew it at about 17 px — a speck in a mostly
    empty icon. Cropping to the content first spends the whole 32 px on the
    animal.

    It has to be the UNION of all frames, not each frame's own box: cropping
    per frame would re-centre the animal every frame and make the animation
    jitter. Square, because a non-square crop into a square icon would either
    distort the artwork or reintroduce padding on one axis.
    """
    boxes = []
    for frame, _ in frames:
        bb = frame.getchannel('A').point(lambda a: 255 if a > 127 else 0).getbbox()
        if bb:
            boxes.append(bb)
    if not boxes:
        return (0, 0, *canvas)

    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)

    side = max(x1 - x0, y1 - y0) * (1 + 2 * MARGIN)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = side / 2
    # Clamp to the canvas, keeping the box square even at an edge.
    half = min(half, cx, cy, canvas[0] - cx, canvas[1] - cy)
    return (round(cx - half), round(cy - half), round(cx + half), round(cy + half))


def _write_sprite(small):
    """All animation frames on one PNG sheet, plus the JSON favicon.js reads.

    Browsers other than Firefox won't animate a GIF favicon, so favicon.js
    repaints the icon itself: it slices this sheet onto a 32x32 canvas and swaps
    the <link rel=icon> href frame by frame. A single sheet keeps that to one
    request rather than one per frame, and lets the frames be pre-rendered once
    at load instead of decoded every cycle.
    """
    frames = small[::SPRITE_STEP]
    # The last frame is dropped when it duplicates the first: the source closes
    # its loop that way, and a repeated frame reads as a stutter mid-cycle.
    if len(frames) > 1 and frames[0][0].tobytes() == frames[-1][0].tobytes():
        frames = frames[:-1]

    cell = GIF_SIZE[0]
    cols = min(SPRITE_COLS, len(frames))
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * cell, rows * cell), (0, 0, 0, 0))
    for i, (frame, _d) in enumerate(frames):
        sheet.paste(frame, ((i % cols) * cell, (i // cols) * cell))

    # Quantise to a 255-colour palette + one transparent index. PNG can't dedupe
    # across frames the way GIF does, so RGBA cost 52 KB for this sheet — more
    # than the whole animated GIF — against 6.7 KB paletted. Quantising the sheet
    # as ONE image rather than per frame also gives every frame the same palette,
    # so the colours don't shift as the animation plays.
    sheet_p = sheet.convert('P', palette=Image.ADAPTIVE, colors=255)
    sheet_p.paste(255, (0, 0),
                  sheet.getchannel('A').point(lambda a: 255 if a < 128 else 0))

    sprite_path = os.path.join(IMG_DIR, 'favicon-sprite.png')
    sheet_p.save(sprite_path, optimize=True, transparency=255)

    meta = {
        'cell': cell,
        'cols': cols,
        'rows': rows,
        'count': len(frames),
        # Per-frame delay in ms, summed over the frames this sheet skipped.
        'delay': sum(d for _f, d in small[:SPRITE_STEP]),
    }
    meta_path = os.path.join(IMG_DIR, 'favicon-sprite.json')
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, indent=2)

    print(f'sprite: {len(frames)} frames  {cols}x{rows} grid of {cell}px  '
          f'{meta["delay"]}ms/frame  '
          f'({len(frames) * meta["delay"] / 1000:.2f}s loop, '
          f'{1000 / meta["delay"]:.1f} fps)')
    return sprite_path, meta_path


def main():
    if not os.path.exists(SOURCE):
        sys.exit(f'missing source artwork: {SOURCE}')

    im = Image.open(SOURCE)
    frames = _frames(im)
    print(f'source: {os.path.basename(SOURCE)}  {im.size[0]}x{im.size[1]}  '
          f'{len(frames)} frames  {os.path.getsize(SOURCE) / 1024:.0f} KB')

    box = _content_box(frames, im.size)
    print(f'crop:   {box}  -> {box[2] - box[0]}x{box[3] - box[1]} '
          f'({(box[2] - box[0]) / im.size[0]:.0%} of the canvas)')
    uncropped = [f for f, _ in frames]      # kept for the apple touch icon
    frames = [(f.crop(box), d) for f, d in frames]

    # Rotate the loop so the clearest frame leads — it is what every browser that
    # refuses to animate a GIF favicon will show, and what the stills export.
    lead = _best_frame(frames)
    print(f'lead:   frame {lead} of {len(frames)} '
          f'(clearest still; the loop is rotated to start there)')
    frames = frames[lead:] + frames[:lead]

    # --- animated 32x32 GIF -------------------------------------------------
    small = [(f.resize(GIF_SIZE, Image.LANCZOS), d) for f, d in frames]
    # Quantise to a palette with one slot reserved for transparency, so the
    # animal keeps a see-through background on light and dark browser chrome.
    paletted = [f.convert('P', palette=Image.ADAPTIVE, colors=255) for f, _ in small]
    for src, dst in zip((f for f, _ in small), paletted):
        # Anything under half alpha becomes the transparent palette index.
        alpha = src.getchannel('A').point(lambda a: 255 if a < 128 else 0)
        dst.paste(255, (0, 0), alpha)

    gif_path = os.path.join(IMG_DIR, 'favicon.gif')
    paletted[0].save(
        gif_path, save_all=True, append_images=paletted[1:],
        duration=[d for _, d in small], loop=0,
        transparency=255, disposal=2, optimize=True,
    )

    # --- static fallbacks, all the same first frame -------------------------
    first = frames[0][0]
    png_path = os.path.join(IMG_DIR, 'favicon.png')
    first.resize(PNG_SIZE, Image.LANCZOS).save(png_path, optimize=True)

    ico_path = os.path.join(IMG_DIR, 'favicon.ico')
    first.resize((48, 48), Image.LANCZOS).save(ico_path, sizes=ICO_SIZES)

    # The touch icon comes off the UNCROPPED frame: at 180 px the 118 px crop
    # would be upscaled and soft, whereas the full 200 px canvas downscales
    # sharply — and its padding is what a home-screen icon wants anyway, since
    # iOS rounds the corners and draws it large.
    apple_src = uncropped[lead]          # same frame the stills use, uncropped
    apple = Image.new('RGB', APPLE_SIZE, APPLE_BG)
    scaled = apple_src.resize(APPLE_SIZE, Image.LANCZOS)
    apple.paste(scaled, (0, 0), scaled)          # flatten onto the site colour
    apple_path = os.path.join(IMG_DIR, 'apple-touch-icon.png')
    apple.save(apple_path, optimize=True)

    sprite_path, meta_path = _write_sprite(small)

    print()
    for p in (gif_path, png_path, ico_path, apple_path,
              sprite_path, meta_path):
        print(f'  {os.path.basename(p):22s} {os.path.getsize(p) / 1024:7.1f} KB')


if __name__ == '__main__':
    main()
