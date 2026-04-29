"""Tests for the TOC endpoint."""

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


def test_toc_404_for_missing_book(client):
    r = client.get('/api/books/999999/toc')
    assert r.status_code == 404


def test_toc_returns_structured_payload_for_existing(client):
    # Pull any existing book id from the DB
    list_r = client.get('/api/books')
    if list_r.status_code != 200:
        pytest.skip('no books endpoint')
    books = list_r.get_json()
    if not books:
        pytest.skip('no existing books to test against')

    bid = books[0]['id']
    r = client.get(f'/api/books/{bid}/toc')
    assert r.status_code == 200
    body = r.get_json()
    assert 'source' in body
    assert 'entries' in body
    assert isinstance(body['entries'], list)
    assert body['source'] in ('pdf_outline', 'detected_headings', 'none')
