"""Matching an ESPN team-sheet name to the SuperBru player it refers to.

SuperBru is the source of truth for identity, and it lengthens the initial to
separate players who would collide at one club. ESPN doesn't, so the formatted
names disagree for exactly the players most likely to matter — the ones with a
namesake in the same squad:

    ESPN 'Arthur Griffin' -> Griffin,A   SuperBru: Griffin,Ar  (and Griffin,Al)
    ESPN 'Tom Curry'      -> Curry,T     SuperBru: Curry,TM    (and Curry,B)
    ESPN 'Oscar Williams' -> Williams,O  SuperBru: Williams,Os (and ,N and ,J)

All three collisions below are real rows from the production player list. The
Griffin case is the one that broke live: BOTH Bath Griffins are props, so
position cannot separate them and only the forename can.

The rule that matters most is the last one — when nothing distinguishes two
namesakes, resolve returns None. A wrong match silently scores the wrong player,
which is worse than a logged miss.
"""

import pytest

from api import player_match as pm

BATH = [
    {'name': 'Griffin,Al', 'position': 'PR'},
    {'name': 'Griffin,Ar', 'position': 'PR'},
    {'name': 'Griffin,C', 'position': 'OBK'},
    {'name': 'Obano,B', 'position': 'PR'},
    {'name': 'du Toit,T', 'position': 'PR'},
]
SALE = [
    {'name': 'Curry,B', 'position': 'LF'},
    {'name': 'Curry,TM', 'position': 'LF'},
]
BRISTOL = [
    {'name': 'Williams,Os', 'position': 'OBK'},
    {'name': 'Williams,N', 'position': 'LF'},
    {'name': 'Williams,J', 'position': 'HK'},
]


def test_exact_match_is_used_when_the_names_already_agree():
    row, rule = pm.resolve('Obano,B', 'Beno Obano', BATH)

    assert row['name'] == 'Obano,B'
    assert rule == pm.EXACT


def test_forename_prefix_resolves_the_bath_griffins():
    """The live failure. 'Griffin,A' matched neither spelling, so a named player
    was written to match_lineups under a name nothing joins to."""
    ar, rule = pm.resolve('Griffin,A', 'Arthur Griffin', BATH)
    al, _ = pm.resolve('Griffin,A', 'Alfie Griffin', BATH)

    assert ar['name'] == 'Griffin,Ar'
    assert al['name'] == 'Griffin,Al'
    assert rule == pm.FORENAME_PREFIX


def test_position_cannot_resolve_the_griffins():
    """Both are props — proof the forename is doing the work, not the jersey."""
    assert {c['position'] for c in BATH if c['name'].startswith('Griffin,A')} == {'PR'}


def test_first_letter_resolves_multi_initial_spellings():
    """'Curry,TM' is initials, not a prefix of 'Tom' — no prefix rule can match
    it, so the first letter has to, and only because 'Curry,B' rules itself out."""
    row, rule = pm.resolve('Curry,T', 'Tom Curry', SALE)

    assert row['name'] == 'Curry,TM'
    assert rule == pm.FIRST_LETTER


def test_ben_curry_still_matches_exactly():
    row, rule = pm.resolve('Curry,B', 'Ben Curry', SALE)

    assert row['name'] == 'Curry,B'
    assert rule == pm.EXACT


def test_prefix_beats_first_letter():
    """Oscar Williams: 'Os' is a prefix of 'Oscar'. The other Williamses start
    with different letters, but the prefix rule should settle it first."""
    row, rule = pm.resolve('Williams,O', 'Oscar Williams', BRISTOL)

    assert row['name'] == 'Williams,Os'
    assert rule == pm.FORENAME_PREFIX


def test_sole_surname_matches_even_when_the_initial_differs():
    """One Obano at the club, so the initial cannot be carrying information."""
    roster = [{'name': 'Obano,B', 'position': 'PR'}]

    row, rule = pm.resolve('Obano,X', 'Beno Obano', roster)

    assert row['name'] == 'Obano,B'
    assert rule == pm.SOLE_SURNAME


def test_particles_in_surnames_survive():
    row, _ = pm.resolve('du Toit,T', 'Thomas du Toit', BATH)

    assert row['name'] == 'du Toit,T'


def test_ambiguous_namesakes_resolve_to_nothing():
    """No forename to go on: 'A. Griffin' fits both props. A guess here writes
    someone else's points to a manager's squad."""
    row, rule = pm.resolve('Griffin,A', 'A. Griffin', BATH,
                           position=pm.position_for_jersey(1))

    assert row is None and rule is None


def test_unknown_surname_resolves_to_nothing():
    row, _ = pm.resolve('Nobody,X', 'Someone Nobody', BATH)

    assert row is None


def test_empty_roster_resolves_to_nothing():
    assert pm.resolve('Griffin,A', 'Arthur Griffin', []) == (None, None)


def test_position_breaks_a_tie_only_alongside_a_name_rule():
    """Two same-surname players share a first letter; the jersey separates them."""
    roster = [{'name': 'Smith,Ja', 'position': 'HK'},
              {'name': 'Smith,Jo', 'position': 'SH'}]

    row, _ = pm.resolve('Smith,J', 'J Smith', roster,
                        position=pm.position_for_jersey(9))

    assert row['name'] == 'Smith,Jo'


# --- shirt numbers ----------------------------------------------------------

@pytest.mark.parametrize('jersey,pos', [
    (1, 'PR'), (2, 'HK'), (3, 'PR'), (4, 'LK'), (5, 'LK'), (6, 'LF'),
    (8, 'LF'), (9, 'SH'), (10, 'FH'), (12, 'MID'), (13, 'MID'), (15, 'OBK'),
])
def test_starting_shirt_numbers_give_a_position(jersey, pos):
    assert pm.position_for_jersey(jersey) == pos


@pytest.mark.parametrize('jersey', [16, 17, 18, 20, 23, 0, 99, None, '', 'x'])
def test_bench_and_junk_numbers_give_no_position(jersey):
    """16-23 are convention, not law, and replacements cover two positions — a
    guess here would be used as a tiebreaker and could pick the wrong player."""
    assert pm.position_for_jersey(jersey) is None


def test_split_formatted():
    assert pm.split_formatted('Griffin,Ar') == ('Griffin', 'Ar')
    assert pm.split_formatted('du Toit,T') == ('du Toit', 'T')
    assert pm.split_formatted('Nocomma') == ('Nocomma', '')


def test_forename_of():
    assert pm.forename_of('Arthur Griffin') == 'Arthur'
    assert pm.forename_of('Ernst van Rhyn') == 'Ernst'
    assert pm.forename_of('Mononym') == ''
