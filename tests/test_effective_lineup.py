"""The effective XV: auto-substitution and who carries the captaincy.

`effective_lineup` is shared by the scorer (api/competition._ofds_team_score)
and the projection model (api/predict._win_probabilities). That sharing is the
point: the win probability for a fixture must be computed over the same XV that
will actually score it. The model previously took `is_bench = 0` verbatim and
ignored both auto-subs and the captain, so it fielded a different team than the
one the table was built from.
"""

import sqlite3

import pytest

from api.competition import effective_lineup

ROUND = 3
TEAM = 'Smith\'s Pizza'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, league_id INTEGER,
                              name TEXT, team TEXT, position TEXT);
        CREATE TABLE team_selections (league_id INTEGER, round INTEGER, team_name TEXT,
                                      player_id INTEGER, is_bench INTEGER,
                                      is_captain INTEGER DEFAULT 0);
        CREATE TABLE match_lineups (league_id INTEGER, round INTEGER, player_name TEXT,
                                    real_team TEXT, is_bench INTEGER);
    ''')
    yield c
    c.close()


def _player(c, pid, name, team, pos):
    c.execute('INSERT INTO players VALUES (?, 2, ?, ?, ?)', (pid, name, team, pos))


def _pick(c, pid, bench=0, cap=0):
    c.execute('INSERT INTO team_selections VALUES (2, ?, ?, ?, ?, ?)',
              (ROUND, TEAM, pid, bench, cap))


def _really_starting(c, name, team):
    c.execute('INSERT INTO match_lineups VALUES (2, ?, ?, ?, 0)', (ROUND, name, team))


def _names(effective):
    return sorted(p['name'] for p in effective)


# ---------------------------------------------------------------------------
# No published line-up - the normal case when projecting an upcoming round
# ---------------------------------------------------------------------------

def test_without_a_lineup_the_named_starters_stand(conn):
    _player(conn, 1, 'Smith,F', 'NOR', 'FH')
    _player(conn, 2, 'Cover,B', 'BAT', 'FH')
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=1)
    conn.commit()
    # match_lineups is empty: lineups aren't scraped until the Thursday.
    assert _names(effective_lineup(conn, TEAM, ROUND)) == ['Smith,F']


# ---------------------------------------------------------------------------
# Auto-substitution
# ---------------------------------------------------------------------------

def test_a_dropped_starter_is_covered_by_a_bench_player_of_the_same_position(conn):
    _player(conn, 1, 'Smith,F', 'NOR', 'FH')      # picked, NOT really starting
    _player(conn, 2, 'Cover,B', 'BAT', 'FH')      # bench, really starting
    _player(conn, 3, 'Other,C', 'SAR', 'LK')      # unrelated starter
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=1)
    _pick(conn, 3, bench=0)
    _really_starting(conn, 'Cover,B', 'BAT')
    _really_starting(conn, 'Other,C', 'SAR')
    conn.commit()
    assert _names(effective_lineup(conn, TEAM, ROUND)) == ['Cover,B', 'Other,C']


def test_cover_must_match_the_position(conn):
    _player(conn, 1, 'Smith,F', 'NOR', 'FH')      # out
    _player(conn, 2, 'Prop,P', 'BAT', 'PR')       # bench, but a prop
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=1)
    _really_starting(conn, 'Prop,P', 'BAT')
    conn.commit()
    # No same-position cover, so the absent starter stays and scores ~0.
    assert _names(effective_lineup(conn, TEAM, ROUND)) == ['Smith,F']


def test_a_bench_player_covers_only_one_starter(conn):
    _player(conn, 1, 'OutA,A', 'NOR', 'FH')
    _player(conn, 2, 'OutB,B', 'SAR', 'FH')
    _player(conn, 3, 'Cover,C', 'BAT', 'FH')
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=0)
    _pick(conn, 3, bench=1)
    _really_starting(conn, 'Cover,C', 'BAT')
    conn.commit()
    eff = _names(effective_lineup(conn, TEAM, ROUND))
    assert eff.count('Cover,C') == 1, 'one bench player cannot cover two absentees'
    assert len(eff) == 2


def test_a_bench_player_who_is_not_really_starting_is_no_cover(conn):
    _player(conn, 1, 'Smith,F', 'NOR', 'FH')
    _player(conn, 2, 'Cover,B', 'BAT', 'FH')
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=1)
    _really_starting(conn, 'Someone,E', 'GLO')     # a lineup exists, neither is in it
    conn.commit()
    assert _names(effective_lineup(conn, TEAM, ROUND)) == ['Smith,F']


def test_apostrophes_are_stripped_when_matching_the_real_lineup(conn):
    """Ingestion strips apostrophes; the match has to agree or every O'Brien
    silently looks dropped and gets auto-subbed."""
    _player(conn, 1, "O'Brien,K", 'LEI', 'LF')
    _player(conn, 2, 'Bench,B', 'BAT', 'LF')
    _pick(conn, 1, bench=0)
    _pick(conn, 2, bench=1)
    _really_starting(conn, 'OBrien,K', 'LEI')      # stored without the apostrophe
    _really_starting(conn, 'Bench,B', 'BAT')
    conn.commit()
    assert _names(effective_lineup(conn, TEAM, ROUND)) == ["O'Brien,K"]


# ---------------------------------------------------------------------------
# The captaincy travels with the effective XV
# ---------------------------------------------------------------------------

def test_captain_flag_is_returned_for_doubling(conn):
    _player(conn, 1, 'Smith,F', 'NOR', 'FH')
    _player(conn, 2, 'Plain,P', 'BAT', 'LK')
    _pick(conn, 1, bench=0, cap=1)
    _pick(conn, 2, bench=0)
    conn.commit()
    eff = {p['name']: p['cap'] for p in effective_lineup(conn, TEAM, ROUND)}
    assert eff == {'Smith,F': True, 'Plain,P': False}


def test_a_substituted_captain_does_not_pass_the_armband_on(conn):
    """If the captain is auto-subbed out, the replacement is not a captain -
    the doubling is simply lost, matching the scorer."""
    _player(conn, 1, 'Capt,C', 'NOR', 'FH')       # captain, not really starting
    _player(conn, 2, 'Cover,B', 'BAT', 'FH')      # bench cover
    _pick(conn, 1, bench=0, cap=1)
    _pick(conn, 2, bench=1)
    _really_starting(conn, 'Cover,B', 'BAT')
    conn.commit()
    eff = effective_lineup(conn, TEAM, ROUND)
    assert [p['name'] for p in eff] == ['Cover,B']
    assert eff[0]['cap'] is False
