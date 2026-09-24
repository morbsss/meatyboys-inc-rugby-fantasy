"""Who can see /observability.

Gated on the ACCOUNT, not on a league role. A commissioner is a manager who
volunteered for fixtures and password resets; this page exposes job internals,
failure messages and row counts, which is operator information. So access is the
named maintainer account and nobody else - the commissioner included.

404, not 403: the page is meant to be invisible, and a 403 confirms it exists.
"""

import pytest

from api import index as idx

MAINTAINER = 'morbsss'
OTHER = 'olicoe'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'access.db'))
    return idx.app.test_client()


def _as(client, username, user_id=1):
    with client.session_transaction() as s:
        s['user_id'] = user_id
        s['username'] = username
    return client


@pytest.mark.parametrize('url', ['/observability', '/api/observability'])
def test_signed_out_is_sent_to_login(client, url):
    r = client.get(url)

    assert r.status_code in (302, 401)


@pytest.mark.parametrize('url', ['/observability', '/api/observability'])
def test_the_maintainer_gets_in(client, url):
    r = _as(client, MAINTAINER).get(url)

    assert r.status_code == 200


@pytest.mark.parametrize('url', ['/observability', '/api/observability'])
def test_any_other_account_gets_a_404(client, url):
    """Not 403 - that would confirm the page exists."""
    r = _as(client, OTHER).get(url)

    assert r.status_code == 404


def test_the_commissioner_is_not_special(client, monkeypatch):
    """The old gate was the commissioner role. It is explicitly NOT enough now."""
    monkeypatch.setattr(idx, '_user_context',
                        lambda conn: {'user_id': 1, 'team_name': 'x', 'league_id': 2,
                                      'is_commissioner': True,
                                      'must_change_password': False})

    r = _as(client, OTHER).get('/api/observability')

    assert r.status_code == 404


def test_the_username_match_ignores_case_and_padding(client):
    for name in ('MORBSSS', 'Morbsss', '  morbsss  '):
        r = _as(client, name).get('/api/observability')
        assert r.status_code == 200, name


def test_a_similar_username_is_not_accepted(client):
    for name in ('morbss', 'morbssss', 'morbsss2', 'xmorbsss'):
        r = _as(client, name).get('/api/observability')
        assert r.status_code == 404, name


def test_the_allowlist_is_configurable(monkeypatch, tmp_path):
    """So a second maintainer doesn't need a code change."""
    monkeypatch.setattr(idx, 'OBSERVABILITY_USERS', {'someone-else'})
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'cfg.db'))
    client = idx.app.test_client()

    assert _as(client, 'someone-else').get('/api/observability').status_code == 200
    assert _as(client, MAINTAINER).get('/api/observability').status_code == 404


def test_the_user_payload_advertises_the_page_only_to_the_maintainer(client):
    """base.js reveals the nav entry from this flag; everyone else never sees it."""
    yes = _as(client, MAINTAINER).get('/api/auth/user')
    assert yes.get_json()['is_maintainer'] is True

    no = _as(client, OTHER).get('/api/auth/user')
    assert no.get_json()['is_maintainer'] is False


def test_the_nav_entry_ships_hidden(client):
    """Defence in depth: even if the flag never arrives, the markup is hidden and
    the route 404s, so the page is not discoverable from the UI."""
    html = _as(client, OTHER).get('/squad').get_data(as_text=True)

    assert 'data-maintainer-only' in html
    assert 'hidden' in html.split('data-maintainer-only')[1][:40]
