"""League rules, derived from the live engine constants.

Every number on the Rules page comes from the code that actually enforces it —
the roster models in api/leagues.py, the scoring/table constants in
api/competition.py, and the draft engine in api/draft.py. Nothing here is a
hand-typed figure, so changing a rule in the engine changes the published rules
with it. If you find yourself about to hardcode a number in rules.html, add it
here instead and pull it from its source module.

The two leagues play genuinely different games (a flexible squad with a club
front-row UNIT vs a strict matchday 23 with a captain and auto-subs), so the
payload exposes the differences as flags the template branches on rather than
one merged description that would be wrong for both.
"""

from . import competition as comp
from .leagues import (
    LEAGUES, POSITION_LABELS, POSITION_ORDER, model_bench_count,
    model_draft_picks, model_starter_count, roster_model,
)


def _slots(counts: dict) -> list[dict]:
    """Per-position slot counts, ordered forwards-to-backs for display."""
    return [
        {'code': pos, 'label': POSITION_LABELS.get(pos, pos), 'count': counts[pos]}
        for pos in POSITION_ORDER if counts.get(pos)
    ]


def build(slug: str, pick_seconds: int) -> dict:
    """The full rule set for one league.

    `pick_seconds` is passed in rather than imported: the draft clock lives in
    api/index.py, which imports this module, so reaching back for it would be a
    circular import.
    """
    league = LEAGUES[slug]
    model = roster_model(slug)

    starters = _slots(model['starters'])
    bench = _slots(model['bench']) if model.get('positioned_bench') else []
    starter_count = model_starter_count(model)
    bench_count = model_bench_count(model)

    return {
        'slug': slug,
        'league': {
            'name': league['name'],
            'brand': league['brand'],
            'competition': league['comp_name'],
            'timezone': league['timezone'],
        },

        # --- squad shape ---------------------------------------------------
        'squad': {
            'starters': starters,
            'starter_count': starter_count,
            'bench': bench,
            'bench_count': bench_count,
            'squad_size': starter_count + bench_count,
            # OFDS names every bench slot by position; meatyboys' bench is free.
            'positioned_bench': bool(model.get('positioned_bench')),
            # meatyboys owns props/hookers only through a club front-row unit.
            # Pluralised here so the template can join them into prose directly.
            'fr_unit': bool(model.get('fr_unit')),
            'fr_positions': [POSITION_LABELS[p] + 's' for p in ('PR', 'HK')],
            # Composition enforced on save (OFDS) vs advisory (meatyboys).
            'strict': not model.get('soft'),
        },

        # --- draft ---------------------------------------------------------
        'draft': {
            'picks_per_team': model_draft_picks(model),
            'pick_seconds': pick_seconds,
            'snake': True,
            # meatyboys' 16th pick is the optional front-row unit.
            'optional_fr_pick': bool(model.get('fr_unit')),
        },

        # --- scoring -------------------------------------------------------
        'scoring': {
            'captain': bool(model.get('captain')),
            'captain_multiplier': 2,
            'auto_sub': bool(model.get('auto_sub')),
            'fr_unit': bool(model.get('fr_unit')),
        },

        # --- league table --------------------------------------------------
        'table': {
            'bonus': bool(model.get('bonus')),
            'win': comp.WIN_PTS,
            'draw': comp.DRAW_PTS,
            'loss': comp.LOSS_PTS,
            'bp': comp.BP_PTS,
            'winner_bp_margin': comp.WINNER_BP_MARGIN,
            'loser_bp_margin': comp.LOSER_BP_MARGIN,
            # Ranking key differs: league points (OFDS) vs raw wins (meatyboys).
            'rank_by': ('League points, then points for' if model.get('bonus')
                        else 'Wins, then points for'),
        },

        # --- season shape --------------------------------------------------
        'season': {
            'regular_rounds': comp.REGULAR_ROUNDS,
            'semi_leg1': comp.SEMI_LEG1,
            'semi_leg2': comp.SEMI_LEG2,
            'final_round': comp.FINAL_ROUND,
            'total_rounds': comp.TOTAL_ROUNDS,
            'sacko_min_teams': 8,
        },
    }


def build_all(pick_seconds: int) -> dict:
    """Rules for every league, so the page can switch without a round trip."""
    return {slug: build(slug, pick_seconds) for slug in LEAGUES}
