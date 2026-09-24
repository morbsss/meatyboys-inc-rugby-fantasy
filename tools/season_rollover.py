"""Season rollover: record honours, archive player totals, open the next season.

Usage:
    python tools/season_rollover.py fantasy_2026_27.db [--season 2026-27]

What it does (idempotent-ish, transactional). Evergreen model: accounts, honours
and past season_entries PERSIST; only the per-season competition data resets.

  1. Computes the FINAL standings (via the app's own calculate_table) and records
     honours - champion / runner_up / sacko - keyed to each team's OWNING ACCOUNT
     (user_id), so a future rename never loses history. Skipped if no rounds were
     played.
  2. Archives each player's final totals into `previous_season` (the draft's
     "last season" ranking data).
  3. Marks the active season 'complete' and opens the next one 'active'.
  4. Resets the per-season competition data: weekly_stats, team_selections,
     team_front_row, match_lineups, rounds, draft_picks, trades; draft_state back
     to 'pending'. Managers opt back in for the new season (season_entries starts
     empty for it) via /api/season/join.

Players, users, honours, seasons and past season_entries are preserved.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

# Allow `python tools/season_rollover.py` to import the `api` package (the
# project root is the parent of this file's directory).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _next_label(label: str) -> str:
    """'2026-27' -> '2027-28'. Falls back to '<label>-next' if unparseable."""
    try:
        start, end = label.split('-')
        return f'{int(start) + 1}-{str(int(end) + 1).zfill(2)}'
    except Exception:
        return f'{label}-next'


def _final_placements(db_path: str) -> dict:
    """{league_id: {'champion': team, 'runner_up': team, 'sacko': team}} from the
    app's standings. Returns {} when unavailable or no rounds were played."""
    os.environ.setdefault('DB_TYPE', 'sqlite')
    os.environ['DB_PATH'] = db_path
    try:
        from api.db import get_connection, ensure_schema
        from api.competition import calculate_table, get_league_teams, generate_regular_fixtures
        from api.index import REGULAR_ROUNDS, _roster_model, get_last_round
    except Exception as e:                                  # pragma: no cover
        print('  (honours) app standings unavailable, skipping:', e)
        return {}

    out: dict = {}
    conn = get_connection()
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute('SELECT league_id FROM leagues')
    for (lid,) in [tuple(r) for r in cur.fetchall()]:
        max_round = get_last_round(conn, lid)
        if not max_round or max_round < 1:
            continue                                        # nothing played
        award_bonus = bool(_roster_model(conn, lid).get('bonus'))
        teams = get_league_teams(conn, lid)
        if len(teams) < 2:
            continue
        table = calculate_table(generate_regular_fixtures(teams), conn,
                                min(max_round, REGULAR_ROUNDS), award_bonus)
        if not table:
            continue
        places = {'champion': table[0].name, 'sacko': table[-1].name}
        if len(table) >= 2:
            places['runner_up'] = table[1].name
        out[lid] = places
    conn.close()
    return out


def _record_honours(cur, closing_season_id, placements, now) -> int:
    """Insert one honour row per placement, resolved team -> owning user_id."""
    written = 0
    for lid, places in placements.items():
        for placement, team in places.items():
            cur.execute('SELECT user_id FROM season_entries '
                        'WHERE league_id = ? AND season_id = ? AND team_name = ?',
                        (lid, closing_season_id, team))
            row = cur.fetchone()
            if row is None:
                cur.execute('SELECT user_id FROM users WHERE league_id = ? AND team_name = ?',
                            (lid, team))
                row = cur.fetchone()
            if row is None:
                print(f'  (honours) no account for {placement} "{team}" (league {lid}); skipped')
                continue
            uid = row['user_id'] if isinstance(row, sqlite3.Row) else row[0]
            cur.execute('INSERT INTO honours (user_id, league_id, season_id, placement, created_at) '
                        'VALUES (?, ?, ?, ?, ?)', (uid, lid, closing_season_id, placement, now))
            written += 1
    return written


def rollover(db_path: str, season: str | None) -> None:
    # Compute final standings first (needs the season data intact + ensure_schema).
    placements = _final_placements(db_path)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Active season being closed (the evergreen tables were created by
    # ensure_schema inside _final_placements).
    srow = cur.execute("SELECT season_id, label FROM seasons WHERE status = 'active' "
                       "ORDER BY season_id DESC LIMIT 1").fetchone()
    closing_id = srow['season_id'] if srow else None
    closing_label = srow['label'] if srow else (season or '2026-27')
    season = season or closing_label

    cur.execute('''
        CREATE TABLE IF NOT EXISTS previous_season (
            player_id INTEGER PRIMARY KEY REFERENCES players(player_id),
            league_id INTEGER, total_points REAL, price REAL, kicking TEXT,
            points_per_game TEXT, popularity TEXT, form TEXT, season TEXT, archived_at TEXT
        )
    ''')

    # Record honours BEFORE wiping the standings' source data.
    honours_written = 0
    if closing_id is not None:
        honours_written = _record_honours(cur, closing_id, placements, now)

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

    # Advance the season: close the current one, open the next.
    next_label = None
    if closing_id is not None:
        cur.execute("UPDATE seasons SET status = 'complete' WHERE season_id = ?", (closing_id,))
        next_label = _next_label(closing_label)
        if cur.execute('SELECT season_id FROM seasons WHERE label = ?', (next_label,)).fetchone() is None:
            cur.execute("INSERT INTO seasons (label, status, created_at) VALUES (?, 'active', ?)",
                        (next_label, now))
        else:
            cur.execute("UPDATE seasons SET status = 'active' WHERE label = ?", (next_label,))

    # Reset per-season competition data. Accounts, honours, seasons and past
    # season_entries are preserved.
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

    print(f'Closed season "{closing_label}" -> opened "{next_label}".')
    print(f'Honours recorded: {honours_written} | players archived: {archived}.')
    for t in ('previous_season', 'players', 'users', 'seasons', 'season_entries',
              'honours', 'weekly_stats', 'team_selections', 'draft_picks', 'trades'):
        n = cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t:18} {n}')
    print('  draft_state ->', [dict(r) for r in cur.execute(
        'SELECT league_id, status, current_pick FROM draft_state')])
    con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('db_path')
    ap.add_argument('--season', default=None,
                    help="Label for the closing/archived season (default: the active season)")
    args = ap.parse_args()
    rollover(args.db_path, args.season)
