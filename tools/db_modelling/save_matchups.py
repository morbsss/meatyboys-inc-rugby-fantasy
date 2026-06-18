"""
save_matchups.py
----------------
One-time setup: loads ref_matchups_{SEASON}.csv into the fantasy_matchups table.

Usage:
    cd db_modelling
    python save_matchups.py          # loads current season
    python save_matchups.py 2026     # explicit season

The CSV columns expected:
    gameid, Round, Team A, Team B, Team A id, Team B id,
    Start, End, Team A bonus, Team B bonus
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

import params


def load(season=None):
    season = season or params.CURRENT_SEASON
    csv_path = Path(__file__).parent.parent / 'reference' / 'data' / f'ref_matchups_{season}.csv'

    if not csv_path.exists():
        print(f'CSV not found: {csv_path}')
        return

    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()

    out = pd.DataFrame({
        'gameid':       df['gameid'],
        'round_num':    df['Round'],
        'season':       season,
        'team_a':       df['Team A'],
        'team_b':       df['Team B'],
        'team_a_id':    df['Team A id'],
        'team_b_id':    df['Team B id'],
        'start_date':   pd.to_datetime(df['Start'], dayfirst=True).dt.strftime('%Y-%m-%d'),
        'end_date':     pd.to_datetime(df['End'], dayfirst=True).dt.strftime('%Y-%m-%d'),
        'team_a_bonus': df['Team A bonus'].fillna(0),
        'team_b_bonus': df['Team B bonus'].fillna(0),
    })

    # Drop rows where both team IDs are blank (unfinalised finals slots)
    out = out.dropna(subset=['team_a_id', 'team_b_id'])
    out = out[out['team_a_id'].str.strip() != '']

    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('DELETE FROM fantasy_matchups WHERE season = ?', (season,))
    out.to_sql('fantasy_matchups', con, if_exists='append', index=False)
    con.commit()
    con.close()

    print(f'fantasy_matchups: {len(out)} rows loaded for season {season}')


if __name__ == '__main__':
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    load(season_arg)
