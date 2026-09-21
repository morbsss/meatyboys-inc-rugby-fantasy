"""
predict.py — offline analysis/prediction job (ported from tools/db_modelling).

Computes, per league and per target round, a projection for every player (and
each meatyboys club front-row UNIT) plus head-to-head win probabilities for the
round's fantasy matchups, and writes them to `player_predictions` /
`matchup_predictions`. The Analysis page only READS those tables, so the web
app needs no ML libraries at request time.

Models (all from our own weekly_stats deltas — points are cumulative, so a
round's points = its total minus the previous round's):
  • ssn_avg / avg3      — season + last-3-game means
  • opposition delta     — how a position scores vs an opponent
  • gamma_p50 / weibull_p50 — distribution medians (Weibull is delta-adjusted)
  • gbm                  — HistGradientBoosting on rolling form + opp strength
  • proj                 — gbm, else gamma_p50, else season average
  • win %                — Gamma percentile distribution per starter, summed per
                           team, cross-joined 100×100 (per f_win_predictions)

Usage:
    DB_PATH=mock_fantasy.db python -m api.predict
"""
import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import gamma as scipy_gamma, weibull_min
from sklearn.ensemble import HistGradientBoostingRegressor

from api.competition import (
    generate_regular_fixtures, REGULAR_ROUNDS,
    get_league_teams, calculate_table, build_playoffs, playoff_fixtures,
)
from api.leagues import roster_model

DB_PATH = os.getenv('DB_PATH', 'mock_fantasy.db')

# Players-per-position group, used to normalise team-level scores to per-player
# when computing opposition deltas (mirrors the reference model).
POSITION_PLAYER_COUNTS = {'OBK': 3, 'LF': 2, 'MID': 2}
MIN_DIST_ROWS = 5
# NOTE: `opp_pos_strength` is deliberately NOT a feature. It is derived from the
# points a team actually conceded *in the round being predicted* (see _engineer),
# so during training it is contemporaneous with the label — a near-oracle signal.
# At prediction time that value cannot exist, and _prediction_features
# substitutes the opponent's most recent PAST round instead. The model therefore
# learned to lean on a feature that means something different when served.
# `opp_pos_last3` is the lagged form of the same idea and is safe.
GBM_FEATURES = ['avg_3', 'max_3', 'max_5', 'p75_3', 'vol_3',
                'opp_pos_last3', 'season_avg', 'season_max']


# ── Distribution helpers ─────────────────────────────────────────────────────

def _gamma_p50(scores):
    if len(scores) < MIN_DIST_ROWS:
        return float(np.mean(scores)) if scores else 0.0
    try:
        shape, loc, scale = scipy_gamma.fit(scores)
        return float(scipy_gamma.ppf(0.50, shape, loc, scale))
    except Exception:
        return float(np.mean(scores))


def _gamma_percentiles_100(scores):
    """100 percentile values (p0..p99) for the win-probability cross-join."""
    if len(scores) < 2:
        return None
    try:
        shape, loc, scale = scipy_gamma.fit(scores)
        return np.array([scipy_gamma.ppf(p / 100, shape, loc, scale) for p in range(100)])
    except Exception:
        return None


def _weibull_p50(scores, delta=0.0):
    if len(scores) < MIN_DIST_ROWS:
        return float(np.mean(scores)) + delta if scores else 0.0
    arr = np.array(scores, dtype=float)
    if np.std(arr) == 0:
        arr = arr + np.linspace(0, 0.01, len(arr))
    shift = float(arr.min()) - 0.001
    try:
        shape, _, scale = weibull_min.fit(arr - shift, floc=0)
    except Exception:
        return float(np.median(arr)) + delta
    base = float(weibull_min.ppf(0.50, shape, loc=0, scale=scale)) + shift
    return float(max(base + delta, float(arr.min())))


# ── Data loading (cumulative weekly_stats → per-round deltas) ────────────────

def _previous_season_prior(con, league_id, rounds=None):
    """{player_id: per-round points last season} — the cold-start prior.

    Without this every projection is 0.0 until several rounds are banked: a
    player with no weekly_stats rows has no mean, no distribution and no GBM
    features, so the Analysis page is useless for the opening weeks of a season
    — the weeks when managers most need help ranking players they can't yet
    judge on form.

    `previous_season` stores a season TOTAL (points_per_game is often null), so
    it is spread across the regular season to get a per-round figure.
    """
    n = rounds or REGULAR_ROUNDS
    out = {}
    try:
        rows = con.execute(
            'SELECT player_id, total_points, points_per_game FROM previous_season '
            'WHERE league_id = ?', (league_id,)).fetchall()
    except Exception:
        return out
    for r in rows:
        pid = r['player_id'] if isinstance(r, sqlite3.Row) else r[0]
        total = r['total_points'] if isinstance(r, sqlite3.Row) else r[1]
        ppg = r['points_per_game'] if isinstance(r, sqlite3.Row) else r[2]
        if ppg:
            out[pid] = float(ppg)
        elif total:
            out[pid] = float(total) / n
    return out


def _load_scores(con, league_id):
    df = pd.read_sql(
        'SELECT ws.player_id AS playerid, ws.round AS round_num, ws.total_points, '
        '       p.name AS playername, p.team, p.position '
        'FROM weekly_stats ws JOIN players p ON p.player_id = ws.player_id '
        'WHERE ws.league_id = ? ORDER BY ws.player_id, ws.round',
        con, params=(league_id,))
    if df.empty:
        # Keep the full column set so the cold-start path (no rounds played yet)
        # can filter and index this frame like any other instead of KeyError-ing.
        for col in ('prev', 'total', 'opposition', 'home'):
            df[col] = pd.Series(dtype='float64')
        return df
    df['prev'] = df.groupby('playerid')['total_points'].shift(1).fillna(0.0)
    df['total'] = (df['total_points'] - df['prev']).astype(float)

    fx = pd.read_sql('SELECT round AS round_num, home_team, away_team '
                     'FROM real_fixtures WHERE league_id = ?', con, params=(league_id,))
    opp = {}
    for _, r in fx.iterrows():
        opp[(r['round_num'], r['home_team'])] = (r['away_team'], 1)
        opp[(r['round_num'], r['away_team'])] = (r['home_team'], 0)
    df['opposition'] = [opp.get((rd, tm), (None, None))[0] for rd, tm in zip(df['round_num'], df['team'])]
    df['home'] = [opp.get((rd, tm), (None, None))[1] for rd, tm in zip(df['round_num'], df['team'])]
    return df


def _fr_scores(con, league_id):
    """Per-round score series for each club's front-row UNIT = the sum of the
    club's PR/HK per-round deltas (matchday players only when a lineup exists)."""
    df = pd.read_sql(
        "SELECT ws.player_id, ws.round AS round_num, ws.total_points, p.team, p.name "
        "FROM weekly_stats ws JOIN players p ON p.player_id = ws.player_id "
        "WHERE ws.league_id = ? AND p.position IN ('PR','HK') "
        "ORDER BY p.team, ws.player_id, ws.round", con, params=(league_id,))
    if df.empty:
        return df
    df['prev'] = df.groupby('player_id')['total_points'].shift(1).fillna(0.0)
    df['total'] = (df['total_points'] - df['prev']).astype(float)
    return (df.groupby(['team', 'round_num'])['total'].sum().reset_index())


def _fr_score_frame(con, league_id, fr_series):
    """Reshape the FR-unit per-round series into the same column layout as
    `_load_scores`, so each club's front row can be fed through the GBM feature
    pipeline as if it were a single (synthetic) player."""
    if fr_series.empty:
        return fr_series
    df = fr_series.copy()
    df['playerid'] = 'FR:' + df['team'].astype(str)
    df['position'] = 'FR'
    fx = pd.read_sql('SELECT round AS round_num, home_team, away_team '
                     'FROM real_fixtures WHERE league_id = ?', con, params=(league_id,))
    opp = {}
    for _, r in fx.iterrows():
        opp[(r['round_num'], r['home_team'])] = (r['away_team'], 1)
        opp[(r['round_num'], r['away_team'])] = (r['home_team'], 0)
    df['opposition'] = [opp.get((rd, tm), (None, None))[0] for rd, tm in zip(df['round_num'], df['team'])]
    df['home'] = [opp.get((rd, tm), (None, None))[1] for rd, tm in zip(df['round_num'], df['team'])]
    return df


def _owner_map(con, league_id, rnd):
    """player_id -> fantasy team that owns it (squad as of round <= rnd)."""
    rows = con.execute(
        'WITH tr AS (SELECT team_name, MAX(round) r FROM team_selections '
        '            WHERE league_id=? AND round<=? GROUP BY team_name) '
        'SELECT ts.player_id, MIN(ts.team_name) FROM team_selections ts JOIN tr '
        '  ON ts.team_name=tr.team_name AND ts.round=tr.r '
        'WHERE ts.league_id=? GROUP BY ts.player_id', (league_id, rnd, league_id)).fetchall()
    return {r[0]: r[1] for r in rows}


def _fr_owner_map(con, league_id, rnd):
    """club -> fantasy team that owns its FR unit (as of round <= rnd)."""
    rows = con.execute(
        'WITH tr AS (SELECT team_name, MAX(round) r FROM team_front_row '
        '            WHERE league_id=? AND round<=? GROUP BY team_name) '
        'SELECT tfr.club, tfr.team_name FROM team_front_row tfr JOIN tr '
        '  ON tfr.team_name=tr.team_name AND tfr.round=tr.r WHERE tfr.league_id=?',
        (league_id, rnd, league_id)).fetchall()
    return {r[0]: r[1] for r in rows}


def _lineup_map(con, league_id, rnd):
    """(name_no_apostrophe, real_team) -> 'S'|'B' for the round's real lineups."""
    rows = con.execute('SELECT player_name, real_team, is_bench FROM match_lineups '
                       'WHERE league_id=? AND round=?', (league_id, rnd)).fetchall()
    out, teams = {}, set()
    for name, team, bench in rows:
        teams.add(team)
        out[((name or '').replace("'", ''), team)] = 'B' if bench else 'S'
    return out, teams


def _opp_deltas(scores_df):
    """{(opposition, position): delta} — how a position scores vs an opponent
    relative to that team's per-position season average."""
    df = scores_df.dropna(subset=['opposition'])
    game = (df.groupby(['round_num', 'team', 'opposition', 'position'])['total']
              .sum().reset_index(name='game_total'))
    avg = (game.groupby(['team', 'position'])['game_total'].mean()
              .reset_index(name='team_avg'))
    m = game.merge(avg, on=['team', 'position'])
    m['n'] = m['position'].map(POSITION_PLAYER_COUNTS).fillna(1)
    m['delta'] = (m['game_total'] - m['team_avg']) / m['n']
    return m.groupby(['opposition', 'position'])['delta'].mean().to_dict()


# ── GBM features ─────────────────────────────────────────────────────────────

def _engineer(scores_df):
    df = scores_df.sort_values(['playerid', 'round_num']).copy()
    g = df.groupby('playerid')['total']
    df['avg_3'] = g.transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean())
    df['avg_5'] = g.transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
    df['std_5'] = g.transform(lambda x: x.shift(1).rolling(5, min_periods=1).std())
    df['max_3'] = g.transform(lambda x: x.shift(1).rolling(3, min_periods=1).max())
    df['max_5'] = g.transform(lambda x: x.shift(1).rolling(5, min_periods=1).max())
    df['p75_3'] = g.transform(lambda x: x.shift(1).rolling(3, min_periods=1).quantile(0.75))
    df['vol_3'] = (df['std_5'] / df['avg_3'].replace(0, np.nan)).fillna(0)
    df['season_avg'] = g.transform(lambda x: x.shift(1).expanding().mean())
    df['season_max'] = g.transform(lambda x: x.shift(1).expanding().max())
    # opposition strength: per-position scoring above the team's running average
    og = (scores_df.dropna(subset=['opposition'])
          .groupby(['round_num', 'team', 'opposition', 'position'])['total']
          .sum().reset_index(name='gt'))
    og = og.sort_values(['team', 'position', 'round_num'])
    og['cum'] = og.groupby(['team', 'position'])['gt'].transform(lambda x: x.shift(1).expanding().mean())
    og['n'] = og['position'].map(POSITION_PLAYER_COUNTS).fillna(1)
    og['ostr'] = (og['gt'] - og['cum'].fillna(0)) / og['n']
    opp = (og.groupby(['round_num', 'opposition', 'position'])['ostr'].mean()
             .reset_index(name='opp_pos_strength'))
    opp = opp.sort_values(['opposition', 'position', 'round_num'])
    opp['opp_pos_last3'] = (opp.groupby(['opposition', 'position'])['opp_pos_strength']
                            .transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean()))
    df = df.merge(opp, on=['round_num', 'opposition', 'position'], how='left')
    return df, opp


def _train_gbm(feat, target_round):
    train = feat[feat['round_num'] < target_round].dropna(subset=GBM_FEATURES + ['total'])
    if len(train) < 30:
        return None
    model = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05,
                                          max_iter=150, random_state=42)
    model.fit(train[GBM_FEATURES].fillna(0), train['total'])
    return model


# ── Per-league computation ───────────────────────────────────────────────────

def _league_model(con, league_id):
    """The roster model for a league (keyed off its slug)."""
    row = con.execute('SELECT slug FROM leagues WHERE league_id = ?', (league_id,)).fetchone()
    slug = (row['slug'] if isinstance(row, dict) else row[0]) if row else None
    return roster_model(slug) if slug else {}


def _award_bonus(con, league_id):
    """True when standings award bonus points (OFDS) — mirrors the app so the
    playoff bracket we seed matches the live competition table."""
    return bool(_league_model(con, league_id).get('bonus', True))


def _has_fr_unit(con, league_id):
    """True only for leagues that field a club front-row UNIT (meatyboys).
    OFDS uses individual players, so it gets no FR pseudo-players."""
    return bool(_league_model(con, league_id).get('fr_unit'))


def _target_round(con, league_id, scores):
    """The round to project: the one managers are currently picking for.

    That is `get_next_round`, which holds the current round until the Tuesday
    rollover — so during a fixture weekend it is the live round (projections
    sit next to actual scores), and from Tuesday noon it is the upcoming one.

    It used to be MAX(weekly_stats.round), i.e. the last round *scored*. That is
    the same round for most of the week, but wrong for the entire Tue->Fri
    window when squads are open — exactly when projections are needed, the page
    was showing the round that had already finished.

    Imported lazily: api.index owns the rollover arithmetic and importing it at
    module scope would pull Flask into every use of this module.
    """
    try:
        from api.index import get_next_round
        return int(get_next_round(con, league_id))
    except Exception:
        # Standalone fallback (no rounds table / import trouble): last scored round.
        return int(scores['round_num'].max())


def compute_league(con, league_id, target=None):
    scores = _load_scores(con, league_id)
    if target is None:
        if scores.empty:
            # Nothing scored yet: there is no "last scored round" to fall back
            # on, so the calendar has to say which round we're picking for.
            try:
                from api.index import get_next_round
                target = int(get_next_round(con, league_id))
            except Exception:
                return None, [], []
        else:
            target = _target_round(con, league_id, scores)
    target = int(target)
    award_bonus = _award_bonus(con, league_id)

    hist = scores[scores['round_num'] < target]      # clean pre-round history
    actual = scores[scores['round_num'] == target].set_index('playerid')['total'].to_dict()
    owners = _owner_map(con, league_id, target)
    lineup, teams_named = _lineup_map(con, league_id, target)
    deltas = _opp_deltas(hist) if not hist.empty else {}
    feat, _ = _engineer(hist) if not hist.empty else (pd.DataFrame(), None)
    gbm = _train_gbm(feat, target) if not feat.empty else None

    # target-round opponent per real team
    fxrows = con.execute('SELECT home_team, away_team FROM real_fixtures WHERE league_id=? AND round=?',
                         (league_id, target)).fetchall()
    opp_at = {}
    for h, a in fxrows:
        opp_at[h] = (a, 1)
        opp_at[a] = (h, 0)

    players = pd.read_sql('SELECT player_id AS playerid, name, team, position '
                          'FROM players WHERE league_id=?', con, params=(league_id,))
    # Last season's per-round scoring, used only where a player has no history
    # in THIS season yet (opening rounds, or a new signing).
    prior_ppr = _previous_season_prior(con, league_id)
    # gamma percentile arrays per player, for the win-probability model
    pct_cache = {}
    rows = []
    for _, p in players.iterrows():
        pid = p['playerid']
        ph = hist[hist['playerid'] == pid]['total'].tolist()
        opp = opp_at.get(p['team'], (None, None))
        if opp[0] is None:
            continue
        d = deltas.get((opp[0], p['position']), 0.0)
        prior = prior_ppr.get(pid)
        ssn = float(np.mean(ph)) if ph else (prior or 0.0)
        a3 = float(np.mean(ph[-3:])) if ph else (prior or 0.0)
        gp50 = _gamma_p50(ph)
        wp50 = _weibull_p50(ph, d)
        gbm_pred = None
        if gbm is not None:
            fr = _prediction_features(pid, p['team'], opp[0], p['position'], hist, feat)
            if fr is not None:
                gbm_pred = round(float(gbm.predict(pd.DataFrame([fr])[GBM_FEATURES].fillna(0))[0]), 1)
        # Fallback chain: model -> distribution -> season mean -> last season.
        if gbm_pred is not None:
            proj = gbm_pred
        elif ph:
            proj = round(gp50, 1)
        elif prior is not None:
            proj = round(prior, 1)
        else:
            proj = round(ssn, 1)
        nm = (p['name'] or '').replace("'", '')
        status = lineup.get((nm, p['team']))
        if status is None and p['team'] in teams_named:
            status = 'O'
        pct_cache[pid] = _gamma_percentiles_100(ph)
        rows.append({
            'league_id': league_id, 'round': target, 'player_id': int(pid), 'is_fr': 0,
            'name': p['name'], 'position': p['position'], 'real_team': p['team'],
            'fantasy_team': owners.get(pid), 'opponent': opp[0], 'home': opp[1],
            'lineup': status, 'score': round(actual[pid], 1) if pid in actual else None,
            'proj': proj, 'gbm': gbm_pred, 'avg3': round(a3, 1), 'ssn_avg': round(ssn, 1),
            'gamma_p50': round(gp50, 1), 'weibull_p50': round(wp50, 1),
        })

    # Front-row UNITs (meatyboys only) as pseudo-players. The FR unit is treated
    # as an individual, so it gets its own GBM trained across all clubs' FR series.
    # OFDS uses individual players, so it has no FR unit in the analysis.
    fr_pct = {}
    fr_series = _fr_scores(con, league_id) if _has_fr_unit(con, league_id) else pd.DataFrame()
    if not fr_series.empty:
        fr_owner = _fr_owner_map(con, league_id, target)
        fr_full = _fr_score_frame(con, league_id, fr_series)
        fr_hist = fr_full[fr_full['round_num'] < target]
        feat_fr, _ = _engineer(fr_hist) if not fr_hist.empty else (pd.DataFrame(), None)
        gbm_fr = _train_gbm(feat_fr, target) if not feat_fr.empty else None
        for club in fr_series['team'].unique():
            ser = fr_series[fr_series['team'] == club]
            ph = ser[ser['round_num'] < target]['total'].tolist()
            opp = opp_at.get(club, (None, None))
            if opp[0] is None:
                continue
            cur = ser[ser['round_num'] == target]['total']
            ssn = float(np.mean(ph)) if ph else 0.0
            a3 = float(np.mean(ph[-3:])) if ph else 0.0
            gp50 = _gamma_p50(ph)
            fr_pct[club] = _gamma_percentiles_100(ph)
            gbm_pred = None
            if gbm_fr is not None:
                frf = _prediction_features('FR:' + str(club), club, opp[0], 'FR', fr_hist, feat_fr)
                if frf is not None:
                    gbm_pred = round(float(gbm_fr.predict(pd.DataFrame([frf])[GBM_FEATURES].fillna(0))[0]), 1)
            proj = gbm_pred if gbm_pred is not None else (round(gp50, 1) if ph else round(ssn, 1))
            rows.append({
                'league_id': league_id, 'round': target, 'player_id': None, 'is_fr': 1,
                'name': f'{club} Front Row', 'position': 'FR', 'real_team': club,
                'fantasy_team': fr_owner.get(club), 'opponent': opp[0], 'home': opp[1],
                'lineup': None, 'score': round(float(cur.iloc[0]), 1) if len(cur) else None,
                'proj': proj, 'gbm': gbm_pred,
                'avg3': round(a3, 1), 'ssn_avg': round(ssn, 1),
                'gamma_p50': round(gp50, 1), 'weibull_p50': round(gp50, 1),
            })

    # Win probabilities need real distributions. With no rounds played every
    # player's array is flat, so every fixture would come out a 100% draw (or a
    # 100/0 split off the priors) — confidently wrong. Publish nothing instead;
    # the page already says "No matchups available yet."
    matchups = (_win_probabilities(con, league_id, target, pct_cache, fr_pct,
                                   hist, fr_series, award_bonus)
                if not hist.empty else [])
    return target, rows, matchups


def _prediction_features(pid, team, opposition, position, hist, feat):
    h = hist[hist['playerid'] == pid].sort_values('round_num')['total']
    if h.empty:
        return None
    last3, last5 = h.tail(3), h.tail(5)
    std5 = last5.std() if len(last5) >= 2 else 0.0
    avg3 = last3.mean() if len(last3) else 0.0
    orow = feat[(feat['opposition'] == opposition) & (feat['position'] == position)] \
        .sort_values('round_num').tail(1) if not feat.empty else feat
    ostr = float(orow['opp_pos_strength'].iloc[0]) if len(orow) and not pd.isna(orow['opp_pos_strength'].iloc[0]) else 0.0
    olast3 = float(orow['opp_pos_last3'].iloc[0]) if len(orow) and not pd.isna(orow['opp_pos_last3'].iloc[0]) else 0.0
    return {
        'avg_3': avg3, 'max_3': last3.max() if len(last3) else 0.0,
        'max_5': last5.max() if len(last5) else 0.0,
        'p75_3': last3.quantile(0.75) if len(last3) else 0.0,
        'vol_3': (std5 / avg3) if avg3 else 0.0,
        'opp_pos_strength': ostr, 'opp_pos_last3': olast3,
        'season_avg': h.mean(), 'season_max': h.max(),
    }


def _win_probabilities(con, league_id, target, pct_cache, fr_pct, hist, fr_series, award_bonus=True):
    """Per fantasy matchup: sum each starter's Gamma percentile array, cross-join
    100×100 → win %. Starters = team_selections (is_bench=0) at round <= target.

    Regular rounds (<= REGULAR_ROUNDS) use the generated schedule; playoff rounds
    derive their fixtures from the bracket seeded off the standings — matching how
    the competition endpoint builds the live fixtures list."""
    teams = get_league_teams(con, league_id)
    regular = generate_regular_fixtures(teams)
    if target <= REGULAR_ROUNDS:
        source = regular
    else:
        table = calculate_table(regular, con, min(target, REGULAR_ROUNDS), award_bonus)
        source = playoff_fixtures(build_playoffs(con, table, target))
    fixtures = [(h, a) for wk, h, _, a, _ in source
                if wk == target and h != 'Bye' and a != 'Bye']

    def team_dist(team):
        rnd = con.execute('SELECT MAX(round) FROM team_selections WHERE league_id=? AND team_name=? AND round<=?',
                          (league_id, team, target)).fetchone()[0]
        if rnd is None:
            return None
        starters = [r[0] for r in con.execute(
            'SELECT player_id FROM team_selections WHERE league_id=? AND team_name=? AND round=? AND is_bench=0',
            (league_id, team, rnd)).fetchall()]
        dist = np.zeros(100)
        n = 0
        for pid in starters:
            pcts = pct_cache.get(pid)
            if pcts is not None:
                dist += pcts; n += 1
            else:
                ph = hist[hist['playerid'] == pid]['total'].tolist()
                if ph:
                    dist += np.full(100, float(np.mean(ph))); n += 1
        # add the team's FR unit if it owns one and it's a starter
        club = con.execute('SELECT club FROM team_front_row WHERE league_id=? AND team_name=? AND is_bench=0 '
                           'AND round=(SELECT MAX(round) FROM team_front_row WHERE league_id=? AND team_name=? AND round<=?)',
                           (league_id, team, league_id, team, target)).fetchone()
        if club and fr_pct.get(club[0]) is not None:
            dist += fr_pct[club[0]]; n += 1
        return dist if n else None

    out = []
    for home, away in fixtures:
        da, db_ = team_dist(home), team_dist(away)
        if da is None or db_ is None:
            continue
        diff = da[:, None] - db_[None, :]
        total = diff.size
        out.append({
            'league_id': league_id, 'round': target, 'home_team': home, 'away_team': away,
            'home_prob': round(int((diff > 0).sum()) / total * 100, 1),
            'away_prob': round(int((diff < 0).sum()) / total * 100, 1),
            'draw_prob': round(int((diff == 0).sum()) / total * 100, 1),
        })
    return out


# ── Persist ──────────────────────────────────────────────────────────────────

def _write(con, league_id, target, players, matchups):
    con.execute('DELETE FROM player_predictions WHERE league_id=? AND round=?', (league_id, target))
    con.execute('DELETE FROM matchup_predictions WHERE league_id=? AND round=?', (league_id, target))
    cols = ['league_id', 'round', 'player_id', 'is_fr', 'name', 'position', 'real_team',
            'fantasy_team', 'opponent', 'home', 'lineup', 'score', 'proj', 'gbm',
            'avg3', 'ssn_avg', 'gamma_p50', 'weibull_p50']
    con.executemany(
        f"INSERT INTO player_predictions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [[r.get(c) for c in cols] for r in players])
    mcols = ['league_id', 'round', 'home_team', 'away_team', 'home_prob', 'away_prob', 'draw_prob']
    con.executemany(
        f"INSERT INTO matchup_predictions ({','.join(mcols)}) VALUES ({','.join('?' * len(mcols))})",
        [[m.get(c) for c in mcols] for m in matchups])
    con.commit()


def _log_run(con, league_id, round_number, status, detail):
    """Mirror the cron scheduler's job_runs logging, so a run launched in the
    background is still visible next to the other ingestion jobs."""
    try:
        con.execute(
            'INSERT INTO job_runs (league_id, job, round_number, status, detail, run_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (league_id, 'predict', round_number, status, detail[:200],
             datetime.now(timezone.utc).isoformat()))
        con.commit()
    except Exception as e:                       # logging must never fail the job
        print(f'  (job_runs) could not log: {e}')


def main(argv=None):
    import argparse
    from api.db import ensure_schema

    ap = argparse.ArgumentParser(description='Compute analysis predictions.')
    ap.add_argument('--league', type=int, default=None,
                    help='league_id to compute (default: all)')
    ap.add_argument('--round', type=int, default=None,
                    help='target round (default: the round being picked for)')
    args = ap.parse_args(argv)

    # The web app writes to this file too; wait rather than failing on a lock.
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    leagues = ([args.league] if args.league is not None else
               [r[0] for r in con.execute(
                   'SELECT league_id FROM leagues ORDER BY league_id').fetchall()])
    failures = 0
    for lid in leagues:
        try:
            target, prows, mrows = compute_league(con, lid, args.round)
        except Exception as e:
            failures += 1
            print(f'league {lid}: FAILED - {e}')
            _log_run(con, lid, args.round, 'error', str(e))
            continue
        if target is None:
            print(f'league {lid}: no scores and no calendar — skipped')
            _log_run(con, lid, None, 'ok', 'skipped (no data)')
            continue
        _write(con, lid, target, prows, mrows)
        detail = f'{len(prows)} player rows, {len(mrows)} matchups'
        print(f'league {lid}: round {target} - {detail}')
        _log_run(con, lid, target, 'ok', detail)
    con.close()
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
