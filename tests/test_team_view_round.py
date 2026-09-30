"""Match Up, scrolled back to a past round, must show that round's XV.

Two faults, both visible the moment a team trades:

1. `/api/team-view?round=N` loaded `MAX(round)` and used N only to attach POINTS.
   So a past matchup showed the team's CURRENT squad beside that round's scores —
   after a trade it listed players the manager did not own that round.

2. It showed the XV as NAMED, not the XV that SCORED. A starter whose club left
   him out of the real 23 is replaced by same-position bench cover, and the total
   already reflects that — so the line-up on screen did not add up to the score
   printed beneath it.
"""

import sqlite3

import pytest

from api import index as idx

LEAGUE, TEAM = 2, 'olicoe'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, league_id INTEGER,
                              name TEXT, team TEXT, position TEXT);
        CREATE TABLE team_selections (league_id INTEGER, round INTEGER,
            team_name TEXT, player_id INTEGER, is_captain INTEGER DEFAULT 0,
            is_kicker INTEGER DEFAULT 0, is_bench INTEGER DEFAULT 0, jersey INTEGER,
            scraped_at TEXT, UNIQUE(round, team_name, player_id));
        CREATE TABLE match_lineups (league_id INTEGER, round INTEGER,
            player_name TEXT, real_team TEXT, jersey INTEGER, is_bench INTEGER);
    ''')
    c.executemany('INSERT INTO players VALUES (?,?,?,?,?)', [
        (1, LEAGUE, 'Dugdale,S', 'BAT', 'LF'),      # named starter, left out
        (2, LEAGUE, 'Christie,T', 'BAT', 'LF'),     # bench cover, really started
        (3, LEAGUE, 'Obano,B', 'BAT', 'PR'),        # unaffected starter
        (4, LEAGUE, 'Newman,A', 'GLO', 'LF'),       # only acquired in round 2
    ])
    picks = [(LEAGUE, 1, TEAM, 1, 0, 0, 0, 6), (LEAGUE, 1, TEAM, 2, 0, 0, 1, 20),
             (LEAGUE, 1, TEAM, 3, 0, 0, 0, 1)]
    picks += [(LEAGUE, 2, TEAM, 4, 0, 0, 0, 6), (LEAGUE, 2, TEAM, 2, 0, 0, 1, 20),
              (LEAGUE, 2, TEAM, 3, 0, 0, 0, 1)]
    c.executemany('INSERT INTO team_selections (league_id, round, team_name,'
                  ' player_id, is_captain, is_kicker, is_bench, jersey)'
                  ' VALUES (?,?,?,?,?,?,?,?)', picks)
    # Round 1 real sheet: Christie started, Dugdale was not named.
    c.executemany('INSERT INTO match_lineups VALUES (?,?,?,?,?,?)', [
        (LEAGUE, 1, 'Christie,T', 'BAT', 6, 0),
        (LEAGUE, 1, 'Obano,B', 'BAT', 1, 0),
    ])
    c.commit()
    return c


def _picks(conn, rnd):
    rows = [dict(r) for r in conn.execute(
        'SELECT p.player_id, p.name, p.position, p.team AS real_team, ts.is_bench'
        ' FROM team_selections ts JOIN players p ON p.player_id = ts.player_id'
        ' WHERE ts.team_name=? AND ts.league_id=? AND ts.round=?',
        (TEAM, LEAGUE, rnd))]
    return rows


def test_auto_subs_are_marked_on_the_effective_xv(conn, monkeypatch):
    monkeypatch.setattr(idx, '_team_model', lambda c, t: {'auto_sub': True})
    picks = _picks(conn, 1)

    idx._mark_auto_subs(conn, TEAM, 1, picks)

    by = {p['name']: p for p in picks}
    assert by['Christie,T']['subbed_in'] is True
    assert by['Christie,T']['is_bench'] == 0, 'the cover must appear in the XV'
    assert by['Dugdale,S']['subbed_out'] is True
    assert by['Dugdale,S']['is_bench'] == 1, 'the dropped starter moves to the bench'
    assert by['Obano,B']['subbed_in'] is False and by['Obano,B']['subbed_out'] is False


def test_nothing_is_marked_when_no_substitution_happened(conn, monkeypatch):
    """Most rounds for most teams. The picks must come back untouched."""
    monkeypatch.setattr(idx, '_team_model', lambda c, t: {'auto_sub': True})
    conn.execute("INSERT INTO match_lineups VALUES (?,?,?,?,?,?)",
                 (LEAGUE, 1, 'Dugdale,S', 'BAT', 6, 0))
    conn.commit()
    picks = _picks(conn, 1)

    idx._mark_auto_subs(conn, TEAM, 1, picks)

    assert all('subbed_in' not in p for p in picks)


def test_a_league_without_auto_subs_is_left_alone(conn, monkeypatch):
    monkeypatch.setattr(idx, '_team_model', lambda c, t: {'auto_sub': False})
    picks = _picks(conn, 1)

    idx._mark_auto_subs(conn, TEAM, 1, picks)

    assert all('subbed_in' not in p for p in picks)


def test_a_round_with_no_real_lineups_is_left_alone(conn, monkeypatch):
    """Before Thursday's scrape there is nothing to substitute against, which is
    the normal state of a round not yet played."""
    monkeypatch.setattr(idx, '_team_model', lambda c, t: {'auto_sub': True})
    conn.execute('DELETE FROM match_lineups')
    conn.commit()
    picks = _picks(conn, 1)

    idx._mark_auto_subs(conn, TEAM, 1, picks)

    assert all('subbed_in' not in p for p in picks)


def test_the_requested_round_selects_that_rounds_squad(conn):
    """Round 1 held Dugdale; round 2 holds Newman. Scrolling back must not show
    a player the manager only acquired later."""
    assert {p['name'] for p in _picks(conn, 1)} == {'Dugdale,S', 'Christie,T', 'Obano,B'}
    assert {p['name'] for p in _picks(conn, 2)} == {'Newman,A', 'Christie,T', 'Obano,B'}


def test_readonly_round_resolution_does_not_write(conn):
    """The view endpoints are GETs; only the write paths may materialise a round."""
    before = conn.execute('SELECT COUNT(*) FROM team_selections').fetchone()[0]

    r = idx._roster_round_readonly(conn, LEAGUE, TEAM, 3)

    assert r == 2
    assert conn.execute('SELECT COUNT(*) FROM team_selections').fetchone()[0] == before
