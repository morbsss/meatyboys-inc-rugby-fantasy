"""
One-time migration: reads detailed_scores, player_summary, ref_players, and
ref_fixtures from each historical fant22–fant26.db and loads them into analytics.db.

Safe to re-run — existing rows for each season_year are replaced before inserting.

Usage:
    cd db_modelling
    python migrate_historical.py
"""
import sqlite3
from pathlib import Path

import pandas as pd

import params

SOURCE_DBS = {
    2022: 'fant22.db',
    2023: 'fant23.db',
    2024: 'fant24.db',
    2025: 'fant25.db',
    2026: 'fant26.db',
}

REF_DATA_DIR = Path(__file__).parent.parent / 'reference' / 'data'

SCORE_COL_RENAMES = {
    'GAMEWEEK':             'gameweek',
    'TOTAL':                'total',
    'MINS':                 'mins',
    'TRIES':                'tries',
    'TRY ASSISTS':          'try_assists',
    'METRES GAINED':        'metres_gained',
    'CLEAN BREAKS':         'clean_breaks',
    'DEFENDERS BEATEN':     'defenders_beaten',
    'OFFLOADS':             'offloads',
    'TURNOVERS WON':        'turnovers_won',
    'TURNOVERS CONCEDED':   'turnovers_conceded',
    'PENALTIES CONCEDED':   'penalties_conceded',
    'TACKLES MADE':         'tackles_made',
    'TACKLES MISSED':       'tackles_missed',
    'LINEOUT STEALS':       'lineout_steals',
    'PENALTIES KICKED':     'penalties_kicked',
    'CONVERSIONS KICKED':   'conversions_kicked',
    'DROP GOALS':           'drop_goals',
    'YELLOW CARDS':         'yellow_cards',
    'RED CARDS':            'red_cards',
    'SCRUMS WON PENALTY':   'scrums_won_penalty',
}

SCORE_DROP_COLS = {'level_0', 'index', 'week', 'page', 'score'}

SCORE_FINAL_COLS = [
    'playerid', 'season', 'season_year', 'playername', 'team', 'position', 'owner',
    'round_num', 'gameweek', 'mins', 'total',
    'tries', 'try_assists', 'metres_gained', 'clean_breaks', 'defenders_beaten',
    'offloads', 'turnovers_won', 'turnovers_conceded', 'penalties_conceded',
    'tackles_made', 'tackles_missed', 'lineout_steals',
    'penalties_kicked', 'conversions_kicked', 'drop_goals',
    'yellow_cards', 'red_cards', 'scrums_won_penalty',
    'opposition', 'news',
]

SUMMARY_COL_RENAMES = {
    'TOTAL mean':       'total_mean',
    'TOTAL sum':        'total_sum',
    'TOTAL count':      'total_count',
    '3_game_avg':       'game3_avg',
    '3_game_avg_rank':  'game3_avg_rank',
    '5_game_avg':       'game5_avg',
    '5_game_avg_rank':  'game5_avg_rank',
}

SUMMARY_FINAL_COLS = [
    'playerid', 'position', 'playername', 'team', 'owner', 'news',
    'total_mean', 'total_sum', 'total_count',
    'avg_rank', 'game3_avg', 'game3_avg_rank', 'game5_avg', 'game5_avg_rank',
    'season_year',
]


def migrate_scores(src_con, dst_con, year):
    df = pd.read_sql('SELECT * FROM detailed_scores', src_con)
    df = df.rename(columns=SCORE_COL_RENAMES)
    df = df.drop(columns=[c for c in SCORE_DROP_COLS if c in df.columns])
    df['season_year'] = year
    df = df[[c for c in SCORE_FINAL_COLS if c in df.columns]]

    dst_con.execute('DELETE FROM detailed_scores WHERE season_year = ?', (year,))
    df.to_sql('detailed_scores', dst_con, if_exists='append', index=False)
    print(f'  detailed_scores: {len(df)} rows')


def migrate_summary(src_con, dst_con, year):
    df = pd.read_sql('SELECT * FROM player_summary', src_con)
    df = df.rename(columns=SUMMARY_COL_RENAMES)
    df = df.drop(columns=[c for c in ('index', 'level_0') if c in df.columns])
    df['season_year'] = year
    df = df[[c for c in SUMMARY_FINAL_COLS if c in df.columns]]

    dst_con.execute('DELETE FROM player_summary WHERE season_year = ?', (year,))
    df.to_sql('player_summary', dst_con, if_exists='append', index=False)
    print(f'  player_summary: {len(df)} rows')


def migrate_players(src_con, dst_con, year):
    table = f'ref_{year}_players'
    try:
        df = pd.read_sql(f'SELECT * FROM "{table}"', src_con)
    except Exception:
        print(f'  ref_players: {table} not found, skipping')
        return

    df = df.drop(columns=[c for c in ('index', 'level_0') if c in df.columns])

    existing_ids = pd.read_sql('SELECT playerid FROM ref_players', dst_con)['playerid']
    new = df[~df['playerid'].isin(existing_ids)].copy()

    if new.empty:
        print(f'  ref_players: no new players from {year}')
        return

    keep = [c for c in ('playerid', 'playername', 'team', 'position', 'date_added') if c in new.columns]
    new = new[keep]
    if 'date_added' not in new.columns:
        new['date_added'] = f'{year}-01-01'

    new.to_sql('ref_players', dst_con, if_exists='append', index=False)
    print(f'  ref_players: {len(new)} new players added')


def migrate_fixtures(src_con, dst_con, year):
    table = f'ref_{year}_fixtures'
    try:
        df = pd.read_sql(f'SELECT * FROM "{table}"', src_con)
    except Exception:
        print(f'  ref_fixtures: {table} not found, skipping')
        return

    df = df.drop(columns=[c for c in ('index', 'level_0', 'Round', 'comp', 'status', 'venue', 'datetime') if c in df.columns])
    df = df.rename(columns={'home/away': 'home_away', 'date': 'match_date'})

    for col in ('team', 'opposition'):
        if col in df.columns:
            df[col] = df[col].replace(params.TEAM_NAME_MAP).str.strip()

    df['season'] = year

    if 'match_date' in df.columns:
        df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce').dt.strftime('%Y-%m-%d')

    final_cols = ['round_num', 'team', 'opposition', 'home_away', 'match_date', 'season']
    df = df[[c for c in final_cols if c in df.columns]]

    dst_con.execute('DELETE FROM ref_fixtures WHERE season = ?', (year,))
    df.to_sql('ref_fixtures', dst_con, if_exists='append', index=False)
    print(f'  ref_fixtures: {len(df)} rows')


def main():
    import db_init
    db_init.init_db()

    dst_con = sqlite3.connect(params.DB_PATH)
    dst_con.execute('PRAGMA journal_mode=WAL')

    for year, fname in SOURCE_DBS.items():
        src_path = REF_DATA_DIR / fname
        if not src_path.exists():
            print(f'\nSkipping {fname} — not found')
            continue

        print(f'\n--- Migrating {fname} (season {year}) ---')
        src_con = sqlite3.connect(src_path)

        migrate_scores(src_con, dst_con, year)
        migrate_summary(src_con, dst_con, year)
        migrate_players(src_con, dst_con, year)
        migrate_fixtures(src_con, dst_con, year)

        dst_con.commit()
        src_con.close()

    dst_con.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()
