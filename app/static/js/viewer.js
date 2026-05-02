/**
 * Feed (raw) page — lists collected posts in timeline order.
 *
 * All post rendering, media, carousel drag, and video autoplay come from
 * PostRenderer — see app/static/js/post-renderer.js. This file owns only
 * the Feed-specific bits: run selector, platform tabs, sort controls,
 * replies toggle.
 */
window.ViewerPage = {
    allPosts: [],
    currentSort: 'time',
    currentPlatform: 'all',
    availableRuns: [],

    render() {
        return `
            <div class="fade-in">
                <div class="page-header">
                    <h1 class="page-title">Raw Feed</h1>
                    <select id="run-selector" onchange="ViewerPage.onRunSelect(this.value)">
                        <option value="latest">Latest runs</option>
                    </select>
                </div>
                <div id="viewer-meta" class="viewer-meta"></div>
                <div class="tabs" id="viewer-platform-tabs"></div>
                <div class="sort-bar" id="viewer-sort-bar">
                    <button class="btn btn-ghost btn-pill btn-sm active" data-sort="time" onclick="ViewerPage.setSort('time',this)">Latest</button>
                    <button class="btn btn-ghost btn-pill btn-sm" data-sort="likes" onclick="ViewerPage.setSort('likes',this)">Most Liked</button>
                    <button class="btn btn-ghost btn-pill btn-sm" data-sort="reposts" onclick="ViewerPage.setSort('reposts',this)">Most Reposted</button>
                    <button class="btn btn-ghost btn-pill btn-sm" data-sort="replies" onclick="ViewerPage.setSort('replies',this)">Most Replies</button>
                </div>
                <div id="viewer-feed"></div>
            </div>
        `;
    },

    async init() {
        await this.loadLatest();
    },

    async loadLatest() {
        try {
            // Fetch latest posts AND the run-list in parallel — they're served
            // by independent endpoints, no reason to await sequentially.
            const [data, runsData] = await Promise.all([
                api('/data/runs/latest'),
                api('/data/runs'),
            ]);
            this.allPosts = [];

            for (const [platform, runData] of Object.entries(data.runs || {})) {
                const posts = (runData.posts || []).map(p => ({
                    ...p,
                    platform: p.platform || platform,
                    reposts: p.reposts ?? p.retweets ?? 0,
                    is_repost: p.is_repost ?? p.is_retweet ?? false,
                }));
                this.allPosts.push(...posts);
            }

            this.availableDates = runsData.dates || [];
            this.availableRuns = (runsData.runs || []).filter(r => r.has_posts);
            this.renderRunSelector();

            if (this.allPosts.length > 0) {
                this.renderAll();
            } else {
                document.getElementById('viewer-feed').innerHTML = `
                    <div class="empty-state">
                        <div class="icon">&#9776;</div>
                        <p>No collected feeds yet</p>
                        <p class="text-sm mt-2">Go to <a href="#collect">Collect</a> to start gathering feeds</p>
                    </div>
                `;
            }
        } catch (e) {
            document.getElementById('viewer-feed').innerHTML =
                `<div class="text-danger" style="padding:20px">Failed to load: ${e.message}</div>`;
        }
    },

    renderRunSelector() {
        const sel = document.getElementById('run-selector');
        let html = '<option value="latest">Latest runs</option>';

        // Group by date using optgroup
        if (this.availableDates && this.availableDates.length > 0) {
            for (const dateGroup of this.availableDates) {
                html += `<optgroup label="${dateGroup.date}">`;
                for (const job of dateGroup.jobs) {
                    for (const run of job.platforms) {
                        if (!run.has_posts) continue;
                        const label = `${run.platform || '?'} — job ${job.job_id} (${run.post_count || '?'} posts)`;
                        html += `<option value="${run.run_id}">${label}</option>`;
                    }
                }
                html += '</optgroup>';
            }
        } else {
            // Fallback for flat list
            for (const run of this.availableRuns) {
                const label = `${run.platform || '?'} — ${run.timestamp || run.run_id} (${run.post_count || '?'} posts)`;
                html += `<option value="${run.run_id}">${label}</option>`;
            }
        }

        sel.innerHTML = html;
    },

    async onRunSelect(value) {
        if (value === 'latest') {
            await this.loadLatest();
            return;
        }

        try {
            const data = await api(`/data/runs/${value}`);
            const posts = (data.posts || data.tweets || []).map(p => ({
                ...p,
                reposts: p.reposts ?? p.retweets ?? 0,
                is_repost: p.is_repost ?? p.is_retweet ?? false,
            }));
            this.allPosts = posts;
            this.renderAll();
        } catch (e) {
            document.getElementById('viewer-feed').innerHTML =
                `<div class="text-danger" style="padding:20px">Failed to load run: ${e.message}</div>`;
        }
    },

    renderAll() {
        this.currentPlatform = 'all';
        this.currentSort = 'time';
        this.renderPlatformTabs();
        this.updateMeta();
        this.renderPosts();
    },

    renderPlatformTabs() {
        const platforms = [...new Set(this.allPosts.map(p => p.platform))];
        const counts = {};
        for (const p of this.allPosts) counts[p.platform] = (counts[p.platform] || 0) + 1;

        const tabs = document.getElementById('viewer-platform-tabs');
        let html = `<button class="tab active" data-platform="all" onclick="ViewerPage.setPlatform('all',this)">All<span class="badge badge-count">${this.allPosts.length}</span></button>`;
        for (const p of platforms) {
            html += `<button class="tab" data-platform="${p}" onclick="ViewerPage.setPlatform('${p}',this)">${p.charAt(0).toUpperCase() + p.slice(1)}<span class="badge badge-count">${counts[p]}</span></button>`;
        }
        tabs.innerHTML = html;
        tabs.style.display = platforms.length > 1 ? 'flex' : 'none';
    },

    setPlatform(platform, btn) {
        this.currentPlatform = platform;
        document.querySelectorAll('#viewer-platform-tabs .tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.updateMeta();
        this.renderPosts();
    },

    setSort(sort, btn) {
        this.currentSort = sort;
        document.querySelectorAll('#viewer-sort-bar button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.renderPosts();
    },

    getFilteredPosts() {
        if (this.currentPlatform === 'all') return this.allPosts;
        return this.allPosts.filter(p => p.platform === this.currentPlatform);
    },

    updateMeta() {
        const posts = this.getFilteredPosts();
        const imgCount = posts.reduce((n, t) => n + (t.media_urls || []).length, 0);
        const vidCount = posts.reduce((n, t) => n + (t.video_urls || []).length, 0);
        const platforms = [...new Set(posts.map(p => p.platform))].join(', ');
        document.getElementById('viewer-meta').textContent =
            `${posts.length} posts | ${imgCount} images, ${vidCount} videos | ${platforms}`;
    },

    sortPosts(posts, sort) {
        const sorted = [...posts];
        switch(sort) {
            case 'likes': sorted.sort((a,b) => (b.likes||0) - (a.likes||0)); break;
            case 'reposts': sorted.sort((a,b) => (b.reposts||0) - (a.reposts||0)); break;
            case 'replies': sorted.sort((a,b) => (b.replies||0) - (a.replies||0)); break;
            default: sorted.sort((a,b) => new Date(b.created_at) - new Date(a.created_at));
        }
        return sorted;
    },

    renderPosts() {
        const posts = this.getFilteredPosts();
        const sorted = this.sortPosts(posts, this.currentSort);
        const feed = document.getElementById('viewer-feed');

        if (sorted.length === 0) {
            feed.innerHTML = '<div class="empty-state">No posts to display</div>';
            return;
        }

        const multiPlatform = this.currentPlatform === 'all'
            && new Set(this.allPosts.map(p => p.platform)).size > 1;

        const html = (t) => {
            const postId = (t.id || Math.random().toString(36).slice(2)).toString();
            const platform = t.platform || 'twitter';
            const hasReplies = t.top_replies && t.top_replies.length > 0;

            const repliesSection = hasReplies ? `
                <button class="post-replies-toggle" onclick="ViewerPage.toggleReplies('${postId}',this)">Show ${t.top_replies.length} replies</button>
                <div id="replies-${postId}" class="post-replies-section hidden">
                    ${t.top_replies.map(r => `
                        <div class="reply">
                            <div class="mb-1">
                                <span class="reply-author">${r.author_name || r.author_handle || 'Unknown'}</span>
                                <span class="reply-handle">${r.author_handle ? `@${r.author_handle}` : ''}</span>
                            </div>
                            <div class="reply-body">${PostRenderer.formatText(r.text, platform)}</div>
                            <div class="reply-stats">${PostRenderer.fmtNum(r.likes)} likes</div>
                        </div>
                    `).join('')}
                </div>` : '';

            return PostRenderer.renderPost(t, {
                postId,
                showPlatformBadge: multiPlatform,
                mediaResolver: p => p.startsWith('feed_data/') ? '/' + p : '/feed_data/' + p,
                afterStats: repliesSection,
            });
        };

        // Progressive render — paint the first chunk immediately so the page
        // feels instant, then stream the rest in on the next frame. Building
        // a single huge innerHTML for hundreds of posts blocked first paint
        // for ~1–2 s. Observers are attached after the full feed lands so
        // every video/iframe gets bound exactly once.
        const FIRST_CHUNK = 30;
        feed.innerHTML = sorted.slice(0, FIRST_CHUNK).map(html).join('');

        if (sorted.length <= FIRST_CHUNK) {
            PostRenderer.setupCarouselDrag();
            PostRenderer.setupVideoAutoplay('viewer-feed');
            PostRenderer.setupYoutubeHover('viewer-feed');
            return;
        }

        const renderToken = (this._renderToken = (this._renderToken || 0) + 1);
        requestAnimationFrame(() => {
            if (renderToken !== this._renderToken) return;  // superseded by a newer render
            const rest = sorted.slice(FIRST_CHUNK).map(html).join('');
            feed.insertAdjacentHTML('beforeend', rest);
            PostRenderer.setupCarouselDrag();
            PostRenderer.setupVideoAutoplay('viewer-feed');
            PostRenderer.setupYoutubeHover('viewer-feed');
        });
    },

    toggleReplies(postId, btn) {
        const section = document.getElementById(`replies-${postId}`);
        if (!section) return;
        const isOpen = !section.classList.contains('hidden');
        section.classList.toggle('hidden');
        const count = section.children.length;
        btn.textContent = isOpen ? `Show ${count} replies` : 'Hide replies';
    },

};
