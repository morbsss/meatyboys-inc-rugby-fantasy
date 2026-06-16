"""Per-team round scoring SQL (spec §5.4): cumulative deltas and captain
doubling. Kicking is always included (no designated kicker). Runs against an
in-memory SQLite DB."""

import sqlite3
import pytest

from api.competition import get_team_score


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE weekly_stats (player_id INT, round INT, total_points REAL, kicking REAL);
        CREATE TABLE team_selections (team_name TEXT, player_id INT, round INT,
                                      is_captain INT, is_kicker INT);
        CREATE TABLE team_front_row (team_name TEXT, round INT, club TEXT, league_id INT,
                                     is_captain INT DEFAULT 0, is_bench INT DEFAULT 0);
    ''')
    # Round 1 baselines and round 2 cumulative totals.
    ws = [
        # player, round, total, kicking
        (1, 1, 10, 2), (1, 2, 25, 5),    # non-kicker:  base 15, kick 3
        (2, 1, 10, 4), (2, 2, 30, 10),   # kicker:      base 20, kick 6
        (3, 1, 5, 1),  (3, 2, 20, 4),    # captain:     base 15, kick 3
    ]
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?)', ws)
    sel = [
        ('T', 1, 2, 0, 0),   # non-kicker, non-captain
        ('T', 2, 2, 0, 1),   # kicker
        ('T', 3, 2, 1, 0),   # captain
    ]
    c.executemany('INSERT INTO team_selections VALUES (?,?,?,?,?)', sel)
    c.commit()
    return c


def test_team_score_always_counts_kicking_with_captain_double(conn):
    # Kicking is always credited (total_points already includes it):
    #   player 1: base 25 - 10 = 15
    #   player 2: base 30 - 10 = 20
    #   captain:  (base 20 - 5 = 15) * 2 = 30
    assert get_team_score(conn, 'T', 2) == pytest.approx(15 + 20 + 30)


def test_empty_team_scores_zero(conn):
    assert get_team_score(conn, 'Nobody', 2) == 0.0


def test_front_row_unit_doubles_when_captain():
    """The club front-row unit scores like any player — and doubles when it's
    the captain."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INT, name TEXT, team TEXT, position TEXT, league_id INT);
        CREATE TABLE weekly_stats (player_id INT, round INT, total_points REAL, kicking REAL);
        CREATE TABLE match_lineups (round INT, real_team TEXT, player_name TEXT);
        CREATE TABLE team_selections (team_name TEXT, player_id INT, round INT, is_captain INT, is_kicker INT);
        CREATE TABLE team_front_row (team_name TEXT, round INT, club TEXT, league_id INT,
                                     is_captain INT DEFAULT 0, is_bench INT DEFAULT 0);
    ''')
    c.execute("INSERT INTO players VALUES (100, 'Prop,A', 'BRI', 'PR', 1)")
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?)', [(100, 1, 5, 0), (100, 2, 15, 0)])
    c.execute("INSERT INTO team_front_row VALUES ('T', 2, 'BRI', 1, 0, 0)")  # not captain
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(10)        # base delta 15 - 5
    c.execute("UPDATE team_front_row SET is_captain = 1 WHERE team_name = 'T'")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(20)        # doubled as captain


def test_super_rugby_front_row_scores_from_unit_player():
    """Super Rugby front row is one pre-aggregated 'FR' player per club; it scores
    from its own points delta and doubles as captain."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE players (player_id INT, name TEXT, team TEXT, position TEXT, league_id INT);
        CREATE TABLE weekly_stats (player_id INT, round INT, total_points REAL, kicking REAL);
        CREATE TABLE match_lineups (round INT, real_team TEXT, player_name TEXT);
        CREATE TABLE team_selections (team_name TEXT, player_id INT, round INT, is_captain INT, is_kicker INT);
        CREATE TABLE team_front_row (team_name TEXT, round INT, club TEXT, league_id INT,
                                     is_captain INT DEFAULT 0, is_bench INT DEFAULT 0);
    ''')
    c.execute("INSERT INTO players VALUES (500, 'Chiefs Front Row', 'Chiefs', 'FR', 1)")
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?)', [(500, 1, 8, 0), (500, 2, 20, 0)])
    c.execute("INSERT INTO team_front_row VALUES ('T', 2, 'Chiefs', 1, 0, 0)")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(12)        # 20 - 8
    c.execute("UPDATE team_front_row SET is_captain = 1 WHERE team_name = 'T'")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(24)        # doubled as captain


# ---------------------------------------------------------------------------
# OFDS full-XV scoring with real-lineup auto-substitution (rule 4)
# ---------------------------------------------------------------------------

def _ofds_conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE leagues (league_id INT, slug TEXT);
        CREATE TABLE players (player_id INT, name TEXT, team TEXT, position TEXT, league_id INT);
        CREATE TABLE weekly_stats (player_id INT, round INT, total_points REAL, kicking REAL);
        CREATE TABLE match_lineups (round INT, real_team TEXT, player_name TEXT, is_bench INT);
        CREATE TABLE team_selections (team_name TEXT, player_id INT, round INT,
                                      is_captain INT, is_kicker INT, is_bench INT, league_id INT);
    ''')
    c.execute("INSERT INTO leagues VALUES (2, 'ofds')")
    c.executemany('INSERT INTO players VALUES (?,?,?,?,?)', [
        (1, 'Aaa,A', 'Bath', 'PR', 2),   # fantasy starter
        (2, 'Bbb,B', 'Bath', 'PR', 2),   # fantasy bench (same position)
    ])
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?)', [
        (1, 1, 0, 0), (1, 2, 100, 0),    # starter delta 100
        (2, 1, 0, 0), (2, 2, 5, 0),      # bench delta 5
    ])
    c.execute("INSERT INTO team_selections VALUES ('T', 1, 2, 0, 0, 0, 2)")   # starter
    c.execute("INSERT INTO team_selections VALUES ('T', 2, 2, 0, 0, 1, 2)")   # bench
    return c


def test_ofds_auto_sub_swaps_in_bench_when_starter_absent():
    c = _ofds_conn()
    # Real XV: only the BENCH player started for real → it subs in for the starter.
    c.execute("INSERT INTO match_lineups VALUES (2, 'Bath', 'Bbb,B', 0)")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(5)         # bench's delta, not 100


def test_ofds_keeps_starter_when_present_ignores_bench():
    c = _ofds_conn()
    # Real XV: the STARTER started → no sub; bench ignored.
    c.execute("INSERT INTO match_lineups VALUES (2, 'Bath', 'Aaa,A', 0)")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(100)


def test_ofds_subbed_in_captain_doubles():
    c = _ofds_conn()
    c.execute("UPDATE team_selections SET is_captain = 1 WHERE player_id = 2")   # bench is captain
    c.execute("INSERT INTO match_lineups VALUES (2, 'Bath', 'Bbb,B', 0)")
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(10)        # 5 doubled


def test_ofds_no_lineup_scores_named_starters():
    c = _ofds_conn()   # no match_lineups rows
    c.commit()
    assert get_team_score(c, 'T', 2) == pytest.approx(100)       # starter as picked
