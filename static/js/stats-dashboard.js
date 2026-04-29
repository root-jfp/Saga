/* Reading-stats dashboard. Lightweight inline canvas chart, no deps.
 *
 * Exposes:
 *   StatsDashboard.show()   — opens modal and loads data
 *   StatsDashboard.hide()
 */
(function (global) {
    'use strict';

    let modalEl = null;

    async function show() {
        if (!global.currentUser) return;
        modalEl = document.getElementById('statsModal');
        if (!modalEl) return;
        modalEl.classList.add('visible');
        await _load();
    }

    function hide() {
        if (modalEl) modalEl.classList.remove('visible');
    }

    async function _load() {
        const r = await fetch(`${global.API_BASE}/library/stats?user_id=${global.currentUser.id}&days=30`);
        if (!r.ok) return;
        const data = await r.json();

        document.getElementById('statTotalBooks').textContent = data.total ?? 0;
        document.getElementById('statReading').textContent    = data.reading ?? 0;
        document.getElementById('statFinished').textContent   = data.finished ?? 0;
        document.getElementById('statHours').textContent      = ((data.seconds_listened || 0) / 3600).toFixed(1);
        document.getElementById('statActiveDays').textContent = data.active_days ?? 0;
        document.getElementById('statPages').textContent      = data.pages_read ?? 0;

        _drawTimeline(data.timeline || [], data.window_days || 30);
    }

    function _drawTimeline(timeline, days) {
        const canvas = document.getElementById('statsTimeline');
        if (!canvas || !canvas.getContext) return;
        const dpr = window.devicePixelRatio || 1;
        const cssW = canvas.clientWidth;
        const cssH = canvas.clientHeight;
        canvas.width = cssW * dpr;
        canvas.height = cssH * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, cssW, cssH);

        // Build a dense day-by-day series to avoid gaps.
        const map = new Map(timeline.map(t => [t.date, t]));
        const today = new Date();
        const series = [];
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(today.getTime() - i * 86400000);
            const key = d.toISOString().slice(0, 10);
            series.push({ date: key, seconds: map.get(key)?.seconds || 0 });
        }

        const max = Math.max(60, ...series.map(s => s.seconds));
        const padX = 20, padY = 24;
        const usableW = cssW - padX * 2;
        const usableH = cssH - padY * 2;
        const barW = Math.max(2, usableW / series.length - 2);

        ctx.fillStyle = 'rgba(124,58,237,0.85)';   // purple to match accent
        series.forEach((s, i) => {
            const h = max ? (s.seconds / max) * usableH : 0;
            const x = padX + i * (barW + 2);
            const y = padY + (usableH - h);
            ctx.fillRect(x, y, barW, h);
        });

        // Axis label
        ctx.fillStyle = 'rgba(255,255,255,0.55)';
        ctx.font = '10px system-ui, sans-serif';
        ctx.fillText(`Last ${days} days · max ${(max / 60).toFixed(0)} min/day`, padX, 14);
    }

    global.StatsDashboard = { show, hide };
})(window);
