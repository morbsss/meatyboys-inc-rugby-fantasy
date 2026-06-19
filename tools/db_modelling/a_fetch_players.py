"""
Step A: Fetch player hub from FRD and update player reference + team news snapshots.

Writes to:
  player_list      - full current-season player hub (replaced each run)
  ref_players      - player reference, appends new players only
  player_team_news - team news snapshot per player per round (upserts current round)
"""
import datetime as dt
import json
import sqlite3

import pandas as pd
import requests
from bs4 import BeautifulSoup as bs

import params
from e_helpers import get_current_round


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


def _table_exists(con, name):
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def fetch_player_list(session, con):
    league_id = f'"{params.FRD_LEAGUE_ID}"'
    season_id = f'"{params.FRD_SEASON_ID}"'
    rows = []

    for page in range(params.PLAYER_HUB_PAGES):
        payload = {
            'Data': (
                f'{{"filter":"","leagueid":{league_id},'
                f'"gameweek":"255","category":"255","seasons":{season_id},'
                f'"owner":"256","position":256,"teamnews":"256","sort":"",'
                f'"pageno":"{page}","action":"member/league/playerhub","type":"control"}}'
            )
        }
        resp = session.post(params.FRD_URL, json=payload)
        page_data = json.loads(resp.json()['d'])
        soup = bs(page_data['Content'], 'html.parser')

        players = soup.find_all('tbody')[1].find_all('tr')
        if not players:
            break

        print(f'  page {page + 1}: {len(players)} players')

        for player in players:
            stats = player.find_all('td')
            row = []
            for j, stat in enumerate(stats):
                if j == 1:
                    div = stat.find('div')
                    row.append(div.get('playerid', '').strip())
                    pname = div.get('playername', '').strip()
                    # FRD sometimes doubles the name for Front Row group entries; halve only if so
                    if pname.count('Front Row') > 1:
                        pname = pname[:len(pname) // 2].strip()
                    row.append(pname)
                elif j in (0, 2, 3, 4, 5, 6):
                    row.append(stat.contents[0].strip())
            row.append(page + 1)
            rows.append(row)

    headers = ['position', 'playerid', 'playername', 'team', 'owner', 'opposition', 'score', 'news', 'page']
    df = pd.DataFrame(rows, columns=headers)
    if df.empty:
        # Transient FRD/empty response — do NOT replace the table or we'd wipe
        # good data and break downstream steps. Keep what we already have.
        print('WARNING: FRD returned 0 players — keeping existing player_list')
        return df
    df['fetched_at'] = dt.datetime.now().isoformat()
    before = len(df)
    df = df.drop_duplicates(subset=['playerid'])
    if len(df) < before:
        print(f'WARNING: dropped {before - len(df)} duplicate playerid rows from FRD response')
    df.to_sql('player_list', con, if_exists='replace', index=False)
    print(f'Saved {len(df)} players to player_list')
    return df


def update_player_ref(player_df, con):
    table = params.PLAYER_REF_TABLE
    if _table_exists(con, table):
        existing_ids = pd.read_sql(f'SELECT playerid FROM {table}', con)['playerid']
        new_players = player_df[~player_df['playerid'].isin(existing_ids)]
    else:
        new_players = player_df

    if new_players.empty:
        print('No new players found')
        return

    ref = new_players[['playerid', 'playername', 'team', 'position']].copy()
    ref['date_added'] = dt.datetime.now().isoformat()
    ref.to_sql(table, con, if_exists='append', index=False)
    print(f'Added {len(ref)} new players to {table}')


def update_team_news(player_df, con, season):
    round_num = get_current_round(con)
    table = params.TEAM_NEWS_TABLE

    news = player_df[['playerid', 'playername', 'news', 'owner']].copy()
    news['round_num'] = round_num
    news['season'] = season

    if _table_exists(con, table):
        existing = pd.read_sql(f'SELECT * FROM {table}', con)
        # Drop any existing rows for this round so we can replace with fresh data
        existing = existing[~((existing['round_num'] == round_num) & (existing['season'] == season))]
        combined = pd.concat([existing, news], ignore_index=True)
    else:
        combined = news

    combined.to_sql(table, con, if_exists='replace', index=False)
    print(f'Team news updated for season {season} round {round_num} ({len(news)} players)')


def main():
    start = dt.datetime.now()
    print(f'Starting player fetch at {start.strftime("%Y-%m-%d %H:%M:%S")}')

    season = params.CURRENT_SEASON
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')

    session = _get_session()
    player_df = fetch_player_list(session, con)
    if player_df.empty:
        print('Skipping ref/team-news updates (no players fetched)')
    else:
        update_player_ref(player_df, con)
        update_team_news(player_df, con, season)

    con.commit()
    con.close()
    print(f'Done in {(dt.datetime.now() - start).total_seconds():.0f}s')


if __name__ == '__main__':
    main()
