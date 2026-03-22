"""Books/PDF Reader API routes blueprint.

Provides CRUD operations for books, pages, bookmarks, annotations, and audio.
"""

import os
import json
import logging
import time as time_module
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename

from utils.db import (
    get_db_connection, release_connection, row_to_dict, rows_to_dict_list,
    serialize_book, serialize_bookmark, serialize_annotation
)
from utils.helpers import sanitize_input, error_response

logger = logging.getLogger('book-reader')

books_bp = Blueprint('books', __name__)

# Book Reader Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
AUDIO_FOLDER = os.path.join(UPLOAD_FOLDER, 'audio')
ALLOWED_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB max
MAX_BOOK_TITLE_LENGTH = 500

# Ensure upload directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ============================================================================
# BOOKS CRUD
# ============================================================================

@books_bp.route('/api/books', methods=['GET'])
def get_books():
    """Get all books for a user."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user_id:
            cur.execute("""
                SELECT b.*, bp.current_page, bp.playback_speed
                FROM books b
                LEFT JOIN book_progress bp ON b.id = bp.book_id AND bp.user_id = %s
                WHERE b.user_id = %s
                ORDER BY b.created_at DESC
            """, (user_id, user_id))
        else:
            cur.execute("SELECT * FROM books ORDER BY created_at DESC")

        books = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_book(b) for b in books])
    except Exception as e:
        return error_response('Failed to fetch books', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/current', methods=['GET'])
def get_current_books():
    """Get currently reading books (for hub widget)."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT b.*, bp.current_page, bp.playback_speed,
                   ROUND(bp.current_page::float / NULLIF(b.total_pages, 0) * 100) as progress_percent
            FROM books b
            INNER JOIN book_progress bp ON b.id = bp.book_id AND bp.user_id = %s
            WHERE b.user_id = %s
              AND bp.current_page > 0
              AND bp.current_page < b.total_pages
            ORDER BY bp.created_at DESC
            LIMIT 3
        """, (user_id, user_id))

        books = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_book(b) for b in books])
    except Exception as e:
        return error_response('Failed to fetch current books', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books', methods=['POST'])
def upload_book():
    """Upload a new PDF book."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    user_id = request.form.get('user_id', type=int)
    title = sanitize_input(request.form.get('title', '')) or file.filename
    author = sanitize_input(request.form.get('author', ''))

    # Validate title length
    if len(title) > MAX_BOOK_TITLE_LENGTH:
        return jsonify({'error': f'Title too long (max {MAX_BOOK_TITLE_LENGTH})'}), 400

    # Secure filename and save
    filename = secure_filename(file.filename)
    # Add timestamp to prevent collisions
    timestamp = int(time_module.time())
    storage_filename = f"{timestamp}_{filename}"
    storage_path = os.path.join(UPLOAD_FOLDER, storage_filename)

    # Check file size
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset

    if file_size > MAX_FILE_SIZE:
        return jsonify({'error': f'File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)'}), 400

    file.save(storage_path)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO books
            (user_id, title, author, filename, storage_path, file_size_bytes, upload_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            RETURNING *
        """, (user_id, title, author, filename, storage_path, file_size))

        book = row_to_dict(cur, cur.fetchone())
        conn.commit()

        # Start background processing
        try:
            from book_tasks import init_processor
            processor = init_processor(UPLOAD_FOLDER, AUDIO_FOLDER)
            processor.process_book_async(book['id'])
        except Exception as e:
            logger.warning(f"Could not start background processing: {e}")

        return jsonify(serialize_book(book)), 201

    except Exception as e:
        conn.rollback()
        # Clean up uploaded file on error
        if os.path.exists(storage_path):
            os.remove(storage_path)
        return error_response('Failed to upload book', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>', methods=['GET'])
def get_book(book_id):
    """Get a single book with full details."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT b.*, bp.current_page, bp.current_sentence,
                   bp.playback_speed, bp.total_time_read_seconds
            FROM books b
            LEFT JOIN book_progress bp ON b.id = bp.book_id AND bp.user_id = %s
            WHERE b.id = %s
        """, (user_id, book_id))

        book = row_to_dict(cur, cur.fetchone())

        if not book:
            return jsonify({'error': 'Book not found'}), 404

        return jsonify(serialize_book(book))
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    """Delete a book and its files."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Get file paths before deletion
        cur.execute(
            'SELECT storage_path, cover_image_path FROM books WHERE id = %s',
            (book_id,)
        )
        book = cur.fetchone()

        if not book:
            return jsonify({'error': 'Book not found'}), 404

        storage_path, cover_path = book

        # Get audio file paths
        cur.execute(
            'SELECT audio_path FROM book_pages WHERE book_id = %s',
            (book_id,)
        )
        audio_paths = [row[0] for row in cur.fetchall() if row[0]]

        # Delete from database (CASCADE handles related tables)
        cur.execute('DELETE FROM books WHERE id = %s', (book_id,))
        conn.commit()

        # Clean up files
        for path in [storage_path, cover_path] + audio_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

        return jsonify({'message': 'Book deleted'}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# BOOK FILES
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/pdf', methods=['GET'])
def get_book_pdf(book_id):
    """Serve the PDF file for a book (for PDF.js rendering)."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT storage_path, filename FROM books WHERE id = %s', (book_id,))
        result = cur.fetchone()

        if not result:
            return jsonify({'error': 'Book not found'}), 404

        storage_path, filename = result

        if not storage_path or not os.path.exists(storage_path):
            return jsonify({'error': 'PDF file not found'}), 404

        return send_file(
            storage_path,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=filename
        )
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/thumbnail', methods=['GET'])
def get_book_thumbnail(book_id):
    """Serve the thumbnail image for a book. Generates on-demand if missing."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT cover_image_path, storage_path FROM books WHERE id = %s', (book_id,))
        result = cur.fetchone()

        if not result:
            return jsonify({'error': 'Book not found'}), 404

        cover_path = result[0]
        storage_path = result[1]

        # If thumbnail exists and file is there, serve it
        if cover_path and os.path.exists(cover_path):
            return send_file(cover_path, mimetype='image/jpeg')

        # Try to generate thumbnail on-demand
        if storage_path and os.path.exists(storage_path):
            try:
                from pdf_processor import PDFProcessor
                thumbnails_dir = os.path.join(UPLOAD_FOLDER, 'thumbnails')
                os.makedirs(thumbnails_dir, exist_ok=True)
                thumbnail_filename = f"book_{book_id}_cover.jpg"
                thumbnail_path = os.path.join(thumbnails_dir, thumbnail_filename)

                processor = PDFProcessor(storage_path)
                if processor.extract_cover(thumbnail_path):
                    # Update database with new thumbnail path
                    cur.execute(
                        "UPDATE books SET cover_image_path = %s WHERE id = %s",
                        (thumbnail_path, book_id)
                    )
                    conn.commit()
                    return send_file(thumbnail_path, mimetype='image/jpeg')
            except Exception as e:
                logger.warning(f"Could not generate thumbnail for book {book_id}: {e}")

        return jsonify({'error': 'Thumbnail not available'}), 404
    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# BOOK PAGES
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/pages/<int:page_number>', methods=['GET'])
def get_book_page(book_id, page_number):
    """Get a specific page with text and audio status."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT * FROM book_pages
            WHERE book_id = %s AND page_number = %s
        """, (book_id, page_number))

        page = row_to_dict(cur, cur.fetchone())

        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Parse sentences from JSON if stored as string
        if page.get('sentences'):
            if isinstance(page['sentences'], str):
                page['sentences'] = json.loads(page['sentences'])

        # Parse audio_timing from JSON if stored as string
        if page.get('audio_timing'):
            if isinstance(page['audio_timing'], str):
                page['audio_timing'] = json.loads(page['audio_timing'])

        # Serialize timestamps
        if page.get('created_at') and isinstance(page['created_at'], datetime):
            page['created_at'] = page['created_at'].isoformat()

        return jsonify(page)
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/pages/<int:page_number>/audio', methods=['GET'])
def get_page_audio(book_id, page_number):
    """Get or generate audio for a page with optional voice selection."""
    voice_id = request.args.get('voice')  # Optional voice ID parameter

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT audio_path, audio_status, audio_voice_id FROM book_pages
            WHERE book_id = %s AND page_number = %s
        """, (book_id, page_number))

        result = cur.fetchone()
        if not result:
            return jsonify({'error': 'Page not found'}), 404

        audio_path, audio_status, cached_voice_id = result

        # 1. Voice-specific file takes priority (generated by background regen for this voice)
        if voice_id:
            safe_voice = voice_id.replace('-', '_')
            voice_filename = f"book_{book_id}_page_{page_number}_{safe_voice}.mp3"
            voice_audio_path = os.path.join(AUDIO_FOLDER, voice_filename)
            if os.path.exists(voice_audio_path):
                return send_file(voice_audio_path, mimetype='audio/mpeg')

        # 2. Any ready audio is immediately playable — don't block on voice mismatch.
        #    Background re-generation for the exact voice is queued at lower priority.
        if audio_status == 'ready' and audio_path and os.path.exists(audio_path):
            if voice_id and voice_id != cached_voice_id:
                try:
                    from run_book_audio_worker import enqueue_priority_page as _wq_enqueue
                    _wq_enqueue(book_id, page_number, voice_id=voice_id, priority=200)
                except Exception as e:
                    logger.warning(f"Could not enqueue voice regen: {e}")
            return send_file(audio_path, mimetype='audio/mpeg')

        # 3. Audio not yet ready — enqueue with high priority and tell client to retry
        if audio_status != 'generating':
            try:
                from run_book_audio_worker import enqueue_priority_page as _wq_enqueue
                _wq_enqueue(book_id, page_number, voice_id=voice_id, priority=500)
            except Exception as e:
                logger.warning(f"Could not enqueue audio generation: {e}")
        return jsonify({'status': 'generating'}), 202

    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/all-pages', methods=['GET'])
def get_all_book_pages(book_id):
    """Get all pages content for continuous reading mode."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Check book exists
        cur.execute("SELECT id, title, total_pages FROM books WHERE id = %s", (book_id,))
        book = cur.fetchone()
        if not book:
            return jsonify({'error': 'Book not found'}), 404

        book_id, title, total_pages = book

        # Get all pages with sentences
        cur.execute("""
            SELECT page_number, text_content, sentences
            FROM book_pages
            WHERE book_id = %s
            ORDER BY page_number
        """, (book_id,))

        pages = []
        for row in cur.fetchall():
            page_number, text_content, sentences = row

            # Parse sentences from JSON if needed
            if sentences:
                if isinstance(sentences, str):
                    sentences = json.loads(sentences)
            else:
                sentences = []

            pages.append({
                'page_number': page_number,
                'text_content': text_content,
                'sentences': sentences
            })

        return jsonify({
            'book_id': book_id,
            'title': title,
            'total_pages': total_pages,
            'pages': pages
        })

    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# AUDIO GENERATION PROGRESS
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/generate-all-audio', methods=['POST'])
def generate_all_audio(book_id):
    """Queue all pages for audio generation."""
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'User ID required'}), 400

    voice_id = request.json.get('voice_id') if request.json else None

    try:
        from run_book_audio_worker import enqueue_book_pages
        count = enqueue_book_pages(book_id, voice_id=voice_id, priority=0)

        return jsonify({
            'success': True,
            'queued': count,
            'message': f'Queued {count} pages for audio generation'
        })
    except Exception as e:
        logger.error(f"Failed to queue audio generation for book {book_id}: {e}")
        return error_response('Failed to queue audio generation', 500, str(e))


@books_bp.route('/api/books/<int:book_id>/audio-progress', methods=['GET'])
def get_book_audio_progress(book_id):
    """Get audio generation progress for a book."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Get book audio status
        cur.execute("""
            SELECT total_pages, audio_generation_status, audio_pages_completed,
                   audio_generation_started_at, audio_generation_completed_at
            FROM books WHERE id = %s
        """, (book_id,))
        book = cur.fetchone()

        if not book:
            return jsonify({'error': 'Book not found'}), 404

        total_pages, status, pages_completed, started_at, completed_at = book
        pages_completed = pages_completed or 0

        # Get detailed job status
        cur.execute("""
            SELECT status, COUNT(*) as count
            FROM book_audio_jobs
            WHERE book_id = %s
            GROUP BY status
        """, (book_id,))
        job_stats = {row[0]: row[1] for row in cur.fetchall()}

        # Calculate percentage
        percentage = 0
        if total_pages and total_pages > 0:
            percentage = round((pages_completed / total_pages) * 100)

        return jsonify({
            'book_id': book_id,
            'total_pages': total_pages or 0,
            'pages_completed': pages_completed,
            'percentage': percentage,
            'status': status or 'pending',
            'job_stats': job_stats,
            'started_at': started_at.isoformat() if started_at else None,
            'completed_at': completed_at.isoformat() if completed_at else None
        })

    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# TTS VOICES
# ============================================================================

@books_bp.route('/api/tts/voices', methods=['GET'])
def get_tts_voices():
    """Get available TTS voices."""
    try:
        from tts_generator import TTSGenerator
        generator = TTSGenerator()
        voices = generator.get_available_voices()

        # Voices already have proper format from tts_generator
        # Each voice has: id, name, gender, locale, backend, quality
        return jsonify(voices)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# BOOK PROGRESS
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/progress', methods=['GET'])
def get_book_progress(book_id):
    """Get reading progress for a book."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT * FROM book_progress
            WHERE book_id = %s AND user_id = %s
        """, (book_id, user_id))

        progress = row_to_dict(cur, cur.fetchone())

        if not progress:
            # Return default progress
            return jsonify({
                'book_id': book_id,
                'current_page': 1,
                'current_sentence': 0,
                'playback_speed': 1.0,
                'total_time_read_seconds': 0
            })

        # Serialize timestamps
        if progress.get('last_read_at') and isinstance(progress['last_read_at'], datetime):
            progress['last_read_at'] = progress['last_read_at'].isoformat()
        if progress.get('created_at') and isinstance(progress['created_at'], datetime):
            progress['created_at'] = progress['created_at'].isoformat()

        return jsonify(progress)
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/progress', methods=['PATCH'])
def update_book_progress(book_id):
    """Update reading progress."""
    data = request.get_json() or {}
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Upsert progress
        cur.execute("""
            INSERT INTO book_progress
            (book_id, user_id, current_page, current_sentence, playback_speed, last_read_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (book_id, user_id) DO UPDATE SET
                current_page = COALESCE(EXCLUDED.current_page, book_progress.current_page),
                current_sentence = COALESCE(EXCLUDED.current_sentence, book_progress.current_sentence),
                playback_speed = COALESCE(EXCLUDED.playback_speed, book_progress.playback_speed),
                last_read_at = NOW()
            RETURNING *
        """, (
            book_id,
            user_id,
            data.get('current_page', 1),
            data.get('current_sentence', 0),
            data.get('playback_speed', 1.0)
        ))

        progress = row_to_dict(cur, cur.fetchone())
        conn.commit()

        # Prefetch upcoming audio pages (current page gets priority=500)
        try:
            from book_tasks import get_processor
            processor = get_processor(UPLOAD_FOLDER, AUDIO_FOLDER)
            if processor and data.get('current_page'):
                processor.prefetch_audio(
                    book_id,
                    data.get('current_page'),
                    voice_id=data.get('voice_id')
                )
        except Exception as e:
            logger.warning(f"Prefetch failed: {e}")

        return jsonify(progress)
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# BOOKMARKS
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/bookmarks', methods=['GET'])
def get_bookmarks(book_id):
    """Get all bookmarks for a book."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT * FROM bookmarks
            WHERE book_id = %s AND user_id = %s
            ORDER BY page_number, sentence_index
        """, (book_id, user_id))

        bookmarks = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_bookmark(b) for b in bookmarks])
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/bookmarks', methods=['POST'])
def create_bookmark(book_id):
    """Create a new bookmark."""
    data = request.get_json() or {}

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO bookmarks
            (book_id, user_id, page_number, sentence_index, label, color)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            book_id,
            data.get('user_id'),
            data.get('page_number'),
            data.get('sentence_index'),
            sanitize_input(data.get('label', '')),
            data.get('color', 'yellow')
        ))

        bookmark = row_to_dict(cur, cur.fetchone())
        conn.commit()

        return jsonify(serialize_bookmark(bookmark)), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/bookmarks/<int:bookmark_id>', methods=['DELETE'])
def delete_bookmark(bookmark_id):
    """Delete a bookmark."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM bookmarks WHERE id = %s', (bookmark_id,))
        conn.commit()
        return jsonify({'message': 'Bookmark deleted'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)


# ============================================================================
# ANNOTATIONS
# ============================================================================

@books_bp.route('/api/books/<int:book_id>/annotations', methods=['GET'])
def get_annotations(book_id):
    """Get all annotations for a book."""
    user_id = request.args.get('user_id', type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT * FROM annotations
            WHERE book_id = %s AND user_id = %s
            ORDER BY page_number, start_offset
        """, (book_id, user_id))

        annotations = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_annotation(a) for a in annotations])
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/books/<int:book_id>/annotations', methods=['POST'])
def create_annotation(book_id):
    """Create a new annotation/highlight."""
    data = request.get_json() or {}

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO annotations
            (book_id, user_id, page_number, start_offset, end_offset, highlighted_text, note, color)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            book_id,
            data.get('user_id'),
            data.get('page_number'),
            data.get('start_offset'),
            data.get('end_offset'),
            sanitize_input(data.get('highlighted_text', '')),
            sanitize_input(data.get('note', '')),
            data.get('color', 'yellow')
        ))

        annotation = row_to_dict(cur, cur.fetchone())
        conn.commit()

        return jsonify(serialize_annotation(annotation)), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)


@books_bp.route('/api/annotations/<int:annotation_id>', methods=['DELETE'])
def delete_annotation(annotation_id):
    """Delete an annotation."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM annotations WHERE id = %s', (annotation_id,))
        conn.commit()
        return jsonify({'message': 'Annotation deleted'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        release_connection(conn)
