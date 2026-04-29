"""Unit tests for metadata_enricher.

Network calls are stubbed via monkeypatch — these tests run offline.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import metadata_enricher
from metadata_enricher import (
    BookMetadata,
    enrich_book,
    _clean_title_hint,
    _split_filename_to_title_author,
)


class TestCleanTitleHint:
    def test_strips_pdf_extension(self):
        assert _clean_title_hint('hello.pdf') == 'hello'

    def test_strips_isbn(self):
        assert 'thinking fast and slow' in _clean_title_hint(
            'Thinking_Fast_and_Slow_9780374533557.pdf'
        ).lower()

    def test_strips_hex_uuid(self):
        assert 'a16fe' not in _clean_title_hint(
            'Some Book a16fe24d9b1e7e29c80abcdef.pdf'
        )

    def test_strips_publisher_metadata(self):
        out = _clean_title_hint('Babywise -- Gary Ezzo -- Lightning Source [N p ] (1)')
        # 'Tier' and bracket cruft gone
        assert '[' not in out
        assert '(1)' not in out

    def test_collapses_whitespace(self):
        assert _clean_title_hint('a   b\t\nc') == 'a b c'


class TestSplitFilename:
    def test_double_dash_pattern_pulls_author(self):
        title, author = _split_filename_to_title_author(
            'On Becoming Babywise -- Gary Ezzo -- Lightning Source -- 2012'
        )
        assert 'Babywise' in title
        assert author == 'Gary Ezzo'

    def test_no_author_when_no_separator(self):
        title, author = _split_filename_to_title_author('Plain Title')
        assert title == 'Plain Title'
        assert author is None


class TestEnrichBook:
    def test_returns_none_for_empty(self):
        assert enrich_book('') is None
        assert enrich_book(None) is None

    def test_returns_none_for_too_short(self):
        assert enrich_book('a') is None

    def test_uses_open_library_first(self, monkeypatch):
        captured = {'ol_called': False, 'gb_called': False}

        def fake_ol(title, author):
            captured['ol_called'] = True
            return BookMetadata(title='Found', author='Author X', source='openlibrary')

        def fake_gb(title, author):
            captured['gb_called'] = True
            return BookMetadata(title='G', source='google_books')

        monkeypatch.setattr(metadata_enricher, '_query_open_library', fake_ol)
        monkeypatch.setattr(metadata_enricher, '_query_google_books', fake_gb)

        result = enrich_book('Some Book Title')
        assert result is not None
        assert result.source == 'openlibrary'
        assert captured['ol_called'] is True
        assert captured['gb_called'] is False

    def test_falls_back_to_google_books(self, monkeypatch):
        monkeypatch.setattr(metadata_enricher, '_query_open_library', lambda t, a: None)
        monkeypatch.setattr(
            metadata_enricher, '_query_google_books',
            lambda t, a: BookMetadata(title='G', source='google_books')
        )
        result = enrich_book('Some Book Title')
        assert result is not None
        assert result.source == 'google_books'

    def test_returns_none_when_both_services_fail(self, monkeypatch):
        monkeypatch.setattr(metadata_enricher, '_query_open_library', lambda t, a: None)
        monkeypatch.setattr(metadata_enricher, '_query_google_books', lambda t, a: None)
        assert enrich_book('Some Title') is None


class TestAsDbDict:
    def test_pipe_joins_subjects(self):
        m = BookMetadata(title='T', subjects=['Fiction', 'Drama', 'Classic'])
        assert m.as_db_dict()['subjects'] == 'Fiction|Drama|Classic'

    def test_caps_subjects_at_20(self):
        m = BookMetadata(title='T', subjects=[f'S{i}' for i in range(50)])
        assert len(m.as_db_dict()['subjects'].split('|')) == 20

    def test_none_subjects(self):
        m = BookMetadata(title='T')
        assert m.as_db_dict()['subjects'] is None
