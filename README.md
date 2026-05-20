# agent-context-builder

A map-reduce pipeline that transforms a source directory tree of text and binary files into a structured, summarized, and indexed knowledge base. Designed for agent-driven execution with resumability, incremental updates, and full audit logging.

## Installation

**Prerequisites:** Python 3.13+ and an [Anthropic API key](https://console.anthropic.com/).

**With [`uv`](https://docs.astral.sh/uv/) (recommended):**
```bash
git clone https://github.com/jfdion/agent-context-builder
cd agent-context-builder
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
```

The three CLI commands are then available via `uv run <command>` or inside an activated `.venv`.

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
| 2 | **Summarize** | Generates a concise `_summary_<name>.md` for every extracted file using Claude Haiku. |
| 3 | **Reduce** | Bottom-up roll-up: for each directory, combines its summaries into a single `_reduce_<dir>.md`, then propagates upward to the root. |
| 4 | **Index** | Writes `index.md` — the canonical entry point for the knowledge base — covering directory structure, key concepts, and file purposes. |

**Supported file types:** `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cs`, `.sql`, `.html`, `.xml`, `.sh` and other text formats; `.pdf`, `.docx`, `.pptx`, `.xlsx`; `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`.

**Resumability:** if a run is interrupted, re-running `ingest` on the same destination will offer to resume from the last completed step.

### Commands

**Terminal (`uv run`):**
```bash
# Full ingest (with resume support)
uv run ingest <source> <destination> [--rpm 60] [--max-binary-mb 50]

# Add a new subdirectory to an existing knowledge base
uv run ingest-add <source-subdir> <destination>

# Re-process files in a subdirectory that have changed
uv run ingest-amend <source-subdir> <destination>
```

**Claude Code slash commands** (from within a Claude Code session in this repo):
```
/ingest <source> <destination> [--rpm INT] [--max-binary-mb INT]
/ingest-add <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT]
/ingest-amend <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT]
```

### Custom prompts

All Claude prompts are plain text files loaded at startup. Pass `--prompts-dir` to use a custom directory instead of the built-in `prompts/`:

```bash
uv run ingest <source> <destination> --prompts-dir ./my-prompts
```

The directory must contain all five prompt files:

| File | Step | Purpose |
|------|------|---------|
| `extract_text.txt` | 1 | Cleanup pass for text and binary-doc files with complex structure |
| `extract_image.txt` | 1 | Vision analysis prompt for image files |
| `summarize.txt` | 2 | Per-file summary generation |
| `reduce.txt` | 3 | Directory-level synthesis |
| `index.txt` | 4 | Top-level knowledge base index |

Copy the built-in `prompts/` directory as a starting point and edit as needed.
