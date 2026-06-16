"""Snake-draft engine + per-league roster validity (meatyboys FR-unit model +
OFDS strict full-XV 23 model)."""

from collections import Counter

from api import draft as d
from api.leagues import (
    validate_roster, roster_model, model_draft_picks, squad_quotas,
    ROSTER_SIZE, BENCH_COUNT, STARTER_COUNT,
)

MB = roster_model('meatyboys')
OFDS = roster_model('ofds')


# ---------------------------------------------------------------------------
# Snake order (model-driven pick counts)
# ---------------------------------------------------------------------------

def test_snake_sequence_reverses_each_round():
    assert d.snake_sequence(['A', 'B', 'C'], {'draft_picks': 2}) == ['A', 'B', 'C', 'C', 'B', 'A']


def test_team_on_clock():
    order = ['A', 'B', 'C']
    assert d.team_on_clock(order, 1, MB) == 'A'
    assert d.team_on_clock(order, 4, MB) == 'C'   # snake turn
    assert d.team_on_clock(order, 6, MB) == 'A'


def test_total_picks_per_model():
    order = ['A', 'B', 'C']
    assert model_draft_picks(MB) == 16 and model_draft_picks(OFDS) == 23
    assert d.total_picks(order, MB) == 3 * 16
    assert d.total_picks(order, OFDS) == 3 * 23


# ---------------------------------------------------------------------------
# meatyboys (soft) roster validity: 10 starters + 5 bench, FR unit separate
# ---------------------------------------------------------------------------

MB_STARTERS = ['LK', 'LF', 'LF', 'SH', 'FH', 'MID', 'MID', 'OBK', 'OBK', 'OBK']
MB_BENCH = ['LK', 'LF', 'MID', 'OBK', 'OBK']


def test_meatyboys_constants():
    assert STARTER_COUNT == 10 and BENCH_COUNT == 5 and ROSTER_SIZE == 15


def test_meatyboys_accepts_legal_15():
    sel = [(p, False) for p in MB_STARTERS] + [(p, True) for p in MB_BENCH]
    assert validate_roster(sel, MB) == (True, None)


def test_meatyboys_rejects_props_or_hookers():
    sel = [(p, False) for p in (['PR'] + MB_STARTERS[1:])] + [(p, True) for p in MB_BENCH]
    assert validate_roster(sel, MB)[0] is False


def test_meatyboys_soft_allows_unbalanced():
    sel = [('OBK', False)] * 10 + [(p, True) for p in MB_BENCH[:-1]]   # odd bench split
    assert validate_roster(sel, MB)[0] is True


def test_meatyboys_rejects_oversized():
    assert validate_roster([('OBK', False)] * (ROSTER_SIZE + 1), MB)[0] is False


# ---------------------------------------------------------------------------
# OFDS (strict) roster validity: exact 15 + 8 by position
# ---------------------------------------------------------------------------

OFDS_STARTERS = (['PR', 'PR', 'HK', 'LK', 'LK', 'LF', 'LF', 'LF', 'SH', 'FH',
                  'MID', 'MID', 'OBK', 'OBK', 'OBK'])                       # 15
OFDS_BENCH = ['PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK']            # 8


def test_ofds_accepts_legal_23():
    sel = [(p, False) for p in OFDS_STARTERS] + [(p, True) for p in OFDS_BENCH]
    assert validate_roster(sel, OFDS) == (True, None)


def test_ofds_rejects_wrong_starter_positions():
    bad = OFDS_STARTERS[:-1] + ['PR']        # drop an OBK, add a 3rd PR starter
    sel = [(p, False) for p in bad] + [(p, True) for p in OFDS_BENCH]
    assert validate_roster(sel, OFDS)[0] is False


def test_ofds_rejects_wrong_bench():
    bad_bench = OFDS_BENCH[:-1] + ['PR']     # 2 PR on bench, no OBK
    sel = [(p, False) for p in OFDS_STARTERS] + [(p, True) for p in bad_bench]
    assert validate_roster(sel, OFDS)[0] is False


# ---------------------------------------------------------------------------
# Auto-draft fills the model's position quotas
# ---------------------------------------------------------------------------

def _pool(model):
    pool, pid = [], 0
    for pos, n in squad_quotas(model).items():
        for _ in range(n + 3):
            pool.append({'id': pid, 'position': pos, 'rank': 100 - pid}); pid += 1
    return pool


def test_auto_draft_ofds_fills_exact_quota():
    avail = {p['id']: p for p in _pool(OFDS)}
    owned = []
    for _ in range(model_draft_picks(OFDS)):          # 23
        pick = d.auto_pick(list(avail.values()), [p['position'] for p in owned], OFDS)
        assert pick and pick['type'] == 'player'
        owned.append(avail.pop(pick['player']['id']))
    assert Counter(p['position'] for p in owned) == Counter(squad_quotas(OFDS))
    starters, bench = d.choose_starting_xi(owned, OFDS)
    assert len(starters) == 15 and len(bench) == 8
    sel = [(p['position'], False) for p in starters] + [(p['position'], True) for p in bench]
    assert validate_roster(sel, OFDS)[0]


def test_auto_draft_meatyboys_individuals_only():
    avail = {p['id']: p for p in _pool(MB)}
    owned = []
    for _ in range(model_draft_picks(MB)):            # 16
        pick = d.auto_pick(list(avail.values()), [p['position'] for p in owned], MB)
        assert pick and pick['type'] == 'player'      # FR unit never auto-drafted
        owned.append(avail.pop(pick['player']['id']))
    assert len(owned) == 16
    starters, bench = d.choose_starting_xi(owned, MB)
    bench = bench[:BENCH_COUNT]
    assert len(starters) == STARTER_COUNT and len(bench) == BENCH_COUNT
    sel = [(p['position'], False) for p in starters] + [(p['position'], True) for p in bench]
    assert validate_roster(sel, MB)[0]


def test_unmet_needs_per_model():
    ofds = d.unmet_needs(['OBK', 'OBK', 'OBK', 'FH'], OFDS)
    assert ofds['OBK'] == 1 and ofds['FH'] == 1 and ofds['PR'] == 3   # quotas 4 / 2 / 3
    mb = d.unmet_needs(['OBK', 'OBK', 'OBK', 'FH'], MB)
    assert mb['OBK'] == 0 and mb['FH'] == 0 and mb['MID'] == 2 and 'PR' not in mb
