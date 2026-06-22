/**
 * Curate with AI — the focused curation workflow.
 *
 * Flow:
 *   1. Set expectations: the agent runs a short Q&A to learn your goals on first run.
 *   2. Pick your AI agent (Claude Code, Cursor, Codex, other) and copy-paste the
 *      launch command + prompt.
 *   3. Review/edit goals — once the agent has interviewed you, your `goals.md`
 *      lives in the workspace and is editable here.
 *   4. When the agent writes posts.filtered.json under data/<date>/<job_id>/,
 *      it shows up under AI Curation.
 *
 * Deliberately separate from the raw Feed (#viewer) and curated Feed
 * (#curated) pages — this is the "how do I actually run curation" surface.
 */
window.CuratePage = {
    workspace: null,
    goals: null,
    selectedAgent: 'claude-code',

    agents: [
        {
            id: 'claude-code',
            name: 'Claude Code',
            tagline: "Anthropic's terminal agent",
            launchCmd: (path) => `cd ${quote(path)}\nclaude`,
            prompt: () => `curate the latest feed`,
            setup: 'Your workspace has the skill installed at <code>.claude/skills/focus-lab-curator/</code> — Claude Code auto-discovers it.',
        },
        {
            id: 'cursor',
            name: 'Cursor',
            tagline: 'AI-first IDE',
            launchCmd: (path) => `cursor ${quote(path)}`,
            prompt: () => `Read \`skills/focus-lab-curator/SKILL.md\` and follow it. Curate the latest collection job — use my preferences in \`goals.md\`, score every post in \`data/<latest date>/<latest job>/<each platform>/posts.json\` together, and write \`posts.filtered.json\` at the job root.`,
            setup: 'Open this folder in Cursor; the skill is at <code>skills/focus-lab-curator/SKILL.md</code>. Copy into <code>.cursor/rules/</code> for auto-invocation.',
        },
        {
            id: 'codex',
            name: 'Codex CLI',
            tagline: "OpenAI's terminal agent",
            launchCmd: (path) => `cd ${quote(path)}\ncodex --instructions skills/focus-lab-curator/SKILL.md`,
            prompt: () => `curate the latest feed`,
            setup: 'Point <code>--instructions</code> at the workspace skill file.',
        },
        {
            id: 'any',
            name: 'Other agent',
            tagline: 'Anything with a system prompt',
            launchCmd: (path) => `cd ${quote(path)}\n# launch your agent here`,
            prompt: (path) => [
                `You are in a Focus Lab Feed workspace: ${path}`,
                ``,
                `Read \`skills/focus-lab-curator/SKILL.md\` in full and follow it exactly.`,
                `Curate the latest collection job under \`data/\` — use my preferences`,
                `in \`goals.md\`, score every platform's posts together, and write`,
                `\`posts.filtered.json\` at the job root. Preserve every original`,
                `field. Sort by score desc.`,
            ].join('\n'),
            setup: "Paste <code>skills/focus-lab-curator/SKILL.md</code> into your agent's system prompt.",
        },
    ],

    render() {
        return `
            <div class="fade-in">
                <h1 class="page-title">Curate with AI</h1>
                <p class="page-subtitle">
                    Teach your agent what you actually want, point it at your workspace, and let it
                    score every post in the latest collection job for you. The curated feed shows up
                    under <a href="#curated">AI Curation</a>.
                </p>
                <div id="curate-body">
                    <div class="empty-state"><p class="text-secondary">Loading…</p></div>
                </div>
            </div>
        `;
    },

    async init() {
        const [ws, goals] = await Promise.all([
            api('/workspace').catch(() => ({ is_setup: false })),
            api('/workspace/goals').catch(() => null),
        ]);
        this.workspace = ws;
        this.goals = goals;
        this.renderBody();
    },

    renderBody() {
        const body = document.getElementById('curate-body');
        if (!body) return;

        const isSetup = this.workspace && this.workspace.is_setup;
        if (!isSetup) {
            // Shouldn't happen post-onboarding, but handle defensively.
            body.innerHTML = `
                <div class="card">
                    <h3 class="font-semibold text-subtitle mb-2">Workspace not set up yet</h3>
                    <p class="text-secondary text-sm mb-3">
                        Set up an export folder in <a href="#settings">Settings</a> and come back.
                    </p>
                </div>
            `;
            return;
        }

        body.innerHTML = [
            this._sectionIntro(),
            this._sectionAgent(),
            this._sectionGoals(),
            this._sectionViewResult(),
        ].join('');

        // Wire up handlers.
        const save = document.getElementById('curate-goals-save');
        if (save) save.addEventListener('click', () => this.saveGoals());
        const refreshGoals = document.getElementById('curate-goals-refresh');
        if (refreshGoals) refreshGoals.addEventListener('click', () => this.refreshGoals());

        document.querySelectorAll('[data-agent-id]').forEach(el => {
            el.addEventListener('click', () => {
                this.selectedAgent = el.dataset.agentId;
                this.renderBody();
            });
        });

        document.querySelectorAll('.copy-btn').forEach(btn => {
            btn.addEventListener('click', () => this._copyToClipboard(btn));
        });
    },

    // --------------------------------------------------------------- sections

    _sectionIntro() {
        return `
            <div class="card curate-section">
                <div class="curate-section-head">
                    <div class="curate-section-num">1</div>
                    <div>
                        <h3 class="font-semibold text-subtitle">How this works</h3>
                        <p class="text-secondary text-sm mt-1">
                            On the first run, your AI agent does a short Q&amp;A to learn what you
                            actually want to see — your goals, what brings you joy, and what feels
                            like drain. It saves that to <code>goals.md</code> in your workspace, then
                            scores every post against it. You can review and tweak the answers
                            below in <strong>step 4</strong> after the agent finishes.
                        </p>
                    </div>
                </div>
            </div>
        `;
    },

    _sectionGoals() {
        const content = (this.goals && this.goals.content) || '';
        const hasContent = content.trim().length > 0;
        const headerNote = hasContent
            ? 'Your agent has captured these from your Q&amp;A. Tweak any time.'
            : 'Empty for now. Run your agent in step 3 — it will interview you and write this file. Come back to refine.';
        return `
            <div class="card curate-section">
                <div class="curate-section-head">
                    <div class="curate-section-num ${hasContent ? 'done' : ''}">4</div>
                    <div style="flex:1">
                        <h3 class="font-semibold text-subtitle">Review &amp; tweak your goals</h3>
                        <p class="text-secondary text-sm mt-1">${headerNote}</p>
                    </div>
                    <button class="btn btn-secondary btn-sm" id="curate-goals-refresh" title="Reload goals.md from disk">Reload</button>
                </div>
                ${hasContent ? `
                    <textarea id="curate-goals-textarea" class="goals-textarea" rows="12" spellcheck="false">${esc(content)}</textarea>
                    <div class="flex items-center gap-2 mt-2">
                        <button class="btn btn-primary btn-sm" id="curate-goals-save">Save changes</button>
                        <span class="text-secondary text-sm" id="curate-goals-status"></span>
                    </div>
                ` : `
                    <div class="empty-state" style="padding: var(--sp-4) 0 0">
                        <p class="text-secondary text-sm">
                            <code>goals.md</code> hasn't been written yet. Run the agent above and
                            answer the Q&amp;A — it'll appear here automatically.
                        </p>
                    </div>
                `}
            </div>
        `;
    },

    _sectionAgent() {
        const agent = this.agents.find(a => a.id === this.selectedAgent);
        const path = this._workspacePath();

        return `
            <div class="card curate-section">
                <div class="curate-section-head">
                    <div class="curate-section-num">2</div>
                    <div>
                        <h3 class="font-semibold text-subtitle">Run your AI agent</h3>
                        <p class="text-secondary text-sm mt-1">
                            Pick the agent you already have. We'll generate a ready-to-paste
                            launch command and the prompt to give it. The agent reads
                            <code>goals.md</code>, scores the latest collection job, and writes
                            <code>posts.filtered.json</code> next to the data.
                        </p>
                    </div>
                </div>

                <div class="agent-grid">
                    ${this.agents.map(a => `
                        <button class="agent-card ${a.id === this.selectedAgent ? 'selected' : ''}"
                                data-agent-id="${a.id}">
                            <div class="agent-name">${a.name}</div>
                            <div class="agent-tagline">${a.tagline}</div>
                        </button>
                    `).join('')}
                </div>
                <div class="agent-setup text-secondary text-sm mt-3 mb-3">${agent.setup}</div>

                <div class="howto-sublabel">Run this in your terminal</div>
                <div class="code-block-wrap">
                    <pre class="code-block" id="launch-code">${esc(agent.launchCmd(path))}</pre>
                    <button class="copy-btn" data-copy-target="launch-code">Copy</button>
                </div>

                <div class="howto-sublabel">Then paste this to the agent</div>
                <div class="code-block-wrap">
                    <pre class="code-block" id="prompt-code">${esc(agent.prompt(path))}</pre>
                    <button class="copy-btn" data-copy-target="prompt-code">Copy</button>
                </div>
            </div>
        `;
    },

    _sectionViewResult() {
        return `
            <div class="card curate-section">
                <div class="curate-section-head">
                    <div class="curate-section-num">5</div>
                    <div>
                        <h3 class="font-semibold text-subtitle">See the curated result</h3>
                        <p class="text-secondary text-sm mt-1">
                            When your agent writes <code>posts.filtered.json</code> at
                            <code>data/&lt;date&gt;/&lt;job_id&gt;/</code>, it shows up on
                            <a href="#curated">AI Curation</a> with per-post scores and reasons.
                        </p>
                    </div>
                </div>
            </div>
        `;
    },

    // --------------------------------------------------------------- actions

    _workspacePath() {
        return (this.workspace && this.workspace.path) || '';
    },

    async refreshGoals() {
        const btn = document.getElementById('curate-goals-refresh');
        if (btn) { btn.disabled = true; btn.textContent = 'Reloading…'; }
        try {
            this.goals = await api('/workspace/goals');
        } catch (e) {
            this.goals = null;
        }
        this.renderBody();
    },

    async saveGoals() {
        const ta = document.getElementById('curate-goals-textarea');
        if (!ta) return;
        const status = document.getElementById('curate-goals-status');
        const btn = document.getElementById('curate-goals-save');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        try {
            const r = await api('/workspace/goals', {
                method: 'POST',
                body: JSON.stringify({ content: ta.value }),
            });
            if (r.success) {
                this.goals = { content: ta.value, path: r.path, exists: true };
                if (status) { status.textContent = 'Saved.'; setTimeout(() => { status.textContent = ''; }, 2500); }
            }
        } catch (e) {
            if (status) status.textContent = 'Save failed: ' + e.message;
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Save goals'; }
        }
    },

    async _copyToClipboard(btn) {
        const target = document.getElementById(btn.dataset.copyTarget);
        if (!target) return;
        try {
            await navigator.clipboard.writeText(target.textContent);
            const orig = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => { btn.textContent = orig; }, 1500);
        } catch (e) {
            const range = document.createRange();
            range.selectNode(target);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            document.execCommand('copy');
            window.getSelection().removeAllRanges();
        }
    },
};

// --------------------------------------------------------------- helpers

function quote(path) {
    if (!path) return '.';
    return path.includes(' ') ? `"${path}"` : path;
}
function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
function escAttr(s) { return esc(s); }
