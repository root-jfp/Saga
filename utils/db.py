"""Database utilities for the Saga microservice."""

import os
import logging
from datetime import datetime

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('book-reader')

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'database': os.getenv('DB_NAME', 'book_reader'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432'),
    'connect_timeout': 5,
    'options': '-c statement_timeout=30000'
}

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
        except Exception as e:
            logger.warning(f"Connection pool failed: {e}, using direct connections")
    return _pool


def get_db_connection():
    pool = _get_pool()
    if pool:
        return pool.getconn()
    return psycopg2.connect(**DB_CONFIG)


def release_connection(conn):
    pool = _get_pool()
    if pool:
        pool.putconn(conn)
    else:
        conn.close()


def row_to_dict(cursor, row):
    if row is None:
        return None
    return {desc[0]: value for desc, value in zip(cursor.description, row)}


def rows_to_dict_list(cursor, rows):
    if not rows:
        return []
    return [row_to_dict(cursor, row) for row in rows]


# ── Serializers ──────────────────────────────────────────────────────────────

def serialize_book(book):
    if book is None:
        return None
    book = dict(book)
    if book.get('created_at') and isinstance(book['created_at'], datetime):
        book['created_at'] = book['created_at'].isoformat()
    book['cover_image_path'] = f"/api/books/{book['id']}/thumbnail"

    # Don't leak filesystem paths in API responses.
    book.pop('storage_path', None)

    # Inline import: tts_generator transitively imports edge_tts which itself
    # touches network helpers — keep utils/db.py free of that dependency at
    # import time.
    from tts_generator import pick_default_voice
    book['recommended_voice_id'] = pick_default_voice(book.get('detected_language'))

    return book


def serialize_bookmark(bookmark):
    if bookmark is None:
        return None
    bookmark = dict(bookmark)
    if bookmark.get('created_at') and isinstance(bookmark['created_at'], datetime):
        bookmark['created_at'] = bookmark['created_at'].isoformat()
    return bookmark


def serialize_annotation(annotation):
    if annotation is None:
        return None
    annotation = dict(annotation)
    if annotation.get('created_at') and isinstance(annotation['created_at'], datetime):
        annotation['created_at'] = annotation['created_at'].isoformat()
    return annotation
