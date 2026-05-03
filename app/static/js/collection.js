/**
 * Collection Page — trigger and monitor feed collection
 */
window.CollectPage = {
    _pollTimer: null,

    render() {
        return `
            <div class="fade-in">
                <h1 class="page-title">Collect</h1>
                <div class="card" id="collect-controls">
                    <div class="text-secondary mb-4">Loading platforms...</div>
                </div>
                <div id="collection-status"></div>
                <h2 class="section-title">Collection History</h2>
                <div id="collection-history">
                    <div class="text-secondary">Loading...</div>
                </div>
            </div>
        `;
    },

    async init() {
        await Promise.all([this.loadControls(), this.loadHistory()]);
        this.startPolling();
    },

    async loadControls() {
        try {
            const [authStatus, config] = await Promise.all([
                api('/auth/status'),
                api('/config'),
            ]);

            const platforms = Object.entries(authStatus);
            const platformsConfig = config.platforms || {};

            let html = '<div class="mb-4">';
            for (const [platform, info] of platforms) {
                const connected = info.connected;
                const pconfig = platformsConfig[platform] || {};
                const maxPosts = pconfig.max_posts || 50;

                html += `
                    <div class="collect-row">
                        <input type="checkbox" id="chk-${platform}" ${connected ? 'checked' : 'disabled'}
                            class="checkbox">
                        <div class="flex-1">
                            <span class="font-semibold">${platform.charAt(0).toUpperCase() + platform.slice(1)}</span>
                            ${!connected ? '<span class="text-sm text-secondary ml-2">Not connected</span>' : ''}
                        </div>
                        <div class="flex items-center gap-2">
                            <label style="display:inline;margin-right:4px">Max posts:</label>
                            <input type="number" id="max-${platform}" value="${maxPosts}" min="1" max="500"
                                class="max-posts-input" ${!connected ? 'disabled' : ''}>
                        </div>
                    </div>
                `;
            }
            html += '</div>';
            html += '<button class="btn btn-primary" id="start-btn" onclick="CollectPage.startCollection()">Start Collection</button>';

            document.getElementById('collect-controls').innerHTML = html;
        } catch (e) {
            document.getElementById('collect-controls').innerHTML =
                `<div class="text-danger">Failed to load: ${e.message}</div>`;
        }
    },

    async startCollection() {
        const platforms = ['x', 'threads', 'instagram', 'youtube', 'linkedin'];
        const selected = platforms.filter(p => {
            const chk = document.getElementById(`chk-${p}`);
            return chk && chk.checked && !chk.disabled;
        });

        if (selected.length === 0) {
            alert('Select at least one connected platform');
            return;
        }

        const btn = document.getElementById('start-btn');
        btn.disabled = true;
        btn.textContent = 'Starting...';

        try {
            // Generate a shared job_id so all platforms in this session are grouped
            const firstResult = await api(`/collection/start/${selected[0]}?${(() => {
                const maxInput = document.getElementById(`max-${selected[0]}`);
                const maxPosts = maxInput ? parseInt(maxInput.value) : null;
                return maxPosts ? `max_posts=${maxPosts}` : '';
            })()}`, { method: 'POST' });
            const jobId = firstResult.job_id;

            // Start remaining platforms with the same job_id
            for (const platform of selected.slice(1)) {
                const maxInput = document.getElementById(`max-${platform}`);
                const maxPosts = maxInput ? parseInt(maxInput.value) : null;
                const params = [maxPosts ? `max_posts=${maxPosts}` : '', jobId ? `job_id=${jobId}` : ''].filter(Boolean).join('&');

                await api(`/collection/start/${platform}?${params}`, {
                    method: 'POST',
                });
            }
            this.startPolling();
        } catch (e) {
            alert('Failed to start: ' + e.message);
        }

        btn.disabled = false;
        btn.textContent = 'Start Collection';
    },

    startPolling() {
        this.stopPolling();
        this._pollTimer = setInterval(() => this.pollStatus(), 3000);
        this.pollStatus();
    },

    stopPolling() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async pollStatus() {
        try {
            const status = await api('/collection/status');
            const container = document.getElementById('collection-status');

            const entries = Object.entries(status);
            if (entries.length === 0) {
                container.innerHTML = '';
                this.stopPolling();
                return;
            }

            const statusClassMap = {
                starting: 'warning',
                running: 'warning',
                completed: 'success',
                failed: 'danger',
                cancelled: 'muted',
            };

            let hasRunning = false;
            let html = '';
            for (const [platform, task] of entries) {
                const isRunning = ['starting', 'running'].includes(task.status);
                if (isRunning) hasRunning = true;
                const cls = statusClassMap[task.status] || 'muted';

                html += `
                    <div class="card card-status card-status-${cls}">
                        <div class="card-header-row">
                            <div>
                                <span class="font-semibold">${platform.charAt(0).toUpperCase() + platform.slice(1)}</span>
                                <span class="text-${cls} ml-2 text-sm">
                                    ${isRunning ? '<span class="dot dot-warning dot-pulse"></span>' : ''}
                                    ${task.status}
                                </span>
                            </div>
                            ${isRunning ? `<button class="btn btn-secondary btn-sm"
                                onclick="CollectPage.stopTask('${task.task_id}')">Stop</button>` : ''}
                        </div>
                        ${task.error ? `<div class="text-danger text-sm mt-2">${task.error}</div>` : ''}
                        ${task.summary ? `
                            <div class="text-sm text-secondary mt-2">
                                ${task.summary.unique_posts || task.summary.total_posts || '?'} posts
                                | ${task.summary.media_downloaded || 0} media
                                | ${task.summary.run_time_seconds ? task.summary.run_time_seconds.toFixed(1) + 's' : ''}
                            </div>
                        ` : ''}
                    </div>
                `;
            }

            container.innerHTML = html;

            if (!hasRunning) {
                this.stopPolling();
                this.loadHistory();
            }
        } catch (e) {
            // Silently fail polling
        }
    },

    async stopTask(taskId) {
        try {
            await api(`/collection/stop/${taskId}`, { method: 'POST' });
            this.pollStatus();
        } catch (e) {
            alert('Failed to stop: ' + e.message);
        }
    },

    toggleTree(id) {
        const btn = document.querySelector(`[data-tree="${id}"]`);
        const children = document.getElementById(id);
        if (!btn || !children) return;
        btn.classList.toggle('open');
        children.classList.toggle('open');
    },

    async loadHistory() {
        try {
            const data = await api('/collection/history');
            const container = document.getElementById('collection-history');
            const dates = data.dates || [];

            if (dates.length === 0) {
                container.innerHTML = '<div class="text-secondary">No collection runs yet</div>';
                return;
            }

            let html = '';
            for (let di = 0; di < Math.min(dates.length, 10); di++) {
                const dateGroup = dates[di];
                const dateId = `history-date-${di}`;
                const totalPosts = dateGroup.jobs.reduce((sum, j) =>
                    sum + j.platforms.reduce((s, p) => s + (p.post_count || 0), 0), 0);
                const isFirst = di === 0;

                html += `<div class="tree-node">
                    <button class="tree-toggle" data-tree="${dateId}" onclick="CollectPage.toggleTree('${dateId}')">
                        <span class="chevron">&#9654;</span>
                        <span class="tree-label">${dateGroup.date}</span>
                        <span class="tree-meta">${dateGroup.jobs.length} job${dateGroup.jobs.length !== 1 ? 's' : ''} &middot; ${totalPosts} posts</span>
                    </button>
                    <div class="tree-children" id="${dateId}">`;

                for (let ji = 0; ji < dateGroup.jobs.length; ji++) {
                    const job = dateGroup.jobs[ji];
                    const jobId = `${dateId}-job-${ji}`;
                    const jobPosts = job.platforms.reduce((s, p) => s + (p.post_count || 0), 0);
                    const jobTime = job.started_at ? new Date(job.started_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';

                    html += `<div class="tree-node">
                        <button class="tree-toggle" data-tree="${jobId}" onclick="CollectPage.toggleTree('${jobId}')">
                            <span class="chevron">&#9654;</span>
                            <span class="tree-label">Job ${job.job_id}${jobTime ? ` — ${jobTime}` : ''}</span>
                            <span class="tree-meta">${job.platforms.length} platform${job.platforms.length !== 1 ? 's' : ''} &middot; ${jobPosts} posts</span>
                        </button>
                        <div class="tree-children" id="${jobId}">`;

                    for (const p of job.platforms) {
                        const summary = p.summary || p.run_log || {};
                        const posts = summary.unique_posts || summary.total_posts || p.post_count || '?';
                        const time = summary.run_time_seconds ? `${summary.run_time_seconds.toFixed(0)}s` : '';

                        html += `<div class="tree-leaf">
                            <span class="badge badge-${p.platform}">${p.platform}</span>
                            <span class="flex-1">${posts} posts</span>
                            ${time ? `<span class="text-muted text-xs">${time}</span>` : ''}
                        </div>`;
                    }

                    html += `</div></div>`;
                }

                html += `</div></div>`;
            }
            container.innerHTML = html;
        } catch (e) {
            document.getElementById('collection-history').innerHTML =
                `<div class="text-danger">Failed to load history</div>`;
        }
    },
};
