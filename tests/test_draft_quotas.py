"""Draft picks must respect the squad's positional quotas.

OFDS drafts exactly 23 players into 23 fixed slots, so every pick has to fill a
slot that is still open. The manual pick path used to check only that a player
was undrafted, so a manager could take a third hooker while still short a prop.
The draft completed "successfully", then _finalize_draft quietly dropped the
surplus player because there was nowhere to field him - leaving that manager 22
players with nothing to explain it.

That is exactly what happened to Smith's Pizza in the 2026-27 draft, and
`test_reproduces_smiths_pizza` pins the real squad that caused it.
"""

from api import index as idx
from api.leagues import roster_model

OFDS = roster_model('ofds')            # strict: 15 starters + positioned 8 bench
MTYBY = roster_model('meatyboys')      # soft: composition advisory


def _owned(**counts):
    """['PR','PR','HK',...] from PR=2, HK=1, ..."""
    return [pos for pos, n in counts.items() for _ in range(n)]


# ---------------------------------------------------------------------------
# The real failure
# ---------------------------------------------------------------------------

def test_reproduces_smiths_pizza():
    """Smith's Pizza at pick 22 of 23: 21 owned, short a PR and a LK.

    They took a 3rd HK (Clare,C). HK was already full at 2, so the last pick
    could only cover one of the two gaps and the squad ended a prop short.
    """
    owned = _owned(FH=2, MID=3, LF=4, OBK=4, HK=2, SH=2, PR=2, LK=2)
    assert len(owned) == 21

    blocked = idx._quota_blocked('HK', owned, OFDS, has_fr=False)
    assert blocked is not None, 'a 3rd hooker here must be refused'
    assert 'Hooker' in blocked
    assert 'Prop' in blocked and 'Lock' in blocked      # names what is still needed

    # The picks they should have been allowed are exactly the two gaps.
    assert idx._quota_blocked('PR', owned, OFDS, has_fr=False) is None
    assert idx._quota_blocked('LK', owned, OFDS, has_fr=False) is None


def test_every_other_position_is_blocked_at_that_moment():
    owned = _owned(FH=2, MID=3, LF=4, OBK=4, HK=2, SH=2, PR=2, LK=2)
    allowed = [p for p in ('PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK')
               if idx._quota_blocked(p, owned, OFDS, has_fr=False) is None]
    assert sorted(allowed) == ['LK', 'PR']


# ---------------------------------------------------------------------------
# General behaviour
# ---------------------------------------------------------------------------

def test_empty_squad_accepts_any_position():
    for pos in ('PR', 'HK', 'LK', 'LF', 'SH', 'FH', 'MID', 'OBK'):
        assert idx._quota_blocked(pos, [], OFDS, has_fr=False) is None


def test_full_position_is_blocked_even_early():
    """OFDS quotas sum to exactly the pick count, so an overfill is never
    affordable - not even on pick 3."""
    owned = _owned(SH=2)                    # SH quota is 2: now full
    assert idx._quota_blocked('SH', owned, OFDS, has_fr=False) is not None
    assert idx._quota_blocked('PR', owned, OFDS, has_fr=False) is None


def test_last_pick_must_fill_the_last_gap():
    owned = _owned(FH=2, MID=3, LF=4, OBK=4, HK=2, SH=2, PR=2, LK=3)
    assert len(owned) == 22
    assert idx._quota_blocked('PR', owned, OFDS, has_fr=False) is None
    assert idx._quota_blocked('OBK', owned, OFDS, has_fr=False) is not None


def test_complete_squad_blocks_everything():
    owned = _owned(FH=2, MID=3, LF=4, OBK=4, HK=2, SH=2, PR=3, LK=3)
    assert len(owned) == 23
    assert idx._quota_blocked('PR', owned, OFDS, has_fr=False) is not None


def test_soft_model_is_exempt():
    """meatyboys composition is advisory - never block a pick there."""
    owned = _owned(SH=6, FH=6)
    for pos in ('SH', 'FH', 'PR', 'OBK'):
        assert idx._quota_blocked(pos, owned, MTYBY, has_fr=False) is None


def test_front_row_unit_counts_against_the_pick_budget():
    """A drafted FR unit consumed a pick, so fewer remain for individuals."""
    owned = _owned(SH=1)
    assert idx._quota_blocked('SH', owned, OFDS, has_fr=True) is None    # SH quota 2
    # Saturate SH, then the FR unit makes the surplus unaffordable.
    owned = _owned(SH=2)
    assert idx._quota_blocked('SH', owned, OFDS, has_fr=True) is not None


# ---------------------------------------------------------------------------
# Auto-pick was always correct - guard against regressing the two paths apart
# ---------------------------------------------------------------------------

def test_autopick_never_produces_a_blocked_position():
    from api import draft as draft_engine

    owned = _owned(FH=2, MID=3, LF=4, OBK=4, HK=2, SH=2, PR=2, LK=2)
    available = [{'id': i, 'name': f'P{i}', 'position': pos, 'rank': 100 - i}
                 for i, pos in enumerate(['HK', 'OBK', 'PR', 'LK', 'MID'])]
    pick = draft_engine.auto_pick(available, owned, OFDS)
    assert pick and pick['type'] == 'player'
    assert idx._quota_blocked(pick['player']['position'], owned, OFDS, has_fr=False) is None
