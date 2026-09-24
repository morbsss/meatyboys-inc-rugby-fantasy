#!/usr/bin/env python3
"""Generate one flat jersey SVG per Premiership club, for the squad page.

Run after changing any club's colours or kit:

    python tools/generate_jerseys.py

Outputs
-------
api/static/img/jerseys/<CODE>.svg   the art the app serves, one per club
data/rugby-jerseys/index.html       a contact sheet for eyeballing all ten

Files are named by the club's canonical code (BAT, BRI, ...) - the same value
carried in `players.team` and `real_fixtures`, so the squad page builds the URL
straight from a player's club with no lookup table to keep in sync.

Why the viewBox is `-6 16 112 84`
---------------------------------
The squad token is a fixed 4:3 box (80x60 at full size, see `.fp-shirt` in
api/static/css/squad.css) and the pitch layout is tuned to the silhouette that
used to fill it: the S/B/O status dot hangs off the jersey's left edge at
`left: 10%`, its base level with the hem at `bottom: 1%`, and the formation's
slot coordinates assume that footprint.

The jersey art is drawn on a 100x110 canvas with the shirt occupying x 6..94,
y 16..100 - content that is nearly square, so dropping it into a 4:3 box would
either distort it (a squashed, elliptical club badge) or letterbox it somewhere
the dot no longer lines up with.

Cropping the canvas to the shirt and padding it back out to 4:3 solves both:
112x84 is exactly 4:3, so the art scales UNIFORMLY into the token (80/112 ==
60/84 == 0.714 - no distortion, the badge stays circular), and it lands where
the old silhouette was:

    jersey x 6..94  -> 10.7%..89.3% of the box   (old mask: 10%..90%)
    jersey y 16..100 -> 0%..100% of the box      (old mask: 6%..99%)

So every tuned offset in squad.css and OFDS_FORMATION stays valid. Keep the
viewBox and the shirt path in step if you edit either.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_OUT = os.path.join(ROOT, 'api', 'static', 'img', 'jerseys')
PREVIEW_OUT = os.path.join(ROOT, 'data', 'rugby-jerseys')

# Shirt outline and collar, on a 100x110 canvas. The shirt spans x 6..94,
# y 16..100; VIEWBOX crops to that and pads to 4:3 (see the module docstring).
JERSEY = ("M40,16 C36,17 32,18 30,19 L10,30 L6,46 L26,44 L28,100 L72,100 "
          "L74,44 L94,46 L90,30 L70,19 C68,18 64,17 60,16 "
          "C57,23 43,23 40,16 Z")
COLLAR = "M40,16 C43,23 57,23 60,16 L62.5,18.5 C58,27 42,27 37.5,18.5 Z"
VIEWBOX = "-6 16 112 84"


def bands(colors, y0=8, y1=104, h=10.0):
    out, y, i = [], y0, 0
    while y < y1:
        out.append(f'<rect x="0" y="{y:.2f}" width="100" height="{h:.2f}" fill="{colors[i % len(colors)]}"/>')
        y += h; i += 1
    return "\n    ".join(out)


def hoops_pinstriped(base, hoop, stripe, hoop_h=11, gap=11):
    out = [f'<rect x="0" y="8" width="100" height="96" fill="{base}"/>']
    y = 16
    while y < 100:
        out.append(f'<rect x="0" y="{y-1.6:.1f}" width="100" height="{hoop_h+3.2:.1f}" fill="{stripe}"/>')
        out.append(f'<rect x="0" y="{y:.1f}" width="100" height="{hoop_h}" fill="{hoop}"/>')
        y += hoop_h + gap
    return "\n    ".join(out)


def leicester_fill():
    out = ['<rect x="0" y="8" width="100" height="96" fill="#5AA152"/>']
    for y in (14, 38, 62, 86):
        out.append(f'<rect x="0" y="{y:.1f}" width="100" height="12" fill="#C42B2B"/>')
        out.append(f'<rect x="0" y="{y+2:.1f}" width="100" height="8" fill="#F4F1E6"/>')
    return "\n    ".join(out)


# Keyed by canonical club code - the filename and the join key to players.team.
TEAMS = {}
TEAMS["SAR"] = dict(name="Saracens", slug="saracens", accent="#E4002B",
    fill=('<rect x="0" y="8" width="100" height="96" fill="#E4002B"/>'
          '<rect x="24" y="31" width="52" height="73" fill="#111111"/>'
          '<rect x="24" y="30" width="52" height="2" fill="#ffffff"/>'),
    collar="#111111", placket="#ffffff")
TEAMS["NOR"] = dict(name="Northampton Saints", slug="northampton", accent="#0B5D34",
    fill=hoops_pinstriped("#0B5D34", "#111111", "#C8A951"), collar="#111111", placket="#C8A951")
TEAMS["EXE"] = dict(name="Exeter Chiefs", slug="exeter", accent="#151515",
    fill=('<rect x="0" y="8" width="100" height="96" fill="#151515"/>'
          '<rect x="6" y="40" width="22" height="6" fill="#ffffff"/>'
          '<rect x="72" y="40" width="22" height="6" fill="#ffffff"/>'),
    collar="#ffffff", placket="#ffffff")
TEAMS["LEI"] = dict(name="Leicester Tigers", slug="leicester", accent="#2E7D32",
    fill=leicester_fill(), collar="#ffffff", placket="#ffffff")
TEAMS["BRI"] = dict(name="Bristol Bears", slug="bristol", accent="#0A1E3F",
    fill=bands(["#0A1E3F", "#ffffff"], h=10), collar="#F2A900", placket="#F2A900")
TEAMS["BAT"] = dict(name="Bath", slug="bath", accent="#0A1D6E",
    fill=('<rect x="0" y="8" width="100" height="96" fill="#0A1D6E"/>'
          '<rect x="0" y="46" width="100" height="4" fill="#ffffff"/>'
          '<rect x="0" y="50" width="100" height="12" fill="#111111"/>'
          '<rect x="0" y="62" width="100" height="4" fill="#ffffff"/>'),
    collar="#ffffff", placket="#ffffff")
TEAMS["GLO"] = dict(name="Gloucester", slug="gloucester", accent="#D0202E",
    fill=hoops_pinstriped("#ffffff", "#D0202E", "#111111", hoop_h=11, gap=9),
    collar="#111111", placket="#D0202E")
TEAMS["HAR"] = dict(name="Harlequins", slug="harlequins", accent="#C6007E",
    fill=('<rect x="0" y="8" width="100" height="96" fill="#A2D45E"/>'
          '<rect x="24" y="8" width="26" height="52" fill="#5CB8E6"/>'
          '<rect x="50" y="8" width="26" height="52" fill="#C6007E"/>'
          '<rect x="24" y="60" width="26" height="44" fill="#6B4A2B"/>'
          '<rect x="50" y="60" width="26" height="44" fill="#AEB4B9"/>'),
    collar="#C6007E", placket="#111111")
TEAMS["SAL"] = dict(name="Sale Sharks", slug="sale", accent="#0A1E3F",
    fill=('<rect x="0" y="8" width="100" height="96" fill="#ffffff"/>'
          '<rect x="0" y="52" width="100" height="9" fill="#0A1E3F"/>'
          '<rect x="6" y="40" width="22" height="5" fill="#0A1E3F"/>'
          '<rect x="72" y="40" width="22" height="5" fill="#0A1E3F"/>'),
    collar="#0A1E3F", placket="#0A1E3F")
TEAMS["NEW"] = dict(name="Newcastle", slug="newcastle", accent="#1A1A1A",
    fill=bands(["#1A1A1A", "#ffffff"], h=10), collar="#1A1A1A", placket="#1A1A1A")

ORDER = ["SAR", "NOR", "EXE", "LEI", "BRI", "BAT", "GLO", "HAR", "SAL", "NEW"]


def badge(code, accent):
    cx, cy, r = 50, 51, 12
    return f'''<g>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="#F5EFE1" stroke="#14110c" stroke-width="1.2"/>
    <circle cx="{cx}" cy="{cy}" r="{r-2.6}" fill="none" stroke="{accent}" stroke-width="1.8"/>
    <text x="{cx}" y="{cy+2.7}" text-anchor="middle" fill="{accent}" font-family="'Arial Narrow','Helvetica Neue',Arial,sans-serif" font-weight="700" font-size="7.6">{code}</text>
  </g>'''


def svg(code, t):
    # The clip id is per club: the contact sheet inlines all ten in one
    # document, and a shared id would make every shirt clip to the first.
    cid = f'jersey-{code}'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}" role="img" aria-label="{t['name']} shirt">
  <defs><clipPath id="{cid}"><path d="{JERSEY}"/></clipPath></defs>
  <g clip-path="url(#{cid})">
    {t['fill']}
  </g>
  <path d="{JERSEY}" fill="none" stroke="#0d0d0d" stroke-width="1.4" stroke-linejoin="round"/>
  <path d="{COLLAR}" fill="{t['collar']}" stroke="#0d0d0d" stroke-width="0.8"/>
  <line x1="50" y1="24" x2="50" y2="38" stroke="{t['placket']}" stroke-width="1.6"/>
  {badge(code, t['accent'])}
</svg>'''


def write_svgs():
    os.makedirs(SVG_OUT, exist_ok=True)
    out = {}
    for code in ORDER:
        art = svg(code, TEAMS[code])
        out[code] = art
        with open(os.path.join(SVG_OUT, f'{code}.svg'), 'w', encoding='utf-8') as fh:
            fh.write(art)
    return out


def write_preview(svgs):
    """A contact sheet: each club's art on its own, and again inside a mock of
    the squad token (jersey number seated below the club badge) so kit changes
    can be checked against how the pitch actually renders them."""
    cards = "\n".join(
        f'''    <figure class="card">
      <div class="shirt">{svgs[code]}</div>
      <div class="token" style="--club-jersey:url('../../api/static/img/jerseys/{code}.svg')">{i + 1}</div>
      <figcaption>{TEAMS[code]['name']}<small>{code}</small></figcaption>
    </figure>''' for i, code in enumerate(ORDER))
    html = f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Fantasy Rugby - Team Shirts</title>
<style>
  :root {{ box-sizing:border-box; --bg:#eef1f4; --card:#fff; --ink:#0f1720; --muted:#5b6672; }}
  * {{ box-sizing:inherit; }}
  body {{ margin:0; padding:28px 16px 48px; background:var(--bg); color:var(--ink);
         font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  header {{ max-width:900px; margin:0 auto 22px; }}
  h1 {{ font-size:1.35rem; margin:0 0 4px; letter-spacing:-0.01em; }}
  p.sub {{ margin:0; color:var(--muted); font-size:0.9rem; }}
  .grid {{ max-width:900px; margin:0 auto; display:grid;
           grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:14px; }}
  .card {{ margin:0; background:var(--card); border-radius:14px; padding:16px 10px 12px;
           box-shadow:0 1px 2px rgba(16,24,32,.08); text-align:center; }}
  .shirt {{ height:96px; display:flex; align-items:center; justify-content:center; }}
  .shirt svg {{ height:100%; width:auto; filter:drop-shadow(0 2px 3px rgba(0,0,0,.12)); }}
  /* Mock of .fp-shirt--art from api/static/css/squad.css, on grass. */
  .token {{ width:80px; height:60px; margin:10px auto 0; display:grid; place-items:center;
            padding:20px 0 4px; position:relative;
            background:var(--club-jersey) center / 100% 100% no-repeat;
            font:800 15px/1 ui-sans-serif,system-ui,sans-serif; color:#fff;
            text-shadow:0 1px 2px rgba(0,0,0,.65); }}
  figcaption {{ margin-top:8px; font-size:0.8rem; font-weight:600; }}
  figcaption small {{ display:block; color:var(--muted); font-weight:500; }}
</style></head>
<body>
  <header><h1>Team shirts</h1>
    <p class="sub">One SVG per club, drawn to the squad token's 4:3 box. Each card shows the
      art on its own and inside a mock pitch token, where the jersey number sits below the
      club badge.</p></header>
  <div class="grid">
{cards}
  </div>
</body></html>'''
    os.makedirs(PREVIEW_OUT, exist_ok=True)
    with open(os.path.join(PREVIEW_OUT, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(html)


if __name__ == '__main__':
    svgs = write_svgs()
    write_preview(svgs)
    print(f'wrote {len(svgs)} jerseys to {SVG_OUT}')
    print(f'preview: {os.path.join(PREVIEW_OUT, "index.html")}')
