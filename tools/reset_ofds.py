"""Reset a league's draft + squad for a fresh start under a new ruleset, while
keeping its player pool, previous-season archive and users.

Built for the OFDS relaunch (full rugby-union XV model): clears the old draft
and any saved squads so teams can re-draft, but preserves the Premiership
players + previous_season (used to rank the draft) and the registered users.

Usage:
    python tools/reset_ofds.py [APP_DB] [--league ofds]
"""
import argparse
import sqlite3


def reset(app_db: str, league_slug: str) -> None:
    con = sqlite3.connect(app_db)
    cur = con.cursor()
    row = cur.execute('SELECT league_id FROM leagues WHERE slug = ?', (league_slug,)).fetchone()
    if not row:
        raise SystemExit(f'League {league_slug!r} not found in {app_db}')
    lid = row[0]

    for table in ('draft_picks', 'team_selections', 'team_front_row', 'trades'):
        cur.execute(f'DELETE FROM {table} WHERE league_id = ?', (lid,))
    cur.execute(
        "UPDATE draft_state SET status='pending', current_pick=0, started_at=NULL, "
        "completed_at=NULL, pick_deadline=NULL WHERE league_id = ?", (lid,))
    cur.execute(
        'UPDATE leagues SET draft_order=NULL, draft_at=NULL, season_start=NULL WHERE league_id = ?',
        (lid,))
    con.commit()

    print(f'Reset league {league_slug!r} (id {lid}) in {app_db}')
    for t in ('draft_picks', 'team_selections', 'team_front_row', 'trades',
              'players', 'previous_season', 'users'):
        n = cur.execute(f'SELECT COUNT(*) FROM {t} WHERE league_id = ?', (lid,)).fetchone()[0]
        print(f'  {t:18} {n}')
    print('  draft_state ->', cur.execute(
        'SELECT status, current_pick FROM draft_state WHERE league_id = ?', (lid,)).fetchone())
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('app_db', nargs='?', default='prem_rugby_26_27.db')
    ap.add_argument('--league', default='ofds')
    args = ap.parse_args()
    reset(args.app_db, args.league)
