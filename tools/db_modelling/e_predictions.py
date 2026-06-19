"""
e_predictions.py
----------------
Runs all prediction methods for every player × current round and writes
results to all_predictions table.

Methods:
  1. baseline_season_avg  – mean total across last 2 seasons
  2. baseline_3g_avg      – last-3-game mean (current season)
  3. baseline_5g_avg      – last-5-game mean (current season)
  4. simple_season_pred   – baseline_season_avg ± opp delta
  5. simple_3g_pred       – baseline_3g_avg ± opp delta
  6. simple_5g_pred       – baseline_5g_avg ± opp delta
  7. gamma_p30/p50/p70    – Gamma distribution percentiles
  8. weibull_p30/p50/p70  – Weibull distribution percentiles (delta-adjusted)
  9. gbm_pred             – HistGradientBoosting regressor

Safe to re-run; replaces current-round rows in all_predictions.
"""
import logging
import sqlite3

import numpy as np
import pandas as pd
from scipy.stats import gamma as scipy_gamma, weibull_min

import params
from e_helpers import (
    load_scores,
    load_player_list,
    load_opp_deltas,
    load_upcoming_fixtures,
    get_current_round,
    engineer_features,
    build_prediction_row,
    train_gbm,
    GBM_FEATURES,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

MIN_DIST_ROWS       = 5
CURRENT_YEAR_WEIGHT = 2   # duplicate current-season rows this many times for Weibull


# ── Distribution helpers ─────────────────────────────────────────────────────

def _gamma_percentiles(scores: list) -> dict:
    """Fit Gamma distribution and return p30/p50/p70, or NaN if insufficient data."""
    if len(scores) < MIN_DIST_ROWS:
        return {'gamma_p30': np.nan, 'gamma_p50': np.nan, 'gamma_p70': np.nan}
    try:
        shape, loc, scale = scipy_gamma.fit(scores)
        return {
            'gamma_p30': float(scipy_gamma.ppf(0.30, shape, loc, scale)),
            'gamma_p50': float(scipy_gamma.ppf(0.50, shape, loc, scale)),
            'gamma_p70': float(scipy_gamma.ppf(0.70, shape, loc, scale)),
        }
    except Exception:
        return {'gamma_p30': np.nan, 'gamma_p50': np.nan, 'gamma_p70': np.nan}


def _weibull_percentiles(scores: list, delta: float = 0.0) -> dict:
    """
    Fit Weibull distribution (shift-fit approach) and return delta-adjusted
    p30/p50/p70, or NaN if insufficient data.
    """
    if len(scores) < MIN_DIST_ROWS:
        return {'weibull_p30': np.nan, 'weibull_p50': np.nan, 'weibull_p70': np.nan}
    arr = np.array(scores, dtype=float)
    if np.std(arr) == 0:
        arr = arr + np.linspace(0, 0.01, len(arr))
    shift = float(arr.min()) - 0.001
    shifted = arr - shift
    try:
        shape, _, scale = weibull_min.fit(shifted, floc=0)
    except Exception:
        try:
            shifted[0] += 0.1
            shape, _, scale = weibull_min.fit(shifted, floc=0)
        except Exception:
            return {'weibull_p30': np.nan, 'weibull_p50': np.nan, 'weibull_p70': np.nan}

    hist_min = float(arr.min())
    results = {}
    for pct, key in [(0.30, 'weibull_p30'), (0.50, 'weibull_p50'), (0.70, 'weibull_p70')]:
        base = float(weibull_min.ppf(pct, shape, loc=0, scale=scale)) + shift
        results[key] = float(max(base + delta, hist_min))
    return results


# ── Baseline helpers (methods 1–3) ───────────────────────────────────────────

def _build_baselines(scores_df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns DataFrame keyed on playerid with columns:
        baseline_season_avg, baseline_3g_avg, baseline_5g_avg
    """
    season_avg = (scores_df.groupby('playerid')['total']
                  .mean().reset_index()
                  .rename(columns={'total': 'baseline_season_avg'}))

    cur = (scores_df[scores_df['is_current_season'] == 1]
           .sort_values(['playerid', 'round_num']))

    avg3 = (cur.groupby('playerid')['total']
            .apply(lambda x: x.tail(3).mean() if len(x) >= 1 else np.nan)
            .reset_index().rename(columns={'total': 'baseline_3g_avg'}))
    avg5 = (cur.groupby('playerid')['total']
            .apply(lambda x: x.tail(5).mean() if len(x) >= 1 else np.nan)
            .reset_index().rename(columns={'total': 'baseline_5g_avg'}))

    out = season_avg.merge(avg3, on='playerid', how='left')
    out = out.merge(avg5, on='playerid', how='left')
    return out


# ── Distribution predictions (methods 7–8) ──────────────────────────────────

def _build_dist_predictions(
    scores_df: pd.DataFrame,
    pred_frame: pd.DataFrame,
    opp_deltas: pd.DataFrame,
) -> pd.DataFrame:
    """
    Returns DataFrame with: playerid, round_num, gamma_p30/p50/p70, weibull_p30/p50/p70.
    Current-season rows are duplicated CURRENT_YEAR_WEIGHT times for Weibull fitting.
    """
    delta_lookup = (opp_deltas.set_index(['opposition', 'position'])['delta'].to_dict())

    # Players with no current-season games get zeros
    cur_games = (scores_df[scores_df['is_current_season'] == 1]
                 .groupby('playerid')['total'].count())
    zero_game_players = (set(pred_frame['playerid'].unique())
                         - set(cur_games[cur_games > 0].index))

    # Build weighted scores for Weibull: duplicate current-season rows
    cur_rows = scores_df[scores_df['is_current_season'] == 1]
    weighted_df = pd.concat(
        [scores_df] + [cur_rows] * (CURRENT_YEAR_WEIGHT - 1),
        ignore_index=True
    )

    records = []
    for _, row in pred_frame.drop_duplicates(['playerid', 'round_num']).iterrows():
        pid = row['playerid']
        rnd = int(row['round_num'])
        opp = row['opposition']
        pos = row['position']

        if pid in zero_game_players:
            records.append({
                'playerid': pid, 'round_num': rnd,
                'gamma_p30': 0.0, 'gamma_p50': 0.0, 'gamma_p70': 0.0,
                'weibull_p30': 0.0, 'weibull_p50': 0.0, 'weibull_p70': 0.0,
            })
            continue

        raw_scores = scores_df[scores_df['playerid'] == pid]['total'].tolist()
        w_scores   = weighted_df[weighted_df['playerid'] == pid]['total'].tolist()
        delta      = delta_lookup.get((opp, pos), 0.0)

        g_pcts = _gamma_percentiles(raw_scores)
        w_pcts = _weibull_percentiles(w_scores, delta=delta)

        records.append({'playerid': pid, 'round_num': rnd, **g_pcts, **w_pcts})

    return pd.DataFrame(records)


# ── GBM predictions (method 9) ───────────────────────────────────────────────

def _build_gbm_predictions(
    scores_df: pd.DataFrame,
    features_df: pd.DataFrame,
    opp_agg: pd.DataFrame,
    pred_frame: pd.DataFrame,
    target_round: int,
) -> pd.DataFrame:
    model = train_gbm(features_df, target_round)
    if model is None:
        log.warning('  GBM: insufficient training data — skipping')
        return pd.DataFrame(columns=['playerid', 'round_num', 'gbm_pred'])

    records = []
    for _, row in pred_frame.iterrows():
        feat = build_prediction_row(
            playerid   = row['playerid'],
            team       = row['team'],
            opposition = row['opposition'],
            position   = row['position'],
            news       = row['news'],
            scores_df  = scores_df,
            opp_agg    = opp_agg,
        )
        if feat is None:
            continue
        feat_df = pd.DataFrame([feat])
        pred    = float(model.predict(feat_df[GBM_FEATURES])[0])
        records.append({
            'playerid':  row['playerid'],
            'round_num': int(row['round_num']),
            'gbm_pred':  round(pred, 3),
        })

    return pd.DataFrame(records)


# ── Main orchestrator ────────────────────────────────────────────────────────

def run(con=None):
    close_after = con is None
    if con is None:
        con = sqlite3.connect(params.DB_PATH)
        con.execute('PRAGMA journal_mode=WAL')

    log.info('=== Predictions job started ===')

    # ── 1. Load data ──────────────────────────────────────────────────────
    log.info('Loading data …')
    scores_df  = load_scores(con, n_seasons=2)
    players    = load_player_list(con)
    fixtures   = load_upcoming_fixtures(con)
    opp_deltas = load_opp_deltas(con)

    round_num = get_current_round(con)
    log.info(f'  Current round: {round_num}')

    # Static predictions must use only data BEFORE the target round, so they stay
    # clean pre-round projections even after the current round's live scores land
    # in detailed_scores. (GBM training already enforces round_num < target_round.)
    scores_df = scores_df[~((scores_df['season_year'] == params.CURRENT_SEASON) &
                            (scores_df['round_num'] >= round_num))].copy()
    log.info(f'  {len(scores_df):,} score rows | {len(players):,} players | '
             f'{len(fixtures):,} fixture rows')

    if fixtures.empty:
        log.warning('No fixtures found for current round — nothing to predict')
        if close_after:
            con.close()
        return

    # ── 2. Build prediction frame (players × fixtures) ────────────────────
    pred_frame = (players.merge(fixtures[['team', 'round_num', 'opposition']], on='team', how='inner')
                  .dropna(subset=['round_num']))
    pred_frame['round_num'] = pred_frame['round_num'].astype(int)
    log.info(f'  {len(pred_frame):,} player-fixture pairs to predict')

    if pred_frame.empty:
        log.warning('No players matched to fixtures — nothing to predict')
        if close_after:
            con.close()
        return

    # ── 3. Baselines (methods 1–3) ────────────────────────────────────────
    log.info('Computing baselines …')
    baselines = _build_baselines(scores_df)

    # ── 4. Simple delta-adjusted predictions (methods 4–6) ────────────────
    log.info('Computing simple delta-adjusted predictions …')
    delta_df = pred_frame.merge(opp_deltas[['opposition', 'position', 'delta']],
                                on=['opposition', 'position'], how='left')
    delta_df['delta'] = delta_df['delta'].fillna(0.0)
    delta_df = delta_df.merge(baselines, on='playerid', how='left')

    delta_df['simple_season_pred'] = (delta_df['baseline_season_avg'] + delta_df['delta']).round(3)
    delta_df['simple_3g_pred']     = (delta_df['baseline_3g_avg']     + delta_df['delta']).round(3)
    delta_df['simple_5g_pred']     = (delta_df['baseline_5g_avg']     + delta_df['delta']).round(3)

    # ── 5. Distribution predictions (methods 7–8) ─────────────────────────
    log.info('Fitting Gamma and Weibull distributions …')
    dist_preds = _build_dist_predictions(scores_df, pred_frame, opp_deltas)

    # ── 6. GBM predictions (method 9) ─────────────────────────────────────
    log.info('Running GBM predictions …')
    features_df, opp_agg = engineer_features(scores_df)
    gbm_preds = _build_gbm_predictions(scores_df, features_df, opp_agg, pred_frame, round_num)

    # ── 7. Assemble output ────────────────────────────────────────────────
    log.info('Assembling final predictions table …')
    out = delta_df[[
        'playerid', 'playername', 'team', 'position', 'owner', 'news',
        'round_num', 'opposition',
        'baseline_season_avg', 'baseline_3g_avg', 'baseline_5g_avg',
        'simple_season_pred', 'simple_3g_pred', 'simple_5g_pred',
    ]].copy()
    out['season_year'] = params.CURRENT_SEASON

    out = out.merge(dist_preds[['playerid', 'round_num',
                                'gamma_p30', 'gamma_p50', 'gamma_p70',
                                'weibull_p30', 'weibull_p50', 'weibull_p70']],
                    on=['playerid', 'round_num'], how='left')

    out = out.merge(gbm_preds[['playerid', 'round_num', 'gbm_pred']],
                    on=['playerid', 'round_num'], how='left')

    out['actual_score'] = None

    # Round numeric columns
    for col in ['baseline_season_avg', 'baseline_3g_avg', 'baseline_5g_avg',
                'gamma_p30', 'gamma_p50', 'gamma_p70',
                'weibull_p30', 'weibull_p50', 'weibull_p70']:
        if col in out.columns:
            out[col] = out[col].round(3)

    # ── 8. Persist ────────────────────────────────────────────────────────
    con.execute(
        'DELETE FROM all_predictions WHERE round_num = ? AND season_year = ?',
        (round_num, params.CURRENT_SEASON)
    )
    out.to_sql('all_predictions', con, if_exists='append', index=False)
    con.commit()

    log.info(f'Wrote {len(out):,} rows to all_predictions (round {round_num}, season {params.CURRENT_SEASON})')

    top = (out.dropna(subset=['gbm_pred'])
             .sort_values('gbm_pred', ascending=False)
             .head(10)[['playername', 'position', 'team', 'opposition', 'gbm_pred']])
    if not top.empty:
        log.info(f'\nTop 10 by GBM prediction:\n{top.to_string(index=False)}')

    if close_after:
        con.close()
    log.info('=== Predictions job complete ===')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    run(con)
    con.close()


if __name__ == '__main__':
    main()
