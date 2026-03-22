"""
PDF Processing Pipeline
- Extract text from PDFs with layout awareness (paragraphs, headings)
- Robust sentence splitting that handles abbreviations, ellipsis, decimals
- TTS-optimized text per sentence (abbreviations expanded, symbols replaced)
- Fallback to pdfplumber for scanned/OCR PDFs
"""

import re
import os
import json
from collections import Counter

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    import nltk
    # Ensure punkt tokenizer is available
    try:
        nltk.data.find('tokenizers/punkt_tab')
    except LookupError:
        nltk.download('punkt_tab', quiet=True)
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Abbreviation set (lowercase, without trailing period)
# Used by the custom sentence splitter to avoid false splits.
# ---------------------------------------------------------------------------

_ABBREVIATIONS = frozenset({
    # Titles — these NEVER end a sentence, so suppress splits unconditionally.
    # (Terminal abbreviations like etc., vs., al. are intentionally excluded:
    #  the uppercase-next-word check is sufficient for those.)
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'rev', 'gen', 'sgt',
    'cpl', 'pvt', 'capt', 'maj', 'col', 'gov', 'pres', 'lt', 'cdr',
    'hon', 'rep', 'sen', 'msgr', 'supt',
    # Addresses — appear in middle of text, never end sentences
    'st', 'ave', 'blvd', 'rd', 'ln', 'ct',
    # Mid-sentence Latin abbreviations
    'ie', 'eg', 'cf', 'nb',
    # Always mid-sentence (never end a sentence)
    'vs',  # "A vs. B" — B is always uppercase but never a new sentence
})

# ---------------------------------------------------------------------------
# TTS abbreviation expansion table
# Order matters: longer patterns before shorter overlapping ones.
# ---------------------------------------------------------------------------

_TTS_EXPANSIONS = [
    # Compound abbreviations first
    (r'\bi\.e\.', 'that is'),
    (r'\be\.g\.', 'for example'),
    (r'\bop\.cit\.', 'opus cited'),
    (r'\bloc\.cit\.', 'location cited'),
    # Titles
    (r'\bDr\.', 'Doctor'),
    (r'\bMrs\.', 'Missus'),
    (r'\bMr\.', 'Mister'),
    (r'\bMs\.', 'Miss'),
    (r'\bProf\.', 'Professor'),
    (r'\bSr\.', 'Senior'),
    (r'\bJr\.', 'Junior'),
    (r'\bGen\.', 'General'),
    (r'\bSgt\.', 'Sergeant'),
    (r'\bCapt\.', 'Captain'),
    (r'\bLt\.', 'Lieutenant'),
    (r'\bCol\.', 'Colonel'),
    (r'\bPvt\.', 'Private'),
    (r'\bRev\.', 'Reverend'),
    (r'\bGov\.', 'Governor'),
    (r'\bPres\.', 'President'),
    (r'\bHon\.', 'Honorable'),
    (r'\bRep\.', 'Representative'),
    (r'\bSen\.', 'Senator'),
    # Academic / Publishing
    (r'\bFig\.', 'Figure'),
    (r'\bNo\.', 'Number'),
    (r'\bVol\.', 'Volume'),
    (r'\bCh\.', 'Chapter'),
    (r'\bSec\.', 'Section'),
    (r'\bpp\.', 'pages'),
    (r'\bp\.', 'page'),
    (r'\bEd\.', 'Edition'),
    (r'\bEds\.', 'Editors'),
    (r'\bTrans\.', 'Translated by'),
    # Common
    (r'\bvs\.', 'versus'),
    (r'\betc\.', 'et cetera'),
    (r'\bcf\.', 'compare'),
    (r'\bapprox\.', 'approximately'),
    (r'\bDept\.', 'Department'),
    (r'\bCorp\.', 'Corporation'),
    (r'\bInc\.', 'Incorporated'),
    (r'\bLtd\.', 'Limited'),
    # Addresses
    (r'\bSt\.', 'Saint'),
    (r'\bAve\.', 'Avenue'),
    (r'\bBlvd\.', 'Boulevard'),
    (r'\bRd\.', 'Road'),
]

# Compile once for performance
_TTS_EXPANSIONS_COMPILED = [
    (re.compile(pattern), replacement)
    for pattern, replacement in _TTS_EXPANSIONS
]

# Footnote / citation markers
_FOOTNOTE_RE = re.compile(
    r'\[\d+\]'          # [1]
    r'|\(\d+\)'         # (1) — only standalone numeric parens
    r'|[¹²³⁴⁵⁶⁷⁸⁹⁰]+'  # Unicode superscripts
    r'|\*{1,3}(?=\s|$)' # trailing asterisks (footnote markers, not emphasis)
)

# List item pattern
_LIST_ITEM_RE = re.compile(
    r'^(\s*'
    r'(?:'
    r'\d+[.):]'             # 1. or 1) or 1:
    r'|\([a-z]\)'           # (a)
    r'|[a-z][.)]'           # a. or a)
    r'|[ivxlcdmIVXLCDM]+[.)]'  # roman numerals
    r'|[•·\-–—*▪◦◉○●]'    # bullet characters
    r')'
    r'\s+)',
    re.IGNORECASE
)

# Sentence boundary pattern:
# punctuation [.!?]+ → optional closing chars ["')]] → whitespace
_BOUNDARY_RE = re.compile(r'([.!?]+)(["\'\)\]]*)\s+')


class PDFProcessor:
    """Process PDF files to extract structured text content."""

    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self.is_scanned = False
        self.pages = []
        self.total_pages = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_total_pages(self):
        """Get total page count without full extraction."""
        try:
            import pdfplumber
            with pdfplumber.open(self.pdf_path) as pdf:
                return len(pdf.pages)
        except Exception as e:
            print(f"Error getting page count: {e}")
            return 0

    def extract_text(self):
        """
        Extract text from all pages with layout analysis.
        Tries PyMuPDF layout extraction first, falls back to pdfplumber.
        Returns: (pages_list, is_scanned)
        """
        try:
            import fitz
            return self._extract_with_fitz()
        except Exception as e:
            print(f"PyMuPDF layout extraction failed ({e}), falling back to pdfplumber")
            return self._extract_with_pdfplumber()

    def extract_page(self, page_number):
        """Extract text from a single page (1-indexed)."""
        # Try PyMuPDF first
        try:
            import fitz
            doc = fitz.open(self.pdf_path)
            try:
                if page_number < 1 or page_number > len(doc):
                    return None
                # Compute body font size from this page to drive heading detection
                raw_blocks = self._get_raw_blocks(doc, page_number - 1)
                raw_blocks = self._merge_drop_caps(raw_blocks)
                all_sizes = [sz for b in raw_blocks for sz in b['font_sizes']]
                body_font_size = _median(all_sizes) if all_sizes else 12.0
                paragraphs = self._classify_blocks(raw_blocks, body_font_size)
                return self._build_page_result(page_number, paragraphs)
            finally:
                doc.close()
        except Exception:
            pass

        # Fallback to pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(self.pdf_path) as pdf:
                if page_number < 1 or page_number > len(pdf.pages):
                    return None
                page = pdf.pages[page_number - 1]
                text = page.extract_text() or ''
                if len(text.strip()) < 50 and OCR_AVAILABLE:
                    ocr_text = self._ocr_page(page_number - 1)
                    if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                        text = ocr_text
                text = self._clean_text(text)
                paragraphs = [{'type': 'paragraph', 'text': text}]
                return self._build_page_result(page_number, paragraphs)
        except Exception as e:
            print(f"Error extracting page {page_number}: {e}")
            return None

    def extract_cover(self, output_path):
        """Extract first page as cover image using PyMuPDF."""
        try:
            import fitz
            doc = fitz.open(self.pdf_path)
            if len(doc) == 0:
                doc.close()
                return None
            page = doc[0]
            zoom = 150 / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            pix.save(output_path)
            doc.close()
            try:
                from PIL import Image
                img = Image.open(output_path)
                img.thumbnail((300, 400))
                img.save(output_path, 'JPEG', quality=85)
            except ImportError:
                pass
            return output_path
        except Exception as e:
            print(f"Error extracting cover: {e}")
            return None

    # ------------------------------------------------------------------
    # PyMuPDF layout extraction
    # ------------------------------------------------------------------

    def _extract_with_fitz(self):
        """Full extraction using PyMuPDF layout analysis."""
        import fitz

        doc = fitz.open(self.pdf_path)
        try:
            self.total_pages = len(doc)

            # Pass 1: collect all font sizes across all pages for global median
            all_font_sizes = []
            pages_blocks = []

            for page_idx in range(self.total_pages):
                blocks = self._get_raw_blocks(doc, page_idx)
                pages_blocks.append(blocks)
                for b in blocks:
                    all_font_sizes.extend(b['font_sizes'])

            body_font_size = _median(all_font_sizes) if all_font_sizes else 12.0

            # Pass 2: collect raw page texts for header/footer detection
            raw_texts = []
            for blocks in pages_blocks:
                page_text = '\n'.join(b['text'] for b in blocks)
                raw_texts.append(page_text)

            header_footer_patterns = self._detect_repeating_lines(raw_texts)

            # Pass 3: build structured pages
            for page_idx, blocks in enumerate(pages_blocks):
                # Filter header/footer blocks
                filtered = [
                    b for b in blocks
                    if self._normalize_line(b['text'].split('\n')[0]) not in header_footer_patterns
                ]

                filtered = self._merge_drop_caps(filtered)
                paragraphs = self._classify_blocks(filtered, body_font_size)
                page_result = self._build_page_result(page_idx + 1, paragraphs)
                self.pages.append(page_result)

            return self.pages, self.is_scanned
        finally:
            doc.close()

    def _get_raw_blocks(self, fitz_doc, page_idx):
        """
        Extract raw text blocks from a page with font metadata.
        Returns list of {text, y0, y1, x0, font_sizes, is_bold}.
        """
        page = fitz_doc[page_idx]
        data = page.get_text("dict", flags=0)

        raw_blocks = []
        for block in data.get("blocks", []):
            if block.get("type") != 0:  # skip image blocks
                continue

            lines = block.get("lines", [])
            if not lines:
                continue

            all_spans = [
                span
                for line in lines
                for span in line.get("spans", [])
            ]

            if not all_spans:
                continue

            font_sizes = [
                s["size"] for s in all_spans
                if s.get("text", "").strip() and s.get("size", 0) > 0
            ]
            if not font_sizes:
                continue

            is_bold = any(
                (s.get("flags", 0) & 16) and s.get("text", "").strip()
                for s in all_spans
            )

            # Build block text with hyphenation handling
            block_lines = []
            for line in lines:
                line_text = ''.join(s.get("text", "") for s in line.get("spans", []))
                block_lines.append(line_text)

            joined_text = self._join_lines(block_lines)

            if not joined_text.strip():
                continue

            raw_blocks.append({
                'text': joined_text,
                'y0': block["bbox"][1],
                'y1': block["bbox"][3],
                'x0': block["bbox"][0],
                'font_sizes': font_sizes,
                'is_bold': is_bold,
            })

        # Sort top-to-bottom, left-to-right
        raw_blocks.sort(key=lambda b: (round(b['y0'], 1), b['x0']))
        return raw_blocks

    def _extract_page_blocks(self, fitz_doc, page_idx, body_font_size):
        """Extract and classify blocks for a single page."""
        blocks = self._get_raw_blocks(fitz_doc, page_idx)
        return self._classify_blocks(blocks, body_font_size)

    def _merge_drop_caps(self, raw_blocks):
        """
        Merge drop-cap blocks with the immediately following text block.

        PDFs often render the opening large letter of a paragraph as a
        separate block with an inflated font size.  PyMuPDF sees:
            block 0: "W"        (large font → classified as heading)
            block 1: "hy the…"  (normal font → starts lowercase)
        This method rejoins them into "Why the…" and adopts the following
        block's font metadata so the result is classified as a paragraph.

        Criteria for a drop-cap block:
        • text is 1–3 characters, all uppercase alphabetic
        • immediately followed by a block whose text starts with a
          lowercase letter (continuation of the same word)
        """
        if not raw_blocks:
            return raw_blocks

        result = []
        i = 0
        while i < len(raw_blocks):
            block = raw_blocks[i]
            text = block['text'].strip()

            # Candidate drop cap: 1-3 uppercase alpha chars
            if (1 <= len(text) <= 3
                    and text.isupper()
                    and text.isalpha()
                    and i + 1 < len(raw_blocks)):
                next_block = raw_blocks[i + 1]
                next_text = next_block['text'].lstrip()
                # Merge only if the next word is a lowercase continuation
                if next_text and next_text[0].islower():
                    merged = {
                        'text': text + next_text,
                        'y0': block['y0'],
                        'y1': next_block['y1'],
                        'x0': block['x0'],
                        # Use next block's font info — drop-cap font is inflated
                        'font_sizes': next_block['font_sizes'],
                        'is_bold': next_block['is_bold'],
                    }
                    result.append(merged)
                    i += 2
                    continue

            result.append(block)
            i += 1

        return result

    def _classify_blocks(self, raw_blocks, body_font_size):
        """
        Classify raw blocks as heading, list, or paragraph.
        Returns list of {type, text}.
        """
        heading_threshold = body_font_size * 1.25
        paragraphs = []

        for block in raw_blocks:
            text = self._clean_text(block['text'])
            if not text:
                continue

            max_size = max(block['font_sizes']) if block['font_sizes'] else body_font_size
            is_bold = block['is_bold']

            if max_size >= heading_threshold or (is_bold and max_size >= body_font_size * 1.1):
                block_type = 'heading'
            elif self._is_list_item(text):
                block_type = 'list'
            else:
                block_type = 'paragraph'

            paragraphs.append({'type': block_type, 'text': text})

        return paragraphs

    def _join_lines(self, lines):
        """
        Join lines within a block, handling soft hyphens and
        converting soft line-breaks to spaces.
        """
        if not lines:
            return ''

        result_parts = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()

            # Hyphenated word split across lines: "connec-\ntion" → "connection"
            if stripped.endswith('-') and i + 1 < len(lines):
                next_line = lines[i + 1].lstrip()
                # Only rejoin if the next line starts with a lowercase letter
                if next_line and next_line[0].islower():
                    result_parts.append(stripped[:-1] + next_line)
                    i += 2
                    continue

            result_parts.append(line)
            i += 1

        # Join with space, normalise internal whitespace
        joined = ' '.join(p.strip() for p in result_parts if p.strip())
        return joined

    def _build_page_result(self, page_number, paragraphs):
        """Build the page dict from a list of classified paragraph blocks."""
        all_sentences = []
        text_parts = []

        for para_idx, para in enumerate(paragraphs):
            text = para['text']
            if not text:
                continue

            text_parts.append(text)

            if para['type'] == 'heading':
                tts_text = self._prepare_tts_text(text)
                all_sentences.append({
                    'text': text,
                    'start': 0,
                    'end': len(text),
                    'paragraph_index': para_idx,
                    'is_paragraph_start': True,
                    'is_heading': True,
                    'tts_text': tts_text,
                })
            elif para['type'] == 'list':
                tts_text = self._prepare_tts_text(text)
                all_sentences.append({
                    'text': text,
                    'start': 0,
                    'end': len(text),
                    'paragraph_index': para_idx,
                    'is_paragraph_start': True,
                    'is_heading': False,
                    'tts_text': tts_text,
                })
            else:
                sents = self._split_into_sentences(text, paragraph_index=para_idx)
                all_sentences.extend(sents)

        full_text = '\n\n'.join(text_parts)
        return {
            'page_number': page_number,
            'text_content': full_text,
            'sentences': all_sentences,
            'word_count': len(full_text.split()),
        }

    # ------------------------------------------------------------------
    # Sentence splitting
    # ------------------------------------------------------------------

    def _split_into_sentences(self, text, paragraph_index=0, is_heading=False):
        """
        Split text into sentences with metadata.
        Uses nltk if available, otherwise falls back to custom splitter.

        Returns list of:
          {text, start, end, paragraph_index, is_paragraph_start, is_heading, tts_text}
        """
        if not text or not text.strip():
            return []

        if NLTK_AVAILABLE:
            parts = nltk.sent_tokenize(text)
        else:
            parts = self._custom_sentence_split(text)

        parts = self._merge_fragments(parts)

        sentences = []
        current_pos = 0

        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            start = text.find(part, current_pos)
            if start == -1:
                start = current_pos
            end = start + len(part)

            sentences.append({
                'text': part,
                'start': start,
                'end': end,
                'paragraph_index': paragraph_index,
                'is_paragraph_start': i == 0,
                'is_heading': is_heading,
                'tts_text': self._prepare_tts_text(part),
            })

            current_pos = end

        return sentences

    def _custom_sentence_split(self, text):
        """
        Abbreviation-aware sentence splitter (no external deps).

        A sentence boundary is: [.!?]+ [closing-chars]* whitespace [A-Z or quote]
        Skipped when:
          - The word before '.' is a known abbreviation
          - The word before '.' is a single letter (initial)
          - The punctuation is '...' (ellipsis)
          - A digit appears on both sides of '.' (decimal)
        """
        if not text:
            return []

        split_points = []  # end-of-sentence positions

        for m in _BOUNDARY_RE.finditer(text):
            punct = m.group(1)
            close = m.group(2)
            punct_start = m.start()
            after_ws = m.end()

            # Must be followed by something
            if after_ws >= len(text):
                continue

            next_char = text[after_ws]

            # Next must start with uppercase, opening quote, or bracket
            if not (next_char.isupper() or next_char in '"\'(['):
                continue

            # Skip ellipsis
            if punct.startswith('...') or len(punct) >= 3:
                continue

            # For '.', check for abbreviations and decimals
            if punct == '.':
                before = text[:punct_start].rstrip()
                word_match = re.search(r'([A-Za-z]+)$', before)

                if word_match:
                    word = word_match.group(1).lower()
                    if word in _ABBREVIATIONS:
                        continue
                    if len(word) == 1:  # single initial
                        continue

                # Decimal number: digit on both sides
                char_before = text[punct_start - 1] if punct_start > 0 else ''
                if char_before.isdigit() and next_char.isdigit():
                    continue

            # Valid boundary: sentence ends after punct + close chars
            end_of_sentence = punct_start + len(punct) + len(close)
            split_points.append((end_of_sentence, after_ws))

        # Build sentence list from split points
        sentences = []
        last_start = 0

        for end_sent, start_next in split_points:
            sentence = text[last_start:end_sent].strip()
            if sentence:
                sentences.append(sentence)
            last_start = start_next

        remainder = text[last_start:].strip()
        if remainder:
            sentences.append(remainder)

        return sentences if sentences else [text.strip()]

    # ------------------------------------------------------------------
    # Fragment merging
    # ------------------------------------------------------------------

    def _merge_fragments(self, sentences):
        """
        Merge short fragments (< 4 words, no sentence-ending punctuation)
        with the following sentence to avoid orphaned splits.
        """
        if len(sentences) <= 1:
            return sentences

        # Work on a copy to avoid mutating the caller's list
        work = list(sentences)
        result = []
        i = 0

        while i < len(work):
            sent = work[i]
            words = sent.split()
            # Merge single-word orphans ("No.", "A.", lone initials).
            # Two-word+ sentences are kept as-is to avoid false merges.
            is_fragment = len(words) == 1

            if is_fragment and i + 1 < len(work):
                # Absorb into next sentence (modifying the copy, not the original)
                work[i + 1] = sent + ' ' + work[i + 1]
                i += 1
            else:
                result.append(sent)
                i += 1

        return result

    # ------------------------------------------------------------------
    # TTS text preparation
    # ------------------------------------------------------------------

    def _prepare_tts_text(self, text):
        """
        Normalize text for TTS:
        - Remove footnote/citation markers
        - Expand common abbreviations
        - Replace symbols with words
        - Normalise punctuation for natural pauses
        - Strip URLs
        """
        if not text:
            return ''

        result = text

        # Remove footnote markers
        result = _FOOTNOTE_RE.sub('', result)

        # Expand abbreviations
        for pattern, replacement in _TTS_EXPANSIONS_COMPILED:
            result = pattern.sub(replacement, result)

        # Symbol → word
        result = re.sub(r'\s*&\s*', ' and ', result)
        result = re.sub(r'(\d)\s*%', r'\1 percent', result)
        result = re.sub(r'\s*@\s*', ' at ', result)
        result = re.sub(r'\$\s*(\d)', r'\1 dollars', result)
        result = re.sub(r'#\s*(\d+)', r'number \1', result)

        # Em-dash / en-dash → comma pause
        result = re.sub(r'\s*[—–]\s*', ', ', result)

        # Ellipsis → single period (avoids "dot dot dot")
        result = re.sub(r'\.{3,}', '.', result)
        result = result.replace('\u2026', '.')

        # Remove URLs
        result = re.sub(r'https?://\S+', '', result)
        result = re.sub(r'www\.\S+', '', result)

        # Clean up artifacts
        result = re.sub(r'[ \t]+', ' ', result)
        result = re.sub(r',,+', ',', result)
        result = re.sub(r'\s+([.,;:!?])', r'\1', result)

        return result.strip()

    # ------------------------------------------------------------------
    # List item detection
    # ------------------------------------------------------------------

    def _is_list_item(self, text):
        """Return True if the text looks like a list item."""
        return bool(_LIST_ITEM_RE.match(text.strip()))

    # ------------------------------------------------------------------
    # pdfplumber fallback extraction
    # ------------------------------------------------------------------

    def _extract_with_pdfplumber(self):
        """Fallback: extract text using pdfplumber (no layout analysis)."""
        import pdfplumber

        self.pages = []
        raw_pages = []

        with pdfplumber.open(self.pdf_path) as pdf:
            self.total_pages = len(pdf.pages)

            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ''
                if len(text.strip()) < 50 and OCR_AVAILABLE:
                    ocr_text = self._ocr_page(i)
                    if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                        text = ocr_text
                        self.is_scanned = True
                raw_pages.append(text)

        header_footer_patterns = self._detect_repeating_lines(raw_pages)

        for i, raw_text in enumerate(raw_pages):
            text = self._remove_repeating_lines(raw_text, header_footer_patterns)
            text = self._clean_text(text)
            paragraphs = [{'type': 'paragraph', 'text': text}]
            self.pages.append(self._build_page_result(i + 1, paragraphs))

        return self.pages, self.is_scanned

    # ------------------------------------------------------------------
    # Header / footer detection (unchanged logic)
    # ------------------------------------------------------------------

    def _detect_repeating_lines(self, raw_pages, top_n=5, bottom_n=5, threshold=0.6):
        """Detect lines that appear repeatedly across pages (headers/footers)."""
        if len(raw_pages) < 3:
            return set()

        header_candidates = Counter()
        footer_candidates = Counter()
        min_occurrences = max(3, int(len(raw_pages) * threshold))

        for raw_text in raw_pages:
            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            if not lines:
                continue
            for line in lines[:top_n]:
                normalized = self._normalize_line(line)
                if normalized and len(normalized) > 3:
                    header_candidates[normalized] += 1
            for line in lines[-bottom_n:]:
                normalized = self._normalize_line(line)
                if normalized and len(normalized) > 3:
                    footer_candidates[normalized] += 1

        repeating = set()
        for line, count in header_candidates.items():
            if count >= min_occurrences:
                repeating.add(line)
        for line, count in footer_candidates.items():
            if count >= min_occurrences:
                repeating.add(line)

        if repeating:
            print(f"Detected {len(repeating)} repeating header/footer patterns")
        return repeating

    def _normalize_line(self, line):
        """Normalize a line for comparison (collapse whitespace, strip page numbers)."""
        if not line:
            return ''
        normalized = re.sub(r'^\d{1,4}\s*', '', line)
        normalized = re.sub(r'\s*\d{1,4}$', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip().lower()
        return normalized

    def _remove_repeating_lines(self, text, patterns):
        """Remove lines matching detected repeating patterns."""
        if not patterns:
            return text
        lines = text.split('\n')
        cleaned = [l for l in lines if self._normalize_line(l) not in patterns]
        return '\n'.join(cleaned)

    # ------------------------------------------------------------------
    # Text cleaning (trimmed — layout extraction handles most artifacts)
    # ------------------------------------------------------------------

    def _clean_text(self, text):
        """Remove common PDF artifacts and normalise whitespace."""
        if not text:
            return ''

        # Printer/proof metadata
        text = re.sub(
            r'\d+\s+WPS:\s*Prepress/Printer\'?s?\s*Proof\s+[\w\-_]+\.pdf\s+\w+\s+\d+,?\s*\d{4}\s+\d+:\d+:\d+',
            '', text, flags=re.IGNORECASE
        )
        text = re.sub(r'Printer\'?s?\s*Proof', '', text, flags=re.IGNORECASE)
        text = re.sub(r'WPS:\s*Prepress', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\d{10,13}_txt_print\.pdf', '', text)
        text = re.sub(
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?\b',
            '', text, flags=re.IGNORECASE
        )
        text = re.sub(r'\b\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\b', '', text)
        text = re.sub(r'\bProof\s*#?\s*\d+\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bDO NOT DISTRIBUTE\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bCONFIDENTIAL\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bUNCORRECTED\s+PROOF\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bADVANCE\s+(?:READING\s+)?COPY\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[A-Z]:\\[^\s]*\.pdf', '', text, flags=re.IGNORECASE)
        text = re.sub(r'/[^\s]*\.pdf', '', text, flags=re.IGNORECASE)

        # Page numbers at start/end of extracted block
        text = re.sub(r'^[\s\n]*\d{1,4}[\s\n]+', '', text)
        text = re.sub(r'[\s\n]+\d{1,4}[\s\n]*$', '', text)
        text = re.sub(r'\n\s*\d{1,4}\s*\n', '\n', text)

        # Control characters
        text = re.sub(r'\x00', '', text)
        text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f]', '', text)
        text = text.replace('\f', '\n')
        text = re.sub(r'\r\n|\r', '\n', text)

        # Whitespace normalisation
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    # ------------------------------------------------------------------
    # OCR (unchanged)
    # ------------------------------------------------------------------

    def _ocr_page(self, page_index):
        """OCR a single page using Tesseract."""
        if not OCR_AVAILABLE:
            return ''
        try:
            images = convert_from_path(
                self.pdf_path,
                first_page=page_index + 1,
                last_page=page_index + 1,
                dpi=300
            )
            if images:
                return pytesseract.image_to_string(images[0])
            return ''
        except Exception as e:
            print(f"OCR error on page {page_index + 1}: {e}")
            return ''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _median(values):
    """Return median of a list of numbers."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def process_pdf(pdf_path):
    """
    Convenience function to process a PDF file.
    Returns: (pages, is_scanned, total_pages)
    """
    processor = PDFProcessor(pdf_path)
    pages, is_scanned = processor.extract_text()
    return pages, is_scanned, processor.total_pages


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        processor = PDFProcessor(pdf_path)
        pages, is_scanned = processor.extract_text()
        print(f"Total pages: {len(pages)}")
        print(f"Is scanned: {is_scanned}")
        if pages:
            print(f"\nFirst page preview:")
            print(f"  text_content[:200]: {pages[0]['text_content'][:200]}")
            print(f"  sentences: {len(pages[0]['sentences'])}")
            if pages[0]['sentences']:
                s = pages[0]['sentences'][0]
                print(f"  first sentence: {s['text'][:100]}")
                print(f"  tts_text:       {s['tts_text'][:100]}")
                print(f"  is_heading:     {s['is_heading']}")
    else:
        print("Usage: python pdf_processor.py <pdf_file>")
