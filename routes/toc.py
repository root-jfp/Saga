"""Table-of-contents endpoints.

The TOC is captured at upload time from `fitz.Document.get_toc()` and stored
as JSON on `books.toc`. If the PDF has no embedded outline we fall back to
detected headings on `book_pages.sentences` (`is_heading=True`).
"""

import json
import logging
from flask import Blueprint, jsonify

from utils.db import get_db_connection, release_connection
from utils.helpers import error_response

logger = logging.getLogger('book-reader')

toc_bp = Blueprint('toc', __name__)


@toc_bp.route('/api/books/<int:book_id>/toc', methods=['GET'])
def get_toc(book_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT toc, total_pages FROM books WHERE id = %s", (book_id,))
        row = cur.fetchone()
        if not row:
            return error_response('book not found', 404)
        toc, total_pages = row

        # Embedded outline path
        if toc:
            entries = toc if isinstance(toc, list) else json.loads(toc)
            return jsonify({'source': 'pdf_outline', 'entries': entries, 'total_pages': total_pages})

        # Fallback: synthesise a TOC from detected headings.
        cur.execute("""
            SELECT page_number, sentences FROM book_pages
            WHERE book_id = %s AND sentences IS NOT NULL
            ORDER BY page_number ASC
        """, (book_id,))
        synthetic = []
        for page_number, sentences in cur.fetchall():
            if not sentences:
                continue
            sentence_list = sentences if isinstance(sentences, list) else json.loads(sentences)
            for sent in sentence_list:
                if sent.get('is_heading'):
                    text = (sent.get('text') or '').strip()
                    if text and len(text) <= 160:
                        synthetic.append({
                            'level': 1,
                            'title': text,
                            'page':  page_number,
                        })
                        break  # one per page is enough — first heading wins

        return jsonify({
            'source': 'detected_headings' if synthetic else 'none',
            'entries': synthetic,
            'total_pages': total_pages,
        })
    finally:
        cur.close()
        release_connection(conn)
