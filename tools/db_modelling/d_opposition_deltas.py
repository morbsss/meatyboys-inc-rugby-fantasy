"""
d_opposition_deltas.py
----------------------
Computes how each team defends against each position — the opposition delta.

A positive delta means attacking players typically score ABOVE their season average
against that opposition (weak defence). Negative = strong defence.

Uses last 2 seasons of data for stability. Writes to opp_position_deltas.
"""
import sqlite3
import pandas as pd
import params

# Number of players per position group (used to normalise team-level score to per-player)
POSITION_PLAYER_COUNTS = {
    'Outside Back':  3,
    'Loose Forward': 2,
    'Midfielder':    2,
}


def compute(con):
    seasons = sorted(params.SEASON_START_DATES.keys())
    use_seasons = seasons[-2:]  # last 2 seasons

    df = pd.read_sql(f'''
        SELECT ds.season_year, ds.round_num, ds.team, ds.opposition,
               ds.position, ds.mins, ds.total
        FROM detailed_scores ds
        WHERE ds.season_year IN ({",".join(str(s) for s in use_seasons)})
          AND ds.mins >= 5
          AND ds.total IS NOT NULL
          AND ds.opposition IS NOT NULL
          AND ds.team IS NOT NULL
    ''', con)

    if df.empty:
        print('No data for opposition deltas')
        return

    # Aggregate to game-position level (sum across players in same position/team/game)
    game = (df.groupby(['season_year', 'round_num', 'team', 'opposition', 'position'])
              ['total'].sum().reset_index(name='game_total'))

    # Each team's season average per position
    season_avg = (game.groupby(['season_year', 'team', 'position'])
                      ['game_total'].mean().reset_index(name='team_season_avg'))

    merged = game.merge(season_avg, on=['season_year', 'team', 'position'])
    merged['n_players'] = merged['position'].map(POSITION_PLAYER_COUNTS).fillna(1)
    merged['delta'] = (merged['game_total'] - merged['team_season_avg']) / merged['n_players']

    opp_deltas = (merged.groupby(['opposition', 'position'])['delta']
                        .mean().reset_index())

    con.execute('DELETE FROM opp_position_deltas')
    opp_deltas.to_sql('opp_position_deltas', con, if_exists='append', index=False)
    con.commit()

    print(f'opp_position_deltas: {len(opp_deltas)} rows '
          f'({opp_deltas["opposition"].nunique()} teams × {opp_deltas["position"].nunique()} positions), '
          f'seasons {use_seasons}')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    compute(con)
    con.close()


if __name__ == '__main__':
    main()
