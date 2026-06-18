"""
d_player_summary.py
-------------------
Computes per-player season stats for the current season.
Replaces player_summary table each run (point-in-time snapshot).

Columns: playerid, position, playername, team, owner, news,
         total_mean, total_sum, total_count, avg_rank,
         game3_avg, game3_avg_rank, game5_avg, game5_avg_rank,
         season_year
"""
import sqlite3
import pandas as pd
import params


def compute(con):
    season = params.CURRENT_SEASON

    df = pd.read_sql(f'''
        SELECT playerid, position, playername, team, owner, news,
               round_num, mins, total
        FROM detailed_scores
        WHERE season_year = {season}
          AND mins >= 5
          AND total IS NOT NULL
        ORDER BY playerid, round_num
    ''', con)

    if df.empty:
        print(f'No score data for season {season}')
        return

    # Season-level aggregation
    summary = (df.groupby(['playerid', 'position', 'playername', 'team', 'owner', 'news'])
                 ['total']
                 .agg(total_mean='mean', total_sum='sum', total_count='count')
                 .reset_index())

    summary['avg_rank'] = summary['total_mean'].rank(ascending=False, method='min')

    # Rolling averages — last N completed games per player
    def _last_n_avg(group, n):
        return group.sort_values('round_num')['total'].tail(n).mean()

    game3 = (df.groupby('playerid')
               .apply(_last_n_avg, n=3)
               .reset_index(name='game3_avg'))
    game5 = (df.groupby('playerid')
               .apply(_last_n_avg, n=5)
               .reset_index(name='game5_avg'))

    summary = summary.merge(game3, on='playerid', how='left')
    summary = summary.merge(game5, on='playerid', how='left')

    summary['game3_avg_rank'] = summary['game3_avg'].rank(ascending=False, method='min')
    summary['game5_avg_rank'] = summary['game5_avg'].rank(ascending=False, method='min')
    summary['season_year'] = season

    con.execute('DELETE FROM player_summary')
    summary.to_sql('player_summary', con, if_exists='append', index=False)
    con.commit()

    print(f'player_summary: {len(summary)} players for season {season}')


def main():
    con = sqlite3.connect(params.DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    compute(con)
    con.close()


if __name__ == '__main__':
    main()
