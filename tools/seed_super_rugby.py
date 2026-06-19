"""Seed the meatyboys (Super Rugby) league with players + last-season totals
from the uploaded Super Rugby database.

Source : tools/db_modelling/super_rugby_2026.db  (table: player_summary)
Target : the app DB (players + previous_season, scoped to the meatyboys league)

`player_summary` gives one row per player for the past season, including the
pre-aggregated club "Front Row" units (e.g. "Chiefs Front Row"). Positions are
mapped to the app's codes; the front row is stored as position 'FR' so it draft-
ranks as a club unit. total_sum (season total) feeds previous_season.total_points
which is what the draft ranks the pool by.

Usage:
    python tools/seed_super_rugby.py [APP_DB] [--source SRC_DB] [--league meatyboys]
"""
import argparse
import sqlite3
from datetime import datetime, timezone

POSITION_MAP = {
    'Loose Forward': 'LF',
    'Outside Back':  'OBK',
    'Lock':          'LK',
    'Midfielder':    'MID',
    'Half Back':     'SH',
    'Fly Half':      'FH',
    'Front Row':     'FR',   # pre-aggregated club unit
}


def seed(app_db: str, source_db: str, league_slug: str) -> None:
    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row
    con = sqlite3.connect(app_db)
    cur = con.cursor()
    now = datetime.now(timezone.utc).isoformat()

    row = cur.execute('SELECT league_id FROM leagues WHERE slug = ?', (league_slug,)).fetchone()
    if not row:
        raise SystemExit(f'League {league_slug!r} not found in {app_db}')
    league_id = row[0]

    # Idempotent: clear this league's player pool + archive, then reseed.
    cur.execute('DELETE FROM previous_season WHERE league_id = ?', (league_id,))
    cur.execute('DELETE FROM players WHERE league_id = ?', (league_id,))

    rows = src.execute('''
        SELECT playername, team, position, total_mean, total_sum, total_count,
               game3_avg, game5_avg, season_year
        FROM player_summary
    ''').fetchall()

    seeded, skipped, by_pos = 0, 0, {}
    for r in rows:
        pos = POSITION_MAP.get(r['position'])
        if pos is None:
            skipped += 1
            continue
        name, team = r['playername'], r['team']
        cur.execute(
            'INSERT OR IGNORE INTO players (name, team, position, league_id) VALUES (?, ?, ?, ?)',
            (name, team, pos, league_id))
        pid_row = cur.execute(
            'SELECT player_id FROM players WHERE name = ? AND team = ? AND position = ? AND league_id = ?',
            (name, team, pos, league_id)).fetchone()
        pid = pid_row[0]
        cur.execute('''
            INSERT OR REPLACE INTO previous_season
                (player_id, league_id, total_points, price, kicking,
                 points_per_game, popularity, form, season, archived_at)
            VALUES (?, ?, ?, NULL, NULL, ?, NULL, ?, ?, ?)
        ''', (
            pid, league_id, round(r['total_sum'] or 0, 1),
            f"{(r['total_mean'] or 0):.1f}",
            f"{(r['game5_avg'] or 0):.1f}",
            str(r['season_year']), now,
        ))
        seeded += 1
        by_pos[pos] = by_pos.get(pos, 0) + 1

    con.commit()

    print(f'Seeded league {league_slug!r} (id {league_id}) from {source_db}')
    print(f'  players seeded : {seeded}   (skipped unmapped: {skipped})')
    print(f'  by position    : {by_pos}')
    print(f'  previous_season: {cur.execute("SELECT COUNT(*) FROM previous_season WHERE league_id = ?", (league_id,)).fetchone()[0]}')
    con.close()
    src.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('app_db', nargs='?', default='fantasy_2026_27.db')
    ap.add_argument('--source', default='tools/db_modelling/super_rugby_2026.db')
    ap.add_argument('--league', default='meatyboys')
    args = ap.parse_args()
    seed(args.app_db, args.source, args.league)
