"""Monte Carlo win probabilities: independent sampling, not correlated sums.

The model used to build a team's score distribution by summing its players'
percentile curves elementwise. That asserts every player in a team lands on the
same percentile at once — a team's p99 was all fifteen players having their best
game simultaneously. Treating within-team scores as perfectly correlated wildly
overstates the spread of the total, and the spread is what a win probability is
made of, so every fixture was dragged toward 50/50.

Measured on the mock season, one 15-player team: the old model gave a total with
mean 116.9 and standard deviation 117.6 — as uncertain as it was large.
Independent sampling gives the same mean with sd 48.0.
"""

import numpy as np
import pytest

from api.predict import simulate_totals, win_draw_pct


def _flat(value, n_players):
    """n players who always score exactly `value` — zero variance."""
    return np.full((n_players, 100), float(value))


def _spread(low, high, n_players):
    """n identical players whose percentile curve runs low..high."""
    return np.tile(np.linspace(low, high, 100), (n_players, 1))


def _rng(seed=7):
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# The correlation fix
# ---------------------------------------------------------------------------

def test_independent_sampling_narrows_the_total_vs_a_correlated_sum():
    """The whole point of Phase 3: fifteen players do not all peak at once."""
    curves = _spread(0, 20, 15)

    correlated = curves.sum(axis=0)                  # the old model
    independent = simulate_totals(curves, _rng(), draws=20000)

    assert independent.mean() == pytest.approx(correlated.mean(), rel=0.02)
    assert independent.std() < correlated.std() / 2, (
        'summing percentile curves should overstate spread by more than 2x')


def test_more_players_concentrate_the_total_further():
    """Independent sums concentrate as n grows; a correlated sum never does."""
    def rel_spread(n):
        t = simulate_totals(_spread(0, 20, n), _rng(), draws=20000)
        return t.std() / t.mean()

    assert rel_spread(20) < rel_spread(5)


def test_zero_variance_players_give_a_fixed_total():
    totals = simulate_totals(_flat(10, 15), _rng(), draws=500)
    assert np.all(totals == 150.0)


def test_totals_are_rounded_to_one_decimal():
    """Fantasy points are recorded to 1dp; without rounding, exact draws are
    impossible and draw_prob is permanently 0."""
    totals = simulate_totals(_spread(0.001, 19.999, 8), _rng(), draws=200)
    assert np.all(np.round(totals, 1) == totals)


def test_draws_shape_is_respected():
    assert simulate_totals(_spread(0, 10, 4), _rng(), draws=123).shape == (123,)


# ---------------------------------------------------------------------------
# Determinism — a published probability must not jitter between runs
# ---------------------------------------------------------------------------

def test_same_seed_gives_identical_totals():
    curves = _spread(0, 20, 15)
    a = simulate_totals(curves, _rng(99), draws=1000)
    b = simulate_totals(curves, _rng(99), draws=1000)
    assert np.array_equal(a, b)


def test_different_seeds_give_different_totals():
    curves = _spread(0, 20, 15)
    a = simulate_totals(curves, _rng(1), draws=1000)
    b = simulate_totals(curves, _rng(2), draws=1000)
    assert not np.array_equal(a, b)


# ---------------------------------------------------------------------------
# Turning totals into probabilities
# ---------------------------------------------------------------------------

def test_a_strictly_stronger_team_always_wins():
    home, draw = win_draw_pct(np.array([100.0] * 50), np.array([50.0] * 50))
    assert home == 100.0 and draw == 0.0


def test_a_strictly_weaker_team_never_wins():
    home, draw = win_draw_pct(np.array([10.0] * 50), np.array([90.0] * 50))
    assert home == 0.0 and draw == 0.0


def test_identical_totals_are_all_draws():
    home, draw = win_draw_pct(np.array([70.0] * 40), np.array([70.0] * 40))
    assert home == 0.0 and draw == 100.0


def test_evenly_matched_teams_land_near_fifty():
    curves = _spread(0, 20, 15)
    h = simulate_totals(curves, _rng(11), draws=4000)
    a = simulate_totals(curves, _rng(12), draws=4000)
    home, draw = win_draw_pct(h, a)
    assert 45 <= home <= 55, f'expected ~50%, got {home}'


def test_probabilities_are_exhaustive():
    """home + away + draw must account for every simulated pair."""
    h = simulate_totals(_spread(0, 20, 10), _rng(3), draws=800)
    a = simulate_totals(_spread(2, 18, 10), _rng(4), draws=800)
    home, draw = win_draw_pct(h, a)
    away = 100 - home - draw
    assert home + away + draw == pytest.approx(100.0)
    assert 0 <= away <= 100


def test_counting_matches_a_brute_force_cross_join():
    """The sort/searchsorted shortcut must equal the naive O(n^2) comparison."""
    h = np.array([1.0, 2.0, 3.0, 3.0])
    a = np.array([2.0, 3.0, 5.0])
    home, draw = win_draw_pct(h, a)
    diff = h[:, None] - a[None, :]
    assert home == pytest.approx((diff > 0).sum() / diff.size * 100)
    assert draw == pytest.approx((diff == 0).sum() / diff.size * 100)
