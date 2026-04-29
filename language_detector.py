"""
Language detection for book text.

Strategy:
  1. Unicode script fast-path — for Cyrillic, Hangul, Japanese kana, CJK Han,
     Devanagari, and Arabic, the script alone identifies the language with
     100% reliability. This bypasses langdetect entirely (and its known
     short-text quirks like fr→af).
  2. langdetect fallback for Latin-script languages, guarded by a lock
     because langdetect's DetectorFactory shares random state across threads.

Usage:
    from language_detector import detect_language

    result = detect_language(text)
    if result:
        lang_code, confidence = result   # e.g. ('en', 0.93)
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Tuple

logger = logging.getLogger('book-reader')

# ---------------------------------------------------------------------------
# Determinism — must be set once at import time before any detect() call.
# ---------------------------------------------------------------------------
try:
    from langdetect import DetectorFactory, detect_langs, LangDetectException
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False
    logger.warning("langdetect not installed. Language detection unavailable.")

# langdetect.DetectorFactory uses class-level shared state with a random seed.
# Concurrent detect_langs() calls from multiple worker threads can race and
# return nondeterministic results — this lock serialises them.
_DETECT_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD: float = 0.85
MIN_TEXT_LENGTH: int = 200  # chars — Latin-script langdetect needs this much
MIN_SCRIPT_TEXT_LENGTH: int = 50  # non-Latin scripts are decisive at much shorter lengths
SCRIPT_DOMINANCE_RATIO: float = 0.30  # script-fast-path needs >=30% of letters in target script


# ---------------------------------------------------------------------------
# Script-based fast path (deterministic, lock-free)
# ---------------------------------------------------------------------------

def _script_counts(text: str) -> dict:
    """Count letter chars per Unicode script we care about."""
    counts = {
        'cyrillic': 0, 'hangul': 0, 'hiragana': 0, 'katakana': 0,
        'han': 0, 'devanagari': 0, 'arabic': 0, 'latin': 0,
    }
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
            counts['cyrillic'] += 1
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
            counts['hangul'] += 1
        elif 0x3040 <= cp <= 0x309F:
            counts['hiragana'] += 1
        elif 0x30A0 <= cp <= 0x30FF:
            counts['katakana'] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            counts['han'] += 1
        elif 0x0900 <= cp <= 0x097F:
            counts['devanagari'] += 1
        elif 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0x08A0 <= cp <= 0x08FF:
            counts['arabic'] += 1
        elif 0x0041 <= cp <= 0x024F:
            counts['latin'] += 1
    return counts


def _detect_by_script(text: str) -> Optional[Tuple[str, float]]:
    """
    Identify language from Unicode script when the script is unambiguous.

    Returns (lang, 1.0) for scripts that map 1:1 to a language.
    Japanese is detected when kana (hiragana/katakana) is present, even
    alongside Han characters (kanji). Han alone → Chinese.

    Returns None if the script is Latin-only or no script dominates.
    """
    counts = _script_counts(text)
    total_letters = sum(counts.values())
    if total_letters == 0:
        return None

    kana = counts['hiragana'] + counts['katakana']

    # Japanese: any meaningful presence of kana (alongside or without kanji)
    if kana > 0 and kana / total_letters >= 0.05:
        return ('ja', 1.0)

    # Single-script languages — require dominance to avoid false positives
    # on mixed-script samples.
    candidates = [
        ('ko', counts['hangul']),
        ('zh', counts['han']),
        ('ru', counts['cyrillic']),
        ('hi', counts['devanagari']),
        ('ar', counts['arabic']),
    ]
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    top_lang, top_count = candidates[0]
    if top_count == 0:
        return None
    if top_count / total_letters >= SCRIPT_DOMINANCE_RATIO:
        return (top_lang, 1.0)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_language(text: Optional[str]) -> Optional[Tuple[str, float]]:
    """
    Detect the primary language of *text*.

    Args:
        text: Raw text to analyse.  May be None, empty, or very short.

    Returns:
        ``(iso_639_1_code, confidence)`` if detection succeeds.
        ``None`` for too-short, inconclusive, or error cases.
    """
    if not text or not isinstance(text, str):
        return None

    stripped = text.strip()
    if len(stripped) < MIN_SCRIPT_TEXT_LENGTH:
        return None

    # 1. Script-based fast path — deterministic, no langdetect.
    #    Non-Latin scripts are decisive at much shorter lengths than Latin.
    script_result = _detect_by_script(stripped)
    if script_result is not None:
        return script_result

    # 2. Latin-script: fall back to langdetect (with lock for thread safety).
    if len(stripped) < MIN_TEXT_LENGTH:
        return None
    if not LANGDETECT_AVAILABLE:
        logger.warning("detect_language called but langdetect is not installed")
        return None

    try:
        with _DETECT_LOCK:
            candidates = detect_langs(stripped)
    except LangDetectException:
        return None
    except Exception as exc:
        logger.warning("Language detection failed: %s", exc)
        return None

    if not candidates:
        return None

    top = candidates[0]
    if top.prob < CONFIDENCE_THRESHOLD:
        return None

    # Normalise to lowercase ISO 639-1 (first two chars before any region tag).
    code = top.lang.split('-')[0].split('_')[0].lower()[:2]
    return (code, float(top.prob))
