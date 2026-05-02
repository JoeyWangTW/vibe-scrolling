# Installing the Focus Lab Curator

The curator is a plain-markdown skill. It runs in any capable coding agent.

The desktop app sets this up for you automatically when you bootstrap a workspace — `<workspace>/skills/focus-lab-curator/` and a `.claude/skills/focus-lab-curator` symlink are both created. The notes below cover manual setups (other agents, hand-rolled workspaces).

## Claude Code

If your workspace was bootstrapped by the app, you're done. Otherwise:

```bash
mkdir -p ~/.claude/skills
ln -sfn "$(pwd)/skills/focus-lab-curator" ~/.claude/skills/focus-lab-curator
```

From the workspace root:

```bash
cd ~/Focus\ Lab\ Feed     # or wherever your workspace lives
claude
```

In the Claude prompt, invoke the skill:

```
/focus-lab-curator
```

or just say *"curate the latest feed"* — the skill's description will match.

## Cursor

Copy `SKILL.md` into your workspace rules:

```bash
mkdir -p .cursor/rules
cp skills/focus-lab-curator/SKILL.md .cursor/rules/focus-lab-curator.md
```

Then open the workspace folder in Cursor and ask the agent to *"curate the latest collection job using the Focus Lab Curator skill"*.

## Codex / OpenAI Agents

Copy `SKILL.md` into your agent's instructions file, or pass it with `--instructions`:

```bash
codex --instructions skills/focus-lab-curator/SKILL.md
```

## Any other agent

Paste the contents of `SKILL.md` into your agent's system prompt. The skill is plain markdown plus a JSON contract — any capable agent can follow it.

---

## Where `goals.md` lives

`<workspace>/goals.md` — one file at the workspace root, shared by every job. The curator reads it on every run, so edits take effect on the next curation.

If `goals.md` is missing or essentially empty, the skill runs a short interview (5 questions) and writes one for you. You can edit by hand at any time.

---

## Output

The skill always writes **one file** per job:

```
<workspace>/data/<date>/<job_id>/posts.filtered.json
```

It contains every kept post (across all platforms in that job, ranked by score) plus a compact audit log of what was dropped and why. The Focus Lab Feed app's **AI Curation** tab picks it up automatically.
