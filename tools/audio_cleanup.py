"""Audio cache hygiene tool.

Use cases:
  1. Find and remove orphan mp3s (file exists, no DB row references it).
  2. Find DB rows pointing at missing files (so we can null them out and the
     route auto-regenerates next time).
  3. Cap cache size: when total mp3 bytes exceed --max-mb, evict files
     belonging to the least-recently-played books until under the cap.

Run:
    python -m tools.audio_cleanup --dry-run
    python -m tools.audio_cleanup --orphans --apply
    python -m tools.audio_cleanup --max-mb 1024 --apply
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Iterable, Tuple

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

AUDIO_FOLDER = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'uploads', 'audio')
)


def _connect():
    return psycopg2.connect(**DB_CONFIG)


def _list_audio_files() -> list[str]:
    if not os.path.isdir(AUDIO_FOLDER):
        return []
    return [f for f in os.listdir(AUDIO_FOLDER) if f.lower().endswith('.mp3')]


def _bytes_human(n: int) -> str:
    if n >= 1024 * 1024 * 1024:
        return f'{n / 1024 / 1024 / 1024:.2f} GB'
    if n >= 1024 * 1024:
        return f'{n / 1024 / 1024:.1f} MB'
    if n >= 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n} B'


def find_orphans() -> list[str]:
    """Files on disk that no `book_pages.audio_path` row references."""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT audio_path FROM book_pages WHERE audio_path IS NOT NULL")
        referenced = {os.path.basename(p[0]) for p in cur.fetchall() if p[0]}
    finally:
        cur.close(); conn.close()

    on_disk = set(_list_audio_files())
    return sorted(on_disk - referenced)


def find_missing() -> list[Tuple[int, int, str]]:
    """DB rows referring to files that don't exist on disk."""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT book_id, page_number, audio_path
            FROM book_pages
            WHERE audio_path IS NOT NULL
        """)
        rows = cur.fetchall()
    finally:
        cur.close(); conn.close()

    return [(b, p, ap) for b, p, ap in rows if not (ap and os.path.exists(ap))]


def total_cache_bytes() -> int:
    return sum(
        os.path.getsize(os.path.join(AUDIO_FOLDER, f))
        for f in _list_audio_files()
    )


def evict_to_cap(cap_bytes: int, apply: bool) -> tuple[int, int]:
    """Evict oldest-played books' audio until total cache < cap_bytes."""
    current = total_cache_bytes()
    if current <= cap_bytes:
        return 0, 0

    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT b.id, MAX(bp.last_read_at) AS last_read, b.title
            FROM books b
            LEFT JOIN book_progress bp ON bp.book_id = b.id
            GROUP BY b.id, b.title
            ORDER BY MAX(bp.last_read_at) ASC NULLS FIRST, b.id ASC
        """)
        candidates = cur.fetchall()
    finally:
        cur.close(); conn.close()

    removed_bytes = 0
    removed_count = 0
    for book_id, last_read, title in candidates:
        if current - removed_bytes <= cap_bytes:
            break
        prefix = f'book_{book_id}_page_'
        files = [f for f in _list_audio_files() if f.startswith(prefix)]
        if not files:
            continue
        size = sum(os.path.getsize(os.path.join(AUDIO_FOLDER, f)) for f in files)
        print(f'  evict book {book_id} ({last_read or "never"}) "{title[:40]}": {len(files)} files / {_bytes_human(size)}')
        if apply:
            for f in files:
                try:
                    os.remove(os.path.join(AUDIO_FOLDER, f))
                except OSError as exc:
                    print(f'    failed: {f}: {exc}')
            # Reset DB rows for that book
            conn = _connect(); cur = conn.cursor()
            try:
                cur.execute("""
                    UPDATE book_pages
                    SET audio_path = NULL, audio_duration_seconds = NULL,
                        audio_timing = NULL, audio_status = 'pending', audio_voice_id = NULL
                    WHERE book_id = %s
                """, (book_id,))
                cur.execute("""
                    UPDATE books
                    SET audio_pages_completed = 0,
                        audio_generation_status = 'pending',
                        audio_voice_settings_hash = NULL
                    WHERE id = %s
                """, (book_id,))
                conn.commit()
            finally:
                cur.close(); conn.close()
        removed_bytes += size
        removed_count += len(files)

    return removed_count, removed_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Audio cache hygiene')
    parser.add_argument('--orphans', action='store_true',
                        help='delete files on disk with no DB reference')
    parser.add_argument('--missing', action='store_true',
                        help='null out DB rows pointing at missing files (forces regen)')
    parser.add_argument('--max-mb', type=int, default=0,
                        help='evict least-recently-played books until cache <= this size (in MB)')
    parser.add_argument('--apply', action='store_true',
                        help='actually delete; default is dry run')
    parser.add_argument('--dry-run', action='store_true',
                        help='show what would change (the default behaviour)')
    args = parser.parse_args(argv)

    apply = args.apply and not args.dry_run

    if not (args.orphans or args.missing or args.max_mb):
        # Default: report everything.
        args.orphans = True
        args.missing = True

    print(f'audio dir: {AUDIO_FOLDER}')
    files = _list_audio_files()
    total = total_cache_bytes()
    print(f'cache: {len(files)} files / {_bytes_human(total)}')

    if args.orphans:
        orphans = find_orphans()
        bytes_orphan = sum(
            os.path.getsize(os.path.join(AUDIO_FOLDER, f))
            for f in orphans if os.path.exists(os.path.join(AUDIO_FOLDER, f))
        )
        print(f'\nORPHANS: {len(orphans)} files / {_bytes_human(bytes_orphan)}')
        for f in orphans[:10]:
            print(f'  {f}')
        if len(orphans) > 10:
            print(f'  … and {len(orphans) - 10} more')
        if apply and orphans:
            for f in orphans:
                try:
                    os.remove(os.path.join(AUDIO_FOLDER, f))
                except OSError as exc:
                    print(f'  failed: {f}: {exc}')
            print(f'  removed {len(orphans)} orphan files')

    if args.missing:
        missing = find_missing()
        print(f'\nMISSING: {len(missing)} DB rows reference files that no longer exist')
        for b, p, ap in missing[:10]:
            print(f'  book {b} page {p}: {ap}')
        if apply and missing:
            conn = _connect(); cur = conn.cursor()
            try:
                ids = [(b, p) for b, p, _ in missing]
                cur.executemany("""
                    UPDATE book_pages
                    SET audio_path=NULL, audio_status='pending', audio_voice_id=NULL,
                        audio_duration_seconds=NULL, audio_timing=NULL
                    WHERE book_id=%s AND page_number=%s
                """, ids)
                conn.commit()
                print(f'  reset {len(ids)} rows')
            finally:
                cur.close(); conn.close()

    if args.max_mb:
        cap = args.max_mb * 1024 * 1024
        print(f'\nCAP CHECK: cap={_bytes_human(cap)} current={_bytes_human(total)}')
        n, by = evict_to_cap(cap, apply)
        print(f'  would evict {n} files / {_bytes_human(by)}' if not apply else
              f'  evicted {n} files / {_bytes_human(by)}')

    if not apply and (args.orphans or args.missing or args.max_mb):
        print('\n(dry run — pass --apply to make changes)')

    return 0


if __name__ == '__main__':
    sys.exit(main())
