/**
 * Data tab — manage collected feeds in place.
 *
 * Lists every collection in the active data directory (date → job → platform
 * tree), with per-row "Open folder" and "Delete" actions. The data directory
 * IS the workspace's `data/` folder once set up; collections write directly
 * there, so there's no separate export step for the common case.
 *
 * The "Open folder" / "Delete" mutations go through /api/data/{reveal,path}.
 */
window.DataPage = {
    runs: [],
    dates: [],
    workspace: null,

    render() {
        return `
            <div class="fade-in">
                <h1 class="page-title">Data</h1>
                <p class="page-subtitle">
                    Every collection lives here. Open a folder to inspect raw posts and media,
                    or delete runs you no longer need.
                </p>

                <div class="card" id="data-location-card">
                    <div class="text-secondary">Loading…</div>
                </div>

                <h2 class="section-title">Collections</h2>
                <div id="data-runs">
                    <div class="text-secondary">Loading…</div>
                </div>
            </div>
        `;
    },

    async init() {
        await Promise.all([this.refreshLocation(), this.loadRuns()]);
    },

    async refreshLocation() {
        const card = document.getElementById('data-location-card');
        if (!card) return;
        try {
            this.workspace = await api('/workspace');
        } catch (e) {
            this.workspace = { is_setup: false };
        }
        const ws = this.workspace || {};
        const isSetup = !!ws.is_setup;
        const dataPath = ws.data_dir || (ws.path ? `${ws.path}/data` : null);
        const pretty = (dataPath || '').replace(/^\/Users\/[^/]+/, '~');

        card.innerHTML = `
            <div class="curation-dir-row">
                <div>
                    <div class="curation-dir-label">Data location</div>
                    <div class="curation-dir-path" title="${dataPath || ''}">${pretty || '(workspace not set up — using app data dir)'}</div>
                </div>
                <button class="btn btn-secondary btn-sm" onclick="DataPage.openRoot()">Open folder</button>
            </div>
            ${!isSetup ? `
                <div class="text-secondary text-sm mt-2">
                    Pick a workspace in <a href="#settings">Settings</a> so collections land in a folder you control.
                </div>
            ` : ''}
        `;
    },

    async loadRuns() {
        const container = document.getElementById('data-runs');
        if (!container) return;
        try {
            const data = await api('/data/runs');
            this.dates = data.dates || [];
            this.runs = (data.runs || []).filter(r => r.has_posts);
        } catch (e) {
            container.innerHTML = `<div class="text-danger">Failed to load runs: ${e.message}</div>`;
            return;
        }
        this.renderRuns();
    },

    renderRuns() {
        const container = document.getElementById('data-runs');
        if (!container) return;
        if (!this.dates.length && !this.runs.length) {
            container.innerHTML = '<div class="text-secondary">No collections yet — run something in the <a href="#collect">Collect</a> tab.</div>';
            return;
        }

        let html = '';
        for (let di = 0; di < this.dates.length; di++) {
            const dateGroup = this.dates[di];
            const dateId = `data-date-${di}`;
            const totalPosts = dateGroup.jobs.reduce((sum, j) =>
                sum + j.platforms.filter(p => p.has_posts).reduce((s, p) => s + (p.post_count || 0), 0), 0);

            html += `<div class="tree-node">
                <div class="tree-row">
                    <button class="tree-toggle" data-tree="${dateId}" onclick="DataPage.toggleTree('${dateId}')">
                        <span class="chevron">&#9654;</span>
                        <span class="tree-label">${dateGroup.date}</span>
                        <span class="tree-meta">${dateGroup.jobs.length} job${dateGroup.jobs.length !== 1 ? 's' : ''} · ${totalPosts} posts</span>
                    </button>
                    <div class="tree-row-actions">
                        <button class="btn btn-secondary btn-sm" onclick="DataPage.openPath('${dateGroup.date}')">Open</button>
                        <button class="btn btn-danger btn-sm" onclick="DataPage.deletePath('${dateGroup.date}', 'date ${dateGroup.date}')">Delete</button>
                    </div>
                </div>
                <div class="tree-children" id="${dateId}">`;

            for (let ji = 0; ji < dateGroup.jobs.length; ji++) {
                const job = dateGroup.jobs[ji];
                const jobId = `${dateId}-job-${ji}`;
                const jobPath = `${dateGroup.date}/job_${job.job_id}`;
                const jobPlatforms = job.platforms.filter(p => p.has_posts);
                const jobPosts = jobPlatforms.reduce((s, p) => s + (p.post_count || 0), 0);
                const jobTime = job.started_at ? new Date(job.started_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';

                html += `<div class="tree-node">
                    <div class="tree-row">
                        <button class="tree-toggle" data-tree="${jobId}" onclick="DataPage.toggleTree('${jobId}')">
                            <span class="chevron">&#9654;</span>
                            <span class="tree-label">Job ${job.job_id}${jobTime ? ` — ${jobTime}` : ''}</span>
                            <span class="tree-meta">${jobPlatforms.length} platform${jobPlatforms.length !== 1 ? 's' : ''} · ${jobPosts} posts</span>
                        </button>
                        <div class="tree-row-actions">
                            <button class="btn btn-secondary btn-sm" onclick="DataPage.openPath('${jobPath}')">Open</button>
                            <button class="btn btn-danger btn-sm" onclick="DataPage.deletePath('${jobPath}', 'job ${job.job_id}')">Delete</button>
                        </div>
                    </div>
                    <div class="tree-children" id="${jobId}">`;

                for (const run of job.platforms) {
                    const runPath = run.run_id;
                    const posts = run.post_count || 0;
                    const dur = run.run_log?.run_time_seconds ? `${Math.round(run.run_log.run_time_seconds)}s` : '';
                    html += `<div class="tree-leaf">
                        <span class="badge badge-${run.platform}">${run.platform}</span>
                        <span class="flex-1">${posts} posts</span>
                        ${dur ? `<span class="text-muted text-xs">${dur}</span>` : ''}
                        <div class="tree-row-actions">
                            <button class="btn btn-secondary btn-sm" onclick="DataPage.openPath('${runPath}')">Open</button>
                            <button class="btn btn-danger btn-sm" onclick="DataPage.deletePath('${runPath}', '${run.platform} run')">Delete</button>
                        </div>
                    </div>`;
                }
                html += `</div></div>`;
            }
            html += `</div></div>`;
        }
        container.innerHTML = html;
    },

    toggleTree(id) {
        const btn = document.querySelector(`[data-tree="${id}"]`);
        const children = document.getElementById(id);
        if (!btn || !children) return;
        btn.classList.toggle('open');
        children.classList.toggle('open');
    },

    async openRoot() {
        try {
            await api('/data/reveal-root', { method: 'POST' });
        } catch (e) {
            alert('Could not open folder: ' + e.message);
        }
    },

    async openPath(relPath) {
        try {
            await api(`/data/reveal/${encodeURI(relPath)}`, { method: 'POST' });
        } catch (e) {
            alert('Could not open folder: ' + e.message);
        }
    },

    async deletePath(relPath, label) {
        if (!confirm(`Delete ${label}? This removes posts.json, media, and raw responses. Cannot be undone.`)) return;
        try {
            await api(`/data/path/${encodeURI(relPath)}`, { method: 'DELETE' });
            await this.loadRuns();
        } catch (e) {
            alert('Delete failed: ' + e.message);
        }
    },
};
