"""
Background Task Processing for Saga
- Process uploaded PDFs (extract text, pages)
- Queue audio generation for background worker
- Runs in separate threads to avoid blocking

Note: Audio generation is handled by run_book_audio_worker.py
This module enqueues jobs and handles PDF processing.
"""

import threading
import json
import os
import hashlib
import psycopg2
from dotenv import load_dotenv

from pdf_processor import PDFProcessor
from tts_generator import TTSGenerator
from language_detector import detect_language

load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'book_reader'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}


def _compute_sentence_timings(sentences, duration):
    """
    Compute sentence-level timing from character proportions.
    Returns [{offset, duration}] aligned with the sentences list.
    """
    if not sentences or duration <= 0:
        return []
    total_chars = sum(len(s.get('text', '')) for s in sentences)
    if total_chars == 0:
        return []
    timings = []
    elapsed = 0.0
    for sent in sentences:
        sent_chars = max(len(sent.get('text', '')), 1)
        sent_duration = duration * (sent_chars / total_chars)
        timings.append({'offset': round(elapsed, 4), 'duration': round(sent_duration, 4)})
        elapsed += sent_duration
    return timings


def compute_settings_hash(voice_id):
    """Compute a hash for voice settings to detect changes."""
    settings = {'voice_id': voice_id or 'default'}
    settings_str = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(settings_str.encode()).hexdigest()[:16]


class BookProcessor:
    """Background processor for books and audio generation."""

    def __init__(self, upload_folder, audio_folder, tts_model_path=None):
        """
        Initialize book processor.

        Args:
            upload_folder: Directory where PDFs are stored
            audio_folder: Directory for generated audio files
            tts_model_path: Optional path to Piper TTS model
        """
        self.upload_folder = upload_folder
        self.audio_folder = audio_folder
        self.tts = TTSGenerator(model_path=tts_model_path)

        # Ensure audio folder exists
        os.makedirs(self.audio_folder, exist_ok=True)

    def process_book_async(self, book_id):
        """Start background processing of a book."""
        thread = threading.Thread(
            target=self._process_book,
            args=(book_id,),
            daemon=True,
            name=f"BookProcessor-{book_id}"
        )
        thread.start()
        return thread

    def _process_book(self, book_id):
        """
        Process a book: extract text, store pages.
        Called in background thread.
        """
        conn = None
        cur = None

        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()

            # Get book info
            cur.execute('SELECT storage_path FROM books WHERE id = %s', (book_id,))
            result = cur.fetchone()

            if not result:
                print(f"Book {book_id} not found")
                return

            pdf_path = result[0]

            if not os.path.exists(pdf_path):
                self._update_book_status(
                    book_id, 'failed',
                    f"PDF file not found: {pdf_path}",
                    conn, cur
                )
                return

            # Update status to processing
            cur.execute(
                "UPDATE books SET upload_status = 'processing' WHERE id = %s",
                (book_id,)
            )
            conn.commit()

            # Extract text from PDF
            print(f"Processing book {book_id}: {pdf_path}")
            processor = PDFProcessor(pdf_path)

            # Generate thumbnail from first page
            thumbnail_path = None
            thumbnails_dir = os.path.join(self.upload_folder, 'thumbnails')
            try:
                os.makedirs(thumbnails_dir, exist_ok=True)
                thumbnail_filename = f"book_{book_id}_cover.jpg"
                thumbnail_path = os.path.join(thumbnails_dir, thumbnail_filename)

                if processor.extract_cover(thumbnail_path):
                    print(f"Thumbnail generated: {thumbnail_path}")
                    cur.execute(
                        "UPDATE books SET cover_image_path = %s WHERE id = %s",
                        (thumbnail_path, book_id)
                    )
                    conn.commit()
                else:
                    thumbnail_path = None
            except Exception as e:
                print(f"Warning: Could not generate thumbnail: {e}")
                thumbnail_path = None

            # Pull TOC from PDF outline (PyMuPDF). Falls back to detected
            # headings later if absent. Stored as JSON in books.toc.
            try:
                import fitz
                with fitz.open(pdf_path) as doc:
                    raw_toc = doc.get_toc(simple=True) or []
                toc = [
                    {'level': lvl, 'title': (title or '').strip(), 'page': page}
                    for lvl, title, page in raw_toc
                    if title and 1 <= page
                ]
                if toc:
                    cur.execute(
                        "UPDATE books SET toc = %s WHERE id = %s",
                        (json.dumps(toc), book_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"Warning: TOC extraction failed: {e}")

            # Open Library / Google Books enrichment — clean title, real cover,
            # author, ISBN, summary, subjects. Best-effort; never blocks upload.
            try:
                cur.execute("SELECT title, author, filename FROM books WHERE id=%s", (book_id,))
                row = cur.fetchone()
                if row:
                    raw_title, raw_author, raw_filename = row
                    from metadata_enricher import enrich_book, download_cover
                    hint = raw_title or raw_filename or ''
                    meta = enrich_book(hint, raw_author or None)
                    if meta:
                        fields = meta.as_db_dict()
                        cur.execute("""
                            UPDATE books SET
                                title          = COALESCE(NULLIF(%s,''), title),
                                author         = COALESCE(NULLIF(%s,''), author),
                                subtitle       = %s,
                                isbn           = %s,
                                published_year = %s,
                                summary        = %s,
                                subjects       = %s,
                                open_library_id= %s,
                                metadata_source= %s,
                                metadata_fetched_at = NOW()
                            WHERE id = %s
                        """, (
                            fields['title'], fields['author'], fields['subtitle'],
                            fields['isbn'], fields['published_year'], fields['summary'],
                            fields['subjects'], fields['open_library_id'], fields['metadata_source'],
                            book_id,
                        ))
                        conn.commit()

                        # Replace the PDF-page thumbnail with the real cover
                        # if we got one. Saved over the same path so the
                        # /api/books/<id>/thumbnail URL stays stable.
                        if meta.cover_url and thumbnail_path:
                            if download_cover(meta.cover_url, thumbnail_path):
                                print(f"Cover replaced from {meta.source}: {meta.cover_url}")
            except Exception as e:
                print(f"Warning: metadata enrichment failed: {e}")

            pages, is_scanned = processor.extract_text()

            if not pages:
                self._update_book_status(
                    book_id, 'failed',
                    "No text could be extracted from PDF",
                    conn, cur
                )
                return

            # Detect book language from first ~5 pages or first 5000 chars.
            # Pages with < 200 chars are skipped (too short for reliable detection).
            detected_language = None
            try:
                sample_parts = []
                sample_chars = 0
                for pg in pages[:5]:
                    pg_text = pg.get('text_content', '')
                    if len(pg_text.strip()) >= 200:
                        sample_parts.append(pg_text)
                        sample_chars += len(pg_text)
                        if sample_chars >= 5000:
                            break
                sample_text = ' '.join(sample_parts)[:5000]
                lang_result = detect_language(sample_text)
                if lang_result:
                    detected_language, _conf = lang_result
            except Exception as lang_err:
                print(f"Warning: language detection failed for book {book_id}: {lang_err}")
                detected_language = None

            # Insert pages first, then mark book ready in the same transaction.
            # This avoids a window where upload_status='ready' is committed but
            # book_pages rows are not yet present (would 404 the reader).
            for page in pages:
                # Build TTS content by joining per-sentence tts_text.
                # Falls back to text_content if sentences lack tts_text (old format).
                sentences = page.get('sentences', [])
                tts_parts = [
                    s.get('tts_text') or s.get('text', '')
                    for s in sentences
                    if (s.get('tts_text') or s.get('text', '')).strip()
                ]
                tts_content = ' '.join(tts_parts) if tts_parts else page['text_content']

                cur.execute("""
                    INSERT INTO book_pages
                    (book_id, page_number, text_content, tts_content, sentences, word_count, audio_status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (book_id, page_number)
                    DO UPDATE SET
                        text_content = EXCLUDED.text_content,
                        tts_content  = EXCLUDED.tts_content,
                        sentences    = EXCLUDED.sentences,
                        word_count   = EXCLUDED.word_count
                """, (
                    book_id,
                    page['page_number'],
                    page['text_content'],
                    tts_content,
                    json.dumps(page['sentences']),
                    page['word_count']
                ))

            # Now that all pages are staged, mark the book ready atomically.
            cur.execute("""
                UPDATE books
                SET total_pages = %s, is_scanned = %s, upload_status = 'ready',
                    detected_language = %s
                WHERE id = %s
            """, (len(pages), is_scanned, detected_language, book_id))

            conn.commit()
            print(f"Book {book_id} processed: {len(pages)} pages, scanned={is_scanned}")

            # Enqueue all pages for background audio generation.
            # The standalone worker (run_book_audio_worker.py) picks these up.
            self._enqueue_book_audio(book_id, len(pages), conn, cur)

        except Exception as e:
            print(f"Error processing book {book_id}: {e}")
            if conn and cur:
                try:
                    conn.rollback()
                    self._update_book_status(book_id, 'failed', str(e), conn, cur)
                except Exception as inner_e:
                    print(f"Failed to update book status after error: {inner_e}")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _update_book_status(self, book_id, status, error, conn, cur):
        """Update book status and error message."""
        try:
            cur.execute("""
                UPDATE books
                SET upload_status = %s, processing_error = %s
                WHERE id = %s
            """, (status, error, book_id))
            conn.commit()
        except Exception as e:
            print(f"Failed to update book status: {e}")

    def generate_audio_async(self, book_id, page_number, voice_id=None, priority=500):
        """
        Enqueue a page with high priority for the background worker.
        Uses the queue instead of spawning threads, so priority ordering is respected.
        """
        return self.enqueue_page_audio(book_id, page_number, voice_id=voice_id, priority=priority)

    def _generate_audio_for_page(self, book_id, page_number, voice_id=None):
        """Generate audio for a single page with optional voice selection."""
        # Defence-in-depth: workers may pull voice_id from the queue; reject
        # anything that doesn't match the strict voice-ID pattern before it
        # flows into filename construction below.
        from tts_generator import is_valid_voice_id
        if not is_valid_voice_id(voice_id):
            print(f"Rejecting invalid voice_id for book {book_id} page {page_number}")
            return

        conn = None
        cur = None

        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()

            # Get page text and sentences
            cur.execute("""
                SELECT text_content, tts_content, audio_status, audio_voice_id, sentences
                FROM book_pages
                WHERE book_id = %s AND page_number = %s
            """, (book_id, page_number))

            result = cur.fetchone()
            if not result:
                print(f"Page {page_number} not found for book {book_id}")
                return

            text_content, tts_content, audio_status, cached_voice_id, sentences_json = result
            # psycopg2 auto-parses jsonb → Python list; guard against calling json.loads on it
            if isinstance(sentences_json, list):
                sentences = sentences_json
            elif sentences_json:
                sentences = json.loads(sentences_json)
            else:
                sentences = []
            # Use TTS-optimised text when available, fall back to display text
            speak_text = tts_content if tts_content and tts_content.strip() else text_content

            # Skip if already generating (unless different voice requested)
            if audio_status == 'generating':
                return

            # Skip if ready with same voice (or no voice specified)
            if audio_status == 'ready' and (not voice_id or voice_id == cached_voice_id):
                return

            if not speak_text or not speak_text.strip():
                # Mark as ready with no audio (empty page)
                cur.execute("""
                    UPDATE book_pages
                    SET audio_status = 'ready', audio_duration_seconds = 0
                    WHERE book_id = %s AND page_number = %s
                """, (book_id, page_number))
                conn.commit()
                return

            # Mark as generating
            cur.execute("""
                UPDATE book_pages
                SET audio_status = 'generating'
                WHERE book_id = %s AND page_number = %s
            """, (book_id, page_number))
            conn.commit()

            # Generate audio file (include voice in filename if specified)
            if voice_id:
                safe_voice = voice_id.replace('-', '_')
                audio_filename = f"book_{book_id}_page_{page_number}_{safe_voice}.mp3"
            else:
                audio_filename = f"book_{book_id}_page_{page_number}.mp3"
            audio_path = os.path.join(self.audio_folder, audio_filename)

            print(f"Generating audio for book {book_id} page {page_number} with voice {voice_id or 'default'}")
            success, gen_result = self.tts.generate_audio(speak_text, audio_path, voice_id=voice_id)

            if success:
                # gen_result is now a dict with duration and word_timings
                duration = gen_result.get('duration', 0) if isinstance(gen_result, dict) else gen_result
                word_timings = gen_result.get('word_timings', []) if isinstance(gen_result, dict) else []

                # Convert to sentence-level timing (one entry per sentence)
                sentence_timings = _compute_sentence_timings(sentences, duration)
                timing_json = json.dumps(sentence_timings) if sentence_timings else None

                cur.execute("""
                    UPDATE book_pages
                    SET audio_status = 'ready',
                        audio_path = %s,
                        audio_duration_seconds = %s,
                        audio_voice_id = %s,
                        audio_timing = %s
                    WHERE book_id = %s AND page_number = %s
                """, (audio_path, duration, voice_id, timing_json, book_id, page_number))
                print(f"Audio generated: {audio_path} ({duration:.2f}s) with {len(word_timings)} timing entries")
            else:
                cur.execute("""
                    UPDATE book_pages
                    SET audio_status = 'failed'
                    WHERE book_id = %s AND page_number = %s
                """, (book_id, page_number))
                print(f"Audio generation failed: {gen_result}")

            conn.commit()

        except Exception as e:
            print(f"Error generating audio for book {book_id} page {page_number}: {e}")
            if conn and cur:
                try:
                    conn.rollback()
                    cur.execute("""
                        UPDATE book_pages
                        SET audio_status = 'failed'
                        WHERE book_id = %s AND page_number = %s
                    """, (book_id, page_number))
                    conn.commit()
                except Exception as inner_e:
                    print(f"Failed to mark page {page_number} as failed: {inner_e}")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def _start_background_audio_generation(self, book_id, total_pages, max_workers=3):
        """
        Start background threads to generate audio for all pages.
        This runs automatically after book processing without requiring
        the separate worker process.

        Uses a thread pool to process multiple pages in parallel.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def generate_single_page(page_number):
            """Generate audio for a single page."""
            try:
                self._generate_audio_for_page(book_id, page_number, voice_id=None)
                return page_number, True
            except Exception as e:
                print(f"[AudioGen] Error generating page {page_number}: {e}")
                return page_number, False

        def generate_all_pages():
            print(f"[AudioGen] Starting automatic audio generation for book {book_id} ({total_pages} pages, {max_workers} workers)")

            completed = 0
            failed = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all pages for processing
                futures = {
                    executor.submit(generate_single_page, page_num): page_num
                    for page_num in range(1, total_pages + 1)
                }

                # Track completion
                for future in as_completed(futures):
                    page_num, success = future.result()
                    if success:
                        completed += 1
                    else:
                        failed += 1

                    # Log progress every 10 pages or at the end
                    if completed % 10 == 0 or (completed + failed) == total_pages:
                        print(f"[AudioGen] Book {book_id}: {completed}/{total_pages} pages completed ({failed} failed)")

            print(f"[AudioGen] Completed audio generation for book {book_id}: {completed} success, {failed} failed")

        # Start in a daemon thread so it doesn't block
        thread = threading.Thread(
            target=generate_all_pages,
            daemon=True,
            name=f"AudioGenAll-{book_id}"
        )
        thread.start()
        print(f"[AudioGen] Started background audio generation thread for book {book_id}")

    def _enqueue_book_audio(self, book_id, total_pages, conn, cur):
        """
        Enqueue all pages of a book for background audio generation.
        The actual generation is handled by run_book_audio_worker.py
        """
        try:
            settings_hash = compute_settings_hash(None)  # Default voice

            # Insert jobs for all pages
            jobs_created = 0
            for page_number in range(1, total_pages + 1):
                try:
                    cur.execute("""
                        INSERT INTO book_audio_jobs
                        (book_id, page_number, voice_id, settings_hash, priority)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (book_id, page_number, settings_hash) DO NOTHING
                    """, (book_id, page_number, None, settings_hash, 0))
                    if cur.rowcount > 0:
                        jobs_created += 1
                except Exception as e:
                    print(f"Error enqueueing page {page_number}: {e}")

            # Update book status
            cur.execute("""
                UPDATE books
                SET audio_generation_status = 'in_progress',
                    audio_generation_started_at = NOW(),
                    audio_voice_settings_hash = %s
                WHERE id = %s
            """, (settings_hash, book_id))

            conn.commit()
            print(f"Enqueued {jobs_created} pages for audio generation (book {book_id})")

        except Exception as e:
            print(f"Error enqueueing audio jobs: {e}")

    def _generate_audio_for_pages(self, book_id, start_page, end_page, conn, cur):
        """Generate audio for a range of pages (legacy - now uses queue)."""
        for page_num in range(start_page, end_page + 1):
            # Check if page exists
            cur.execute("""
                SELECT id FROM book_pages
                WHERE book_id = %s AND page_number = %s
            """, (book_id, page_num))

            if cur.fetchone():
                # Enqueue with high priority; caller owns the commit
                self._enqueue_priority_page(book_id, page_num, None, conn, cur)
        conn.commit()

    def _enqueue_priority_page(self, book_id, page_number, voice_id, conn, cur, priority=100):
        """
        Execute the INSERT/UPDATE for a single page job.
        Does NOT commit — callers own the transaction boundary.
        """
        settings_hash = compute_settings_hash(voice_id)
        cur.execute("""
            INSERT INTO book_audio_jobs
            (book_id, page_number, voice_id, settings_hash, priority)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (book_id, page_number, settings_hash)
            DO UPDATE SET priority = GREATEST(book_audio_jobs.priority, %s),
                         status = CASE
                             WHEN book_audio_jobs.status = 'failed' THEN 'pending'
                             ELSE book_audio_jobs.status
                         END
        """, (book_id, page_number, voice_id, settings_hash, priority, priority))

    def prefetch_audio(self, book_id, current_page, prefetch_count=2, voice_id=None):
        """
        Boost current page to maximum priority (500) and pre-generate
        upcoming pages with elevated priority (100).
        Called when user navigates to a page to stay ahead of playback.
        """
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()

            # Current page: highest priority so it's processed immediately
            self._enqueue_priority_page(book_id, current_page, voice_id, conn, cur, priority=500)

            # Upcoming pages: elevated priority
            for i in range(1, prefetch_count + 1):
                next_page = current_page + i
                self._enqueue_priority_page(book_id, next_page, voice_id, conn, cur, priority=100)

            conn.commit()

        except Exception as e:
            if conn:
                conn.rollback()
            print(f"Error prefetching audio: {e}")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def enqueue_page_audio(self, book_id, page_number, voice_id=None, priority=500):
        """
        Enqueue a single page for audio generation with maximum priority.
        Used for on-demand generation when user is actively reading a page.
        """
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            self._enqueue_priority_page(book_id, page_number, voice_id, conn, cur, priority=priority)
            conn.commit()
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"Error enqueueing page audio: {e}")
            return False
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()


# Global processor instance (initialized by app.py)
_processor = None


def get_processor(upload_folder=None, audio_folder=None):
    """Get or create the global BookProcessor instance."""
    global _processor
    if _processor is None and upload_folder and audio_folder:
        _processor = BookProcessor(upload_folder, audio_folder)
    return _processor


def init_processor(upload_folder, audio_folder, tts_model_path=None):
    """Initialize the global BookProcessor."""
    global _processor
    _processor = BookProcessor(upload_folder, audio_folder, tts_model_path)
    return _processor
