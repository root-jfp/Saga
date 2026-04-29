/* Table-of-contents drawer in the reader.
 *
 * Hits /api/books/<id>/toc which returns:
 *   { source: 'pdf_outline'|'detected_headings'|'none', entries: [{level,title,page}], total_pages }
 *
 * Renders into #tocPanel; clicking an entry calls global.goToPage(page).
 *
 * Exposes:
 *   TocPanel.openFor(bookId)
 *   TocPanel.toggle()
 */
(function (global) {
    'use strict';

    let panelEl = null;
    let currentBookId = null;
    let lastEntries = [];

    function _ensurePanel() {
        if (panelEl) return panelEl;
        panelEl = document.getElementById('tocPanel');
        return panelEl;
    }

    async function openFor(bookId) {
        const panel = _ensurePanel();
        if (!panel) return;
        currentBookId = bookId;
        panel.classList.remove('hidden');
        panel.innerHTML = '<div class="toc-loading">Loading chapters…</div>';

        try {
            const r = await fetch(`${global.API_BASE}/books/${bookId}/toc`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            lastEntries = data.entries || [];
            _render(data);
        } catch (err) {
            // Escape: err.message is opaque text from the network layer; we
            // never want to render it as HTML.
            const safeMsg = (global.escapeHtml || ((s) => String(s)))(err && err.message || 'unknown error');
            panel.innerHTML = `<div class="toc-empty">Couldn't load chapters (${safeMsg})</div>`;
        }
    }

    function _render(data) {
        const panel = _ensurePanel();
        if (!panel) return;
        const sourceLabel = data.source === 'pdf_outline' ? 'From PDF outline'
                           : data.source === 'detected_headings' ? 'Detected headings'
                           : '';
        if (!data.entries || !data.entries.length) {
            panel.innerHTML = `
                <div class="toc-header">
                    <span>Chapters</span>
                    <button class="toc-close" id="tocCloseBtn" aria-label="Close">×</button>
                </div>
                <div class="toc-empty">No chapter outline available for this book.</div>
            `;
            panel.querySelector('#tocCloseBtn')?.addEventListener('click', close);
            return;
        }

        const list = data.entries.map(e => {
            const level = Math.max(1, Math.min(parseInt(e.level || 1, 10), 4));
            return `<li class="toc-entry toc-level-${level}" data-page="${e.page}">
                <span class="toc-title">${global.escapeHtml(e.title)}</span>
                <span class="toc-page">p.${e.page}</span>
            </li>`;
        }).join('');

        panel.innerHTML = `
            <div class="toc-header">
                <span>Chapters · <em>${sourceLabel}</em></span>
                <button class="toc-close" id="tocCloseBtn" aria-label="Close">×</button>
            </div>
            <ul class="toc-list">${list}</ul>
        `;

        panel.querySelector('#tocCloseBtn')?.addEventListener('click', close);
        panel.querySelectorAll('.toc-entry').forEach(li => {
            li.addEventListener('click', () => {
                const page = parseInt(li.dataset.page, 10);
                if (Number.isFinite(page) && typeof global.goToPage === 'function') {
                    global.goToPage(page);
                }
            });
        });
    }

    function close() {
        const panel = _ensurePanel();
        if (panel) panel.classList.add('hidden');
    }

    function toggle() {
        const panel = _ensurePanel();
        if (!panel) return;
        if (panel.classList.contains('hidden') && currentBookId) {
            openFor(currentBookId);
        } else {
            close();
        }
    }

    global.TocPanel = { openFor, toggle, close };
})(window);
