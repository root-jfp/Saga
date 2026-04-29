/* Library search & sort. Hits /api/library/search and rewires the books grid.
 *
 * Exposes:
 *   LibrarySearch.bind(opts)   — wire DOM controls and initial state
 *   LibrarySearch.run()        — re-run the current query
 */
(function (global) {
    'use strict';

    const state = {
        q: '',
        sort: 'recent',
        language: '',
        status: '',
        category_id: undefined,    // matched against current category view
        debounceTimer: null,
        onResult: null,
    };

    async function run() {
        if (!global.currentUser || typeof state.onResult !== 'function') return;
        const params = new URLSearchParams({ user_id: global.currentUser.id });
        if (state.q)        params.set('q', state.q);
        if (state.sort)     params.set('sort', state.sort);
        if (state.language) params.set('language', state.language);
        if (state.status)   params.set('status', state.status);
        if (state.category_id === null)        params.set('category_id', 'null');
        else if (typeof state.category_id === 'number') params.set('category_id', state.category_id);

        try {
            const r = await fetch(`${global.API_BASE}/library/search?${params.toString()}`);
            if (!r.ok) return;
            const books = await r.json();
            state.onResult(books);
        } catch (err) {
            console.warn('[search] failed', err);
        }
    }

    function debouncedRun(delay = 220) {
        clearTimeout(state.debounceTimer);
        state.debounceTimer = setTimeout(run, delay);
    }

    function bind(opts) {
        state.onResult = opts.onResult;
        const input = document.getElementById('librarySearchInput');
        const sortSel = document.getElementById('librarySortSelect');
        const langSel = document.getElementById('libraryLangFilter');
        const statusSel = document.getElementById('libraryStatusFilter');

        input?.addEventListener('input', () => {
            state.q = input.value.trim();
            debouncedRun();
        });
        sortSel?.addEventListener('change', () => { state.sort = sortSel.value; run(); });
        langSel?.addEventListener('change', () => { state.language = langSel.value; run(); });
        statusSel?.addEventListener('change', () => { state.status = statusSel.value; run(); });
    }

    function setCategoryContext(catId) { state.category_id = catId; }

    global.LibrarySearch = { bind, run, setCategoryContext };
})(window);
