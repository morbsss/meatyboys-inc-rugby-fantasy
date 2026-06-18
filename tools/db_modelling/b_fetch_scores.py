"""
Step B: Fetch game-by-game scores for every player in player_list.

Reads:  player_list, ref_fixtures
Writes: detailed_scores_staging  (always replaced — current season only)

Then merge_to_historical() safely moves staging data into detailed_scores,
deleting only the current season_year rows and leaving all prior years intact.
"""
import datetime as dt
import json
import sqlite3

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup as bs

import params

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

FINAL_COLS = [
    'playerid', 'season', 'season_year', 'playername', 'team', 'position', 'owner',
    'round_num', 'gameweek', 'mins', 'total',
    'tries', 'try_assists', 'metres_gained', 'clean_breaks', 'defenders_beaten',
    'offloads', 'turnovers_won', 'turnovers_conceded', 'penalties_conceded',
    'tackles_made', 'tackles_missed', 'lineout_steals',
    'penalties_kicked', 'conversions_kicked', 'drop_goals',
    'yellow_cards', 'red_cards', 'scrums_won_penalty',
    'opposition', 'news',
]


def _get_session():
    s = requests.Session()
    cred = {
        'Data': (
            f"{{'tblogin': '{params.FRD_EMAIL}','tbpassword':'{params.FRD_PASSWORD}',"
            '"rememberme":"on","leagueid":"","code":"","timezoneoffset":"-660",'
            '"action":"user/login","type":"action"}'
        )
    }
    s.post(params.FRD_URL, json=cred)
    return s


def fetch_scores(session, con, season):
    """Fetch scores from FRD and write to detailed_scores_staging. Does not touch detailed_scores."""
    start = dt.datetime.now()

    player_list = pd.read_sql('SELECT playerid, playername, team, position, owner, news FROM player_list', con)
    player_ids = player_list['playerid'].unique()
    total = len(player_ids)
    log_every = max(total // 10, 1)

    print(f'Fetching scores for {total} players...')

    rows = []
    stat_headers = None

    for i, pid in enumerate(player_ids, 1):
        payload = {
            'Data': json.dumps({
                'playerid': pid,
                'leagueid': params.FRD_LEAGUE_ID,
                'action': 'member/common/playerstats',
                'type': 'control',
            })
        }
        resp = session.post(params.FRD_URL, json=payload)
        html = json.loads(resp.json()['d'])['Content']
        soup = bs(html, 'html.parser')

        season_tag = soup.find('h1')
        season_str = ''.join(season_tag.string.splitlines()) if season_tag else str(season)

        tbody = soup.find('tbody')
        thead = soup.find('thead')

        if thead is not None:
            candidate = ['playerid', 'season'] + [th.contents[0] for th in thead.find_all('th')]
            if stat_headers is None or len(candidate) > len(stat_headers):
                stat_headers = candidate

        if tbody:
            for row in tbody.find_all('tr'):
                cells = row.find_all('td')
                rows.append([pid, season_str] + [c.contents[0].strip() for c in cells])

        if i % log_every == 0:
            elapsed = (dt.datetime.now() - start).total_seconds()
            print(f'  {i}/{total} ({i * 100 // total}%) — {elapsed:.0f}s elapsed')

    if not rows or stat_headers is None:
        print('No score data retrieved')
        return

    # Pad rows for players with no games yet
    ncols = len(stat_headers)
    for row in rows:
        if len(row) < ncols:
            row.extend([None] * (ncols - len(row)))

    df = pd.DataFrame(rows, columns=stat_headers)
    df = df.rename(columns=SCORE_COL_RENAMES)
    df = df.merge(player_list, on='playerid', how='left')

    # Front Row players share a name in FRD — identify by team instead
    df['playername'] = np.where(
        df['position'] == 'Front Row',
        df['team'] + ' Front Row',
        df['playername']
    )

    # Clean scraped text fields
    for col in ('season', 'gameweek', 'total', 'mins'):
        if col in df.columns:
            df[col] = df[col].replace(r'\n', '', regex=True).str.strip()

    df['season_year'] = season
    df['round_num'] = df['gameweek'].str.extract(r'(\d+)').astype(float).astype('Int64')
    df['mins'] = pd.to_numeric(df['mins'], errors='coerce').fillna(0).astype(int)
    df['total'] = pd.to_numeric(df['total'], errors='coerce')

    # Join opposition via fixtures
    fixtures = pd.read_sql(
        f'SELECT round_num, team, opposition FROM {params.FIXTURE_TABLE} WHERE season = {season}',
        con
    )
    df = df.merge(fixtures, on=['team', 'round_num'], how='left')

    df = df[[c for c in FINAL_COLS if c in df.columns]]

    # Write to staging — always a full replace, never touches detailed_scores
    df.to_sql('detailed_scores_staging', con, if_exists='replace', index=False)
    con.commit()

    elapsed = (dt.datetime.now() - start).total_seconds()
    print(f'Staging loaded: {len(df)} rows for season {season} in {elapsed:.0f}s')


def merge_to_historical(con, season):
    """
    Safely merge detailed_scores_staging into detailed_scores.
    Only deletes rows for the current season_year — prior years are untouched.
    """
    staging_count = con.execute('SELECT COUNT(*) FROM detailed_scores_staging').fetchone()[0]
    if staging_count == 0:
        print('Staging is empty — skipping merge')
        return

    con.execute('DELETE FROM detailed_scores WHERE season_year = ?', (season,))
    con.execute('''
        INSERT INTO detailed_scores
        SELECT * FROM detailed_scores_staging
    ''')
    con.commit()

    historical_count = con.execute(
        'SELECT COUNT(*) FROM detailed_scores WHERE season_year = ?', (season,)
    ).fetchone()[0]
    print(f'Merged {historical_count} rows into detailed_scores for season {season}')


def main():
    print(f'Starting score fetch at {dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    season = params.CURRENT_SEASON
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    session = _get_session()
    fetch_scores(session, con, season)
    merge_to_historical(con, season)
    con.close()


if __name__ == '__main__':
    main()
