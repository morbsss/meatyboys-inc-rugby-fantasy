"""
f_win_predictions.py
--------------------
Calculates head-to-head win probabilities for the current fantasy round.

Approach (matching reference f3_win_predictions.py):
  1. Get each manager's starting players from manager_team
  2. Fit a Gamma distribution to each player's score history → 100 percentile values
  3. Sum percentile values across each manager's starting roster → 100 team score estimates
  4. Cross-join Team A's 100 estimates × Team B's 100 estimates (10,000 comparisons)
  5. Win probability = proportion where Team A score > Team B score

Requires:
  - manager_team populated (run b2_fetch_lineups.py first)
  - fantasy_matchups loaded (run save_matchups.py once)
  - detailed_scores with canonical player IDs (via player_id_map join)

Writes to: win_predictions table.
"""
import logging
import sqlite3

import numpy as np
import pandas as pd
from scipy.stats import gamma as scipy_gamma

import params
from e_helpers import load_scores, get_current_round

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

MIN_SCORES_FOR_GAMMA = 2
PERCENTILES = list(range(0, 100))  # 0–99, 100 values


def _fit_gamma_percentiles(scores: list) -> np.ndarray | None:
    """Fit Gamma to scores and return 100 percentile values, or None."""
    if len(scores) < MIN_SCORES_FOR_GAMMA:
        return None
    try:
        shape, loc, scale = scipy_gamma.fit(scores)
        return np.array([scipy_gamma.ppf(p / 100, shape, loc, scale) for p in PERCENTILES])
    except Exception:
        return None


def _team_score_distribution(manager: str, starting_pids: list, scores_df: pd.DataFrame) -> np.ndarray | None:
    """
    Sum Gamma percentile arrays across all starting players for a manager.
    Returns array of 100 team score estimates, or None if no players could be fitted.
    """
    team_dist = np.zeros(100)
    fitted_count = 0

    for pid in starting_pids:
        player_scores = scores_df[scores_df['playerid'] == pid]['total'].dropna().tolist()
        if not player_scores:
            # Player with no history — use 0 contribution
            continue
        pcts = _fit_gamma_percentiles(player_scores)
        if pcts is None:
            # Too few games — use mean as a flat estimate
            team_dist += np.full(100, np.mean(player_scores))
        else:
            team_dist += pcts
        fitted_count += 1

    if fitted_count == 0:
        log.warning(f'  {manager}: no players with score history')
        return None

    log.info(f'  {manager}: {fitted_count}/{len(starting_pids)} players fitted')
    return team_dist


def run(con=None):
    close_after = con is None
    if con is None:
        con = sqlite3.connect(params.DB_PATH)
        con.execute('PRAGMA journal_mode=WAL')

    round_num = get_current_round(con)
    log.info(f'=== Win predictions: round {round_num}, season {params.CURRENT_SEASON} ===')

    # ── 1. Load data ──────────────────────────────────────────────────────
    matchups = pd.read_sql(f'''
        SELECT team_a, team_b, team_a_id, team_b_id, team_a_bonus, team_b_bonus
        FROM fantasy_matchups
        WHERE round_num = {round_num} AND season = {params.CURRENT_SEASON}
    ''', con)

    if matchups.empty:
        log.warning(f'No matchups found for round {round_num} — run save_matchups.py?')
        if close_after:
            con.close()
        return

    all_lineups = pd.read_sql(
        "SELECT manager, playerid, role FROM manager_team",
        con
    )

    if all_lineups.empty:
        log.warning('manager_team is empty — run b2_fetch_lineups.py first')
        if close_after:
            con.close()
        return

    # Reverse MANAGER_TEAMS to map team_id → manager name
    id_to_name = {v: k for k, v in params.MANAGER_TEAMS.items()}

    scores_df = load_scores(con, n_seasons=2)
    log.info(f'  {len(scores_df):,} score rows loaded')

    # ── 2. Build team score distributions ─────────────────────────────────
    team_dists = {}
    for manager in all_lineups['manager'].unique():
        starters = all_lineups[(all_lineups['manager'] == manager) & (all_lineups['role'] == 'Starting')]['playerid'].tolist()
        if starters:
            dist = _team_score_distribution(manager, starters, scores_df)
            if dist is not None:
                team_dists[manager] = dist
        else:
            log.warning(f'  {manager}: no starters set — using zero score distribution')
            team_dists[manager] = np.zeros(100)

    # ── 3. Calculate win probabilities for each matchup ───────────────────
    records = []
    for _, row in matchups.iterrows():
        team_a_name = id_to_name.get(row['team_a_id'], row['team_a'])
        team_b_name = id_to_name.get(row['team_b_id'], row['team_b'])

        dist_a = team_dists.get(team_a_name)
        dist_b = team_dists.get(team_b_name)

        if dist_a is None or dist_b is None:
            log.warning(f'  Skipping {team_a_name} vs {team_b_name}: missing distribution')
            continue

        # Apply any bonus scores
        adj_a = dist_a + float(row.get('team_a_bonus', 0))
        adj_b = dist_b + float(row.get('team_b_bonus', 0))

        # Cross-join: 100 × 100 = 10,000 comparisons
        diff = adj_a[:, None] - adj_b[None, :]   # shape (100, 100)
        total = diff.size
        a_wins  = int((diff > 0).sum())
        b_wins  = int((diff < 0).sum())
        draws   = int((diff == 0).sum())

        a_prob = round(a_wins / total * 100, 1)
        b_prob = round(b_wins / total * 100, 1)
        d_prob = round(draws  / total * 100, 1)

        log.info(f'  {team_a_name} {a_prob}% vs {team_b_name} {b_prob}% (draw {d_prob}%)')

        records.append({
            'round_num':       round_num,
            'season':          params.CURRENT_SEASON,
            'team_a':          row['team_a'],
            'team_b':          row['team_b'],
            'team_a_id':       row['team_a_id'],
            'team_b_id':       row['team_b_id'],
            'team_a_win_prob': a_prob,
            'team_b_win_prob': b_prob,
            'draw_prob':       d_prob,
        })

    if not records:
        log.warning('No win predictions computed')
        if close_after:
            con.close()
        return

    out = pd.DataFrame(records)
    con.execute(
        'DELETE FROM win_predictions WHERE round_num = ? AND season = ?',
        (round_num, params.CURRENT_SEASON)
    )
    out.to_sql('win_predictions', con, if_exists='append', index=False)
    con.commit()

    log.info(f'Wrote {len(out)} matchup predictions to win_predictions')

    if close_after:
        con.close()
    log.info('=== Win predictions complete ===')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    run(con)
    con.close()


if __name__ == '__main__':
    main()
