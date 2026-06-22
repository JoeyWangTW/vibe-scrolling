/**
 * Settings page — two sections:
 *   1. Connected accounts (the old Platforms page)
 *   2. Workspace folder (where collections + curation live)
 *
 * Replaces the standalone Platforms tab. Onboarding still owns first-time
 * connection + workspace setup; this page is for ongoing changes.
 */
window.SettingsPage = {
    workspace: null,
    _platformPolls: {},

    render() {
        return `
            <div class="fade-in">
                <h1 class="page-title">Settings</h1>
                <p class="page-subtitle">
                    Manage connected accounts and where exports land.
                </p>

                <h2 class="section-title">Connected accounts</h2>
                <p class="text-secondary text-sm mb-3">
                    Each connection saves a session locally so the app can scroll on your behalf.
                    No tokens leave this machine.
                </p>
                <div class="card-grid" id="settings-platforms">
                    <div class="card flex-center text-secondary" style="padding:40px">Loading…</div>
                </div>

                <h2 class="section-title">Workspace folder</h2>
                <p class="text-secondary text-sm mb-3">
                    Collected feeds, your <code>goals.md</code>, and curated results all live here.
                    Anything inside iCloud Drive will sync to your phone.
                </p>
                <div class="card" id="settings-workspace-card">
                    <div class="text-secondary">Loading…</div>
                </div>
            </div>
        `;
    },

    async init() {
        await Promise.all([
            this._loadPlatforms(),
            this._loadWorkspace(),
        ]);
    },

    // ---------------------------------------------------------------- platforms

    async _loadPlatforms() {
        try {
            const status = await api('/auth/status');
            this._renderPlatforms(status);
        } catch (e) {
            const el = document.getElementById('settings-platforms');
            if (el) el.innerHTML = `<div class="card text-danger">Failed to load platform status</div>`;
        }
    },

    _renderPlatforms(status) {
        const cards = Object.entries(status).map(([platform, info]) => {
            const name = PlatformIcons.name(platform);
            const icon = PlatformIcons.svg(platform, { size: 28 }) || '?';
            const connected = !!(info && info.connected);
            return `
                <div class="card platform-${platform}" id="settings-card-${platform}">
                    <div class="card-header">
                        <div class="platform-icon platform-icon-${platform}">${icon}</div>
                        <div>
                            <div class="font-semibold text-subtitle">${name}</div>
                            <div class="text-sm text-secondary">
                                <span class="dot ${connected ? 'dot-success' : 'dot-muted'}"></span>
                                ${connected ? 'Connected' : 'Not connected'}
                            </div>
                        </div>
                    </div>
                    <div id="settings-action-${platform}">
                        ${connected
                            ? `<button class="btn btn-danger" onclick="SettingsPage.disconnect('${platform}')">Disconnect</button>`
                            : `<button class="btn btn-primary" onclick="SettingsPage.connect('${platform}')">Connect</button>`}
                    </div>
                </div>
            `;
        }).join('');
        const root = document.getElementById('settings-platforms');
        if (root) root.innerHTML = cards;
    },

    async connect(platform) {
        const actionDiv = document.getElementById(`settings-action-${platform}`);
        if (!actionDiv) return;
        actionDiv.innerHTML = `
            <div class="text-sm text-secondary">
                <span class="dot dot-warning dot-pulse"></span>
                Opening browser…
            </div>
        `;
        try {
            await api(`/auth/connect/${platform}`, { method: 'POST' });
            actionDiv.innerHTML = `
                <div class="text-sm text-warning mb-3">
                    <span class="dot dot-warning dot-pulse"></span>
                    A browser window should be open — log in there
                </div>
                <button class="btn btn-primary" onclick="SettingsPage.completeAuth('${platform}')">
                    Done — I've logged in
                </button>
                <button class="btn btn-secondary ml-2" onclick="SettingsPage.cancelAuth('${platform}')">
                    Cancel
                </button>
            `;
        } catch (e) {
            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Failed to open browser: ${e.message}</div>
                <button class="btn btn-primary" onclick="SettingsPage.connect('${platform}')">Retry</button>
            `;
        }
    },

    async completeAuth(platform) {
        const actionDiv = document.getElementById(`settings-action-${platform}`);
        if (!actionDiv) return;
        actionDiv.innerHTML = `
            <div class="text-sm text-secondary">
                <span class="dot dot-warning dot-pulse"></span>
                Saving session and verifying login…
            </div>
        `;
        try {
            await api(`/auth/connect/${platform}/complete`, { method: 'POST' });
            let attempts = 0;
            while (attempts < 30) {
                await new Promise(r => setTimeout(r, 1000));
                const status = await api(`/auth/connect/${platform}/status`);
                if (status.status === 'completed') { await this._loadPlatforms(); return; }
                if (status.status === 'failed') {
                    actionDiv.innerHTML = `
                        <div class="text-danger mb-2">${status.error || 'Login failed'}</div>
                        <button class="btn btn-primary" onclick="SettingsPage.connect('${platform}')">Try again</button>
                    `;
                    return;
                }
                if (status.status === 'cancelled') { await this._loadPlatforms(); return; }
                attempts++;
            }
            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Verification timed out</div>
                <button class="btn btn-primary" onclick="SettingsPage.connect('${platform}')">Try again</button>
            `;
        } catch (e) {
            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Error: ${e.message}</div>
                <button class="btn btn-primary" onclick="SettingsPage.connect('${platform}')">Try again</button>
            `;
        }
    },

    async cancelAuth(platform) {
        const actionDiv = document.getElementById(`settings-action-${platform}`);
        if (actionDiv) actionDiv.innerHTML = '<button class="btn btn-secondary" disabled>Cancelling…</button>';
        try { await api(`/auth/connect/${platform}/cancel`, { method: 'POST' }); } catch (e) {}
        await this._loadPlatforms();
    },

    async disconnect(platform) {
        if (!confirm(`Disconnect ${platform}? You'll need to log in again to collect.`)) return;
        try {
            await api(`/auth/disconnect/${platform}`, { method: 'POST' });
            await this._loadPlatforms();
        } catch (e) {
            alert('Failed to disconnect: ' + e.message);
        }
    },

    // ---------------------------------------------------------------- workspace

    async _loadWorkspace() {
        try {
            this.workspace = await api('/workspace');
        } catch (e) {
            this.workspace = { is_setup: false };
        }
        this._renderWorkspace();
    },

    _renderWorkspace() {
        const card = document.getElementById('settings-workspace-card');
        if (!card) return;
        const ws = this.workspace || {};
        if (!ws.is_setup) {
            const suggested = ws.suggested_path || '~/Focus Lab Feed';
            card.innerHTML = `
                <div class="setup-box">
                    <div class="setup-title">Pick a workspace folder</div>
                    <p class="text-secondary text-sm mb-3">
                        Collected feeds and your <code>goals.md</code> live here. The app will create the
                        folder if it doesn't exist.
                    </p>
                    <div class="setup-row">
                        <input type="text" id="settings-ws-path" class="setup-input"
                               value="${suggested.replace(/^\/Users\/[^/]+/, '~')}">
                        <button class="btn btn-secondary btn-sm" onclick="SettingsPage.pickFolder()">Choose…</button>
                        <button class="btn btn-primary btn-sm" onclick="SettingsPage.setupWorkspace()">Create &amp; set up</button>
                    </div>
                    <div id="settings-ws-error" class="text-danger text-sm mt-2" hidden></div>
                </div>
            `;
            return;
        }
        const pretty = (ws.path || '').replace(/^\/Users\/[^/]+/, '~');
        card.innerHTML = `
            <div class="curation-dir-row">
                <div class="curation-dir-label">Folder</div>
                <div class="curation-dir-path" title="${ws.path || ''}">${pretty}</div>
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.openWorkspace()">Open</button>
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.changeWorkspace()">Change…</button>
            </div>
            <p class="text-secondary text-xs mt-2">
                Collections land in <code>${pretty}/data/</code>.
            </p>
        `;
    },

    async pickFolder() {
        if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.pick_folder) return;
        const input = document.getElementById('settings-ws-path');
        try {
            const picked = await window.pywebview.api.pick_folder('');
            if (picked && input) {
                input.value = picked.replace(/^\/Users\/[^/]+/, '~');
                input.dataset.absolute = picked;
            }
        } catch (e) {
            console.warn('Folder picker failed:', e);
        }
    },

    async setupWorkspace() {
        const input = document.getElementById('settings-ws-path');
        const errEl = document.getElementById('settings-ws-error');
        const absPath = (input && input.dataset && input.dataset.absolute) || '';
        const raw = (absPath || (input && input.value || '')).trim();
        if (!raw) {
            if (errEl) { errEl.textContent = 'Please enter a folder path.'; errEl.hidden = false; }
            return;
        }
        try {
            const r = await api('/workspace/setup', {
                method: 'POST',
                body: JSON.stringify({ path: raw, update_app_files: true }),
            });
            if (r.success) {
                await this._loadWorkspace();
                window.dispatchEvent(new CustomEvent('workspace:updated'));
            }
        } catch (e) {
            if (errEl) { errEl.textContent = e.message || String(e); errEl.hidden = false; }
        }
    },

    async changeWorkspace() {
        const current = (this.workspace && this.workspace.path) || '';
        let next = null;
        if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
            try { next = await window.pywebview.api.pick_folder(current); } catch (e) { /* fall through */ }
        }
        if (!next) next = prompt('New workspace folder path:', current);
        if (!next || next === current) return;
        try {
            await api('/workspace/setup', {
                method: 'POST',
                body: JSON.stringify({ path: next }),
            });
            await this._loadWorkspace();
            window.dispatchEvent(new CustomEvent('workspace:updated'));
        } catch (e) {
            alert('Could not change workspace: ' + e.message);
        }
    },

    async openWorkspace() {
        try {
            await api('/workspace/reveal', { method: 'POST', body: JSON.stringify({}) });
        } catch (e) {
            console.warn('Reveal failed:', e);
        }
    },
};
