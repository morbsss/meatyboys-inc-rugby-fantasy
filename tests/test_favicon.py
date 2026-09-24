"""Site favicons, built from api/static/img/animal.gif.

These are generated assets (tools/generate_favicon.py) that are committed rather
than built at deploy time, so the failure mode is a missing or stale file that
nothing notices — a favicon 404 doesn't break a page, it just quietly shows the
browser's blank document icon.

The source artwork is a 200x200, 61-frame GIF. That is deliberately NOT the file
referenced by the page: browsers draw favicons at 16-32 px, and shipping the
full-size original would spend ~182 KB on something rendered into 32 px.
"""

import json
import os
import re

import pytest
from PIL import Image

from api.index import app

IMG_DIR = os.path.join(app.static_folder, 'img')

# name -> (expected size, must the file stay small?)
ICONS = {
    'favicon.gif': (32, 32),
    'favicon.png': (32, 32),
    'apple-touch-icon.png': (180, 180),
}
# A favicon is fetched by every visitor; the source artwork is 182 KB.
MAX_FAVICON_KB = 40


@pytest.fixture
def client():
    return app.test_client()


def _path(name):
    return os.path.join(IMG_DIR, name)


@pytest.mark.parametrize('name', sorted(ICONS) + ['favicon.ico', 'animal.gif'])
def test_icon_file_exists(name):
    assert os.path.exists(_path(name)), \
        f'{name} missing — run python tools/generate_favicon.py'


@pytest.mark.parametrize('name,size', sorted(ICONS.items()))
def test_icon_has_the_declared_size(name, size):
    """base.html advertises `sizes=`; a mismatch makes the browser pick badly."""
    assert Image.open(_path(name)).size == size


def test_favicon_gif_is_still_animated():
    """The whole point of using the GIF. A static export would silently lose it.

    One frame fewer than the source is expected, not a bug: animal.gif ends with
    a duplicate of its first frame to close the loop, and rotating the loop onto
    its clearest frame (see _best_frame) makes that pair adjacent, so the encoder
    collapses it — which is what you want, since a repeated frame mid-loop reads
    as a stutter.
    """
    im = Image.open(_path('favicon.gif'))
    source_frames = Image.open(_path('animal.gif')).n_frames

    assert im.is_animated
    assert source_frames - 1 <= im.n_frames <= source_frames


def test_favicon_gif_leads_on_a_clear_frame():
    """Chrome, Edge and Safari draw frame 0 and never animate, so frame 0 has to
    be a recognisable picture — not the sliver the source happens to start on.

    Before the loop was rotated this was 11x25 of the 32px box, 16% opaque.
    """
    im = Image.open(_path('favicon.gif'))
    im.seek(0)
    mask = im.convert('RGBA').getchannel('A').point(lambda v: 255 if v > 127 else 0)
    bb = mask.getbbox()
    ink = sum(1 for p in mask.getdata() if p)

    assert bb is not None
    assert bb[2] - bb[0] >= 24, 'lead frame barely uses the icon width'
    assert bb[3] - bb[1] >= 24, 'lead frame barely uses the icon height'
    assert ink / (32 * 32) > 0.30, 'lead frame is mostly empty'


def test_favicon_gif_keeps_transparency():
    """It sits on browser chrome that may be light or dark."""
    assert 'transparency' in Image.open(_path('favicon.gif')).info


def test_favicon_ico_carries_multiple_sizes():
    """Windows and older clients pick a size out of the .ico itself."""
    sizes = Image.open(_path('favicon.ico')).ico.sizes()

    assert (16, 16) in sizes and (32, 32) in sizes


def test_favicon_is_much_smaller_than_the_source_artwork():
    small = os.path.getsize(_path('favicon.gif'))
    source = os.path.getsize(_path('animal.gif'))

    assert small < source / 4, 'downscaling the favicon saved almost nothing'
    assert small < MAX_FAVICON_KB * 1024


def test_favicon_ico_is_served_at_the_root_path(client):
    """Browsers and crawlers probe /favicon.ico without being told to."""
    r = client.get('/favicon.ico')

    assert r.status_code == 200
    assert 'icon' in r.headers['Content-Type']
    assert r.headers.get('Cache-Control', '').startswith('public')


@pytest.mark.parametrize('name', sorted(ICONS))
def test_icons_are_served_from_static(client, name):
    r = client.get(f'/static/img/{name}')

    assert r.status_code == 200


def test_page_head_declares_the_icons(client):
    head = client.get('/auth').get_data(as_text=True).split('</head>')[0]
    links = re.findall(r'<link rel="(icon|apple-touch-icon)"[^>]*href="([^"]+)"', head)
    by_rel = {}
    for rel, href in links:
        by_rel.setdefault(rel, []).append(href)

    assert '/static/img/favicon.png' in by_rel['icon']
    assert '/static/img/favicon.gif' in by_rel['icon']
    assert by_rel['apple-touch-icon'] == ['/static/img/apple-touch-icon.png']
    # Browsers take the last icon they understand, so the animated GIF must come
    # after the PNG to win wherever both are supported.
    assert by_rel['icon'].index('/static/img/favicon.gif') > \
           by_rel['icon'].index('/static/img/favicon.png')


def test_the_page_never_links_the_full_size_artwork(client):
    """182 KB for a 32 px icon — the regression this whole tool exists to avoid."""
    head = client.get('/auth').get_data(as_text=True).split('</head>')[0]

    assert 'animal.gif' not in head


# --- the JS-driven animation ------------------------------------------------
# Chrome, Edge and Safari draw only a GIF favicon's first frame, so favicon.js
# repaints the icon from a sprite sheet. These tests cover the contract between
# the generator (which writes the sheet + metadata) and the script (which slices
# it): a mismatch shows up as a blank or scrambled icon, never as an error.

SPRITE = 'favicon-sprite.png'
SPRITE_META = 'favicon-sprite.json'


def _meta():
    with open(_path(SPRITE_META), encoding='utf-8') as fh:
        return json.load(fh)


def test_sprite_and_metadata_exist():
    for name in (SPRITE, SPRITE_META):
        assert os.path.exists(_path(name)), \
            f'{name} missing — run python tools/generate_favicon.py'


def test_metadata_describes_a_usable_animation():
    meta = _meta()

    assert meta['count'] >= 2, 'a single frame is not an animation'
    assert meta['cell'] == 32
    assert 40 <= meta['delay'] <= 200, 'frame delay outside a sane range'


def test_sheet_dimensions_match_the_metadata():
    """favicon.js computes cell offsets from cols/cell; if the sheet is a
    different shape it slices the wrong rectangles and the icon is garbage."""
    meta = _meta()
    sheet = Image.open(_path(SPRITE))

    assert sheet.size == (meta['cols'] * meta['cell'], meta['rows'] * meta['cell'])
    assert meta['count'] <= meta['cols'] * meta['rows']


def test_every_sprite_cell_holds_a_distinct_non_empty_frame():
    """Slices the sheet exactly as favicon.js does. An empty cell would blink the
    icon out; duplicate cells would stall the animation."""
    meta = _meta()
    sheet = Image.open(_path(SPRITE)).convert('RGBA')
    cell, cols = meta['cell'], meta['cols']

    seen, empty = set(), []
    for n in range(meta['count']):
        x, y = (n % cols) * cell, (n // cols) * cell
        frame = sheet.crop((x, y, x + cell, y + cell))
        if not any(p > 127 for p in frame.getchannel('A').getdata()):
            empty.append(n)
        seen.add(frame.tobytes())

    assert not empty, f'blank sprite cells at {empty}'
    assert len(seen) == meta['count'], 'duplicate frames in the sheet'


def test_sprite_frame_zero_matches_the_static_icon():
    """The script swaps in frame 0 first. If it didn't match favicon.png the icon
    would visibly jump the moment JS took over."""
    meta = _meta()
    first = Image.open(_path(SPRITE)).convert('RGBA').crop(
        (0, 0, meta['cell'], meta['cell']))
    still = Image.open(_path('favicon.png')).convert('RGBA')

    # Silhouettes must be identical; colour may differ slightly because the sheet
    # is paletted and the PNG is truecolour.
    mismatch = sum(1 for a, b in zip(first.getchannel('A').getdata(),
                                     still.getchannel('A').getdata())
                   if (a > 127) != (b > 127))
    assert mismatch == 0

    drawn = [max(abs(c1 - c2) for c1, c2 in zip(p[:3], q[:3]))
             for p, q in zip(first.getdata(), still.getdata())
             if p[3] > 127 and q[3] > 127]
    assert max(drawn) <= 48, 'frame 0 is visibly different from the still'


def test_sprite_is_paletted_and_small():
    """RGBA cost 52 KB — more than the entire animated GIF — because PNG can't
    dedupe across frames. Paletted it is under 10 KB."""
    assert os.path.getsize(_path(SPRITE)) < 16 * 1024


def test_page_loads_the_animation_script(client):
    html = client.get('/auth').get_data(as_text=True)

    assert '/static/js/favicon.js' in html
    assert client.get('/static/js/favicon.js').status_code == 200


@pytest.mark.parametrize('name', [SPRITE, SPRITE_META])
def test_sprite_assets_are_served(client, name):
    assert client.get(f'/static/img/{name}').status_code == 200


def test_animation_respects_reduced_motion_and_hidden_tabs():
    """Both are the difference between a nice touch and a battery complaint, and
    both are easy to drop in a refactor."""
    js = open(os.path.join(app.static_folder, 'js', 'favicon.js'),
              encoding='utf-8').read()

    assert 'prefers-reduced-motion' in js
    assert 'visibilitychange' in js
    assert 'document.hidden' in js
