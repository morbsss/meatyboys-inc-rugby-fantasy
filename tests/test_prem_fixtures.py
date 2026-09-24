"""Fixtures + club registry sourced from data/prem_fixtures_2026_27.json.

The whole point of this file is that team names from three different sources
(the official feed, SuperBru's `players.team`, ESPN's lineup payloads) must
collapse onto ONE code, or opponents and lineups silently fail to join.
"""

from api import prem_fixtures as pf
from api.datasource.live import LiveAdapter

# The codes SuperBru uses in players.team, and ESPN uses as `abbreviation`.
SUPERBRU_CODES = {'BAT', 'BRI', 'EXE', 'GLO', 'HAR', 'LEI', 'NEW', 'NOR', 'SAL', 'SAR'}


# ---------------------------------------------------------------------------
# Club registry
# ---------------------------------------------------------------------------

def test_every_club_maps_to_a_superbru_code():
    assert set(pf.clubs()) == SUPERBRU_CODES


def test_clubs_carry_team_information():
    bath = pf.club('BAT')
    assert bath['name'] == 'Bath Rugby'
    assert bath['stadium']
    assert bath['colour_dark'].startswith('#')
    assert bath['logo_light'].startswith('https://')


def test_resolve_accepts_code_official_name_and_slug():
    for name in ('LEI', 'lei', 'Leicester Tigers', 'leicester-tigers'):
        assert pf.resolve_team(name) == 'LEI'


def test_resolve_handles_espn_pre_rebrand_names():
    # ESPN still publishes the old club names; they must not become new teams.
    assert pf.resolve_team('Bristol Rugby') == 'BRI'
    assert pf.resolve_team('Bristol Bears') == 'BRI'
    assert pf.resolve_team('Newcastle Falcons') == 'NEW'
    assert pf.resolve_team('Newcastle Red Bulls') == 'NEW'


def test_resolve_accepts_provider_team_id():
    assert pf.resolve_team(pf.club('HAR')['team_id']) == 'HAR'


def test_resolve_returns_none_for_unknown_and_placeholder():
    assert pf.resolve_team('TBC') is None      # play-off slot
    assert pf.resolve_team(None) is None
    assert pf.resolve_team('Nonsense FC') is None


# ---------------------------------------------------------------------------
# Fixture list
# ---------------------------------------------------------------------------

def test_season_has_twenty_rounds_including_knockouts():
    rounds = pf.rounds()
    assert [r['round'] for r in rounds] == list(range(1, 21))
    assert rounds[18]['label'] == 'PO'
    assert rounds[19]['label'] == 'F'


def test_round_window_is_first_to_last_kickoff():
    first, last = pf.round_window(1)
    assert first <= last
    assert first.startswith('2026-09-25')


def test_round_window_unknown_round_is_none():
    assert pf.round_window(99) is None


# ---------------------------------------------------------------------------
# Adapter - fixtures come from the file, so this needs no network
# ---------------------------------------------------------------------------

def test_fetch_rounds_emits_joinable_team_codes():
    rounds = LiveAdapter().fetch_rounds('premiership')
    assert len(rounds) == 20
    teams = {t for r in rounds for m in r.matches for t in (m.home, m.away)}
    # Every fixture team must be a code that players.team can join to.
    assert teams == SUPERBRU_CODES


def test_fetch_rounds_orders_and_windows_each_round():
    for rd in LiveAdapter().fetch_rounds('premiership'):
        assert rd.first_kickoff <= rd.last_kickoff
        for m in rd.matches:
            assert rd.first_kickoff <= m.kickoff <= rd.last_kickoff


def test_fetch_rounds_skips_undecided_playoff_matchups():
    # Rounds 19/20 exist (they set the lockout window) but carry no matches
    # until the regular season decides who is in them.
    knockouts = {r.round_number: r for r in LiveAdapter().fetch_rounds('premiership')
                 if r.round_number >= 19}
    assert knockouts[19].matches == []
    assert knockouts[20].matches == []
    assert knockouts[20].first_kickoff.startswith('2027-06-19')


def test_regular_season_rounds_have_five_matches_each():
    for rd in LiveAdapter().fetch_rounds('premiership'):
        if rd.round_number <= 18:
            assert len(rd.matches) == 5, f'round {rd.round_number}'


def test_no_team_plays_twice_in_a_round():
    for rd in LiveAdapter().fetch_rounds('premiership'):
        playing = [t for m in rd.matches for t in (m.home, m.away)]
        assert len(playing) == len(set(playing)), f'round {rd.round_number}'


def test_unconfigured_competition_returns_no_fixtures():
    assert LiveAdapter().fetch_rounds('super_rugby') == []
