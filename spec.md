# Knowledge Base Ingestion Pipeline

## Overview

A map-reduce pipeline that transforms a source directory tree of text and binary files into a structured, summarized, and indexed knowledge base. Designed for agent-driven execution with resumability, incremental updates, and full audit logging.

---

## Architecture

```
/source                          /context
├── moduleA/                     ├── moduleA/
│   ├── doc1.pdf        ──map──► │   ├── doc1.md
│   ├── schema.png               │   ├── schema.md
│   └── notes.txt                │   ├── notes.md
├── moduleB/                     │   ├── _summary_doc1.md
│   └── guide.docx               │   ├── _summary_schema.md
│                                │   └── _summary_notes.md
│                                ├── moduleB/
│                                │   ├── guide.md
│                                │   └── _summary_guide.md
│                                ├── _reduce_moduleA.md
│                                ├── _reduce_moduleB.md
│                                ├── _reduce_root.md
│                                ├── index.md
│                                └── .ingest/
│                                    ├── journal.jsonl
│                                    ├── manifest.json
│                                    └── state.json
```

`_reduce_{dirname}.md` files are placed **one level above** the directory they summarize, co-located with sibling reduce files.

---

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Extracted content | `{original_stem}.md` | `guide.md` |
| Per-file summary | `_summary_{original_stem}.md` | `_summary_guide.md` |
| Directory reduce | `_reduce_{dirname}.md` | `_reduce_moduleA.md` |
| Root reduce | `_reduce_root.md` | `_reduce_root.md` |
| Entry point | `index.md` | `index.md` |
| Journal | `.ingest/journal.jsonl` | — |
| Manifest | `.ingest/manifest.json` | — |
| State | `.ingest/state.json` | — |

---

## Execution Model

| Role | Technology | Responsibility |
|------|-----------|----------------|
| **Orchestrator** | Python | State machine, routing, journal, file I/O, throttling |
| **Extractor** | Python + Haiku | Step 1 — local text extraction; Haiku only when complex structures detected or for image analysis |
| **Summarizer** | Haiku | Step 2/3 — `_summary_*.md`, `_reduce_*.md` |
| **Indexer** | Sonnet | Step 4 — `index.md` |

All control flow is deterministic Python. Claude API calls are content-only operations with no decision authority. `time.sleep(1)` enforced before every API call.

---

## CLI Entry Points

### Installation

```
uv run ingest <source> <destination>
uv run ingest-add <source>
uv run ingest-amend <source>
```

Defined via `[project.scripts]` in `pyproject.toml`. Each command maps to a Click entry point in the package.

### Claude Code slash commands

`.claude/commands/ingest.md`, `.claude/commands/ingest-add.md`, `.claude/commands/ingest-amend.md` wrap the `uv run` commands for in-session invocation from Claude Code.

---

## Prompt System

All Claude prompts are stored as editable text files under `prompts/` and loaded at Python startup. Users may tune these files directly without modifying source code.

```
prompts/
├── extract_text.txt
├── extract_image.txt
├── summarize.txt
├── reduce.txt
└── index.txt
```

Prompt caching is applied to system prompts wherever the same prompt is reused across multiple API calls within a run (extraction, summary, reduce phases).

```python
system=[{"type": "text", "text": prompt_text, "cache_control": {"type": "ephemeral"}}]
```

---

## Progress and Error Output

- A `rich` progress bar shows the current step and the file being processed.
- Errors are caught per-file, logged to `journal.jsonl` as `file_error` events, and recorded in `state.json.failed_files`.
- The pipeline continues after non-fatal errors.
- On completion, a final summary is printed: files processed, tokens used, elapsed time, and a list of failed files with their error messages.

---

## Step 0 — Source Analysis

**Input:** `<source>` root path  
**Output:** mirrored directory tree at `<destination>`, `.ingest/manifest.json`

### Process

1. Walk `<source>` recursively, collect all file paths.
2. Classify each file (see File Classification below).
3. Create matching directory structure at `<destination>`.
4. Write `manifest.json`.

### File Classification

| Category | Extensions | Handler |
|----------|-----------|---------|
| `text` | `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.yml`, `.xml`, `.html`, `.java`, `.py`, `.js`, `.ts`, `.sql`, `.rst` | Text extraction |
| `binary-doc` | `.pdf`, `.docx`, `.pptx`, `.xlsx` | OCR/parse extraction |
| `binary-image` | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg` | Visual analysis |
| `skip` | `.DS_Store`, `.gitignore`, `*.lock`, `node_modules/**` | Ignored |

### manifest.json schema

```json
{
  "version": "1.0",
  "source_root": "/source",
  "destination_root": "/context",
  "created_at": "ISO8601",
  "files": [
    {
      "id": "sha256:abcdef",
      "source_path": "/source/moduleA/doc1.pdf",
      "destination_path": "/context/moduleA/doc1.md",
      "category": "binary-doc",
      "size_bytes": 204800,
      "mtime": "ISO8601",
      "status": "pending"
    }
  ]
}
```

---

## Step 1 — Extraction (Map)

**Input:** one file record from manifest  
**Output:** one `.md` file at destination path

### Text files — two-pass extraction

**Pass 1 (always): Python-only extraction**

Read raw text. If none of the following complexity signals are present, write the output directly without an API call:
- Tables detected (delimiter-aligned columns, `|`-separated rows)
- Repeated header/footer lines across pages or sections
- Embedded image references

**Pass 2 (conditional): Claude cleanup**

When complexity signals are detected, send the extracted text to Haiku with:

```
Extract the full textual content of the following file verbatim.
Rules:
- Exclude headers and footers that are repeated page decorations (page numbers, document name banners).
- If any footer contains a copyright or legal notice, append it once at the very end under a ## Legal section.
- Do NOT summarize, interpret, or alter the content.
- Output format: raw markdown with no preamble.

Source: {source_path}
Content: {raw_text}
```

### Binary-doc files (PDF, DOCX, PPTX, XLSX)

Always performs Python extraction first; always sends to Claude for cleanup (binary formats reliably contain complex structure).

Local extraction per format:
- **PDF**: `pymupdf` (`page.get_text("text")`); detect repeated footer lines via frequency analysis (line on ≥80% of pages = footer candidate)
- **DOCX**: `python-docx`; footer sections are explicit
- **PPTX**: `python-pptx`; slide text extracted; speaker notes appended under `## Speaker Notes`
- **XLSX**: `openpyxl`; each sheet extracted as a markdown table; sheet names used as `## {Sheet Name}` headings

Claude prompt (same as text Pass 2 above, applied to extracted text).

### Binary-image files

Sent directly to Haiku vision (base64-encoded). No local pre-processing beyond reading the file.

```
Analyze this image.
1. Identify the diagram type: one of [generic-schema, relational-data-model, uml-class, uml-sequence, uml-activity, uml-component, flowchart, wireframe, chart-data, screenshot, photo, other].
2. Write a high-level summary (3–5 sentences) describing the content and purpose.
3. If the diagram type supports PlantUML representation, produce a faithful PlantUML block.
   Supported types: relational-data-model, uml-class, uml-sequence, uml-activity, uml-component, flowchart.
4. If PlantUML is not applicable, produce the best available markdown representation (table, ASCII, description).

Output format exactly:
## Diagram Type
{type}

## Summary
{summary}

## Representation
{plantuml or markdown block}
```

PlantUML blocks are validated with the `plantuml` library before writing. Validation failures are logged to `journal.jsonl` as `plantuml_warning` events; the unvalidated block is written regardless.

### Output file header (all categories)

Every extracted `.md` file starts with:

```markdown
---
source: {source_path}
category: {category}
extracted_at: {ISO8601}
ingest_id: {sha256}
---
```

---

## Step 2 — Summary (Map)

**Input:** one extracted `.md` file  
**Output:** `_summary_{stem}.md` in same directory as the extracted file

Processed bottom-up: deepest directory level first.

Prompt contract:

```
Given the following extracted document, produce a summary.
Rules:
- Preserve all essential facts, definitions, rules, and relationships.
- Length: 10–20% of source, minimum 3 sentences.
- Produce a tags list: 5–15 lowercase keywords covering theme, domain, and scope.
- The summary must reference the source file as the source of truth.

Output format exactly:
---
source_summary: {extracted_md_path}
tags: [tag1, tag2, ...]
summarized_at: {ISO8601}
---

## Summary
{summary text}
```

---

## Step 3 — Reduce

**Input:** all `_summary_*.md` files within one directory + any `_reduce_*.md` files from subdirectories at that level  
**Output:** `_reduce_{dirname}.md` placed **one level above** the directory being reduced

Repeat until only `_reduce_root.md` remains.

### Reduce traversal order

```
deepest dirs first → _reduce_{dir}.md placed at parent level
...repeat upward...
last pass → _reduce_root.md at destination root
```

Example for a two-level tree:

```
moduleA/_summary_*.md  →  _reduce_moduleA.md  (at context root)
moduleB/_summary_*.md  →  _reduce_moduleB.md  (at context root)
_reduce_moduleA.md + _reduce_moduleB.md  →  _reduce_root.md  (at context root)
```

Prompt contract:

```
Synthesize the following summaries into a single directory-level summary.
Rules:
- Identify cross-cutting themes and relationships between documents.
- Mention each summarized file by name with a relative markdown link.
- Produce a merged tags list (union, deduplicated, sorted).
- Do not repeat information already captured at the sub-summary level verbatim; synthesize.

Output format exactly:
---
type: reduce
directory: {dir_name}
sources:
  - {relative_path_to_summary_1}
  - {relative_path_to_summary_2}
tags: [tag1, tag2, ...]
reduced_at: {ISO8601}
---

## Directory Summary: {dir_name}
{synthesis text}

## Contents
| File | Tags |
|------|------|
| [_summary_doc1.md]({rel_path}) | tag1, tag2 |
```

---

## Step 4 — Index

**Input:** all first-level `_reduce_*.md` files + `_reduce_root.md`  
**Output:** `index.md` at `<destination>` root

Prompt contract:

```
Given the root-level reduce file and all first-level directory reduce files, produce a knowledge base index.
Rules:
- Group content by theme, not by directory structure (themes emerge from tags).
- Each theme entry links to the most relevant reduce or summary file.
- Include a directory tree overview section.
- Remain factual; no editorial commentary.

Output format exactly:
# Knowledge Base Index

Generated: {ISO8601}
Source root: {source_root}

## Directory Overview
{ASCII tree of /context with one-line description per node}

## Thematic Index
### {Theme 1}
- [{descriptor}]({path}) — {one sentence}

### {Theme 2}
...

## Top-Level References
| Directory | Reduce File | Primary Tags |
|-----------|-------------|--------------|
| moduleA | [_reduce_moduleA.md](...) | tag1, tag2 |
```

---

## State and Journal

### .ingest/state.json

Tracks pipeline progress for resumability.

```json
{
  "pipeline_version": "1.0",
  "command": "ingest",
  "source_root": "/source",
  "destination_root": "/context",
  "started_at": "ISO8601",
  "last_updated": "ISO8601",
  "rpm": 60,
  "current_step": 2,
  "completed_steps": [0, 1],
  "pending_files": ["sha256:abc", "sha256:def"],
  "completed_files": ["sha256:xyz"],
  "failed_files": [],
  "total_input_tokens": 0,
  "total_output_tokens": 0
}
```

### .ingest/journal.jsonl

One JSON object per line. Append-only.

```json
{"ts": "ISO8601", "cmd": "ingest", "event": "step_start", "step": 0, "detail": null}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_extracted", "step": 1, "source": "/source/moduleA/doc1.pdf", "dest": "/context/moduleA/doc1.md", "agent": "haiku", "tokens": 1240}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_summarized", "step": 2, "source": "/context/moduleA/doc1.md", "dest": "/context/moduleA/_summary_doc1.md", "agent": "haiku", "tokens": 340}
{"ts": "ISO8601", "cmd": "ingest", "event": "api_call", "input_tokens": 3200, "output_tokens": 840, "cumulative_tokens": 42100}
{"ts": "ISO8601", "cmd": "ingest", "event": "plantuml_warning", "file": "/context/moduleA/schema.md", "detail": "syntax validation failed"}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_error", "step": 1, "source": "/source/moduleA/doc1.pdf", "error": "..."}
{"ts": "ISO8601", "cmd": "ingest", "event": "step_complete", "step": 4, "detail": "index.md written"}
{"ts": "ISO8601", "cmd": "ingest", "event": "pipeline_complete", "total_files": 42, "total_tokens": 98400, "elapsed_seconds": 312}
```

---

## Resumability

Existing output files are considered valid and are not reprocessed unless explicitly requested (via `/ingest-amend`). On resume:

- Files with `status: "completed"` in `manifest.json` are skipped entirely.
- Files with `status: "pending"` or `status: "failed"` are (re)processed.
- Completed steps (recorded in `state.json.completed_steps`) are skipped.

When `state.json` exists and `current_step < 4`, the user is prompted:

```
Existing ingest state detected (step {N}/4). Resume? [y/N/amend]
```

- `y` → resume from pending files
- `N` → abort
- `amend` → equivalent to `/ingest-amend <source>`

---

## Commands

### `uv run ingest <source> <destination>`

Full pipeline execution from scratch.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--rpm INT` | `60` | Maximum API requests per minute. Sleep interval = `60 / rpm` seconds between calls. |

```
1. Validate source path exists.
2. Create destination directory.
3. Initialize .ingest/manifest.json, state.json, journal.jsonl.
4. Execute Step 0 → manifest population.
5. Execute Step 1 → all files sequentially, throttled to --rpm.
6. Execute Step 2 → bottom-up per directory.
7. Execute Step 3 → reduce loop until root.
8. Execute Step 4 → index.md.
9. Write final journal entry: pipeline_complete.
10. Print summary: files processed, tokens used, elapsed time, errors.
```

### `uv run ingest-add <source>`

Adds a new subdirectory from the original source tree to an existing context.

`<source>` is an absolute path rooted at the current working directory. It must be a subdirectory of the `source_root` recorded in `manifest.json`. The destination path is derived automatically by replacing the `source_root` prefix with `destination_root`.

**Flags:** same `--rpm` flag as `ingest`.

```
Pre-conditions:
- destination/.ingest/manifest.json must exist
- <source> prefix must match manifest.source_root

1. Resolve destination path: dest = source.replace(source_root, destination_root)
2. Create dest directory if absent.
3. Classify new files; append records to manifest.json with status "pending".
4. Execute Step 1 for new files only, throttled to --rpm.
5. Execute Step 2 for new files only.
6. Re-execute Step 3 upward from the lowest affected directory to root (_reduce_root.md).
7. Re-execute Step 4 → regenerate index.md.
8. Append journal entries; update state.json.last_updated.
```

Journal event:
```json
{"ts": "ISO8601", "cmd": "ingest-add", "event": "source_added", "new_source": "./source/moduleC", "derived_dest": "./context/moduleC", "files_added": 7}
```

### `uv run ingest-amend <source>`

Full reset and reprocessing of a source directory.

**Flags:** same `--rpm` flag as `ingest`.

```
1. Delete all generated files under destination that originate from <source>:
   - Match via manifest.json source_path prefix.
   - Remove extracted .md, _summary_*.md, affected _reduce_*.md, index.md.
2. Reset affected manifest.json records to status: "pending".
3. Reset state.json to step 0 for affected scope.
4. Append journal entry: amend_start.
5. Re-execute Steps 1–4 for affected scope.
6. Full index.md regeneration.
```

Journal event:
```json
{"ts": "ISO8601", "cmd": "ingest-amend", "event": "amend_start", "scope": "/source/moduleA", "files_reset": 12}
```

---

## Implementation Notes

### Python dependencies

```
pymupdf          # PDF extraction
python-docx      # DOCX extraction
python-pptx      # PPTX extraction
openpyxl         # XLSX extraction
anthropic        # Claude API (Sonnet + Haiku)
Pillow           # image pre-processing
plantuml         # PlantUML syntax validation (non-blocking)
click            # CLI
rich             # progress bar
```

### Concurrency model

All API calls are strictly sequential with a configurable sleep between each call. No parallel workers. Steps execute in order; within a step, files are processed one at a time.

The `--rpm` flag (default `60`) controls throughput. Sleep interval is computed as `60.0 / rpm` seconds. The `rpm` value is recorded in `state.json` at run start and reused on resume.

```python
def call_api(client, rpm: int, **kwargs):
    time.sleep(60.0 / rpm)
    response = client.messages.create(**kwargs)
    return response
```

### Token logging

No truncation. Input and output tokens are logged to journal per call. Running totals tracked in `state.json`.

| Operation | Model | Max tokens out |
|-----------|-------|----------------|
| Text extraction (Claude pass) | Haiku | 4096 |
| Image analysis | Haiku (vision) | 1024 |
| Summary | Haiku | 1024 |
| Reduce | Haiku | 2048 |
| Index | Sonnet | 4096 |

### Error handling

- Per-file failures are logged to journal as `file_error` events and recorded in `state.json.failed_files`.
- Pipeline continues on non-fatal errors.
- `failed_files` are printed in the final summary with their error messages.
- A `uv run ingest-amend` on the source re-attempts failed files.

---

## API Call Contract

Each Claude call is a pure content transformation. Python constructs the full prompt from files under `prompts/` and writes the output. No state is inferred by the model.

```python
def call_claude(client, model, system_prompt, user_content, max_tokens, rpm: int):
    time.sleep(60.0 / rpm)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}]
    )
    log_tokens(response.usage.input_tokens, response.usage.output_tokens)
    return response.content[0].text
```

Models used:
- `claude-haiku-4-5-20251001` — extraction cleanup, image analysis, summary, reduce
- `claude-sonnet-4-6` — index only

---

## Directory: .ingest/

This hidden directory is the agent's source of truth for evolving the context over time. It must be preserved across all operations. An agent resuming work reads:

1. `manifest.json` → file inventory and status
2. `state.json` → current pipeline position
3. `journal.jsonl` → full audit trail

These three files contain sufficient information to reconstruct pipeline state, identify what was processed, detect drift between source and context, and plan incremental updates.
