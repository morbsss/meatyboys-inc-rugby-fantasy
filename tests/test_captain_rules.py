"""The captain must be in the starting XV.

Reported by AWOL XV: benching the captain carried the armband to the bench with
him. That is worse than it sounds. Doubling is applied over the EFFECTIVE XV
(competition._ofds_team_score walks effective_lineup and doubles `cap`), and a
benched player is never in it — so the manager did not end up with a captain on
the bench, they ended up with **no captain at all** and no doubling, silently.

Three gates now, because the squad page has several routes to the same state:

  swapSamePosition  benching the captain hands the armband to whoever takes his
                    place — the behaviour asked for
  setCaptain        refuses to pin the armband on a bench player
  save_picks        rejects captain_id ∈ bench_ids, for a stale cached client

The browser-side rules live in api/static/js/squad.js and are asserted here as
contracts rather than re-implemented: there is no JS runner in this suite, and a
grep that fails loudly beats a rule nobody checks.
"""

import os
import re

import pytest

from api import index as idx

SQUAD_JS = os.path.join(idx.app.static_folder, 'js', 'squad.js')


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'cap.db'))
    monkeypatch.setattr(idx, 'is_locked', lambda conn, lid: False)
    monkeypatch.setattr(idx, 'current_league_id', lambda conn: 2)
    c = idx.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1
        s['username'] = 'awol'
        s['team_name'] = 'AWOL XV'
    return c


def _save(client, captain_id, bench_ids):
    return client.post('/api/team/AWOL%20XV/picks', json={
        'player_ids': [1, 2, 3], 'bench_ids': bench_ids,
        'captain_id': captain_id, 'jerseys': {},
    })


# --- the server gate --------------------------------------------------------

def test_a_benched_captain_is_rejected(client):
    r = _save(client, captain_id=1, bench_ids=[1, 2])

    assert r.status_code == 400
    assert 'bench' in r.get_json()['error'].lower()


def test_a_starting_captain_is_not_rejected_for_that_reason(client):
    """It may fail later for other reasons; it must not fail on the captain."""
    r = _save(client, captain_id=3, bench_ids=[1, 2])

    if r.status_code == 400:
        assert 'captain' not in r.get_json().get('error', '').lower()


def test_no_captain_is_not_rejected_as_benched(client):
    """mtyby has no captain at all, so captain_id is absent."""
    r = _save(client, captain_id=None, bench_ids=[1, 2])

    if r.status_code == 400:
        assert 'bench' not in r.get_json().get('error', '').lower()


# --- the browser rules ------------------------------------------------------

def _js():
    return open(SQUAD_JS, encoding='utf-8').read()


def test_the_swap_hands_the_armband_to_the_incoming_player():
    """The reported bug. Without this the badge rides the player to the bench."""
    fn = _js().split('function swapSamePosition(p)')[1].split('\n  }')[0]

    assert 'is_captain' in fn, 'the swap ignores the armband entirely'
    assert 'onField.is_captain = true' in fn
    assert 'onBench.is_captain = false' in fn


def test_the_swap_only_moves_an_armband_that_exists():
    """Swapping two ordinary players must not invent a captain."""
    fn = _js().split('function swapSamePosition(p)')[1].split('\n  }')[0]

    assert re.search(r'if \(p\.is_captain \|\| q\.is_captain\)', fn)


def test_captaining_a_bench_player_is_refused():
    fn = _js().split('function setCaptain(p)')[1].split('\n  }')[0]

    assert 'p.is_bench' in fn and 'return' in fn


def test_the_save_bar_blocks_a_benched_captain():
    """Defence in depth for a squad saved before this fix: it must not be
    saveable again unchanged."""
    fn = _js().split('function positionedValidity(')[1].split('\n  }')[0]

    assert 'captainStarts' in fn
    assert 'captains === 1 && captainStarts' in fn
