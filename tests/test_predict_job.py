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

from datetime import datetime, timedelta, timezone

import pytest

from api import index as idx
from api import scheduler as s

LON = 'Europe/London'


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _due(**kw):
    base = dict(
        competition='premiership',
        now_utc=_utc(2026, 9, 29, 11),          # Tue 12:00 BST - the rollover
        tz_name=LON,
        last_runs={'sync_rounds': datetime.now(timezone.utc).isoformat(),
                   'lineups': None, 'live_scoring': None, 'predict': None},
        live_now=False,
        finalize_done=True,
        rounds_known=True,
    )
    base.update(kw)
    return s.due_jobs(**base)


# ---------------------------------------------------------------------------
# When predict runs
# ---------------------------------------------------------------------------

def test_predict_runs_mid_week_for_the_round_being_picked():
    """The behaviour this job was changed for.

    It used to fire ONLY at the Tuesday rollover, gated to once per round - which
    meant the round being played never got projections at all, because the run at
    round N's rollover targets N+1. A manager picking for round 1 saw an empty
    Analysis page all week.
    """
    for moment, label in [(_utc(2026, 9, 24, 15), 'Thursday'),
                          (_utc(2026, 9, 25, 9), 'Friday morning'),
                          (_utc(2026, 9, 26, 12), 'Saturday'),
                          (_utc(2026, 9, 28, 11), 'Monday')]:
        assert 'predict' in _due(now_utc=moment), label


def test_predict_is_still_due_at_the_rollover():
    assert 'predict' in _due()


def test_predict_runs_after_finalize_in_the_same_tick():
    """At the rollover the model must train on settled scores, not the weekend's
    provisional ones."""
    due = _due(finalize_done=False)

    assert 'finalize' in due and 'predict' in due
    assert due.index('finalize') < due.index('predict')


def test_predict_runs_after_live_scoring_in_the_same_tick():
    """While a match is live both come due together, and predict must see the
    scores written by this same tick rather than the previous one."""
    due = _due(now_utc=_utc(2026, 9, 25, 19), live_now=True,
               last_runs={'sync_rounds': datetime.now(timezone.utc).isoformat(),
                          'lineups': None, 'live_scoring': None, 'predict': None})

    assert 'live_scoring' in due and 'predict' in due
    assert due.index('live_scoring') < due.index('predict')


def test_predict_uses_the_live_cadence_during_a_match():
    """5 minutes, matching live_scoring, so win probabilities move with the score.
    Affordable because a run measures ~7-9s wall on the VM."""
    now = _utc(2026, 9, 25, 19)
    recent = (now - timedelta(minutes=3)).isoformat()
    older = (now - timedelta(minutes=7)).isoformat()

    assert 'predict' not in _due(now_utc=now, live_now=True,
                                 last_runs={'predict': recent})
    assert 'predict' in _due(now_utc=now, live_now=True,
                             last_runs={'predict': older})


def test_predict_uses_a_slow_cadence_when_nothing_is_live():
    """A 2h floor off match day: inputs only change when team sheets (2h) or the
    roster (daily) do, so there is nothing to gain from running hot."""
    now = _utc(2026, 9, 24, 15)
    assert 'predict' not in _due(now_utc=now,
                                 last_runs={'predict': (now - timedelta(minutes=30)).isoformat()})
    assert 'predict' in _due(now_utc=now,
                             last_runs={'predict': (now - timedelta(hours=3)).isoformat()})


def test_predict_does_not_run_without_a_calendar():
    """No rounds means no target round and no fixtures to project against."""
    assert 'predict' not in _due(rounds_known=False)


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
