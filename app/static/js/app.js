/**
 * Focus Lab Feed Collector — SPA Router & State
 * Page modules are loaded from separate files.
 */

// Exposed on `window` so other classic scripts (e.g. onboarding.js running
// inside its own IIFE) can call `window.App.bootApp()` after onboarding finishes.
// Top-level `const` in a classic script does NOT attach to window — without
// this, finish() short-circuited and the main app never booted.
const App = window.App = {
    currentPage: null,
    pages: {},

    async init() {
        // Gate on onboarding — returning users (workspace set up, Chromium installed)
        // skip straight to the app; first-timers get the full walkthrough.
        const needsOnboarding = await this.checkOnboardingNeeded();
        if (needsOnboarding) {
            Onboarding.start();
            return;
        }

        this.bootApp();
    },

    bootApp() {
        // Register page modules (loaded from separate script files)
        this.pages = {
            collect: window.CollectPage || { render: () => '<div class="empty-state"><div class="icon">&#9655;</div><p>Loading collection...</p></div>' },
            viewer: window.ViewerPage || { render: () => '<div class="empty-state"><div class="icon">&#9776;</div><p>Loading viewer...</p></div>' },
            curated: window.CuratedPage || { render: () => '<div class="empty-state"><div class="icon">&#9734;</div><p>Loading curated feed...</p></div>' },
            data: window.DataPage || { render: () => '<div class="empty-state"><div class="icon">&#9776;</div><p>Loading data...</p></div>' },
            curate: window.CuratePage || { render: () => '<div class="empty-state"><div class="icon">&#9883;</div><p>Loading curation...</p></div>' },
            settings: window.SettingsPage || { render: () => '<div class="empty-state"><div class="icon">&#9881;</div><p>Loading settings...</p></div>' },
        };

        // Show main app — overrides any inline display:none from Onboarding.start().
        const appEl = document.getElementById('app');
        if (appEl) appEl.style.display = 'flex';

        // Populate workspace chip (runs once; refreshWorkspaceChip binds button handler)
        this.refreshWorkspaceChip();

        // Any page can fire `workspace:updated` after a setup/change to force
        // the sidebar chip to re-read /api/workspace. Cleaner than every call
        // site remembering to poke App.refreshWorkspaceChip directly.
        window.addEventListener('workspace:updated', () => this.refreshWorkspaceChip());

        // Cmd/Ctrl+R inside pywebview doesn't reload by default — wire it up.
        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && (e.key === 'r' || e.key === 'R')) {
                e.preventDefault();
                location.reload();
            }
        });

        // Theme toggle — light ⇄ dark, persisted in localStorage.
        const themeBtn = document.getElementById('theme-toggle');
        if (themeBtn) {
            themeBtn.addEventListener('click', () => {
                const isDark = document.documentElement.classList.toggle('dark');
                try { localStorage.setItem('focuslab:theme', isDark ? 'dark' : 'light'); } catch (e) {}
            });
        }

        // Nav click handlers
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const page = item.dataset.page;
                this.navigate(page);
            });
        });

        // Handle hash navigation
        window.addEventListener('hashchange', () => {
            const page = location.hash.slice(1) || 'collect';
            this.navigate(page, false);
        });

        // Initial page — default to Collect (the action), not Settings (config)
        const page = location.hash.slice(1) || 'collect';
        this.navigate(page, false);
    },

    async checkOnboardingNeeded() {
        // Show onboarding when EITHER the browser engine is missing OR the
        // user hasn't set up a workspace yet. Both are hard requirements to
        // actually use the app, and both are collected by Onboarding.
        try {
            const [setup, ws] = await Promise.all([
                api('/setup/status').catch(() => ({ setup_needed: false })),
                api('/workspace').catch(() => ({ is_setup: true })), // fail open — don't block if API down
            ]);
            return !!(setup.setup_needed || !ws.is_setup);
        } catch (e) {
            return false;
        }
    },

    async refreshWorkspaceChip() {
        try {
            const ws = await api('/workspace');
            const pathEl = document.getElementById('workspace-path');
            const openBtn = document.getElementById('workspace-open-btn');
            if (!pathEl || !openBtn) return;
            if (ws.is_setup) {
                const home = (ws.path || '').replace(/^\/Users\/[^/]+/, '~');
                pathEl.textContent = home;
                pathEl.title = ws.path || '';
                openBtn.textContent = 'Open folder';
                openBtn.disabled = false;
            } else {
                pathEl.textContent = 'Not set up';
                pathEl.title = '';
                openBtn.textContent = 'Set up in Settings';
                openBtn.disabled = false;
                openBtn.onclick = () => { location.hash = 'settings'; };
                return;
            }
            openBtn.onclick = async () => {
                try { await api('/workspace/reveal', { method: 'POST', body: JSON.stringify({}) }); }
                catch (e) { console.warn('Reveal failed:', e); }
            };
        } catch (e) {
            console.warn('Workspace info unavailable:', e);
        }
    },

    navigate(page, updateHash = true) {
        // Legacy aliases — old hash links from before the Platforms→Settings move.
        if (page === 'platforms') page = 'settings';
        if (!this.pages[page]) page = 'collect';

        // Update nav active state
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.page === page);
        });

        if (updateHash) location.hash = page;

        // Render page
        const content = document.getElementById('content');
        const pageModule = this.pages[page];
        content.innerHTML = pageModule.render();

        // Add fade-in animation
        const wrapper = content.firstElementChild;
        if (wrapper) wrapper.classList.add('fade-in');

        if (pageModule.init) pageModule.init();
        this.currentPage = page;
    },
};

// Helper: fetch JSON from API
async function api(path, options = {}) {
    const res = await fetch(`/api${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

document.addEventListener('DOMContentLoaded', () => App.init());
