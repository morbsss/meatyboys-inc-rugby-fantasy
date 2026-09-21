"""The fixtures week-card header: round date, and the LIVE indicator.

The header used to show a Played/Upcoming badge, which duplicated what the rows
already said and never told you *when* a round was. It now shows the round's
date, or a live marker while the round is being played.

The date is formatted server-side on purpose. Round 1 kicks off 18:45 UTC on a
Friday, which is already Saturday in Sydney — formatting in the browser would
show a manager abroad the wrong day for an English fixture.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from api import index as idx


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    _now = _utc(2026, 9, 1, 12)

    @classmethod
    def now(cls, tz=None):
        return cls._now.astimezone(tz) if tz else cls._now.replace(tzinfo=None)


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE leagues (league_id INTEGER PRIMARY KEY, slug TEXT,
                              name TEXT, competition TEXT, theme TEXT);
        CREATE TABLE rounds (round_number INTEGER, first_kickoff TEXT,
                             last_kickoff TEXT, league_id INTEGER);
    ''')
    c.execute("INSERT INTO leagues VALUES (2, 'ofds', 'OFDS', 'premiership', 'union')")
    c.executemany(
        'INSERT INTO rounds (round_number, first_kickoff, last_kickoff, league_id) '
        'VALUES (?, ?, ?, 2)',
        [
            (1, '2026-09-25T18:45:00+00:00', '2026-09-27T14:00:00+00:00'),
            (5, '2026-10-30T19:45:00+00:00', '2026-10-31T17:30:00+00:00'),
            (8, '2026-12-26T15:00:00+00:00', '2026-12-28T17:00:00+00:00'),
        ])
    c.commit()
    yield c
    c.close()


def _at(monkeypatch, moment):
    monkeypatch.setattr(_FrozenDatetime, '_now', moment)
    monkeypatch.setattr(idx, 'datetime', _FrozenDatetime)


# ---------------------------------------------------------------------------
# The date label
# ---------------------------------------------------------------------------

def test_label_uses_the_leagues_local_date(conn, monkeypatch):
    """18:45 UTC Friday is Friday in London — not Saturday, as a browser east of
    UTC would render it."""
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    assert idx._round_timing(conn, 2, 1)['date_label'] == '25 Sept 2026'


def test_label_september_takes_four_letters(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    assert 'Sept' in idx._round_timing(conn, 2, 1)['date_label']


def test_label_other_months_take_three(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    assert idx._round_timing(conn, 2, 5)['date_label'] == '30 Oct 2026'
    assert idx._round_timing(conn, 2, 8)['date_label'] == '26 Dec 2026'


def test_label_has_no_leading_zero(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    conn.execute('INSERT INTO rounds VALUES (9, ?, ?, 2)',
                 ('2027-01-01T19:45:00+00:00', '2027-01-03T15:00:00+00:00'))
    conn.commit()
    assert idx._round_timing(conn, 2, 9)['date_label'] == '1 Jan 2027'


def test_unknown_round_has_no_label(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 1, 12))
    t = idx._round_timing(conn, 2, 99)
    assert t == {'first_kickoff': None, 'last_kickoff': None,
                 'date_label': None, 'is_live': False}


# ---------------------------------------------------------------------------
# The LIVE flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('moment, live, why', [
    (_utc(2026, 9, 25, 18, 44), False, 'a minute before the first kickoff'),
    (_utc(2026, 9, 25, 18, 46), True,  'first match under way'),
    (_utc(2026, 9, 26, 12),     True,  'Saturday, between matches — the round is on'),
    (_utc(2026, 9, 27, 14, 30), True,  'last match in play'),
    (_utc(2026, 9, 27, 15, 59), True,  'inside the 2h window after the last kickoff'),
    (_utc(2026, 9, 27, 16, 30), False, 'last match over'),
    (_utc(2026, 9, 28, 12),     False, 'Monday — played, not live'),
])
def test_live_spans_the_whole_round(conn, monkeypatch, moment, live, why):
    _at(monkeypatch, moment)
    assert idx._round_timing(conn, 2, 1)['is_live'] is live, why


def test_only_the_round_in_play_is_live(conn, monkeypatch):
    _at(monkeypatch, _utc(2026, 9, 26, 12))     # round 1 weekend
    assert idx._round_timing(conn, 2, 1)['is_live'] is True
    assert idx._round_timing(conn, 2, 5)['is_live'] is False
    assert idx._round_timing(conn, 2, 8)['is_live'] is False


def test_live_survives_a_monday_evening_finish(conn, monkeypatch):
    """Round 8 runs Boxing Day to Monday evening; it stays live until then."""
    _at(monkeypatch, _utc(2026, 12, 28, 16))
    assert idx._round_timing(conn, 2, 8)['is_live'] is True
    _at(monkeypatch, _utc(2026, 12, 28, 19, 30))
    assert idx._round_timing(conn, 2, 8)['is_live'] is False
