"""Smoke tests for tools/audio_cleanup.py.

The tool reaches into the live filesystem + DB; here we just import it and
verify the helpers work and a dry-run on the real environment doesn't crash.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools import audio_cleanup


def test_bytes_human_formats_units():
    assert audio_cleanup._bytes_human(0) == '0 B'
    assert audio_cleanup._bytes_human(2048).endswith('KB')
    assert audio_cleanup._bytes_human(2 * 1024 * 1024).endswith('MB')
    assert audio_cleanup._bytes_human(2 * 1024 * 1024 * 1024).endswith('GB')


def test_list_audio_files_returns_list_of_str():
    files = audio_cleanup._list_audio_files()
    assert isinstance(files, list)
    assert all(isinstance(f, str) for f in files)
    assert all(f.lower().endswith('.mp3') for f in files)


def test_total_cache_bytes_is_nonnegative_int():
    n = audio_cleanup.total_cache_bytes()
    assert isinstance(n, int)
    assert n >= 0


def test_dry_run_main_exits_clean(monkeypatch):
    """A pure dry-run with --orphans should exit 0 and not raise."""
    rc = audio_cleanup.main(['--orphans', '--dry-run'])
    assert rc == 0


def test_find_orphans_returns_list():
    orphans = audio_cleanup.find_orphans()
    assert isinstance(orphans, list)
    assert all(isinstance(o, str) for o in orphans)


def test_find_missing_returns_list_of_tuples():
    missing = audio_cleanup.find_missing()
    assert isinstance(missing, list)
    for entry in missing[:5]:
        assert len(entry) == 3
        b, p, ap = entry
        assert isinstance(b, int)
        assert isinstance(p, int)
        assert isinstance(ap, str)
