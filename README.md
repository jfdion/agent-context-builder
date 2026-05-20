# agent-context-builder

A map-reduce pipeline that transforms a source directory tree of text and binary files into a structured, summarized, and indexed knowledge base. Designed for agent-driven execution with resumability, incremental updates, and full audit logging.

## Installation

**Prerequisites:** Python 3.13+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jfdion/agent-context-builder
cd agent-context-builder
uv sync
```

An [Anthropic API key](https://console.anthropic.com/) is required for the Python CLI commands (`-api` suffix) but **not** for the Claude Code slash commands, which use your active Claude Code session instead.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # required for uv run / /ingest-api commands only
```

## Suggested Workflow

1. **Build the knowledge base** — run `ingest` on your source directory once. This produces `context/index.md` and all supporting files.
2. **Point an agent at the context** — copy [`CLAUDE.md`](CLAUDE.md) into your target project. It instructs Claude to enter via `index.md`, read summaries before full extractions, and never read source or binary files directly. This keeps token usage low and answers fast.
3. **Keep it up to date** — when source files change, run `ingest-amend` on the affected subdirectory. When new content is added, use `ingest-add`.

## Pipeline Overview

`ingest` runs a five-step map-reduce pipeline and writes a structured knowledge base to the destination directory. See [spec.md](spec.md) for the full specification.

```
source/          →      context/
├── docs/               ├── docs/
│   ├── spec.pdf        │   ├── spec.md          ← extracted text
│   └── arch.png        │   ├── arch.md
│                       │   ├── _summary_spec.md ← per-file summary
│                       │   ├── _summary_arch.md
│                       │   └── _reduce_docs.md  ← directory roll-up
└── README.md           ├── README.md
                        └── index.md             ← top-level entry point
```

| Step | Name | What happens |
|------|------|--------------|
| 0 | **Walk** | Scans source, mirrors directory tree, builds a manifest with SHA-256 fingerprints for each file. |
| 1 | **Extract** | Converts each file to Markdown. Text files are read directly; PDFs/DOCX/PPTX/XLSX are parsed with their respective libraries; images are described via Claude vision. |
| 2 | **Summarize** | Generates a concise `_summary_<name>.md` for every extracted file. |
| 3 | **Reduce** | Bottom-up roll-up: for each directory, combines its summaries into a single `_reduce_<dir>.md`, then propagates upward to the root. |
| 4 | **Index** | Writes `index.md` — the canonical entry point for the knowledge base — covering directory structure, key concepts, and file purposes. |

**Supported file types:** `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cs`, `.sql`, `.html`, `.xml`, `.sh` and other text formats; `.pdf`, `.docx`, `.pptx`, `.xlsx`; `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`.

**Resumability:** if a run is interrupted, re-running `ingest-api` on the same destination will offer to resume from the last completed step.

### Commands

There are two ways to run the pipeline: the **Python CLI** (requires `ANTHROPIC_API_KEY`) and **Claude Code slash commands** (uses your active session, no key needed).

#### Python CLI (`uv run`)

```bash
# Full ingest (with resume support)
uv run ingest <source> <destination> [--rpm 60] [--max-binary-mb 50]

# Add a new subdirectory to an existing knowledge base
uv run ingest-add <source-subdir> <destination>

# Re-process files in a subdirectory that have changed
uv run ingest-amend <source-subdir> <destination>
```

#### Claude Code slash commands

Six commands are available from within a Claude Code session in this repo:

| Command | Backend | Description |
|---------|---------|-------------|
| `/ingest <source> <destination>` | Agent (parallel) | Full pipeline — Sonnet orchestrates, Haiku processes files in parallel, Opus builds the index |
| `/ingest-add <source-subdir> <destination>` | Agent (parallel) | Add a new subdirectory, re-reduce and re-index |
| `/ingest-amend <source-subdir> <destination>` | Agent (parallel) | Reset and re-process a subdirectory, re-reduce and re-index |
| `/ingest-api <source> <destination> [--rpm INT] [--max-binary-mb INT]` | Python CLI | Same as `uv run ingest`; requires `ANTHROPIC_API_KEY` |
| `/ingest-add-api <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT]` | Python CLI | Same as `uv run ingest-add`; requires `ANTHROPIC_API_KEY` |
| `/ingest-amend-api <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT]` | Python CLI | Same as `uv run ingest-amend`; requires `ANTHROPIC_API_KEY` |

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
