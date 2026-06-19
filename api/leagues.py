"""
League registry and roster rules for Meatyboys Rugby Fantasy.

Two leagues run concurrently and independently (spec §1, §5.1):

  meatyboys  → Super Rugby Pacific   → existing repo theme  → Australia/Sydney
  ofds       → English Premiership   → red / blue / white   → Europe/London

This module is pure configuration — no DB, no network — so it can be imported
by the schema layer, the data-source adapters, the draft engine, and the UI
without creating import cycles.
"""

from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# League definitions
# ---------------------------------------------------------------------------

LEAGUES = {
    'meatyboys': {
        'slug':        'meatyboys',
        'name':        'meatyboys',
        'brand':       'Meatyboys',
        'competition': 'super_rugby',
        'comp_name':   'Super Rugby Pacific',
        # "existing repo colours" — the forest/cream/amber design system already
        # in base.html is the default theme.
        'theme':       'forest',
        'timezone':    'Australia/Sydney',
        # Live-source identifiers. The mock adapter is the contract of record
        # (spec §3, §8.6); these are best-effort hooks for the live adapter.
        'espn_league_id':   '270557',   # ESPN Super Rugby Pacific (best-effort)
        'superbru_table':   None,        # no confirmed SuperBru table id yet
        'joinable':         True,       # parked while OFDS is developed
    },
    'ofds': {
        'slug':        'ofds',
        'name':        'Owen Farrell Disappreciation Society',
        'brand':       'OFDS',
        'competition': 'premiership',
        'comp_name':   'English Premiership',
        'theme':       'union',          # red / blue / white
        'timezone':    'Europe/London',
        'espn_league_id':   '267979',   # ESPN Gallagher Premiership
        'superbru_table':   '2017',
        'joinable':         True,        # the active league under development
    },
}

# The competition each league mirrors, keyed for convenience.
COMPETITION_BY_LEAGUE = {k: v['competition'] for k, v in LEAGUES.items()}

DEFAULT_LEAGUE = 'ofds'   # the active league for now (Premiership / full-XV model)


def get_league(slug: str) -> dict:
    """Return the config for a league slug, or raise KeyError."""
    return LEAGUES[slug]


def league_slugs() -> list[str]:
    return list(LEAGUES.keys())


def joinable_leagues() -> dict:
    """Leagues users can currently sign up to (onboarding chooser)."""
    return {k: v for k, v in LEAGUES.items() if v.get('joinable')}


# ---------------------------------------------------------------------------
# Roster rules — front row is a CLUB UNIT, the rest are individual players
# ---------------------------------------------------------------------------
#
# A squad = ONE OPTIONAL club front-row unit (e.g. "Leicester FR") + 15
# individual players. Props (PR) and hookers (HK) are NOT owned individually —
# they only score via the front-row unit, whose scoring players come from the
# club's real matchday lineup (status S/B). The individual squad is
# unconstrained (any 15); the starting-team composition is enforced only on save
# (validate_roster):
#
#   Front Row unit  (1 club → fills 1 of the 11 starting spots; OPTIONAL)
#   Lock            1   (LK)
#   Loose Forwards  2   (LF)
#   Half Back       1   (SH)
#   Fly Half        1   (FH)
#   Midfielders     2   (MID)
#   Outside Backs   3   (OBK)
#   -----------------------------
#   Individual starters  10   (+ optional front-row unit = 11 starting spots)
#   Bench (any individual) 5
#   Individual squad      15   (+ up to 1 front-row unit)

# Positions owned only via the club front-row unit (never drafted individually).
FR_POSITIONS = ('PR', 'HK')
# The front-row unit fills this one of the 11 starting spots (display only).
FRONT_ROW_SPOTS = 1

# Individual (non-front-row) positions and their starting-slot requirements.
INDIVIDUAL_POSITIONS = ['LK', 'LF', 'SH', 'FH', 'MID', 'OBK']

SLOT_POSITIONS: dict[str, set[str]] = {
    'LK':  {'LK'},
    'LF':  {'LF'},
    'SH':  {'SH'},
    'FH':  {'FH'},
    'MID': {'MID'},
    'OBK': {'OBK'},
}

STARTER_SLOTS: dict[str, int] = {
    'LK': 1, 'LF': 2, 'SH': 1, 'FH': 1, 'MID': 2, 'OBK': 3,
}

BENCH_COUNT = 5
STARTER_COUNT = sum(STARTER_SLOTS.values())          # 10 individual starters
ROSTER_SIZE = STARTER_COUNT + BENCH_COUNT            # 15 individual players (+ optional FR unit)
DRAFT_PICKS_PER_TEAM = ROSTER_SIZE + 1               # 16 (15 individuals + up to 1 FR unit)

# All recognised player position codes.
POSITIONS = ['PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK']

# Reverse map: position code → the starting slots it is eligible to fill.
SLOTS_FOR_POSITION: dict[str, set[str]] = {pos: set() for pos in POSITIONS}
for _slot, _positions in SLOT_POSITIONS.items():
    for _pos in _positions:
        SLOTS_FOR_POSITION.setdefault(_pos, set()).add(_slot)


def eligible_slots(position: str) -> set[str]:
    """Starting slots a player of `position` can fill (empty set ⇒ bench only)."""
    return SLOTS_FOR_POSITION.get(position, set())


def starter_demand_by_position() -> dict[str, int]:
    """
    Minimum number of players of each position needed to fill the starting XI.
    Slots that accept multiple positions (Front Row = PR|HK) are attributed to
    their first listed position for demand-estimation purposes; the draft
    engine validates the real eligibility graph, this is only a sizing hint.
    """
    demand: dict[str, int] = {pos: 0 for pos in POSITIONS}
    for slot, count in STARTER_SLOTS.items():
        # Attribute to the alphabetically-first eligible position as a hint.
        primary = sorted(SLOT_POSITIONS[slot])[0]
        demand[primary] += count
    return demand


# ---------------------------------------------------------------------------
# Roster feasibility (used by the draft engine and the squad-save validator)
# ---------------------------------------------------------------------------

def _slot_units() -> list[set[str]]:
    """STARTER_SLOTS expanded into one allowed-position set per starting place
    (length == STARTER_COUNT, i.e. 10 individual starters)."""
    units: list[set[str]] = []
    for slot, count in STARTER_SLOTS.items():
        units.extend(SLOT_POSITIONS[slot] for _ in range(count))
    return units


def assign_starters(positions: list[str]) -> list[int] | None:
    """Try to place `positions` (one per starter) into the starting slots.

    Returns a list mapping each starting-place index → the index in `positions`
    assigned to it, or None if no perfect matching exists. Bipartite matching
    via augmenting paths; inputs are tiny (11 players, 11 places).
    """
    units = _slot_units()
    if len(positions) != len(units):
        return None
    place_to_player = [-1] * len(units)

    def augment(player_idx: int, seen: list[bool]) -> bool:
        for j, allowed in enumerate(units):
            if positions[player_idx] in allowed and not seen[j]:
                seen[j] = True
                if place_to_player[j] == -1 or augment(place_to_player[j], seen):
                    place_to_player[j] = player_idx
                    return True
        return False

    for i in range(len(positions)):
        if not augment(i, [False] * len(units)):
            return None
    return place_to_player


def can_fill_starters(positions: list[str]) -> bool:
    """True if exactly these starter positions can fill the starting slots."""
    return assign_starters(positions) is not None


# ---------------------------------------------------------------------------
# Per-league roster models
# ---------------------------------------------------------------------------
# Each league builds its squad differently. The active league's model drives the
# draft size, the squad validator, the squad-builder UI, and scoring. The
# meatyboys model reuses the constants above; OFDS is a strict full-XV 23.

ROSTER_MODELS: dict[str, dict] = {
    'meatyboys': {
        # Super Rugby: optional club Front-Row UNIT + flexible individuals.
        'fr_unit': True,
        'starters': dict(STARTER_SLOTS),                  # 10 individual starters (+ FR unit)
        'bench_count': BENCH_COUNT,                       # 5, any position
        'positioned_bench': False,
        'individual_positions': list(INDIVIDUAL_POSITIONS),   # excludes PR/HK (the FR unit)
        'draft_picks': DRAFT_PICKS_PER_TEAM,              # 16
        'auto_sub': False,
        'soft': True,                                     # composition is advisory
        'captain': False,                                 # no captain (no x2 scoring)
        'bonus': False,                                   # standings: wins, then points-for
    },
    'ofds': {
        # Premiership: a real rugby-union matchday 23 with strict positions.
        'fr_unit': False,
        'starters': {'PR': 2, 'HK': 1, 'LK': 2, 'LF': 3, 'SH': 1, 'FH': 1, 'MID': 2, 'OBK': 3},  # 15
        'bench':    {'PR': 1, 'HK': 1, 'LK': 1, 'LF': 1, 'SH': 1, 'FH': 1, 'MID': 1, 'OBK': 1},  # 8
        'positioned_bench': True,
        'individual_positions': list(POSITIONS),          # all 8 positions are draftable
        'draft_picks': 23,
        'auto_sub': True,
        'soft': False,
        'captain': True,                                  # one captain, scores x2
        'bonus': True,                                    # league points + bonus points
    },
}

DEFAULT_MODEL = ROSTER_MODELS['meatyboys']

# Human-readable labels for position codes (squad-builder slot headings).
POSITION_LABELS = {
    'PR': 'Prop', 'HK': 'Hooker', 'LK': 'Lock', 'LF': 'Loose Forward',
    'SH': 'Half Back', 'FH': 'Fly Half', 'MID': 'Midfielder', 'OBK': 'Outside Back',
}
# Forward-to-back ordering for displaying a line-up.
POSITION_ORDER = ['PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK']


def roster_model(slug: str) -> dict:
    return ROSTER_MODELS.get(slug, DEFAULT_MODEL)


def model_starter_count(model: dict) -> int:
    return sum(model['starters'].values())


def model_bench_count(model: dict) -> int:
    return sum(model['bench'].values()) if model.get('positioned_bench') else model['bench_count']


def model_draft_picks(model: dict) -> int:
    return model['draft_picks']


def model_individual_positions(model: dict) -> list[str]:
    return model['individual_positions']


def squad_quotas(model: dict) -> dict[str, int]:
    """Per-position total squad size (starters + bench) — what a draft must fill.
    For an any-position bench (meatyboys) only the positioned starters count."""
    q = dict(model['starters'])
    if model.get('positioned_bench'):
        for pos, n in model['bench'].items():
            q[pos] = q.get(pos, 0) + n
    return q


def _fmt_counts(counts: dict[str, int]) -> str:
    return ', '.join(f"{n}x{POSITION_LABELS.get(p, p)}"
                     for p, n in sorted(counts.items(), key=lambda kv: POSITION_ORDER.index(kv[0])))


def validate_roster(selections: list[tuple[str, bool]], model: dict = DEFAULT_MODEL) -> tuple[bool, str | None]:
    """Validate a squad's (position, is_bench) selections against its league model.

    * Strict models (OFDS): the starting XV must hit the exact per-position
      starter counts and the bench must hold exactly one of each position.
    * Soft models (meatyboys): composition is advisory; only the squad size cap
      and the no-individual-front-row rule are enforced.
    """
    if model.get('positioned_bench'):
        starters = Counter(pos for pos, is_bench in selections if not is_bench)
        bench = Counter(pos for pos, is_bench in selections if is_bench)
        want_start, want_bench = Counter(model['starters']), Counter(model['bench'])
        if starters != want_start:
            return False, f"Starting XV must be exactly {_fmt_counts(model['starters'])}."
        if bench != want_bench:
            return False, f"Bench must be exactly one of each: {_fmt_counts(model['bench'])}."
        return True, None

    # Soft (meatyboys) model.
    if model.get('fr_unit') and any(pos in FR_POSITIONS for pos, _ in selections):
        return False, 'Props and hookers are part of the club front-row unit, not the squad.'
    cap = model_starter_count(model) + model_bench_count(model)
    if len(selections) > cap:
        return False, f'A squad can field at most {cap} players (got {len(selections)})'
    return True, None


def starter_minimums() -> list[tuple[frozenset[str], int]]:
    """Mandatory draft minimums: (allowed positions, count) per starting slot.

    A 17-man roster is valid iff it contains players covering every starting
    slot's minimum; the remaining BENCH_COUNT picks are any position.
    """
    return [(frozenset(SLOT_POSITIONS[slot]), count) for slot, count in STARTER_SLOTS.items()]
