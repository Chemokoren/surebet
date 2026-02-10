// static/js/pwa.js

// PWA Initialization
if ('serviceWorker' in navigator && 'PushManager' in window) {
    window.addEventListener('load', function() {
        initializeServiceWorker();
        setupInstallPrompt();
    });
}

function initializeServiceWorker() {
    navigator.serviceWorker.register('/static/sw.js', { scope: '/static/' })
        .then(function(registration) {
            console.log('ServiceWorker registration successful');
            
            // Check for updates
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;
                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        showUpdateNotification();
                    }
                });
            });
        })
        .catch(function(err) {
            console.warn('ServiceWorker registration failed (non-critical): ', err);
            // Service workers are optional, don't break the app if they fail
        });
}

function setupInstallPrompt() {
    let deferredPrompt;
    
    window.addEventListener('beforeinstallprompt', (e) => {
        // e.preventDefault();  // Allow browser to show banner
        deferredPrompt = e;
        // showInstallPrompt();  // Comment out custom prompt
    });
    
    window.addEventListener('appinstalled', () => {
        console.log('FuturaPredict was installed');
        hideInstallPrompt();
    });
}

function showInstallPrompt() {
    const installPrompt = document.createElement('div');
    installPrompt.className = 'install-prompt elegant-card position-fixed bottom-0 start-0 m-3';
    installPrompt.style.zIndex = '999';
    installPrompt.style.maxWidth = '400px';
    installPrompt.innerHTML = `
        <div class="d-flex align-items-center">
            <i class="fas fa-download fa-2x text-primary me-3"></i>
            <div class="flex-grow-1">
                <h6 class="mb-1">Install FuturaPredict</h6>
                <p class="text-muted small mb-0">Get faster access with the app</p>
            </div>
            <div class="ms-3">
                <button class="btn btn-sm btn-premium" id="installButton">Install</button>
                <button class="btn btn-sm btn-outline-secondary ms-2" id="dismissInstall">Dismiss</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(installPrompt);
    
    document.getElementById('installButton').addEventListener('click', async () => {
        const deferredPrompt = window.deferredPrompt;
        if (deferredPrompt) {
            deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            if (outcome === 'accepted') {
                console.log('User installed app');
            }
            window.deferredPrompt = null;
        }
        installPrompt.remove();
    });
    
    document.getElementById('dismissInstall').addEventListener('click', () => {
        installPrompt.remove();
    });
}

function hideInstallPrompt() {
    const prompt = document.querySelector('.install-prompt');
    if (prompt) prompt.remove();
}

function showUpdateNotification() {
    showToast('A new version is available. Refresh to update.', 'info');
}

// Offline/Online detection
window.addEventListener('online', () => {
    showToast('You are back online', 'success');
    if (typeof predictionsManager !== 'undefined' && predictionsManager.refreshPredictions) {
        predictionsManager.refreshPredictions();
    }
});

window.addEventListener('offline', () => {
    showToast('You are offline. Some features may be limited.', 'warning');
});

// Background Sync (for future use)
if ('serviceWorker' in navigator && 'SyncManager' in window) {
    async function registerSync() {
        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.sync.register('sync-predictions');
            console.log('Background sync registered');
        } catch (err) {
            console.error('Sync registration failed:', err);
        }
    }
}

// Periodic Background Sync (for future use)
if ('serviceWorker' in navigator && 'PeriodicSyncManager' in window) {
    async function registerPeriodicSync() {
        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.periodicSync.register('update-predictions', { minInterval: 24 * 60 * 60 * 1000 });
            console.log('Periodic sync registered');
        } catch (err) {
            console.error('Periodic sync registration failed:', err);
        }
    }
}