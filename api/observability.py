"""Health of the ingestion pipeline, behind the maintainer-only /observability page.

The governing idea: **`status='ok'` is not health.** Every real outage this
pipeline has had logged a success.

  * `lineups` recorded `ok / 0 entries` on every run for weeks. ESPN was 403ing a
    spoofed User-Agent and 400ing a date range, and both failures were swallowed
    into an empty list - so the job "succeeded" and wrote nothing.
  * `predict` recorded `ok / launched for round 1` while the subprocess died
    immediately on `import numpy`, because the analysis requirements were never
    rsynced to the VM. The job only ever reported the launch, never the result.
  * `sync_players` didn't exist, so nothing was reported at all - and a job that
    never runs is invisible in a log of runs.

So this module reports three different things and keeps them apart:

  1. **liveness**   - is the cron tick itself still arriving?
  2. **job status** - per job: last run, failures, whether it is overdue
  3. **output freshness** - row counts and timestamps of what each job is
     supposed to WRITE, which is the only thing that catches a job succeeding at
     nothing

Checks are pure functions of the DB plus `now`, so they're testable without a
request and without the network.
"""

from datetime import datetime, timedelta, timezone

from .leagues import LEAGUES
from . import scheduler

# A tick is expected every 5 minutes (deploy.sh installs */5). Allow a wide
# margin before calling the cron dead - a slow scrape can push a tick late, and
# crying wolf on a healthy pipeline trains people to ignore the page.
TICK_INTERVAL = timedelta(minutes=5)
TICK_GRACE = timedelta(minutes=45)

# Jobs we expect to exist, with what drives their cadence. `window` jobs only run
# inside a match-week window, so being "stale" outside it is correct and must not
# be flagged.
JOB_SPECS = {
    'sync_rounds':  {'cadence': timedelta(hours=24), 'kind': 'interval',
                     'writes': 'rounds + real_fixtures',
                     'expect': 'daily'},
    'sync_players': {'cadence': timedelta(hours=24), 'kind': 'interval',
                     'writes': 'players',
                     'expect': 'daily'},
    'lineups':      {'cadence': timedelta(hours=2), 'kind': 'window',
                     'writes': 'match_lineups',
                     'expect': 'every 2h inside the team-sheet window'},
    'live_scoring': {'cadence': timedelta(minutes=5), 'kind': 'live',
                     'writes': 'weekly_stats',
                     'expect': 'every 5 min while a match is live'},
    'finalize':     {'cadence': None, 'kind': 'rollover',
                     'writes': 'weekly_stats (authoritative)',
                     'expect': 'once at the Tue 12:00 rollover'},
    'predict':      {'cadence': timedelta(hours=2), 'kind': 'interval',
                     'writes': 'player_predictions + matchup_predictions',
                     'expect': 'every 2h, and every 5 min while a match is '
                               'live, for the round being picked'},
}

ERROR, WARN, INFO = 'error', 'warn', 'info'


def _rows(cur):
    # dict() over the row, not an isinstance check: sqlite3.Row is a mapping but
    # is NOT a dict, so `dict(r) if isinstance(r, dict) else r` passed Rows
    # straight through and every later `.get()` blew up.
    return [dict(r) for r in cur.fetchall()]


def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def _age(iso, now):
    d = _dt(iso)
    return None if d is None else (now - d).total_seconds()


def _one(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return (list(row.values())[0] if isinstance(row, dict) else row[0])


# ---------------------------------------------------------------------------
# Per-job history
# ---------------------------------------------------------------------------

def job_summaries(conn, league_id: int, now: datetime) -> list[dict]:
    """One row per known job: its last run, its last failure, recent counts.

    Built from a single scan so the page stays cheap on a 1 vCPU box, and keyed by
    JOB_SPECS rather than by what's in the log - a job that has NEVER run is the
    most important thing to show, and it cannot appear in a GROUP BY of runs.
    """
    day_ago = (now - timedelta(hours=24)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    runs = _rows(conn.execute(
        'SELECT job, status, detail, run_at, round_number FROM job_runs '
        'WHERE league_id = ? AND run_at >= ? ORDER BY run_at DESC',
        (league_id, week_ago)))

    out = []
    for job, spec in JOB_SPECS.items():
        mine = [r for r in runs if r['job'] == job]
        last = mine[0] if mine else None
        # Fall back beyond the 7-day window so "last run" is never blank just
        # because the job is infrequent (finalize and predict run weekly).
        if last is None:
            older = _rows(conn.execute(
                'SELECT job, status, detail, run_at, round_number FROM job_runs '
                'WHERE league_id = ? AND job = ? ORDER BY run_at DESC LIMIT 1',
                (league_id, job)))
            last = older[0] if older else None

        ok_runs = [r for r in mine if r['status'] == 'ok']
        failures = [r for r in mine if r['status'] != 'ok']
        last_ok = ok_runs[0] if ok_runs else None
        if last_ok is None:
            older_ok = _rows(conn.execute(
                "SELECT run_at, detail FROM job_runs WHERE league_id = ? AND job = ? "
                "AND status = 'ok' ORDER BY run_at DESC LIMIT 1", (league_id, job)))
            last_ok = older_ok[0] if older_ok else None
        last_fail = failures[0] if failures else None

        out.append({
            'job': job,
            'writes': spec['writes'],
            'expect': spec['expect'],
            'kind': spec['kind'],
            'cadence_seconds': (spec['cadence'].total_seconds()
                                if spec['cadence'] else None),
            'never_run': last is None,
            'status': (last or {}).get('status'),
            'detail': (last or {}).get('detail'),
            'round': (last or {}).get('round_number'),
            'last_run': (last or {}).get('run_at'),
            'last_run_age': _age((last or {}).get('run_at'), now),
            'last_ok': (last_ok or {}).get('run_at'),
            'last_ok_age': _age((last_ok or {}).get('run_at'), now),
            'last_ok_detail': (last_ok or {}).get('detail'),
            'runs_24h': sum(1 for r in mine if r['run_at'] >= day_ago),
            'failures_24h': sum(1 for r in failures if r['run_at'] >= day_ago),
            'last_failure': ({'run_at': last_fail['run_at'],
                              'detail': last_fail['detail']}
                             if last_fail else None),
        })
    return out


# ---------------------------------------------------------------------------
# Output freshness - what the jobs actually wrote
# ---------------------------------------------------------------------------

def output_freshness(conn, league_id: int, active_round: int) -> list[dict]:
    """Row counts for each table the pipeline writes.

    This is the half that catches a job succeeding at nothing. `lineups` reporting
    `ok / 0 entries` looks fine in a job log and is obviously broken here, because
    the clubs-named count is zero on a match week.
    """
    def count(sql, params=()):
        return _one(conn, sql, params) or 0

    return [
        {'table': 'rounds', 'label': 'Round calendar',
         'written_by': 'sync_rounds',
         'count': count('SELECT COUNT(*) FROM rounds WHERE league_id = ?',
                        (league_id,)),
         'unit': 'rounds', 'scoped': 'league', 'updated': None},
        {'table': 'real_fixtures', 'label': 'Fixtures this round',
         'written_by': 'sync_rounds',
         'count': count('SELECT COUNT(*) FROM real_fixtures '
                        'WHERE league_id = ? AND round = ?', (league_id, active_round)),
         'unit': 'matches', 'scoped': 'round', 'updated': None},
        {'table': 'players', 'label': 'Player list',
         'written_by': 'sync_players',
         'count': count('SELECT COUNT(*) FROM players WHERE league_id = ?',
                        (league_id,)),
         'unit': 'players', 'scoped': 'league', 'updated': None},
        {'table': 'match_lineups', 'label': 'Team sheets this round',
         'written_by': 'lineups',
         'count': count('SELECT COUNT(*) FROM match_lineups '
                        'WHERE league_id = ? AND round = ?', (league_id, active_round)),
         'extra': {'clubs named': count(
             'SELECT COUNT(DISTINCT real_team) FROM match_lineups '
             'WHERE league_id = ? AND round = ?', (league_id, active_round))},
         'unit': 'entries', 'scoped': 'round',
         'updated': _one(conn, 'SELECT MAX(scraped_at) FROM match_lineups '
                               'WHERE league_id = ? AND round = ?',
                         (league_id, active_round))},
        {'table': 'weekly_stats', 'label': 'Player scores this round',
         'written_by': 'live_scoring / finalize',
         'count': count('SELECT COUNT(*) FROM weekly_stats '
                        'WHERE league_id = ? AND round = ?', (league_id, active_round)),
         'unit': 'rows', 'scoped': 'round',
         'updated': _one(conn, 'SELECT MAX(scraped_at) FROM weekly_stats '
                               'WHERE league_id = ? AND round = ?',
                         (league_id, active_round))},
        {'table': 'player_predictions', 'label': 'Player projections',
         'written_by': 'predict',
         'count': count('SELECT COUNT(*) FROM player_predictions '
                        'WHERE league_id = ? AND round = ?', (league_id, active_round)),
         'unit': 'rows', 'scoped': 'round', 'updated': None},
        {'table': 'matchup_predictions', 'label': 'Win probabilities',
         'written_by': 'predict',
         'count': count('SELECT COUNT(*) FROM matchup_predictions '
                        'WHERE league_id = ? AND round = ?', (league_id, active_round)),
         'unit': 'rows', 'scoped': 'round', 'updated': None},
    ]


# ---------------------------------------------------------------------------
# Derived checks
# ---------------------------------------------------------------------------

def last_tick(conn) -> tuple[str | None, bool]:
    """(timestamp, from_heartbeat) of the most recent cron tick.

    Reads pipeline_heartbeat, which records every tick including the ones with
    nothing to do. Falls back to the newest job_runs row when the heartbeat table
    is empty - which is the state on any deployment from before it existed, and on
    those the fallback under-reports liveness rather than lying about it.
    """
    try:
        beat = _one(conn, 'SELECT last_tick FROM pipeline_heartbeat WHERE id = 1')
    except Exception:
        beat = None
    if beat:
        return beat, True
    return _one(conn, 'SELECT MAX(run_at) FROM job_runs'), False


def _tick_check(conn, now) -> list[dict]:
    """Is the cron itself alive? Nothing else on the page means anything if not."""
    last, from_beat = last_tick(conn)
    age = _age(last, now)

    if age is None:
        return [{'level': ERROR, 'code': 'cron_never_ran',
                 'title': 'The cron has never reported in',
                 'detail': 'No heartbeat and no job runs. The crontab entry '
                           'calling /api/cron/tick may be missing, or CRON_SECRET '
                           'may be wrong - a 401 is rejected before anything is '
                           'recorded, so it leaves no trace here.'}]

    if age <= TICK_GRACE.total_seconds():
        return []

    mins = int(age // 60)
    every = int(TICK_INTERVAL.total_seconds() // 60)
    if from_beat:
        return [{'level': ERROR, 'code': 'cron_silent',
                 'title': 'Cron has gone quiet',
                 'detail': f'Last tick {mins} min ago; one is expected every '
                           f'{every} min. Check `crontab -l` on the VM and that '
                           f'the app is answering.'}]
    # No heartbeat row yet, so all we know is when a job last ran - and jobs only
    # run when they are due. Hours of silence overnight is normal, so this cannot
    # be reported as an outage.
    return [{'level': INFO, 'code': 'tick_unknown',
             'title': 'Cron liveness unknown',
             'detail': f'No heartbeat recorded yet, so this only shows the last '
                       f'job run ({mins} min ago). Jobs are logged only when they '
                       f'are due, so a quiet spell is expected. Liveness becomes '
                       f'exact from the next tick after this deploy.'}]


def _job_checks(conn, league_id, summaries, now, competition, tz_name,
                live_now) -> list[dict]:
    local = scheduler.to_local(now, tz_name)
    out = []
    for s in summaries:
        job, kind = s['job'], s['kind']

        if s['never_run']:
            # A rollover job that hasn't come round yet is not a problem.
            level = INFO if kind == 'rollover' else WARN
            out.append({'level': level, 'code': 'job_never_ran',
                        'title': f'{job} has never run',
                        'detail': f"Expected {s['expect']}. It writes "
                                  f"{s['writes']}, so nothing downstream of it "
                                  f"has data."})
            continue

        if s['status'] and s['status'] != 'ok':
            out.append({'level': ERROR, 'code': 'job_failing',
                        'title': f'{job} last run failed',
                        'detail': str(s['detail'])[:300]})

        # Overdue, but only where "late" is meaningful. A window job outside its
        # window and a live job with nothing live are both correctly idle.
        if kind == 'interval' and s['last_ok_age'] is not None:
            if s['last_ok_age'] > s['cadence_seconds'] * 2:
                hrs = s['last_ok_age'] / 3600
                out.append({'level': WARN, 'code': 'job_overdue',
                            'title': f'{job} is overdue',
                            'detail': f'Last success {hrs:.1f}h ago; expected '
                                      f'{s["expect"]}.'})
        if kind == 'window' and scheduler.in_lineup_window(local, competition):
            if s['last_ok_age'] is None or \
                    s['last_ok_age'] > s['cadence_seconds'] * 2:
                when = ('never' if s['last_ok_age'] is None
                        else f"{s['last_ok_age'] / 3600:.1f}h ago")
                out.append({'level': WARN, 'code': 'job_overdue',
                            'title': f'{job} is overdue inside its window',
                            'detail': f'The team-sheet window is open and the last '
                                      f'success was {when}.'})
        if kind == 'live' and live_now and (
                s['last_ok_age'] is None
                or s['last_ok_age'] > s['cadence_seconds'] * 4):
            out.append({'level': ERROR, 'code': 'job_overdue',
                        'title': 'live_scoring is not running during a match',
                        'detail': 'A match is live and scores are not being '
                                  'scraped, so the table is going stale mid-round.'})
    return out


def _empty_success_checks(summaries, freshness, active_round, competition,
                          now, tz_name, live_now) -> list[dict]:
    """The class of failure that logs success. Every one of these has happened."""
    local = scheduler.to_local(now, tz_name)
    by_table = {f['table']: f for f in freshness}
    by_job = {s['job']: s for s in summaries}
    out = []

    lineups = by_job.get('lineups') or {}
    sheets = by_table.get('match_lineups') or {}
    in_window = scheduler.in_lineup_window(local, competition)
    if in_window and lineups.get('status') == 'ok' and not sheets.get('count'):
        out.append({'level': ERROR, 'code': 'ok_but_empty',
                    'title': 'lineups reports success but wrote nothing',
                    'detail': f'Last run: {lineups.get("detail")!r}. No team-sheet '
                              f'rows exist for round {active_round} while the '
                              f'window is open. This is exactly how the ESPN 403 '
                              f'and the 400 on date ranges presented.'})

    predict = by_job.get('predict') or {}
    proj = by_table.get('player_predictions') or {}
    if predict.get('status') == 'ok' and 'launched' in str(predict.get('detail', '')) \
            and not proj.get('count'):
        out.append({'level': WARN, 'code': 'launched_no_output',
                    'title': 'predict launched but wrote no rows',
                    'detail': f'The job records only the launch, never the result, '
                              f'so "ok" here says nothing about whether the model '
                              f'ran. There are no projections for round '
                              f'{active_round}. Re-run it in the foreground to see '
                              f'why: `python -m api.predict --league <id> --round '
                              f'{active_round}`. It exits quietly when the round '
                              f'has no fixtures or when there is no scoring '
                              f'history to train on, and dies outright if the '
                              f'analysis dependencies are missing.'})

    scores = by_table.get('weekly_stats') or {}
    if live_now and not scores.get('count'):
        out.append({'level': WARN, 'code': 'no_scores_live',
                    'title': 'A match is live with no scores recorded',
                    'detail': f'weekly_stats has no rows for round {active_round}.'})

    players = by_table.get('players') or {}
    if not players.get('count'):
        out.append({'level': ERROR, 'code': 'no_players',
                    'title': 'The player list is empty',
                    'detail': 'Nothing can be drafted, scored or projected.'})

    rounds = by_table.get('rounds') or {}
    if not rounds.get('count'):
        out.append({'level': ERROR, 'code': 'no_rounds',
                    'title': 'No round calendar',
                    'detail': 'Lockout, rollover and every schedule decision '
                              'depend on this.'})
    return out


def league_health(conn, league_id: int, slug: str, active_round: int,
                  live_now: bool, now: datetime | None = None) -> dict:
    """Everything the page shows for one league."""
    now = now or datetime.now(timezone.utc)
    cfg = LEAGUES.get(slug) or {}
    competition = cfg.get('competition', '')
    tz_name = cfg.get('timezone', 'UTC')

    summaries = job_summaries(conn, league_id, now)
    freshness = output_freshness(conn, league_id, active_round)

    warnings = (_job_checks(conn, league_id, summaries, now, competition,
                            tz_name, live_now)
                + _empty_success_checks(summaries, freshness, active_round,
                                       competition, now, tz_name, live_now))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    warnings.sort(key=lambda w: order.get(w['level'], 3))

    return {
        'league_id': league_id,
        'slug': slug,
        'name': cfg.get('name', slug),
        'competition': competition,
        'timezone': tz_name,
        'local_time': scheduler.to_local(now, tz_name).isoformat(),
        'active_round': active_round,
        'live_now': live_now,
        'in_lineup_window': scheduler.in_lineup_window(
            scheduler.to_local(now, tz_name), competition),
        'jobs': summaries,
        'outputs': freshness,
        'warnings': warnings,
        'worst': (warnings[0]['level'] if warnings else 'ok'),
    }
