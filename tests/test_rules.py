"""The Rules page must state what the engine actually does.

These tests bind the published rules to the constants that enforce them, so a
scoring or roster change can't quietly leave the rule book lying. They also
check the two leagues' rules stay separated — publishing OFDS's captain and
auto-sub rules to meatyboys managers (or vice versa) would be worse than
publishing nothing.
"""

import pytest

from api import competition as comp
from api import rules
from api.leagues import LEAGUES, model_bench_count, model_draft_picks, roster_model

PICK_SECONDS = 60


@pytest.fixture(params=sorted(LEAGUES))
def slug(request):
    return request.param


# ---------------------------------------------------------------------------
# Numbers must come from the engine, not from prose
# ---------------------------------------------------------------------------

def test_table_points_match_the_scoring_constants(slug):
    tb = rules.build(slug, PICK_SECONDS)['table']
    assert (tb['win'], tb['draw'], tb['loss'], tb['bp']) == (
        comp.WIN_PTS, comp.DRAW_PTS, comp.LOSS_PTS, comp.BP_PTS)
    assert tb['winner_bp_margin'] == comp.WINNER_BP_MARGIN
    assert tb['loser_bp_margin'] == comp.LOSER_BP_MARGIN


def test_season_shape_matches_the_playoff_constants(slug):
    se = rules.build(slug, PICK_SECONDS)['season']
    assert se['regular_rounds'] == comp.REGULAR_ROUNDS
    assert (se['semi_leg1'], se['semi_leg2']) == (comp.SEMI_LEG1, comp.SEMI_LEG2)
    assert se['final_round'] == comp.FINAL_ROUND
    assert se['total_rounds'] == comp.TOTAL_ROUNDS


def test_draft_picks_match_the_roster_model(slug):
    r = rules.build(slug, PICK_SECONDS)
    assert r['draft']['picks_per_team'] == model_draft_picks(roster_model(slug))
    assert r['draft']['pick_seconds'] == PICK_SECONDS


def test_squad_counts_add_up(slug):
    sq = rules.build(slug, PICK_SECONDS)['squad']
    model = roster_model(slug)
    assert sq['starter_count'] == sum(s['count'] for s in sq['starters'])
    assert sq['bench_count'] == model_bench_count(model)
    assert sq['squad_size'] == sq['starter_count'] + sq['bench_count']
    if sq['positioned_bench']:
        assert sq['bench_count'] == sum(b['count'] for b in sq['bench'])


# ---------------------------------------------------------------------------
# The two leagues genuinely differ — the payload must say so
# ---------------------------------------------------------------------------

def test_ofds_is_a_strict_matchday_23_with_captain_and_autosub():
    r = rules.build('ofds', PICK_SECONDS)
    assert (r['squad']['starter_count'], r['squad']['bench_count']) == (15, 8)
    assert r['squad']['squad_size'] == 23 == r['draft']['picks_per_team']
    assert r['squad']['positioned_bench'] and r['squad']['strict']
    assert r['scoring']['captain'] and r['scoring']['auto_sub']
    assert not r['squad']['fr_unit']
    assert r['table']['bonus']


def test_meatyboys_uses_a_front_row_unit_and_no_captain():
    r = rules.build('meatyboys', PICK_SECONDS)
    assert (r['squad']['starter_count'], r['squad']['bench_count']) == (10, 5)
    assert r['squad']['fr_unit'] and r['draft']['optional_fr_pick']
    # 15 individuals + one optional front-row unit pick.
    assert r['draft']['picks_per_team'] == r['squad']['squad_size'] + 1
    assert not r['scoring']['captain'] and not r['scoring']['auto_sub']
    assert not r['squad']['positioned_bench'] and not r['squad']['strict']
    assert not r['table']['bonus']


def test_ranking_rule_follows_whether_the_league_awards_bonus_points(slug):
    r = rules.build(slug, PICK_SECONDS)
    expected = 'league points' if r['table']['bonus'] else 'wins'
    assert r['table']['rank_by'].lower().startswith(expected)


def test_front_row_positions_are_plural_for_prose(slug):
    # The template joins these straight into a sentence.
    assert rules.build(slug, PICK_SECONDS)['squad']['fr_positions'] == ['Props', 'Hookers']


def test_build_all_covers_every_league():
    assert set(rules.build_all(PICK_SECONDS)) == set(LEAGUES)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render(slug):
    from api.index import app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['league_slug'] = slug
    resp = client.get('/rules')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_rules_page_requires_login():
    from api.index import app
    resp = app.test_client().get('/rules')
    assert resp.status_code == 302
    assert '/auth' in resp.headers['Location']


def test_page_renders_for_every_league(slug):
    html = _render(slug)
    assert LEAGUES[slug]['name'] in html
    # No unrendered template syntax or leaked undefined values.
    for bad in ('{{', '{%', 'Undefined'):
        assert bad not in html


def test_ofds_page_shows_captain_autosub_and_bonus_points():
    html = _render('ofds')
    assert 'Captain scores double' in html
    assert 'Auto-substitution' in html
    assert f'win by {comp.WINNER_BP_MARGIN}+' in html
    assert 'Like-for-like' in html
    # Rules that belong to the other league must not appear.
    assert 'front-row unit' not in html


def test_meatyboys_page_shows_front_row_unit_and_hides_ofds_rules():
    html = _render('meatyboys')
    assert 'front-row unit' in html
    assert 'Props and Hookers' in html
    assert 'no league points and no bonus points' in html
    assert 'Captain scores double' not in html
    assert 'Auto-substitution' not in html
