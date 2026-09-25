"""Timezone/DST-aware scheduler decisions (spec §3, §4.5)."""

from datetime import datetime, timezone, timedelta

from api import scheduler as s

LON = 'Europe/London'
SYD = 'Australia/Sydney'


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _local(dt, tz):
    return s.to_local(dt, tz)


# ---------------------------------------------------------------------------
# Lineup windows
# ---------------------------------------------------------------------------

def test_premiership_lineup_window_saturday():
    assert s.in_lineup_window(_local(_utc(2026, 7, 11, 14), LON), 'premiership')   # Sat


def test_super_rugby_wednesday_in_thursday_out():
    # Wed 15:00 AEDT == Wed 04:00 UTC (Feb, +11)
    assert s.in_lineup_window(_local(_utc(2026, 2, 18, 4), SYD), 'super_rugby')
    # Thursday is not part of the Super Rugby window
    assert not s.in_lineup_window(_local(_utc(2026, 2, 19, 4), SYD), 'super_rugby')


def test_dst_shifts_the_sunday_cutoff():
    # 17:30 UTC: in BST (summer) that's 18:30 → past the Sun 18:00 cutoff (out);
    # in GMT (winter) that's 17:30 → still inside the window (in).
    summer = _local(_utc(2026, 7, 12, 17, 30), LON)   # Sunday, BST
    winter = _local(_utc(2026, 1, 11, 17, 30), LON)   # Sunday, GMT
    assert s.in_lineup_window(summer, 'premiership') is False
    assert s.in_lineup_window(winter, 'premiership') is True


# ---------------------------------------------------------------------------
# Finalize (Monday 12:00 local) - DST-aware
# ---------------------------------------------------------------------------

def test_finalize_only_tuesday_noon_local():
    """Finalize is pinned to the Tuesday-noon rollover, not a fixed Monday."""
    assert s.is_finalize_time(_local(_utc(2026, 7, 14, 11), LON))      # Tue 12:00 BST
    assert not s.is_finalize_time(_local(_utc(2026, 7, 14, 10), LON))  # Tue 11:00 BST
    assert not s.is_finalize_time(_local(_utc(2026, 7, 13, 11), LON))  # Monday - was the old rule
    assert not s.is_finalize_time(_local(_utc(2026, 7, 12, 11), LON))  # Sunday


def test_finalize_tuesday_noon_is_local_across_dst():
    # Tue 12:00 GMT (winter) == 12:00 UTC; Tue 12:00 BST (summer) == 11:00 UTC.
    assert s.is_finalize_time(_local(_utc(2027, 1, 26, 12), LON))      # Tue, GMT
    assert not s.is_finalize_time(_local(_utc(2027, 1, 26, 11), LON))  # Tue 11:00 GMT


# ---------------------------------------------------------------------------
# Live-match detection
# ---------------------------------------------------------------------------

def test_match_is_live_within_window():
    ko = _utc(2026, 3, 7, 15).isoformat()
    assert s.match_is_live(_utc(2026, 3, 7, 16), [ko])              # 1h in
    assert not s.match_is_live(_utc(2026, 3, 7, 18), [ko])          # >2h after
    assert not s.match_is_live(_utc(2026, 3, 7, 14, 30), [ko])      # before kickoff


# ---------------------------------------------------------------------------
# due_jobs: windows + cadence + live + finalize together
# ---------------------------------------------------------------------------

def _due(comp, now, tz, last=None, live=False, fin=False, known=True):
    last_runs = {'sync_rounds': '2099-01-01T00:00:00+00:00', 'lineups': last, 'live_scoring': last}
    return s.due_jobs(comp, now, tz, last_runs, live, fin, known)


def test_due_jobs_lineups_in_window():
    assert 'lineups' in _due('premiership', _utc(2026, 7, 11, 14), LON)


def test_due_jobs_cadence_suppresses_recent_lineups():
    recent = _utc(2026, 7, 11, 13, 30).isoformat()   # 30 min ago (< 2h interval)
    assert 'lineups' not in _due('premiership', _utc(2026, 7, 11, 14), LON, last=recent)


def test_due_jobs_live_scoring_only_when_live():
    assert 'live_scoring' not in _due('premiership', _utc(2026, 7, 11, 14), LON, live=False)
    assert 'live_scoring' in _due('premiership', _utc(2026, 7, 11, 14), LON, live=True)


def test_due_jobs_bootstraps_sync_rounds_when_unknown():
    # Even with a recent sync timestamp, an unknown calendar forces sync_rounds.
    last_runs = {'sync_rounds': datetime.now(timezone.utc).isoformat(),
                 'lineups': None, 'live_scoring': None}
    due = s.due_jobs('premiership', _utc(2026, 7, 7, 9), LON, last_runs,
                     live_now=False, finalize_done=False, rounds_known=False)
    assert 'sync_rounds' in due


def test_due_jobs_finalize_once_per_gameweek():
    now = _utc(2026, 7, 14, 11)   # Tue 12:00 BST - the rollover
    assert 'finalize' in _due('premiership', now, LON, fin=False)
    assert 'finalize' not in _due('premiership', now, LON, fin=True)


def test_due_jobs_no_finalize_on_monday():
    """Monday used to trigger it, which could precede a Monday-evening fixture."""
    now = _utc(2026, 7, 13, 11)   # Mon 12:00 BST
    assert 'finalize' not in _due('premiership', now, LON, fin=False)


# --- sync_players: the daily roster reconciliation --------------------------

def test_sync_players_runs_when_never_run_before():
    due = s.due_jobs(
        'premiership', _utc(2026, 9, 22, 10), LON,
        {}, live_now=False, finalize_done=True, rounds_known=True)

    assert 'sync_players' in due


def test_sync_players_respects_its_daily_floor():
    """It scrapes eight SuperBru pages, and the cron ticks every ten minutes."""
    now = _utc(2026, 9, 22, 10)
    recent = (now - timedelta(hours=3)).isoformat()

    due = s.due_jobs(
        'premiership', now, LON, {'sync_players': recent},
        live_now=False, finalize_done=True, rounds_known=True)

    assert 'sync_players' not in due


def test_sync_players_runs_again_after_a_day():
    now = _utc(2026, 9, 22, 10)
    old = (now - timedelta(hours=25)).isoformat()

    due = s.due_jobs(
        'premiership', now, LON, {'sync_players': old},
        live_now=False, finalize_done=True, rounds_known=True)

    assert 'sync_players' in due


def test_sync_players_is_ordered_before_lineups():
    """Resolving a team sheet needs the club rosters to be current - a player who
    transferred this week is otherwise matched against his old club and dropped.
    Both fall due together on a Thursday."""
    due = s.due_jobs(
        'premiership', _utc(2026, 9, 24, 14), LON,      # Thu, lineup window open
        {}, live_now=False, finalize_done=True, rounds_known=True)

    assert 'sync_players' in due and 'lineups' in due
    assert due.index('sync_players') < due.index('lineups')


def test_sync_players_is_not_tied_to_the_lineup_window():
    """Squads change mid-week; a Tuesday transfer must land before Thursday."""
    due = s.due_jobs(
        'premiership', _utc(2026, 9, 22, 9), LON,       # Tuesday morning
        {}, live_now=False, finalize_done=True, rounds_known=True)

    assert 'sync_players' in due
    assert 'lineups' not in due


# --- interval floors vs the cron tick ---------------------------------------
# The tick is the hard ceiling on every cadence: a floor can only be honoured if
# a tick arrives that often. deploy.sh installs */5, so the 5-minute floors are
# the tightest the scheduler can express.

def test_a_never_run_job_is_always_due():
    assert s._interval_ok(None, _utc(2026, 9, 25, 19), timedelta(minutes=5))


def test_the_floor_tolerates_a_fractionally_early_tick():
    """The bug this grace exists for.

    A floor is measured from the last run's COMPLETION. live_scoring scrapes eight
    SuperBru pages, so it finishes ~20s into its tick; the next */5 tick is then
    4m40s later - strictly too early. Without the grace that run is skipped and
    the real cadence quietly becomes 10 minutes, half the time.
    """
    now = _utc(2026, 9, 25, 19, 0)
    completed = (now - timedelta(minutes=4, seconds=40)).isoformat()

    assert s._interval_ok(completed, now, timedelta(minutes=5))


def test_the_grace_is_small_enough_not_to_double_the_rate():
    """It must absorb tick jitter, not licence a run a whole period early."""
    now = _utc(2026, 9, 25, 19, 0)
    half_a_period = (now - timedelta(minutes=2, seconds=30)).isoformat()

    assert not s._interval_ok(half_a_period, now, timedelta(minutes=5))
    assert s.INTERVAL_GRACE < timedelta(minutes=1)


def test_live_scoring_and_predict_share_the_live_cadence():
    """They run in the same tick during a match, so a mismatch would mean predict
    either lagged a scrape behind or fired without new scores."""
    assert s.INTERVALS['live_scoring'] == s.INTERVALS['predict_live']
    assert s.INTERVALS['live_scoring'] == timedelta(minutes=5)


def test_no_floor_is_tighter_than_the_cron_tick():
    """deploy.sh installs */5. A floor below that can never be honoured, so it
    would be documentation that lies about how often the job really runs - which
    is exactly what the 3-minute live_scoring floor was doing behind a */10 tick.
    """
    tick = timedelta(minutes=5)
    for job, floor in s.INTERVALS.items():
        assert floor >= tick, f'{job} floor {floor} is tighter than the {tick} tick'


def test_the_registry_declares_jobs_enabled_for_every_league():
    """cron_tick defaults a missing flag to True, so a typo here silently turns a
    league's ingestion back on rather than off."""
    from api.leagues import LEAGUES

    for slug, cfg in LEAGUES.items():
        assert 'jobs_enabled' in cfg, slug
        assert isinstance(cfg['jobs_enabled'], bool), slug


def test_meatyboys_ingestion_is_off_and_ofds_is_on():
    """Super Rugby has no data source wired (live.py returns [] for anything but
    the Premiership, and superbru_table is None), no users and a season that ended
    in June — so every job for it was a guaranteed no-op."""
    from api.leagues import LEAGUES

    assert LEAGUES['meatyboys']['jobs_enabled'] is False
    assert LEAGUES['ofds']['jobs_enabled'] is True


def test_the_tick_survives_a_disabled_league(tmp_path, monkeypatch):
    """Regression: the heartbeat summed s['due'] across every league summary, and
    a skipped league's entry has no 'due' key — so switching meatyboys off made
    /api/cron/tick 500 on every single tick, for BOTH leagues."""
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'tick.db'))
    from api import index as idx

    r = idx.app.test_client().get('/api/cron/tick')

    assert r.status_code == 200
    by_league = {L['league']: L for L in r.get_json()['leagues']}
    assert by_league['meatyboys'].get('skipped')
    assert 'due' in by_league['ofds']
