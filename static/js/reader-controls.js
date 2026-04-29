/* Reader-side audiobook controls layered on top of book-reader.js:
 *   - Skip back 15s / forward 30s buttons
 *   - Smooth playback-rate slider (0.5–3.0x in 0.05 steps)
 *   - Sleep-timer fade-out (volume tapers in last 30s before pause)
 *   - Heartbeat loop: posts seconds_listened to /api/library/heartbeat
 *
 * All hooks rely on `currentAudio`, `currentUser`, `currentBook`, and
 * `playbackSpeed` already exposed by book-reader.js as window globals.
 *
 * Exposes:
 *   ReaderControls.init()
 *   ReaderControls.startSleepTimer(minutes)
 *   ReaderControls.cancelSleepTimer()
 */
(function (global) {
    'use strict';

    const SKIP_BACK_SEC    = 15;
    const SKIP_FWD_SEC     = 30;
    const HEARTBEAT_MS     = 15000;
    const FADE_OUT_SEC     = 30;

    let heartbeatTimer = null;
    let lastBeatAt = null;
    let sleepDeadline = null;       // ms epoch
    let sleepTickTimer = null;
    let sleepFadeStartedAt = null;

    // ── Skip back / forward ────────────────────────────────────────────────

    function skip(seconds) {
        const audio = global.currentAudio;
        if (!audio) return;
        const target = Math.max(0, Math.min(audio.duration || Infinity, audio.currentTime + seconds));
        audio.currentTime = target;
    }

    // ── Playback rate slider ───────────────────────────────────────────────

    function bindRateSlider() {
        const slider = document.getElementById('playbackRateSlider');
        const label  = document.getElementById('playbackRateValue');
        if (!slider) return;

        slider.addEventListener('input', () => {
            const v = parseFloat(slider.value);
            if (!Number.isFinite(v)) return;
            global.playbackSpeed = v;
            if (global.currentAudio) global.currentAudio.playbackRate = v;
            if (label) label.textContent = `${v.toFixed(2)}×`;
        });

        // Initialise from current state
        const initial = global.playbackSpeed || 1;
        slider.value = String(initial);
        if (label) label.textContent = `${initial.toFixed(2)}×`;
    }

    // ── Heartbeat (logs listening time) ─────────────────────────────────────

    function startHeartbeat() {
        stopHeartbeat();
        lastBeatAt = Date.now();
        heartbeatTimer = setInterval(_tick, HEARTBEAT_MS);
    }

    function stopHeartbeat() {
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        heartbeatTimer = null;
        lastBeatAt = null;
    }

    function _tick() {
        const audio = global.currentAudio;
        const playing = audio && !audio.paused;
        const now = Date.now();
        const deltaSec = lastBeatAt ? Math.round((now - lastBeatAt) / 1000) : 0;
        lastBeatAt = now;

        if (!playing || !global.currentBook || !global.currentUser || deltaSec <= 0) return;

        // Bound to a sane window so a long-paused tab doesn't credit hours.
        const seconds = Math.min(deltaSec, Math.round(HEARTBEAT_MS / 1000) + 5);

        fetch(`${global.API_BASE}/library/heartbeat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: global.currentUser.id,
                book_id: global.currentBook.id,
                seconds,
                pages: 0,
            }),
            keepalive: true,
        }).catch(() => {});
    }

    // ── Sleep timer with fade-out ──────────────────────────────────────────

    function startSleepTimer(minutes) {
        cancelSleepTimer();
        const ms = Math.max(1, Math.round(parseFloat(minutes) * 60 * 1000));
        sleepDeadline = Date.now() + ms;
        sleepFadeStartedAt = null;
        sleepTickTimer = setInterval(_sleepTick, 500);
        if (typeof global.showToast === 'function') {
            global.showToast(`Sleep timer: ${minutes} min`, 'info');
        }
        _renderSleepRemaining();
    }

    function cancelSleepTimer() {
        if (sleepTickTimer) clearInterval(sleepTickTimer);
        sleepTickTimer = null;
        sleepDeadline = null;
        sleepFadeStartedAt = null;
        const audio = global.currentAudio;
        if (audio) audio.volume = 1.0;
        _renderSleepRemaining();
    }

    function _sleepTick() {
        if (!sleepDeadline) return;
        const remaining = sleepDeadline - Date.now();
        const audio = global.currentAudio;

        if (audio && remaining > 0 && remaining <= FADE_OUT_SEC * 1000) {
            // Linear taper from 1 → 0 over the last FADE_OUT_SEC seconds.
            if (sleepFadeStartedAt == null) sleepFadeStartedAt = Date.now();
            const fadePct = Math.max(0, Math.min(1, remaining / (FADE_OUT_SEC * 1000)));
            audio.volume = fadePct;
        }

        _renderSleepRemaining();

        if (remaining <= 0) {
            if (audio && !audio.paused) audio.pause();
            cancelSleepTimer();
            if (typeof global.showToast === 'function') {
                global.showToast('Sleep timer ended — paused', 'info');
            }
        }
    }

    function _renderSleepRemaining() {
        const el = document.getElementById('sleepTimerStatus');
        if (!el) return;
        if (!sleepDeadline) { el.textContent = ''; return; }
        const remainingMs = Math.max(0, sleepDeadline - Date.now());
        const m = Math.floor(remainingMs / 60000);
        const s = Math.floor((remainingMs % 60000) / 1000);
        el.textContent = `Sleep in ${m}:${String(s).padStart(2, '0')}`;
    }

    // ── Init ───────────────────────────────────────────────────────────────

    function init() {
        document.getElementById('skipBackBtn')?.addEventListener('click', () => skip(-SKIP_BACK_SEC));
        document.getElementById('skipFwdBtn')?.addEventListener('click',  () => skip(SKIP_FWD_SEC));
        bindRateSlider();
        startHeartbeat();

        // Hook sleep-timer modal options if present
        document.querySelectorAll('[data-sleep-minutes]').forEach(btn => {
            btn.addEventListener('click', () => {
                const m = parseFloat(btn.dataset.sleepMinutes);
                if (m > 0) startSleepTimer(m); else cancelSleepTimer();
            });
        });
        document.getElementById('cancelSleepBtn')?.addEventListener('click', cancelSleepTimer);
    }

    global.ReaderControls = { init, startSleepTimer, cancelSleepTimer };
})(window);
