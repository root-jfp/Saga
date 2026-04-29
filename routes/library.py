"""Library shelf endpoints: continue-listening, search, sort, stats.

These are read-mostly endpoints used by the new home screen. Kept separate
from `routes/books.py` so library-organisation logic doesn't bloat that file.
"""

import logging
from flask import Blueprint, request, jsonify

from utils.db import get_db_connection, release_connection, rows_to_dict_list, serialize_book
from utils.helpers import error_response

logger = logging.getLogger('book-reader')

library_bp = Blueprint('library', __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Continue listening
# ─────────────────────────────────────────────────────────────────────────────

@library_bp.route('/api/library/continue', methods=['GET'])
def continue_listening():
    """Books the user has touched recently and not yet finished, ordered by
    most-recent activity. Used by the home shelf at the top of the library.

    Query params:
        user_id (required)
        limit   (default 12)
        days    (default 30) — only books with last_read_at within this window
    """
    user_id = request.args.get('user_id', type=int)
    limit = min(max(request.args.get('limit', 12, type=int), 1), 50)
    days = max(request.args.get('days', 30, type=int), 1)

    if not user_id:
        return error_response('user_id is required', 400)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT b.*,
                   bp.current_page,
                   bp.current_sentence,
                   bp.playback_speed,
                   bp.last_read_at,
                   bp.total_time_read_seconds,
                   ROUND((bp.current_page::float / NULLIF(b.total_pages, 0)) * 100) AS progress_pct
            FROM books b
            JOIN book_progress bp
              ON bp.book_id = b.id AND bp.user_id = %s
            WHERE b.user_id = %s
              AND COALESCE(b.read_status, 'reading') != 'archived'
              AND bp.last_read_at >= NOW() - (%s || ' days')::interval
              AND COALESCE(b.read_status,'reading') != 'finished'
            ORDER BY bp.last_read_at DESC NULLS LAST
            LIMIT %s
        """, (user_id, user_id, str(days), limit))
        rows = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_book(r) for r in rows])
    except Exception as e:
        return error_response('Failed to fetch continue-listening shelf', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Library search & sort
# ─────────────────────────────────────────────────────────────────────────────

VALID_SORTS = {
    'recent':       'COALESCE(bp.last_read_at, b.created_at) DESC',
    'created':      'b.created_at DESC',
    'title':        "LOWER(b.title) ASC",
    'author':       "LOWER(COALESCE(b.author,'zzz')) ASC, LOWER(b.title) ASC",
    'progress':     'COALESCE((bp.current_page::float / NULLIF(b.total_pages, 0)), 0) DESC',
}


@library_bp.route('/api/library/search', methods=['GET'])
def search_library():
    """Search/sort the user's library.

    Query params:
        user_id          required
        q                full-text-ish over title/author/subjects (ILIKE)
        sort             one of: recent | created | title | author | progress  (default 'recent')
        category_id      int | 'null' (uncategorised) | absent (no filter)
        language         e.g. 'en' (filters by detected_language)
        status           reading | finished | archived (default: not archived)
        limit            default 100, max 500
    """
    user_id = request.args.get('user_id', type=int)
    q = (request.args.get('q') or '').strip()
    sort = request.args.get('sort', 'recent')
    category_filter = request.args.get('category_id')
    language = request.args.get('language')
    status = request.args.get('status')
    limit = min(max(request.args.get('limit', 100, type=int), 1), 500)

    if not user_id:
        return error_response('user_id is required', 400)
    if sort not in VALID_SORTS:
        return error_response(f'invalid sort (allowed: {sorted(VALID_SORTS)})', 400)

    where = ['b.user_id = %s']
    params = [user_id]

    # Status filter — by default exclude archived but include both reading + finished
    if status in ('reading', 'finished', 'archived'):
        where.append("COALESCE(b.read_status,'reading') = %s")
        params.append(status)
    else:
        where.append("COALESCE(b.read_status,'reading') != 'archived'")

    if q:
        where.append("""(
            b.title    ILIKE %s OR
            b.author   ILIKE %s OR
            b.subjects ILIKE %s
        )""")
        like = f'%{q}%'
        params.extend([like, like, like])

    if category_filter == 'null':
        where.append('b.category_id IS NULL')
    elif category_filter and category_filter != 'any':
        try:
            where.append('b.category_id = %s')
            params.append(int(category_filter))
        except ValueError:
            return error_response('invalid category_id', 400)

    if language:
        where.append('LOWER(b.detected_language) = %s')
        params.append(language.lower())

    sql = f"""
        SELECT b.*, bp.current_page, bp.playback_speed, bp.last_read_at
        FROM books b
        LEFT JOIN book_progress bp ON bp.book_id = b.id AND bp.user_id = %s
        WHERE {' AND '.join(where)}
        ORDER BY {VALID_SORTS[sort]}
        LIMIT %s
    """
    params = [user_id] + params + [limit]

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = rows_to_dict_list(cur, cur.fetchall())
        return jsonify([serialize_book(r) for r in rows])
    except Exception as e:
        return error_response('search failed', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Read-status (finish / archive / unarchive)
# ─────────────────────────────────────────────────────────────────────────────

@library_bp.route('/api/books/<int:book_id>/status', methods=['PUT'])
def set_book_status(book_id):
    data = request.get_json() or {}
    status = (data.get('status') or '').strip().lower()
    if status not in ('reading', 'finished', 'archived'):
        return error_response('status must be reading | finished | archived', 400)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE books SET read_status = %s WHERE id = %s RETURNING id",
            (status, book_id)
        )
        if not cur.fetchone():
            return error_response('book not found', 404)
        conn.commit()
        return jsonify({'book_id': book_id, 'read_status': status})
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Reading stats (light)
# ─────────────────────────────────────────────────────────────────────────────

@library_bp.route('/api/library/stats', methods=['GET'])
def reading_stats():
    user_id = request.args.get('user_id', type=int)
    days = min(max(request.args.get('days', 30, type=int), 1), 365)
    if not user_id:
        return error_response('user_id is required', 400)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE COALESCE(read_status,'reading') = 'reading'),
                   COUNT(*) FILTER (WHERE COALESCE(read_status,'reading') = 'finished'),
                   COUNT(*) FILTER (WHERE COALESCE(read_status,'reading') = 'archived'),
                   COUNT(*)
            FROM books WHERE user_id = %s
        """, (user_id,))
        reading_n, finished_n, archived_n, total_n = cur.fetchone()

        cur.execute("""
            SELECT COALESCE(SUM(seconds_listened), 0) AS sec,
                   COALESCE(SUM(pages_read), 0) AS pages,
                   COUNT(DISTINCT session_date) AS active_days
            FROM reading_sessions
            WHERE user_id = %s
              AND session_date >= CURRENT_DATE - (%s || ' days')::interval
        """, (user_id, str(days)))
        sec, pages, active_days = cur.fetchone()

        cur.execute("""
            SELECT session_date, SUM(seconds_listened) AS sec, SUM(pages_read) AS pages
            FROM reading_sessions
            WHERE user_id = %s
              AND session_date >= CURRENT_DATE - (%s || ' days')::interval
            GROUP BY session_date
            ORDER BY session_date ASC
        """, (user_id, str(days)))
        timeline = [
            {'date': str(d), 'seconds': int(s or 0), 'pages': int(p or 0)}
            for d, s, p in cur.fetchall()
        ]

        return jsonify({
            'reading': reading_n,
            'finished': finished_n,
            'archived': archived_n,
            'total': total_n,
            'window_days': days,
            'seconds_listened': int(sec or 0),
            'pages_read': int(pages or 0),
            'active_days': int(active_days or 0),
            'timeline': timeline,
        })
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Heartbeat: log seconds listened (called by the player every ~15s)
# ─────────────────────────────────────────────────────────────────────────────

@library_bp.route('/api/library/heartbeat', methods=['POST'])
def heartbeat():
    """Record listening activity into reading_sessions (UPSERT by date)."""
    data = request.get_json() or {}
    user_id = data.get('user_id')
    book_id = data.get('book_id')
    seconds = max(int(data.get('seconds') or 0), 0)
    pages = max(int(data.get('pages') or 0), 0)

    if not user_id or not book_id or seconds <= 0:
        return jsonify({'success': True, 'noop': True})

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO reading_sessions (user_id, book_id, session_date, seconds_listened, pages_read)
            VALUES (%s, %s, CURRENT_DATE, %s, %s)
            ON CONFLICT (user_id, book_id, session_date)
            DO UPDATE SET
                seconds_listened = reading_sessions.seconds_listened + EXCLUDED.seconds_listened,
                pages_read       = reading_sessions.pages_read + EXCLUDED.pages_read
        """, (user_id, book_id, seconds, pages))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return error_response('heartbeat failed', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)
