"""Load a full PLAYED Super Rugby season into the meatyboys league of an app DB.

Source : tools/db_modelling/super_rugby_2026.db  (modelling schema)
Target : an app DB (default fantasy_2025_26.db), league 'meatyboys'

Unlike seed_super_rugby.py (which only seeds the draft pool), this builds a
scored season the app can render — standings, fixtures, match-ups:

  players        ← distinct 2026 players from detailed_scores (incl. FR units)
  weekly_stats   ← per-round CUMULATIVE points (the app stores cumulative totals)
  real_fixtures  ← the round's matches (home/away from ref_fixtures)
  rounds         ← one row per round, dated in the past so the season reads done
  team_selections ← each manager's roster (Starting/Bench) for every round
  team_front_row  ← each manager's club FR unit
  previous_season ← season totals (draft ranking / rollover)

Roster picks are matched to the scoring data by source playerid; picks with no
2026 scores in the dataset are skipped (reported at the end).

Usage:
    python tools/load_super_rugby_season.py [APP_DB] \
        [--source tools/db_modelling/super_rugby_2026.db] [--season 2026]
"""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc).isoformat()

# Modelling position names → app codes ('Front Row' is the pre-aggregated unit).
POS = {'Loose Forward': 'LF', 'Outside Back': 'OBK', 'Lock': 'LK',
       'Midfielder': 'MID', 'Half Back': 'SH', 'Fly Half': 'FH', 'Front Row': 'FR'}


def load(app_db, source_db, league_slug, season_year):
    src = sqlite3.connect(source_db); src.row_factory = sqlite3.Row
    app = sqlite3.connect(app_db);    app.row_factory = sqlite3.Row
    cur = app.cursor()

    row = cur.execute('SELECT league_id FROM leagues WHERE slug = ?', (league_slug,)).fetchone()
    if not row:
        raise SystemExit(f'League {league_slug!r} not found in {app_db}')
    lid = row['league_id']

    # ---- wipe this league (clean reseed) --------------------------------
    for t in ('weekly_stats', 'team_selections', 'team_front_row', 'rounds',
              'match_lineups', 'real_fixtures', 'draft_picks', 'previous_season',
              'player_predictions', 'matchup_predictions', 'players'):
        cur.execute(f'DELETE FROM {t} WHERE league_id = ?', (lid,))
    cur.execute('DELETE FROM draft_state WHERE league_id = ?', (lid,))

    # ---- valid rounds for the season ------------------------------------
    rounds = [r[0] for r in src.execute(
        'SELECT DISTINCT round_num FROM detailed_scores '
        'WHERE season_year = ? AND round_num BETWEEN 1 AND 30 ORDER BY round_num',
        (season_year,))]
    max_round = max(rounds)

    # ---- players (distinct scorers, incl. FR units) ---------------------
    # One canonical (name, team, position) per source playerid.
    meta = src.execute(
        'SELECT playerid, playername, team, position FROM detailed_scores '
        'WHERE season_year = ? AND round_num BETWEEN 1 AND 30 GROUP BY playerid',
        (season_year,)).fetchall()
    src_to_app = {}            # source playerid -> app player_id
    club_of = {}               # source playerid -> club (team)
    skipped_pos = 0
    for m in meta:
        code = POS.get(m['position'])
        if not code:
            skipped_pos += 1
            continue
        cur.execute(
            'INSERT OR IGNORE INTO players (name, team, position, league_id) VALUES (?, ?, ?, ?)',
            (m['playername'], m['team'], code, lid))
        pid = cur.execute(
            'SELECT player_id FROM players WHERE name = ? AND team = ? AND position = ? AND league_id = ?',
            (m['playername'], m['team'], code, lid)).fetchone()['player_id']
        src_to_app[m['playerid']] = pid
        club_of[m['playerid']] = m['team']

    # ---- weekly_stats: per-round CUMULATIVE points ----------------------
    rows = src.execute(
        'SELECT playerid, round_num, total FROM detailed_scores '
        'WHERE season_year = ? AND round_num BETWEEN 1 AND 30 ORDER BY playerid, round_num',
        (season_year,)).fetchall()
    by_player = {}
    for r in rows:
        by_player.setdefault(r['playerid'], {})[r['round_num']] = r['total'] or 0.0
    ws_rows = []
    for spid, app_pid in src_to_app.items():
        cum = 0.0
        per = by_player.get(spid, {})
        for rnd in range(1, max_round + 1):
            cum += per.get(rnd, 0.0)
            ws_rows.append((app_pid, rnd, round(cum, 1), NOW, lid))
    cur.executemany(
        'INSERT INTO weekly_stats (player_id, round, total_points, scraped_at, league_id) '
        'VALUES (?, ?, ?, ?, ?)', ws_rows)

    # ---- real_fixtures (+ rounds) ---------------------------------------
    # home/away lookup from ref_fixtures (case-normalised).
    homeaway = {}
    for r in src.execute('SELECT round_num, team, opposition, home_away FROM ref_fixtures'):
        ha = (r['home_away'] or '').strip().lower()
        if ha in ('home', 'away'):
            homeaway[(r['round_num'], r['team'], r['opposition'])] = ha
    seen = set()
    fx_rows = []
    for r in src.execute(
            'SELECT DISTINCT round_num, team, opposition FROM detailed_scores '
            'WHERE season_year = ? AND round_num BETWEEN 1 AND 30 AND opposition IS NOT NULL',
            (season_year,)):
        rnd, a, b = r['round_num'], r['team'], r['opposition']
        key = (rnd, frozenset((a, b)))
        if key in seen:
            continue
        seen.add(key)
        if homeaway.get((rnd, a, b)) == 'home':
            home, away = a, b
        elif homeaway.get((rnd, b, a)) == 'home' or homeaway.get((rnd, a, b)) == 'away':
            home, away = b, a
        else:
            home, away = sorted((a, b))      # deterministic fallback
        fx_rows.append((lid, rnd, home, away))
    cur.executemany(
        'INSERT INTO real_fixtures (league_id, round, home_team, away_team) VALUES (?, ?, ?, ?)',
        fx_rows)

    # No `rounds` rows: this is a completed past season, and the legacy app DB
    # keeps a sole round_number PK on `rounds` (shared with the Premiership side),
    # so per-league round numbers would collide. With no rows, get_next_round
    # falls back to MAX(weekly_stats.round) + 1 — exactly what a finished season
    # wants (nothing is "upcoming", nothing locks).

    # ---- rosters → team_selections + team_front_row ---------------------
    managers = [r['manager'] for r in src.execute(
        'SELECT DISTINCT manager FROM manager_team ORDER BY manager')]
    matched = skipped_pick = 0
    draft_state_picks = 0
    for team in managers:
        picks = src.execute(
            'SELECT playerid, position, role FROM manager_team WHERE manager = ?', (team,)).fetchall()
        indiv, fr_pick = [], None
        for p in picks:
            app_pid = src_to_app.get(p['playerid'])
            if app_pid is None:
                skipped_pick += 1
                continue
            matched += 1
            if p['position'] == 'FR':
                fr_pick = (club_of.get(p['playerid']), p['role'] == 'Bench')
            else:
                indiv.append((app_pid, 1 if p['role'] == 'Bench' else 0))
        # jersey: starters first, then bench
        indiv.sort(key=lambda x: x[1])
        for rnd in range(1, max_round + 1):
            for j, (app_pid, bench) in enumerate(indiv, start=1):
                cur.execute(
                    'INSERT INTO team_selections '
                    '(round, team_name, player_id, is_captain, is_kicker, is_bench, jersey, scraped_at, league_id) '
                    'VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?)',
                    (rnd, team, app_pid, bench, j, NOW, lid))
            if fr_pick and fr_pick[0]:
                cur.execute(
                    'INSERT INTO team_front_row '
                    '(league_id, team_name, round, club, is_captain, is_bench, scraped_at) '
                    'VALUES (?, ?, ?, ?, 0, ?, ?)',
                    (lid, team, rnd, fr_pick[0], 1 if fr_pick[1] else 0, NOW))
        draft_state_picks += len(indiv) + (1 if fr_pick and fr_pick[0] else 0)

    cur.execute('INSERT INTO draft_state (league_id, status, current_pick, started_at, completed_at) '
                'VALUES (?, ?, ?, ?, ?)', (lid, 'complete', draft_state_picks, NOW, NOW))

    # ---- previous_season (totals for draft / rollover) ------------------
    prev = 0
    for r in src.execute('SELECT playerid, total_sum, total_mean, game5_avg, season_year FROM player_summary'):
        app_pid = src_to_app.get(r['playerid'])
        if app_pid is None:
            continue
        cur.execute(
            'INSERT OR REPLACE INTO previous_season '
            '(player_id, league_id, total_points, price, kicking, points_per_game, popularity, form, season, archived_at) '
            'VALUES (?, ?, ?, NULL, NULL, ?, NULL, ?, ?, ?)',
            (app_pid, lid, round(r['total_sum'] or 0, 1),
             f"{(r['total_mean'] or 0):.1f}", f"{(r['game5_avg'] or 0):.1f}",
             str(r['season_year']), NOW))
        prev += 1

    app.commit()
    app.close(); src.close()

    print(f'Loaded Super Rugby {season_year} → league {league_slug!r} (id {lid}) of {app_db}')
    print(f'  players        : {len(src_to_app)}  (skipped unmapped position: {skipped_pos})')
    print(f'  weekly_stats   : {len(ws_rows)}  over {max_round} rounds')
    print(f'  real_fixtures  : {len(fx_rows)}')
    print(f'  fantasy teams  : {len(managers)}')
    print(f'  roster picks   : {matched} matched, {skipped_pick} skipped (no 2026 scores)')
    print(f'  previous_season: {prev}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('app_db', nargs='?', default='fantasy_2025_26.db')
    ap.add_argument('--source', default='tools/db_modelling/super_rugby_2026.db')
    ap.add_argument('--league', default='meatyboys')
    ap.add_argument('--season', type=int, default=2026)
    args = ap.parse_args()
    load(args.app_db, args.source, args.league, args.season)


if __name__ == '__main__':
    main()
