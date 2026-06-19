"""
g_live.py
---------
Live, in-round player projections and head-to-head win probabilities.

Runs every few minutes during the live window (cron). For each starting player:
  - if their real match has kicked off  -> lock in their live actual score
  - if their match hasn't started yet    -> keep their Gamma score distribution

Team final score = sum(locked actuals) + sum(distributions of not-yet-played).
Win % via the same cross-join as f_win_predictions. Naturally leak-free: a
not-yet-played player has no current-round row in detailed_scores.

Reads:
  - data/fixtures.json   (liveWindow gate)
  - data/draft{round}.json (live per-player score + real team, keyed by playerId)
  - ref_fixtures.kickoff (per-team kickoff time, AEST)
  - manager_team (Starting rosters), fantasy_matchups, all_predictions, detailed_scores

Writes: live_win_predictions, live_predictions (current round/season; DELETE+INSERT).
"""
import datetime as dt
import json
import logging
import sqlite3

import numpy as np
import pandas as pd

import params
import db_init
from e_helpers import load_scores, get_current_round
from f_win_predictions import _fit_gamma_percentiles

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s  %(levelname)s  %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger(__name__)

AEST_OFFSET = dt.timedelta(hours=10)  # CSV kickoffs are Sydney local; see save_round.week_cutoff


def _in_live_window(now_utc):
    """True if now is inside data/fixtures.json liveWindow (mirrors app.py)."""
    try:
        fx = json.loads((params.DATA_DIR / 'fixtures.json').read_text())
        lw = fx.get('liveWindow', {})
        start = dt.datetime.fromisoformat(lw['start'].replace('Z', '+00:00'))
        end   = dt.datetime.fromisoformat(lw['end'].replace('Z', '+00:00'))
        return start <= now_utc <= end
    except Exception:
        return False


def _load_live_scores(round_num):
    """playerId -> {'score': float, 'team': str} from data/draft{round}.json."""
    out = {}
    try:
        data = json.loads((params.DATA_DIR / f'draft{round_num}.json').read_text())
    except Exception:
        return out
    for p in data:
        pid = p.get('playerId')
        if not pid:
            continue
        try:
            score = float(str(p.get('score', 0) or 0))
        except (ValueError, TypeError):
            score = 0.0
        out[pid] = {'score': score, 'team': (p.get('team') or '').strip()}
    return out


def _played_teams(con, round_num, now_utc):
    """Set of real teams whose match has kicked off (now >= kickoff)."""
    played = set()
    rows = con.execute(
        'SELECT team, kickoff FROM ref_fixtures WHERE season = ? AND round_num = ?',
        (params.CURRENT_SEASON, round_num)
    ).fetchall()
    for team, kickoff in rows:
        if not kickoff:
            continue
        try:
            ko_utc = dt.datetime.fromisoformat(kickoff).replace(tzinfo=dt.timezone.utc) - AEST_OFFSET
        except ValueError:
            continue
        if now_utc >= ko_utc:
            played.add(team)
    return played


def run(con=None):
    close_after = con is None
    if con is None:
        con = sqlite3.connect(params.DB_PATH)
        con.execute('PRAGMA journal_mode=WAL')

    now_utc = dt.datetime.now(dt.timezone.utc)
    if not _in_live_window(now_utc):
        log.info('Outside live window — skipping live computation')
        if close_after:
            con.close()
        return

    db_init.init_db()  # ensure live tables / kickoff column exist
    round_num = get_current_round(con)
    log.info(f'=== Live update: round {round_num}, season {params.CURRENT_SEASON} ===')

    live      = _load_live_scores(round_num)
    played    = _played_teams(con, round_num, now_utc)
    scores_df = load_scores(con, n_seasons=2)
    lineups   = pd.read_sql("SELECT manager, playerid, role FROM manager_team", con)
    matchups  = pd.read_sql(f'''
        SELECT team_a, team_b, team_a_id, team_b_id, team_a_bonus, team_b_bonus
        FROM fantasy_matchups
        WHERE round_num = {round_num} AND season = {params.CURRENT_SEASON}
    ''', con)

    if lineups.empty or matchups.empty:
        log.warning('No lineups or matchups — nothing to compute')
        if close_after:
            con.close()
        return

    # Static projection fallback for not-yet-played players (per-player)
    proj = {}
    for r in con.execute('''SELECT playerid, gbm_pred, gamma_p50, baseline_season_avg
                            FROM all_predictions WHERE season_year = ? AND round_num = ?''',
                         (params.CURRENT_SEASON, round_num)).fetchall():
        proj[r[0]] = next((v for v in (r[1], r[2], r[3]) if v is not None), 0.0)

    id_to_name = {v: k for k, v in params.MANAGER_TEAMS.items()}

    # ── Build per-manager team distribution + locked total + per-player live rows ──
    team_dists, team_locked = {}, {}
    live_rows = []
    for manager in lineups['manager'].unique():
        starters = lineups[(lineups['manager'] == manager) &
                           (lineups['role'] == 'Starting')]['playerid'].tolist()
        dist, locked = np.zeros(100), 0.0
        for pid in starters:
            info = live.get(pid)
            team = info['team'] if info else None
            is_played = bool(team) and team in played
            if is_played:
                score = info['score']
                dist += score          # locked point mass
                locked += score
                status, projected = 'live', score
            else:
                hist = scores_df[scores_df['playerid'] == pid]['total'].dropna().tolist()
                pcts = _fit_gamma_percentiles(hist) if hist else None
                if pcts is not None:
                    dist += pcts
                elif hist:
                    dist += np.full(100, float(np.mean(hist)))
                status, projected = 'upcoming', proj.get(pid, 0.0)
            live_rows.append({
                'playerid': pid, 'playername': None, 'team': team, 'owner': manager,
                'round_num': round_num, 'season': params.CURRENT_SEASON,
                'live_score': (info['score'] if info else 0.0),
                'status': status, 'projected_final': round(float(projected), 1),
                'computed_at': now_utc.isoformat(),
            })
        team_dists[manager]  = dist
        team_locked[manager] = round(locked, 1)

    # ── Win probabilities per matchup ──────────────────────────────────────
    win_rows = []
    for _, row in matchups.iterrows():
        a = id_to_name.get(row['team_a_id'], row['team_a'])
        b = id_to_name.get(row['team_b_id'], row['team_b'])
        da, db_ = team_dists.get(a), team_dists.get(b)
        if da is None or db_ is None:
            continue
        adj_a = da + float(row.get('team_a_bonus', 0) or 0)
        adj_b = db_ + float(row.get('team_b_bonus', 0) or 0)
        diff = adj_a[:, None] - adj_b[None, :]
        total = diff.size
        win_rows.append({
            'round_num': round_num, 'season': params.CURRENT_SEASON,
            'team_a': row['team_a'], 'team_b': row['team_b'],
            'team_a_id': row['team_a_id'], 'team_b_id': row['team_b_id'],
            'team_a_win_prob': round(int((diff > 0).sum()) / total * 100, 1),
            'team_b_win_prob': round(int((diff < 0).sum()) / total * 100, 1),
            'draw_prob':       round(int((diff == 0).sum()) / total * 100, 1),
            'team_a_locked': team_locked.get(a, 0.0),
            'team_b_locked': team_locked.get(b, 0.0),
            'computed_at': now_utc.isoformat(),
        })

    # ── Persist (DELETE + INSERT current round/season) ─────────────────────
    con.execute('DELETE FROM live_win_predictions WHERE round_num = ? AND season = ?',
                (round_num, params.CURRENT_SEASON))
    con.execute('DELETE FROM live_predictions WHERE round_num = ? AND season = ?',
                (round_num, params.CURRENT_SEASON))
    if win_rows:
        pd.DataFrame(win_rows).to_sql('live_win_predictions', con, if_exists='append', index=False)
    if live_rows:
        pd.DataFrame(live_rows).to_sql('live_predictions', con, if_exists='append', index=False)
    con.commit()

    n_played = sum(1 for r in live_rows if r['status'] == 'live')
    log.info(f'Wrote {len(win_rows)} matchups, {len(live_rows)} players '
             f'({n_played} locked, played teams: {sorted(played)})')

    if close_after:
        con.close()
    log.info('=== Live update complete ===')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    run(con)
    con.close()


if __name__ == '__main__':
    main()
