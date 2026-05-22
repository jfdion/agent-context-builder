# agent-context-builder

A map-reduce pipeline that transforms a source directory tree of text and binary files into a structured, summarized, and indexed knowledge base. Designed for agent-driven execution with resumability, incremental updates, and full audit logging.

## Installation

**Prerequisites:** Python 3.13+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jfdion/agent-context-builder
cd agent-context-builder
uv sync
```

An [Anthropic API key](https://console.anthropic.com/) is required for the Python CLI commands (`uv run ingest …` and the `/…-api` slash commands) but **not** for the agent-orchestrated slash commands (`/ingest`, `/ingest-add`, `/ingest-amend`), which use your active Claude Code session instead.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # required for uv run / /…-api commands only
```

## Quick Start

### 1. Make the tool available globally

From the cloned repo, run the installer once:

```bash
./install.sh
```

This sets up everything Claude Code needs to run the pipeline from any directory:

- runs `uv sync` if the local `.venv/` is missing;
- symlinks `.claude/commands/` → `~/.claude/commands/` so the seven `/ingest*` slash commands are available in **every** Claude Code session;
- symlinks `templates/CLAUDE-ingest-template.md` → `~/.claude/CLAUDE-ingest-template.md` (used by `/ingest-init`);
- installs an `ingest-extract` wrapper at `~/.local/bin/ingest-extract` that runs the binary-doc extractor from anywhere. If `~/.local/bin` is not already on your `PATH`, the installer prints the line to add to your shell profile.

To undo: remove `~/.claude/commands` and `~/.claude/CLAUDE-ingest-template.md` (both symlinks) and `~/.local/bin/ingest-extract`.

### 2. Build a knowledge base from any directory

Open Claude Code in the project you want to ingest and run:

```text
/ingest-init                            # create ./CLAUDE.md and ./context/
/ingest ./src ./context                 # full pipeline (Sonnet + Haiku + Opus, no API key)
```

Or, with an API key, use the Python CLI:

```bash
/ingest-api ./src ./context             # same, from inside Claude Code
uv run ingest ./src ./context           # same, from a regular shell (in the repo dir)
```

Add `--locale fr_CA` (or any BCP-47 tag) to force the synthesis language.

### 3. Keep it up to date

```text
/ingest-add   ./src/new-module ./context     # new content
/ingest-amend ./src/changed-dir ./context    # changed content (SHA-256 diff)
```

The same Python CLI equivalents — `/ingest-add-api`, `/ingest-amend-api`, `uv run ingest-add`, `uv run ingest-amend` — work identically.

## Suggested Workflow

1. **Build the knowledge base** — run `ingest` on your source directory once. This produces `context/index.md` and all supporting files.
2. **Point an agent at the context** — copy [`CLAUDE.md`](CLAUDE.md) into your target project (or use `/ingest-init` to do it for you). It instructs Claude to enter via `index.md`, read summaries before full extractions, and never read source or binary files directly. This keeps token usage low and answers fast.
3. **Keep it up to date** — when source files change, run `ingest-amend` on the affected subdirectory. When new content is added, use `ingest-add`.

## Pipeline Overview

`ingest` runs a five-step map-reduce pipeline and writes a structured knowledge base to the destination directory. See [spec.md](spec.md) for the full specification.

```
source/                 context/
├── docs/               ├── docs/
│   ├── spec.pdf        │   ├── spec.md            ← extracted text
│   └── arch.png        │   ├── arch.md
│                       │   ├── _summary_spec.md   ← per-file summary
└── README.md           │   └── _summary_arch.md
                        ├── README.md
                        ├── _summary_README.md
                        ├── _reduce_docs.md        ← roll-up for docs/ (one level above)
                        ├── _reduce_root.md        ← roll-up for context/ itself
                        ├── index.md               ← top-level entry point
                        └── .ingest/               ← manifest, state, journal
```

`_reduce_<dir>.md` files are placed **one level above** the directory they summarize, alongside sibling reduce files. The root roll-up is named `_reduce_root.md`.

| Step | Name | Model (slash commands) | What happens |
|------|------|------------------------|--------------|
| 0 | **Walk** | Sonnet (orchestrator) | Scans source, mirrors directory tree, builds a manifest with SHA-256 content hashes for each file. |
| 1 | **Extract** | Haiku (parallel agents) | Converts each file to Markdown. Text files are read directly; PDFs/DOCX/PPTX/XLSX are parsed with their respective libraries; images are described via Claude vision. |
| 2 | **Summarize** | Haiku (parallel agents) | Generates a concise `_summary_<name>.md` for every extracted file. |
| 3 | **Reduce** | Haiku (parallel agents) | Bottom-up roll-up: for each directory, combines its summaries into a single `_reduce_<dir>.md`, then propagates upward to the root. |
| 4 | **Index** | Opus (single agent) | Writes `index.md` — the canonical entry point for the knowledge base — covering directory structure, key concepts, and file purposes. |

**Supported file types** (extensions can be tuned in `src/ingest_pipeline/config.py`):

- **Text** — `.txt`, `.md`, `.rst`, `.csv`, `.json`, `.yaml`, `.yml`, `.xml`, `.html`, `.svg`, `.toml`, `.ini`, `.cfg`, `.sql`, `.sh`, `.bash`, `.zsh`, `.py`, `.js`, `.ts`, `.java`, `.cs`, `.kt`, `.swift`, `.go`, `.rb`, `.rs`, `.c`, `.h`, `.cpp`, `.hpp`
- **Binary documents** — `.pdf`, `.docx`, `.pptx`, `.xlsx`
- **Images** — `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`

Files matching `SKIP_NAMES` (`.DS_Store`, `.gitignore`, `.gitkeep`), `SKIP_SUFFIXES` (`.lock`, `.pyc`), or living under `SKIP_DIRS` (`node_modules`, `.git`, `__pycache__`, `.ingest`, `.venv`, `venv`, `.tox`, `dist`, `build`, `.mypy_cache`) are pruned during the walk.

**Resumability:** if a run is interrupted, re-running `uv run ingest` on the same destination offers to resume from the last completed step. Files with `status: "completed"` in the manifest are skipped; only `pending`, `extracted`, and `failed` files are re-processed.

### Commands

There are two ways to run the pipeline: the **Python CLI** (requires `ANTHROPIC_API_KEY`) and **Claude Code slash commands** (uses your active session, no key needed).

#### Python CLI (`uv run`)

```bash
# Full ingest (with resume support)
uv run ingest <source> <destination> [--rpm 60] [--max-binary-mb 50] [--locale fr_CA]

# Add a new subdirectory to an existing knowledge base
uv run ingest-add <source-subdir> <destination> [--locale fr_CA]

# Re-process files in a subdirectory that have changed
uv run ingest-amend <source-subdir> <destination> [--locale fr_CA]
```

#### Claude Code slash commands

Six commands are available from within a Claude Code session in this repo:

| Command | Backend | Description |
|---------|---------|-------------|
| `/ingest <source> <destination>` | Agent (parallel) | Full pipeline — Sonnet orchestrates, Haiku processes files in parallel, Opus builds the index |
| `/ingest-add <source-subdir> <destination>` | Agent (parallel) | Add a new subdirectory, re-reduce and re-index |
| `/ingest-amend <source-subdir> <destination>` | Agent (parallel) | Reset and re-process a subdirectory, re-reduce and re-index |
| `/ingest-api <source> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE]` | Python CLI | Same as `uv run ingest`; requires `ANTHROPIC_API_KEY` |
| `/ingest-add-api <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE]` | Python CLI | Same as `uv run ingest-add`; requires `ANTHROPIC_API_KEY` |
| `/ingest-amend-api <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE]` | Python CLI | Same as `uv run ingest-amend`; requires `ANTHROPIC_API_KEY` |

The agent commands (`/ingest`, `/ingest-add`, `/ingest-amend`) use a multi-agent architecture:

- **Sonnet** (orchestrator) — walks the source tree, builds the manifest, batches work, and coordinates phases.
- **Haiku** (parallel workers) — extract, summarize, and reduce steps; files are batched (≤10 per agent) and all batches within a phase run simultaneously.
- **Opus** (index builder) — generates `index.md` from the completed reduce tree.

### Custom prompts

All prompts are plain text files under `prompts/`. The agent commands read these files at runtime, so edits apply to both backends.

Pass `--prompts-dir` to the Python CLI to use a different directory:

```bash
uv run ingest <source> <destination> --prompts-dir ./my-prompts
```

| File | Step | Purpose |
|------|------|---------|
| `extract_text.txt` | 1 | Cleanup pass for text and binary-doc files with complex structure |
| `extract_image.txt` | 1 | Vision analysis prompt for image files |
| `summarize.txt` | 2 | Per-file summary generation |
| `reduce.txt` | 3 | Directory-level synthesis |
| `index.txt` | 4 | Top-level knowledge base index |

Copy the built-in `prompts/` directory as a starting point and edit as needed.

### Locale support

The pipeline detects the language of each source file at extraction time and records it in the `locale:` field of the resulting extract, summary, and reduce front matter. The reduce step is also told which language to synthesize in, so a French source tree produces French summaries and roll-ups.

**Auto-detection (default).** During Step 1, `detect_locale()` scores each extracted text against built-in stopword sets (currently French and English) and writes one of `fr`, `en`, or `und` into the file's front matter. Between Steps 2 and 3 the pipeline tallies per-file locales:

- All files agree on one locale → it is used for the run automatically.
- Multiple locales are detected → you are prompted interactively to pick one.
- No locale could be determined → `"und"` is used.

**Forcing a locale.** Pass `--locale` with a BCP-47 tag to skip detection and the interactive prompt entirely:

```bash
uv run ingest <source> <destination> --locale fr
uv run ingest <source> <destination> --locale fr_CA
uv run ingest-add <source-subdir> <destination> --locale en
```

The same flag is available on all three CLI commands and their `/…-api` Claude Code equivalents (e.g. `/ingest-api <source> <destination> --locale fr_CA`).

See [spec.md § Locale Handling](spec.md) for the full resolution order and how each step consumes the locale.
