"""
Tests for Phase 3: API exposure of detected_language + recommended_voice_id
and the updated /api/tts/voices endpoint.

RED phase: written before implementation.
Run with: pytest tests/test_routes_voices.py -v
"""

import sys
import os
import json
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# We test Flask routes in isolation by creating a minimal test app.

from flask import Flask
import importlib


def _make_test_app():
    """Create a minimal Flask test app with books blueprint registered."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    from routes import register_blueprints
    register_blueprints(app)

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book_row(book_id=1, detected_language='en'):
    """Build a minimal book dict as returned by row_to_dict."""
    return {
        'id': book_id,
        'user_id': 1,
        'title': 'Test Book',
        'author': 'Author',
        'filename': 'test.pdf',
        'storage_path': '/tmp/test.pdf',
        'cover_image_path': None,
        'total_pages': 10,
        'file_size_bytes': 1024,
        'is_scanned': False,
        'upload_status': 'ready',
        'processing_error': None,
        'audio_generation_status': 'pending',
        'audio_pages_completed': 0,
        'audio_generation_started_at': None,
        'audio_generation_completed_at': None,
        'audio_voice_settings_hash': None,
        'detected_language': detected_language,
        'created_at': None,
        # Progress fields (from JOIN)
        'current_page': None,
        'current_sentence': None,
        'playback_speed': None,
        'total_time_read_seconds': None,
    }


def _get_book_response(detected_language):
    """
    Helper: run GET /api/books/1 with mocked DB and return the JSON response dict.

    Patches at the routes.books level so the route function uses our mock.
    """
    app = _make_test_app()
    book_data = _make_book_row(detected_language=detected_language)

    mock_conn = mock.MagicMock()
    mock_cur = mock.MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.description = [(col,) for col in book_data.keys()]
    mock_cur.fetchone.return_value = tuple(book_data.values())

    with app.test_client() as client:
        with mock.patch('routes.books.get_db_connection', return_value=mock_conn), \
             mock.patch('routes.books.release_connection'), \
             mock.patch('routes.books.row_to_dict', return_value=book_data):
            resp = client.get('/api/books/1')

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data}"
    return resp.get_json()


# ---------------------------------------------------------------------------
# GET /api/books/<id> — detected_language + recommended_voice_id
# ---------------------------------------------------------------------------

class TestGetBookIncludesLanguageFields:
    """GET /api/books/<id> must return detected_language and recommended_voice_id."""

    def test_get_book_includes_detected_language(self):
        """Response JSON must contain 'detected_language' key."""
        data = _get_book_response('en')
        assert 'detected_language' in data, (
            f"'detected_language' missing from response: {list(data.keys())}"
        )

    def test_get_book_includes_recommended_voice_id(self):
        """Response JSON must contain 'recommended_voice_id' key."""
        data = _get_book_response('en')
        assert 'recommended_voice_id' in data, (
            f"'recommended_voice_id' missing from response: {list(data.keys())}"
        )

    def test_recommended_voice_id_for_english_book(self):
        """detected_language='en' should yield a recommended voice starting with 'en-'."""
        data = _get_book_response('en')
        rec = data.get('recommended_voice_id', '')
        assert rec.startswith('en-'), (
            f"Expected en- voice for English book, got {rec!r}"
        )

    def test_recommended_voice_id_for_portuguese_book(self):
        """detected_language='pt' should yield a pt-BR voice."""
        data = _get_book_response('pt')
        rec = data.get('recommended_voice_id', '')
        assert rec.startswith('pt-BR'), (
            f"Expected pt-BR voice for Portuguese book, got {rec!r}"
        )

    def test_recommended_voice_defaults_when_no_language(self):
        """detected_language=None → recommended_voice_id == DEFAULT_VOICE."""
        from tts_generator import DEFAULT_VOICE
        data = _get_book_response(None)
        rec = data.get('recommended_voice_id', '')
        assert rec == DEFAULT_VOICE, (
            f"Expected DEFAULT_VOICE={DEFAULT_VOICE!r} when no language detected, got {rec!r}"
        )


# ---------------------------------------------------------------------------
# GET /api/tts/voices — ?lang= filter
# ---------------------------------------------------------------------------

class TestGetVoicesFilteredByLang:
    """GET /api/tts/voices?lang=en returns only voices with en- locale."""

    def test_lang_filter_returns_only_matching_voices(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?lang=en')
        assert resp.status_code == 200
        voices = resp.get_json()
        assert isinstance(voices, list)
        assert len(voices) > 0, "Expected at least 1 English voice"
        for v in voices:
            assert v['locale'].startswith('en-'), (
                f"Non-English voice in lang=en result: {v['locale']}"
            )

    def test_lang_filter_pt_returns_portuguese_only(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?lang=pt')
        assert resp.status_code == 200
        voices = resp.get_json()
        assert isinstance(voices, list)
        assert len(voices) > 0, "Expected at least 1 Portuguese voice"
        for v in voices:
            assert v['locale'].startswith('pt-'), (
                f"Non-Portuguese voice in lang=pt result: {v['locale']}"
            )

    def test_no_lang_filter_returns_all_voices(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp_all = client.get('/api/tts/voices')
            resp_en = client.get('/api/tts/voices?lang=en')
        all_voices = resp_all.get_json()
        en_voices = resp_en.get_json()
        assert len(all_voices) > len(en_voices), (
            "Unfiltered list should have more voices than en-only"
        )


# ---------------------------------------------------------------------------
# GET /api/tts/voices — ?grouped=1
# ---------------------------------------------------------------------------

class TestGetVoicesGroupedFormat:
    """GET /api/tts/voices?grouped=1 returns [{locale, voices}] structure."""

    def test_grouped_returns_list_of_objects(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?grouped=1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) > 0

    def test_grouped_entries_have_locale_and_voices_keys(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?grouped=1')
        data = resp.get_json()
        for entry in data:
            assert 'locale' in entry, f"Entry missing 'locale': {entry}"
            assert 'voices' in entry, f"Entry missing 'voices': {entry}"
            assert isinstance(entry['voices'], list), (
                f"'voices' should be a list, got {type(entry['voices'])}"
            )

    def test_grouped_locale_en_gb_is_present(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?grouped=1')
        data = resp.get_json()
        locales = [entry['locale'] for entry in data]
        assert 'en-GB' in locales, f"en-GB missing from grouped locales: {locales}"

    def test_grouped_voices_have_required_fields(self):
        app = _make_test_app()
        with app.test_client() as client:
            resp = client.get('/api/tts/voices?grouped=1')
        data = resp.get_json()
        required = {'id', 'name', 'gender', 'locale', 'backend', 'quality'}
        for entry in data:
            for v in entry['voices']:
                missing = required - set(v.keys())
                assert not missing, (
                    f"Voice {v.get('id')} missing fields: {missing}"
                )
