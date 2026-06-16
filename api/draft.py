"""
Snake-draft engine — pure logic, no DB or network.

Teams draft in snake order (1..N, N..1, 1..N, ...) for the league model's pick
count. Each league has its own roster model (api/leagues.py): meatyboys drafts
flexible individuals (+ one optional FR unit); OFDS drafts a strict full-XV 23
by position quota. These functions take the model so both work; absent users
are auto-drafted. The DB-backed orchestration lives in api/index.py.
"""

from collections import Counter, defaultdict

from .leagues import DEFAULT_MODEL, model_draft_picks, squad_quotas

TOTAL_PICKS_PER_TEAM = model_draft_picks(DEFAULT_MODEL)   # back-compat alias


def snake_sequence(order: list[str], model: dict = DEFAULT_MODEL) -> list[str]:
    """The full pick order: forward on odd rounds, reversed on even ones."""
    seq: list[str] = []
    for r in range(model_draft_picks(model)):
        seq.extend(order if r % 2 == 0 else list(reversed(order)))
    return seq


def total_picks(order: list[str], model: dict = DEFAULT_MODEL) -> int:
    return len(order) * model_draft_picks(model)


def team_on_clock(order: list[str], pick_number: int, model: dict = DEFAULT_MODEL) -> str | None:
    """Team for a 1-based pick number, or None if out of range / draft over."""
    seq = snake_sequence(order, model)
    if pick_number < 1 or pick_number > len(seq):
        return None
    return seq[pick_number - 1]


def draft_round_of_pick(order: list[str], pick_number: int) -> int:
    """1-based snake round a pick falls in."""
    return ((pick_number - 1) // len(order)) + 1 if order else 0


# ---------------------------------------------------------------------------
# Roster needs + auto-pick
# ---------------------------------------------------------------------------

def unmet_needs(owned_positions: list[str], model: dict = DEFAULT_MODEL) -> dict[str, int]:
    """Per-position squad slots still to fill (positioned starters + bench).

    For a flexible/any-position bench (meatyboys) only the positioned starter
    slots count; the bench is filled with best-available afterwards.
    """
    quotas = squad_quotas(model)
    have = Counter(owned_positions)
    return {pos: max(0, n - have.get(pos, 0)) for pos, n in quotas.items()}


def auto_pick(available: list[dict], owned_positions: list[str], model: dict = DEFAULT_MODEL,
              available_fr_clubs: list[dict] | None = None, has_fr: bool = False) -> dict | None:
    """Choose the next individual player to auto-draft for a team.

    Returns {'type': 'player', 'player': <dict>} or None. The club front-row
    unit is OPTIONAL and never auto-drafted (manual pick only); the params are
    kept for call-site compatibility.

    Priority: fill an unmet position quota (by rank), then best-available.
    """
    ind = sorted(available, key=lambda p: (-p.get('rank', 0), str(p['id'])))
    remaining = unmet_needs(owned_positions, model)
    if sum(remaining.values()) > 0:
        helper = next((p for p in ind if remaining.get(p['position'], 0) > 0), None)
        if helper is not None:
            return {'type': 'player', 'player': helper}
    if ind:
        return {'type': 'player', 'player': ind[0]}
    return None


def choose_starting_xi(roster: list[dict], model: dict = DEFAULT_MODEL) -> tuple[list[dict], list[dict]]:
    """Split a drafted roster into (starters, bench) for the initial line-up.

    `roster` items are dicts with 'position' (+ 'rank' for ordering). Strict
    models place, per position, the top N by rank as starters and the next M on
    the bench; flexible models fill the starter slots greedily then bench the
    rest.
    """
    ranked = sorted(roster, key=lambda p: (-p.get('rank', 0), str(p.get('id', ''))))

    if model.get('positioned_bench'):
        by_pos: dict[str, list] = defaultdict(list)
        for p in ranked:
            by_pos[p['position']].append(p)
        starters, bench = [], []
        for pos, players in by_pos.items():
            ns = model['starters'].get(pos, 0)
            nb = model['bench'].get(pos, 0)
            starters.extend(players[:ns])
            bench.extend(players[ns:ns + nb])
        return starters, bench

    remaining = dict(model['starters'])
    starter_count = sum(model['starters'].values())
    starters, bench = [], []
    for p in ranked:
        if len(starters) < starter_count and remaining.get(p['position'], 0) > 0:
            remaining[p['position']] -= 1
            starters.append(p)
        else:
            bench.append(p)
    return starters, bench
