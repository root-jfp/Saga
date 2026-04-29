"""Backfill sentence-level audio_timing for pages whose timings were stored
as word-level data by an older worker.

The frontend's seekToSentence() expects book_pages.audio_timing to be a list
with one entry per displayed sentence. Older builds (run_book_audio_worker
prior to the sentence-timing fix) wrote edge-tts WordBoundary events here
instead, which made click-to-seek jump to the wrong place.

This tool finds every completed page where len(audio_timing) doesn't match
len(sentences) and recomputes a character-proportional sentence timing using
the page's stored audio_duration_seconds. No audio is regenerated.

Run:
    python -m tools.backfill_sentence_timings --dry-run
    python -m tools.backfill_sentence_timings --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'book_reader'),
    'user':     os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port':     os.getenv('DB_PORT', '5432'),
}


def _compute(sentences, duration):
    if not sentences or duration <= 0:
        return []
    total = sum(len((s or {}).get('text', '')) for s in sentences)
    if total == 0:
        return []
    out = []
    elapsed = 0.0
    for s in sentences:
        chars = max(len((s or {}).get('text', '')), 1)
        d = duration * (chars / total)
        out.append({'offset': round(elapsed, 4), 'duration': round(d, 4)})
        elapsed += d
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='Write changes to the DB.')
    ap.add_argument('--dry-run', action='store_true', help='Preview only (default).')
    args = ap.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, book_id, page_number, sentences, audio_timing,
               audio_duration_seconds
        FROM book_pages
        WHERE audio_status = 'ready'
          AND audio_path IS NOT NULL
          AND audio_duration_seconds IS NOT NULL
          AND audio_duration_seconds > 0
    """)

    rows = cur.fetchall()
    print(f"Scanning {len(rows)} completed pages…")

    fixes = []
    for row in rows:
        page_id, book_id, page_number, sentences, audio_timing, duration = row
        if isinstance(sentences, str):
            try:
                sentences = json.loads(sentences)
            except Exception:
                continue
        if isinstance(audio_timing, str):
            try:
                audio_timing = json.loads(audio_timing)
            except Exception:
                audio_timing = None
        if not isinstance(sentences, list) or not sentences:
            continue

        # If timings already match the sentence count, leave them alone.
        if isinstance(audio_timing, list) and len(audio_timing) == len(sentences):
            continue

        new_timings = _compute(sentences, float(duration))
        if not new_timings:
            continue

        fixes.append((page_id, book_id, page_number, len(audio_timing or []), len(new_timings)))

    print(f"\n{len(fixes)} page(s) need backfill.")
    for page_id, book_id, page_number, old_n, new_n in fixes[:10]:
        print(f"  book={book_id} p={page_number}: {old_n} -> {new_n} entries")
    if len(fixes) > 10:
        print(f"  …and {len(fixes) - 10} more")

    if not fixes:
        cur.close(); conn.close()
        return

    if not args.apply:
        print("\nDry run — pass --apply to write changes.")
        cur.close(); conn.close()
        return

    print("\nApplying…")
    for page_id, book_id, page_number, _, _ in fixes:
        cur.execute(
            "SELECT sentences, audio_duration_seconds FROM book_pages WHERE id = %s",
            (page_id,),
        )
        sents, dur = cur.fetchone()
        if isinstance(sents, str):
            sents = json.loads(sents)
        new_timings = _compute(sents, float(dur))
        cur.execute(
            "UPDATE book_pages SET audio_timing = %s WHERE id = %s",
            (json.dumps(new_timings), page_id),
        )
    conn.commit()
    print(f"Updated {len(fixes)} page(s).")
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
