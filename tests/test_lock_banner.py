"""The lockout countdown in the shared banner.

It rides on /api/auth/user rather than /api/state or an endpoint of its own:
base.js already fetches that on every page for the user chip, and /api/state also
serialises every player in the league - hundreds of rows of JSON to move for a
clock in the header.

Two deadlines are published because the timer counts toward whichever is next:

    locks_at    the round's FIRST kickoff, when picks close league-wide
    reopens_at  the Tuesday 12:00 rollover, when the next round opens

The lock is round-wide and trips at the first kickoff, so a manager holding only
Sunday players is frozen from Friday evening. That is the thing the countdown
exists to make obvious.
"""

import pytest

from api import index as idx

LOCKS_AT = '2026-09-25T18:45:00+00:00'
REOPENS_AT = '2026-09-29T12:00:00+01:00'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'banner.db'))
    monkeypatch.setattr(idx, '_user_context', lambda conn: {
        'user_id': 1, 'team_name': 'PizzaSmith', 'league_id': 2,
        'is_commissioner': False, 'must_change_password': False})
    monkeypatch.setattr(idx, '_rounds_known', lambda conn, lid: True)
    monkeypatch.setattr(idx, 'get_next_round', lambda conn, lid: 1)
    monkeypatch.setattr(idx, 'next_lock_time', lambda conn, rnd, lid: LOCKS_AT)
    monkeypatch.setattr(idx, 'reopen_time', lambda conn, rnd, lid: REOPENS_AT)
    monkeypatch.setattr(idx, 'is_locked', lambda conn, lid: False)
    c = idx.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1
        s['username'] = 'morbsss'
    return c


def _lock(client):
    return client.get('/api/auth/user').get_json()['lock']


def test_the_user_payload_carries_both_deadlines(client):
    lock = _lock(client)

    assert lock == {'is_locked': False, 'locks_at': LOCKS_AT,
                    'reopens_at': REOPENS_AT, 'round': 1}


def test_is_locked_is_reported_from_the_engine(client, monkeypatch):
    monkeypatch.setattr(idx, 'is_locked', lambda conn, lid: True)

    assert _lock(client)['is_locked'] is True


def test_no_lock_without_a_calendar(client, monkeypatch):
    """Pre-season with no rounds: there is no honest deadline, so the banner shows
    nothing rather than counting toward a guess."""
    monkeypatch.setattr(idx, '_rounds_known', lambda conn, lid: False)

    assert _lock(client) is None


def test_no_lock_without_a_league(client, monkeypatch):
    monkeypatch.setattr(idx, '_user_context', lambda conn: None)

    assert _lock(client) is None


def test_the_deadlines_keep_their_timezone_offset(client):
    """The two come from different code paths - one UTC, one league-local BST -
    and the browser parses either, but only while the offset survives the JSON."""
    lock = _lock(client)

    assert lock['locks_at'].endswith('+00:00')
    assert lock['reopens_at'].endswith('+01:00')


# --- banner markup ----------------------------------------------------------

def test_banner_right_side_order(client):
    """pill · timer · round · profile · logout, left to right."""
    import re
    html = client.get('/squad').get_data(as_text=True)
    end = html.split('mtyby-header-end')[1].split('</header>')[0]

    order = re.findall(
        r'id="(lock-pill|lock-timer|header-round|user-chip|logout-btn)"', end)

    assert order == ['lock-pill', 'lock-timer', 'header-round',
                     'user-chip', 'logout-btn']


def test_pill_and_timer_ship_hidden(client):
    """There is nothing honest to show until the lock state has loaded, and a
    placeholder that flickers into a real time reads as a glitch."""
    import re
    html = client.get('/squad').get_data(as_text=True)

    assert re.search(r'id="lock-pill"[^>]*\shidden', html)
    assert re.search(r'id="lock-timer"[^>]*\shidden', html)


def test_the_banner_is_on_every_page(client):
    for path in ('/squad', '/fixtures', '/players', '/competition', '/rules'):
        html = client.get(path).get_data(as_text=True)
        assert 'id="lock-timer"' in html, path


def test_the_squad_page_no_longer_owns_a_second_pill(client):
    """It used to live in the header's centre slot on this page only. Two elements
    with one id, or two writers on one element, is how they drift apart."""
    html = client.get('/squad').get_data(as_text=True)

    assert html.count('id="lock-pill"') == 1
    centre = html.split('mtyby-header-center')[1].split('</div>')[0]
    assert 'lock-pill' not in centre


def test_the_pill_style_is_in_the_shared_stylesheet(client):
    """It is on every page now, so it cannot live in squad.css."""
    base = client.get('/static/css/base.css').get_data(as_text=True)
    squad = client.get('/static/css/squad.css').get_data(as_text=True)

    assert '.mtyby-lock-pill {' in base
    assert '.mtyby-lock-pill {' not in squad


def test_hidden_beats_the_author_display(client):
    """Both carry inline-flex, which would otherwise win over the attribute and
    leave an empty pill sitting in the banner before the fetch returns."""
    base = client.get('/static/css/base.css').get_data(as_text=True)

    assert '.mtyby-lock-pill[hidden]' in base
    assert '.mtyby-lock-timer[hidden]' in base
