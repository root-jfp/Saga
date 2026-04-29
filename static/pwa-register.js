/* Register the Saga service worker. Loaded from index.html. */
(function () {
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', () => {
        navigator.serviceWorker
            .register('/sw.js', { scope: '/' })
            .then((reg) => {
                // Auto-update if a new worker is found.
                reg.addEventListener('updatefound', () => {
                    const fresh = reg.installing;
                    if (!fresh) return;
                    fresh.addEventListener('statechange', () => {
                        if (fresh.state === 'installed' && navigator.serviceWorker.controller) {
                            // Surface to whatever toast helper is on the page,
                            // never leak via console.log.
                            if (typeof window.showToast === 'function') {
                                window.showToast('A new version of Saga is available — refresh to apply.', 'info');
                            }
                        }
                    });
                });
            })
            .catch((err) => console.warn('[saga] sw register failed', err));
    });
})();
