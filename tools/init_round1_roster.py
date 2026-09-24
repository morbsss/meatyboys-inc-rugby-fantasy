"""Initialise the Premiership (OFDS) roster for the new season from SuperBru.

Scrapes the live SuperBru player list (all 8 position pages), stores it as this
season's ROUND 1 snapshot (weekly_stats, all pre-season 0-point rows), and makes
the DRAFT board rank each player by their LAST SEASON total - matched BY NAME, so
returning players keep their points and newcomers start at 0.

  python tools/init_round1_roster.py fantasy_2026_27.db
  python tools/init_round1_roster.py fantasy_2026_27.db --tbl 2017 --dry-run

It (transactionally):
  1. Captures {name -> last-season total_points} from the existing previous_season.
  2. Scrapes pages 1..8 (pg = position) of f_write_player_stats.php.
  3. Wipes the league's players + per-season data, then inserts the fresh roster
     as players + weekly_stats round 1.
  4. Rebuilds previous_season for the new player_ids, total_points mapped by name
     (this is what the draft board's rank reads).

Leagues, users, seasons, entries and honours are untouched.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = 'https://www.superbru.com/premrugbyfantasy/ajax/f_write_player_stats.php'
POSITION_BY_PAGE = {1: 'PR', 2: 'HK', 3: 'LK', 4: 'LF', 5: 'SH', 6: 'FH', 7: 'MID', 8: 'OBK'}
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
}


def _to_float(val) -> float:
    try:
        return float(str(val).replace('£', '').replace('m', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _to_price(val) -> float:
    return _to_float(val) * 1_000_000


def scrape_roster(tbl: str) -> list[dict]:
    """One dict per player across all 8 position pages. Header-driven column map
    so a changed column set (e.g. a 'kicking' column) doesn't misalign fields."""
    players: list[dict] = []
    for page, position in POSITION_BY_PAGE.items():
        resp = requests.get(f'{BASE_URL}?pg={page}&tbl={tbl}',
                            headers=HEADERS, timeout=20, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        thead, tbody = soup.find('thead'), soup.find('tbody')
        if not tbody:
            print(f'  pg={page} ({position}): no table body')
            continue
        # Stat column labels come after Team + Player headers; row cells after the
        # name have an empty colspan filler cell, so stats begin at cell index 3.
        stat_labels = [th.get_text(strip=True) for th in thead.find_all('th')][2:] if thead else []
        n = 0
        for tr in tbody.find_all('tr'):
            cells = [td.get_text(strip=True) for td in tr.find_all('td')]
            if len(cells) < 4 or not cells[1]:
                continue
            stats = dict(zip(stat_labels, cells[3:3 + len(stat_labels)]))
            players.append({
                'team': cells[0],
                'name': cells[1],
                'position': position,
                'total_points': _to_float(stats.get('Points', 0)),
                'price': _to_price(stats.get('Price', 0)),
                'points_per_game': stats.get('Points per game', ''),
                'popularity': stats.get('Popularity', ''),
                'form': stats.get('Form', ''),
                'kicking': stats.get('Kicking', ''),
            })
            n += 1
        print(f'  pg={page} ({position}): {n} players')
    return players


def rebuild(db_path: str, tbl: str, dry_run: bool) -> None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    now = datetime.now(timezone.utc).isoformat()

    row = cur.execute("SELECT league_id FROM leagues WHERE slug = 'ofds'").fetchone()
    if not row:
        sys.exit('No ofds league in this DB.')
    lid = row['league_id']

    # 1) last-season totals, keyed by name (case-insensitive).
    last = {}
    for r in cur.execute(
        'SELECT p.name AS name, ps.total_points AS pts '
        'FROM previous_season ps JOIN players p ON p.player_id = ps.player_id '
        'WHERE ps.league_id = ?', (lid,)):
        if r['name']:
            last[r['name'].lower()] = r['pts'] or 0.0
    print(f'Captured {len(last)} last-season name→points entries.')

    print(f'Scraping SuperBru (tbl={tbl})...')
    roster = scrape_roster(tbl)
    print(f'Scraped {len(roster)} players total.')
    if len(roster) < 50:
        sys.exit('Refusing to proceed: scrape returned too few players (site issue?).')

    # SuperBru's abbreviated "Last,F" names can collide within a position; the
    # players table is UNIQUE(name, team, position), so keep one row per key.
    seen, deduped = set(), []
    for p in roster:
        key = (p['name'], p['team'], p['position'])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    if len(deduped) != len(roster):
        print(f'Dropped {len(roster) - len(deduped)} duplicate (name,team,position) rows.')
    roster = deduped

    matched = sum(1 for p in roster if p['name'].lower() in last)
    print(f'{matched}/{len(roster)} scraped players matched a last-season total by name.')

    if dry_run:
        print('\n[dry-run] no DB changes. Sample (name | pos | team | last-season pts):')
        for p in sorted(roster, key=lambda x: last.get(x['name'].lower(), 0), reverse=True)[:10]:
            print(f"  {p['name']:16} {p['position']:4} {p['team']:4} {last.get(p['name'].lower(), 0)}")
        con.close()
        return

    # 2) Replace the league's roster + per-season data (FK-safe order). Accounts,
    #    seasons, entries and honours are NOT touched.
    for t in ('weekly_stats', 'previous_season', 'team_selections', 'team_front_row',
              'match_lineups', 'draft_picks', 'real_fixtures', 'player_predictions',
              'matchup_predictions', 'trades'):
        try:
            cur.execute(f'DELETE FROM {t} WHERE league_id = ?', (lid,))
        except sqlite3.OperationalError:
            pass
    cur.execute('DELETE FROM players WHERE league_id = ?', (lid,))

    # Load the round-1 roster into players, and set each player's DRAFT rank to
    # their last-season total (matched by name). We deliberately do NOT write
    # weekly_stats rows: round-1 SCORES are all 0 until the games are played
    # (25 Sep), and empty round-1 rows would flip the app out of pre-season and
    # push the draft's target round to 2. The live cron fills weekly_stats when
    # round 1 is actually played.
    inserted = 0
    for p in roster:
        cur.execute('INSERT INTO players (name, team, position, league_id) VALUES (?, ?, ?, ?)',
                    (p['name'], p['team'], p['position'], lid))
        pid = cur.lastrowid
        cur.execute(
            'INSERT INTO previous_season (player_id, league_id, total_points, season, archived_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (pid, lid, last.get(p['name'].lower(), 0.0), 'last-season', now))
        inserted += 1

    con.commit()
    print(f'\nInserted {inserted} players (round 1 + draft ranks).')
    top = cur.execute(
        'SELECT p.name, p.position, ps.total_points FROM players p '
        'JOIN previous_season ps ON ps.player_id = p.player_id '
        'WHERE p.league_id = ? ORDER BY ps.total_points DESC LIMIT 8', (lid,)).fetchall()
    print('Top draft ranks (by last-season points):')
    for r in top:
        print(f"  {r['name']:16} {r['position']:4} {r['total_points']}")
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('db_path')
    ap.add_argument('--tbl', default='2017', help='SuperBru table id (default: 2017)')
    ap.add_argument('--dry-run', action='store_true', help='scrape + report, no DB writes')
    args = ap.parse_args()
    rebuild(args.db_path, args.tbl, args.dry_run)
