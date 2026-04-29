"""
Background Audio Worker for Saga

This script processes the audio generation queue for books.
It should be run as a separate process alongside the main Flask app.

Usage:
    python run_book_audio_worker.py

The worker will:
1. Poll the book_audio_jobs table for pending jobs
2. Process jobs in PARALLEL (configurable concurrency)
3. Generate audio using TTS
4. Update job status and book progress

Environment variables:
    AUDIO_WORKER_POLL_INTERVAL: Seconds between queue checks (default: 2)
    AUDIO_WORKER_MAX_RETRIES: Max retry attempts per job (default: 3)
    AUDIO_WORKER_CONCURRENCY: Number of parallel jobs (default: 4)
"""

import os
import sys
import time
import signal
import hashlib
import json
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'book_reader'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

# Worker configuration
POLL_INTERVAL = int(os.getenv('AUDIO_WORKER_POLL_INTERVAL', 2))
MAX_RETRIES = int(os.getenv('AUDIO_WORKER_MAX_RETRIES', 3))
CONCURRENCY = int(os.getenv('AUDIO_WORKER_CONCURRENCY', 4))
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
AUDIO_FOLDER = os.path.join(UPLOAD_FOLDER, 'audio')

# Thread-local storage for database connections
thread_local = threading.local()

# Ensure audio folder exists
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# Global flag for graceful shutdown
running = True


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global running
    print("\n[WORKER] Received shutdown signal, finishing current job...")
    running = False


def get_db_connection():
    """Get a database connection."""
    return psycopg2.connect(**DB_CONFIG)


def compute_settings_hash(voice_id):
    """Compute a hash for voice settings to detect changes."""
    settings = {'voice_id': voice_id or 'default'}
    settings_str = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(settings_str.encode()).hexdigest()[:16]


def get_thread_db_connection():
    """Get a thread-local database connection."""
    if not hasattr(thread_local, 'conn') or thread_local.conn.closed:
        thread_local.conn = psycopg2.connect(**DB_CONFIG)
    return thread_local.conn


def get_next_jobs(conn, count=1):
    """Get the next N pending jobs from the queue."""
    cur = conn.cursor()
    try:
        # Get highest priority pending jobs, with row-level lock
        cur.execute("""
            SELECT j.id, j.book_id, j.page_number, j.voice_id, j.settings_hash, j.attempts
            FROM book_audio_jobs j
            WHERE j.status = 'pending'
            ORDER BY j.priority DESC, j.created_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (count,))
        rows = cur.fetchall()

        jobs = []
        for row in rows:
            job_id, book_id, page_number, voice_id, settings_hash, attempts = row

            # Mark as in_progress
            cur.execute("""
                UPDATE book_audio_jobs
                SET status = 'in_progress', started_at = NOW()
                WHERE id = %s
            """, (job_id,))

            jobs.append({
                'id': job_id,
                'book_id': book_id,
                'page_number': page_number,
                'voice_id': voice_id,
                'settings_hash': settings_hash,
                'attempts': attempts
            })

        conn.commit()
        return jobs
    finally:
        cur.close()


def get_next_job(conn):
    """Get the next pending job from the queue (legacy single-job version)."""
    jobs = get_next_jobs(conn, 1)
    return jobs[0] if jobs else None


def get_page_text(conn, book_id, page_number):
    """Get TTS-optimised text for a page (falls back to display text)."""
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT tts_content, text_content FROM book_pages
            WHERE book_id = %s AND page_number = %s
        """, (book_id, page_number))
        result = cur.fetchone()
        if not result:
            return None
        tts_content, text_content = result
        return tts_content if tts_content and tts_content.strip() else text_content
    finally:
        cur.close()


def _compute_sentence_timings_for_page(conn, book_id, page_number, duration):
    """Fetch the page's sentences and compute character-proportional
    sentence-level timings as [{offset, duration}, …]. Returns [] on failure
    so the caller can store NULL — the frontend will fall back to even
    distribution rather than mis-aligned data."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT sentences FROM book_pages WHERE book_id = %s AND page_number = %s",
            (book_id, page_number),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        sentences = row[0]
        if isinstance(sentences, str):
            sentences = json.loads(sentences)
        if not isinstance(sentences, list) or not sentences or duration <= 0:
            return []
        total_chars = sum(len((s or {}).get('text', '')) for s in sentences)
        if total_chars == 0:
            return []
        timings = []
        elapsed = 0.0
        for sent in sentences:
            sent_chars = max(len((sent or {}).get('text', '')), 1)
            sent_duration = duration * (sent_chars / total_chars)
            timings.append({
                'offset': round(elapsed, 4),
                'duration': round(sent_duration, 4),
            })
            elapsed += sent_duration
        return timings
    except Exception as e:
        print(f"[AUDIO WORKER] sentence-timing compute failed for book={book_id} p={page_number}: {e}")
        return []
    finally:
        cur.close()


def generate_audio(text_content, audio_path, voice_id=None):
    """Generate audio using TTS generator."""
    try:
        from tts_generator import TTSGenerator
        tts = TTSGenerator()
        success, result = tts.generate_audio(text_content, audio_path, voice_id=voice_id)
        return success, result
    except Exception as e:
        return False, str(e)


def update_job_completed(conn, job_id, book_id, page_number, audio_path, duration, voice_id, word_timings):
    """Mark a job as completed and update page audio info.

    The frontend's seekToSentence() expects audio_timing to be a list of
    sentence-level entries (one per displayed sentence) with {offset, duration}.
    Word-level timings from edge-tts WordBoundary events would mis-map sentence
    indices to early offsets (the fifth sentence would seek to the fifth word).
    So we recompute sentence timings here from the page's sentences and store
    those instead.
    """
    cur = conn.cursor()
    try:
        # Update job status
        cur.execute("""
            UPDATE book_audio_jobs
            SET status = 'completed', completed_at = NOW()
            WHERE id = %s
        """, (job_id,))

        # Compute sentence-level timings from the stored sentences + total duration
        sentence_timings = _compute_sentence_timings_for_page(
            conn, book_id, page_number, duration
        )
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

        # Update book progress
        update_book_audio_progress(conn, book_id)

        conn.commit()
    finally:
        cur.close()


def update_job_failed(conn, job_id, error_message, attempts):
    """Mark a job as failed or retry."""
    cur = conn.cursor()
    try:
        if attempts + 1 >= MAX_RETRIES:
            # Max retries reached, mark as failed
            cur.execute("""
                UPDATE book_audio_jobs
                SET status = 'failed', error_message = %s, attempts = %s
                WHERE id = %s
            """, (error_message, attempts + 1, job_id))
        else:
            # Retry: reset to pending with incremented attempts
            cur.execute("""
                UPDATE book_audio_jobs
                SET status = 'pending', error_message = %s, attempts = %s, started_at = NULL
                WHERE id = %s
            """, (error_message, attempts + 1, job_id))
        conn.commit()
    finally:
        cur.close()


def update_job_skipped(conn, job_id, book_id, page_number):
    """Mark job as skipped (empty page or already done)."""
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE book_audio_jobs
            SET status = 'skipped', completed_at = NOW()
            WHERE id = %s
        """, (job_id,))

        # Mark page as ready (no audio needed for empty page)
        cur.execute("""
            UPDATE book_pages
            SET audio_status = 'ready', audio_duration_seconds = 0
            WHERE book_id = %s AND page_number = %s AND audio_status != 'ready'
        """, (book_id, page_number))

        update_book_audio_progress(conn, book_id)
        conn.commit()
    finally:
        cur.close()


def update_book_audio_progress(conn, book_id):
    """Update the overall audio generation progress for a book."""
    cur = conn.cursor()
    try:
        # Count completed pages
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE audio_status = 'ready') as completed,
                COUNT(*) as total
            FROM book_pages
            WHERE book_id = %s
        """, (book_id,))
        completed, total = cur.fetchone()

        # Update book progress
        if completed >= total and total > 0:
            cur.execute("""
                UPDATE books
                SET audio_pages_completed = %s,
                    audio_generation_status = 'completed',
                    audio_generation_completed_at = NOW()
                WHERE id = %s
            """, (completed, book_id))
        else:
            cur.execute("""
                UPDATE books
                SET audio_pages_completed = %s,
                    audio_generation_status = 'in_progress'
                WHERE id = %s
            """, (completed, book_id))

        conn.commit()
    finally:
        cur.close()


def process_job(job):
    """Process a single audio generation job."""
    conn = get_db_connection()
    try:
        book_id = job['book_id']
        page_number = job['page_number']
        voice_id = job['voice_id']

        # Defence-in-depth: voice_id flows into filename construction below.
        # Reject anything that doesn't match the strict pattern. If a malformed
        # row is in the queue, drop it as a no-op.
        from tts_generator import is_valid_voice_id
        if not is_valid_voice_id(voice_id):
            print(f"[WORKER] Rejecting invalid voice_id for book {book_id} page {page_number}")
            return False

        print(f"[WORKER] Processing book {book_id} page {page_number} with voice {voice_id or 'default'}")

        # Get page text
        text_content = get_page_text(conn, book_id, page_number)

        if not text_content or not text_content.strip():
            # Empty page, skip
            print(f"[WORKER] Page {page_number} is empty, skipping")
            update_job_skipped(conn, job['id'], book_id, page_number)
            return True

        # Generate audio filename
        if voice_id:
            safe_voice = voice_id.replace('-', '_')
            audio_filename = f"book_{book_id}_page_{page_number}_{safe_voice}.mp3"
        else:
            audio_filename = f"book_{book_id}_page_{page_number}.mp3"
        audio_path = os.path.join(AUDIO_FOLDER, audio_filename)

        # If a job is in the queue, the upstream code already concluded that
        # this page needs (re)generation — never trust an on-disk file. The
        # old "skip if file exists with size > 0" optimisation silently reused
        # stale audio whose source text had since changed (e.g. after a
        # re-extraction), which is exactly the bug we just hit. Always
        # regenerate; that's why the job exists.
        success, result = generate_audio(text_content, audio_path, voice_id)

        if success:
            duration = result.get('duration', 0) if isinstance(result, dict) else result
            word_timings = result.get('word_timings', []) if isinstance(result, dict) else []

            update_job_completed(
                conn, job['id'], book_id, page_number,
                audio_path, duration, voice_id, word_timings
            )
            print(f"[WORKER] Completed: {audio_path} ({duration:.2f}s)")
            return True
        else:
            error_msg = str(result)
            print(f"[WORKER] Failed: {error_msg}")
            update_job_failed(conn, job['id'], error_msg, job['attempts'])
            return False

    except Exception as e:
        print(f"[WORKER] Error processing job: {e}")
        try:
            update_job_failed(conn, job['id'], str(e), job['attempts'])
        except Exception as inner_e:
            print(f"[WORKER] Failed to record job failure: {inner_e}")
        return False
    finally:
        conn.close()


def enqueue_book_pages(book_id, voice_id=None, priority=0):
    """
    Enqueue all pages of a book for audio generation.
    Called when a book is first imported or when regeneration is requested.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        settings_hash = compute_settings_hash(voice_id)

        # Get all pages for the book
        cur.execute("""
            SELECT page_number FROM book_pages
            WHERE book_id = %s
            ORDER BY page_number
        """, (book_id,))
        pages = [row[0] for row in cur.fetchall()]

        if not pages:
            print(f"[WORKER] No pages found for book {book_id}")
            return 0

        # Insert jobs for each page (ON CONFLICT DO NOTHING to avoid duplicates)
        jobs_created = 0
        for page_number in pages:
            try:
                cur.execute("""
                    INSERT INTO book_audio_jobs
                    (book_id, page_number, voice_id, settings_hash, priority)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (book_id, page_number, settings_hash) DO NOTHING
                """, (book_id, page_number, voice_id, settings_hash, priority))
                if cur.rowcount > 0:
                    jobs_created += 1
            except Exception as insert_e:
                print(f"[WORKER] Error enqueueing page {page_number}: {insert_e}")

        # Update book status
        cur.execute("""
            UPDATE books
            SET audio_generation_status = 'in_progress',
                audio_generation_started_at = NOW(),
                audio_voice_settings_hash = %s
            WHERE id = %s
        """, (settings_hash, book_id))

        conn.commit()
        print(f"[WORKER] Enqueued {jobs_created} pages for book {book_id}")
        return jobs_created

    finally:
        cur.close()
        conn.close()


def enqueue_priority_page(book_id, page_number, voice_id=None, priority=500):
    """
    Enqueue a single page with elevated priority.
    Used for on-demand generation when user is actively reading a page.
    Default priority=500 puts it ahead of all background jobs (priority=0).
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
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
            RETURNING status
        """, (book_id, page_number, voice_id, settings_hash, priority, priority))

        result = cur.fetchone()
        conn.commit()

        status = result[0] if result else 'pending'
        return status

    finally:
        cur.close()
        conn.close()


def process_job_wrapper(job):
    """Wrapper for process_job that handles thread-local connections."""
    try:
        return process_job(job)
    except Exception as e:
        print(f"[WORKER] Error in job wrapper: {e}")
        return False


def run_worker():
    """Main worker loop with parallel processing."""
    global running

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"[WORKER] Audio generation worker started")
    print(f"[WORKER] Poll interval: {POLL_INTERVAL}s, Max retries: {MAX_RETRIES}")
    print(f"[WORKER] Concurrency: {CONCURRENCY} parallel jobs")
    print(f"[WORKER] Audio folder: {AUDIO_FOLDER}")

    idle_count = 0

    # Create thread pool for parallel processing
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        while running:
            try:
                conn = get_db_connection()
                jobs = get_next_jobs(conn, CONCURRENCY)
                conn.close()

                if jobs:
                    idle_count = 0
                    print(f"[WORKER] Processing {len(jobs)} jobs in parallel...")

                    # Submit all jobs to thread pool
                    futures = {executor.submit(process_job_wrapper, job): job for job in jobs}

                    # Wait for all to complete
                    for future in as_completed(futures):
                        job = futures[future]
                        try:
                            result = future.result()
                            if result:
                                print(f"[WORKER] Job completed: book {job['book_id']} page {job['page_number']}")
                        except Exception as e:
                            print(f"[WORKER] Job failed: {e}")

                else:
                    idle_count += 1
                    if idle_count % 30 == 1:  # Log every minute (30 * 2s)
                        print(f"[WORKER] Queue empty, waiting...")
                    time.sleep(POLL_INTERVAL)

            except Exception as e:
                print(f"[WORKER] Error in main loop: {e}")
                time.sleep(POLL_INTERVAL * 2)

    print("[WORKER] Worker stopped")


if __name__ == '__main__':
    run_worker()
