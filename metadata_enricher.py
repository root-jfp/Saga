"""External book metadata lookup (Open Library + Google Books).

Personal-use, no API keys. Fills in clean title/author/cover/ISBN/summary so
the library shows real book art instead of a PDF first-page.

Public API:
    enrich_book(title_hint, author_hint=None) -> Optional[BookMetadata]
    download_cover(url, output_path) -> bool
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger('book-reader')

OPEN_LIBRARY_SEARCH = 'https://openlibrary.org/search.json'
OPEN_LIBRARY_COVER  = 'https://covers.openlibrary.org/b/id/{cover_id}-L.jpg'
GOOGLE_BOOKS_SEARCH = 'https://www.googleapis.com/books/v1/volumes'

REQUEST_TIMEOUT = 8     # seconds — keep small so upload UX doesn't stall
USER_AGENT      = 'SagaBookReader/1.0 (+personal use; contact: local)'

# Hosts we'll accept cover/JSON URLs from. Open Library and Google Books
# return arbitrary URLs in their JSON; without a host allowlist we'd be
# vulnerable to SSRF (the JSON could redirect us at internal services like
# 169.254.169.254 or localhost ports). Personal-use risk is low but the
# fix is cheap and removes a class of bugs.
ALLOWED_COVER_HOSTS = frozenset({
    'covers.openlibrary.org',
    'archive.org',
    'books.google.com',
    'books.googleusercontent.com',
    'lh3.googleusercontent.com',
    'lh4.googleusercontent.com',
    'lh5.googleusercontent.com',
    'lh6.googleusercontent.com',
})


def _is_safe_https_url(url: str, allowed_hosts) -> bool:
    """True iff URL is https with a host in the allowlist."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != 'https':
        return False
    host = (parsed.hostname or '').lower()
    return host in allowed_hosts


@dataclass
class BookMetadata:
    title: str
    author: Optional[str] = None
    subtitle: Optional[str] = None
    isbn: Optional[str] = None
    published_year: Optional[int] = None
    summary: Optional[str] = None
    subjects: List[str] = field(default_factory=list)
    cover_url: Optional[str] = None
    open_library_id: Optional[str] = None      # /works/OLxxxW
    source: str = 'openlibrary'                # 'openlibrary' | 'google_books'

    def as_db_dict(self) -> dict:
        return {
            'title': self.title,
            'author': self.author,
            'subtitle': self.subtitle,
            'isbn': self.isbn,
            'published_year': self.published_year,
            'summary': self.summary,
            'subjects': '|'.join(self.subjects[:20]) if self.subjects else None,
            'open_library_id': self.open_library_id,
            'metadata_source': self.source,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        logger.warning('metadata fetch failed (%s): %s', url, exc)
        return None


def _clean_title_hint(filename_or_title: str) -> str:
    """Strip noise that PDF filenames pick up so search can land on real books."""
    s = filename_or_title or ''
    s = re.sub(r'\.pdf$', '', s, flags=re.I)
    s = re.sub(r'[_\-]+', ' ', s)
    # Drop ISBN-looking runs (10 or 13 digits)
    s = re.sub(r'\b\d{9,13}\b', ' ', s)
    # Drop hex/uuid-ish trailing identifiers
    s = re.sub(r'\b[0-9a-f]{16,}\b', ' ', s, flags=re.I)
    # Drop "(1)", "[N p ]", "Tier 1" style noise
    s = re.sub(r'\(\s*\d+\s*\)', ' ', s)
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    s = re.sub(r'\bTier\s*\d+\b', ' ', s, flags=re.I)
    s = re.sub(r'\bN\s*p\b', ' ', s, flags=re.I)
    # Squash separators
    s = re.sub(r'\s+--\s+|\s+\|\s+|\s+/\s+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _split_filename_to_title_author(raw: str) -> tuple[str, Optional[str]]:
    """Common pattern: 'Title -- Author Name -- Publisher -- year ...'."""
    parts = [p.strip() for p in re.split(r'\s+--\s+', raw) if p.strip()]
    if len(parts) >= 2:
        return _clean_title_hint(parts[0]), _clean_title_hint(parts[1]) or None
    return _clean_title_hint(raw), None


# ---------------------------------------------------------------------------
# Open Library
# ---------------------------------------------------------------------------

def _query_open_library(title: str, author: Optional[str]) -> Optional[BookMetadata]:
    params = {'q': title, 'limit': 5}
    if author:
        params['author'] = author
    url = f"{OPEN_LIBRARY_SEARCH}?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url)
    if not data:
        return None
    docs = data.get('docs') or []
    if not docs:
        return None

    # Prefer the doc that actually has a cover_i; OL returns a lot of
    # publication records without cover art that look fine on paper.
    docs.sort(key=lambda d: (0 if d.get('cover_i') else 1, -int(d.get('edition_count') or 0)))
    doc = docs[0]

    isbn_list = doc.get('isbn') or []
    pub_year = doc.get('first_publish_year')
    cover_id = doc.get('cover_i')
    work_key = doc.get('key')  # e.g. /works/OL12345W

    summary = None
    if work_key:
        work_data = _fetch_json(f'https://openlibrary.org{work_key}.json')
        if work_data:
            desc = work_data.get('description')
            if isinstance(desc, dict):
                summary = desc.get('value')
            elif isinstance(desc, str):
                summary = desc
            if summary:
                summary = re.sub(r'\[\d+\]', '', summary).strip()
                if len(summary) > 1200:
                    summary = summary[:1200].rsplit(' ', 1)[0] + '…'

    # OL returns duplicate author entries (data quality varies by edition);
    # dedupe and cap at 2 to keep the author cell from sprawling.
    seen, unique_authors = set(), []
    for name in doc.get('author_name', []):
        key = (name or '').strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_authors.append(name)
        if len(unique_authors) >= 2:
            break

    return BookMetadata(
        title=doc.get('title') or title,
        author=', '.join(unique_authors) or None,
        isbn=isbn_list[0] if isbn_list else None,
        published_year=int(pub_year) if pub_year else None,
        summary=summary,
        subjects=list(doc.get('subject', []))[:20],
        cover_url=OPEN_LIBRARY_COVER.format(cover_id=cover_id) if cover_id else None,
        open_library_id=work_key.split('/')[-1] if work_key else None,
        source='openlibrary',
    )


# ---------------------------------------------------------------------------
# Google Books fallback
# ---------------------------------------------------------------------------

def _query_google_books(title: str, author: Optional[str]) -> Optional[BookMetadata]:
    q = f'intitle:{title}'
    if author:
        q += f'+inauthor:{author}'
    url = f'{GOOGLE_BOOKS_SEARCH}?q={urllib.parse.quote(q)}&maxResults=5'
    data = _fetch_json(url)
    if not data or not data.get('items'):
        return None

    items = data['items']
    items.sort(key=lambda it: 0 if it.get('volumeInfo', {}).get('imageLinks') else 1)
    info = items[0].get('volumeInfo', {})

    images = info.get('imageLinks') or {}
    cover = (
        images.get('extraLarge') or images.get('large') or
        images.get('medium')     or images.get('thumbnail') or
        images.get('smallThumbnail')
    )
    if cover and cover.startswith('http://'):
        cover = 'https://' + cover[len('http://'):]

    pub_year = None
    pub_date = info.get('publishedDate') or ''
    m = re.match(r'(\d{4})', pub_date)
    if m:
        pub_year = int(m.group(1))

    isbn = None
    for ident in info.get('industryIdentifiers', []):
        if ident.get('type') in ('ISBN_13', 'ISBN_10'):
            isbn = ident.get('identifier')
            break

    return BookMetadata(
        title=info.get('title') or title,
        subtitle=info.get('subtitle'),
        author=', '.join(info.get('authors', [])[:2]) or None,
        isbn=isbn,
        published_year=pub_year,
        summary=info.get('description'),
        subjects=list(info.get('categories', []))[:20],
        cover_url=cover,
        source='google_books',
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_book(title_hint: str, author_hint: Optional[str] = None) -> Optional[BookMetadata]:
    """Look up clean metadata + cover for a book.

    `title_hint` is usually the raw filename or extracted title from the PDF.
    Returns the first plausible match from Open Library, falling back to
    Google Books. Returns None if neither service produced anything.
    """
    if not title_hint:
        return None

    title, parsed_author = _split_filename_to_title_author(title_hint)
    author = author_hint or parsed_author

    # Strip suffix annotations like "(1)" "(book two)"
    title = re.sub(r'\(.*?\)', '', title).strip()

    if len(title) < 3:
        return None

    return (_query_open_library(title, author)
            or _query_google_books(title, author))


def download_cover(url: str, output_path: str) -> bool:
    """Save the cover at *url* to *output_path*. Returns False on failure.

    Refuses to fetch anything that isn't https on a known book-cover host —
    blocks SSRF via crafted Open Library / Google Books JSON pointing at
    internal addresses (e.g. http://169.254.169.254/latest/meta-data/).
    """
    if not _is_safe_https_url(url, ALLOWED_COVER_HOSTS):
        logger.warning('cover URL refused (not in allowlist): %s', url)
        return False
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            # Cap the read so a hostile cover endpoint can't blow up memory.
            data = resp.read(8 * 1024 * 1024)
    except Exception as exc:
        logger.warning('cover download failed (%s): %s', url, exc)
        return False

    # Open Library can return tiny placeholder images (1x1) when the cover
    # was deleted. Anything under ~1KB is suspect.
    if len(data) < 1024:
        return False

    try:
        with open(output_path, 'wb') as f:
            f.write(data)
        # Optionally re-encode at a sane size with Pillow
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                img.thumbnail((600, 900))
                img.convert('RGB').save(output_path, 'JPEG', quality=85)
        except Exception:
            pass
        return True
    except OSError as exc:
        logger.warning('cover save failed (%s): %s', output_path, exc)
        return False
