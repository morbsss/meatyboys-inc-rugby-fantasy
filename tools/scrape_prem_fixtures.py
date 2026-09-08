"""Scrape the full Gallagher PREM fixture list from premiershiprugby.com.

The fixtures-results page is a Nuxt app that renders nothing server-side — it
fetches its data client-side from the InCrowd rugby-union feed. Rather than
driving a browser over 20 round pages, we call that feed directly (the params
come from window.__NUXT__.config on the page: compId=1011, season=202601).

The feed serves the WHOLE season in one request, so the 20 "pages" (rounds
1-18, plus 19 = play-offs and 20 = final) are just a `round` field to group by.

Club branding (stadium, colours, crests, socials) isn't in the match feed — it
lives in the page's embedded Nuxt payload, which we parse separately. Clubs key
on the same numeric id the match feed uses for teams, so the two join directly.

  python tools/scrape_prem_fixtures.py                     # -> data/prem_fixtures_2026_27.json
  python tools/scrape_prem_fixtures.py --out other.json
  python tools/scrape_prem_fixtures.py --flat             # one flat list, no round grouping
  python tools/scrape_prem_fixtures.py --no-clubs         # skip the club directory

Kickoff times are published in UTC (ISO-8601 'Z') and are copied through as-is.
Note `time_confirmed`: rounds 10-19 are still placeholder 15:00 slots that the
league hasn't fixed yet — see the notes in the emitted file's `meta`.
"""
import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import requests

FEED_URL = 'https://rugby-union-feeds.incrowdsports.com/v1/matches'
PAGE_URL = 'https://www.premiershiprugby.com/fixtures-results?competition=gallagher-prem&round=1'
COMP_ID = 1011          # Gallagher PREM
SEASON = 202601         # 2026-27
PROVIDER = 'rugbyviz'
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    'Referer': 'https://www.premiershiprugby.com/',
}

# The feed's own round titles for the knockouts are a stage out of step with the
# site (it labels the 2 play-off matches 'QF' and the single final 'SF'), so we
# derive the label from the round number, which the site treats as PO / F.
ROUND_LABELS = {19: 'PO', 20: 'F'}


# ── match feed ────────────────────────────────────────────────────────────────
def fetch_matches(comp_id: int, season: int) -> list[dict]:
    resp = requests.get(
        FEED_URL,
        params={'compId': comp_id, 'season': season, 'provider': PROVIDER},
        headers=HEADERS, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['data']


def _side(team: dict) -> dict:
    """Team identity + its live score fields (all null until the match is played)."""
    return {
        'id':              team.get('id'),
        'name':            team.get('name'),
        'short_name':      team.get('shortName'),
        'score':           team.get('score'),
        'half_time_score': team.get('halfTimeScore'),
        'eighty_min_score': team.get('eightyMinScore'),
        'extra_time_score': team.get('extraFTScore'),
        'kicking_comp_score': team.get('kickingCompScore'),
    }


def normalise(m: dict) -> dict:
    venue = m.get('venue') or {}
    venue_name = venue.get('name') or None
    tbd = venue_name == 'TBD'
    return {
        'match_id':       m['id'],
        'round':          m['round'],
        'round_label':    ROUND_LABELS.get(m['round'], str(m['round'])),
        'home_team':      m['homeTeam']['name'],
        'away_team':      m['awayTeam']['name'],
        # Feed publishes UTC ('...Z'); normalised to a plain '+00:00' ISO string.
        'kickoff_utc':    m['date'].replace('.000Z', '+00:00') if m.get('date') else None,
        # 0 = time locked in, 1 = league hasn't confirmed the slot (placeholder).
        'time_confirmed': not bool(m.get('tbc')),
        'venue':          None if tbd else venue_name,
        'venue_id':       None if tbd else venue.get('id'),
        'broadcasters':   m.get('broadcasters') or [],
        # Full identity + live scoring. `home`/`away` carry the provider team ids
        # that join to the `clubs` block below.
        'home':           _side(m['homeTeam']),
        'away':           _side(m['awayTeam']),
        'status':         m.get('status'),          # 'fixture' until it's played
        'match_winner':   m.get('matchWinner'),
        'attendance':     m.get('attendance'),
    }


# ── club directory (from the page's embedded Nuxt payload) ────────────────────
# Nuxt serialises its payload with `devalue`: a flat array where every dict/list
# member is an INDEX into that array rather than an inline value. We resolve
# those references back into ordinary nested dicts.
_WRAPPERS = {'ShallowReactive', 'Reactive', 'Ref', 'ShallowRef', 'EmptyRef', 'NuxtError'}


def _resolve(arr: list, i, depth: int = 0):
    if depth > 20 or not isinstance(i, int) or not 0 <= i < len(arr):
        return None
    v = arr[i]
    if isinstance(v, dict):
        return {k: _resolve(arr, x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in _WRAPPERS:
            return _resolve(arr, v[1], depth + 1)
        return [_resolve(arr, x, depth + 1) for x in v]
    return v


def fetch_clubs() -> list[dict]:
    """Club branding keyed by the same team id the match feed uses."""
    resp = requests.get(PAGE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return []
    arr = json.loads(m.group(1))

    clubs = []
    for idx, node in enumerate(arr):
        # A club node is the only shape carrying both a stadium and a slug.
        if not (isinstance(node, dict) and 'stadium' in node and 'slug' in node):
            continue
        c = _resolve(arr, idx) or {}
        social = c.get('socialNetworks') or {}
        clubs.append({
            'team_id':      int(c['id']) if str(c.get('id', '')).isdigit() else c.get('id'),
            'name':         c.get('name'),
            'slug':         c.get('slug'),
            # The club's own name for its ground; the match feed's per-fixture
            # `venue` can differ (e.g. 'Recreation Ground' vs 'The Rec').
            'stadium':      c.get('stadium'),
            'year_founded': c.get('yearFounded'),
            'website':      c.get('website'),
            'colour_dark':  c.get('colorDark'),
            'colour_light': c.get('colorLight'),
            'logo_dark':    (c.get('logoDark') or {}).get('src'),
            'logo_light':   (c.get('logoLight') or {}).get('src'),
            'socials':      {k.replace('_string', ''): v for k, v in social.items() if v},
            'tickets_url':  c.get('buyTicketsLink'),
        })
    return sorted(clubs, key=lambda c: c['name'] or '')


# ── assembly ──────────────────────────────────────────────────────────────────
def build(comp_id: int, season: int, flat: bool, with_clubs: bool) -> dict:
    raw = fetch_matches(comp_id, season)
    fixtures = sorted((normalise(m) for m in raw),
                      key=lambda f: (f['round'], f['kickoff_utc'] or '', f['home_team']))

    meta = {
        'competition': 'Gallagher PREM',
        'season': '2026-27',
        'source': PAGE_URL.rsplit('&round=', 1)[0],
        'feed': f'{FEED_URL}?compId={comp_id}&season={season}&provider={PROVIDER}',
        'scraped_at': datetime.now(timezone.utc).isoformat(),
        'total_fixtures': len(fixtures),
        'timezone': 'All kickoff times are UTC.',
        'notes': [
            "Rounds 1-18 are the regular season; round 19 = play-offs ('PO'), "
            "round 20 = final ('F').",
            "time_confirmed=false means the league has not fixed that kickoff yet — "
            "the feed carries a placeholder 15:00 UTC slot. Currently true for "
            "rounds 1-9 and 20 only; rounds 10-19 are unconfirmed.",
            "Play-off team names are 'TBC' until the regular season finishes.",
            "Score fields (score, half_time_score, eighty_min_score, match_winner, "
            "attendance) are null pre-season and fill in as matches are played — "
            "re-run this script to refresh them.",
            "home.id / away.id are provider team ids that join to clubs[].team_id.",
        ],
    }

    payload = {'meta': meta}
    if with_clubs:
        payload['clubs'] = fetch_clubs()
    if flat:
        payload['fixtures'] = fixtures
    else:
        by_round = defaultdict(list)
        for f in fixtures:
            by_round[f['round']].append(f)
        payload['rounds'] = [
            {'round': r, 'label': ROUND_LABELS.get(r, str(r)), 'fixtures': by_round[r]}
            for r in sorted(by_round)
        ]
    return payload


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join('data', 'prem_fixtures_2026_27.json'))
    ap.add_argument('--comp-id', type=int, default=COMP_ID)
    ap.add_argument('--season', type=int, default=SEASON)
    ap.add_argument('--flat', action='store_true', help='one flat fixture list')
    ap.add_argument('--no-clubs', action='store_true', help='skip the club directory')
    args = ap.parse_args()

    payload = build(args.comp_id, args.season, args.flat, not args.no_clubs)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write('\n')

    n = payload['meta']['total_fixtures']
    rounds = len(payload.get('rounds', [])) or 'flat'
    clubs = len(payload.get('clubs', []))
    print(f'Wrote {n} fixtures across {rounds} rounds, {clubs} clubs -> {args.out}')
