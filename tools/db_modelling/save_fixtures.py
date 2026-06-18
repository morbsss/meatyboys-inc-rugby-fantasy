"""
One-time (or per-season) script to load a fixtures CSV into ref_fixtures.

Usage:
    python save_fixtures.py [season]     # defaults to CURRENT_SEASON in params
    python save_fixtures.py 2025         # load a specific season's file

Expects: db_modelling/fixtures/fixtures_{season}.csv
"""
import sqlite3
import sys

import pandas as pd

import params


def load_fixtures(filepath, season, con):
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['datetime'], format='%d/%m/%Y %H:%M')
    df['round'] = df['round'].astype(int)

    df.rename(columns={
        'Team 1':   'team',
        'Team 2':   'opposition',
        'round':    'round_num',
        'home/away':'home_away',
    }, inplace=True)

    for col in ('team', 'opposition'):
        df[col] = df[col].replace(params.TEAM_NAME_MAP).str.strip()

    df['match_date'] = df['date'].dt.strftime('%Y-%m-%d')
    df['kickoff'] = df['date'].dt.strftime('%Y-%m-%dT%H:%M')  # local (AEST) kickoff
    df['season'] = season

    home_rows = df[['round_num', 'team', 'opposition', 'home_away', 'match_date', 'kickoff', 'season']].copy()

    # Add away-team perspective so merges work for all players
    away_rows = home_rows.copy()
    away_rows['team'], away_rows['opposition'] = home_rows['opposition'], home_rows['team']
    away_rows['home_away'] = away_rows['home_away'].map({'home': 'away', 'away': 'home'})

    combined = pd.concat([home_rows, away_rows], ignore_index=True)
    # The CSV may already contain both home and away perspectives; drop duplicates
    # so we don't violate the (round_num, team, season) primary key.
    combined = combined.drop_duplicates(subset=['round_num', 'team', 'season'], keep='first')

    con.execute('DELETE FROM ref_fixtures WHERE season = ?', (season,))
    combined.to_sql(params.FIXTURE_TABLE, con, if_exists='append', index=False)
    con.commit()
    print(f'Loaded {len(combined)} fixture rows for season {season}')


def main():
    season = int(sys.argv[1]) if len(sys.argv) > 1 else params.CURRENT_SEASON
    fixture_file = params.FIXTURES_DIR / f'fixtures_{season}.csv'

    if not fixture_file.exists():
        print(f'No fixture file found at {fixture_file}')
        print(f'Add a fixtures_{season}.csv to db_modelling/fixtures/')
        return

    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    load_fixtures(fixture_file, season, con)
    con.close()


if __name__ == '__main__':
    main()
