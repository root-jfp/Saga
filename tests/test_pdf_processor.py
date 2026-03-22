"""
Tests for the refactored PDFProcessor.

RED phase: all tests written before implementation.
Run with: pytest tests/test_pdf_processor.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pdf_processor import PDFProcessor, _ABBREVIATIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_processor():
    """Return a PDFProcessor instance without a real PDF path."""
    return PDFProcessor.__new__(PDFProcessor)


# ---------------------------------------------------------------------------
# _custom_sentence_split
# ---------------------------------------------------------------------------

class TestCustomSentenceSplit:

    def test_basic_two_sentences(self):
        p = make_processor()
        result = p._custom_sentence_split("Hello world. This is a test.")
        assert len(result) == 2
        assert result[0] == "Hello world."
        assert result[1] == "This is a test."

    def test_three_sentences(self):
        p = make_processor()
        result = p._custom_sentence_split("One. Two. Three.")
        assert len(result) == 3

    def test_exclamation_mark(self):
        p = make_processor()
        result = p._custom_sentence_split("Stop! Don't move.")
        assert len(result) == 2
        assert result[0] == "Stop!"

    def test_question_mark(self):
        p = make_processor()
        result = p._custom_sentence_split("Who are you? I am nobody.")
        assert len(result) == 2

    def test_does_not_split_on_dr(self):
        p = make_processor()
        result = p._custom_sentence_split("Dr. Smith arrived early. He was tired.")
        assert len(result) == 2
        assert "Dr. Smith" in result[0]

    def test_does_not_split_on_mr(self):
        p = make_processor()
        result = p._custom_sentence_split("Mr. Jones spoke first. Then Mrs. Jones replied.")
        assert len(result) == 2

    def test_does_not_split_on_prof(self):
        p = make_processor()
        result = p._custom_sentence_split("Prof. Brown taught the class. It was engaging.")
        assert len(result) == 2

    def test_does_not_split_on_vs(self):
        p = make_processor()
        result = p._custom_sentence_split("The match was Smith vs. Jones. It was close.")
        assert len(result) == 2

    def test_etc_ends_sentence_correctly(self):
        # 'etc' is intentionally NOT in _ABBREVIATIONS so it can end sentences.
        # "etc. Then" → uppercase 'T' triggers a real boundary → 2 sentences.
        p = make_processor()
        result = p._custom_sentence_split("He listed cats, dogs, etc. Then he stopped.")
        assert len(result) == 2
        assert result[0].endswith("etc.")
        assert result[1].startswith("Then")

    def test_does_not_split_on_decimal_number(self):
        p = make_processor()
        result = p._custom_sentence_split("The value was 3.14 exactly. It matched.")
        assert len(result) == 2

    def test_does_not_split_on_single_initial(self):
        p = make_processor()
        result = p._custom_sentence_split("J. K. Rowling wrote the book. It was popular.")
        assert len(result) == 2

    def test_does_not_split_on_ellipsis(self):
        p = make_processor()
        result = p._custom_sentence_split("She paused... and then spoke. It was quiet.")
        assert len(result) == 2

    def test_splits_after_closing_quote(self):
        p = make_processor()
        result = p._custom_sentence_split('"Hello." She waved goodbye.')
        assert len(result) == 2

    def test_splits_after_closing_paren(self):
        p = make_processor()
        result = p._custom_sentence_split("He agreed (finally.) She was relieved.")
        assert len(result) == 2

    def test_empty_string_returns_empty(self):
        p = make_processor()
        assert p._custom_sentence_split("") == []

    def test_single_sentence_no_split(self):
        p = make_processor()
        result = p._custom_sentence_split("This is one sentence")
        assert len(result) == 1

    def test_preserves_full_text(self):
        """All text should be preserved across all sentences."""
        p = make_processor()
        text = "First sentence. Second sentence. Third sentence."
        result = p._custom_sentence_split(text)
        rejoined = " ".join(result)
        # Every word should appear somewhere
        for word in text.replace(".", "").split():
            assert word in rejoined

    def test_us_abbreviation_not_split(self):
        p = make_processor()
        result = p._custom_sentence_split("He moved to Washington D.C. last year. It was nice.")
        assert len(result) == 2

    def test_no_abbreviation_set(self):
        """
        Module-level abbreviation set should contain titles (never sentence-enders)
        and vs (always mid-sentence). Terminal abbreviations like 'etc' are
        intentionally excluded so they can properly end sentences when followed
        by uppercase.
        """
        assert 'dr' in _ABBREVIATIONS
        assert 'mr' in _ABBREVIATIONS
        assert 'mrs' in _ABBREVIATIONS
        assert 'prof' in _ABBREVIATIONS
        assert 'vs' in _ABBREVIATIONS
        assert 'jr' in _ABBREVIATIONS
        assert 'sr' in _ABBREVIATIONS
        # 'etc' is intentionally NOT in the set — it can end sentences
        assert 'etc' not in _ABBREVIATIONS


# ---------------------------------------------------------------------------
# _merge_fragments
# ---------------------------------------------------------------------------

class TestMergeFragments:

    def test_merges_short_fragment_with_next(self):
        p = make_processor()
        # "No." is a fragment (1 word), should merge with next
        result = p._merge_fragments(["No.", "That is not right."])
        assert len(result) == 1
        assert "No." in result[0]
        assert "That is not right." in result[0]

    def test_does_not_merge_normal_sentences(self):
        p = make_processor()
        sentences = ["This is a normal sentence.", "So is this one here."]
        result = p._merge_fragments(sentences)
        assert len(result) == 2

    def test_does_not_merge_two_word_sentence(self):
        """Two-word sentences are valid and should not be merged."""
        p = make_processor()
        result = p._merge_fragments(["Well then.", "We should proceed with caution."])
        # "Well then." is 2 words — kept as a valid short sentence
        assert len(result) == 2

    def test_single_sentence_unchanged(self):
        p = make_processor()
        result = p._merge_fragments(["Only one sentence here."])
        assert len(result) == 1

    def test_empty_list_unchanged(self):
        p = make_processor()
        assert p._merge_fragments([]) == []

    def test_does_not_merge_if_no_next(self):
        """Last sentence even if short should not be lost."""
        p = make_processor()
        result = p._merge_fragments(["Normal long sentence here.", "Short."])
        # Short last sentence may be kept or merged back — must not be lost
        full = " ".join(result)
        assert "Short" in full


# ---------------------------------------------------------------------------
# _merge_drop_caps
# ---------------------------------------------------------------------------

class TestMergeDropCaps:

    def _make_block(self, text, font_sizes=None, is_bold=False, y0=0, y1=10, x0=0):
        return {
            'text': text,
            'y0': y0, 'y1': y1, 'x0': x0,
            'font_sizes': font_sizes or [12.0],
            'is_bold': is_bold,
        }

    def test_merges_single_uppercase_letter_with_lowercase_continuation(self):
        p = make_processor()
        blocks = [
            self._make_block('W', font_sizes=[36.0]),
            self._make_block('hy are you here?', font_sizes=[12.0]),
        ]
        result = p._merge_drop_caps(blocks)
        assert len(result) == 1
        assert result[0]['text'] == 'Why are you here?'

    def test_merged_block_uses_next_block_font_sizes(self):
        """Drop-cap font size must NOT pollute heading detection."""
        p = make_processor()
        blocks = [
            self._make_block('W', font_sizes=[36.0]),
            self._make_block('hy the story began.', font_sizes=[12.0]),
        ]
        result = p._merge_drop_caps(blocks)
        assert result[0]['font_sizes'] == [12.0]

    def test_does_not_merge_when_next_starts_uppercase(self):
        """'A' followed by 'New sentence' must not merge — true heading."""
        p = make_processor()
        blocks = [
            self._make_block('A', font_sizes=[36.0]),
            self._make_block('New chapter begins here.', font_sizes=[12.0]),
        ]
        result = p._merge_drop_caps(blocks)
        assert len(result) == 2

    def test_does_not_merge_normal_word_block(self):
        p = make_processor()
        blocks = [
            self._make_block('Hello world.', font_sizes=[12.0]),
            self._make_block('nother sentence.', font_sizes=[12.0]),
        ]
        result = p._merge_drop_caps(blocks)
        assert len(result) == 2

    def test_empty_blocks_unchanged(self):
        p = make_processor()
        assert p._merge_drop_caps([]) == []

    def test_single_block_unchanged(self):
        p = make_processor()
        blocks = [self._make_block('W', font_sizes=[36.0])]
        result = p._merge_drop_caps(blocks)
        assert len(result) == 1

    def test_merges_two_letter_drop_cap(self):
        """Some PDFs emit a two-letter drop cap like 'WH' + 'en she arrived.'"""
        p = make_processor()
        blocks = [
            self._make_block('WH', font_sizes=[36.0]),
            self._make_block('en she arrived.', font_sizes=[12.0]),
        ]
        result = p._merge_drop_caps(blocks)
        assert len(result) == 1
        assert result[0]['text'] == 'WHen she arrived.'


# ---------------------------------------------------------------------------
# _prepare_tts_text
# ---------------------------------------------------------------------------

class TestPrepareTtsText:

    def test_expands_dr(self):
        p = make_processor()
        result = p._prepare_tts_text("Dr. Smith arrived.")
        assert "Doctor" in result
        assert "Dr." not in result

    def test_expands_mr(self):
        p = make_processor()
        result = p._prepare_tts_text("Mr. Jones spoke.")
        assert "Mister" in result

    def test_expands_mrs(self):
        p = make_processor()
        result = p._prepare_tts_text("Mrs. Jones replied.")
        assert "Missus" in result

    def test_expands_prof(self):
        p = make_processor()
        result = p._prepare_tts_text("Prof. Brown lectured.")
        assert "Professor" in result

    def test_expands_vs(self):
        p = make_processor()
        result = p._prepare_tts_text("Smith vs. Jones was intense.")
        assert "versus" in result

    def test_expands_etc(self):
        p = make_processor()
        result = p._prepare_tts_text("Cats, dogs, etc. are pets.")
        assert "et cetera" in result

    def test_expands_ie(self):
        p = make_processor()
        result = p._prepare_tts_text("Use the tool, i.e. a hammer.")
        assert "that is" in result

    def test_expands_eg(self):
        p = make_processor()
        result = p._prepare_tts_text("Fruits, e.g. apples, are healthy.")
        assert "for example" in result

    def test_removes_footnote_brackets(self):
        p = make_processor()
        result = p._prepare_tts_text("As shown by Smith[1] in the study.")
        assert "[1]" not in result
        assert "Smith" in result

    def test_removes_superscript_footnote(self):
        p = make_processor()
        result = p._prepare_tts_text("The result was significant.²")
        assert "²" not in result

    def test_removes_asterisk_footnote(self):
        p = make_processor()
        result = p._prepare_tts_text("The value* was confirmed.")
        assert "*" not in result

    def test_replaces_ampersand(self):
        p = make_processor()
        result = p._prepare_tts_text("Smith & Jones agreed.")
        assert "&" not in result
        assert "and" in result

    def test_replaces_percent(self):
        p = make_processor()
        result = p._prepare_tts_text("The rate was 98% accurate.")
        assert "%" not in result
        assert "percent" in result

    def test_replaces_em_dash(self):
        p = make_processor()
        result = p._prepare_tts_text("He paused — then spoke.")
        assert "—" not in result

    def test_replaces_ellipsis_dots(self):
        p = make_processor()
        result = p._prepare_tts_text("She thought... and decided.")
        assert "..." not in result

    def test_replaces_unicode_ellipsis(self):
        p = make_processor()
        result = p._prepare_tts_text("She thought\u2026 and decided.")
        assert "\u2026" not in result

    def test_removes_urls(self):
        p = make_processor()
        result = p._prepare_tts_text("Visit https://example.com for more.")
        assert "https://" not in result

    def test_empty_string_returns_empty(self):
        p = make_processor()
        assert p._prepare_tts_text("") == ""

    def test_plain_text_unchanged_in_substance(self):
        p = make_processor()
        text = "The cat sat on the mat."
        result = p._prepare_tts_text(text)
        assert "cat" in result
        assert "mat" in result


# ---------------------------------------------------------------------------
# _is_list_item
# ---------------------------------------------------------------------------

class TestIsListItem:

    def test_numbered_period(self):
        p = make_processor()
        assert p._is_list_item("1. First item")

    def test_numbered_paren(self):
        p = make_processor()
        assert p._is_list_item("2) Second item")

    def test_letter_paren(self):
        p = make_processor()
        assert p._is_list_item("(a) First option")

    def test_bullet_dash(self):
        p = make_processor()
        assert p._is_list_item("- Some item")

    def test_bullet_dot(self):
        p = make_processor()
        assert p._is_list_item("• Some bullet")

    def test_normal_sentence_not_list(self):
        p = make_processor()
        assert not p._is_list_item("This is a normal sentence.")

    def test_roman_numeral(self):
        p = make_processor()
        assert p._is_list_item("i. First point")


# ---------------------------------------------------------------------------
# _split_into_sentences — structured output (paragraph + heading metadata)
# ---------------------------------------------------------------------------

class TestSplitIntoSentencesStructured:

    def test_returns_list_of_dicts(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world. This is a test.")
        assert isinstance(result, list)
        assert all(isinstance(s, dict) for s in result)

    def test_each_sentence_has_required_keys(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world. This is a test.")
        required = {'text', 'start', 'end', 'paragraph_index', 'is_paragraph_start', 'is_heading', 'tts_text'}
        for s in result:
            assert required.issubset(s.keys()), f"Missing keys in: {s}"

    def test_first_sentence_is_paragraph_start(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world. This is a test.")
        assert result[0]['is_paragraph_start'] is True

    def test_subsequent_sentences_not_paragraph_start(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world. This is a test.")
        assert result[1]['is_paragraph_start'] is False

    def test_paragraph_index_propagated(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world. This is a test.", paragraph_index=3)
        for s in result:
            assert s['paragraph_index'] == 3

    def test_is_heading_false_by_default(self):
        p = make_processor()
        result = p._split_into_sentences("Hello world.")
        assert result[0]['is_heading'] is False

    def test_is_heading_propagated_when_true(self):
        p = make_processor()
        result = p._split_into_sentences("Chapter One", is_heading=True)
        assert result[0]['is_heading'] is True

    def test_tts_text_present(self):
        p = make_processor()
        result = p._split_into_sentences("Dr. Smith came. He was tired.")
        assert all(isinstance(s['tts_text'], str) for s in result)

    def test_tts_text_has_expansion(self):
        p = make_processor()
        result = p._split_into_sentences("Dr. Smith came.")
        assert "Doctor" in result[0]['tts_text']

    def test_start_end_offsets_valid(self):
        p = make_processor()
        text = "First sentence. Second sentence."
        result = p._split_into_sentences(text)
        for s in result:
            assert s['start'] >= 0
            assert s['end'] <= len(text)
            assert s['end'] > s['start']

    def test_empty_string_returns_empty(self):
        p = make_processor()
        assert p._split_into_sentences("") == []


# ---------------------------------------------------------------------------
# Integration: extract_page structure (mocked — no real PDF needed)
# ---------------------------------------------------------------------------

class TestExtractPageStructure:

    def test_page_dict_has_required_keys(self):
        """extract_page result must have text_content, sentences, word_count."""
        import unittest.mock as mock

        p = make_processor()
        p.pdf_path = "fake.pdf"

        # Mock pdfplumber to return a page with some text
        mock_page = mock.MagicMock()
        mock_page.extract_text.return_value = (
            "Dr. Smith wrote the report. It was thorough. "
            "He concluded with recommendations."
        )

        mock_pdf = mock.MagicMock()
        mock_pdf.__enter__ = mock.Mock(return_value=mock_pdf)
        mock_pdf.__exit__ = mock.Mock(return_value=False)
        mock_pdf.pages = [mock_page]

        with mock.patch('pdfplumber.open', return_value=mock_pdf):
            with mock.patch('fitz.open', side_effect=Exception("no fitz")):
                result = p.extract_page(1)

        assert result is not None
        assert 'text_content' in result
        assert 'sentences' in result
        assert 'word_count' in result
        assert isinstance(result['sentences'], list)

    def test_sentences_have_tts_text(self):
        """All sentences in extract_page result must have tts_text."""
        import unittest.mock as mock

        p = make_processor()
        p.pdf_path = "fake.pdf"

        mock_page = mock.MagicMock()
        mock_page.extract_text.return_value = (
            "Mr. Brown gave a lecture. It was informative."
        )

        mock_pdf = mock.MagicMock()
        mock_pdf.__enter__ = mock.Mock(return_value=mock_pdf)
        mock_pdf.__exit__ = mock.Mock(return_value=False)
        mock_pdf.pages = [mock_page]

        with mock.patch('pdfplumber.open', return_value=mock_pdf):
            with mock.patch('fitz.open', side_effect=Exception("no fitz")):
                result = p.extract_page(1)

        assert result is not None
        for s in result['sentences']:
            assert 'tts_text' in s, f"Missing tts_text in sentence: {s}"
