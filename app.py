"""
Saga Microservice
Standalone Flask app — runs independently of the Life Planner.

Usage:
    python app.py                     # Dev server on port 5001
    python app.py --port 8080         # Custom port
    python run.py                     # Production (Waitress)
"""

import os
import sys
import argparse
import logging
from datetime import datetime

from flask import Flask, render_template, jsonify, send_from_directory
from flask_cors import CORS

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('book-reader')

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'book-reader-secret-change-me')
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB upload limit

CORS(app)

# ── Database init ─────────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist."""
    from utils.db import get_db_connection, release_connection
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            cur.execute(f.read())
        conn.commit()
        logger.info("Database initialised")
    except Exception as e:
        conn.rollback()
        logger.error(f"DB init failed: {e}")
        raise
    finally:
        cur.close()
        release_connection(conn)

# ── Page routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'book-reader'})

# Service worker must be served from the site root so its default scope
# covers the whole app — otherwise the browser limits scope to /static/.
@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/manifest.webmanifest')
def manifest():
    return send_from_directory('static', 'manifest.webmanifest',
                               mimetype='application/manifest+json')

# ── API blueprints ────────────────────────────────────────────────────────────

from routes import register_blueprints
register_blueprints(app)

# ── Entry point ───────────────────────────────────────────────────────────────

def _start_audio_worker_thread():
    """Start the audio generation worker in a daemon background thread."""
    import threading, time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run():
        try:
            from run_book_audio_worker import (
                get_db_connection, get_next_jobs, process_job_wrapper,
                CONCURRENCY, POLL_INTERVAL
            )
            logger.info(f"[AUDIO WORKER] Started — concurrency={CONCURRENCY}, poll={POLL_INTERVAL}s")
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
                while True:
                    try:
                        conn = get_db_connection()
                        jobs = get_next_jobs(conn, CONCURRENCY)
                        conn.close()
                        if jobs:
                            futures = {executor.submit(process_job_wrapper, j): j for j in jobs}
                            for future in as_completed(futures):
                                j = futures[future]
                                try:
                                    if future.result():
                                        logger.info(f"[AUDIO WORKER] Done: book {j['book_id']} p{j['page_number']}")
                                except Exception as e:
                                    logger.warning(f"[AUDIO WORKER] Job error: {e}")
                        else:
                            time.sleep(POLL_INTERVAL)
                    except Exception as e:
                        logger.warning(f"[AUDIO WORKER] Loop error: {e}")
                        time.sleep(POLL_INTERVAL * 2)
        except ImportError as e:
            logger.error(f"[AUDIO WORKER] Could not import worker: {e}")

    t = threading.Thread(target=_run, daemon=True, name="AudioWorker")
    t.start()
    return t


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    args = parser.parse_args()

    logger.info("Initialising database...")
    init_db()

    logger.info("Starting audio worker thread...")
    _start_audio_worker_thread()

    logger.info(f"Starting Saga on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
