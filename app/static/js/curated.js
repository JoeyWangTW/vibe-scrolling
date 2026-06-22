/**
 * Curated Feed page — shares the Viewer's post layout (same classes, same
 * read-more / carousel / stats markup) and layers curator-specific bits on
 * top: a job selector, category filter chips, score pill + filter_reason
 * per post, and a collapsed audit log of dropped posts.
 *
 * Helpers (formatText, timeAgo, fmtNum, renderPostBody, toggleReadMore,
 * setupVideoAutoplay, setupCarouselDrag, openLightbox) are borrowed from
 * ViewerPage so the two pages stay pixel-identical.
 */
(function () {
'use strict';

window.CuratedPage = {
    jobs: [],                   // [{id, date, job_id, kept, ...}]
    selectedJob: null,          // currently shown job metadata
    filterData: null,           // {filter_metadata, posts} for selected job
    activeCategory: 'all',

    render() {
        return `
            <div class="fade-in">
                <div class="page-header">
                    <h1 class="page-title">Curated Feed</h1>
                    <select id="curated-job-selector" onchange="CuratedPage.onJobSelect(this.value)">
                        <option value="">Loading…</option>
                    </select>
                </div>
                <div id="curated-body"></div>
            </div>
        `;
    },

    async init() {
        try {
            const data = await api('/curated/jobs');
            this.jobs = data.jobs || [];
            this.renderJobSelector();
            if (this.jobs.length > 0) {
                await this.loadJob(this.jobs[0].id);
            } else {
                this.renderEmpty(data.is_setup);
            }
        } catch (e) {
            document.getElementById('curated-body').innerHTML =
                `<div class="card text-danger">Failed to load curated jobs: ${esc(e.message)}</div>`;
        }
    },

    renderJobSelector() {
        const sel = document.getElementById('curated-job-selector');
        if (!sel) return;
        if (this.jobs.length === 0) {
            sel.innerHTML = '<option value="">No curated jobs</option>';
            sel.disabled = true;
            return;
        }
        sel.innerHTML = this.jobs.map(j => {
            const when = j.filtered_at ? new Date(j.filtered_at).toLocaleString() : '';
            const platforms = (j.platforms || []).join(' · ');
            const label = `${j.date} · ${j.job_id}${platforms ? ' (' + platforms + ')' : ''} — ${j.kept} kept${when ? ' · ' + when : ''}`;
            return `<option value="${escAttr(j.id)}">${esc(label)}</option>`;
        }).join('');
        sel.disabled = false;
    },

    renderEmpty(isSetup) {
        const body = document.getElementById('curated-body');
        if (!body) return;
        if (!isSetup) {
            body.innerHTML = `
                <div class="card">
                    <h3 class="font-semibold text-subtitle mb-2">Workspace not set up</h3>
                    <p class="text-secondary text-sm mb-3">
                        Pick a workspace folder in <strong>Settings</strong> first. Once a collection runs,
                        an agent can curate it in place and the results appear here.
                    </p>
                    <a href="#settings" class="btn btn-primary">Go to Settings</a>
                </div>
            `;
            return;
        }
        body.innerHTML = `
            <div class="card">
                <h3 class="font-semibold text-subtitle mb-2">No curated jobs yet</h3>
                <p class="text-secondary text-sm mb-3">
                    Run a collection in the <a href="#collect">Collect</a> tab, then run an agent against
                    your workspace — when it writes <code>posts.filtered.json</code> at
                    <code>data/&lt;date&gt;/&lt;job_id&gt;/</code>, the curated job will show up here.
                </p>
                <div class="flex gap-2">
                    <a href="#data" class="btn btn-secondary">Manage data</a>
                    <a href="#curate" class="btn btn-primary">Curate with AI</a>
                </div>
            </div>
        `;
    },

    async onJobSelect(id) {
        if (!id) return;
        await this.loadJob(id);
    },

    async loadJob(id) {
        const body = document.getElementById('curated-body');
        body.innerHTML = '<div class="empty-state"><p class="text-secondary">Loading curated job…</p></div>';
        try {
            // id is "YYYY-MM-DD/job_HHMMSS" — split for the URL
            const [date, jobDir] = id.split('/');
            this.filterData = await api(`/curated/jobs/${encodeURIComponent(date)}/${encodeURIComponent(jobDir)}`);
            this.selectedJob = this.jobs.find(j => j.id === id) || { id };
            this.activeCategory = 'all';
            this.renderFeed();
        } catch (e) {
            body.innerHTML = `<div class="card text-danger">Failed to load curated job: ${esc(e.message)}</div>`;
        }
    },

    renderFeed() {
        const body = document.getElementById('curated-body');
        const meta = (this.filterData && this.filterData.filter_metadata) || {};
        const posts = (this.filterData && this.filterData.posts) || [];
        const dropped = meta.dropped || [];
        const cc = meta.category_counts || {};

        const chip = (cat, label, count) => `
            <button class="btn btn-ghost btn-pill btn-sm ${this.activeCategory === cat ? 'active' : ''}"
                    data-chip="${cat}"
                    onclick="CuratedPage.setCategory('${cat}')">
                ${label}${typeof count === 'number' ? ` <span class="chip-count">${count}</span>` : ''}
            </button>
        `;

        const FIRST_CHUNK = 30;
        const firstHtml = posts.length === 0
            ? '<div class="empty-state"><p class="text-secondary">No kept posts.</p></div>'
            : posts.slice(0, FIRST_CHUNK).map(p => this.renderPost(p)).join('');

        // Per-platform kept counts as a small ribbon under the header.
        const pc = meta.platform_counts || {};
        const platformRibbon = Object.keys(pc).length === 0 ? '' : `
            <div class="curated-meta-item text-secondary">
                ${Object.entries(pc).map(([p, n]) =>
                    `<span class="badge badge-${esc(p)}">${esc(p)}</span> ${n}`
                ).join(' &nbsp; ')}
            </div>`;

        body.innerHTML = `
            <div class="curated-meta">
                <div class="curated-meta-item"><strong>${posts.length}</strong> kept</div>
                ${meta.dropped_count ? `<div class="curated-meta-item text-secondary">${meta.dropped_count} dropped</div>` : ''}
                ${meta.median_score != null ? `<div class="curated-meta-item text-secondary">median score ${meta.median_score}</div>` : ''}
                ${meta.drop_rule ? `<div class="curated-meta-item text-secondary"><code>${esc(meta.drop_rule)}</code></div>` : ''}
                ${meta.filtered_at ? `<div class="curated-meta-item text-secondary">${new Date(meta.filtered_at).toLocaleString()}</div>` : ''}
                ${platformRibbon}
            </div>

            <div class="sort-bar" id="curated-chips">
                ${chip('all', 'All', posts.length)}
                ${chip('goal', 'Goal', cc.goal)}
                ${chip('joy', 'Joy', cc.joy)}
                ${chip('adjacent', 'Adjacent', cc.adjacent)}
                ${chip('neutral', 'Neutral', cc.neutral)}
            </div>

            <div id="curated-feed">${firstHtml}</div>

            ${dropped.length > 0 ? `
                <details class="dropped-log">
                    <summary>${dropped.length} dropped posts (audit log)</summary>
                    <table class="dropped-table">
                        <thead><tr><th>Score</th><th>Category</th><th>Platform</th><th>ID</th><th>Reason</th></tr></thead>
                        <tbody>
                            ${dropped.map(d => `
                                <tr>
                                    <td>${d.score ?? '—'}</td>
                                    <td><span class="badge badge-${escAttr(d.category || 'neutral')}">${esc(d.category || '')}</span></td>
                                    <td>${d.platform ? `<span class="badge badge-${escAttr(d.platform)}">${esc(d.platform)}</span>` : ''}</td>
                                    <td><code>${esc(d.id || '')}</code></td>
                                    <td>${esc(d.filter_reason || '')}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </details>
            ` : ''}
        `;

        // Bind observers + filter for the first chunk so it's interactive
        // immediately. If there are more posts, stream them in on the next
        // frame and re-bind observers afterward (the bound-once guards inside
        // PostRenderer.* keep us from double-attaching to the first chunk).
        if (this._videoObserver) this._videoObserver.disconnect();
        this._videoObserver = PostRenderer.setupVideoAutoplay('curated-feed');
        PostRenderer.setupCarouselDrag();
        PostRenderer.setupYoutubeHover('curated-feed');
        this.applyCategoryFilter();

        if (posts.length > FIRST_CHUNK) {
            const renderToken = (this._renderToken = (this._renderToken || 0) + 1);
            requestAnimationFrame(() => {
                if (renderToken !== this._renderToken) return;
                const feed = document.getElementById('curated-feed');
                if (!feed) return;
                const restHtml = posts.slice(FIRST_CHUNK).map(p => this.renderPost(p)).join('');
                feed.insertAdjacentHTML('beforeend', restHtml);
                if (this._videoObserver) this._videoObserver.disconnect();
                this._videoObserver = PostRenderer.setupVideoAutoplay('curated-feed');
                PostRenderer.setupCarouselDrag();
                PostRenderer.setupYoutubeHover('curated-feed');
                this.applyCategoryFilter();
            });
        }
    },

    toggleReason(postId, btn) {
        const el = document.getElementById(`reason-${postId}`);
        if (!el) return;
        const isHidden = el.classList.toggle('hidden');
        if (btn) btn.textContent = isHidden ? 'why' : 'hide';
    },

    setCategory(cat) {
        this.activeCategory = cat;
        const chips = document.querySelectorAll('#curated-chips [data-chip]');
        chips.forEach(btn => btn.classList.toggle('active', btn.dataset.chip === cat));
        this.applyCategoryFilter();
    },

    applyCategoryFilter() {
        const feed = document.getElementById('curated-feed');
        if (!feed) return;
        const cat = this.activeCategory;
        feed.querySelectorAll('.post').forEach(el => {
            el.style.display = (cat === 'all' || el.dataset.category === cat) ? '' : 'none';
        });
    },

    // ---- renderPost: uses Viewer's class names/structure so the two pages
    // share one visual language. Curator-specific bits (score pill, category
    // badge, filter_reason) are layered on top.
    renderPost(post) {
        const postId = String(post.id || Math.random().toString(36).slice(2));

        // Curator-specific extras layered onto the shared PostRenderer.
        const scoreHtml = typeof post.score === 'number'
            ? `<span class="score-pill score-${scoreBucket(post.score)}">${post.score}</span>` : '';
        const categoryHtml = post.category
            ? `<span class="badge badge-${esc(post.category)}">${esc(post.category)}</span>` : '';
        const reasonToggle = post.filter_reason
            ? `<button class="reason-toggle" onclick="CuratedPage.toggleReason('${postId}', this)">why</button>` : '';
        const reasonBody = post.filter_reason
            ? `<div class="filter-reason hidden" id="reason-${postId}">${esc(post.filter_reason)}</div>` : '';

        // local_media_paths in posts.filtered.json are already relative to the
        // active data dir (e.g. "2026-05-02/job_020746/linkedin/media/foo.jpg"),
        // so we serve them via the same /feed_data/ route the Raw Feed viewer uses.
        const mediaResolver = (path) =>
            path.startsWith('feed_data/') ? '/' + path : '/feed_data/' + path;

        return PostRenderer.renderPost(post, {
            postId,
            showPlatformBadge: true,
            mediaResolver,
            headerExtras: `${categoryHtml}${scoreHtml}`,
            statsExtras: reasonToggle,
            afterStats: reasonBody,
            dataAttrs: { category: post.category || 'neutral' },
        });
    },
};

// ---- helpers ----

function scoreBucket(n) {
    if (n >= 80) return 'high';
    if (n >= 60) return 'mid';
    if (n >= 40) return 'low';
    return 'verylow';
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escAttr(s) { return esc(s); }

})();
