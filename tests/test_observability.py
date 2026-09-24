"""Pipeline health checks for the commissioner's status page.

These exist because every outage this pipeline has had reported success, so the
page cannot be built on `status='ok'`:

  * lineups wrote "ok / 0 entries" for weeks behind an ESPN 403 and a 400 on date
    ranges - both swallowed into an empty list
  * predict wrote "ok / launched for round 1" while the model never produced a row
  * sync_players did not exist, and a job that never runs cannot appear in a log
    of runs

So the tests below are mostly about the cases where the log looks fine and the
pipeline is not, plus the inverse: an idle job must NOT be reported as broken, or
the page turns permanently amber and stops being read.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from api import observability as obs

LEAGUE = 2
SLUG = 'ofds'
ROUND = 1
NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)   # Thursday, window open


def _iso(dt):
    return dt.isoformat()


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER, job TEXT, round_number INTEGER,
            status TEXT, detail TEXT, run_at TEXT);
        CREATE TABLE pipeline_heartbeat (id INTEGER PRIMARY KEY,
            last_tick TEXT, due_count INTEGER DEFAULT 0, detail TEXT);
        CREATE TABLE rounds (round_number INTEGER, first_kickoff TEXT,
            last_kickoff TEXT, league_id INTEGER);
        CREATE TABLE real_fixtures (id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER, round INTEGER, home_team TEXT, away_team TEXT);
        CREATE TABLE players (player_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, team TEXT, position TEXT, league_id INTEGER);
        CREATE TABLE match_lineups (id INTEGER PRIMARY KEY AUTOINCREMENT,
            round INTEGER, player_name TEXT, real_team TEXT, jersey INTEGER,
            is_bench INTEGER, scraped_at TEXT, league_id INTEGER);
        CREATE TABLE weekly_stats (id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER, round INTEGER, total_points REAL, price REAL,
            scraped_at TEXT, league_id INTEGER);
        CREATE TABLE player_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER, round INTEGER, player_id INTEGER);
        CREATE TABLE matchup_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER, round INTEGER, home_team TEXT, away_team TEXT);
    ''')
    # A minimally healthy league: calendar, fixtures and players exist.
    c.executemany('INSERT INTO rounds VALUES (?,?,?,?)',
                  [(1, '2026-09-25T18:45:00+00:00', '2026-09-27T14:00:00+00:00', LEAGUE)])
    c.executemany('INSERT INTO real_fixtures (league_id, round, home_team, away_team)'
                  ' VALUES (?,?,?,?)', [(LEAGUE, 1, 'HAR', 'BAT')])
    c.executemany('INSERT INTO players (name, team, position, league_id)'
                  ' VALUES (?,?,?,?)', [('Obano,B', 'BAT', 'PR', LEAGUE)])
    c.commit()
    return c


def _run(conn, job, status='ok', detail='fine', minutes_ago=5, round_number=ROUND):
    conn.execute('INSERT INTO job_runs (league_id, job, round_number, status, detail,'
                 ' run_at) VALUES (?,?,?,?,?,?)',
                 (LEAGUE, job, round_number, status, detail,
                  _iso(NOW - timedelta(minutes=minutes_ago))))
    conn.commit()


def _beat(conn, minutes_ago=2):
    conn.execute('INSERT INTO pipeline_heartbeat (id, last_tick, due_count)'
                 ' VALUES (1,?,0)', (_iso(NOW - timedelta(minutes=minutes_ago)),))
    conn.commit()


def _health(conn, live_now=False):
    return obs.league_health(conn, LEAGUE, SLUG, ROUND, live_now, now=NOW)


def _codes(health):
    return [w['code'] for w in health['warnings']]


# --- liveness ---------------------------------------------------------------

def test_a_recent_heartbeat_is_healthy(conn):
    _beat(conn, minutes_ago=3)

    assert obs._tick_check(conn, NOW) == []


def test_a_stale_heartbeat_is_an_error(conn):
    _beat(conn, minutes_ago=120)

    assert [w['code'] for w in obs._tick_check(conn, NOW)] == ['cron_silent']


def test_no_heartbeat_and_no_runs_reports_never_ran(conn):
    assert [w['code'] for w in obs._tick_check(conn, NOW)] == ['cron_never_ran']


def test_a_quiet_spell_without_a_heartbeat_is_not_called_an_outage(conn):
    """job_runs only gets a row when a job FIRES, so hours of silence overnight is
    normal. Before the heartbeat table existed this was reported as a dead cron -
    it must degrade to 'unknown', not to an error."""
    _run(conn, 'sync_rounds', minutes_ago=300)

    warnings = obs._tick_check(conn, NOW)

    assert [w['code'] for w in warnings] == ['tick_unknown']
    assert warnings[0]['level'] == obs.INFO


def test_heartbeat_is_preferred_over_the_run_log(conn):
    _run(conn, 'sync_rounds', minutes_ago=600)
    _beat(conn, minutes_ago=4)

    at, exact = obs.last_tick(conn)

    assert exact is True
    assert obs._age(at, NOW) < 600


# --- success that produced nothing -----------------------------------------

def test_lineups_ok_with_no_rows_is_an_error(conn):
    """The exact shape of the ESPN outage: 'ok / 0 entries', week after week."""
    _beat(conn)
    _run(conn, 'lineups', detail='0 entries')

    health = _health(conn)

    assert 'ok_but_empty' in _codes(health)
    assert health['worst'] == obs.ERROR


def test_lineups_ok_with_rows_is_clean(conn):
    _beat(conn)
    _run(conn, 'lineups', detail='92 entries')
    conn.execute('INSERT INTO match_lineups (round, player_name, real_team, jersey,'
                 ' is_bench, scraped_at, league_id) VALUES (?,?,?,?,?,?,?)',
                 (ROUND, 'Obano,B', 'BAT', 1, 0, _iso(NOW), LEAGUE))
    conn.commit()

    assert 'ok_but_empty' not in _codes(_health(conn))


def test_predict_launched_with_no_rows_is_flagged(conn):
    """The job records the launch, never the result, so 'ok' says nothing."""
    _beat(conn)
    _run(conn, 'predict', detail='launched for round 1', minutes_ago=600)

    assert 'launched_no_output' in _codes(_health(conn))


def test_predict_with_rows_is_clean(conn):
    _beat(conn)
    _run(conn, 'predict', detail='launched for round 1', minutes_ago=600)
    conn.execute('INSERT INTO player_predictions (league_id, round, player_id)'
                 ' VALUES (?,?,?)', (LEAGUE, ROUND, 1))
    conn.commit()

    assert 'launched_no_output' not in _codes(_health(conn))


# --- failures and staleness -------------------------------------------------

def test_a_failed_run_is_an_error(conn):
    _beat(conn)
    _run(conn, 'sync_rounds', status='error', detail='HTTP 500 from provider')

    health = _health(conn)

    assert 'job_failing' in _codes(health)
    assert health['worst'] == obs.ERROR
    job = next(j for j in health['jobs'] if j['job'] == 'sync_rounds')
    assert job['last_failure']['detail'] == 'HTTP 500 from provider'


def test_an_interval_job_long_past_its_cadence_is_overdue(conn):
    _beat(conn)
    _run(conn, 'sync_players', minutes_ago=60 * 60)      # 60h, cadence 24h

    assert 'job_overdue' in _codes(_health(conn))


def test_a_job_inside_its_cadence_is_not_overdue(conn):
    _beat(conn)
    _run(conn, 'sync_players', minutes_ago=60 * 6)       # 6h, cadence 24h

    assert 'job_overdue' not in _codes(_health(conn))


def test_a_never_run_interval_job_is_warned_about(conn):
    """sync_players did not exist, so nothing reported it - the case a log of runs
    structurally cannot show."""
    _beat(conn)

    codes = [w for w in _health(conn)['warnings'] if w['code'] == 'job_never_ran']
    jobs = {w['title'].split()[0] for w in codes}

    assert 'sync_players' in jobs


def test_a_never_run_rollover_job_is_only_informational(conn):
    """finalize and predict run weekly; before the first rollover, silence is
    correct and must not read as a fault."""
    _beat(conn)

    levels = {w['title'].split()[0]: w['level'] for w in _health(conn)['warnings']
              if w['code'] == 'job_never_ran'}

    assert levels.get('finalize') == obs.INFO
    assert levels.get('predict') == obs.INFO


def test_live_scoring_idle_outside_a_match_is_not_an_error(conn):
    """Nothing is live, so not scraping is correct."""
    _beat(conn)
    _run(conn, 'live_scoring', minutes_ago=5000)

    assert 'job_overdue' not in _codes(_health(conn, live_now=False))


def test_live_scoring_stale_during_a_match_is_an_error(conn):
    _beat(conn)
    _run(conn, 'live_scoring', minutes_ago=90)           # cadence is 3 min

    health = _health(conn, live_now=True)

    assert 'job_overdue' in _codes(health)
    assert health['worst'] == obs.ERROR


# --- output freshness -------------------------------------------------------

def test_outputs_report_row_counts_per_table(conn):
    conn.execute('INSERT INTO match_lineups (round, player_name, real_team, jersey,'
                 ' is_bench, scraped_at, league_id) VALUES (?,?,?,?,?,?,?)',
                 (ROUND, 'Obano,B', 'BAT', 1, 0, _iso(NOW), LEAGUE))
    conn.commit()

    by_table = {o['table']: o for o in obs.output_freshness(conn, LEAGUE, ROUND)}

    assert by_table['players']['count'] == 1
    assert by_table['rounds']['count'] == 1
    assert by_table['real_fixtures']['count'] == 1
    assert by_table['match_lineups']['count'] == 1
    assert by_table['match_lineups']['extra']['clubs named'] == 1
    assert by_table['weekly_stats']['count'] == 0


def test_an_empty_player_list_is_an_error(conn):
    _beat(conn)
    conn.execute('DELETE FROM players')
    conn.commit()

    assert 'no_players' in _codes(_health(conn))


def test_an_empty_round_calendar_is_an_error(conn):
    _beat(conn)
    conn.execute('DELETE FROM rounds')
    conn.commit()

    assert 'no_rounds' in _codes(_health(conn))


# --- shape ------------------------------------------------------------------

def test_every_known_job_appears_even_with_an_empty_log(conn):
    jobs = {j['job'] for j in obs.job_summaries(conn, LEAGUE, NOW)}

    assert jobs == set(obs.JOB_SPECS)


def test_warnings_are_ordered_worst_first(conn):
    _beat(conn)
    _run(conn, 'sync_rounds', status='error', detail='boom')

    levels = [w['level'] for w in _health(conn)['warnings']]

    assert levels == sorted(levels, key=lambda l: {'error': 0, 'warn': 1, 'info': 2}[l])


def test_health_includes_the_context_a_reader_needs(conn):
    _beat(conn)
    health = _health(conn)

    for key in ('league_id', 'slug', 'timezone', 'local_time', 'active_round',
                'live_now', 'in_lineup_window', 'jobs', 'outputs', 'warnings',
                'worst'):
        assert key in health
