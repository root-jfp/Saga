/* Glue between the upgrade modules and the existing book-reader.js.
 *
 * Wires:
 *   - ContinueListening shelf at the top of the library
 *   - LibrarySearch box / sort / filter — replaces the books grid contents
 *   - TocPanel (open/close from the reader)
 *   - ReaderControls (skip, speed slider, sleep fade, heartbeat)
 *   - StatsDashboard modal
 *   - "Open Library cover refresh" trigger when a book card is clicked and
 *     the cover thumbnail 404s — re-runs enrichment via /api/books/<id>.
 *
 * Strategy: piggyback on existing event hooks rather than refactor
 * book-reader.js. The author of book-reader.js exposes `currentBook`,
 * `currentUser`, `books`, `openBook`, `goToPage`, `escapeHtml`, etc. on
 * the global `window` so we can wire from the outside cleanly.
 */
(function () {
    'use strict';

    function init() {
        // Continue-listening
        const shelf = document.getElementById('continueShelf');
        if (shelf && window.ContinueListening) ContinueListening.mount(shelf);

        // Library search bar
        if (window.LibrarySearch) {
            LibrarySearch.bind({
                onResult: (filteredBooks) => {
                    // The existing renderer reads the global `books` array. We
                    // patch it transiently and rerender the current view.
                    if (!Array.isArray(filteredBooks)) return;
                    window.books = filteredBooks;
                    if (typeof window.renderLibrary === 'function') window.renderLibrary();
                },
            });
            _populateLanguageFilter();
        }

        // Stats button + modal
        document.getElementById('statsBtn')?.addEventListener('click', () => {
            window.StatsDashboard && StatsDashboard.show();
        });
        document.getElementById('closeStatsModal')?.addEventListener('click', () => {
            window.StatsDashboard && StatsDashboard.hide();
        });

        // TOC button
        document.getElementById('tocBtn')?.addEventListener('click', () => {
            if (!window.currentBook || !window.TocPanel) return;
            TocPanel.openFor(window.currentBook.id);
        });

        // Reader audiobook controls (skip, rate slider, sleep fade, heartbeat)
        if (window.ReaderControls) ReaderControls.init();

        // Refresh continue-listening shelf whenever the user changes (the
        // existing handlers fire `usersLoaded` and we re-render below).
        document.addEventListener('usersLoaded', () => {
            if (window.ContinueListening) ContinueListening.refresh();
            _populateLanguageFilter();
        });
        // And after a book finishes loading, refresh the shelf so progress
        // pct shows the latest page.
        document.addEventListener('bookProgressSaved', () => {
            if (window.ContinueListening) ContinueListening.refresh();
        });
    }

    function _populateLanguageFilter() {
        const sel = document.getElementById('libraryLangFilter');
        if (!sel || !Array.isArray(window.books)) return;
        const langs = Array.from(new Set(
            window.books.map(b => b.detected_language).filter(Boolean)
        )).sort();
        // Preserve current selection
        const current = sel.value;
        sel.innerHTML = '<option value="">All languages</option>' +
            langs.map(l => `<option value="${l}">${l.toUpperCase()}</option>`).join('');
        sel.value = langs.includes(current) ? current : '';
    }

    function provideGoToPage() {
        // book-reader.js doesn't export goToPage globally — TOC needs it.
        if (typeof window.goToPage === 'function') return;
        window.goToPage = function (pageNumber) {
            if (typeof window.renderPage === 'function') {
                window.renderPage(pageNumber);
            } else if (typeof window.currentBook !== 'undefined') {
                // Fallback: nudge the next-page button N times — ugly but safe.
                const target = parseInt(pageNumber, 10);
                const cur = parseInt(window.currentPage || 1, 10);
                const diff = target - cur;
                const btn = document.getElementById(diff >= 0 ? 'nextPageBtn' : 'prevPageBtn');
                if (btn) for (let i = 0; i < Math.abs(diff); i++) btn.click();
            }
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            provideGoToPage();
            init();
        });
    } else {
        provideGoToPage();
        init();
    }
})();
