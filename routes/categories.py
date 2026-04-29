"""Book category management for the Saga microservice.

Categories are per-user collections (Fantasy, Sci-Fi, Self-Help...). They can
nest one level via parent_id so a Fantasy category can have a High Fantasy
child. Each category carries one of three visuals: an emoji, a preset image
key, or an uploaded image file.
"""

import os
import re
import time as time_module
from werkzeug.utils import secure_filename
from flask import Blueprint, request, jsonify, send_file

from utils.db import get_db_connection, release_connection, row_to_dict, rows_to_dict_list
from utils.helpers import sanitize_input, error_response

categories_bp = Blueprint('categories', __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
CATEGORY_IMAGE_FOLDER = os.path.join(UPLOAD_FOLDER, 'category_images')
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_NAME_LENGTH = 120

os.makedirs(CATEGORY_IMAGE_FOLDER, exist_ok=True)


# Stock visual presets the UI can render with a known emoji/colour combo.
# Each preset also points to an Iconify icon URL — Iconify serves SVGs with no
# auth and no rate-limiting from a CDN, so we can render proper illustrated
# tiles instead of bare emojis. Mostly Phosphor (ph:) with a couple of
# game-icons (gi:) where Phosphor lacks a fitting symbol.
def _iconify(prefix_name: str, colour_hex: str, size: int = 200) -> str:
    """Build an Iconify SVG URL with a fixed colour and size.

    Iconify pattern: https://api.iconify.design/<prefix>/<name>.svg
    Note: Iconify expects the slash form, not the prefix:name colon form.
    """
    return f"https://api.iconify.design/{prefix_name}.svg?color=%23{colour_hex.lstrip('#')}&width={size}"


PRESETS = [
    {'key': 'fantasy',    'label': 'Fantasy',    'emoji': '🐉', 'colour': '#7c3aed', 'icon': _iconify('game-icons/dragon-head',      'ffffff')},
    {'key': 'scifi',      'label': 'Sci-Fi',     'emoji': '🚀', 'colour': '#0ea5e9', 'icon': _iconify('ph/rocket-launch-bold',       'ffffff')},
    {'key': 'mystery',    'label': 'Mystery',    'emoji': '🕵️', 'colour': '#475569', 'icon': _iconify('ph/magnifying-glass-bold',     'ffffff')},
    {'key': 'romance',    'label': 'Romance',    'emoji': '💖', 'colour': '#ec4899', 'icon': _iconify('ph/heart-fill',                'ffffff')},
    {'key': 'thriller',   'label': 'Thriller',   'emoji': '🗡️', 'colour': '#b91c1c', 'icon': _iconify('game-icons/curvy-knife',      'ffffff')},
    {'key': 'history',    'label': 'History',    'emoji': '🏛️', 'colour': '#a16207', 'icon': _iconify('ph/bank-bold',                 'ffffff')},
    {'key': 'biography',  'label': 'Biography',  'emoji': '👤', 'colour': '#0d9488', 'icon': _iconify('ph/user-circle-bold',          'ffffff')},
    {'key': 'selfhelp',   'label': 'Self-Help',  'emoji': '🌱', 'colour': '#16a34a', 'icon': _iconify('ph/plant-bold',                'ffffff')},
    {'key': 'business',   'label': 'Business',   'emoji': '💼', 'colour': '#1e293b', 'icon': _iconify('ph/briefcase-bold',            'ffffff')},
    {'key': 'children',   'label': 'Children',   'emoji': '🧸', 'colour': '#f59e0b', 'icon': _iconify('game-icons/teddy-bear',       'ffffff')},
    {'key': 'horror',     'label': 'Horror',     'emoji': '👻', 'colour': '#3f3f46', 'icon': _iconify('ph/ghost-bold',                'ffffff')},
    {'key': 'philosophy', 'label': 'Philosophy', 'emoji': '🦉', 'colour': '#6366f1', 'icon': _iconify('ph/lightbulb-filament-bold',   'ffffff')},
    {'key': 'poetry',     'label': 'Poetry',     'emoji': '🌙', 'colour': '#9333ea', 'icon': _iconify('ph/moon-stars-bold',           'ffffff')},
    {'key': 'cooking',    'label': 'Cooking',    'emoji': '🍳', 'colour': '#ea580c', 'icon': _iconify('ph/cooking-pot-bold',          'ffffff')},
    {'key': 'travel',     'label': 'Travel',     'emoji': '🗺️', 'colour': '#0891b2', 'icon': _iconify('ph/airplane-tilt-bold',        'ffffff')},
    {'key': 'science',    'label': 'Science',    'emoji': '🔬', 'colour': '#0284c7', 'icon': _iconify('ph/flask-bold',                'ffffff')},
    {'key': 'art',        'label': 'Art',        'emoji': '🎨', 'colour': '#db2777', 'icon': _iconify('ph/paint-brush-bold',          'ffffff')},
    {'key': 'religion',   'label': 'Religion',   'emoji': '🕊️', 'colour': '#0f766e', 'icon': _iconify('ph/church-bold',               'ffffff')},
    {'key': 'comics',     'label': 'Comics',     'emoji': '💥', 'colour': '#dc2626', 'icon': _iconify('ph/lightning-bold',            'ffffff')},
    {'key': 'general',    'label': 'General',    'emoji': '📚', 'colour': '#6b7280', 'icon': _iconify('ph/books-bold',                'ffffff')},
]
PRESET_KEYS = frozenset(p['key'] for p in PRESETS)


def _serialize_category(row, book_count=0):
    return {
        'id': row['id'],
        'user_id': row['user_id'],
        'parent_id': row['parent_id'],
        'name': row['name'],
        'emoji': row['emoji'],
        'image_path': bool(row.get('image_path')),  # don't leak server path
        'preset_image': row['preset_image'],
        'sort_order': row['sort_order'],
        'book_count': book_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Presets
# ─────────────────────────────────────────────────────────────────────────────

@categories_bp.route('/api/categories/presets', methods=['GET'])
def list_presets():
    return jsonify(PRESETS)


# ─────────────────────────────────────────────────────────────────────────────
# Categories CRUD
# ─────────────────────────────────────────────────────────────────────────────

@categories_bp.route('/api/categories', methods=['GET'])
def list_categories():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return error_response('user_id is required', 400)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.*, COALESCE(b.cnt, 0) AS book_count
            FROM book_categories c
            LEFT JOIN (
                SELECT category_id, COUNT(*) AS cnt
                FROM books WHERE user_id = %s GROUP BY category_id
            ) b ON b.category_id = c.id
            WHERE c.user_id = %s
            ORDER BY c.parent_id NULLS FIRST, c.sort_order, c.name
        """, (user_id, user_id))
        rows = rows_to_dict_list(cur, cur.fetchall())

        # Count uncategorised books (category_id IS NULL).
        cur.execute("""
            SELECT COUNT(*) FROM books
            WHERE user_id = %s AND category_id IS NULL
        """, (user_id,))
        uncategorised = cur.fetchone()[0]

        return jsonify({
            'categories': [_serialize_category(r, r.get('book_count', 0)) for r in rows],
            'uncategorised_count': uncategorised,
        })
    finally:
        cur.close()
        release_connection(conn)


@categories_bp.route('/api/categories', methods=['POST'])
def create_category():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    name = sanitize_input(data.get('name', ''))
    parent_id = data.get('parent_id')
    emoji = (data.get('emoji') or '').strip() or None
    preset = (data.get('preset_image') or '').strip() or None

    if not user_id or not name:
        return error_response('user_id and name are required', 400)
    if len(name) > MAX_NAME_LENGTH:
        return error_response(f'name too long (max {MAX_NAME_LENGTH})', 400)
    if preset and preset not in PRESET_KEYS:
        return error_response('unknown preset_image', 400)
    if emoji and len(emoji) > 16:
        return error_response('emoji too long', 400)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # If parent_id provided, ensure it belongs to the same user and isn't itself a child.
        if parent_id is not None:
            cur.execute(
                "SELECT user_id, parent_id FROM book_categories WHERE id = %s",
                (parent_id,)
            )
            row = cur.fetchone()
            if not row:
                return error_response('parent_id not found', 400)
            if row[0] != user_id:
                return error_response('parent_id belongs to another user', 403)
            if row[1] is not None:
                return error_response('cannot nest categories deeper than one level', 400)

        cur.execute("""
            INSERT INTO book_categories (user_id, parent_id, name, emoji, preset_image)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
        """, (user_id, parent_id, name, emoji, preset))
        category = row_to_dict(cur, cur.fetchone())
        conn.commit()
        return jsonify(_serialize_category(category, 0)), 201
    except Exception as e:
        conn.rollback()
        if 'unique' in str(e).lower():
            return error_response('a category with that name already exists here', 409)
        return error_response('Failed to create category', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@categories_bp.route('/api/categories/<int:category_id>', methods=['PATCH'])
def update_category(category_id):
    data = request.get_json() or {}
    fields, values = [], []

    if 'name' in data:
        name = sanitize_input(data['name'])
        if not name or len(name) > MAX_NAME_LENGTH:
            return error_response('invalid name', 400)
        fields.append('name = %s'); values.append(name)
    if 'emoji' in data:
        emoji = (data['emoji'] or '').strip() or None
        if emoji and len(emoji) > 16:
            return error_response('emoji too long', 400)
        # Setting emoji clears any conflicting visuals.
        fields.append('emoji = %s'); values.append(emoji)
        if emoji:
            fields.append('preset_image = NULL')
            fields.append('image_path = NULL')
    if 'preset_image' in data:
        preset = (data['preset_image'] or '').strip() or None
        if preset and preset not in PRESET_KEYS:
            return error_response('unknown preset_image', 400)
        fields.append('preset_image = %s'); values.append(preset)
        if preset:
            fields.append('emoji = NULL')
            fields.append('image_path = NULL')
    if 'sort_order' in data:
        fields.append('sort_order = %s'); values.append(int(data['sort_order']))

    if not fields:
        return error_response('no updatable fields provided', 400)

    values.append(category_id)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE book_categories SET {', '.join(fields)}
            WHERE id = %s RETURNING *
        """, values)
        row = cur.fetchone()
        if not row:
            return error_response('category not found', 404)
        category = row_to_dict(cur, row)
        conn.commit()
        return jsonify(_serialize_category(category, 0))
    except Exception as e:
        conn.rollback()
        return error_response('Failed to update category', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@categories_bp.route('/api/categories/<int:category_id>', methods=['DELETE'])
def delete_category(category_id):
    """Delete a category. Books in it fall back to uncategorised
    (books.category_id is set to NULL by FK ON DELETE)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT image_path FROM book_categories WHERE id = %s", (category_id,))
        row = cur.fetchone()
        if not row:
            return error_response('category not found', 404)
        image_path = row[0]

        cur.execute("DELETE FROM book_categories WHERE id = %s", (category_id,))
        conn.commit()

        # Best-effort cleanup of an uploaded image; ignore failures.
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass
        return jsonify({'message': 'Category deleted'})
    except Exception as e:
        conn.rollback()
        return error_response('Failed to delete category', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Image upload + serving
# ─────────────────────────────────────────────────────────────────────────────

@categories_bp.route('/api/categories/<int:category_id>/image', methods=['POST'])
def upload_category_image(category_id):
    if 'file' not in request.files:
        return error_response('No file provided', 400)
    file = request.files['file']
    if not file or not file.filename:
        return error_response('No file provided', 400)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return error_response(f'Unsupported image type {ext}', 400)

    # Size guard
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_IMAGE_BYTES:
        return error_response(
            f'Image too large (max {MAX_IMAGE_BYTES // (1024 * 1024)}MB)', 400
        )

    safe_name = secure_filename(file.filename)
    timestamp = int(time_module.time())
    storage_filename = f"category_{category_id}_{timestamp}_{safe_name}"
    storage_path = os.path.join(CATEGORY_IMAGE_FOLDER, storage_filename)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT image_path FROM book_categories WHERE id = %s",
            (category_id,)
        )
        row = cur.fetchone()
        if not row:
            return error_response('category not found', 404)
        previous_path = row[0]

        file.save(storage_path)

        cur.execute("""
            UPDATE book_categories
            SET image_path = %s, emoji = NULL, preset_image = NULL
            WHERE id = %s RETURNING *
        """, (storage_path, category_id))
        category = row_to_dict(cur, cur.fetchone())
        conn.commit()

        # Remove the previous image only after the new one is committed.
        if previous_path and os.path.exists(previous_path) and previous_path != storage_path:
            try:
                os.remove(previous_path)
            except OSError:
                pass

        return jsonify(_serialize_category(category, 0)), 201
    except Exception as e:
        conn.rollback()
        if os.path.exists(storage_path):
            try:
                os.remove(storage_path)
            except OSError:
                pass
        return error_response('Failed to upload image', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@categories_bp.route('/api/categories/<int:category_id>/image', methods=['GET'])
def get_category_image(category_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT image_path FROM book_categories WHERE id = %s",
            (category_id,)
        )
        row = cur.fetchone()
        if not row or not row[0] or not os.path.exists(row[0]):
            return error_response('image not found', 404)
        return send_file(row[0])
    finally:
        cur.close()
        release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Book → Category assignment
# ─────────────────────────────────────────────────────────────────────────────

@categories_bp.route('/api/books/<int:book_id>/category', methods=['PUT'])
def set_book_category(book_id):
    data = request.get_json() or {}
    category_id = data.get('category_id')  # may be None to clear

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if category_id is not None:
            cur.execute(
                "SELECT user_id FROM book_categories WHERE id = %s",
                (category_id,)
            )
            cat_row = cur.fetchone()
            if not cat_row:
                return error_response('category not found', 404)
            cur.execute("SELECT user_id FROM books WHERE id = %s", (book_id,))
            book_row = cur.fetchone()
            if not book_row:
                return error_response('book not found', 404)
            if cat_row[0] != book_row[0]:
                return error_response('category belongs to another user', 403)

        cur.execute(
            "UPDATE books SET category_id = %s WHERE id = %s RETURNING id",
            (category_id, book_id)
        )
        if not cur.fetchone():
            return error_response('book not found', 404)
        conn.commit()
        return jsonify({'book_id': book_id, 'category_id': category_id})
    except Exception as e:
        conn.rollback()
        return error_response('Failed to set category', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)
