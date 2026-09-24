"""Mock and live must identify a club by the same key.

The live adapter resolves every Premiership club through
prem_fixtures.resolve_team, so production stores the canonical 3-letter code:
`players.team = 'BAT'`. The mock seed used to store the display name
('Bath', 'Bristol Bears', ...) instead, which meant a DB built under
DATA_SOURCE=mock described the same clubs under a different key — and everything
keyed on a club quietly found nothing:

  * squad jersey art and club-colour tints, both looked up by code
  * the real-fixtures strip on the fixtures page
  * anything joining players to match_lineups by club

Nothing errored; shirts just fell back to the league's default colour, so it was
only visible if you knew what the page should look like.

Super Rugby is the other way round: no scraped club list, no code vocabulary, so
the display name IS its identifier. These tests pin both halves of that contract.
"""

import pytest

from api import prem_fixtures
from api.datasource import generate_seed as gs
from api.index import _jersey_codes


def _seed_teams(competition):
    """Club identifiers as the seed actually writes them (players + fixtures)."""
    data = gs.build(competition,
                    'ofds' if competition == 'premiership' else 'meatyboys')
    from_players = {p['team'] for p in data['players']}
    from_fixtures = set()
    for rd in data['rounds']:
        for m in rd['matches']:
            from_fixtures.add(m['home'])
            from_fixtures.add(m['away'])
    return from_players, from_fixtures


def test_premiership_seed_identifies_clubs_by_canonical_code():
    from_players, from_fixtures = _seed_teams('premiership')

    assert from_players == set(prem_fixtures.clubs())
    assert from_fixtures == set(prem_fixtures.clubs())


def test_every_seeded_premiership_club_resolves_to_itself():
    """A code that doesn't round-trip through resolve_team isn't canonical."""
    from_players, _ = _seed_teams('premiership')

    for team in sorted(from_players):
        assert prem_fixtures.resolve_team(team) == team


def test_players_and_fixtures_agree_on_the_key():
    """They are joined on this value, so a mismatch breaks lineups silently."""
    for competition in ('premiership', 'super_rugby'):
        from_players, from_fixtures = _seed_teams(competition)

        assert from_players == from_fixtures, competition


def test_seeded_premiership_clubs_all_have_jersey_art():
    """The point of the exercise: the mock squad page can dress every player."""
    from_players, _ = _seed_teams('premiership')
    missing = sorted(from_players - _jersey_codes())

    assert not missing, f'no jersey art for seeded clubs {missing}'


def test_super_rugby_seed_keeps_display_names():
    """It has no code vocabulary — switching it to abbreviations would rename
    every club to something the rest of the app has never heard of."""
    from_players, _ = _seed_teams('super_rugby')

    assert 'Moana Pasifika' in from_players
    assert 'MOA' not in from_players


def test_team_key_is_declared_for_every_seeded_competition():
    assert set(gs.TEAM_KEY) == set(gs.REAL_TEAMS)


@pytest.mark.parametrize('competition', ['premiership', 'super_rugby'])
def test_team_ids_are_unique(competition):
    """A duplicate identifier would merge two clubs' players."""
    ids = gs.team_ids(competition)

    assert len(ids) == len(set(ids))


def test_short_club_names_resolve():
    """'Bath' and 'Gloucester' are the everyday names and what the seed used to
    write, but the club list only carries 'Bath Rugby' / 'Gloucester Rugby' — so
    both resolved to None until they were aliased."""
    assert prem_fixtures.resolve_team('Bath') == 'BAT'
    assert prem_fixtures.resolve_team('Gloucester') == 'GLO'
    assert prem_fixtures.resolve_team('Bath Rugby') == 'BAT'
    assert prem_fixtures.resolve_team('Gloucester Rugby') == 'GLO'
