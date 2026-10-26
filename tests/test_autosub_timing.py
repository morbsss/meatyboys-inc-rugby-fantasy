"""WHEN auto-substitution resolves: at the Tuesday-noon rollover, never live.

The rule used to be purely a function of DB state, so a substitution took effect
the moment a team sheet landed - from the Thursday scrape onwards. Managers saw
their XV and their points rearrange themselves over the weekend, and the table
moved with them.

Worse, it was resolving off an incomplete picture. Sheets arrive one match at a
time, so a starter whose club had not published yet looked identical to a
starter who had been dropped, and could lose his place to a bench player at a
club that had published - a swap that then quietly reversed when his own sheet
arrived.

Both halves of that are fixed here: substitutions wait for the rollover (the
same break that makes a round's scores final, README §1), and absence only
counts as OUT for a club whose sheet we actually hold.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from api.competition import (
    effective_lineup, get_team_score, round_has_rolled_over,
)

ROUND = 2
TEAM = 'T'


def _conn(last_kickoff: datetime | None):
    """An OFDS team whose starter was left out and whose bench cover is playing.

    `last_kickoff` places the round in time; None leaves the calendar empty.
    """
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE leagues (league_id INT, slug TEXT);
        CREATE TABLE rounds (round_number INT, first_kickoff TEXT, last_kickoff TEXT,
                             league_id INT);
        CREATE TABLE players (player_id INT, name TEXT, team TEXT, position TEXT,
                              league_id INT);
        CREATE TABLE weekly_stats (player_id INT, round INT, total_points REAL, kicking REAL);
        CREATE TABLE match_lineups (round INT, real_team TEXT, player_name TEXT, is_bench INT);
        CREATE TABLE team_selections (team_name TEXT, player_id INT, round INT,
                                      is_captain INT, is_kicker INT, is_bench INT,
                                      league_id INT, jersey INT);
        CREATE TABLE team_front_row (team_name TEXT, round INT, club TEXT, league_id INT,
                                     is_captain INT DEFAULT 0, is_bench INT DEFAULT 0);
    ''')
    c.execute("INSERT INTO leagues VALUES (2, 'ofds')")
    c.executemany('INSERT INTO players VALUES (?,?,?,?,?)', [
        (1, 'Out,A',   'BAT', 'FH', 2),    # fantasy starter, left out by Bath
        (2, 'Cover,B', 'GLO', 'FH', 2),    # fantasy bench, in Gloucester's 23
    ])
    c.executemany('INSERT INTO weekly_stats VALUES (?,?,?,?)', [
        (1, 1, 0, 0), (1, 2, 0, 0),        # did not play: delta 0
        (2, 1, 0, 0), (2, 2, 12, 0),       # played: delta 12
    ])
    c.execute("INSERT INTO team_selections VALUES ('T', 1, 2, 0, 0, 0, 2, 1)")
    c.execute("INSERT INTO team_selections VALUES ('T', 2, 2, 0, 0, 1, 2, 16)")
    # Both clubs named a 23. Out,A is absent from Bath's, so he is genuinely out.
    c.executemany('INSERT INTO match_lineups VALUES (?,?,?,?)', [
        (ROUND, 'BAT', 'Teammate,T', 0),
        (ROUND, 'GLO', 'Cover,B', 1),      # among the replacements - valid cover
    ])
    if last_kickoff is not None:
        c.execute('INSERT INTO rounds VALUES (?, ?, ?, 2)',
                  (ROUND, last_kickoff.isoformat(), last_kickoff.isoformat()))
    c.commit()
    return c


def _names(eff):
    return sorted(p['name'] for p in eff)


# ---------------------------------------------------------------------------
# The gate: mid-round the squad stands as picked
# ---------------------------------------------------------------------------

def test_mid_round_the_squad_stands_as_picked():
    """A round still being played shows and scores the XV the manager named."""
    now = datetime.now(timezone.utc)
    c = _conn(now + timedelta(days=1))          # last kickoff still to come
    assert _names(effective_lineup(c, TEAM, ROUND)) == ['Out,A']
    c.close()


def test_mid_round_the_points_do_not_move_either():
    """The points half of the same rule. Out,A scored nothing and the squad
    scores nothing - the cover's 12 does not arrive until the rollover."""
    now = datetime.now(timezone.utc)
    c = _conn(now + timedelta(days=1))
    assert get_team_score(c, TEAM, ROUND) == pytest.approx(0)
    c.close()


def test_after_the_rollover_the_substitution_applies():
    now = datetime.now(timezone.utc)
    c = _conn(now - timedelta(days=14))         # rolled over a week ago at least
    assert _names(effective_lineup(c, TEAM, ROUND)) == ['Cover,B']
    assert get_team_score(c, TEAM, ROUND) == pytest.approx(12)
    c.close()


def test_an_unknown_calendar_still_substitutes():
    """With no `rounds` row there is no rollover to wait for, so the rule falls
    back to resolving. A finished round must not silently lose its
    substitutions just because its calendar entry went missing."""
    c = _conn(None)
    assert _names(effective_lineup(c, TEAM, ROUND)) == ['Cover,B']
    c.close()


def test_resolve_subs_overrides_the_calendar_both_ways():
    """api/predict.py passes resolve_subs=True: it is projecting the final,
    post-rollover score, so it has to field the XV that will settle."""
    now = datetime.now(timezone.utc)
    live = _conn(now + timedelta(days=1))
    assert _names(effective_lineup(live, TEAM, ROUND, resolve_subs=True)) == ['Cover,B']
    live.close()

    settled = _conn(now - timedelta(days=14))
    assert _names(effective_lineup(settled, TEAM, ROUND, resolve_subs=False)) == ['Out,A']
    settled.close()


# ---------------------------------------------------------------------------
# Absence is only absence for a club we have a sheet for
# ---------------------------------------------------------------------------

def test_a_club_with_no_sheet_cannot_leave_anyone_out():
    """`lineups` has written "ok / 0 entries" for weeks behind an ESPN 403, so a
    club with no rows is a real state. Reading that as "omitted" would drop the
    club's entire contingent and hand a manager someone else's points."""
    c = _conn(datetime.now(timezone.utc) - timedelta(days=14))
    c.execute("DELETE FROM match_lineups WHERE real_team = 'BAT'")
    c.commit()
    assert _names(effective_lineup(c, TEAM, ROUND)) == ['Out,A']
    assert get_team_score(c, TEAM, ROUND) == pytest.approx(0)
    c.close()


# ---------------------------------------------------------------------------
# The rollover instant itself, in the league's own timezone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('now_utc, rolled, why', [
    ('2026-09-29T10:59:00+00:00', False, 'a minute before noon BST'),
    ('2026-09-29T11:00:00+00:00', True,  'noon BST exactly'),
])
def test_the_break_is_local_noon_under_BST(now_utc, rolled, why):
    """Round ending Sun 27 Sep 2026 rolls over Tue 29 Sep at 12:00 London time,
    which is 11:00 UTC while BST is in force. Comparing in UTC would open the
    round an hour late for half the season."""
    c = _conn(datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc))
    assert round_has_rolled_over(
        c, ROUND, TEAM, now=datetime.fromisoformat(now_utc)) is rolled, why
    c.close()


@pytest.mark.parametrize('now_utc, rolled', [
    ('2026-12-29T11:59:00+00:00', False),
    ('2026-12-29T12:00:00+00:00', True),
])
def test_the_break_is_local_noon_under_GMT(now_utc, rolled):
    """The same noon in winter is 12:00 UTC. Round 8 of 2026-27 ends Mon 28 Dec
    17:00 and rolls the next day - the Monday finish is why finalize moved off
    its fixed Monday in the first place (api/scheduler.is_finalize_time)."""
    c = _conn(datetime(2026, 12, 28, 17, 0, tzinfo=timezone.utc))
    assert round_has_rolled_over(
        c, ROUND, TEAM, now=datetime.fromisoformat(now_utc)) is rolled
    c.close()
