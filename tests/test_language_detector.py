"""
Tests for language_detector.py module.

RED phase: written before implementation.
Run with: pytest tests/test_language_detector.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from language_detector import detect_language, CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Happy-path language detection
# ---------------------------------------------------------------------------

class TestDetectsKnownLanguages:
    """Clear single-language paragraphs should be detected at >= CONFIDENCE_THRESHOLD."""

    def test_detects_english_paragraph(self):
        """A clear English paragraph must return ('en', confidence >= 0.85)."""
        text = (
            "The quick brown fox jumps over the lazy dog. "
            "Language detection is an important part of natural language processing. "
            "English is widely spoken around the world and is used in many books. "
            "This sentence is here to ensure sufficient text length for detection."
        )
        result = detect_language(text)
        assert result is not None, "Should detect English, got None"
        lang, conf = result
        assert lang == 'en', f"Expected 'en', got {lang!r}"
        assert conf >= CONFIDENCE_THRESHOLD, f"Confidence {conf} < threshold {CONFIDENCE_THRESHOLD}"

    def test_detects_portuguese_paragraph(self):
        """A clear Portuguese paragraph must return ('pt', confidence >= 0.85)."""
        text = (
            "O rato roeu a roupa do rei de Roma. "
            "A língua portuguesa é falada em muitos países do mundo. "
            "Portugal e o Brasil são os dois maiores países lusófonos. "
            "Esta frase está aqui para garantir texto suficiente para deteção."
        )
        result = detect_language(text)
        assert result is not None, "Should detect Portuguese, got None"
        lang, conf = result
        assert lang == 'pt', f"Expected 'pt', got {lang!r}"
        assert conf >= CONFIDENCE_THRESHOLD, f"Confidence {conf} < threshold {CONFIDENCE_THRESHOLD}"

    def test_detects_french_paragraph(self):
        """A clear French paragraph should be detected as 'fr'."""
        text = (
            "Le français est une langue romane parlée dans de nombreux pays. "
            "La France est connue pour sa culture, sa gastronomie et son histoire. "
            "Paris est la capitale de la France et une ville très visitée. "
            "La langue française est également parlée en Belgique, en Suisse et au Canada."
        )
        result = detect_language(text)
        assert result is not None, "Should detect French, got None"
        lang, conf = result
        assert lang == 'fr', f"Expected 'fr', got {lang!r}"

    def test_detects_german_paragraph(self):
        """A clear German paragraph should be detected as 'de'."""
        text = (
            "Die deutsche Sprache wird von etwa 100 Millionen Menschen gesprochen. "
            "Deutschland ist bekannt für seine Ingenieurleistungen und Philosophie. "
            "Berlin ist die Hauptstadt von Deutschland und eine bedeutende Kulturstadt. "
            "Die Sprache hat viele Dialekte und regionale Variationen."
        )
        result = detect_language(text)
        assert result is not None, "Should detect German, got None"
        lang, conf = result
        assert lang == 'de', f"Expected 'de', got {lang!r}"


# ---------------------------------------------------------------------------
# Edge cases — should return None
# ---------------------------------------------------------------------------

class TestReturnsNone:
    """Cases where detect_language should return None."""

    def test_empty_string_returns_none(self):
        result = detect_language('')
        assert result is None, f"Expected None for empty string, got {result!r}"

    def test_none_input_returns_none(self):
        result = detect_language(None)
        assert result is None, f"Expected None for None input, got {result!r}"

    def test_whitespace_only_returns_none(self):
        result = detect_language('   \n\t  ')
        assert result is None, f"Expected None for whitespace, got {result!r}"

    def test_short_text_below_threshold_returns_none(self):
        """Text under 200 chars should return None (too short to be reliable)."""
        short_text = "Hello world. This is short."
        assert len(short_text) < 200, "Test text must be under 200 chars"
        result = detect_language(short_text)
        assert result is None, (
            f"Expected None for text under 200 chars, got {result!r}"
        )

    def test_garbage_numeric_text_returns_none(self):
        """Pure numbers and punctuation have no language signal."""
        garbage = "1234567890 !@#$%^&*() 9999.00 1000 2000 3000 4000 5000 6000"
        result = detect_language(garbage)
        # Either returns None (no language detected) or low confidence
        if result is not None:
            lang, conf = result
            assert conf < CONFIDENCE_THRESHOLD, (
                f"Garbage text returned confidence {conf} >= threshold"
            )

    def test_random_unicode_symbols_returns_none(self):
        """Random unicode that isn't real language text."""
        symbols = "★ ☆ ♥ ♦ ♣ ♠ ♪ ♫ ♬ ♭ ♮ ♯ ⊕ ⊗ ∞ ∑ ∏ √ ∂ " * 10
        result = detect_language(symbols)
        if result is not None:
            lang, conf = result
            assert conf < CONFIDENCE_THRESHOLD, (
                f"Symbol text returned confidence {conf} >= threshold"
            )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same input must always produce the same output (seed=0 set at import)."""

    def test_deterministic_seed_same_input_same_output(self):
        """Running detect_language twice on the same text yields identical results."""
        text = (
            "Language detection must be deterministic for reliable book processing. "
            "The seed ensures that results do not vary between runs. "
            "This is important for reproducibility in production systems. "
            "We test this by running the same text through detection twice."
        )
        result1 = detect_language(text)
        result2 = detect_language(text)
        assert result1 == result2, (
            f"Non-deterministic: first call {result1!r}, second call {result2!r}"
        )

    def test_different_texts_can_yield_different_results(self):
        """Sanity: an English and Portuguese text should NOT return the same language."""
        en_text = (
            "The English language has many words borrowed from French and Latin. "
            "It is the most widely spoken language in the world for business. "
            "Grammar rules in English can be complex for non-native speakers. "
            "Reading books in English improves vocabulary and comprehension skills."
        )
        pt_text = (
            "O português é uma língua bonita com muita história e tradição. "
            "Muitos livros importantes foram escritos em língua portuguesa. "
            "A literatura lusófona inclui autores de renome mundial como Pessoa. "
            "Ler em português é uma experiência cultural enriquecedora."
        )
        result_en = detect_language(en_text)
        result_pt = detect_language(pt_text)

        if result_en and result_pt:
            assert result_en[0] != result_pt[0], (
                f"Both texts detected as same language: {result_en[0]!r}"
            )


# ---------------------------------------------------------------------------
# Confidence threshold constant
# ---------------------------------------------------------------------------

class TestConfidenceThreshold:
    """CONFIDENCE_THRESHOLD must be 0.85 as specified in the plan."""

    def test_threshold_value(self):
        assert CONFIDENCE_THRESHOLD == 0.85, (
            f"Expected CONFIDENCE_THRESHOLD=0.85, got {CONFIDENCE_THRESHOLD}"
        )


# ---------------------------------------------------------------------------
# Script-based fast path (deterministic, lock-free)
# ---------------------------------------------------------------------------

class TestScriptFastPath:
    """Non-Latin scripts must be identified by Unicode block alone."""

    def test_detects_russian_by_cyrillic(self):
        text = (
            "Каждую зиму, ровно в полночь двадцать первого декабря, через маленький "
            "северный город проходит поезд, которого нет ни в одном расписании. "
            "Он появляется из метели, бесшумно останавливается у заброшенной станции."
        )
        result = detect_language(text)
        assert result == ('ru', 1.0), f"Expected ('ru', 1.0), got {result!r}"

    def test_detects_korean_by_hangul(self):
        text = (
            "서울의 한 골목길에는 보름달이 뜨는 밤에만 문을 여는 작은 빵집이 있다. "
            "주인은 말이 적은 젊은 여성으로, 그녀가 만드는 빵은 평범해 보이지만, "
            "한 입 베어 물면 잊고 있던 어린 시절의 기억이 떠오른다고 한다."
        )
        result = detect_language(text)
        assert result == ('ko', 1.0), f"Expected ('ko', 1.0), got {result!r}"

    def test_detects_japanese_by_kana(self):
        """Japanese must be identified by kana presence, not confused with Chinese."""
        text = (
            "東京の片隅に、夜にしか開かない小さな図書館があります。"
            "日が沈むとともに、古い木の扉が静かに開き、本好きの人々がひとり、"
            "またひとりと中に入っていきます。司書は白髪の老婦人で、"
            "彼女は訪れる人の心を見るだけで、その人に必要な本を選んでくれると言われています。"
        )
        result = detect_language(text)
        assert result == ('ja', 1.0), f"Expected ('ja', 1.0), got {result!r}"

    def test_detects_chinese_by_han_only(self):
        """Pure Han text (no kana) must be detected as Chinese, not Japanese."""
        text = (
            "在杭州西湖边的一条小巷里，有一家不起眼的茶馆，已经经营了三代人。"
            "茶馆的主人是一位白发苍苍的老人，他每天清晨四点起床，亲自烧水，亲自挑选茶叶。"
            "来这里喝茶的人不多，但都是常客。"
        )
        result = detect_language(text)
        assert result == ('zh', 1.0), f"Expected ('zh', 1.0), got {result!r}"

    def test_detects_arabic_by_script(self):
        text = (
            "في قلب مدينة دمشق القديمة، خلف باب خشبي قديم منقوش بأشكال هندسية، "
            "تختبئ حديقة صغيرة لا يعرفها إلا القليل من الناس. تحتوي الحديقة على "
            "شجرة ياسمين عمرها أكثر من مئة عام."
        )
        result = detect_language(text)
        assert result == ('ar', 1.0), f"Expected ('ar', 1.0), got {result!r}"

    def test_detects_hindi_by_devanagari(self):
        text = (
            "मुंबई की एक संकरी गली में एक बूढ़ा चायवाला है जो केवल बारिश के दिनों में "
            "अपनी दुकान खोलता है। उसकी चाय की खुशबू इतनी अनोखी है कि लोग छाते भूलकर "
            "भीगते-भीगते उसके पास पहुंच जाते हैं।"
        )
        result = detect_language(text)
        assert result == ('hi', 1.0), f"Expected ('hi', 1.0), got {result!r}"


# ---------------------------------------------------------------------------
# Thread-safety — concurrent detection on Latin scripts
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """detect_language must be deterministic under concurrent calls."""

    def test_concurrent_french_detection_is_deterministic(self):
        """
        Multiple threads detecting the same French text must all agree.
        Without the lock, langdetect's shared state can produce 'af', 'ca',
        etc. on short Latin samples.
        """
        import concurrent.futures

        text = (
            "À Paris, dans une petite rue pavée du Marais, il existe une boulangerie "
            "qui n'ouvre qu'à minuit. Personne ne sait vraiment qui est le boulanger, "
            "mais tous ceux qui ont goûté à son pain disent qu'il a un goût d'enfance."
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            results = list(ex.map(lambda _: detect_language(text), range(24)))

        unique = set(results)
        assert len(unique) == 1, f"Non-deterministic under concurrency: {unique}"
        assert results[0] is not None and results[0][0] == 'fr', (
            f"Expected 'fr', got {results[0]!r}"
        )
