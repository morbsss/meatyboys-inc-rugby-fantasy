"""The daily roster sync: new signings and transfers, with SuperBru as truth.

Why it exists. `players.team` is what the lineup join matches on, so a player
listed at the wrong club is invisible on a team sheet — no S/B/O badge, no
auto-substitution, no score. Dun,J sat at Bristol in production while ESPN had
him starting at lock for Harlequins, so his sheet entry joined to nothing.

Why it is its own job rather than part of live_scoring: squads change mid-week,
and live_scoring only runs inside match windows. A Tuesday transfer has to be in
the table before Thursday's sheets arrive.

The bug it replaces: `_player_id` upserted `ON CONFLICT (name, team, position)` —
the club IS in that key, so a transfer never conflicted and inserted a SECOND
row. The player then existed twice, the original stopped receiving scores, and
any squad holding them was frozen at the old club.
"""

import sqlite3

import pytest

from api import ingest
from api.datasource.base import PlayerRecord

LEAGUE = 2
COMP = 'premiership'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INTEGER PRIMARY KEY AUTOINCREMENT,
                              name TEXT NOT NULL, team TEXT, position TEXT,
                              league_id INTEGER, UNIQUE(name, team, position));
        CREATE TABLE team_selections (league_id INTEGER, round INTEGER,
                                      team_name TEXT, player_id INTEGER,
                                      is_bench INTEGER DEFAULT 0);
    ''')
    c.executemany(
        'INSERT INTO players (name, team, position, league_id) VALUES (?,?,?,?)',
        [('Dun,J', 'BRI', 'LK', LEAGUE),
         ('Obano,B', 'BAT', 'PR', LEAGUE),
         ('Wilson,T', 'BRI', 'OBK', LEAGUE),
         ('Wilson,T', 'SAL', 'OBK', LEAGUE)])
    c.commit()
    return c


def _feed(monkeypatch, rows):
    """Stub SuperBru with these (name, team, position) rows."""
    class Src:
        def fetch_players(self, competition):
            return [PlayerRecord(name=n, team=t, position=p, price=0.0)
                    for n, t, p in rows]
    monkeypatch.setattr(ingest, 'get_player_source', lambda: Src())


def _rows(conn, name):
    return [dict(r) for r in conn.execute(
        'SELECT player_id, name, team, position FROM players WHERE name = ?'
        ' ORDER BY player_id', (name,))]


# --- transfers --------------------------------------------------------------

def test_transfer_updates_the_club_in_place(conn, monkeypatch):
    """Dun,J: Bristol -> Harlequins. One row, same player_id."""
    before = _rows(conn, 'Dun,J')[0]['player_id']
    _feed(monkeypatch, [('Dun,J', 'HAR', 'LK')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    after = _rows(conn, 'Dun,J')
    assert len(after) == 1, 'a transfer must not duplicate the player'
    assert after[0]['team'] == 'HAR'
    assert after[0]['player_id'] == before, 'player_id must be stable'
    assert counts['transferred'] == 1


def test_transfer_keeps_the_player_in_squads_that_drafted_him(conn, monkeypatch):
    """The point of updating in place: selections reference player_id."""
    pid = _rows(conn, 'Dun,J')[0]['player_id']
    conn.execute('INSERT INTO team_selections (league_id, round, team_name, player_id)'
                 ' VALUES (?,?,?,?)', (LEAGUE, 1, "Smith's Pizza", pid))
    conn.commit()
    _feed(monkeypatch, [('Dun,J', 'HAR', 'LK')])

    ingest.ingest_players(conn, LEAGUE, COMP)

    still = conn.execute(
        'SELECT p.team FROM team_selections ts JOIN players p'
        ' ON p.player_id = ts.player_id WHERE ts.player_id = ?', (pid,)).fetchone()
    assert still['team'] == 'HAR'


def test_the_old_bug_would_have_duplicated(conn, monkeypatch):
    """Guards the fix directly: (name, team, position) as the key means a moved
    player does not conflict, so the insert path would create a second row."""
    _feed(monkeypatch, [('Dun,J', 'HAR', 'LK')])

    ingest.ingest_players(conn, LEAGUE, COMP)

    assert conn.execute("SELECT COUNT(*) FROM players WHERE name='Dun,J'"
                        ).fetchone()[0] == 1


def test_same_name_different_position_is_a_different_player(conn, monkeypatch):
    """Identity is (name, position). A prop named Dun,J is not the lock."""
    _feed(monkeypatch, [('Dun,J', 'HAR', 'PR')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['added'] == 1
    assert counts['transferred'] == 0
    assert {r['team'] for r in _rows(conn, 'Dun,J')} == {'BRI', 'HAR'}


# --- ambiguity --------------------------------------------------------------

def test_two_real_namesakes_are_never_moved(conn, monkeypatch):
    """Wilson,T (OBK) genuinely plays for both Bristol and Sale. Seeing him at a
    third club must not pick one at random to move."""
    _feed(monkeypatch, [('Wilson,T', 'HAR', 'OBK')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['ambiguous'] == 1
    assert counts['transferred'] == 0
    assert {r['team'] for r in _rows(conn, 'Wilson,T')} == {'BRI', 'SAL'}


def test_an_exact_match_among_namesakes_is_left_alone(conn, monkeypatch):
    _feed(monkeypatch, [('Wilson,T', 'SAL', 'OBK')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['unchanged'] == 1
    assert counts['ambiguous'] == 0


# --- new and departed -------------------------------------------------------

def test_a_new_signing_is_added(conn, monkeypatch):
    _feed(monkeypatch, [('Newman,A', 'GLO', 'FH')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['added'] == 1
    assert _rows(conn, 'Newman,A')[0]['team'] == 'GLO'


def test_a_player_no_longer_listed_is_counted_but_kept(conn, monkeypatch):
    """Never delete: a drafted player must not vanish from someone's squad."""
    _feed(monkeypatch, [('Obano,B', 'BAT', 'PR')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['departed'] == 3          # Dun,J + both Wilson,T rows
    assert conn.execute('SELECT COUNT(*) FROM players').fetchone()[0] == 4


def test_an_upstream_rename_shows_as_a_named_add_and_departure(conn, monkeypatch):
    """SuperBru currently lists Northampton's Walker,H as 'WalkerNOPE,H' — their
    typo. Identity is the name, so this can only arrive as one addition plus one
    departure; naming both in the log is what makes it recognisable as a rename
    rather than a signing."""
    conn.execute('INSERT INTO players (name, team, position, league_id)'
                 ' VALUES (?,?,?,?)', ('Walker,H', 'NOR', 'HK', LEAGUE))
    conn.commit()
    _feed(monkeypatch, [('WalkerNOPE,H', 'NOR', 'HK')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['added'] == 1
    assert counts['new'] == ['WalkerNOPE,H (NOR)']
    assert 'Walker,H (NOR)' in counts['gone']
    # Both rows survive: renaming a player row is an identity change, so it is
    # reported for a human rather than applied on a guess.
    assert {r['name'] for r in conn.execute(
        "SELECT name FROM players WHERE team='NOR'")} == {'Walker,H', 'WalkerNOPE,H'}


def test_an_empty_scrape_changes_nothing(conn, monkeypatch):
    """A failed scrape is not 'every player left the league'."""
    _feed(monkeypatch, [])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts == {'added': 0, 'transferred': 0, 'unchanged': 0,
                      'departed': 0, 'ambiguous': 0,
                      'moves': [], 'new': [], 'gone': []}
    assert conn.execute('SELECT COUNT(*) FROM players').fetchone()[0] == 4


# --- general ----------------------------------------------------------------

def test_is_idempotent(conn, monkeypatch):
    feed = [('Dun,J', 'HAR', 'LK'), ('Obano,B', 'BAT', 'PR')]
    _feed(monkeypatch, feed)

    first = ingest.ingest_players(conn, LEAGUE, COMP)
    second = ingest.ingest_players(conn, LEAGUE, COMP)

    assert first['transferred'] == 1
    assert second['transferred'] == 0
    assert second['unchanged'] == 2
    assert conn.execute('SELECT COUNT(*) FROM players').fetchone()[0] == 4


def test_moves_are_named_for_the_job_log(conn, monkeypatch):
    """A transfer silently changes who a drafted player is — the log should say
    which, not just how many."""
    _feed(monkeypatch, [('Dun,J', 'HAR', 'LK')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['moves'] == ['Dun,J BRI->HAR']


def test_incomplete_rows_are_skipped(conn, monkeypatch):
    _feed(monkeypatch, [('', 'HAR', 'LK'), ('X,Y', '', 'LK'), ('Z,A', 'HAR', '')])

    counts = ingest.ingest_players(conn, LEAGUE, COMP)

    assert counts['added'] == 0


def test_another_league_is_untouched(conn, monkeypatch):
    conn.execute('INSERT INTO players (name, team, position, league_id)'
                 ' VALUES (?,?,?,?)', ('Dun,J', 'Blues', 'LK', 1))
    conn.commit()
    _feed(monkeypatch, [('Dun,J', 'HAR', 'LK')])

    ingest.ingest_players(conn, LEAGUE, COMP)

    other = conn.execute('SELECT team FROM players WHERE league_id = 1').fetchone()
    assert other['team'] == 'Blues'
