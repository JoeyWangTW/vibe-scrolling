/**
 * Platforms Page — manage platform connections
 */
window.PlatformsPage = {
    _pollTimers: {},

    render() {
        return `
            <div class="fade-in">
                <h1 class="page-title">Platforms</h1>
                <p class="page-subtitle">Connect your social media accounts to start collecting feeds.</p>
                <div class="card-grid" id="platform-cards">
                    <div class="card flex-center text-secondary" style="padding:40px">Loading...</div>

                </div>
            </div>
        `;
    },

    async init() {
        await this.loadStatus();
    },

    async loadStatus() {
        try {
            const status = await api('/auth/status');
            this.renderCards(status);
        } catch (e) {
            document.getElementById('platform-cards').innerHTML =
                '<div class="card text-danger">Failed to load platform status</div>';
        }
    },

    renderCards(status) {
        const cards = Object.entries(status).map(([platform, info]) => {
            const name = PlatformIcons.name(platform);
            const icon = PlatformIcons.svg(platform, { size: 28 }) || '?';
            const connected = info.connected;

            return `
                <div class="card platform-${platform}" id="card-${platform}">
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
                    <div id="action-${platform}">
                        ${connected
                            ? `<button class="btn btn-danger" onclick="PlatformsPage.disconnect('${platform}')">Disconnect</button>`
                            : `<button class="btn btn-primary" onclick="PlatformsPage.connect('${platform}')">Connect</button>`
                        }
                    </div>
                </div>
            `;
        }).join('');

        document.getElementById('platform-cards').innerHTML = cards;
    },

    async connect(platform) {
        const actionDiv = document.getElementById(`action-${platform}`);
        actionDiv.innerHTML = `
            <div class="text-sm text-secondary">
                <span class="dot dot-warning dot-pulse"></span>
                Opening browser...
            </div>
        `;

        try {
            const result = await api(`/auth/connect/${platform}`, { method: 'POST' });

            actionDiv.innerHTML = `
                <div class="text-sm text-warning mb-3">
                    <span class="dot dot-warning dot-pulse"></span>
                    A browser window should be open — log in there
                </div>
                <button class="btn btn-primary" onclick="PlatformsPage.completeAuth('${platform}')">
                    Done — I've logged in
                </button>
                <button class="btn btn-secondary ml-2" onclick="PlatformsPage.cancelAuth('${platform}')">
                    Cancel
                </button>
            `;
        } catch (e) {
            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Failed to open browser: ${e.message}</div>
                <button class="btn btn-primary" onclick="PlatformsPage.connect('${platform}')">Retry</button>
            `;
        }
    },

    async completeAuth(platform) {
        const actionDiv = document.getElementById(`action-${platform}`);
        actionDiv.innerHTML = `
            <div class="text-sm text-secondary">
                <span class="dot dot-warning dot-pulse"></span>
                Saving session and verifying login...
            </div>
        `;

        try {
            // Signal completion — the backend saves, closes browser, then verifies
            await api(`/auth/connect/${platform}/complete`, { method: 'POST' });

            // Poll for final result since verification takes a few seconds
            let attempts = 0;
            while (attempts < 30) {
                await new Promise(r => setTimeout(r, 1000));
                const status = await api(`/auth/connect/${platform}/status`);

                if (status.status === 'completed') {
                    await this.loadStatus();
                    return;
                } else if (status.status === 'failed') {
                    actionDiv.innerHTML = `
                        <div class="text-danger mb-2">${status.error || 'Login failed'}</div>
                        <button class="btn btn-primary" onclick="PlatformsPage.connect('${platform}')">Try Again</button>
                    `;
                    return;
                } else if (status.status === 'cancelled') {
                    await this.loadStatus();
                    return;
                }
                attempts++;
            }

            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Verification timed out</div>
                <button class="btn btn-primary" onclick="PlatformsPage.connect('${platform}')">Try Again</button>
            `;
        } catch (e) {
            actionDiv.innerHTML = `
                <div class="text-danger mb-2">Error: ${e.message}</div>
                <button class="btn btn-primary" onclick="PlatformsPage.connect('${platform}')">Try Again</button>
            `;
        }
    },

    async cancelAuth(platform) {
        const actionDiv = document.getElementById(`action-${platform}`);
        actionDiv.innerHTML = '<button class="btn btn-secondary" disabled>Cancelling...</button>';

        try {
            await api(`/auth/connect/${platform}/cancel`, { method: 'POST' });
        } catch (e) {
            // Ignore errors — we're cancelling anyway
        }

        await this.loadStatus();
    },

    async disconnect(platform) {
        if (!confirm(`Disconnect ${platform}? You'll need to log in again to collect.`)) return;

        try {
            await api(`/auth/disconnect/${platform}`, { method: 'POST' });
            await this.loadStatus();
        } catch (e) {
            alert('Failed to disconnect: ' + e.message);
        }
    },
};
