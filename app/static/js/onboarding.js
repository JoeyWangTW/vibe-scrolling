/**
 * Onboarding — gates the main app on first launch.
 *
 * Steps:
 *   1. welcome   — what this app does, emoji walkthrough
 *   2. setup     — download Chromium (skipped if already installed)
 *   3. connect   — hook up at least one social account
 *   4. workspace — pick export folder + auto-export toggle
 *   5. done      — summary, enter the app
 *
 * Main app stays hidden until finish() runs. Returning users (workspace
 * already set up, Chromium installed) skip onboarding entirely — the boot
 * logic in app.js decides whether to enter here.
 */
(function () {
'use strict';

const PLATFORMS = PlatformIcons.list().map(id => ({
    id,
    name: PlatformIcons.name(id),
    iconHtml: PlatformIcons.svg(id, { size: 24 }),
}));

const STEPS = ['welcome', 'setup', 'connect', 'workspace', 'done'];

window.Onboarding = {
    step: 'welcome',
    state: {
        chromiumNeeded: false,
        connected: {},       // platform -> bool
        workspacePath: '',   // what the user picked
        workspaceAbsPath: '',// absolute path from native picker, if any
        autoExport: true,    // default on — user can turn off
        suggestedPath: '',
    },

    async start() {
        const appEl = document.getElementById('app');
        if (appEl) appEl.style.display = 'none';
        this._ensureOverlay();

        // Render an immediate loading skeleton so the overlay is never empty
        // while we wait on /setup, /auth, /workspace.
        const overlay = document.getElementById('onboarding');
        if (overlay) overlay.innerHTML = `
            <div class="ob-shell">
                <div class="ob-step">
                    <div class="ob-icon-big">✨</div>
                    <p class="ob-lead">Getting things ready…</p>
                </div>
            </div>
        `;

        // Pull current state so we can skip already-done steps on subsequent runs.
        let setupStatus = { setup_needed: false };
        let authStatus = {};
        let workspace = { is_setup: false, suggested_path: '' };
        try { setupStatus = await api('/setup/status'); } catch (e) {}
        try { authStatus = await api('/auth/status'); } catch (e) {}
        try { workspace = await api('/workspace'); } catch (e) {}

        this.state.chromiumNeeded = !!setupStatus.setup_needed;
        this.state.connected = Object.fromEntries(
            Object.entries(authStatus || {}).map(([p, info]) => [p, !!(info && info.connected)])
        );
        this.state.suggestedPath = workspace.suggested_path || '~/Focus Lab Feed';
        this.state.workspacePath = this.state.suggestedPath;

        this.step = 'welcome';
        this.render();
    },

    render() {
        const overlay = document.getElementById('onboarding');
        if (!overlay) return;
        let stageHtml = '';
        try {
            stageHtml = this._renderStep() || '';
        } catch (e) {
            console.error('[onboarding] render failed for step', this.step, e);
            stageHtml = `<div class="ob-step"><h1 class="ob-title">Something went wrong</h1>
                <p class="ob-lead text-danger">${(e && e.message) || String(e)}</p>
                <div class="ob-actions"><button class="btn btn-primary" id="ob-finish">Continue anyway</button></div></div>`;
        }
        overlay.innerHTML = `
            <div class="ob-shell">
                <div class="ob-progress">
                    ${STEPS.filter(s => s !== 'done').map((s) => {
                        const done = STEPS.indexOf(this.step) > STEPS.indexOf(s) || this.step === 'done';
                        const active = this.step === s;
                        return `<div class="ob-dot ${done ? 'done' : ''} ${active ? 'active' : ''}"
                                    data-step="${s}"></div>`;
                    }).join('')}
                </div>
                <div class="ob-stage-wrap" id="ob-stage">${stageHtml}</div>
            </div>
        `;
        try { this._bindStep(); } catch (e) { console.error('[onboarding] bind failed', e); }
    },

    _ensureOverlay() {
        let overlay = document.getElementById('onboarding');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'onboarding';
            document.body.appendChild(overlay);
        }
        overlay.className = 'onboarding-overlay';
    },

    _renderStep() {
        switch (this.step) {
            case 'welcome':   return this._renderWelcome();
            case 'setup':     return this._renderSetup();
            case 'connect':   return this._renderConnect();
            case 'workspace': return this._renderWorkspace();
            case 'done':      return this._renderDone();
            default:          return '';
        }
    },

    // --------------------------------------------------------------- steps

    _renderWelcome() {
        return `
            <div class="ob-step ob-welcome">
                <div class="ob-hero">Vibe Scrolling</div>
                <h1 class="ob-title">We scroll social media<br>so you don't have to.</h1>
                <p class="ob-lead">
                    Your agent logs into your accounts, auto-scrolls your feeds in the background,
                    and collects posts, images, and videos. Then AI curates them around
                    <em>your</em> goals — not the algorithm's.
                </p>
                <div class="ob-pipeline">
                    <div class="ob-stage-item" style="--d:0s">
                        <div class="ob-emoji">🔐</div>
                        <div class="ob-stage-label">Log in once</div>
                    </div>
                    <div class="ob-arrow" style="--d:.3s">→</div>
                    <div class="ob-stage-item" style="--d:.6s">
                        <div class="ob-emoji">🌀</div>
                        <div class="ob-stage-label">Auto-scroll</div>
                    </div>
                    <div class="ob-arrow" style="--d:.9s">→</div>
                    <div class="ob-stage-item" style="--d:1.2s">
                        <div class="ob-emoji">📦</div>
                        <div class="ob-stage-label">Collect posts</div>
                    </div>
                    <div class="ob-arrow" style="--d:1.5s">→</div>
                    <div class="ob-stage-item" style="--d:1.8s">
                        <div class="ob-emoji">🤖</div>
                        <div class="ob-stage-label">AI curates</div>
                    </div>
                </div>
                <div class="ob-actions">
                    <button class="btn btn-primary btn-lg" id="ob-next">Get started</button>
                </div>
                <div class="ob-footnote">
                    Everything runs locally. No servers, no tokens sent anywhere — just a real
                    browser doing what you'd do yourself.
                </div>
            </div>
        `;
    },

    _renderSetup() {
        // `_advance` already skips this step when chromium isn't needed, so
        // if we ever render here the install is required.
        return `
            <div class="ob-step">
                <div class="ob-icon-big">🧭</div>
                <h1 class="ob-title">Install the browser engine</h1>
                <p class="ob-lead">
                    We use a dedicated Chromium install so scrolling doesn't interfere with your
                    regular browser. One-time download, about 150 MB.
                </p>
                <div class="ob-actions">
                    <button class="btn btn-primary btn-lg" id="ob-setup-start">Install Chromium</button>
                </div>
                <div id="ob-setup-progress" class="onboarding-progress hidden">
                    <div class="progress-bar"><div class="progress-fill" id="ob-setup-bar" style="width:0%;animation:setup-pulse 2s infinite"></div></div>
                    <p id="ob-setup-msg">Downloading…</p>
                </div>
            </div>
        `;
    },

    _renderConnect() {
        const connectedCount = Object.values(this.state.connected).filter(Boolean).length;
        const canContinue = connectedCount > 0;
        return `
            <div class="ob-step ob-step-wide">
                <div class="ob-icon-big">🔐</div>
                <h1 class="ob-title">Connect at least one account</h1>
                <p class="ob-lead">
                    Click one — a real browser window opens, you log in like normal, the app saves
                    the session locally and closes the window. You can connect more later.
                </p>
                <div class="ob-platform-grid">
                    ${PLATFORMS.map(p => this._renderPlatformCard(p)).join('')}
                </div>
                <div class="ob-actions">
                    <button class="btn btn-secondary" id="ob-back">Back</button>
                    <button class="btn btn-primary" id="ob-next" ${canContinue ? '' : 'disabled'}>
                        ${canContinue ? `Continue (${connectedCount} connected)` : 'Connect one to continue'}
                    </button>
                </div>
            </div>
        `;
    },

    _renderPlatformCard(p) {
        const connected = !!this.state.connected[p.id];
        const inProgress = !!this.state._connectingPlatform && this.state._connectingPlatform === p.id;
        let action;
        if (connected) {
            action = `<span class="ob-platform-status connected">✓ Connected</span>`;
        } else if (inProgress) {
            action = `
                <div class="ob-platform-status working">
                    <span class="dot dot-warning dot-pulse"></span>
                    Log in in the open window
                </div>
                <div class="ob-platform-row">
                    <button class="btn btn-primary btn-sm" data-ob-action="complete" data-ob-platform="${p.id}">Done — I logged in</button>
                    <button class="btn btn-secondary btn-sm" data-ob-action="cancel" data-ob-platform="${p.id}">Cancel</button>
                </div>
            `;
        } else {
            action = `<button class="btn btn-primary btn-sm" data-ob-action="connect" data-ob-platform="${p.id}">Connect</button>`;
        }
        return `
            <div class="ob-platform-card ${connected ? 'is-connected' : ''}">
                <div class="ob-platform-head">
                    <div class="platform-icon platform-icon-${p.id}">${p.iconHtml}</div>
                    <div class="ob-platform-name">${p.name}</div>
                </div>
                <div class="ob-platform-action">${action}</div>
            </div>
        `;
    },

    _renderWorkspace() {
        const hasPicker = !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
        const shown = (this.state.workspacePath || this.state.suggestedPath || '~/Focus Lab Feed')
            .replace(/^\/Users\/[^/]+/, '~');
        return `
            <div class="ob-step ob-step-wide">
                <div class="ob-icon-big">📁</div>
                <h1 class="ob-title">Where should your content live?</h1>
                <p class="ob-lead">
                    Collected posts, media, and curation packs go into this folder. Anywhere works
                    — iCloud Drive is a nice pick if you want it to sync to your phone.
                </p>

                <div class="ob-folder-box">
                    <label class="ob-folder-label">Folder</label>
                    <div class="ob-folder-row">
                        <input type="text" id="ob-ws-path" class="setup-input" value="${shown}">
                        ${hasPicker ? '<button class="btn btn-secondary btn-sm" id="ob-ws-pick">Choose…</button>' : ''}
                    </div>
                    <div class="text-secondary text-xs mt-2">
                        We'll create the folder if it doesn't exist.
                    </div>
                </div>

                <label class="ob-auto-toggle">
                    <span class="auto-export-text">
                        <strong>Auto-export after each collection</strong>
                        <span class="text-secondary text-xs">
                            Packs your latest run into a curation-ready folder automatically.
                            Heads up: images and videos can take up disk space over time.
                        </span>
                    </span>
                    <span class="toggle-switch">
                        <input type="checkbox" id="ob-auto-export" ${this.state.autoExport ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </span>
                </label>

                <div class="ob-hint" id="ob-auto-hint" ${this.state.autoExport ? 'hidden' : ''}>
                    <span class="ob-hint-icon">💡</span>
                    <span class="ob-hint-text">
                        With auto-export off, head to the <strong>Export</strong> tab after
                        collecting to pick specific days and platforms.
                    </span>
                </div>

                <div class="ob-actions">
                    <button class="btn btn-secondary" id="ob-back">Back</button>
                    <button class="btn btn-primary" id="ob-finish-setup">Create folder &amp; continue</button>
                </div>
                <div id="ob-ws-error" class="text-danger text-sm mt-2" hidden></div>
            </div>
        `;
    },

    _renderDone() {
        const pretty = (this.state.workspaceAbsPath || this.state.workspacePath || '')
            .replace(/^\/Users\/[^/]+/, '~');
        const connectedCount = Object.values(this.state.connected).filter(Boolean).length;
        return `
            <div class="ob-step">
                <div class="ob-icon-big ob-bounce">🎉</div>
                <h1 class="ob-title">You're all set.</h1>
                <p class="ob-lead">
                    ${connectedCount} account${connectedCount === 1 ? '' : 's'} connected ·
                    workspace at <code>${pretty}</code>${this.state.autoExport ? ' · auto-export <strong>on</strong>' : ''}.
                </p>
                <div class="ob-actions">
                    <button class="btn btn-primary btn-lg" id="ob-finish">Open Vibe Scrolling</button>
                </div>
            </div>
        `;
    },

    // --------------------------------------------------------------- events

    _bindStep() {
        const nextBtn = document.getElementById('ob-next');
        const backBtn = document.getElementById('ob-back');
        if (nextBtn) nextBtn.addEventListener('click', () => this.onNext());
        if (backBtn) backBtn.addEventListener('click', () => this.onBack());

        if (this.step === 'setup') {
            const s = document.getElementById('ob-setup-start');
            if (s) s.addEventListener('click', () => this._runSetup());
        }

        if (this.step === 'connect') {
            document.querySelectorAll('[data-ob-action]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const action = btn.dataset.obAction;
                    const platform = btn.dataset.obPlatform;
                    if (action === 'connect')  this._connectPlatform(platform);
                    if (action === 'complete') this._completeAuth(platform);
                    if (action === 'cancel')   this._cancelAuth(platform);
                });
            });
        }

        if (this.step === 'workspace') {
            const pickBtn = document.getElementById('ob-ws-pick');
            if (pickBtn) pickBtn.addEventListener('click', () => this._pickFolder());
            const auto = document.getElementById('ob-auto-export');
            if (auto) auto.addEventListener('change', (e) => {
                this.state.autoExport = e.target.checked;
                const hint = document.getElementById('ob-auto-hint');
                if (hint) hint.hidden = !!e.target.checked;
            });
            const finish = document.getElementById('ob-finish-setup');
            if (finish) finish.addEventListener('click', () => this._commitWorkspace());
        }

        // ob-finish is the "enter the app" button — used by both the done
        // step and the render-error fallback UI, so bind it whenever it
        // exists rather than gating on step.
        const finish = document.getElementById('ob-finish');
        if (finish) finish.addEventListener('click', () => this.finish());
    },

    onNext() {
        const order = STEPS;
        const idx = order.indexOf(this.step);
        if (idx < 0 || idx >= order.length - 1) return;
        this._advance(order[idx + 1]);
    },

    onBack() {
        const order = STEPS;
        const idx = order.indexOf(this.step);
        if (idx <= 0) return;
        this._advance(order[idx - 1]);
    },

    _advance(step) {
        // Skip the Chromium install step when the browser engine is already
        // present — works in both directions so back/forward never flash it.
        if (step === 'setup' && !this.state.chromiumNeeded) {
            const idx = STEPS.indexOf(this.step);
            const targetIdx = STEPS.indexOf(step);
            step = targetIdx > idx ? 'connect' : 'welcome';
        }
        this.step = step;
        this.render();
    },

    // --------------------------------------------------------------- setup

    async _runSetup() {
        const btn = document.getElementById('ob-setup-start');
        if (btn) btn.classList.add('hidden');
        const progress = document.getElementById('ob-setup-progress');
        if (progress) progress.classList.remove('hidden');

        try {
            await api('/setup/install', { method: 'POST' });
            let attempts = 0;
            while (attempts < 120) {
                await new Promise(r => setTimeout(r, 2500));
                const status = await api('/setup/status');
                if (!status.installing) {
                    if (status.install_result && !status.install_result.success) {
                        this._setupError(status.install_result.message || 'Install failed');
                        return;
                    }
                    const bar = document.getElementById('ob-setup-bar');
                    if (bar) { bar.style.width = '100%'; bar.style.animation = 'none'; }
                    const msg = document.getElementById('ob-setup-msg');
                    if (msg) msg.textContent = 'Ready.';
                    this.state.chromiumNeeded = false;
                    await new Promise(r => setTimeout(r, 600));
                    this._advance('connect');
                    return;
                }
                attempts++;
            }
            this._setupError('Timed out.');
        } catch (e) {
            this._setupError(e.message || String(e));
        }
    },

    _setupError(message) {
        const msg = document.getElementById('ob-setup-msg');
        const btn = document.getElementById('ob-setup-start');
        if (msg) { msg.textContent = `Setup failed: ${message}`; msg.classList.add('text-danger'); }
        if (btn) { btn.classList.remove('hidden'); btn.textContent = 'Retry'; }
        const progress = document.getElementById('ob-setup-progress');
        if (progress) progress.classList.add('hidden');
    },

    // --------------------------------------------------------------- connect

    async _connectPlatform(platform) {
        this.state._connectingPlatform = platform;
        this.render();
        try {
            await api(`/auth/connect/${platform}`, { method: 'POST' });
        } catch (e) {
            this.state._connectingPlatform = null;
            alert(`Failed to open browser: ${e.message || e}`);
            this.render();
        }
    },

    async _completeAuth(platform) {
        try {
            await api(`/auth/connect/${platform}/complete`, { method: 'POST' });
            let attempts = 0;
            while (attempts < 30) {
                await new Promise(r => setTimeout(r, 1000));
                const status = await api(`/auth/connect/${platform}/status`);
                if (status.status === 'completed') {
                    this.state.connected[platform] = true;
                    this.state._connectingPlatform = null;
                    this.render();
                    return;
                } else if (status.status === 'failed' || status.status === 'cancelled') {
                    this.state._connectingPlatform = null;
                    if (status.status === 'failed' && status.error) alert(status.error);
                    this.render();
                    return;
                }
                attempts++;
            }
            this.state._connectingPlatform = null;
            alert('Verification timed out.');
            this.render();
        } catch (e) {
            this.state._connectingPlatform = null;
            alert(`Error: ${e.message || e}`);
            this.render();
        }
    },

    async _cancelAuth(platform) {
        try { await api(`/auth/connect/${platform}/cancel`, { method: 'POST' }); } catch (e) {}
        this.state._connectingPlatform = null;
        this.render();
    },

    // --------------------------------------------------------------- workspace

    async _pickFolder() {
        if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.pick_folder) return;
        try {
            const picked = await window.pywebview.api.pick_folder('');
            if (picked) {
                this.state.workspaceAbsPath = picked;
                const input = document.getElementById('ob-ws-path');
                if (input) input.value = picked.replace(/^\/Users\/[^/]+/, '~');
            }
        } catch (e) {
            console.warn('Picker failed:', e);
        }
    },

    async _commitWorkspace() {
        const input = document.getElementById('ob-ws-path');
        const errEl = document.getElementById('ob-ws-error');
        const btn = document.getElementById('ob-finish-setup');
        const raw = (this.state.workspaceAbsPath || (input && input.value || '')).trim();
        if (!raw) { this._wsError('Please enter a folder path.'); return; }

        if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }
        if (errEl) errEl.hidden = true;

        try {
            const result = await api('/workspace/setup', {
                method: 'POST',
                body: JSON.stringify({ path: raw, update_app_files: true }),
            });
            if (!result || !result.success) {
                throw new Error((result && result.detail) || 'Workspace setup did not succeed.');
            }
            this.state.workspaceAbsPath = result.workspace || raw;
            this.state.workspacePath = result.workspace || raw;
            // Persist auto-export choice. Non-blocking — bad config is recoverable
            // from the Export tab later, no reason to fail the whole flow.
            try {
                await api('/workspace/auto-export', {
                    method: 'POST',
                    body: JSON.stringify({ enabled: !!this.state.autoExport }),
                });
            } catch (e) {
                console.warn('[onboarding] auto-export persist failed', e);
            }
            window.dispatchEvent(new CustomEvent('workspace:updated'));
            this._advance('done');
        } catch (e) {
            console.error('[onboarding] workspace commit failed', e);
            this._wsError(e.message || String(e));
            if (btn) { btn.disabled = false; btn.textContent = 'Create folder & continue'; }
        }
    },

    _wsError(msg) {
        const el = document.getElementById('ob-ws-error');
        if (el) { el.textContent = msg; el.hidden = false; }
    },

    // --------------------------------------------------------------- finish

    finish() {
        const overlay = document.getElementById('onboarding');
        if (overlay) overlay.remove();
        // Belt-and-suspenders — clear the inline display:none we put on #app
        // in start() *before* bootApp runs, so even if bootApp throws midway
        // through (page render bug, etc.) the user still sees the sidebar
        // and isn't staring at a blank window.
        const appEl = document.getElementById('app');
        if (appEl) appEl.style.display = 'flex';
        try {
            if (window.App && typeof window.App.bootApp === 'function') {
                window.App.bootApp();
            } else {
                throw new Error('App.bootApp is not available — app.js may have failed to load.');
            }
        } catch (e) {
            console.error('[onboarding] bootApp failed', e);
            const content = document.getElementById('content');
            if (content) content.innerHTML = `
                <div class="empty-state">
                    <div class="icon">⚠️</div>
                    <p>The app hit an error starting up: ${(e && e.message) || String(e)}</p>
                    <p><a href="javascript:location.reload()">Reload</a></p>
                </div>
            `;
        }
    },
};

})();
