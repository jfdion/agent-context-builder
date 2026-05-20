# agent-context-builder

A map-reduce pipeline that transforms a source directory tree of text and binary files into a structured, summarized, and indexed knowledge base. Designed for agent-driven execution with resumability, incremental updates, and full audit logging.

## Installation

**Prerequisites:** Python 3.13+ and an [Anthropic API key](https://console.anthropic.com/).

**With `uv` (recommended):**
```bash
git clone https://github.com/your-org/agent-context-builder
cd agent-context-builder
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
```

The three CLI commands (`ingest`, `ingest-add`, `ingest-amend`) are then available in the `.venv`.

## Pipeline Overview

`ingest` runs a five-step map-reduce pipeline and writes a structured knowledge base to the destination directory.

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

```bash
# Full ingest (with resume support)
ingest <source> <destination> [--rpm 60] [--max-binary-mb 50]

# Add a new subdirectory to an existing knowledge base
ingest-add <source-subdir> <destination>

# Re-process files in a subdirectory that have changed
ingest-amend <source-subdir> <destination>
```
