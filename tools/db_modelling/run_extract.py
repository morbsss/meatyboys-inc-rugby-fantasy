"""
Full analytics pipeline — run by cron.

Order:
  1. db_init             — ensure schema exists (safe to run repeatedly)
  2. a_fetch_players     — player hub + reference update + team news snapshot
  3. b_fetch_scores      — game-by-game scores for all players
  4. b2_fetch_lineups    — manager team selections from FRD
  5. d_opposition_deltas — opposition defensive strength deltas
  6. d_player_summary    — current season player stats snapshot
  7. e_predictions       — 9-method ensemble predictions for current round
  8. f_picks             — best picks per position → round_picks table
  9. f_win_predictions   — head-to-head win probabilities → win_predictions table

One-time setup (run manually before first cron run):
  python save_fixtures.py          # loads fixtures_{CURRENT_SEASON}.csv into DB
  python save_fixtures.py 2025     # repeat for each historical season
  python save_matchups.py          # loads ref_matchups_{CURRENT_SEASON}.csv into DB
"""
import datetime as dt
import sqlite3
import db_init
import a_fetch_players
import b_fetch_scores
import b2_fetch_lineups
import d_opposition_deltas
import d_player_summary
import e_predictions
import f_picks
import f_win_predictions
import params


def main():
    start = dt.datetime.now(dt.timezone.utc)
    print(f'=== Analytics Pipeline — {start.strftime("%Y-%m-%d %H:%M:%S")} ===\n')

    print('--- Step 0: Initialise database')
    db_init.init_db()
    print()

    print('--- Step 1: Fetch player list')
    a_fetch_players.main()
    print()

    print('--- Step 2: Fetch detailed scores')
    b_fetch_scores.main()
    print()

    print('--- Step 3: Fetch manager lineups')
    b2_fetch_lineups.main()
    print()

    # Shared connection for analysis steps
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')

    print('--- Step 4: Opposition deltas')
    d_opposition_deltas.compute(con)
    print()

    print('--- Step 5: Player summary')
    d_player_summary.compute(con)
    print()

    print('--- Step 6: Predictions')
    e_predictions.run(con)
    print()

    print('--- Step 7: Round picks')
    f_picks.run(con)
    print()

    print('--- Step 8: Win predictions')
    f_win_predictions.run(con)
    print()

    # Step 9: backfill each prediction's actual score from detailed_scores
    # (current round fills in as games are played; past rounds fill for accuracy).
    print('--- Step 9: Backfill actual scores')
    n = con.execute('''
        UPDATE all_predictions
        SET actual_score = (
            SELECT ds.total FROM detailed_scores ds
            WHERE ds.playerid    = all_predictions.playerid
              AND ds.season_year = all_predictions.season_year
              AND ds.round_num   = all_predictions.round_num
              AND ds.total IS NOT NULL
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM detailed_scores ds
            WHERE ds.playerid    = all_predictions.playerid
              AND ds.season_year = all_predictions.season_year
              AND ds.round_num   = all_predictions.round_num
              AND ds.total IS NOT NULL
        )
    ''').rowcount
    con.commit()
    print(f'Backfilled actual_score for {n} prediction rows')
    print()

    completed = dt.datetime.now(dt.timezone.utc)
    con.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_extract', ?)",
                (completed.isoformat(),))
    con.commit()
    con.close()

    elapsed = (completed - start).total_seconds()
    print(f'=== Pipeline complete in {elapsed:.0f}s ===')


if __name__ == '__main__':
    main()
