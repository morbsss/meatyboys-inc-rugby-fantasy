"""Premiership fixtures + club registry, sourced from data/prem_fixtures_2026_27.json.

That file is the record for FIXTURES and TEAM information (produced by
tools/scrape_prem_fixtures.py from premiershiprugby.com's own feed). ESPN
remains the source for match-day LINEUPS only — see api/real_lineups.py.

Why the official feed rather than ESPN for this:
  * ESPN's club names are out of date (it still says "Bristol Rugby" and
    "Newcastle Falcons"; the clubs are now Bristol Bears and Newcastle Red Bulls).
  * ESPN publishes no round numbers, so the old code inferred them from gaps
    between match dates — fragile, and it silently renumbers the whole season if
    a match is postponed. The official feed carries an explicit round per match.
  * ESPN rate-limits aggressively (HTTP 403), which made the fixture calendar an
    unreliable dependency. The JSON is on disk, so rounds always resolve.

TEAM CODES are the join key across the whole app. `players.team` (SuperBru) and
ESPN's `abbreviation` already agree on the same three-letter codes, so those are
canonical here too, and everything else (official name, ESPN's older name, short
name) resolves to them via `resolve_team`.
"""

import json
import os
from functools import lru_cache

FIXTURES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'prem_fixtures_2026_27.json',
)

# Canonical three-letter code per club, keyed by the feed's stable slug. These
# match both SuperBru's `players.team` values and ESPN's team abbreviations.
CODE_BY_SLUG = {
    'bath-rugby':         'BAT',
    'bristol-bears':      'BRI',
    'exeter-chiefs':      'EXE',
    'gloucester-rugby':   'GLO',
    'harlequins':         'HAR',
    'leicester-tigers':   'LEI',
    'newcastle-redbulls': 'NEW',
    'northampton-saints': 'NOR',
    'sale-sharks':        'SAL',
    'saracens':           'SAR',
}

# Names used by other sources that don't appear in the official feed. ESPN keeps
# the pre-rebrand club names, so those must resolve to the same code.
EXTRA_ALIASES = {
    'bristol rugby':      'BRI',
    'bristol bears':      'BRI',
    'newcastle falcons':  'NEW',
    'newcastle red bulls': 'NEW',
    'quins':              'HAR',
}


def _norm(name: str) -> str:
    return (name or '').strip().lower()


@lru_cache(maxsize=1)
def load() -> dict:
    """The parsed fixtures file. Cached — call `reload()` after regenerating it."""
    try:
        with open(FIXTURES_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise FileNotFoundError(
            f'Fixture list missing: {FIXTURES_FILE}. It ships with the repo and is '
            'uploaded by deploy.ps1; regenerate it with '
            '`python tools/scrape_prem_fixtures.py`.'
        ) from None


def reload() -> dict:
    load.cache_clear()
    _clubs_by_code.cache_clear()
    _alias_map.cache_clear()
    return load()


# ---------------------------------------------------------------------------
# Clubs
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _clubs_by_code() -> dict:
    out = {}
    for club in load().get('clubs', []):
        code = CODE_BY_SLUG.get(club.get('slug'))
        if not code:
            continue
        out[code] = dict(club, code=code)
    return out


def clubs() -> dict:
    """{code: club dict} — name, stadium, colours, crest URLs, socials, team_id."""
    return _clubs_by_code()


def club(code: str) -> dict | None:
    return _clubs_by_code().get((code or '').upper())


def team_name(code: str) -> str:
    """Official club name for a code, falling back to the code itself."""
    c = club(code)
    return c['name'] if c else (code or '')


@lru_cache(maxsize=1)
def _alias_map() -> dict:
    """Every known spelling → canonical code."""
    aliases = dict(EXTRA_ALIASES)
    for code, c in _clubs_by_code().items():
        aliases[_norm(code)] = code
        for key in ('name', 'slug'):
            if c.get(key):
                aliases[_norm(c[key])] = code
    # Feed team ids join fixtures to clubs.
    for code, c in _clubs_by_code().items():
        if c.get('team_id') is not None:
            aliases[str(c['team_id'])] = code
    return aliases


def resolve_team(name) -> str | None:
    """Canonical code for a club identified by code, official name, ESPN name,
    slug or provider team id. None if unrecognised (e.g. play-off 'TBC')."""
    if name is None:
        return None
    return _alias_map().get(_norm(str(name)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def rounds() -> list[dict]:
    """Rounds in order, each {'round', 'label', 'fixtures': [...]}."""
    data = load()
    if 'rounds' in data:
        return data['rounds']
    # Tolerate a --flat file by regrouping it.
    grouped: dict[int, list] = {}
    for f in data.get('fixtures', []):
        grouped.setdefault(f['round'], []).append(f)
    return [{'round': r, 'label': str(r), 'fixtures': grouped[r]} for r in sorted(grouped)]


def round_fixtures(round_number: int) -> list[dict]:
    for rd in rounds():
        if rd['round'] == round_number:
            return rd['fixtures']
    return []


def round_window(round_number: int) -> tuple[str, str] | None:
    """(first_kickoff, last_kickoff) ISO-8601 UTC for a round, or None."""
    kickoffs = sorted(f['kickoff_utc'] for f in round_fixtures(round_number)
                      if f.get('kickoff_utc'))
    return (kickoffs[0], kickoffs[-1]) if kickoffs else None
