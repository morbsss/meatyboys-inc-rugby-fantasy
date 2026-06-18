"""
e_helpers.py
------------
Shared data loading, feature engineering, and model training utilities
for the prediction pipeline.
"""
import math
import datetime as dt
import sqlite3

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

import params

CURRENT_SEASON_WEIGHT = 1.0
PRIOR_SEASON_WEIGHT   = 0.7

NEWS_STARTED = {'starting', 'starting (c)'}

POSITION_PLAYER_COUNTS = {
    'Outside Back':  3,
    'Loose Forward': 2,
    'Midfielder':    2,
}


# ── Data loaders ────────────────────────────────────────────────────────────────

def load_scores(con, n_seasons=2):
    """
    Load detailed_scores for the last n_seasons, mapping historical player IDs
    to canonical IDs via player_id_map.

    Returns DataFrame with columns:
        playerid (canonical), season_year, round_num, team, opposition,
        position, playername, owner, news, mins, total, started, is_current_season
    """
    season_list = sorted(params.SEASON_START_DATES.keys())[-n_seasons:]
    seasons_sql = ','.join(str(s) for s in season_list)

    df = pd.read_sql(f'''
        SELECT
            m.canonical_playerid   AS playerid,
            ds.season_year,
            ds.round_num,
            ds.team,
            ds.opposition,
            ds.position,
            ds.playername,
            ds.owner,
            ds.news,
            ds.mins,
            ds.total
        FROM detailed_scores ds
        JOIN player_id_map m
            ON  ds.playerid    = m.source_playerid
            AND ds.season_year = m.source_season_year
        WHERE ds.season_year IN ({seasons_sql})
          AND ds.total IS NOT NULL
          AND m.canonical_playerid IS NOT NULL
        ORDER BY m.canonical_playerid, ds.season_year, ds.round_num
    ''', con)

    df['started'] = df['news'].str.lower().isin(NEWS_STARTED).astype(int)
    df['is_current_season'] = (df['season_year'] == params.CURRENT_SEASON).astype(int)
    return df


def load_player_list(con):
    return pd.read_sql(
        'SELECT playerid, playername, team, position, owner, news FROM player_list',
        con
    )


def load_opp_deltas(con):
    return pd.read_sql('SELECT opposition, position, delta FROM opp_position_deltas', con)


def _week_cutoff(game_date):
    """Monday 07:00 AEST (= Sunday 21:00 UTC) of the week containing game_date.

    Mirrors scripts/save_round.py so the analytics pipeline advances rounds at
    exactly the same moment as the live JSON pipeline.
    """
    monday = game_date - dt.timedelta(days=game_date.weekday())
    return (dt.datetime(monday.year, monday.month, monday.day, tzinfo=dt.timezone.utc)
            - dt.timedelta(hours=3))


def get_current_round(con=None):
    """Return the current round number.

    Mirrors the JSON pipeline (scripts/save_round.py): a round becomes current
    at Monday 07:00 AEST of the week containing its first game. Walks each
    round's first match_date from ref_fixtures and returns the latest round
    whose cutoff has passed. Falls back to date arithmetic only when the
    fixtures table is empty or unavailable.
    """
    if con is not None:
        try:
            rows = con.execute('''
                SELECT round_num, MIN(match_date) AS first_date
                FROM ref_fixtures
                WHERE season = ?
                GROUP BY round_num
                ORDER BY first_date
            ''', (params.CURRENT_SEASON,)).fetchall()
            if rows:
                now = dt.datetime.now(dt.timezone.utc)
                current = rows[-1][0]
                for i, (_, first_date) in enumerate(rows):
                    if _week_cutoff(dt.date.fromisoformat(first_date)) > now:
                        current = rows[i - 1][0] if i > 0 else rows[0][0]
                        break
                return current
        except Exception:
            pass
    start = dt.date.fromisoformat(params.SEASON_START_DATES[params.CURRENT_SEASON])
    return math.ceil((dt.date.today() - start).days / 7)


def load_upcoming_fixtures(con):
    """Return fixtures for the current round."""
    round_num = get_current_round(con)
    return pd.read_sql(f'''
        SELECT round_num, team, opposition, home_away, match_date
        FROM ref_fixtures
        WHERE season = {params.CURRENT_SEASON}
          AND round_num = {round_num}
    ''', con)


# ── Feature engineering ─────────────────────────────────────────────────────────

def _opp_strength_features(scores_df):
    """
    Compute lag-based opposition strength features per (season_year, round_num, opposition, position).
    Returns a DataFrame indexed by (season_year, round_num, opposition, position) with columns:
        opp_pos_strength, opp_pos_last3, opp_pos_last5, opp_pos_max_allowed
    """
    df = scores_df[scores_df['mins'] >= 5].copy()

    # Game-level total per (season_year, round_num, team, opposition, position)
    game = (df.groupby(['season_year', 'round_num', 'team', 'opposition', 'position'])
              ['total'].sum().reset_index(name='game_total'))

    # Team's cumulative avg per position (lagged by 1 round to avoid leakage)
    game = game.sort_values(['season_year', 'team', 'position', 'round_num'])
    game['team_cum_avg'] = (game.groupby(['season_year', 'team', 'position'])['game_total']
                               .transform(lambda x: x.shift(1).expanding().mean()))

    n_map = POSITION_PLAYER_COUNTS
    game['n_players'] = game['position'].map(n_map).fillna(1)
    game['delta'] = (game['game_total'] - game['team_cum_avg'].fillna(0)) / game['n_players']

    # Aggregate by opposition-position-round (how much did attackers score above avg against this opp)
    opp = (game.groupby(['season_year', 'round_num', 'opposition', 'position'])
               ['delta'].mean().reset_index(name='opp_pos_strength'))
    opp = opp.sort_values(['season_year', 'opposition', 'position', 'round_num'])

    grp = opp.groupby(['season_year', 'opposition', 'position'])
    opp['opp_pos_last3'] = grp['opp_pos_strength'].transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean())
    opp['opp_pos_last5'] = grp['opp_pos_strength'].transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())

    # Max per-player score conceded per round
    raw_per_player = (game.groupby(['season_year', 'round_num', 'opposition', 'position'])
                          .apply(lambda g: (g['game_total'] / g['n_players']).max())
                          .reset_index(name='max_per_player'))
    opp = opp.merge(raw_per_player, on=['season_year', 'round_num', 'opposition', 'position'], how='left')
    opp['opp_pos_max_allowed'] = (opp.groupby(['season_year', 'opposition', 'position'])['max_per_player']
                                     .transform(lambda x: x.shift(1).rolling(5, min_periods=1).max()))
    return opp[['season_year', 'round_num', 'opposition', 'position',
                'opp_pos_strength', 'opp_pos_last3', 'opp_pos_last5', 'opp_pos_max_allowed']]


def engineer_features(scores_df):
    """
    Add rolling player form + opposition strength features to scores_df.
    All features use shift(1) lag to prevent data leakage.

    Returns (enriched_df, opp_agg_df)
    """
    df = scores_df.sort_values(['playerid', 'season_year', 'round_num']).copy()
    grp = df.groupby('playerid')

    # Player form features (lagged)
    df['avg_3']   = grp['total'].transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean())
    df['avg_5']   = grp['total'].transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
    df['std_5']   = grp['total'].transform(lambda x: x.shift(1).rolling(5, min_periods=1).std())
    df['max_3']   = grp['total'].transform(lambda x: x.shift(1).rolling(3, min_periods=1).max())
    df['max_5']   = grp['total'].transform(lambda x: x.shift(1).rolling(5, min_periods=1).max())
    df['p75_3']   = grp['total'].transform(lambda x: x.shift(1).rolling(3, min_periods=1).quantile(0.75))
    df['vol_3']   = (df['std_5'] / df['avg_3'].replace(0, np.nan)).fillna(0)

    # Cumulative season stats (lagged)
    curr = df[df['is_current_season'] == 1].copy()
    curr_grp = curr.groupby('playerid')
    curr['season_games'] = curr_grp.cumcount()
    curr['season_sum']   = curr_grp['total'].transform(lambda x: x.shift(1).expanding().sum())
    curr['season_avg']   = curr_grp['total'].transform(lambda x: x.shift(1).expanding().mean())
    curr['season_std']   = curr_grp['total'].transform(lambda x: x.shift(1).expanding().std())
    curr['season_min']   = curr_grp['total'].transform(lambda x: x.shift(1).expanding().min())
    curr['season_max']   = curr_grp['total'].transform(lambda x: x.shift(1).expanding().max())

    df = df.merge(
        curr[['playerid', 'season_year', 'round_num',
              'season_games', 'season_sum', 'season_avg', 'season_std', 'season_min', 'season_max']],
        on=['playerid', 'season_year', 'round_num'], how='left'
    )

    # Opposition strength features
    opp_agg = _opp_strength_features(scores_df)
    df = df.merge(opp_agg, on=['season_year', 'round_num', 'opposition', 'position'], how='left')

    return df, opp_agg


def build_prediction_row(playerid, team, opposition, position, news, scores_df, opp_agg):
    """
    Build a single feature row for an upcoming round prediction (no lag needed —
    we use the full history up to now).
    """
    history = scores_df[scores_df['playerid'] == playerid].sort_values('round_num')

    started = 1 if str(news).lower() in NEWS_STARTED else 0

    if history.empty:
        return {k: 0 for k in ['avg_3', 'max_3', 'max_5', 'p75_3', 'vol_3', 'started',
                                'opp_pos_last3', 'opp_pos_last5', 'opp_pos_strength',
                                'opp_pos_max_allowed', 'is_current_season',
                                'season_avg', 'season_std', 'season_max']}

    curr = history[history['is_current_season'] == 1]['total']
    all_t = history['total']
    last3 = all_t.tail(3)
    last5 = all_t.tail(5)
    avg_3 = last3.mean() if len(last3) >= 1 else 0
    std_5 = last5.std() if len(last5) >= 2 else 0
    vol_3 = (std_5 / avg_3) if avg_3 != 0 else 0

    # Latest opposition strength for this position-opposition combo
    opp_row = (opp_agg[(opp_agg['opposition'] == opposition) & (opp_agg['position'] == position)]
               .sort_values(['season_year', 'round_num'])
               .tail(1))

    opp_features = {
        'opp_pos_strength':   opp_row['opp_pos_strength'].values[0] if not opp_row.empty else 0,
        'opp_pos_last3':      opp_row['opp_pos_last3'].values[0]    if not opp_row.empty else 0,
        'opp_pos_last5':      opp_row['opp_pos_last5'].values[0]    if not opp_row.empty else 0,
        'opp_pos_max_allowed':opp_row['opp_pos_max_allowed'].values[0] if not opp_row.empty else 0,
    }

    return {
        'avg_3':               avg_3,
        'max_3':               last3.max() if len(last3) >= 1 else 0,
        'max_5':               last5.max() if len(last5) >= 1 else 0,
        'p75_3':               last3.quantile(0.75) if len(last3) >= 1 else 0,
        'vol_3':               vol_3,
        'started':             started,
        'is_current_season':   1,
        'season_avg':          curr.mean() if len(curr) >= 1 else 0,
        'season_std':          curr.std()  if len(curr) >= 2 else 0,
        'season_max':          curr.max()  if len(curr) >= 1 else 0,
        **opp_features,
    }


GBM_FEATURES = [
    'avg_3', 'max_3', 'max_5', 'p75_3', 'vol_3', 'started',
    'opp_pos_last3', 'opp_pos_last5', 'opp_pos_strength', 'opp_pos_max_allowed',
    'is_current_season', 'season_avg', 'season_std', 'season_max',
]


def train_gbm(features_df, target_round):
    """Train GBM on all data strictly before target_round. Returns fitted model."""
    train = features_df[features_df['round_num'] < target_round].dropna(subset=GBM_FEATURES + ['total'])
    if len(train) < 30:
        return None

    X = train[GBM_FEATURES].fillna(0)
    y = train['total']
    w = np.where(train['is_current_season'] == 1, CURRENT_SEASON_WEIGHT, PRIOR_SEASON_WEIGHT)

    model = HistGradientBoostingRegressor(
        max_depth=3, learning_rate=0.008, max_iter=125, random_state=42
    )
    model.fit(X, y, sample_weight=w)
    return model
