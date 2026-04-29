/* Continue-listening shelf — horizontal row at the top of the library that
 * surfaces books the user has touched recently. Pulls /api/library/continue.
 *
 * Exposes:
 *   ContinueListening.refresh()  — re-fetch and re-render
 *   ContinueListening.mount(el)  — initial render into a container element
 */
(function (global) {
    'use strict';

    let mountEl = null;

    async function fetchShelf() {
        if (!global.currentUser) return [];
        try {
            const r = await fetch(`${global.API_BASE}/library/continue?user_id=${global.currentUser.id}&limit=10`);
            if (!r.ok) return [];
            return await r.json();
        } catch (err) {
            console.warn('[continue] fetch failed', err);
            return [];
        }
    }

    function _bookTile(book) {
        const tile = document.createElement('div');
        tile.className = 'continue-tile';
        tile.dataset.bookId = book.id;

        const img = document.createElement('img');
        img.src = `${global.API_BASE}/books/${book.id}/thumbnail`;
        img.alt = book.title || '';
        img.onerror = () => { img.replaceWith(_placeholder()); };
        tile.appendChild(img);

        const meta = document.createElement('div');
        meta.className = 'continue-tile-meta';
        const pct = Math.max(0, Math.min(100,
            Math.round(((book.current_page || 1) / Math.max(book.total_pages || 1, 1)) * 100)));
        meta.innerHTML = `
            <div class="continue-tile-title">${global.escapeHtml(book.title || 'Untitled')}</div>
            <div class="continue-tile-progress">
                <div class="continue-tile-bar"><div class="continue-tile-fill" style="width:${pct}%"></div></div>
                <span>${pct}%</span>
            </div>
        `;
        tile.appendChild(meta);

        tile.addEventListener('click', () => {
            if (typeof global.openBook === 'function') global.openBook(book.id);
        });
        return tile;
    }

    function _placeholder() {
        const div = document.createElement('div');
        div.className = 'continue-tile-placeholder';
        div.textContent = 'PDF';
        return div;
    }

    async function render() {
        if (!mountEl) return;
        const books = await fetchShelf();
        mountEl.innerHTML = '';
        if (!books.length) {
            mountEl.classList.add('hidden');
            return;
        }
        mountEl.classList.remove('hidden');

        const heading = document.createElement('div');
        heading.className = 'shelf-heading';
        heading.textContent = 'Continue listening';
        mountEl.appendChild(heading);

        const strip = document.createElement('div');
        strip.className = 'continue-strip';
        for (const b of books) strip.appendChild(_bookTile(b));
        mountEl.appendChild(strip);
    }

    global.ContinueListening = {
        mount(el) { mountEl = el; return render(); },
        refresh: render,
    };
})(window);
