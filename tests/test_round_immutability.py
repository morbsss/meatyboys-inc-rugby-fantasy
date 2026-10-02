"""A completed round's team sheet is immutable.

The bug this exists for, reported by a manager: after the Tuesday rollover into
round 2 they made a trade, and their **round 1 score changed**.

Cause. `_roster_round` answered "which round does this team's squad live on?"
with `MAX(round <= next_round)`. Nothing created rows for the new round at the
rollover — `_carry_forward_picks` only ran from `live_scoring`, which does not
fire until a round-2 match is actually underway — so immediately after the
rollover the newest rows were still round 1's. Trades therefore did:

    UPDATE team_selections SET player_id = ? WHERE ... AND round = 1

rewriting the settled team sheet in place. Round 1's score is computed from those
rows, so it moved. An UPDATE does not touch `scraped_at`, so it left no trace.

Meanwhile a squad SAVE wrote to `next_round`, so the two write paths disagreed
about which round "now" was.

The invariant: writes only ever touch the ACTIVE round; every earlier round is
frozen.
"""

import sqlite3

import pytest

from api import index as idx

LEAGUE = 2
TEAM = 'London Waspcester'
OTHER = 'PizzaSmith'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE team_selections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round INTEGER NOT NULL, team_name TEXT NOT NULL, player_id INTEGER NOT NULL,
            is_captain INTEGER DEFAULT 0, is_kicker INTEGER DEFAULT 0,
            is_bench INTEGER DEFAULT 0, jersey INTEGER,
            scraped_at TEXT, league_id INTEGER,
            UNIQUE(round, team_name, player_id));
    ''')
    rows = [(1, TEAM, pid, 0, 0, 1 if pid > 102 else 0, pid - 100, '2026-09-25T10:00', LEAGUE)
            for pid in (101, 102, 103)]
    rows += [(1, OTHER, pid, 0, 0, 0, pid - 200, '2026-09-25T10:00', LEAGUE)
             for pid in (201, 202)]
    c.executemany('INSERT INTO team_selections (round, team_name, player_id, is_captain,'
                  ' is_kicker, is_bench, jersey, scraped_at, league_id)'
                  ' VALUES (?,?,?,?,?,?,?,?,?)', rows)
    c.commit()
    return c


def _squad(conn, rnd, team=TEAM):
    return sorted(r[0] for r in conn.execute(
        'SELECT player_id FROM team_selections WHERE league_id=? AND team_name=? AND round=?',
        (LEAGUE, team, rnd)))


# --- the reported bug -------------------------------------------------------

def test_a_trade_after_the_rollover_leaves_round_1_untouched(conn):
    """The exact sequence the manager hit: roll to round 2, then trade."""
    before = _squad(conn, 1)

    rnd = idx._roster_round(conn, LEAGUE, TEAM, 2)
    idx._swap_player(conn, LEAGUE, TEAM, rnd, out_id=103, in_id=999)
    conn.commit()

    assert _squad(conn, 1) == before, 'round 1 was rewritten by a round 2 trade'
    assert 999 in _squad(conn, 2)
    assert 103 not in _squad(conn, 2)


def test_writes_target_the_active_round_not_the_latest_one(conn):
    """`_roster_round` used to return 1 here, which is the whole bug."""
    assert idx._roster_round(conn, LEAGUE, TEAM, 2) == 2


def test_the_new_round_inherits_the_previous_squad(conn):
    """A manager who does nothing still fields the side they had."""
    idx._roster_round(conn, LEAGUE, TEAM, 2)

    assert _squad(conn, 2) == _squad(conn, 1)


def test_lineup_slots_carry_across(conn):
    """Bench flags and jerseys come too, or everyone starts on the bench."""
    idx._roster_round(conn, LEAGUE, TEAM, 2)

    got = {r['player_id']: (r['is_bench'], r['jersey']) for r in conn.execute(
        'SELECT player_id, is_bench, jersey FROM team_selections '
        'WHERE league_id=? AND team_name=? AND round=2', (LEAGUE, TEAM))}
    assert got == {101: (0, 1), 102: (0, 2), 103: (1, 3)}


# --- the carry-forward must not resurrect dropped players -------------------

def test_carry_forward_skips_a_team_that_already_edited_the_round(conn):
    """The latent flaw in the old league-wide copy. A manager drops a player in
    round 2; the next carry-forward must not put him back."""
    idx._ensure_round_squad(conn, LEAGUE, 2)
    conn.execute('DELETE FROM team_selections WHERE league_id=? AND team_name=?'
                 ' AND round=2 AND player_id=103', (LEAGUE, TEAM))
    conn.commit()

    idx._carry_forward_picks(conn, LEAGUE, 2)

    assert 103 not in _squad(conn, 2), 'a dropped player was resurrected'


def test_carry_forward_is_idempotent(conn):
    for _ in range(3):
        idx._ensure_round_squad(conn, LEAGUE, 2)

    assert _squad(conn, 2) == [101, 102, 103]


def test_it_seeds_every_team_not_just_one(conn):
    idx._ensure_round_squad(conn, LEAGUE, 2)

    assert _squad(conn, 2, OTHER) == [201, 202]


def test_a_team_that_missed_a_round_still_carries_forward(conn):
    """Copy from the team's OWN most recent squad, not blindly from round-1 —
    a team can go a round with no save and no trade."""
    idx._ensure_round_squad(conn, LEAGUE, 2)

    idx._ensure_round_squad(conn, LEAGUE, 4)      # nothing at round 3 at all

    assert _squad(conn, 4) == [101, 102, 103]


def test_round_1_is_never_seeded(conn):
    """There is no round 0 to copy from, and the draft owns round 1."""
    assert idx._ensure_round_squad(conn, LEAGUE, 1) == 0


def test_other_leagues_are_untouched(conn):
    conn.execute('INSERT INTO team_selections (round, team_name, player_id, league_id)'
                 ' VALUES (1, ?, 301, 1)', (TEAM,))
    conn.commit()

    idx._ensure_round_squad(conn, LEAGUE, 2)

    assert conn.execute('SELECT COUNT(*) FROM team_selections WHERE league_id=1'
                        ' AND round=2').fetchone()[0] == 0


def test_every_earlier_round_stays_frozen_across_several_rollovers(conn):
    """Walk three rounds of trades and assert nothing behind the cursor moves."""
    idx._swap_player(conn, LEAGUE, TEAM, idx._roster_round(conn, LEAGUE, TEAM, 2), 103, 993)
    snapshot_1 = _squad(conn, 1)
    snapshot_2 = _squad(conn, 2)

    idx._swap_player(conn, LEAGUE, TEAM, idx._roster_round(conn, LEAGUE, TEAM, 3), 993, 994)
    conn.commit()

    assert _squad(conn, 1) == snapshot_1
    assert _squad(conn, 2) == snapshot_2
    assert 994 in _squad(conn, 3)


# --- finalize must not be blocked by a run that predates the round -----------

@pytest.fixture
def jobs_conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER, job TEXT, round_number INTEGER, status TEXT,
            detail TEXT, run_at TEXT);
        CREATE TABLE rounds (round_number INTEGER, first_kickoff TEXT,
            last_kickoff TEXT, league_id INTEGER);
    ''')
    c.execute("INSERT INTO rounds VALUES (1, '2026-09-25T18:45:00+00:00',"
              " '2026-09-27T14:00:00+00:00', ?)", (LEAGUE,))
    c.commit()
    return c


def _finalize_run(conn, when, round_number=1, status='ok'):
    conn.execute("INSERT INTO job_runs (league_id, job, round_number, status, run_at)"
                 " VALUES (?, 'finalize', ?, ?, ?)", (LEAGUE, round_number, status, when))
    conn.commit()


def test_a_finalize_logged_before_the_round_does_not_count(jobs_conn):
    """The live failure. A mock-era finalize from 24 August claimed round 1, so
    the real one on Tuesday 29 September never ran — leaving round 1 frozen at a
    scrape taken mid-match, and every later round carrying the correction."""
    _finalize_run(jobs_conn, '2026-08-24T11:00:02+00:00')

    assert idx._finalize_done(jobs_conn, LEAGUE, 1) is False


def test_a_finalize_after_the_last_kickoff_does_count(jobs_conn):
    _finalize_run(jobs_conn, '2026-09-29T11:00:00+00:00')

    assert idx._finalize_done(jobs_conn, LEAGUE, 1) is True


def test_no_finalize_at_all_is_not_done(jobs_conn):
    assert idx._finalize_done(jobs_conn, LEAGUE, 1) is False


def test_a_failed_finalize_does_not_count(jobs_conn):
    _finalize_run(jobs_conn, '2026-09-29T11:00:00+00:00', status='error')

    assert idx._finalize_done(jobs_conn, LEAGUE, 1) is False


def test_an_unknown_round_falls_back_to_the_run_existing(jobs_conn):
    """No calendar row to compare against — trust the log rather than re-running
    a definitive scrape against a round we know nothing about."""
    _finalize_run(jobs_conn, '2026-08-24T11:00:02+00:00', round_number=9)

    assert idx._finalize_done(jobs_conn, LEAGUE, 9) is True
