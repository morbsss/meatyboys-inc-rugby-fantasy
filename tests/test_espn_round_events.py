"""ESPN scoreboard access: the two defects that silently broke round-1 lineups.

Both were invisible in production because `_espn_round_events` swallowed every
exception and returned `[]`, so the lineups job recorded a cheerful "0 entries"
each run while auto-substitution quietly did nothing.

1. **The `dates=start-end` range form is rejected outright.** ESPN answers HTTP
   400 to every range, padded or exact, wide or narrow, with or without `limit`.
   Only the single-date form works, so the round window must be walked one day
   at a time.

2. **A spoofed browser User-Agent is 403'd.** Measured against both the
   scoreboard and summary endpoints, from this machine and from the production
   VM: no UA and `curl/8.5.0` get 200; `Mozilla/5.0 ... Chrome/124` and a custom
   `meatyboys-fantasy/1.0` get 403. That is covered here by asserting
   `fetch_json` sends no User-Agent - the header is the bug, so the test guards
   its absence.

These tests stub the network. They pin the shape of the requests we make, which
is what regressed; they can't detect ESPN changing its mind again.
"""

from datetime import date, timedelta
from urllib.request import Request

import pytest

from api import real_lineups, sync_rounds
from api.datasource.live import LiveAdapter

CFG = {'competition': 'premiership', 'espn_league_id': '267979'}
ROUND = 1
# The real round-1 window from data/prem_fixtures_2026_27.json: Fri 25 → Sun 27
# Sep 2026. _espn_round_events pads a day either side, so it walks 24th → 28th.
WINDOW = ('2026-09-25T18:45:00+00:00', '2026-09-27T14:00:00+00:00')
EXPECTED_DAYS = ['20260924', '20260925', '20260926', '20260927', '20260928']


@pytest.fixture
def espn(monkeypatch):
    """Stub the scoreboard. Records requested URLs; returns events per day."""
    calls = []
    events_by_day = {
        '20260925': [{'id': '604614'}, {'id': '604616'}],
        '20260926': [{'id': '604615'}, {'id': '604617'}],
        '20260927': [{'id': '604618'}],
    }

    def fake_fetch(url):
        calls.append(url)
        day = url.rsplit('dates=', 1)[1].split('&')[0]
        if '-' in day:
            raise RuntimeError(f'HTTP Error 400: Bad Request ({url})')
        return {'events': events_by_day.get(day, [])}

    monkeypatch.setattr(real_lineups, 'fetch_json', fake_fetch)
    monkeypatch.setattr('api.prem_fixtures.round_window', lambda n: WINDOW)
    return calls


def test_walks_the_window_one_day_at_a_time(espn):
    """No request may carry a date range - ESPN 400s all of them."""
    LiveAdapter._espn_round_events(CFG, ROUND)

    assert len(espn) == len(EXPECTED_DAYS)
    for url in espn:
        dates = url.rsplit('dates=', 1)[1].split('&')[0]
        assert '-' not in dates, f'range form is rejected by ESPN: {url}'


def test_covers_the_round_window_padded_by_a_day(espn):
    LiveAdapter._espn_round_events(CFG, ROUND)

    requested = [u.rsplit('dates=', 1)[1].split('&')[0] for u in espn]
    assert requested == EXPECTED_DAYS


def test_returns_every_fixture_in_the_round(espn):
    events = LiveAdapter._espn_round_events(CFG, ROUND)

    assert sorted(e['id'] for e in events) == [
        '604614', '604615', '604616', '604617', '604618']


def test_deduplicates_events_seen_on_more_than_one_day(monkeypatch):
    """The window is padded, and ESPN lists a fixture under neighbouring days in
    some timezones - the same event must not be fetched or scored twice."""
    monkeypatch.setattr('api.prem_fixtures.round_window', lambda n: WINDOW)
    monkeypatch.setattr(real_lineups, 'fetch_json',
                        lambda url: {'events': [{'id': '604614'}, {'id': '604616'}]})

    events = LiveAdapter._espn_round_events(CFG, ROUND)

    assert [e['id'] for e in events] == ['604614', '604616']


def test_partial_failure_keeps_the_days_that_answered(monkeypatch):
    """One bad day must not cost the whole round - four fixtures still beat none."""
    monkeypatch.setattr('api.prem_fixtures.round_window', lambda n: WINDOW)

    def flaky(url):
        if '20260926' in url:
            raise RuntimeError('HTTP Error 500')
        return {'events': [{'id': 'ok-' + url[-8:]}]}

    monkeypatch.setattr(real_lineups, 'fetch_json', flaky)

    events = LiveAdapter._espn_round_events(CFG, ROUND)

    assert len(events) == len(EXPECTED_DAYS) - 1


def test_total_failure_raises_rather_than_reporting_an_empty_round(monkeypatch):
    """The original bug: every call failing looked identical to a round with no
    fixtures, so the job logged success. An outage must surface in job_runs."""
    monkeypatch.setattr('api.prem_fixtures.round_window', lambda n: WINDOW)

    def always_fails(url):
        raise RuntimeError('HTTP Error 403: Forbidden')

    monkeypatch.setattr(real_lineups, 'fetch_json', always_fails)

    with pytest.raises(RuntimeError, match='unreachable'):
        LiveAdapter._espn_round_events(CFG, ROUND)


def test_unknown_round_is_not_an_outage(monkeypatch):
    """No window (play-off slot still TBC) is a legitimately empty result."""
    monkeypatch.setattr('api.prem_fixtures.round_window', lambda n: None)

    assert LiveAdapter._espn_round_events(CFG, 99) == []


@pytest.mark.parametrize('fetch_json', [real_lineups.fetch_json, sync_rounds.fetch_json],
                         ids=['real_lineups', 'sync_rounds'])
def test_no_browser_user_agent_is_sent(fetch_json, monkeypatch):
    """ESPN 403s a spoofed Chrome UA. Both copies of fetch_json must stay bare."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['headers'] = dict(req.headers)
        raise _Stop()

    class _Stop(Exception):
        pass

    module = 'api.real_lineups' if fetch_json is real_lineups.fetch_json else 'api.sync_rounds'
    monkeypatch.setattr(f'{module}.urlopen', fake_urlopen)

    with pytest.raises(_Stop):
        fetch_json('https://site.api.espn.com/apis/site/v2/sports/rugby/267979/scoreboard')

    # Request.headers capitalises keys as 'User-agent'.
    sent = {k.lower(): v for k, v in captured['headers'].items()}
    assert 'user-agent' not in sent, f'ESPN 403s a User-Agent header: {sent}'
    assert 'accept' in sent
