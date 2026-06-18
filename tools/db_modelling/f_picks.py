"""
f_picks.py
----------
Selects the best available players per position for the current round
from all_predictions and writes them to round_picks.

Eligibility:
  - Player has a fixture this round (already in all_predictions)
  - Player news is 'Starting' or 'Starting (c)' (if any news data exists)
  - Uses gbm_pred as the primary rank, falls back to simple_5g_pred

Writes one row per eligible player to round_picks with rank_in_pos.
"""
import sqlite3
import logging

import pandas as pd

import params
from e_helpers import get_current_round

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

STARTING_NEWS = {'starting', 'starting (c)'}


def run(con=None):
    close_after = con is None
    if con is None:
        con = sqlite3.connect(params.DB_PATH)
        con.execute('PRAGMA journal_mode=WAL')

    round_num = get_current_round(con)
    log.info(f'=== Round picks: round {round_num}, season {params.CURRENT_SEASON} ===')

    preds = pd.read_sql(f'''
        SELECT playerid, playername, team, position, owner, round_num, opposition,
               news, gbm_pred, simple_5g_pred, baseline_season_avg
        FROM all_predictions
        WHERE round_num = {round_num}
          AND season_year = {params.CURRENT_SEASON}
    ''', con)

    if preds.empty:
        log.warning('No predictions found for current round — run e_predictions.py first')
        if close_after:
            con.close()
        return

    log.info(f'  {len(preds):,} players in prediction pool')

    # Filter to starting players if news data is populated
    has_news = preds['news'].notna() & (preds['news'].str.strip() != '')
    starting = preds[has_news & preds['news'].str.lower().isin(STARTING_NEWS)]
    non_starting = preds[has_news & ~preds['news'].str.lower().isin(STARTING_NEWS)]
    no_news = preds[~has_news]

    log.info(f'  Starting: {len(starting)}, Non-starting: {len(non_starting)}, No news: {len(no_news)}')

    # Use starting players if any news exists; otherwise use all (pre-team-announcement)
    if len(starting) > 0:
        eligible = pd.concat([starting, no_news], ignore_index=True)
        log.info(f'  Eligible pool: {len(eligible)} (starting + no-news)')
    else:
        eligible = preds.copy()
        log.info(f'  No news yet — using full pool of {len(eligible)}')

    # Primary sort: gbm_pred, fallback to simple_5g_pred then simple_season_avg
    eligible['pred_score'] = (eligible['gbm_pred']
                              .fillna(eligible.get('simple_5g_pred', pd.Series(dtype=float)))
                              .fillna(eligible.get('baseline_season_avg', pd.Series(dtype=float)))
                              .fillna(0.0))

    # Rank within position
    eligible['rank_in_pos'] = (eligible.groupby('position')['pred_score']
                               .rank(ascending=False, method='min').astype(int))

    picks = eligible[[
        'playerid', 'playername', 'team', 'position', 'owner',
        'round_num', 'opposition', 'news', 'pred_score', 'rank_in_pos',
    ]].copy()

    con.execute(
        'DELETE FROM round_picks WHERE round_num = ?',
        (round_num,)
    )
    picks.to_sql('round_picks', con, if_exists='append', index=False)
    con.commit()

    log.info(f'Wrote {len(picks):,} rows to round_picks')

    for pos in sorted(picks['position'].unique()):
        top = (picks[picks['position'] == pos]
               .sort_values('rank_in_pos')
               .head(5)[['playername', 'team', 'opposition', 'pred_score', 'owner']])
        log.info(f'\n  Top 5 {pos}:\n{top.to_string(index=False)}')

    if close_after:
        con.close()
    log.info('=== Round picks complete ===')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    run(con)
    con.close()


if __name__ == '__main__':
    main()
