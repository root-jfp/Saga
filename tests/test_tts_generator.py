"""
Tests for extended TTS voice catalogue, cache, and pick_default_voice helper.

RED phase: written before implementation.
Run with: pytest tests/test_tts_generator.py -v
"""

import sys
import os
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ---------------------------------------------------------------------------
# Module-level imports (tested after implementation)
# ---------------------------------------------------------------------------

from tts_generator import (
    EDGE_VOICES,
    DEFAULT_VOICE,
    FEATURED_VOICES,
    FEATURED_VOICES_PREFERRED_REGION,
    refresh_voice_cache,
    get_voices_for_locale,
    pick_default_voice,
    _EDGE_VOICE_CACHE,
)


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    """FEATURED_VOICES and PREFERRED_REGION map are present and well-formed."""

    def test_default_voice_is_sonia(self):
        """DEFAULT_VOICE must remain en-GB-SoniaNeural (contract for fallback)."""
        assert DEFAULT_VOICE == 'en-GB-SoniaNeural'

    def test_featured_voices_is_dict(self):
        assert isinstance(FEATURED_VOICES, dict)

    def test_featured_voices_has_english_locales(self):
        """At minimum en-GB and en-US must be featured."""
        assert 'en-GB' in FEATURED_VOICES, "en-GB missing from FEATURED_VOICES"
        assert 'en-US' in FEATURED_VOICES, "en-US missing from FEATURED_VOICES"

    def test_featured_voices_has_portuguese_locales(self):
        """pt-BR and pt-PT must be featured."""
        assert 'pt-BR' in FEATURED_VOICES, "pt-BR missing from FEATURED_VOICES"
        assert 'pt-PT' in FEATURED_VOICES, "pt-PT missing from FEATURED_VOICES"

    def test_featured_voices_values_are_lists(self):
        for locale, voices in FEATURED_VOICES.items():
            assert isinstance(voices, list), f"{locale} value should be a list"
            assert len(voices) >= 1, f"{locale} list should not be empty"

    def test_preferred_region_is_dict(self):
        assert isinstance(FEATURED_VOICES_PREFERRED_REGION, dict)

    def test_portuguese_preferred_region_is_pt_br(self):
        """Plan specifies pt → pt-BR first."""
        assert FEATURED_VOICES_PREFERRED_REGION.get('pt') == 'pt-BR'

    def test_featured_voices_entries_are_valid_voice_ids(self):
        """Every voice ID in FEATURED_VOICES must be in the combined voice catalogue."""
        # Import the full catalogue helper
        from tts_generator import get_all_voices_sync
        catalogue_ids = {v['id'] for v in get_all_voices_sync()}
        for locale, voice_ids in FEATURED_VOICES.items():
            for vid in voice_ids:
                assert vid in catalogue_ids, (
                    f"Featured voice {vid!r} (locale {locale}) not in voice catalogue"
                )


# ---------------------------------------------------------------------------
# pick_default_voice
# ---------------------------------------------------------------------------

class TestPickDefaultVoice:
    """Unit tests for pick_default_voice(lang_code) helper."""

    def test_pick_default_voice_none_returns_default(self):
        """None lang → DEFAULT_VOICE."""
        result = pick_default_voice(None)
        assert result == DEFAULT_VOICE

    def test_pick_default_voice_unknown_lang_returns_default(self):
        """Unrecognised language code → DEFAULT_VOICE."""
        result = pick_default_voice('xx')
        assert result == DEFAULT_VOICE

    def test_pick_default_voice_en_returns_english_voice(self):
        """'en' → returns a voice whose locale starts with 'en-'."""
        result = pick_default_voice('en')
        assert result.startswith('en-'), (
            f"Expected English voice, got {result!r}"
        )

    def test_pick_default_voice_pt_prefers_br_region(self):
        """'pt' → voice from pt-BR (as per FEATURED_VOICES_PREFERRED_REGION)."""
        result = pick_default_voice('pt')
        assert result.startswith('pt-BR'), (
            f"Expected pt-BR voice for lang 'pt', got {result!r}"
        )

    def test_pick_default_voice_returns_string(self):
        """Return value is always a non-empty string."""
        for lang in ['en', 'pt', 'fr', 'de', 'es', None, 'zz']:
            result = pick_default_voice(lang)
            assert isinstance(result, str) and result, (
                f"pick_default_voice({lang!r}) returned non-string or empty: {result!r}"
            )

    def test_pick_default_voice_de_returns_german_voice_or_default(self):
        """'de' → German voice if available, else DEFAULT_VOICE (never raises)."""
        result = pick_default_voice('de')
        assert isinstance(result, str) and result


# ---------------------------------------------------------------------------
# Voice cache — refresh_voice_cache
# ---------------------------------------------------------------------------

class TestVoiceCacheFallback:
    """refresh_voice_cache falls back to hardcoded EDGE_VOICES on network error."""

    def test_fallback_on_network_error(self):
        """When edge_tts.list_voices raises, cache must still return voices."""
        import tts_generator as tg

        async def raise_network_error():
            raise ConnectionError("Network unreachable")

        with mock.patch('edge_tts.list_voices', side_effect=raise_network_error):
            cache = refresh_voice_cache()

        assert isinstance(cache, dict), "Cache should be a dict even on error"
        assert len(cache) > 0, "Cache must not be empty after fallback"
        # All fallback entries come from EDGE_VOICES
        for voice_id in EDGE_VOICES:
            assert voice_id in cache, f"{voice_id} missing from fallback cache"

    def test_fallback_cache_has_correct_structure(self):
        """Fallback entries must contain id, locale, gender keys."""

        async def raise_err():
            raise RuntimeError("Simulated failure")

        with mock.patch('edge_tts.list_voices', side_effect=raise_err):
            cache = refresh_voice_cache()

        for voice_id, info in cache.items():
            assert 'id' in info, f"{voice_id} missing 'id'"
            assert 'locale' in info, f"{voice_id} missing 'locale'"
            assert 'gender' in info, f"{voice_id} missing 'gender'"


# ---------------------------------------------------------------------------
# get_voices_for_locale
# ---------------------------------------------------------------------------

class TestGetVoicesForLocale:
    """get_voices_for_locale(locale) returns voices filtered by that locale."""

    def test_filters_by_exact_locale(self):
        """Only voices matching the given locale are returned."""
        voices = get_voices_for_locale('en-GB')
        for v in voices:
            assert v['locale'] == 'en-GB', (
                f"Expected locale en-GB, got {v['locale']}"
            )

    def test_returns_list(self):
        result = get_voices_for_locale('en-US')
        assert isinstance(result, list)

    def test_en_gb_has_sonia(self):
        """en-GB-SoniaNeural must appear in en-GB voices."""
        voices = get_voices_for_locale('en-GB')
        ids = [v['id'] for v in voices]
        assert 'en-GB-SoniaNeural' in ids, f"SoniaNeural not found in en-GB voices: {ids}"

    def test_unknown_locale_returns_empty(self):
        """Locale with no voices returns empty list (not error)."""
        result = get_voices_for_locale('xx-XX')
        assert result == [], f"Expected [], got {result!r}"

    def test_voices_have_required_fields(self):
        voices = get_voices_for_locale('en-US')
        required_fields = {'id', 'name', 'gender', 'locale', 'backend', 'quality'}
        for v in voices:
            missing = required_fields - set(v.keys())
            assert not missing, f"Voice {v.get('id')} missing fields: {missing}"


# ---------------------------------------------------------------------------
# Voice cache — _EDGE_VOICE_CACHE structure
# ---------------------------------------------------------------------------

class TestEdgeVoiceCache:
    """_EDGE_VOICE_CACHE is a dict keyed by ShortName / voice id."""

    def test_cache_is_dict(self):
        assert isinstance(_EDGE_VOICE_CACHE, dict)

    def test_cache_contains_hardcoded_voices(self):
        """All original EDGE_VOICES entries must be reachable in the cache."""
        for voice_id in EDGE_VOICES:
            assert voice_id in _EDGE_VOICE_CACHE, (
                f"{voice_id} missing from _EDGE_VOICE_CACHE"
            )
