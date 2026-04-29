"""
Production runner for Saga Microservice.

Usage:
    python run.py                   # Waitress on port 5001
    python run.py --port 8080       # Custom port
    python run.py --no-audio-worker # Skip background audio worker
"""

import os
import sys
import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


_audio_worker_running = True


def start_audio_worker():
    """Run the audio generation worker in a background thread."""
    global _audio_worker_running

    try:
        from run_book_audio_worker import (
            get_db_connection, get_next_jobs, process_job_wrapper,
            CONCURRENCY, POLL_INTERVAL
        )

        print(f"[AUDIO WORKER] Started — concurrency={CONCURRENCY}, poll={POLL_INTERVAL}s")
        idle_count = 0

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            while _audio_worker_running:
                try:
                    conn = get_db_connection()
                    jobs = get_next_jobs(conn, CONCURRENCY)
                    conn.close()

                    if jobs:
                        idle_count = 0
                        futures = {executor.submit(process_job_wrapper, job): job for job in jobs}
                        for future in as_completed(futures):
                            job = futures[future]
                            try:
                                if future.result():
                                    print(f"[AUDIO WORKER] Done: book {job['book_id']} p{job['page_number']}")
                            except Exception as e:
                                print(f"[AUDIO WORKER] Job error: {e}")
                    else:
                        idle_count += 1
                        if idle_count % 30 == 1:
                            print("[AUDIO WORKER] Queue empty, waiting...")
                        time.sleep(POLL_INTERVAL)

                except Exception as e:
                    print(f"[AUDIO WORKER] Loop error: {e}")
                    time.sleep(POLL_INTERVAL * 2)

        print("[AUDIO WORKER] Stopped")

    except ImportError as e:
        print(f"[AUDIO WORKER] Could not start: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run Saga microservice")
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--no-audio-worker', action='store_true')
    args = parser.parse_args()

    from app import app, init_db

    print("Initialising database...")
    init_db()

    if not args.no_audio_worker:
        t = threading.Thread(target=start_audio_worker, daemon=True)
        t.start()

    try:
        from waitress import serve
        print(f"Saga running at http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, threads=args.threads)
    except ImportError:
        print("Waitress not installed. Run: pip install waitress")
        sys.exit(1)


if __name__ == '__main__':
    main()
