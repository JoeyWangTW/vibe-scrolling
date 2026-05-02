/**
 * Curated Feed page — shares the Viewer's post layout (same classes, same
 * read-more / carousel / stats markup) and layers curator-specific bits on
 * top: a pack selector, category filter chips, score pill + filter_reason
 * per post, and a collapsed audit log of dropped posts.
 *
 * Helpers (formatText, timeAgo, fmtNum, renderPostBody, toggleReadMore,
 * setupVideoAutoplay, setupCarouselDrag, openLightbox) are borrowed from
 * ViewerPage so the two pages stay pixel-identical.
 */
(function () {
'use strict';

window.CuratedPage = {
    packs: [],                  // {name, kept, dropped_count, ...}
    selectedPack: null,         // pack metadata currently being shown
    filterData: null,           // { filter_metadata, posts } for selected pack
    activeCategory: 'all',

    render() {
        return `
            <div class="fade-in">
                <div class="page-header">
                    <h1 class="page-title">Curated Feed</h1>
                    <select id="curated-pack-selector" onchange="CuratedPage.onPackSelect(this.value)">
                        <option value="">Loading…</option>
                    </select>
                </div>
                <div id="curated-body"></div>
            </div>
        `;
    },

    async init() {
        try {
            const data = await api('/curated/packs');
            this.packs = data.packs || [];
            this.renderPackSelector();
            if (this.packs.length > 0) {
                await this.loadPack(this.packs[0].name);
            } else {
                this.renderEmpty(data.is_setup);
            }
        } catch (e) {
            document.getElementById('curated-body').innerHTML =
                `<div class="card text-danger">Failed to load curated packs: ${esc(e.message)}</div>`;
        }
    },

    renderPackSelector() {
        const sel = document.getElementById('curated-pack-selector');
        if (!sel) return;
        if (this.packs.length === 0) {
            sel.innerHTML = '<option value="">No curated packs</option>';
            sel.disabled = true;
            return;
        }
        sel.innerHTML = this.packs.map(p => {
            const when = p.filtered_at ? new Date(p.filtered_at).toLocaleString() : '';
            return `<option value="${escAttr(p.name)}">${esc(p.name)} — ${p.kept} kept${when ? ' · ' + when : ''}</option>`;
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
                        Pick an export folder in <strong>Settings</strong> first, then come back after you've
                        exported and curated a pack.
                    </p>
                    <a href="#settings" class="btn btn-primary">Go to Settings</a>
                </div>
            `;
            return;
        }
        body.innerHTML = `
            <div class="card">
                <h3 class="font-semibold text-subtitle mb-2">No curated packs yet</h3>
                <p class="text-secondary text-sm mb-3">
                    Export a pack, then run your agent against it — when it writes
                    <code>posts.filtered.json</code> in the pack folder, it'll show up here.
                </p>
                <div class="flex gap-2">
                    <a href="#export" class="btn btn-secondary">Export</a>
                    <a href="#curate" class="btn btn-primary">AI Curation</a>
                </div>
            </div>
        `;
    },

    async onPackSelect(name) {
        if (!name) return;
        await this.loadPack(name);
    },

    async loadPack(name) {
        const body = document.getElementById('curated-body');
        body.innerHTML = '<div class="empty-state"><p class="text-secondary">Loading pack…</p></div>';
        try {
            this.filterData = await api(`/curated/packs/${encodeURIComponent(name)}`);
            this.selectedPack = this.packs.find(p => p.name === name) || { name };
            this.activeCategory = 'all';
            this.renderFeed();
        } catch (e) {
            body.innerHTML = `<div class="card text-danger">Failed to load pack: ${esc(e.message)}</div>`;
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

        body.innerHTML = `
            <div class="curated-meta">
                <div class="curated-meta-item"><strong>${posts.length}</strong> kept</div>
                ${meta.dropped_count ? `<div class="curated-meta-item text-secondary">${meta.dropped_count} dropped</div>` : ''}
                ${meta.median_score != null ? `<div class="curated-meta-item text-secondary">median score ${meta.median_score}</div>` : ''}
                ${meta.drop_rule ? `<div class="curated-meta-item text-secondary"><code>${esc(meta.drop_rule)}</code></div>` : ''}
                ${meta.filtered_at ? `<div class="curated-meta-item text-secondary">${new Date(meta.filtered_at).toLocaleString()}</div>` : ''}
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
                        <thead><tr><th>Score</th><th>Category</th><th>ID</th><th>Reason</th></tr></thead>
                        <tbody>
                            ${dropped.map(d => `
                                <tr>
                                    <td>${d.score ?? '—'}</td>
                                    <td><span class="badge badge-${escAttr(d.category || 'neutral')}">${esc(d.category || '')}</span></td>
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

        const packName = this.selectedPack ? this.selectedPack.name : '';
        const mediaResolver = (path) => packName
            ? `/api/curated/packs/${encodeURIComponent(packName)}/${path}`
            : path;

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
