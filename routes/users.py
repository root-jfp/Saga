"""Simple user management for the Book Reader microservice."""

from flask import Blueprint, request, jsonify
from utils.db import get_db_connection, release_connection, row_to_dict, rows_to_dict_list
from utils.helpers import sanitize_input, error_response

users_bp = Blueprint('users', __name__)


@users_bp.route('/api/users', methods=['GET'])
def get_users():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users ORDER BY created_at ASC")
        users = rows_to_dict_list(cur, cur.fetchall())
        return jsonify(users)
    except Exception as e:
        return error_response('Failed to fetch users', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@users_bp.route('/api/users', methods=['POST'])
def create_user():
    data = request.get_json() or {}
    name = sanitize_input(data.get('name', ''))
    avatar = sanitize_input(data.get('avatar', '📚')) or '📚'

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (name, avatar) VALUES (%s, %s) RETURNING *",
            (name, avatar)
        )
        user = row_to_dict(cur, cur.fetchone())
        conn.commit()
        return jsonify(user), 201
    except Exception as e:
        conn.rollback()
        return error_response('Failed to create user', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)


@users_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({'message': 'User deleted'}), 200
    except Exception as e:
        conn.rollback()
        return error_response('Failed to delete user', 500, str(e))
    finally:
        cur.close()
        release_connection(conn)
