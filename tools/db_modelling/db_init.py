import sqlite3
import params


def init_db():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS player_list (
            playerid    TEXT,
            position    TEXT,
            playername  TEXT,
            team        TEXT,
            owner       TEXT,
            opposition  TEXT,
            score       TEXT,
            news        TEXT,
            page        INTEGER,
            fetched_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS ref_players (
            playerid    TEXT PRIMARY KEY,
            playername  TEXT,
            team        TEXT,
            position    TEXT,
            date_added  TEXT
        );

        CREATE TABLE IF NOT EXISTS player_team_news (
            playerid    TEXT,
            playername  TEXT,
            news        TEXT,
            owner       TEXT,
            round_num   INTEGER,
            season      INTEGER,
            PRIMARY KEY (playerid, round_num, season)
        );

        CREATE TABLE IF NOT EXISTS ref_fixtures (
            round_num   INTEGER,
            team        TEXT,
            opposition  TEXT,
            home_away   TEXT,
            match_date  TEXT,
            kickoff     TEXT,
            season      INTEGER,
            PRIMARY KEY (round_num, team, season)
        );

        CREATE TABLE IF NOT EXISTS detailed_scores (
            playerid             TEXT,
            season               TEXT,
            season_year          INTEGER,
            playername           TEXT,
            team                 TEXT,
            position             TEXT,
            owner                TEXT,
            round_num            INTEGER,
            gameweek             TEXT,
            mins                 INTEGER,
            total                REAL,
            tries                TEXT,
            try_assists          TEXT,
            metres_gained        TEXT,
            clean_breaks         TEXT,
            defenders_beaten     TEXT,
            offloads             TEXT,
            turnovers_won        TEXT,
            turnovers_conceded   TEXT,
            penalties_conceded   TEXT,
            tackles_made         TEXT,
            tackles_missed       TEXT,
            lineout_steals       TEXT,
            penalties_kicked     TEXT,
            conversions_kicked   TEXT,
            drop_goals           TEXT,
            yellow_cards         TEXT,
            red_cards            TEXT,
            scrums_won_penalty   TEXT,
            opposition           TEXT,
            news                 TEXT
        );

        CREATE TABLE IF NOT EXISTS player_summary (
            playerid        TEXT,
            position        TEXT,
            playername      TEXT,
            team            TEXT,
            owner           TEXT,
            news            TEXT,
            total_mean      REAL,
            total_sum       REAL,
            total_count     INTEGER,
            avg_rank        REAL,
            game3_avg       REAL,
            game3_avg_rank  REAL,
            game5_avg       REAL,
            game5_avg_rank  REAL,
            season_year     INTEGER
        );

        -- Staging table: holds current-season extract before merging into detailed_scores
        CREATE TABLE IF NOT EXISTS detailed_scores_staging (
            playerid             TEXT,
            season               TEXT,
            season_year          INTEGER,
            playername           TEXT,
            team                 TEXT,
            position             TEXT,
            owner                TEXT,
            round_num            INTEGER,
            gameweek             TEXT,
            mins                 INTEGER,
            total                REAL,
            tries                TEXT,
            try_assists          TEXT,
            metres_gained        TEXT,
            clean_breaks         TEXT,
            defenders_beaten     TEXT,
            offloads             TEXT,
            turnovers_won        TEXT,
            turnovers_conceded   TEXT,
            penalties_conceded   TEXT,
            tackles_made         TEXT,
            tackles_missed       TEXT,
            lineout_steals       TEXT,
            penalties_kicked     TEXT,
            conversions_kicked   TEXT,
            drop_goals           TEXT,
            yellow_cards         TEXT,
            red_cards            TEXT,
            scrums_won_penalty   TEXT,
            opposition           TEXT,
            news                 TEXT
        );

        -- Maps every historical (season_year, playerid) to a canonical playerid.
        -- match_type: 'identity' | 'exact' | 'fuzzy' | 'manual' | 'none'
        -- canonical_playerid is NULL for players with no current-season equivalent.
        CREATE TABLE IF NOT EXISTS player_id_map (
            source_season_year      INTEGER,
            source_playerid         TEXT,
            source_name             TEXT,
            canonical_playerid      TEXT,
            canonical_season_year   INTEGER,
            canonical_name          TEXT,
            match_type              TEXT,
            match_score             REAL,
            PRIMARY KEY (source_season_year, source_playerid)
        );

        -- One row per unique player across all seasons.
        CREATE TABLE IF NOT EXISTS master_players (
            canonical_playerid      TEXT PRIMARY KEY,
            canonical_season_year   INTEGER,
            playername              TEXT,
            position                TEXT,
            first_season            INTEGER,
            last_season             INTEGER
        );

        CREATE TABLE IF NOT EXISTS opp_position_deltas (
            opposition  TEXT,
            position    TEXT,
            delta       REAL,
            PRIMARY KEY (opposition, position)
        );

        CREATE TABLE IF NOT EXISTS all_predictions (
            playerid            TEXT,
            playername          TEXT,
            team                TEXT,
            position            TEXT,
            owner               TEXT,
            round_num           INTEGER,
            opposition          TEXT,
            news                TEXT,
            season_year         INTEGER,
            baseline_season_avg REAL,
            baseline_3g_avg     REAL,
            baseline_5g_avg     REAL,
            simple_season_pred  REAL,
            simple_3g_pred      REAL,
            simple_5g_pred      REAL,
            gamma_p30           REAL,
            gamma_p50           REAL,
            gamma_p70           REAL,
            weibull_p30         REAL,
            weibull_p50         REAL,
            weibull_p70         REAL,
            gbm_pred            REAL,
            actual_score        REAL,
            PRIMARY KEY (playerid, round_num, season_year)
        );

        CREATE TABLE IF NOT EXISTS round_picks (
            playerid        TEXT,
            playername      TEXT,
            team            TEXT,
            position        TEXT,
            owner           TEXT,
            round_num       INTEGER,
            opposition      TEXT,
            news            TEXT,
            pred_score      REAL,
            rank_in_pos     INTEGER,
            PRIMARY KEY (playerid, round_num)
        );

        CREATE TABLE IF NOT EXISTS manager_team (
            manager     TEXT,
            playerid    TEXT,
            playername  TEXT,
            position    TEXT,
            role        TEXT,
            news        TEXT,
            fetched_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS fantasy_matchups (
            gameid          INTEGER,
            round_num       INTEGER,
            season          INTEGER,
            team_a          TEXT,
            team_b          TEXT,
            team_a_id       TEXT,
            team_b_id       TEXT,
            start_date      TEXT,
            end_date        TEXT,
            team_a_bonus    REAL,
            team_b_bonus    REAL,
            PRIMARY KEY (round_num, team_a_id, season)
        );

        CREATE TABLE IF NOT EXISTS win_predictions (
            round_num       INTEGER,
            season          INTEGER,
            team_a          TEXT,
            team_b          TEXT,
            team_a_id       TEXT,
            team_b_id       TEXT,
            team_a_win_prob REAL,
            team_b_win_prob REAL,
            draw_prob       REAL,
            PRIMARY KEY (round_num, team_a_id, season)
        );

        -- Live (in-round) head-to-head probabilities, refreshed every few minutes
        -- during the live window by g_live.py. Played starters are locked to their
        -- actual score; not-yet-played starters keep their projection distribution.
        CREATE TABLE IF NOT EXISTS live_win_predictions (
            round_num       INTEGER,
            season          INTEGER,
            team_a          TEXT,
            team_b          TEXT,
            team_a_id       TEXT,
            team_b_id       TEXT,
            team_a_win_prob REAL,
            team_b_win_prob REAL,
            draw_prob       REAL,
            team_a_locked   REAL,
            team_b_locked   REAL,
            computed_at     TEXT,
            PRIMARY KEY (round_num, team_a_id, season)
        );

        -- Live per-player view: actual score so far + projected final for the round.
        CREATE TABLE IF NOT EXISTS live_predictions (
            playerid        TEXT,
            playername      TEXT,
            team            TEXT,
            owner           TEXT,
            round_num       INTEGER,
            season          INTEGER,
            live_score      REAL,
            status          TEXT,
            projected_final REAL,
            computed_at     TEXT,
            PRIMARY KEY (playerid, round_num, season)
        );

        CREATE INDEX IF NOT EXISTS idx_ds_player     ON detailed_scores (playerid, season_year);
        CREATE INDEX IF NOT EXISTS idx_ds_round      ON detailed_scores (round_num, season_year);
        CREATE INDEX IF NOT EXISTS idx_ps_season     ON player_summary (season_year);
        CREATE INDEX IF NOT EXISTS idx_map_canonical ON player_id_map (canonical_playerid);
        CREATE INDEX IF NOT EXISTS idx_pred_round    ON all_predictions (round_num, season_year);
    ''')

    # CREATE TABLE IF NOT EXISTS won't add columns to a pre-existing table —
    # add new columns idempotently here.
    ref_cols = {r[1] for r in con.execute("PRAGMA table_info(ref_fixtures)")}
    if 'kickoff' not in ref_cols:
        con.execute("ALTER TABLE ref_fixtures ADD COLUMN kickoff TEXT")

    con.commit()
    con.close()
    print(f'Database ready at {params.DB_PATH}')


if __name__ == '__main__':
    init_db()
