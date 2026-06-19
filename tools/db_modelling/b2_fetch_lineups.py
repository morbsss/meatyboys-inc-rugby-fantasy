"""
b2_fetch_lineups.py
-------------------
Fetches the current selected lineup for every manager team from FRD
and writes to the manager_team table (full replace each run).

Lineup source: member/homepage/lineup FRD endpoint.
Players in positions 1–11 are 'Starting'; 12+ are 'Bench'.
"""
import json
import sqlite3
import datetime as dt
import logging

import requests
from bs4 import BeautifulSoup

import params

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)


def _login() -> requests.Session:
    s = requests.Session()
    cred = {'Data': (
        "{'tblogin':'" + params.FRD_EMAIL + "',"
        "'tbpassword':'" + params.FRD_PASSWORD + "',"
        '"rememberme":"on","leagueid":"","code":"",'
        '"timezoneoffset":"-660","action":"user/login","type":"action"}'
    )}
    resp = s.post(params.FRD_URL, json=cred)
    result = json.loads(resp.json()['d'])
    if not result.get('Success'):
        raise RuntimeError(f'FRD login failed: {result}')
    log.info('FRD login successful')
    return s


def _fetch_manager_lineup(session: requests.Session, manager: str, team_id: str) -> list[dict]:
    """Fetch and parse the lineup for one manager team. Returns list of player dicts."""
    payload = {'Data': json.dumps({
        'leagueid': params.FRD_LEAGUE_ID,
        'teamid':   team_id,
        'action':   'member/homepage/lineup',
        'type':     'control',
    })}
    resp = session.post(params.FRD_URL, json=payload)
    page = json.loads(resp.json()['d'])
    soup = BeautifulSoup(page['Content'], 'html.parser')

    tbodies = soup.find_all('tbody')
    if not tbodies:
        log.warning(f'  No tbody for {manager}')
        return []

    rows = tbodies[0].find_all('tr')
    fetched_at = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    players = []
    player_num = 0

    for row in rows:
        cells = row.find_all('td')
        if not cells:
            continue

        first_text = cells[0].get_text(strip=True)
        if first_text == 'BENCH':
            continue

        player_num += 1
        if player_num == 12:
            # Row 12 is a blank separator between starters and bench
            continue

        role = 'Starting' if player_num <= 11 else 'Bench'

        position_code = ''
        playerid      = ''
        playername    = ''
        news          = ''

        for j, cell in enumerate(cells):
            if j == 1:
                position_code = cell.get_text(strip=True)
            elif j == 2:
                div = cell.find('div')
                if div:
                    playerid   = (div.get('playerid')   or '').strip()
                    playername = (div.get('playername') or '').strip()
            elif j == 4:
                news = cell.get_text(strip=True)

        if not playerid:
            continue

        players.append({
            'manager':    manager,
            'playerid':   playerid,
            'playername': playername,
            'position':   position_code,
            'role':       role,
            'news':       news,
            'fetched_at': fetched_at,
        })

    return players


def run(con=None):
    close_after = con is None
    if con is None:
        con = sqlite3.connect(params.DB_PATH)
        con.execute('PRAGMA journal_mode=WAL')

    log.info('=== Fetching manager lineups ===')

    session = _login()
    all_players = []

    for manager, team_id in params.MANAGER_TEAMS.items():
        try:
            players = _fetch_manager_lineup(session, manager, team_id)
            log.info(f'  {manager}: {len(players)} players')
            all_players.extend(players)
        except Exception as e:
            log.error(f'  {manager}: failed — {e}')

    if not all_players:
        log.warning('No lineup data fetched')
        if close_after:
            con.close()
        return

    import pandas as pd
    df = pd.DataFrame(all_players)

    con.execute('DELETE FROM manager_team')
    df.to_sql('manager_team', con, if_exists='append', index=False)
    con.commit()

    log.info(f'manager_team: {len(df)} rows saved '
             f'({df["manager"].nunique()} managers)')

    if close_after:
        con.close()
    log.info('=== Lineup fetch complete ===')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    run(con)
    con.close()


if __name__ == '__main__':
    main()
