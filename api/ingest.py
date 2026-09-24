"""
Ingestion jobs (spec §4) - league-aware, idempotent, adapter-driven.

These pull through the data-source adapters (api/datasource), so they run
offline against the mock adapter and against SuperBru/ESPN when
DATA_SOURCE=live. Every write is an upsert keyed to make re-runs idempotent,
and every row carries its league_id so the two leagues stay isolated.

Layering (spec §3): the scheduler decides *when*; these functions do the
ingestion; the domain/API layers read the resulting tables.
"""

from datetime import datetime, timezone

from .db import execute as _exec, DB_TYPE
from . import player_match
from .datasource import (get_fixture_source, get_lineup_source, get_player_source,
                         get_score_source)


# Names from the last ingest_lineups run that couldn't be matched to a player
# row. Diagnostics only — read by the cron tick to put the count in job_runs, so
# a provider renaming someone shows up in the log instead of silently producing
# rows that join to nothing.
LAST_LINEUP_UNRESOLVED: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scalar(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


# ---------------------------------------------------------------------------
# §4.2 fixtures → rounds
# ---------------------------------------------------------------------------

def ingest_rounds(conn, league_id: int, competition: str) -> int:
    """Refresh the round calendar AND the per-round match list.

    `rounds` drives pick lockout (first kickoff); `real_fixtures` records who
    each real team plays each round, which is what shows a player's opponent
    beside their score. Both come from the same fetch so they can't drift.
    """
    rounds = get_fixture_source().fetch_rounds(competition)
    for rd in rounds:
        _exec(conn,
              'INSERT INTO rounds (round_number, first_kickoff, last_kickoff, league_id) '
              'VALUES (?, ?, ?, ?) '
              'ON CONFLICT (league_id, round_number) DO UPDATE SET '
              '  first_kickoff = excluded.first_kickoff, '
              '  last_kickoff  = excluded.last_kickoff',
              (rd.round_number, rd.first_kickoff, rd.last_kickoff, league_id))
        for m in rd.matches:
            if not m.home or not m.away:
                continue
            _exec(conn,
                  'INSERT INTO real_fixtures (league_id, round, home_team, away_team) '
                  'VALUES (?, ?, ?, ?) '
                  'ON CONFLICT (league_id, round, home_team) DO UPDATE SET '
                  '  away_team = excluded.away_team',
                  (league_id, rd.round_number, m.home, m.away))
    conn.commit()
    return len(rounds)


def round_kickoffs(competition: str, round_number: int) -> list[str]:
    """Per-match kickoff ISO strings for one round (for live detection §4.2)."""
    for rd in get_fixture_source().fetch_rounds(competition):
        if rd.round_number == round_number:
            return [m.kickoff for m in rd.matches] or [rd.first_kickoff]
    return []


# ---------------------------------------------------------------------------
# §4.3 lineups → match_lineups (status S/B; O = absent from squad, no row)
# ---------------------------------------------------------------------------

def _club_roster(conn, league_id: int, team: str) -> list[dict]:
    cur = _exec(conn, 'SELECT name, position FROM players '
                      'WHERE league_id = ? AND team = ?', (league_id, team))
    return [dict(r) if isinstance(r, dict) else {'name': r[0], 'position': r[1]}
            for r in cur.fetchall()]


def ingest_lineups(conn, league_id: int, competition: str, round_number: int) -> int:
    """Store the round's team sheets, under SuperBru's spelling of each name.

    The provider's formatted name is resolved against the club's roster before
    writing (see api/player_match): SuperBru lengthens the initial to separate
    namesakes at one club, so ESPN's 'Griffin,A' matches neither 'Griffin,Ar' nor
    'Griffin,Al' and the row would land unjoinable — present in match_lineups,
    invisible to every query that needs it.

    Resolving here rather than at read time means the stored name is the canonical
    one, so the existing joins keep working untouched.
    """
    entries = get_lineup_source().fetch_lineups(competition, round_number)
    now = _now()
    written = 0
    rosters: dict[str, list[dict]] = {}
    unresolved: list[str] = []

    for e in entries:
        if e.status == 'O' or not e.player_name:
            continue

        if e.real_team not in rosters:
            rosters[e.real_team] = _club_roster(conn, league_id, e.real_team)
        match, _rule = player_match.resolve(
            e.player_name, e.full_name or '', rosters[e.real_team],
            position=player_match.position_for_jersey(e.jersey))
        if match:
            name = match['name']
        else:
            # Keep the provider's spelling: an unmatched sheet entry is still
            # evidence the club named someone, and it is what makes the miss
            # visible instead of silently dropping a player.
            name = e.player_name
            unresolved.append(f'{e.real_team}:{e.player_name}')

        _exec(conn,
              'INSERT INTO match_lineups '
              '  (round, player_name, real_team, jersey, is_bench, scraped_at, league_id) '
              'VALUES (?, ?, ?, ?, ?, ?, ?) '
              'ON CONFLICT (round, player_name, real_team) DO UPDATE SET '
              '  jersey = excluded.jersey, is_bench = excluded.is_bench, '
              '  scraped_at = excluded.scraped_at, league_id = excluded.league_id',
              (round_number, name, e.real_team, e.jersey, e.is_bench, now, league_id))
        written += 1
    conn.commit()
    LAST_LINEUP_UNRESOLVED[:] = unresolved
    return written


# ---------------------------------------------------------------------------
# §4.4 player scoring → weekly_stats
# ---------------------------------------------------------------------------

def _player_row(conn, name: str, position: str, league_id: int):
    """The league's player of this name and position, whatever club they're at."""
    cur = _exec(conn,
                'SELECT player_id, team FROM players '
                'WHERE league_id = ? AND name = ? AND position = ?',
                (league_id, name, position))
    rows = cur.fetchall()
    return [dict(r) if isinstance(r, dict) else {'player_id': r[0], 'team': r[1]}
            for r in rows]


def _player_id(conn, name: str, team: str, position: str, league_id: int) -> int:
    """player_id for a SuperBru row, following transfers rather than duplicating.

    Identity is (league, name, position) — NOT the club. The old version upserted
    `ON CONFLICT (name, team, position)`, which has the club IN the key, so a
    transfer never matched and inserted a SECOND row: the transferred player then
    existed twice, the original stopped receiving scores, and any squad holding
    them was frozen at their old club — which also broke the lineup join, since
    that matches on `players.team`.

    A club change on an existing (name, position) is therefore treated as a
    transfer and updates the row in place, keeping player_id stable so draft
    picks, selections and history follow the player.
    """
    existing = _player_row(conn, name, position, league_id)

    for row in existing:
        if row['team'] == team:
            return row['player_id']

    # Exactly one namesake at another club: a transfer. More than one and we
    # cannot tell which (two real players can share surname+initial+position), so
    # fall through and insert rather than move the wrong one.
    if len(existing) == 1:
        _exec(conn, 'UPDATE players SET team = ? WHERE player_id = ?',
              (team, existing[0]['player_id']))
        return existing[0]['player_id']

    _exec(conn,
          'INSERT INTO players (name, team, position, league_id) VALUES (?, ?, ?, ?) '
          'ON CONFLICT (name, team, position) DO UPDATE SET team = excluded.team',
          (name, team, position, league_id))
    cur = _exec(conn,
                'SELECT player_id FROM players WHERE name = ? AND team = ? AND position = ?',
                (name, team, position))
    return _scalar(cur.fetchone())


# ---------------------------------------------------------------------------
# §4.1 players → roster sync (new signings and transfers)
# ---------------------------------------------------------------------------

def ingest_players(conn, league_id: int, competition: str) -> dict:
    """Reconcile `players` with SuperBru, the source of truth for who is where.

    Runs on its own daily cadence rather than riding on live_scoring, because
    squads change mid-week — a transfer or a new signing announced on Tuesday has
    to be in the table before Thursday's team sheets arrive, or the lineup join
    misses that player all weekend.

    Three outcomes per SuperBru row, and a fourth that is deliberately inert:

      added       no player of that name and position -> insert
      transferred exactly one, at a different club    -> update in place, keeping
                  player_id so drafts and history follow the player
      unchanged   already correct
      departed    in our table but no longer listed by SuperBru. NEVER deleted:
                  a drafted player must not vanish from someone's squad, and a
                  scrape that half-fails would otherwise wipe the roster. Counted
                  and returned so it shows up in the job log.

    `ambiguous` counts rows where two players share surname, initial and position
    across clubs — real people do (Wilson,T plays for both Bristol and Sale), so
    guessing a transfer there would move the wrong one. Reported, not resolved.
    """
    players = get_player_source().fetch_players(competition)
    seen: set[tuple] = set()
    counts = {'added': 0, 'transferred': 0, 'unchanged': 0,
              'departed': 0, 'ambiguous': 0}
    moves: list[str] = []
    new: list[str] = []
    gone: list[str] = []

    for p in players:
        if not p.name or not p.team or not p.position:
            continue
        seen.add((p.name, p.position))
        existing = _player_row(conn, p.name, p.position, league_id)

        if any(r['team'] == p.team for r in existing):
            counts['unchanged'] += 1
            continue
        if len(existing) == 1:
            _exec(conn, 'UPDATE players SET team = ? WHERE player_id = ?',
                  (p.team, existing[0]['player_id']))
            counts['transferred'] += 1
            moves.append(f"{p.name} {existing[0]['team']}->{p.team}")
            continue
        if len(existing) > 1:
            counts['ambiguous'] += 1
            continue

        _exec(conn,
              'INSERT INTO players (name, team, position, league_id) '
              'VALUES (?, ?, ?, ?) '
              'ON CONFLICT (name, team, position) DO UPDATE SET team = excluded.team',
              (p.name, p.team, p.position, league_id))
        counts['added'] += 1
        new.append(f'{p.name} ({p.team})')

    # A wholly empty scrape means the source failed, not that every player left.
    if players:
        cur = _exec(conn, 'SELECT name, position, team FROM players '
                          'WHERE league_id = ?', (league_id,))
        for r in cur.fetchall():
            d = (dict(r) if isinstance(r, dict)
                 else {'name': r[0], 'position': r[1], 'team': r[2]})
            if (d['name'], d['position']) not in seen:
                counts['departed'] += 1
                gone.append(f"{d['name']} ({d['team']})")

    conn.commit()
    counts['moves'] = moves
    # Named, not just counted: an addition paired with a departure at the same
    # club and position is a RENAME upstream, not a signing. SuperBru currently
    # lists Northampton's Walker,H as 'WalkerNOPE,H' — their typo, which this job
    # would otherwise import as a brand-new player with no trace of why.
    counts['new'] = new
    counts['gone'] = gone
    return counts


def ingest_player_scores(conn, league_id: int, competition: str,
                         round_number: int, finalize: bool = False) -> int:
    """Upsert one round of cumulative player scores. `finalize` is the Monday
    authoritative pass (spec §4.4) - same write path, overwriting live values."""
    scores = get_score_source().fetch_player_scores(competition, round_number)
    now = _now()
    written = 0
    for s in scores:
        if not s.name:
            continue
        pid = _player_id(conn, s.name, s.team, s.position, league_id)
        _exec(conn,
              'INSERT INTO weekly_stats '
              '  (player_id, round, total_points, price, kicking, points_per_game, '
              '   popularity, form, scraped_at, league_id) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
              'ON CONFLICT (player_id, round) DO UPDATE SET '
              '  total_points = excluded.total_points, price = excluded.price, '
              '  kicking = excluded.kicking, points_per_game = excluded.points_per_game, '
              '  popularity = excluded.popularity, form = excluded.form, '
              '  scraped_at = excluded.scraped_at, league_id = excluded.league_id',
              (pid, round_number, s.total_points, s.price, s.kicking, s.points_per_game,
               s.popularity, s.form, now, league_id))
        written += 1
    conn.commit()
    return written
