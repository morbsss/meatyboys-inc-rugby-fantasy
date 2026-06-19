"""
Fantasy Rugby Competition Table.

Reads fixtures from fixtures.csv and calculates weekly team scores
from team_selections + weekly_stats in fantasy_2025_26.db.

Scoring:
  Win  = 4 league pts
  Draw = 2 league pts each
  Loss = 0 league pts
  Winning BP  = +1 if winning margin >= 81
  Losing BP   = +1 if losing margin <= 18
  Bye  = 2 league pts (no match played)
"""

import re
import sqlite3
import os
from collections import defaultdict
from dataclasses import dataclass, field

from .leagues import roster_model, DEFAULT_MODEL

DB_PATH      = 'fantasy_2025_26.db'
FIXTURES_CSV = 'fixtures.csv'
DB_TYPE      = os.getenv('DB_TYPE', 'sqlite').lower()

WIN_PTS          = 4
DRAW_PTS         = 2
LOSS_PTS         = 0
BP_PTS           = 1
WINNER_BP_MARGIN = 27   # winner gets BP if margin >= this
LOSER_BP_MARGIN  = 11   # loser gets BP if margin <= this

# Season shape (applies to any league size).
REGULAR_ROUNDS = 15     # rounds 1..15 are the round-robin regular season
SEMI_LEG1      = 16     # semi-final first leg
SEMI_LEG2      = 17     # semi-final second leg
FINAL_ROUND    = 18     # grand final
TOTAL_ROUNDS   = FINAL_ROUND


def _get_placeholder(conn):
    """Return the appropriate placeholder for the database type."""
    try:
        import psycopg2
        if isinstance(conn, psycopg2.extensions.connection):
            return '%s'
    except ImportError:
        pass
    return '?'


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Team:
    name:           str
    played:         int   = 0
    won:            int   = 0
    drawn:          int   = 0
    lost:           int   = 0
    points_for:     float = 0.0
    points_against: float = 0.0
    bonus_points:   int   = 0
    league_points:  int   = 0

    @property
    def points_diff(self) -> float:
        return self.points_for - self.points_against


# ---------------------------------------------------------------------------
# Fixture parsing
# ---------------------------------------------------------------------------

def parse_fixtures(path: str) -> list[tuple[int, str, bool, str, bool]]:
    """
    Returns list of (week, home_team, home_bp, away_team, away_bp).
    'Bye' is kept as a team name — handled separately in scoring.
    """
    fixtures = []
    current_week = None

    with open(path, newline='', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            cols = [c.strip() for c in line.split(',')]

            week_match = re.match(r'Week (\d+)', cols[0])
            if week_match:
                current_week = int(week_match.group(1))
                continue

            if current_week is None or not any(cols):
                continue

            raw_home = cols[0]
            raw_away = cols[4] if len(cols) > 4 else ''

            if not raw_home or not raw_away:
                continue

            home_bp = raw_home.endswith(' BP')
            away_bp = raw_away.endswith(' BP')
            home    = raw_home.removesuffix(' BP').strip()
            away    = raw_away.removesuffix(' BP').strip()

            fixtures.append((current_week, home, home_bp, away, away_bp))

    return fixtures


# ---------------------------------------------------------------------------
# Score lookup
# ---------------------------------------------------------------------------

def _team_model(conn, team_name: str) -> dict:
    """Resolve the roster model for a team via its league. Falls back to the
    default model when the league can't be determined (e.g. test fixtures)."""
    ph = _get_placeholder(conn)
    cur = conn.cursor()
    slug = None
    try:
        cur.execute(f'SELECT league_id FROM team_selections WHERE team_name = {ph} LIMIT 1', (team_name,))
        row = cur.fetchone()
        lid = (row['league_id'] if isinstance(row, dict) else row[0]) if row else None
        if lid is not None:
            cur.execute(f'SELECT slug FROM leagues WHERE league_id = {ph}', (lid,))
            r2 = cur.fetchone()
            slug = (r2['slug'] if isinstance(r2, dict) else r2[0]) if r2 else None
    except Exception:
        slug = None
    finally:
        cur.close()
    return roster_model(slug) if slug else DEFAULT_MODEL


def get_team_score(conn, team_name: str, round_num: int) -> float:
    """
    Sum weekly points for team_name's line-up in round_num.
    - Points are cumulative, so each player's score = this round - previous round.
    - Kicking points are ALWAYS credited; there is no designated goal kicker.
    - Captain (is_captain=1): base_delta * 2.
    - OFDS-style leagues (auto_sub) only count the effective starting XV after the
      real-lineup auto-substitution; all others sum every selected player.
    """
    model = _team_model(conn, team_name)
    if model.get('auto_sub'):
        return _ofds_team_score(conn, team_name, round_num)

    # Captain doubling only applies in leagues that have a captain (OFDS).
    score_expr = ('CASE WHEN is_captain = 1 THEN base_delta * 2 ELSE base_delta END'
                  if model.get('captain') else 'base_delta')
    cursor = conn.cursor()
    placeholder = _get_placeholder(conn)
    cursor.execute(f'''
        SELECT COALESCE(SUM(
            {score_expr}
        ), 0) AS total_score
        FROM (
            SELECT
                ts.is_captain,
                MAX(ws_curr.total_points) - COALESCE(MAX(ws_prev.total_points), 0)
                    AS base_delta
            FROM team_selections ts
            JOIN weekly_stats ws_curr
                ON ws_curr.player_id = ts.player_id AND ws_curr.round = ts.round
            LEFT JOIN weekly_stats ws_prev
                ON ws_prev.player_id = ts.player_id AND ws_prev.round = ts.round - 1
            WHERE ts.team_name = {placeholder} AND ts.round = {placeholder}
            GROUP BY ts.player_id, ts.is_captain
        )
    ''', (team_name, round_num))
    row = cursor.fetchone()
    cursor.close()

    if not row:
        individuals = 0.0
    elif DB_TYPE == 'postgres':
        individuals = float(row.get('total_score', 0))
    else:
        individuals = float(row[0])

    return individuals + _front_row_score(conn, team_name, round_num)


def _front_row_score(conn, team_name: str, round_num: int) -> float:
    """Points from a team's club front-row unit: the club's PR/HK players who
    were in the real matchday squad (match_lineups) that round, by points delta.
    Falls back to all the club's front-rowers when no lineup data exists yet."""
    ph = _get_placeholder(conn)
    cur = conn.cursor()
    cur.execute(f'SELECT club, league_id, is_captain FROM team_front_row '
                f'WHERE team_name = {ph} AND round = {ph}', (team_name, round_num))
    fr = cur.fetchone()
    if not fr:
        cur.close()
        return 0.0
    club = fr['club'] if isinstance(fr, dict) else fr[0]
    league_id = fr['league_id'] if isinstance(fr, dict) else fr[1]
    # mtyby (the only league with a front-row unit) has no captain, so the unit
    # never doubles — score its plain points delta.

    # Super Rugby: the club front row is one pre-aggregated 'FR' player —
    # score its own points delta directly (no matchday PR/HK derivation).
    cur.execute(f"SELECT player_id FROM players "
                f"WHERE league_id = {ph} AND team = {ph} AND position = 'FR' LIMIT 1",
                (league_id, club))
    fr_player = cur.fetchone()
    if fr_player:
        pid = fr_player['player_id'] if isinstance(fr_player, dict) else fr_player[0]
        cur.execute(f'''
            SELECT COALESCE((SELECT MAX(total_points) FROM weekly_stats WHERE player_id = {ph} AND round = {ph}), 0)
                 - COALESCE((SELECT MAX(total_points) FROM weekly_stats WHERE player_id = {ph} AND round = {ph}), 0) AS d
        ''', (pid, round_num, pid, round_num - 1))
        r = cur.fetchone()
        cur.close()
        return float((r['d'] if isinstance(r, dict) else r[0]) or 0)

    # Premiership (parked): derive the front row from the club's PR/HK players.
    cur.execute(f'SELECT COUNT(*) FROM match_lineups WHERE round = {ph} AND real_team = {ph}',
                (round_num, club))
    cnt_row = cur.fetchone()
    has_lineup = ((cnt_row['count'] if isinstance(cnt_row, dict) else cnt_row[0]) or 0) > 0

    matchday = ''
    extra = []
    if has_lineup:
        matchday = (f"AND EXISTS (SELECT 1 FROM match_lineups ml WHERE ml.round = {ph} "
                    f"AND ml.real_team = p.team AND REPLACE(p.name, '''', '') = ml.player_name)")
        extra = [round_num]

    cur.execute(f'''
        SELECT COALESCE(SUM(base_delta), 0) AS s FROM (
            SELECT MAX(ws.total_points) - COALESCE(MAX(wp.total_points), 0) AS base_delta
            FROM players p
            JOIN weekly_stats ws ON ws.player_id = p.player_id AND ws.round = {ph}
            LEFT JOIN weekly_stats wp ON wp.player_id = p.player_id AND wp.round = {ph}
            WHERE p.league_id = {ph} AND p.team = {ph} AND p.position IN ('PR', 'HK')
            {matchday}
            GROUP BY p.player_id
        ) t
    ''', (round_num, round_num - 1, league_id, club, *extra))
    row = cur.fetchone()
    cur.close()
    if not row:
        return 0.0
    return float((row['s'] if isinstance(row, dict) else row[0]) or 0)


def _ofds_team_score(conn, team_name: str, round_num: int) -> float:
    """Score a full-XV (OFDS) team for a round with the real-lineup auto-sub.

    Only the effective starting XV scores. A fantasy starter who isn't in the
    real ESPN starting line-up is replaced by a same-position fantasy bench
    player who IS starting for real (rule 4); if no such cover exists the starter
    stays (and simply scores whatever they got, ~0 if they didn't play). Before
    any real line-up is published the named starters score as picked. Captain
    points double when the captain is in the effective XV.
    """
    ph = _get_placeholder(conn)
    cur = conn.cursor()
    cur.execute(f'''
        SELECT ts.player_id, ts.is_bench, ts.is_captain, p.position, p.name, p.team
        FROM team_selections ts JOIN players p ON p.player_id = ts.player_id
        WHERE ts.team_name = {ph} AND ts.round = {ph}
    ''', (team_name, round_num))
    rows = []
    for r in cur.fetchall():
        d = dict(r) if not isinstance(r, dict) else r
        rows.append({'pid': d['player_id'], 'bench': bool(d['is_bench']),
                     'cap': bool(d['is_captain']), 'pos': d['position'],
                     'name': d['name'], 'team': d['team']})

    # Real starting XV for the round (apostrophes stripped, matching ingestion).
    cur.execute(f'SELECT real_team, player_name FROM match_lineups '
                f'WHERE round = {ph} AND is_bench = 0', (round_num,))
    real = {((rt['real_team'] if isinstance(rt, dict) else rt[0]),
             (rt['player_name'] if isinstance(rt, dict) else rt[1])) for rt in cur.fetchall()}
    have_lineup = bool(real)

    def starting_real(pl):
        return (pl['team'], pl['name'].replace("'", "")) in real

    starters = [r for r in rows if not r['bench']]
    bench_by_pos: dict[str, list] = defaultdict(list)
    for b in (r for r in rows if r['bench']):
        bench_by_pos[b['pos']].append(b)

    if not have_lineup:
        effective = starters
    else:
        effective, used = [], set()
        for s in starters:
            if starting_real(s):
                effective.append(s)
                continue
            sub = next((b for b in bench_by_pos[s['pos']]
                        if id(b) not in used and starting_real(b)), None)
            if sub:
                used.add(id(sub))
                effective.append(sub)
            else:
                effective.append(s)   # no cover — keep the starter

    total = 0.0
    for pl in effective:
        cur.execute(f'''
            SELECT COALESCE((SELECT MAX(total_points) FROM weekly_stats WHERE player_id = {ph} AND round = {ph}), 0)
                 - COALESCE((SELECT MAX(total_points) FROM weekly_stats WHERE player_id = {ph} AND round = {ph}), 0) AS d
        ''', (pl['pid'], round_num, pl['pid'], round_num - 1))
        rr = cur.fetchone()
        delta = float((rr['d'] if isinstance(rr, dict) else rr[0]) or 0)
        total += delta * 2 if pl['cap'] else delta
    cur.close()
    return total


# ---------------------------------------------------------------------------
# Table calculation
# ---------------------------------------------------------------------------

def calculate_table(
    fixtures: list[tuple[int, str, bool, str, bool]],
    conn: sqlite3.Connection,
    max_round: int | None = None,
    award_bonus: bool = True,
) -> list[Team]:
    """Standings table. With `award_bonus` (OFDS) teams earn league points +
    bonus points and rank by league points then points-for. Without it (mtyby)
    there are no bonus/league points — ranking is purely wins, then points-for."""
    teams: dict[str, Team] = {}

    # Group fixtures by week for two-pass bye processing
    weeks: dict[int, list] = defaultdict(list)
    for fix in fixtures:
        if max_round is None or fix[0] <= max_round:
            weeks[fix[0]].append(fix)

    for week, week_fixtures in sorted(weeks.items()):
        # Register all teams
        for _, home, _, away, _ in week_fixtures:
            for t in (home, away):
                if t != 'Bye' and t not in teams:
                    teams[t] = Team(name=t)

        # Pass 1: score all non-bye matches
        played_scores: dict[str, float] = {}
        for _, home, _, away, _ in week_fixtures:
            if home == 'Bye' or away == 'Bye':
                continue
            hs = get_team_score(conn, home, week)
            aw = get_team_score(conn, away, week)
            played_scores[home] = hs
            played_scores[away] = aw

        # Bye score = average of all non-bye team scores that week
        bye_score = (
            sum(played_scores.values()) / len(played_scores)
            if played_scores else 0.0
        )

        # Pass 2a: process regular matches
        for _, home, _, away, _ in week_fixtures:
            if home == 'Bye' or away == 'Bye':
                continue
            hs = played_scores[home]
            aw = played_scores[away]
            if hs == 0 and aw == 0:
                continue

            teams[home].played         += 1
            teams[away].played         += 1
            teams[home].points_for     += hs
            teams[home].points_against += aw
            teams[away].points_for     += aw
            teams[away].points_against += hs

            _apply_result(teams[home], teams[away], hs, aw, award_bonus)

        # Pass 2b: process bye matches vs the week average
        for _, home, _, away, _ in week_fixtures:
            if home != 'Bye' and away != 'Bye':
                continue
            team_name = home if away == 'Bye' else away
            ts = get_team_score(conn, team_name, week)
            if ts == 0 and bye_score == 0:
                continue

            teams[team_name].played         += 1
            teams[team_name].points_for     += ts
            teams[team_name].points_against += bye_score

            margin = abs(ts - bye_score)
            if ts > bye_score:
                teams[team_name].won           += 1
                teams[team_name].league_points += WIN_PTS
                if award_bonus and margin >= WINNER_BP_MARGIN:
                    teams[team_name].league_points += BP_PTS
                    teams[team_name].bonus_points  += BP_PTS
            elif ts < bye_score:
                teams[team_name].lost          += 1
                if award_bonus and margin <= LOSER_BP_MARGIN:
                    teams[team_name].league_points += BP_PTS
                    teams[team_name].bonus_points  += BP_PTS
            else:
                teams[team_name].drawn         += 1
                teams[team_name].league_points += DRAW_PTS

    # Standings order: OFDS by league points then points-for; mtyby purely by
    # wins, then points-for (no league/bonus points).
    key = ((lambda t: (t.league_points, t.points_for)) if award_bonus
           else (lambda t: (t.won, t.points_for)))
    return sorted(teams.values(), key=key, reverse=True)


def _apply_result(home: Team, away: Team, hs: float, aw: float, award_bonus: bool = True) -> None:
    """Apply win/draw/loss (+ bonus points when the league awards them)."""
    if hs > aw:
        margin = hs - aw
        home.won           += 1
        away.lost          += 1
        home.league_points += WIN_PTS
        if award_bonus and margin >= WINNER_BP_MARGIN:
            home.league_points += BP_PTS
            home.bonus_points  += BP_PTS
        if award_bonus and margin <= LOSER_BP_MARGIN:
            away.league_points += BP_PTS
            away.bonus_points  += BP_PTS
    elif aw > hs:
        margin = aw - hs
        away.won           += 1
        home.lost          += 1
        away.league_points += WIN_PTS
        if award_bonus and margin >= WINNER_BP_MARGIN:
            away.league_points += BP_PTS
            away.bonus_points  += BP_PTS
        if award_bonus and margin <= LOSER_BP_MARGIN:
            home.league_points += BP_PTS
            home.bonus_points  += BP_PTS
    else:
        home.drawn         += 1
        away.drawn         += 1
        home.league_points += DRAW_PTS
        away.league_points += DRAW_PTS


# ---------------------------------------------------------------------------
# League roster + fixture generation (dynamic: any team count)
# ---------------------------------------------------------------------------

def get_league_teams(conn, league_id=None) -> list[str]:
    """Distinct fantasy team names that have a squad, sorted for a stable schedule.

    Pass `league_id` to scope to one league (required once both leagues hold
    data); omitting it returns every team across all leagues (legacy behaviour).
    """
    cursor = conn.cursor()
    if league_id is None:
        cursor.execute('SELECT DISTINCT team_name FROM team_selections')
    else:
        ph = _get_placeholder(conn)
        cursor.execute(
            f'SELECT DISTINCT team_name FROM team_selections WHERE league_id = {ph}',
            (league_id,),
        )
    rows = cursor.fetchall()
    cursor.close()
    names = [r['team_name'] if isinstance(r, dict) else r[0] for r in rows]
    return sorted(n for n in names if n)


def generate_regular_fixtures(
    teams: list[str],
    n_rounds: int = REGULAR_ROUNDS,
) -> list[tuple[int, str, bool, str, bool]]:
    """
    Round-robin schedule via the circle method, cycled to `n_rounds`.

    - Odd team counts get a rotating 'Bye' each round.
    - Home/away alternates each full cycle so pairings even out.
    - Returns the same tuple shape as parse_fixtures:
      (week, home, home_bp, away, away_bp) — bp flags are always False
      (bonus points are computed from margins in calculate_table).
    """
    ts = sorted(teams)
    if not ts:
        return []
    if len(ts) % 2:
        ts = ts + ['Bye']          # odd → pad with a bye sentinel

    m = len(ts)                    # even
    arr = ts[:]
    base: list[list[tuple[str, str]]] = []   # base[r] = list of (home, away)
    for r in range(m - 1):
        pairs = []
        for i in range(m // 2):
            h, a = arr[i], arr[m - 1 - i]
            if r % 2:              # alternate within the base block too
                h, a = a, h
            pairs.append((h, a))
        base.append(pairs)
        # rotate all but the first element (circle method)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]

    fixtures: list[tuple[int, str, bool, str, bool]] = []
    R = len(base)                  # m-1 distinct rounds
    for w in range(n_rounds):
        cycle = w // R
        for h, a in base[w % R]:
            if cycle % 2:          # swap home/away on alternate cycles
                h, a = a, h
            fixtures.append((w + 1, h, False, a, False))
    return fixtures


# ---------------------------------------------------------------------------
# Playoffs (Championship top-4 + Sacko bottom-4; two-legged semis + final)
# ---------------------------------------------------------------------------

def _semi(conn, home: str, away: str, max_round: int, mode: str) -> dict:
    """
    Two-legged aggregate semi-final (legs in SEMI_LEG1 / SEMI_LEG2).

    `mode='champ'`: the WINNER advances to the final.
    `mode='sacko'`: the LOSER advances (it's a race to the wooden spoon —
                    winning lets you escape). `winner` here = who advances.
    The higher seed (home) is protected on a tie.
    """
    h1, a1 = get_team_score(conn, home, SEMI_LEG1), get_team_score(conn, away, SEMI_LEG1)
    h2, a2 = get_team_score(conn, home, SEMI_LEG2), get_team_score(conn, away, SEMI_LEG2)
    agg_h, agg_a = h1 + h2, a1 + a2
    played = max_round >= SEMI_LEG2 and not (agg_h == 0 and agg_a == 0)
    if mode == 'sacko':
        advancer = (home if agg_h < agg_a else away)   # loser advances; tie protects home
    else:
        advancer = (home if agg_h >= agg_a else away)  # winner advances; tie protects home
    return {
        'home': home, 'away': away,
        'home_leg1': round(h1, 1), 'away_leg1': round(a1, 1),
        'home_leg2': round(h2, 1), 'away_leg2': round(a2, 1),
        'home_agg': round(agg_h, 1), 'away_agg': round(agg_a, 1),
        'played': played, 'winner': advancer if played else None,
    }


def _bracket(conn, seeds: list[str], max_round: int, mode: str) -> dict:
    """
    4-team bracket: semis (1v4, 2v3) + final. `seeds` is best->worst.
    `mode='champ'`: final winner is `champion`.
    `mode='sacko'`: the two semi LOSERS contest the final and the final
                    LOSER takes the spoon (stored in `champion`).
    """
    s1, s2, s3, s4 = seeds
    semis = [_semi(conn, s1, s4, max_round, mode), _semi(conn, s2, s3, max_round, mode)]

    home = semis[0]['winner']   # 'winner' = the team that advances
    away = semis[1]['winner']
    fh = get_team_score(conn, home, FINAL_ROUND) if home else 0.0
    fa = get_team_score(conn, away, FINAL_ROUND) if away else 0.0
    final_played = bool(home and away) and max_round >= FINAL_ROUND and not (fh == 0 and fa == 0)
    if mode == 'sacko':
        champion = (home if fh < fa else away) if final_played else None   # loser = wooden spoon
    else:
        champion = (home if fh >= fa else away) if final_played else None  # winner = champion
    final = {
        'home': home, 'away': away,
        'home_score': round(fh, 1), 'away_score': round(fa, 1),
        'played': final_played, 'champion': champion,
    }
    return {'seeds': seeds, 'semis': semis, 'final': final}


def build_playoffs(conn, table: list[Team], max_round: int) -> dict:
    """
    Playoff brackets seeded off the regular-season standings.
      Championship = top 4 (win to advance; final winner is champion).
      Sacko        = bottom 4 (lose to advance; final loser takes the spoon).
    Sacko needs >= 8 teams. Pre-completion seeds are provisional and all
    matches read played=False.
    """
    n = len(table)
    out = {
        'complete': max_round >= REGULAR_ROUNDS,
        'championship': None,
        'sacko': None,
    }
    if n >= 4:
        out['championship'] = _bracket(conn, [t.name for t in table[:4]], max_round, 'champ')
    if n >= 8:
        out['sacko'] = _bracket(conn, [t.name for t in table[-4:]], max_round, 'sacko')
    return out


def standings_progression(
    regular: list[tuple[int, str, bool, str, bool]],
    conn,
    max_round: int,
    award_bonus: bool = True,
) -> list[dict]:
    """Per-round regular-season standings (spec §7 movement arrows + history graph).

    Returns [{'round': r, 'order': [team_name, ...]}] for r in 1..min(max_round,
    REGULAR_ROUNDS), best-ranked first. Every team appears in every round (teams
    yet to play are padded at the bottom) so the front-end can plot a continuous
    rank line per team and derive week-on-week movement.
    """
    all_teams = sorted({t for fx in regular for t in (fx[1], fx[3]) if t != 'Bye'})
    upto = min(max_round, REGULAR_ROUNDS)
    history = []
    for r in range(1, upto + 1):
        order = [t.name for t in calculate_table(regular, conn, r, award_bonus)]
        order += [t for t in all_teams if t not in order]   # pad teams not yet ranked
        history.append({'round': r, 'order': order})
    return history


def playoff_fixtures(playoffs: dict) -> list[tuple[int, str, bool, str, bool]]:
    """
    Emit (week, home, False, away, False) rows for the playoff weeks so the
    fixtures page and weekly chart include them. Semis appear in both legs
    (weeks 16 & 17); the final (week 18) only once both finalists are known.
    """
    rows: list[tuple[int, str, bool, str, bool]] = []
    for bracket in (playoffs.get('championship'), playoffs.get('sacko')):
        if not bracket:
            continue
        for semi in bracket['semis']:
            for wk in (SEMI_LEG1, SEMI_LEG2):
                rows.append((wk, semi['home'], False, semi['away'], False))
        fin = bracket['final']
        if fin['home'] and fin['away']:
            rows.append((FINAL_ROUND, fin['home'], False, fin['away'], False))
    return rows


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_table(table: list[Team]) -> None:
    print(f'\n{"=" * 82}')
    print(f'  FANTASY RUGBY — COMPETITION TABLE')
    print(f'{"=" * 82}')
    print(f'  {"#":>2}  {"Team":<35} {"P":>3} {"W":>3} {"D":>3} {"L":>3}'
          f' {"PF":>7} {"PA":>7} {"PD":>7} {"Pts":>4}')
    print(f'  {"-" * 78}')
    for i, t in enumerate(table, 1):
        pd_str = f'+{t.points_diff:.1f}' if t.points_diff >= 0 else f'{t.points_diff:.1f}'
        print(
            f'  {i:>2}. {t.name:<33} {t.played:>3} {t.won:>3} {t.drawn:>3} {t.lost:>3}'
            f' {t.points_for:>7.1f} {t.points_against:>7.1f} {pd_str:>7} {t.league_points:>4}'
        )
    print()


def display_results(
    fixtures: list[tuple[int, str, bool, str, bool]],
    conn: sqlite3.Connection,
    max_round: int | None = None,
) -> None:
    current_week = None
    for week, home, home_bp, away, away_bp in fixtures:
        if max_round is not None and week > max_round:
            break
        if week != current_week:
            current_week = week
            print(f'\n  --- Week {week} ---')
        if away == 'Bye':
            print(f'  {home} — BYE')
            continue
        if home == 'Bye':
            print(f'  {away} — BYE')
            continue
        hs = get_team_score(conn, home, week)
        as_ = get_team_score(conn, away, week)
        if hs == 0 and as_ == 0:
            print(f'  {home} vs {away} — no data')
        else:
            margin = abs(hs - as_)
            winner = home if hs > as_ else (away if as_ > hs else None)
            loser  = away if hs > as_ else (home if as_ > hs else None)
            w_bp   = winner and margin >= WINNER_BP_MARGIN
            l_bp   = loser  and margin <= LOSER_BP_MARGIN
            h_tag  = ' (BP)' if (home == winner and w_bp) or (home == loser and l_bp) else ''
            a_tag  = ' (BP)' if (away == winner and w_bp) or (away == loser and l_bp) else ''
            print(f'  {home}{h_tag} {hs:.1f} – {as_:.1f} {away}{a_tag}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    fixtures = parse_fixtures(FIXTURES_CSV)
    print(f'Loaded {len(fixtures)} fixtures.')

    with sqlite3.connect(DB_PATH) as conn:
        max_round = conn.execute('SELECT MAX(round) FROM weekly_stats').fetchone()[0]
        print(f'Stats available up to round {max_round}.\n')

        display_results(fixtures, conn, max_round)
        table = calculate_table(fixtures, conn, max_round)

    display_table(table)


if __name__ == '__main__':
    main()
