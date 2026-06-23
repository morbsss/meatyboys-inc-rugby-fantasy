"""
Seed a fully offline mock database for both leagues (spec milestone 3, §10).

    DB_PATH=mock_fantasy.db python -m api.seed_mock

Pulls all data through the mock data-source adapter (no network) and writes a
self-contained DB so the app runs end-to-end:

  * players + a season of weekly_stats scores      (§4.1, §4.4)
  * the round calendar per league                  (§4.2)
  * match-day lineups per round                    (§4.3)
  * a deterministic mock draft → valid 17-man       (§6.2)
    rosters per fantasy team, materialised into
    team_selections for every scored round

The draft here is a simple greedy stand-in so the standings/fixtures pages have
data; the live snake-draft engine (with auto-draft) is a later milestone. It
does, however, validate against the real roster rules in api/leagues.py.

Run the app against the result with:
    DB_TYPE=sqlite DB_PATH=mock_fantasy.db DATA_SOURCE=mock ALLOW_UNRESTRICTED_EDITS=true \
        python -m flask --app api.index run
"""

import os
from datetime import datetime, timezone

from .db import get_connection, ensure_schema, _ph, _league_id_for_slug
from .leagues import (
    LEAGUES, POSITION_ORDER, roster_model,
    model_starter_count, model_bench_count,
)
from .datasource.mock import MockAdapter

NOW = datetime.now(timezone.utc).isoformat()


def _exec(cur, sql, params=()):
    cur.execute(sql.replace('?', _ph()), params)


# ---------------------------------------------------------------------------
# Deterministic mock draft (greedy, satisfies the 17-man roster rules)
# ---------------------------------------------------------------------------

def _snake_order(teams: list[str], n_rounds: int) -> list[str]:
    order: list[str] = []
    for r in range(n_rounds):
        order += teams if r % 2 == 0 else list(reversed(teams))
    return order


def _draft(players: list[dict], teams: list[str], model: dict) -> dict[str, dict]:
    """Greedy snake draft honouring `model`'s roster shape. Returns
    {team: {'starters': [p,...], 'bench': [p,...]}}.

    Each pick takes the highest-rate available player that fills an open starter
    slot for the team's position quota, else an open bench slot (positioned
    bench → per-position quota, e.g. OFDS; flexible bench → any position, e.g.
    meatyboys). The seed pool is balanced enough that greedy fills every team.
    """
    starter_q = dict(model['starters'])                       # {pos: count}
    positioned_bench = bool(model.get('positioned_bench'))
    bench_q = dict(model['bench']) if positioned_bench else {}
    bench_cap = model_bench_count(model)
    starter_cap = model_starter_count(model)
    roster_size = starter_cap + bench_cap

    available = sorted(players, key=lambda p: (-p['rate'], p['id']))
    rosters = {t: {'starters': [], 'bench': []} for t in teams}
    s_filled = {t: {} for t in teams}                         # per-pos starters taken
    b_filled = {t: {} for t in teams}                         # per-pos bench taken

    def open_starter(team, p) -> bool:
        pos = p['position']
        return s_filled[team].get(pos, 0) < starter_q.get(pos, 0)

    def open_bench(team, p) -> bool:
        if len(rosters[team]['bench']) >= bench_cap:
            return False
        if positioned_bench:
            pos = p['position']
            return b_filled[team].get(pos, 0) < bench_q.get(pos, 0)
        return True

    for team in _snake_order(teams, roster_size):
        if len(rosters[team]['starters']) + len(rosters[team]['bench']) >= roster_size:
            continue
        pick = None
        if len(rosters[team]['starters']) < starter_cap:
            pick = next((p for p in available if open_starter(team, p)), None)
            if pick is not None:
                rosters[team]['starters'].append(pick)
                s_filled[team][pick['position']] = s_filled[team].get(pick['position'], 0) + 1
        if pick is None:
            pick = next((p for p in available if open_bench(team, p)), None)
            if pick is not None:
                rosters[team]['bench'].append(pick)
                b_filled[team][pick['position']] = b_filled[team].get(pick['position'], 0) + 1
        if pick is not None:
            available.remove(pick)
    return rosters


# ---------------------------------------------------------------------------
# Per-league seeding
# ---------------------------------------------------------------------------

def _wipe_league(cur, league_id: int) -> None:
    for table in ('weekly_stats', 'team_selections', 'team_front_row', 'rounds',
                  'match_lineups', 'real_fixtures', 'draft_picks', 'players'):
        _exec(cur, f'DELETE FROM {table} WHERE league_id = ?', (league_id,))
    _exec(cur, 'DELETE FROM draft_state WHERE league_id = ?', (league_id,))


def seed_league(conn, cur, slug: str, adapter: MockAdapter) -> dict:
    cfg = LEAGUES[slug]
    competition = cfg['competition']
    league_id = _league_id_for_slug(cur, slug)
    _wipe_league(cur, league_id)

    # --- players ----------------------------------------------------------
    players = adapter.fetch_players(competition)
    pid_map: dict[tuple, int] = {}
    for p in players:
        _exec(cur, 'INSERT INTO players (name, team, position, league_id) VALUES (?, ?, ?, ?)',
              (p.name, p.team, p.position, league_id))
    # Map back to ids (the freshly inserted league rows).
    _exec(cur, 'SELECT player_id, name, team, position FROM players WHERE league_id = ?', (league_id,))
    for row in cur.fetchall():
        r = row if isinstance(row, dict) else {'player_id': row[0], 'name': row[1], 'team': row[2], 'position': row[3]}
        pid_map[(r['name'], r['team'], r['position'])] = r['player_id']

    # --- rounds -----------------------------------------------------------
    rounds = adapter.fetch_rounds(competition)
    for rd in rounds:
        _exec(cur, 'INSERT INTO rounds (round_number, first_kickoff, last_kickoff, league_id) '
                   'VALUES (?, ?, ?, ?)',
              (rd.round_number, rd.first_kickoff, rd.last_kickoff, league_id))
        # Real-life fixtures (home/away) for the round — shown on the player card.
        for m in rd.matches:
            _exec(cur, 'INSERT INTO real_fixtures (league_id, round, home_team, away_team) '
                       'VALUES (?, ?, ?, ?)',
                  (league_id, rd.round_number, m.home, m.away))
    n_rounds = len(rounds)

    # --- weekly_stats (cumulative scores per round) -----------------------
    for r in range(1, n_rounds + 1):
        for s in adapter.fetch_player_scores(competition, r):
            pid = pid_map.get((s.name, s.team, s.position))
            if pid is None:
                continue
            _exec(cur, 'INSERT INTO weekly_stats '
                       '(player_id, round, total_points, price, kicking, points_per_game, '
                       ' popularity, form, scraped_at, league_id) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (pid, r, s.total_points, s.price, s.kicking, s.points_per_game,
                   s.popularity, s.form, NOW, league_id))

    # --- match lineups ----------------------------------------------------
    for r in range(1, n_rounds + 1):
        for e in adapter.fetch_lineups(competition, r):
            if e.status == 'O':
                continue   # out = absent from the matchday squad (no row)
            _exec(cur, 'INSERT INTO match_lineups '
                       '(round, player_name, real_team, jersey, is_bench, scraped_at, league_id) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?)',
                  (r, e.player_name, e.real_team, e.jersey, e.is_bench, NOW, league_id))

    # --- mock draft → rosters --------------------------------------------
    model = roster_model(slug)
    roster_size = model_starter_count(model) + model_bench_count(model)
    pool = [{'id': p.name + '|' + p.team, 'name': p.name, 'team': p.team,
             'position': p.position, 'rate': adapter_rate(adapter, competition, p),
             'pid': pid_map[(p.name, p.team, p.position)]}
            for p in players]
    fantasy_teams = adapter.fantasy_teams(competition)
    rosters = _draft(pool, fantasy_teams, model)

    # draft_state + draft_picks (record of the draft)
    _exec(cur, 'INSERT INTO draft_state (league_id, status, current_pick, started_at, completed_at) '
               'VALUES (?, ?, ?, ?, ?)',
          (league_id, 'complete', len(fantasy_teams) * roster_size, NOW, NOW))

    pick_no = 0
    # Materialise rosters into team_selections for every scored round.
    for team, roster in rosters.items():
        # Jerseys 1..N, forward-to-back: starters first, then bench.
        starters = sorted(roster['starters'], key=lambda p: POSITION_ORDER.index(p['position']))
        bench = sorted(roster['bench'], key=lambda p: POSITION_ORDER.index(p['position']))
        flat = [(pid, is_bench, i + 1) for i, (pid, is_bench) in enumerate(
            [(p['pid'], 0) for p in starters] + [(p['pid'], 1) for p in bench])]
        # captain = highest-rate starter; kicker = highest-rate FH/SH/OBK in squad
        captain_pid = max(starters, key=lambda p: p['rate'])['pid'] if starters else None
        kickers = [p for p in starters + bench if p['position'] in ('FH', 'SH', 'OBK')]
        kicker_pid = max(kickers, key=lambda p: p['rate'])['pid'] if kickers else captain_pid

        # draft_picks (one row per drafted player, provenance)
        for pid, is_bench, jno in flat:
            pick_no += 1
            _exec(cur, 'INSERT INTO draft_picks '
                       '(league_id, pick_number, round_number, team_name, player_id, is_auto, picked_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?)',
                  (league_id, pick_no, ((pick_no - 1) // len(fantasy_teams)) + 1, team, pid, 0, NOW))

        for r in range(1, n_rounds + 1):
            for pid, is_bench, jno in flat:
                _exec(cur, 'INSERT INTO team_selections '
                           '(round, team_name, player_id, is_captain, is_kicker, is_bench, '
                           ' jersey, scraped_at, league_id) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (r, team, pid, 1 if pid == captain_pid else 0,
                       1 if pid == kicker_pid else 0, is_bench, jno, NOW, league_id))

    # --- front-row UNIT (meatyboys) --------------------------------------
    # Each fantasy team owns one club's front row, seeded as a STARTER so the
    # line-up is the full 11 (the FR unit + 10 individual starters) + 5 bench.
    # Clubs are assigned distinctly (10 teams, 11 Super Rugby clubs).
    fr_units = 0
    if model.get('fr_unit'):
        clubs = sorted({p.team for p in players})
        for i, team in enumerate(fantasy_teams):
            club = clubs[i % len(clubs)]
            for r in range(1, n_rounds + 1):
                _exec(cur, 'INSERT INTO team_front_row '
                           '(league_id, team_name, round, club, is_captain, is_bench, scraped_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (league_id, team, r, club, 0, 0, NOW))
            fr_units += 1

    return {
        'league': slug, 'players': len(players), 'rounds': n_rounds,
        'fantasy_teams': len(fantasy_teams), 'fr_units': fr_units,
    }


def adapter_rate(adapter: MockAdapter, competition: str, player) -> float:
    """Look up a player's seed `rate` (drives draft order)."""
    # The PlayerRecord doesn't carry rate; read it from the seed once, cached.
    return _rate_index(competition).get((player.name, player.team, player.position), player.price)


_rate_cache: dict[str, dict] = {}


def _rate_index(competition: str) -> dict:
    if competition not in _rate_cache:
        from .datasource.mock import _load
        _rate_cache[competition] = {
            (p['name'], p['team'], p['position']): p['rate']
            for p in _load(competition)['players']
        }
    return _rate_cache[competition]


def main() -> None:
    if not os.getenv('DB_PATH'):
        os.environ['DB_PATH'] = 'mock_fantasy.db'
    conn = get_connection()
    ensure_schema(conn)
    cur = conn.cursor()
    adapter = MockAdapter()
    summaries = []
    for slug in ('ofds', 'meatyboys'):
        summaries.append(seed_league(conn, cur, slug, adapter))
    conn.commit()
    cur.close()
    conn.close()
    print(f'Seeded mock DB at {os.environ["DB_PATH"]}:')
    for s in summaries:
        fr = f", {s['fr_units']} FR units" if s.get('fr_units') else ""
        print(f"  {s['league']:>10}: {s['players']} players, "
              f"{s['fantasy_teams']} teams, {s['rounds']} rounds{fr}")


if __name__ == '__main__':
    main()
