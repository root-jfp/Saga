"""
Tests for BookProcessor audio improvements.

RED phase: tests written before implementation.
Run with: pytest tests/test_book_tasks.py -v
"""

import sys
import os
import json
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from book_tasks import BookProcessor, _compute_sentence_timings, compute_settings_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_processor():
    """Return a BookProcessor without real folders/TTS."""
    p = BookProcessor.__new__(BookProcessor)
    p.upload_folder = '/tmp/uploads'
    p.audio_folder = '/tmp/audio'
    return p


def _make_fake_page_row(sentences_value, tts_content='Hello world.', audio_status='pending'):
    """
    Build a fake DB row for book_pages as returned by psycopg2.
    sentences_value can be a list (jsonb auto-parsed) or a JSON string.
    """
    text_content = 'Hello world.'
    cached_voice_id = None
    return (text_content, tts_content, audio_status, cached_voice_id, sentences_value)


# ---------------------------------------------------------------------------
# Phase 1: JSON guard in _generate_audio_for_page
# ---------------------------------------------------------------------------

class TestGenerateAudioJsonGuard:
    """
    psycopg2 returns jsonb columns as Python objects (list/dict).
    _generate_audio_for_page must NOT call json.loads() on an already-parsed list.
    """

    def _run_generate(self, sentences_value):
        """
        Run _generate_audio_for_page with a mocked DB that returns
        sentences_value as the sentences column.
        Returns the mock TTS object so callers can assert generate_audio was called.
        """
        p = make_processor()

        fake_row = _make_fake_page_row(sentences_value)

        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = fake_row

        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # TTS returns success
        mock_tts = mock.MagicMock()
        mock_tts.generate_audio.return_value = (True, {'duration': 5.0, 'word_timings': []})
        p.tts = mock_tts

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            with mock.patch('os.path.join', return_value='/tmp/audio/test.mp3'):
                p._generate_audio_for_page(1, 1)

        return mock_tts

    def test_sentences_as_list_actually_generates_audio(self):
        """
        When sentences is already a list (psycopg2 jsonb auto-parse),
        json.loads must NOT be called on it — and TTS generation must succeed.

        Previously: json.loads(list) → TypeError caught silently → TTS never called.
        After fix:  list used directly → TTS called successfully.
        """
        sentences_as_list = [
            {'text': 'Hello world.', 'tts_text': 'Hello world.',
             'start': 0, 'end': 12, 'paragraph_index': 0,
             'is_paragraph_start': True, 'is_heading': False}
        ]
        mock_tts = self._run_generate(sentences_as_list)
        assert mock_tts.generate_audio.called, (
            "TTS generate_audio was NOT called — "
            "json.loads(list) TypeError was silently caught, killing audio generation"
        )

    def test_sentences_as_json_string_still_generates_audio(self):
        """sentences column as JSON string: TTS must still be called."""
        sentences_as_str = json.dumps([
            {'text': 'Hello world.', 'tts_text': 'Hello world.',
             'start': 0, 'end': 12, 'paragraph_index': 0,
             'is_paragraph_start': True, 'is_heading': False}
        ])
        mock_tts = self._run_generate(sentences_as_str)
        assert mock_tts.generate_audio.called

    def test_sentences_as_none_still_generates_audio(self):
        """sentences column NULL: TTS must still be called (empty timing is fine)."""
        mock_tts = self._run_generate(None)
        assert mock_tts.generate_audio.called

    def test_sentences_as_empty_list_still_generates_audio(self):
        """Empty list: TTS must still be called."""
        mock_tts = self._run_generate([])
        assert mock_tts.generate_audio.called


# ---------------------------------------------------------------------------
# Phase 2: Priority parameter on _enqueue_priority_page
# ---------------------------------------------------------------------------

class TestEnqueuePriorityPage:
    """
    _enqueue_priority_page must accept a `priority` keyword argument
    and use it in the SQL insert (instead of always hardcoding 100).
    """

    def _run_enqueue(self, priority=100):
        p = make_processor()
        mock_cur = mock.MagicMock()
        mock_conn = mock.MagicMock()

        with mock.patch('book_tasks.compute_settings_hash', return_value='abc123'):
            p._enqueue_priority_page(1, 5, 'en-US-AriaNeural', mock_conn, mock_cur,
                                     priority=priority)

        return mock_cur.execute.call_args

    def test_default_priority_is_100(self):
        """Default priority remains 100 (backwards compatible)."""
        call_args = self._run_enqueue(priority=100)
        sql_params = call_args[0][1]   # positional params tuple passed to execute
        # priority appears twice: INSERT value and GREATEST(...) value
        assert 100 in sql_params, f"Expected 100 in params, got {sql_params}"

    def test_custom_priority_500_is_used(self):
        """Passing priority=500 must use 500 in the SQL params."""
        call_args = self._run_enqueue(priority=500)
        sql_params = call_args[0][1]
        assert 500 in sql_params, f"Expected 500 in params, got {sql_params}"

    def test_priority_value_appears_twice(self):
        """
        Priority must appear twice: once for INSERT and once for the
        GREATEST(book_audio_jobs.priority, %s) in the ON CONFLICT clause.
        """
        call_args = self._run_enqueue(priority=200)
        sql_params = call_args[0][1]
        assert sql_params.count(200) == 2, (
            f"Expected priority 200 twice in params, got {sql_params}"
        )


# ---------------------------------------------------------------------------
# Phase 3: prefetch_audio boosts current page to priority=500
# ---------------------------------------------------------------------------

class TestPrefetchAudioPriority:
    """
    prefetch_audio must enqueue the CURRENT page with priority=500
    (not just the next N pages), so the page the user is on gets
    processed immediately.
    """

    def test_current_page_gets_priority_500(self):
        p = make_processor()

        enqueued = []

        def fake_enqueue(book_id, page_number, voice_id, conn, cur, priority=100):
            enqueued.append({'page': page_number, 'priority': priority})

        p._enqueue_priority_page = fake_enqueue

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            mock_conn.cursor.return_value = mock_cur
            p.prefetch_audio(book_id=1, current_page=20)

        current_page_calls = [e for e in enqueued if e['page'] == 20]
        assert current_page_calls, "Current page (20) was not enqueued at all"
        assert current_page_calls[0]['priority'] == 500, (
            f"Current page priority should be 500, got {current_page_calls[0]['priority']}"
        )

    def test_next_pages_get_elevated_priority(self):
        """Next pages (not current) should get priority >= 100."""
        p = make_processor()

        enqueued = []

        def fake_enqueue(book_id, page_number, voice_id, conn, cur, priority=100):
            enqueued.append({'page': page_number, 'priority': priority})

        p._enqueue_priority_page = fake_enqueue

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            mock_conn.cursor.return_value = mock_cur
            p.prefetch_audio(book_id=1, current_page=20, prefetch_count=2)

        next_page_calls = [e for e in enqueued if e['page'] != 20]
        assert len(next_page_calls) >= 2, "Expected at least 2 next-page calls"
        for call in next_page_calls:
            assert call['priority'] >= 100, (
                f"Next page {call['page']} should have priority >= 100, got {call['priority']}"
            )

    def test_current_page_has_highest_priority(self):
        """Current page priority must be strictly higher than next-page priority."""
        p = make_processor()

        enqueued = []

        def fake_enqueue(book_id, page_number, voice_id, conn, cur, priority=100):
            enqueued.append({'page': page_number, 'priority': priority})

        p._enqueue_priority_page = fake_enqueue

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            mock_conn.cursor.return_value = mock_cur
            p.prefetch_audio(book_id=1, current_page=5, prefetch_count=2)

        current_priority = next(e['priority'] for e in enqueued if e['page'] == 5)
        other_priorities = [e['priority'] for e in enqueued if e['page'] != 5]

        assert all(current_priority > p for p in other_priorities), (
            f"Current page priority {current_priority} should exceed {other_priorities}"
        )


# ---------------------------------------------------------------------------
# Phase 4: enqueue_page_audio forwards priority
# ---------------------------------------------------------------------------

class TestEnqueuePageAudioPriority:
    """
    enqueue_page_audio must forward the priority parameter to
    _enqueue_priority_page (defaulting to 500 for on-demand requests).
    """

    def test_default_priority_is_500(self):
        """On-demand page requests default to priority=500."""
        p = make_processor()

        received_priority = []

        def fake_enqueue(book_id, page_number, voice_id, conn, cur, priority=100):
            received_priority.append(priority)

        p._enqueue_priority_page = fake_enqueue

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            mock_conn.cursor.return_value = mock_cur
            p.enqueue_page_audio(book_id=1, page_number=7)

        assert received_priority == [500], (
            f"enqueue_page_audio default priority should be 500, got {received_priority}"
        )

    def test_custom_priority_forwarded(self):
        """Explicitly passed priority is forwarded correctly."""
        p = make_processor()

        received_priority = []

        def fake_enqueue(book_id, page_number, voice_id, conn, cur, priority=100):
            received_priority.append(priority)

        p._enqueue_priority_page = fake_enqueue

        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()

        with mock.patch('psycopg2.connect', return_value=mock_conn):
            mock_conn.cursor.return_value = mock_cur
            p.enqueue_page_audio(book_id=1, page_number=7, priority=200)

        assert received_priority == [200], (
            f"Expected priority 200, got {received_priority}"
        )


# ---------------------------------------------------------------------------
# Phase 5: Language detection integration in _process_book
# ---------------------------------------------------------------------------

class TestProcessBookLanguageDetection:
    """
    _process_book must call detect_language and save detected_language
    in the UPDATE books SET ... upload_status = 'ready' statement.
    """

    def _build_mock_page(self, text_content: str = 'Hello world.', sentences=None):
        """Return a fake page dict similar to what PDFProcessor.extract_text() yields."""
        if sentences is None:
            sentences = [
                {
                    'text': text_content,
                    'tts_text': text_content,
                    'start': 0, 'end': len(text_content),
                    'paragraph_index': 0,
                    'is_paragraph_start': True,
                    'is_heading': False,
                }
            ]
        return {
            'page_number': 1,
            'text_content': text_content,
            'sentences': sentences,
            'word_count': len(text_content.split()),
        }

    def _run_process_book(
        self,
        pages: list,
        detected_language_result,
        is_scanned: bool = False,
    ) -> list:
        """
        Run _process_book with mocked DB and PDFProcessor.

        Returns the list of (sql, params) tuples passed to cur.execute().
        """
        p = BookProcessor.__new__(BookProcessor)
        p.upload_folder = '/tmp/uploads'
        p.audio_folder = '/tmp/audio'

        mock_tts = mock.MagicMock()
        p.tts = mock_tts

        executed_calls = []

        def capture_execute(sql, params=None):
            executed_calls.append((sql, params))

        mock_cur = mock.MagicMock()
        mock_cur.execute.side_effect = capture_execute
        mock_cur.fetchone.return_value = ('/tmp/fake.pdf',)

        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cur

        mock_processor = mock.MagicMock()
        mock_processor.extract_cover.return_value = False
        mock_processor.extract_text.return_value = (pages, is_scanned)

        with mock.patch('psycopg2.connect', return_value=mock_conn), \
             mock.patch('os.path.exists', return_value=True), \
             mock.patch('book_tasks.PDFProcessor', return_value=mock_processor), \
             mock.patch(
                 'book_tasks.detect_language',
                 return_value=detected_language_result,
             ):
            p._process_book(book_id=1)

        return executed_calls

    def test_detected_language_saved_on_ready_update(self):
        """
        When language detection returns ('en', 0.95), the UPDATE that sets
        upload_status='ready' must also set detected_language='en'.
        """
        pages = [self._build_mock_page('Hello world. ' * 20)]
        calls = self._run_process_book(
            pages=pages,
            detected_language_result=('en', 0.95),
        )

        # Find the UPDATE books SET ... upload_status = 'ready' call
        # The SQL contains "upload_status = 'ready'" and "detected_language"
        ready_updates = [
            (sql, params) for sql, params in calls
            if sql and "upload_status" in sql and "detected_language" in sql
        ]
        assert ready_updates, (
            "No UPDATE with detected_language column found. "
            f"All calls: {[(s[:60] if s else None) for s, _ in calls]}"
        )

        # The params must include the detected language code
        all_params = [p for _, p in ready_updates]
        assert any('en' in (p or ()) for p in all_params), (
            f"'en' not found in ready UPDATE params: {all_params}"
        )

    def test_detected_language_none_when_detection_fails(self):
        """
        When detect_language returns None, the UPDATE must set
        detected_language=None (not crash).
        """
        pages = [self._build_mock_page('Hello world. ' * 20)]
        calls = self._run_process_book(
            pages=pages,
            detected_language_result=None,
        )

        ready_updates = [
            (sql, params) for sql, params in calls
            if sql and "upload_status" in sql and "detected_language" in sql
        ]
        assert ready_updates, (
            "No UPDATE with detected_language column found. "
            f"All calls: {[(s[:60] if s else None) for s, _ in calls]}"
        )

        # None should appear in params (actual Python None, not the string)
        _, params = ready_updates[0]
        assert None in params, (
            f"Expected None in params when detection returns None, got {params}"
        )

    def test_process_book_does_not_crash_when_detect_language_raises(self):
        """
        If detect_language raises an exception, _process_book must not propagate it.
        The book should still be processed (upload_status set to 'ready').
        """
        pages = [self._build_mock_page('Hello world. ' * 20)]

        p = BookProcessor.__new__(BookProcessor)
        p.upload_folder = '/tmp/uploads'
        p.audio_folder = '/tmp/audio'
        p.tts = mock.MagicMock()

        executed_calls = []

        def capture_execute(sql, params=None):
            executed_calls.append((sql, params))

        mock_cur = mock.MagicMock()
        mock_cur.execute.side_effect = capture_execute
        mock_cur.fetchone.return_value = ('/tmp/fake.pdf',)
        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cur

        mock_processor = mock.MagicMock()
        mock_processor.extract_cover.return_value = False
        mock_processor.extract_text.return_value = (pages, False)

        with mock.patch('psycopg2.connect', return_value=mock_conn), \
             mock.patch('os.path.exists', return_value=True), \
             mock.patch('book_tasks.PDFProcessor', return_value=mock_processor), \
             mock.patch('book_tasks.detect_language', side_effect=RuntimeError("boom")):
            # Must not raise
            p._process_book(book_id=1)

        ready_updates = [
            (sql, params) for sql, params in executed_calls
            if sql and 'upload_status' in sql and 'detected_language' in sql
        ]
        assert ready_updates, (
            "upload_status='ready' was not set — language detection error broke book processing"
        )
