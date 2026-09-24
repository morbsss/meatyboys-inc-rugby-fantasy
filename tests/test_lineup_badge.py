"""The S/B/O lineup badge: "not announced yet" is not the same as "Out".

`lineup_status` drives a small letter badge on the squad page. It used to be
'S', 'B' or NULL, and the frontend rendered NULL as 'O' (Out) - so from the
Tuesday 12:00 rollover, when the round advances and `match_lineups` holds
nothing for the new round, until Thursday's first ESPN scrape, every player in
every squad displayed a grey 'O'. Managers looked dropped wholesale for two and
a half days.

Now the status is gated on the player's own club having published a team sheet
for the round:

    club published, in the XV      -> 'S'
    club published, on the bench   -> 'B'
    club published, not in the 23  -> 'O'
    club has not published         -> None   (no badge at all)

The gate is per club, not per league, because clubs announce at different times
across a Thursday–Saturday window: a league-wide gate would flip every player to
'O' the moment the first club announced.
"""

import sqlite3

import pytest

from api.index import _state_players

LEAGUE = 2
LAST_ROUND = 0        # pre-season: nothing scored yet
NEXT_ROUND = 1

# Two clubs, so one can announce while the other has not.
PLAYERS = [
    # player_id, name, team, position
    (1, 'Obano,B',     'BAT', 'PR'),
    (2, 'Dunn,T',      'BAT', 'HK'),
    (3, 'du Toit,T',   'BAT', 'PR'),    # will be left out of the 23
    (4, 'Sio,S',       'EXE', 'PR'),
    (5, "O'Brien,M",   'EXE', 'LF'),    # apostrophe: exercises REPLACE()
]


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, league_id INTEGER,
                              name TEXT, team TEXT, position TEXT);
        CREATE TABLE weekly_stats (player_id INTEGER, league_id INTEGER, round INTEGER,
                                   total_points REAL, price REAL);
        CREATE TABLE team_selections (league_id INTEGER, round INTEGER, team_name TEXT,
                                      player_id INTEGER, is_bench INTEGER,
                                      is_captain INTEGER DEFAULT 0);
        CREATE TABLE match_lineups (league_id INTEGER, round INTEGER, player_name TEXT,
                                    real_team TEXT, jersey INTEGER, is_bench INTEGER);
    ''')
    c.executemany('INSERT INTO players VALUES (?,?,?,?,?)',
                  [(pid, LEAGUE, nm, tm, pos) for pid, nm, tm, pos in PLAYERS])
    # weekly_stats for LAST_ROUND is what the query INNER JOINs on, so every
    # player needs a row there to appear at all.
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?,?)',
                  [(pid, LEAGUE, LAST_ROUND, 0.0, 5.0) for pid, *_ in PLAYERS])
    c.executemany('INSERT INTO team_selections VALUES (?,?,?,?,?,?)',
                  [(LEAGUE, NEXT_ROUND, 'Smith\'s Pizza', pid, 0, 0) for pid, *_ in PLAYERS])
    c.commit()
    return c


def _announce(conn, club, starters=(), bench=(), round_num=NEXT_ROUND):
    """Publish a team sheet for one club."""
    rows = [(LEAGUE, round_num, n, club, i + 1, 0) for i, n in enumerate(starters)]
    rows += [(LEAGUE, round_num, n, club, 16 + i, 1) for i, n in enumerate(bench)]
    conn.executemany('INSERT INTO match_lineups VALUES (?,?,?,?,?,?)', rows)
    conn.commit()


def _status(conn):
    return {p['name']: p['lineup_status']
            for p in _state_players(conn, LEAGUE, LAST_ROUND, NEXT_ROUND)}


# --- the Tuesday reset ------------------------------------------------------

def test_no_badge_for_anyone_before_any_club_announces(conn):
    """The Tuesday 12:00 state: round rolled, no lineups scraped yet."""
    assert set(_status(conn).values()) == {None}


def test_previous_rounds_lineups_do_not_leak_into_the_new_round(conn):
    """The specific Tuesday bug: round 1's sheets must not badge round 2.

    The rollover advances the round, so the club gate has to be evaluated for
    the new round - a gate on 'club has ever published' would keep last week's
    badges on screen all week.
    """
    _announce(conn, 'BAT', starters=['Obano,B', 'Dunn,T'], round_num=NEXT_ROUND - 1)

    assert set(_status(conn).values()) == {None}


# --- Thursday's first scrape ------------------------------------------------

def test_badges_appear_for_the_club_that_has_announced(conn):
    _announce(conn, 'BAT', starters=['Obano,B'], bench=['Dunn,T'])

    assert _status(conn) == {
        'Obano,B': 'S',      # named in the XV
        'Dunn,T': 'B',       # named on the bench
        'du Toit,T': 'O',    # club announced and left them out
        'Sio,S': None,       # EXE has not announced
        "O'Brien,M": None,
    }


def test_a_club_yet_to_announce_is_not_marked_out(conn):
    """The reason the gate is per club: EXE players must not read as dropped
    just because BAT published first."""
    _announce(conn, 'BAT', starters=['Obano,B', 'Dunn,T', 'du Toit,T'])

    statuses = _status(conn)
    assert statuses['Sio,S'] is None
    assert statuses["O'Brien,M"] is None


def test_out_is_reported_once_the_players_own_club_announces(conn):
    """'O' must still be reachable - it's the real signal a manager needs."""
    _announce(conn, 'BAT', starters=['Obano,B', 'Dunn,T'])

    assert _status(conn)['du Toit,T'] == 'O'


def test_all_clubs_announced_gives_every_player_a_badge(conn):
    """By kickoff nothing is None."""
    _announce(conn, 'BAT', starters=['Obano,B', 'Dunn,T'], bench=['du Toit,T'])
    _announce(conn, 'EXE', starters=['Sio,S'], bench=["OBrien,M"])

    assert None not in _status(conn).values()


# --- details that would silently break the badge ---------------------------

def test_apostrophes_are_normalised_when_matching_lineup_rows(conn):
    """ESPN names are stored without apostrophes; players.name keeps them."""
    _announce(conn, 'EXE', starters=['Sio,S', 'OBrien,M'])

    assert _status(conn)["O'Brien,M"] == 'S'


def test_another_leagues_lineups_do_not_badge_this_league(conn):
    conn.execute('INSERT INTO match_lineups VALUES (?,?,?,?,?,?)',
                 (LEAGUE + 1, NEXT_ROUND, 'Obano,B', 'BAT', 1, 0))
    conn.commit()

    assert set(_status(conn).values()) == {None}


def test_every_player_is_returned_regardless_of_badge(conn):
    """The badge gate must not drop rows from the player list."""
    _announce(conn, 'BAT', starters=['Obano,B'])

    assert len(_state_players(conn, LEAGUE, LAST_ROUND, NEXT_ROUND)) == len(PLAYERS)


def test_badges_work_before_any_scoring_data_exists(conn):
    """Pre-season: weekly_stats is completely empty and last_round is 0.

    This is the state the league is in the week before round 1 — and the week the
    first team sheets are published, so it is precisely when the badges need to
    work. `weekly_stats` was INNER JOINed on last_round, so an empty table
    eliminated every player, /api/state returned no players at all, and the squad
    page had no statuses to render. Verified live: 92 lineup rows on the VM, 4
    clubs announced, and not a single badge on the page.
    """
    conn.execute('DELETE FROM weekly_stats')
    conn.commit()
    _announce(conn, 'BAT', starters=['Obano,B'], bench=['Dunn,T'])

    rows = _state_players(conn, LEAGUE, 0, NEXT_ROUND)

    assert len(rows) == len(PLAYERS), 'no scores yet must not hide the players'
    by_name = {r['name']: r['lineup_status'] for r in rows}
    assert by_name['Obano,B'] == 'S'
    assert by_name['Dunn,T'] == 'B'
    assert by_name['du Toit,T'] == 'O'
    assert by_name['Sio,S'] is None          # EXE has not announced


def test_missing_scores_leave_price_and_score_null_not_zero(conn):
    """A player with no weekly_stats row has an UNKNOWN price, not a free one —
    the LEFT JOIN must not invent numbers the UI could display."""
    conn.execute('DELETE FROM weekly_stats')
    conn.commit()

    row = _state_players(conn, LEAGUE, 0, NEXT_ROUND)[0]

    assert row['price'] is None
    assert row['last_round_score'] is None
