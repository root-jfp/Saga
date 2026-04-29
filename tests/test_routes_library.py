"""Integration tests for the library/search/continue/heartbeat routes.

These spin up Flask in test mode against the real Postgres so we exercise the
actual SQL. Tests create a throwaway user + book + progress row and clean up.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def temp_user(client):
    r = client.post('/api/users', json={'name': f'pytest-lib-{os.getpid()}', 'avatar': '🧪'})
    assert r.status_code == 201, r.data
    user = r.get_json()
    yield user
    client.delete(f'/api/users/{user["id"]}')


def test_continue_listening_requires_user_id(client):
    r = client.get('/api/library/continue')
    assert r.status_code == 400


def test_continue_listening_returns_array_when_no_progress(client, temp_user):
    r = client.get(f'/api/library/continue?user_id={temp_user["id"]}')
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_search_requires_user_id(client):
    r = client.get('/api/library/search')
    assert r.status_code == 400


def test_search_rejects_invalid_sort(client, temp_user):
    r = client.get(f'/api/library/search?user_id={temp_user["id"]}&sort=bogus')
    assert r.status_code == 400


def test_search_accepts_all_valid_sorts(client, temp_user):
    for s in ('recent', 'created', 'title', 'author', 'progress'):
        r = client.get(f'/api/library/search?user_id={temp_user["id"]}&sort={s}')
        assert r.status_code == 200, f'{s}: {r.data}'


def test_search_filters_by_language(client, temp_user):
    r = client.get(f'/api/library/search?user_id={temp_user["id"]}&language=en')
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_status_endpoint_rejects_bad_value(client):
    r = client.put('/api/books/999999/status', json={'status': 'wibble'})
    assert r.status_code == 400


def test_status_endpoint_404_for_missing_book(client):
    r = client.put('/api/books/999999/status', json={'status': 'finished'})
    assert r.status_code == 404


def test_stats_returns_window(client, temp_user):
    r = client.get(f'/api/library/stats?user_id={temp_user["id"]}&days=7')
    assert r.status_code == 200
    body = r.get_json()
    for key in ('reading', 'finished', 'archived', 'total',
                'window_days', 'seconds_listened', 'pages_read',
                'active_days', 'timeline'):
        assert key in body
    assert body['window_days'] == 7


def test_heartbeat_noop_with_zero_seconds(client, temp_user):
    r = client.post('/api/library/heartbeat', json={
        'user_id': temp_user['id'], 'book_id': 1, 'seconds': 0
    })
    assert r.status_code == 200
    assert r.get_json().get('noop') is True
