"""Helper utilities for the Book Reader microservice."""

import html
import logging
from flask import jsonify

logger = logging.getLogger('book-reader')

MAX_BOOK_TITLE_LENGTH = 500


def sanitize_input(text):
    if text is None:
        return None
    return html.escape(str(text).strip())


def error_response(message, status_code=500, log_error=None):
    if log_error:
        logger.error(f"{message}: {log_error}")
    return jsonify({'error': message}), status_code
