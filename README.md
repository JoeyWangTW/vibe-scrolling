<p align="center">
  <img src="assets/focuslab-logo.svg" width="120" alt="Focus Lab Feed logo">
</p>

<h1 align="center">Focus Lab Feed</h1>

<p align="center">
  A desktop app that scrolls your social feeds for you, lets your own AI agent curate them, and hands you back a phone-sized feed you actually want to open.
</p>

<p align="center">
  <img src="docs/images/platforms.png" alt="Focus Lab Feed — Platforms page" width="720">
</p>

---

## Download

**macOS `.dmg`** → [Latest release](https://github.com/JoeyWangTW/vibe-scrolling/releases/latest)

1. Download, open the `.dmg`, drag **Focus Lab Feed** into `/Applications`.
2. First launch: **right-click → Open** (the app isn't code-signed yet, so Gatekeeper shows an "unidentified developer" warning — this only happens once).
3. That's it. The app walks you through connecting platforms, picking a workspace folder, and collecting your first feed.

No Python install, no Homebrew, no Terminal required.

> If you don't see a release yet, [build it yourself](#build-it-yourself) — it's three commands.

---

## What it does

Modern social feeds are engineered to keep you there — the algorithm's goal (your attention) is not your goal (your life). Focus Lab Feed flips the loop:

1. **Collect** — the desktop app opens a real browser under the hood and scrolls Twitter/X, Threads, Instagram, and YouTube on your behalf. No API tokens, no rate limits, just automation of what you'd be doing anyway.
2. **Curate** — export a *pack* (a folder with `posts.json`, media, and a Markdown skill file). `cd` into it, run any agent you like — Claude Code, Cursor, Codex CLI — and say *"curate this feed."* The agent reads your `goals.md` and writes `posts.filtered.json`.
3. **Consume** — open the desktop app's **AI Curation** tab and scroll. The curated pack appears automatically once the agent finishes.

### The philosophy

A feed that's *purely* useful — only goal-relevant content — is boring. People close it and go read a book (which is fine, but it's not what we're optimizing for). The secret sauce of social media is engagement: dopamine, laughter, surprise. That part matters.

So the curated feed has three goals, in order:

1. **Help you toward your goals** — the stuff you're actually trying to learn or build.
2. **Keep the joy** — humor, art, hobbies. Non-negotiable.
3. **Drop the drain** — outrage loops, engagement bait, content that leaves you feeling worse.

Not "quit social media." Not "productivity feed." A *joy-aware* feed that nudges you toward your goals.

<p align="center">
  <img src="docs/images/viewer.png" alt="Focus Lab Feed — Viewer page" width="720">
</p>

---

## Build it yourself

You'd do this if (a) there's no release yet for your macOS version, (b) you want to sign the bundle with your own Apple Developer ID, or (c) you're contributing.

### Prerequisites

- macOS 10.15+
- [Python 3.13](https://www.python.org/downloads/) (`python3 --version` to check — Homebrew works: `brew install python@3.13`)
- Xcode Command Line Tools: `xcode-select --install`

### Clone and run from source (dev mode)

```bash
git clone https://github.com/JoeyWangTW/vibe-scrolling.git focus-lab-feed-collector
cd focus-lab-feed-collector

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-app.txt
playwright install chromium

python3 -m app.main          # native window opens
```

The first launch walks you through connecting your social accounts (they're just real browser logins — no API keys), picking a workspace folder, and running a collection.

### Build the `.app` / `.dmg`

```bash
./scripts/build-macos.sh
# produces:
#   dist/Focus Lab Feed.app
#   dist/FocusLabFeed.dmg
```

The build script uses [PyInstaller](https://pyinstaller.org) to bundle Python, FastAPI, Playwright's Chromium, the curator skill, and the mobile viewer into a self-contained `.app`. No external dependencies at runtime.

### Rebuild the app icon (optional)

If you tweak `assets/focuslab-logo.svg`:

```bash
./scripts/build-icon.sh
# Uses only built-in macOS tools (qlmanage + sips + iconutil).
# Writes assets/focuslab.icns, which the spec references for the bundle.
```

### Sign & notarize (for public distribution)

An unsigned `.app` opens fine but triggers macOS's "unidentified developer" warning on first launch. To ship a clean double-click experience for non-technical users, you need an Apple Developer ID and the `codesign` + `xcrun notarytool` flow. That's outside the scope of `build-macos.sh` today.

---

## Project layout

```
focus-lab-feed-collector/
├── app/                      # Desktop app (FastAPI + pywebview)
│   ├── api/                  # REST endpoints
│   ├── static/               # SPA frontend (vanilla JS)
│   ├── main.py               # Entry: launches uvicorn + native window
│   └── workspace.py          # Workspace bootstrap
├── src/                      # Collection engine (platform-agnostic)
│   └── platforms/            # Twitter / Threads / Instagram / YouTube
├── skills/focus-lab-curator/ # Agent skill (SKILL.md + goals template)
├── viewer/mobile.html        # Phone viewer — single self-contained HTML file
├── assets/                   # Logo SVG + compiled .icns
├── scripts/                  # Build scripts (macOS bundle, icon)
└── docs/                     # Status, worklog, landing copy, screenshots
```

See [`docs/landing.md`](docs/landing.md) for the longer-form pitch and technical write-up of the viewer.

---

## Agents supported

The curator is agent-agnostic — a plain Markdown skill + a strict JSON output contract. Tested with:

- **Claude Code** — skill auto-discovered via `.claude/skills/` symlink in your workspace
- **Cursor** — `skills/focus-lab-curator/SKILL.md` or copy into `.cursor/rules/`
- **Codex CLI** — `codex --instructions skills/focus-lab-curator/SKILL.md`
- **Any other agent** — paste `SKILL.md` into its system prompt

The **AI Curation** tab in the desktop app generates a copy-paste launch command + prompt tailored to whichever agent you pick.

---

## Status

Pre-alpha. Works end-to-end on one maintainer's machine; rough edges expected. Contributions welcome, especially around:

- Windows / Linux support (currently macOS-only)
- Apple Developer signing in `build-macos.sh`
- Streaming zip extraction in the viewer (for packs >1GB on iOS)
- Hosted viewer PWA (service-worker offline, Add-to-Home-Screen flow)

---

## License

TBD.
