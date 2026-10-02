"""Two different players can share a formatted name.

SuperBru's feed currently contains eight such names - Wheeler,C · Mann,J ·
Thomas,F · Thomas,O · Bennett,J · Williams,T · Williams,S · Wilson,T - because
"Surname,Initial" is not unique across 650 players at ten clubs:

    Thomas,F    LEI/HK  and  GLO/LK
    Williams,T  HAR/LF  and  SAR/SH

A scorer that keys on the NAME alone silently collapses them: whichever row the
feed yields last wins, and both players end up holding the same points. That is
not hypothetical - it put SAR's Williams,T 10 points onto HAR's Williams,T, and
GLO's Thomas,F 2.5 onto LEI's, which flowed straight into a manager's total.

Identity for SCORING is (name, team, position). That is narrower than identity
for the ROSTER, which is (name, position) so a transfer updates in place rather
than inserting a duplicate - see _player_id. The difference is deliberate: a
scraped score row always states a club, so it can and must be matched exactly.
"""

import sqlite3

import pytest

from api import ingest
from api.datasource.base import ScoreRecord

LEAGUE, COMP, ROUND = 2, 'premiership', 1


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, team TEXT, position TEXT, league_id INTEGER,
            UNIQUE(name, team, position));
        CREATE TABLE weekly_stats (id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER, round INTEGER, total_points REAL, price REAL,
            kicking REAL, points_per_game TEXT, popularity TEXT, form TEXT,
            scraped_at TEXT, league_id INTEGER, UNIQUE(player_id, round));
    ''')
    c.executemany('INSERT INTO players (name, team, position, league_id) VALUES (?,?,?,?)',
                  [('Williams,T', 'HAR', 'LF', LEAGUE),
                   ('Williams,T', 'SAR', 'SH', LEAGUE),
                   ('Obano,B', 'BAT', 'PR', LEAGUE)])
    c.commit()
    return c


def _feed(monkeypatch, rows):
    class Src:
        def fetch_player_scores(self, competition, round_number):
            return [ScoreRecord(name=n, team=t, position=p, total_points=pts,
                                price=0.0, kicking=0.0, points_per_game='',
                                popularity='', form='') for n, t, p, pts in rows]
    monkeypatch.setattr(ingest, 'get_score_source', lambda: Src())


def _points(conn, name, team):
    row = conn.execute(
        'SELECT w.total_points FROM weekly_stats w JOIN players p'
        ' ON p.player_id = w.player_id'
        ' WHERE p.name=? AND p.team=? AND w.round=?', (name, team, ROUND)).fetchone()
    return row['total_points'] if row else None


def test_namesakes_at_different_clubs_keep_their_own_points(conn, monkeypatch):
    """The live failure: both Williams,T rows ended up on 10.0."""
    _feed(monkeypatch, [('Williams,T', 'HAR', 'LF', 0.0),
                        ('Williams,T', 'SAR', 'SH', 10.0)])

    ingest.ingest_player_scores(conn, LEAGUE, COMP, ROUND)

    assert _points(conn, 'Williams,T', 'HAR') == 0.0
    assert _points(conn, 'Williams,T', 'SAR') == 10.0


def test_feed_order_does_not_decide_the_winner(conn, monkeypatch):
    """Keying by name alone makes the LAST row win. Reversing the feed must not
    change a single stored value."""
    _feed(monkeypatch, [('Williams,T', 'SAR', 'SH', 10.0),
                        ('Williams,T', 'HAR', 'LF', 0.0)])

    ingest.ingest_player_scores(conn, LEAGUE, COMP, ROUND)

    assert _points(conn, 'Williams,T', 'HAR') == 0.0
    assert _points(conn, 'Williams,T', 'SAR') == 10.0


def test_no_extra_player_rows_are_created(conn, monkeypatch):
    """Both already exist; scoring must match them, not insert a third."""
    _feed(monkeypatch, [('Williams,T', 'HAR', 'LF', 1.0),
                        ('Williams,T', 'SAR', 'SH', 2.0)])

    ingest.ingest_player_scores(conn, LEAGUE, COMP, ROUND)

    assert conn.execute("SELECT COUNT(*) FROM players WHERE name='Williams,T'").fetchone()[0] == 2


def test_an_ordinary_name_is_unaffected(conn, monkeypatch):
    _feed(monkeypatch, [('Obano,B', 'BAT', 'PR', 7.5)])

    ingest.ingest_player_scores(conn, LEAGUE, COMP, ROUND)

    assert _points(conn, 'Obano,B', 'BAT') == 7.5


def test_same_name_and_position_at_different_clubs_stays_separate(conn, monkeypatch):
    """Wilson,T plays OBK for both Bristol and Sale - name AND position collide,
    so only the club tells them apart."""
    conn.executemany('INSERT INTO players (name, team, position, league_id) VALUES (?,?,?,?)',
                     [('Wilson,T', 'BRI', 'OBK', LEAGUE), ('Wilson,T', 'SAL', 'OBK', LEAGUE)])
    conn.commit()
    _feed(monkeypatch, [('Wilson,T', 'BRI', 'OBK', 3.0),
                        ('Wilson,T', 'SAL', 'OBK', 8.0)])

    ingest.ingest_player_scores(conn, LEAGUE, COMP, ROUND)

    assert _points(conn, 'Wilson,T', 'BRI') == 3.0
    assert _points(conn, 'Wilson,T', 'SAL') == 8.0
