"""Season rollover: archive last season's player totals and reset for a clean draft.

Usage:
    python tools/season_rollover.py prem_rugby_26_27.db [--season 2025-26]

What it does (idempotent, transactional):
  1. Creates the `previous_season` table if missing.
  2. Archives each player's final totals (latest weekly_stats round) into
     `previous_season`. This is the "total player data" used to rank the draft.
  3. Empties weekly_stats — the new season starts with no scores.
  4. Clears all round selections: team_selections, team_front_row,
     match_lineups, rounds.
  5. Resets the draft: deletes draft_picks and sets every draft_state row back
     to status='pending', current_pick=0, clocks cleared.
  6. Clears the trades log.

Players, users and leagues are preserved.
"""
import argparse
import sqlite3
from datetime import datetime, timezone


def rollover(db_path: str, season: str) -> None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS previous_season (
            player_id INTEGER PRIMARY KEY REFERENCES players(player_id),
            league_id INTEGER,
            total_points REAL,
            price REAL,
            kicking TEXT,
            points_per_game TEXT,
            popularity TEXT,
            form TEXT,
            season TEXT,
            archived_at TEXT
        )
    ''')

    # Latest weekly_stats row per player = that player's final season totals.
    cur.execute('''
        INSERT OR REPLACE INTO previous_season
            (player_id, league_id, total_points, price, kicking,
             points_per_game, popularity, form, season, archived_at)
        SELECT ws.player_id, ws.league_id, ws.total_points, ws.price, ws.kicking,
               ws.points_per_game, ws.popularity, ws.form, ?, ?
        FROM weekly_stats ws
        JOIN (SELECT player_id, MAX(round) AS r FROM weekly_stats GROUP BY player_id) m
          ON m.player_id = ws.player_id AND m.r = ws.round
    ''', (season, now))
    archived = cur.rowcount

    cur.execute('DELETE FROM weekly_stats')
    cur.execute('DELETE FROM team_selections')
    cur.execute('DELETE FROM team_front_row')
    cur.execute('DELETE FROM match_lineups')
    cur.execute('DELETE FROM rounds')
    cur.execute('DELETE FROM draft_picks')
    cur.execute('DELETE FROM trades')
    cur.execute('''
        UPDATE draft_state
        SET status='pending', current_pick=0,
            started_at=NULL, completed_at=NULL, pick_deadline=NULL
    ''')

    con.commit()

    print(f'Archived {archived} players into previous_season (season {season}).')
    for t in ('previous_season', 'players', 'weekly_stats', 'team_selections',
              'team_front_row', 'match_lineups', 'rounds', 'draft_picks',
              'trades', 'users'):
        n = cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t:18} {n}')
    print('  draft_state ->', [dict(r) for r in cur.execute(
        'SELECT league_id, status, current_pick FROM draft_state')])
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('db_path')
    ap.add_argument('--season', default='2025-26',
                    help="Label for the archived season (default: 2025-26)")
    args = ap.parse_args()
    rollover(args.db_path, args.season)
