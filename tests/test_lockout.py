"""Weekly lockout: the Tuesday-noon round rollover.

A round stays current from its first kickoff until the following Tuesday at
12:00 in the league's own timezone. That single boundary decides three things
at once - when the round rolls, when its table goes final, and when squads and
transfers reopen - so these tests walk a real OFDS week hour by hour.

The regression they exist to catch: the round used to roll over at its *last*
kickoff, which reopened squads on Sunday afternoon (before Monday's finalize
scrape) and left the round's final match being scored against the next round's
fixture list.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from api import index as idx

LON = 'Europe/London'


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """`datetime` with a pinned `now()`; everything else is inherited."""
    _now = _utc(2026, 9, 23, 12)

    @classmethod
    def now(cls, tz=None):
        return cls._now.astimezone(tz) if tz else cls._now.replace(tzinfo=None)


@pytest.fixture
def conn():
    """An OFDS-shaped DB: league 2 = ofds (Europe/London) with four rounds."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE leagues (league_id INTEGER PRIMARY KEY, slug TEXT,
                              name TEXT, competition TEXT, theme TEXT);
        CREATE TABLE rounds (round_number INTEGER, first_kickoff TEXT,
                             last_kickoff TEXT, league_id INTEGER);
        CREATE TABLE weekly_stats (player_id INTEGER, round INTEGER, league_id INTEGER);
    ''')
    c.execute("INSERT INTO leagues VALUES (2, 'ofds', 'OFDS', 'premiership', 'union')")
    c.executemany(
        'INSERT INTO rounds (round_number, first_kickoff, last_kickoff, league_id) '
        'VALUES (?, ?, ?, 2)',
        [
            # Real 2026-27 Premiership rounds 1, 2, 4 and 5. Round 4 finishes on
            # the day the clocks go back, so it straddles the BST→GMT switch.
            (1, '2026-09-25T18:45:00+00:00', '2026-09-27T14:00:00+00:00'),
            (2, '2026-10-02T18:45:00+00:00', '2026-10-04T14:00:00+00:00'),
            (4, '2026-10-23T18:45:00+00:00', '2026-10-25T15:00:00+00:00'),
            (5, '2026-10-30T19:45:00+00:00', '2026-10-31T17:30:00+00:00'),
        ])
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _unlocked(monkeypatch):
    """Production lock semantics by default (the env override is tested separately)."""
    monkeypatch.setattr(idx, 'ALLOW_UNRESTRICTED_EDITS', False)


def _at(monkeypatch, moment):
    """Freeze the clock inside api.index at `moment`."""
    monkeypatch.setattr(_FrozenDatetime, '_now', moment)
    monkeypatch.setattr(idx, 'datetime', _FrozenDatetime)


# ---------------------------------------------------------------------------
# _rollover_at - the pure Tuesday-noon arithmetic
# ---------------------------------------------------------------------------

def test_rollover_is_the_tuesday_after_a_sunday_finish():
    # Sun 27 Sep 14:00 UTC → Tue 29 Sep 12:00 BST (= 11:00 UTC).
    assert idx._rollover_at(_utc(2026, 9, 27, 14), LON) == \
        datetime.fromisoformat('2026-09-29T12:00:00+01:00')


def test_rollover_follows_a_monday_night_finish_to_the_next_day():
    # Boxing Day round: last fixture Mon 28 Dec 17:00 → Tue 29 Dec noon.
    assert idx._rollover_at(_utc(2026, 12, 28, 17), LON) == \
        datetime.fromisoformat('2026-12-29T12:00:00+00:00')


def test_rollover_is_strictly_after_so_noon_itself_rolls_a_week():
    # A (hypothetical) fixture finishing exactly at the rollover must not make
    # the round roll over on top of itself.
    noon_tuesday = datetime.fromisoformat('2026-09-29T12:00:00+01:00')
    assert idx._rollover_at(noon_tuesday, LON) == \
        datetime.fromisoformat('2026-10-06T12:00:00+01:00')


def test_rollover_is_local_noon_across_the_dst_switch():
    """Noon stays noon for managers; only the UTC offset moves."""
    bst = idx._rollover_at(_utc(2026, 9, 27, 14), LON)    # BST, UTC+1
    gmt = idx._rollover_at(_utc(2026, 10, 25, 15), LON)   # GMT, UTC+0
    assert (bst.hour, gmt.hour) == (12, 12)
    assert bst.utcoffset().total_seconds() == 3600
    assert gmt.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# A week in the life of round 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('moment, expected_round, expected_locked, why', [
    (_utc(2026, 9, 23, 12),     1, False, 'Wednesday: open, picking for round 1'),
    (_utc(2026, 9, 25, 18, 44), 1, False, 'one minute before the first kickoff'),
    (_utc(2026, 9, 25, 18, 46), 1, True,  'first kickoff has passed'),
    (_utc(2026, 9, 26, 12),     1, True,  'Saturday, mid-round'),
    (_utc(2026, 9, 27, 20),     1, True,  'Sunday evening, after the last kickoff'),
    (_utc(2026, 9, 28, 12),     1, True,  'Monday, while finalize runs'),
    (_utc(2026, 9, 29, 10, 59), 1, True,  'Tue 11:59 BST, one minute short'),
    (_utc(2026, 9, 29, 11, 1),  2, False, 'Tue 12:01 BST: rolled over and open'),
])
def test_week_of_round_one(conn, monkeypatch, moment, expected_round,
                           expected_locked, why):
    _at(monkeypatch, moment)
    assert idx.get_next_round(conn, 2) == expected_round, why
    assert idx.is_locked(conn, 2) is expected_locked, why


def test_sunday_evening_does_not_reopen_the_squad(conn, monkeypatch):
    """The specific regression: rolling at the last kickoff unlocked here."""
    _at(monkeypatch, _utc(2026, 9, 27, 14, 30))   # 30 min after the last kickoff
    assert idx.get_next_round(conn, 2) == 1
    assert idx.is_locked(conn, 2) is True


def test_rollover_lands_on_local_noon_across_dst(conn, monkeypatch):
    # Round 4 finishes Sun 25 Oct, the day the clocks go back.
    _at(monkeypatch, _utc(2026, 10, 27, 11, 59))
    assert idx.get_next_round(conn, 2) == 4        # 11:59 GMT - still round 4
    _at(monkeypatch, _utc(2026, 10, 27, 12, 1))
    assert idx.get_next_round(conn, 2) == 5        # 12:01 GMT - rolled over


# ---------------------------------------------------------------------------
# The countdown the UI shows
# ---------------------------------------------------------------------------

def test_reopen_time_is_the_rollover_not_the_last_kickoff(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 26, 12))
    assert idx.reopen_time(conn, 1, 2) == '2026-09-29T12:00:00+01:00'


def test_next_lock_time_is_the_first_kickoff(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 23, 12))
    assert idx.next_lock_time(conn, 1, 2) == '2026-09-25T18:45:00+00:00'


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_zero_length_round_still_locks(conn, monkeypatch):
    """Rounds 10-20 have unconfirmed times: every fixture shares one slot, so
    first_kickoff == last_kickoff. They used to have no lock window at all."""
    conn.executemany('INSERT INTO rounds VALUES (?, ?, ?, 2)', [
        (10, '2027-01-23T15:00:00+00:00', '2027-01-23T15:00:00+00:00'),
        (11, '2027-03-20T15:00:00+00:00', '2027-03-20T15:00:00+00:00'),
    ])
    conn.commit()
    _at(monkeypatch, _utc(2027, 1, 23, 16))       # an hour after the one kickoff
    assert idx.get_next_round(conn, 2) == 10
    assert idx.is_locked(conn, 2) is True
    _at(monkeypatch, _utc(2027, 1, 26, 12, 1))    # Tue 12:01 GMT
    assert idx.is_locked(conn, 2) is False


def test_preseason_is_open(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    assert idx.get_next_round(conn, 2) == 1
    assert idx.is_locked(conn, 2) is False


def test_empty_calendar_falls_back_to_scraped_rounds(conn, monkeypatch):
    conn.execute('DELETE FROM rounds')
    conn.commit()
    _at(monkeypatch, _utc(2026, 9, 26, 12))
    assert idx.get_next_round(conn, 2) == 1       # no stats either → round 1
    assert idx.is_locked(conn, 2) is False        # nothing to lock against


def test_exhausted_calendar_locks_the_league_shut(conn, monkeypatch):
    """A calendar entirely in the past wedges the league locked forever.

    This is how OFDS ended up frozen on production: the rounds table held the
    mock seed's 2026 Feb-Jun dates, so no round had a future rollover, the
    MAX(weekly_stats.round) + 1 fallback returned round 1, and round 1's kickoff
    was long past. Documented here so the failure mode is recognisable - the fix
    is a correct calendar (DATA_SOURCE=live), not a change to this logic.
    """
    _at(monkeypatch, _utc(2027, 8, 1, 12))        # long after every round
    assert idx.get_next_round(conn, 2) == 1       # fallback, not a real "next"
    assert idx.is_locked(conn, 2) is True


def test_unrestricted_edits_bypasses_the_lock(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 26, 12))       # mid-round, would be locked
    assert idx.is_locked(conn, 2) is True
    monkeypatch.setattr(idx, 'ALLOW_UNRESTRICTED_EDITS', True)
    assert idx.is_locked(conn, 2) is False


# ---------------------------------------------------------------------------
# Finalize fires AT the rollover, so it settles the round that just rolled
# ---------------------------------------------------------------------------

def test_round_to_finalize_is_the_one_that_just_rolled(conn, monkeypatch):
    # Before round 1 has rolled, there is nothing to settle.
    _at(monkeypatch, _utc(2026, 9, 27, 20))       # Sunday evening
    assert idx._round_to_finalize(conn, 2) is None

    # At the rollover, round 1 is done and round 2 is active.
    _at(monkeypatch, _utc(2026, 9, 29, 11, 1))    # Tue 12:01 BST
    assert idx._round_to_finalize(conn, 2) == 1
    assert idx.get_next_round(conn, 2) == 2

    # Next week it moves on with the calendar.
    _at(monkeypatch, _utc(2026, 10, 6, 11, 1))    # Tue 12:01 BST
    assert idx._round_to_finalize(conn, 2) == 2


def test_finalize_settles_the_finished_round_not_the_active_one(conn, monkeypatch):
    """The trap: at Tuesday noon the round has already advanced, so finalising
    `active_round` would write the definitive scrape against a gameweek that
    hasn't been played."""
    called = {}

    def _fake(conn_, league_id, competition, round_number, finalize=False):
        called.update(round_number=round_number, finalize=finalize)
        return 42

    monkeypatch.setattr(idx.ingest, 'ingest_player_scores', _fake)
    _at(monkeypatch, _utc(2026, 9, 29, 11, 1))    # Tue 12:01 BST - at the rollover
    active = idx.get_next_round(conn, 2)
    assert active == 2, 'the round has rolled by the time finalize runs'

    rnd, detail = idx._run_job(conn, 2, 'premiership', 'finalize', active)
    assert called == {'round_number': 1, 'finalize': True}
    assert rnd == 1 and '42' in detail


def test_finalize_never_precedes_a_monday_evening_fixture(conn, monkeypatch):
    """Round 8 of 2026-27 ends Mon 28 Dec 17:00. A Monday-noon finalize ran five
    hours early and marked itself done; the rollover cannot."""
    conn.execute('INSERT INTO rounds VALUES (8, ?, ?, 2)',
                 ('2026-12-26T15:00:00+00:00', '2026-12-28T17:00:00+00:00'))
    conn.commit()

    # Monday noon - the old finalize moment, still mid-round.
    _at(monkeypatch, _utc(2026, 12, 28, 12))
    assert idx._round_to_finalize(conn, 2) != 8, 'round 8 is not finished yet'

    # The rollover is after the last fixture, so round 8 settles correctly.
    _at(monkeypatch, _utc(2026, 12, 29, 12, 1))   # Tue 12:01 GMT
    assert idx._round_to_finalize(conn, 2) == 8


def test_finalize_is_a_noop_before_any_round_completes(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))        # pre-season
    rnd, detail = idx._run_job(conn, 2, 'premiership', 'finalize',
                               idx.get_next_round(conn, 2))
    assert 'no completed round' in detail
