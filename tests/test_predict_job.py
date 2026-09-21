"""Scheduling and cold-start behaviour for the analysis prediction job.

The Analysis page had never produced a row in production: predict.py was never
scheduled, and its dependencies weren't installed on the VM. These tests cover
the scheduling half (the dependency half is deploy.sh + requirements-analysis.txt)
and the cold-start prior, which is what makes the page useful in the opening
weeks of a season rather than showing 0.0 for everyone.

They deliberately avoid importing api.predict: that pulls numpy/pandas/scipy/
sklearn, which are intentionally NOT web dependencies. The prediction maths is
validated separately against mock_fantasy.db.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from api import index as idx
from api import scheduler as s

LON = 'Europe/London'


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _due(**kw):
    base = dict(
        competition='premiership',
        now_utc=_utc(2026, 9, 29, 11),          # Tue 12:00 BST — the rollover
        tz_name=LON,
        last_runs={'sync_rounds': datetime.now(timezone.utc).isoformat(),
                   'lineups': None, 'live_scoring': None},
        live_now=False,
        finalize_done=True,
        rounds_known=True,
        predict_done=False,
    )
    base.update(kw)
    return s.due_jobs(**base)


# ---------------------------------------------------------------------------
# When predict runs
# ---------------------------------------------------------------------------

def test_predict_is_due_at_the_rollover():
    assert 'predict' in _due()


def test_predict_runs_after_finalize_in_the_same_tick():
    """Order matters: the model should train on settled scores, not the
    weekend's provisional ones."""
    due = _due(finalize_done=False)
    assert 'finalize' in due and 'predict' in due
    assert due.index('finalize') < due.index('predict')


def test_predict_is_once_per_round():
    assert 'predict' not in _due(predict_done=True)


def test_predict_does_not_run_mid_week():
    for moment, label in [(_utc(2026, 9, 25, 18), 'Friday'),
                          (_utc(2026, 9, 26, 12), 'Saturday'),
                          (_utc(2026, 9, 28, 11), 'Monday')]:
        assert 'predict' not in _due(now_utc=moment), label


def test_predict_defaults_to_not_running():
    """A caller that doesn't pass predict_done must not trigger the job."""
    due = s.due_jobs('premiership', _utc(2026, 9, 29, 11), LON,
                     {'sync_rounds': datetime.now(timezone.utc).isoformat(),
                      'lineups': None, 'live_scoring': None},
                     live_now=False, finalize_done=True, rounds_known=True)
    assert 'predict' not in due


# ---------------------------------------------------------------------------
# The once-per-round gate
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE job_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, '
              'league_id INTEGER, job TEXT, round_number INTEGER, status TEXT, '
              'detail TEXT, run_at TEXT)')
    c.commit()
    yield c
    c.close()


def test_gate_is_open_until_a_run_is_logged(conn):
    assert idx._predict_done(conn, 2, 3) is False
    conn.execute("INSERT INTO job_runs (league_id, job, round_number, status) "
                 "VALUES (2, 'predict', 3, 'ok')")
    conn.commit()
    assert idx._predict_done(conn, 2, 3) is True


def test_gate_is_per_round_and_per_league(conn):
    conn.execute("INSERT INTO job_runs (league_id, job, round_number, status) "
                 "VALUES (2, 'predict', 3, 'ok')")
    conn.commit()
    assert idx._predict_done(conn, 2, 4) is False, 'next round still needs a run'
    assert idx._predict_done(conn, 1, 3) is False, 'other league still needs a run'


def test_a_failed_run_does_not_close_the_gate(conn):
    conn.execute("INSERT INTO job_runs (league_id, job, round_number, status) "
                 "VALUES (2, 'predict', 3, 'error')")
    conn.commit()
    assert idx._predict_done(conn, 2, 3) is False


def test_gate_closed_when_there_is_no_round(conn):
    assert idx._predict_done(conn, 2, None) is True


# ---------------------------------------------------------------------------
# Launching out-of-process
# ---------------------------------------------------------------------------

def test_launch_is_detached_and_non_blocking(monkeypatch):
    """A fit takes over a minute; the single gunicorn worker must not wait."""
    seen = {}

    class _FakePopen:
        def __init__(self, cmd, **kw):
            seen['cmd'] = cmd
            seen['kw'] = kw

    monkeypatch.setattr('subprocess.Popen', _FakePopen)
    detail = idx._launch_predict(2, 5)

    assert 'launched' in detail
    assert seen['cmd'][1:] == ['-m', 'api.predict', '--league', '2', '--round', '5']
    assert seen['kw']['start_new_session'] is True, 'must survive the request'
    assert seen['kw']['stdout'] is not None and seen['kw']['stderr'] is not None


def test_launch_failure_is_reported_not_raised(monkeypatch):
    def _boom(*a, **kw):
        raise OSError('no such executable')
    monkeypatch.setattr('subprocess.Popen', _boom)
    detail = idx._launch_predict(2, 5)
    assert 'launch failed' in detail        # logged to job_runs, tick survives
