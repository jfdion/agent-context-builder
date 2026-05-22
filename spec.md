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
uv run ingest <source> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE] [--prompts-dir PATH]
uv run ingest-add <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE] [--prompts-dir PATH]
uv run ingest-amend <source-subdir> <destination> [--rpm INT] [--max-binary-mb INT] [--locale LOCALE] [--prompts-dir PATH]
```

Defined via `[project.scripts]` in `pyproject.toml`. Each command maps to a Click entry point in `cli.py`. `ingest-add` and `ingest-amend` both require an explicit `destination` — they do **not** derive it from a single positional argument.

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

Symlinks encountered during the walk are excluded from the manifest, logged as `file_skipped` journal events with `"step": 0, "reason": "symlink"`, and printed as console warnings.

2. Classify each file (see File Classification below).
3. Create matching directory structure at `<destination>`.
4. Write `manifest.json`.

### File Classification

| Category | Extensions | Handler |
|----------|-----------|---------|
| `text` | `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.yml`, `.xml`, `.html`, `.java`, `.py`, `.js`, `.ts`, `.sql`, `.rst`, `.toml`, `.ini`, `.cfg`, `.sh`, `.bash`, `.zsh`, `.go`, `.rb`, `.rs`, `.c`, `.h`, `.cpp`, `.hpp`, `.cs`, `.kt`, `.swift`, `.svg` | Text extraction |
| `binary-doc` | `.pdf`, `.docx`, `.pptx`, `.xlsx` | OCR/parse extraction |
| `binary-image` | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | Visual analysis |
| `skip` (names) | `.DS_Store`, `.gitignore`, `.gitkeep` | Ignored |
| `skip` (suffixes) | `*.lock`, `*.pyc` | Ignored |
| `skip` (dirs) | `node_modules`, `.git`, `__pycache__`, `.ingest`, `.venv`, `venv`, `.tox`, `dist`, `build`, `.mypy_cache` | Pruned during walk |

The authoritative lists live in `config.py` as `TEXT_EXTENSIONS`, `BINARY_DOC_EXTENSIONS`, `BINARY_IMAGE_EXTENSIONS`, `SKIP_NAMES`, `SKIP_SUFFIXES`, and `SKIP_DIRS`.

SVG is classified as `text` (it is XML); it is copied verbatim without an API call unless complexity signals are detected.

Binary files (`binary-doc`, `binary-image`) larger than `--max-binary-mb` (default 50 MB, 0 = unlimited) are classified as `skipped-oversized` at Step 0 and never sent to Claude. A `file_skipped` journal event is written and the file is listed in the final summary.

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

Valid `status` values: `"pending"` (not yet processed), `"extracted"` (Step 1 complete, awaiting summarize), `"completed"` (summary written successfully), `"failed"` (error during processing, see `state.json.failed_files`), `"skipped-oversized"` (binary file exceeded `--max-binary-mb`).

---

## Step 1 — Extraction (Map)

**Input:** one file record from manifest  
**Output:** one `.md` file at destination path

### Text files — two-pass extraction

**Pass 1 (always): Python-only extraction**

Read raw text.

Encoding strategy: UTF-8 is attempted first. On `UnicodeDecodeError`, latin-1 is tried. If latin-1 also fails, UTF-8 with `errors='replace'` is used as final fallback.

If none of the following complexity signals are present, write the output directly without an API call:
- Tables detected (`|`-separated rows where the line starts with `|`)
- Aligned columns (two or more consecutive spaces appearing mid-line, on 3 or more lines)
- Repeated lines (same non-empty line of more than 10 characters appearing 3 or more times)

**Large file chunking**: If the raw text exceeds 500,000 bytes (UTF-8-encoded), the file is treated as complex regardless of complexity signals and the two-pass check is skipped. The text is split into chunks of ≤ 500,000 bytes on line boundaries; Claude is called on each chunk; the outputs are concatenated before writing the output file.

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
- **PDF**: `pymupdf` (`page.get_text("text")`); detect repeated header and footer lines via frequency analysis (line appearing on ≥80% of pages = header/footer candidate, stripped before Claude pass). If the extracted text exceeds 200 KB, split into 200 KB chunks, call Claude on each chunk, and concatenate the results before writing the output file.
- **DOCX**: `python-docx`; header and footer sections are explicit in the format and stripped directly.
- **PPTX**: `python-pptx`; text extracted per slide; speaker notes appended under `## Speaker Notes`. Slides that contain no text (image-only slides) are logged as a `file_skipped` journal event with `"reason": "image-only slide"` and skipped — they do not produce an empty block.
- **XLSX**: `openpyxl`; each sheet extracted as a markdown table under `## {Sheet Name}`. Empty cells render as an empty table cell. Merged cells: the value is written in the first (top-left) cell of the merge; all other cells in the merge render empty.

Claude prompt (same as text Pass 2 above, applied to extracted text).

### Binary-image files

Sent directly to Haiku vision (base64-encoded). No local pre-processing beyond reading the file.

```
Analyze this image.
1. Identify the diagram type: one of [generic-schema, flowchart, wireframe, chart-data, screenshot, photo, other].
2. Write a high-level summary (3–5 sentences) describing the content and purpose.
3. Produce the best available markdown representation (table, ASCII art, or description paragraph).

Output format exactly:
## Diagram Type
{type}

## Summary
{summary}

## Representation
{markdown block}
```

### Output file header (all categories)

Every extracted `.md` file starts with:

```markdown
---
source: {source_path}
category: {category}
locale: {bcp47-or-und}
extracted_at: {ISO8601}
ingest_id: {sha256}
---
```

The `locale` field is set by `detect_locale()` in `extract.py` (stopword-frequency comparison; currently distinguishes `fr`, `en`, or `und`). See § "Locale Handling" below.

---

## Step 2 — Summary (Map)

**Input:** one extracted `.md` file
**Output:** `_summary_{stem}.md` in same directory as the extracted file

Files are processed in manifest insertion order (not bottom-up). Step 3 is the bottom-up phase.

The model receives `prompts/summarize.txt` and is instructed to return prose + a `## Key Topics` bullet list with **no front matter** (the pipeline prepends it). The summary is written in the dominant language of the extracted content.

Tags are parsed from the `## Key Topics` block by `reduce._extract_tags()` after the call returns. The `locale` is carried over from the extract file's front matter via `_extract_locale()`.

Output file format (front matter prepended by `summarize._build_front_matter`, body returned by the model):

```markdown
---
type: summary
source_summary: {extracted_md_path}
locale: {bcp47-or-und}
tags:
  - {tag1}
  - {tag2}
summarized_at: {ISO8601}
---

{prose summary, 3–8 sentences}

## Key Topics
- {topic 1}
- {topic 2}
```

If no `## Key Topics` block is detected the front matter records `tags: []`.

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

The model receives `prompts/reduce.txt` plus an input block containing every `_summary_*.md` and child `_reduce_*.md` file in the directory, prefixed with a `Language:` line set by `_dominant_locale()` (most-frequent non-`und` locale across the inputs). The model returns prose + `## Components` + `## Key Topics` with **no front matter**.

Tags in the front matter are the union (insertion-order, deduplicated) of all `tags:` blocks across the input files, computed in Python by `_parse_front_matter_tags()`.

Output file format (front matter prepended by `reduce._build_reduce_front_matter`, body returned by the model):

```markdown
---
type: reduce
directory: {rel_dir_path}
locale: {bcp47-or-und}
sources:
  - {relative_path_to_input_1}
  - {relative_path_to_input_2}
tags:
  - {tag1}
  - {tag2}
reduced_at: {ISO8601}
---

{prose synthesis, 4–10 sentences}

## Components
- {file or subdir}: {one-line description}

## Key Topics
- {topic 1}
- {topic 2}
```

---

## Step 4 — Index

**Input:** all top-level `_reduce_*.md` files at `<destination>` root (including `_reduce_root.md`)
**Output:** `index.md` at `<destination>` root

The model receives `prompts/index.txt` plus the concatenated content of every top-level reduce file. It returns prose only — front matter is prepended by `index.run_index_step`. After the model returns, `_build_non_processed_section()` appends a `## Non-Processed Documents` section listing any files whose manifest status is `skipped-oversized` or `failed`; if no such files exist the section is omitted.

Output file format:

```markdown
---
type: index
generated_at: {ISO8601}
---

## Overview
{4–8 sentences describing the knowledge base}

## Directory Structure
- {top-level dir}: {one-line description}

## Key Concepts
- {concept 1}
- {concept 2}

## Quick Reference
| File | Path | Purpose |
|------|------|---------|
| ... | ... | ... |

## Non-Processed Documents   ← appended by Python only if any exist
| File | Reason | Size |
|------|--------|------|
| ... | oversized | 84.2 MB |
```

If no top-level reduce files exist (empty knowledge base) the index step is skipped and a warning is added to the run's error list.

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
  "total_output_tokens": 0,
  "max_binary_mb": 50,
  "locale": "und"
}
```

`max_binary_mb` is reused on resume; an explicit `--max-binary-mb` CLI flag overrides it and is written back. `locale` is set during Step 2/3 boundary by `_resolve_locale()` (see § "Locale Handling").

### .ingest/journal.jsonl

One JSON object per line. Append-only.

```json
{"ts": "ISO8601", "cmd": "ingest", "event": "step_start", "step": 0, "detail": null}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_skipped", "step": 0, "source": "/source/link.py", "reason": "symlink"}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_skipped", "step": 1, "source": "/source/deck.pptx", "reason": "image-only slide", "slide": 3}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_skipped", "step": 1, "source": "/source/large.bin", "reason": "oversized", "size_bytes": 104857600}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_extracted", "step": 1, "source": "/source/moduleA/doc1.pdf", "dest": "/context/moduleA/doc1.md", "agent": "haiku", "tokens": 1240}
{"ts": "ISO8601", "cmd": "ingest", "event": "file_summarized", "step": 2, "source": "/context/moduleA/doc1.md", "dest": "/context/moduleA/_summary_doc1.md", "agent": "haiku", "tokens": 340}
{"ts": "ISO8601", "cmd": "ingest", "event": "api_call", "input_tokens": 3200, "output_tokens": 840, "cumulative_tokens": 42100}
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

When `state.json` exists and `current_step < 4`, the user sees a yes/no Click confirm prompt (default = yes):

```
Found existing run at step {N}. Resume? [Y/n]:
```

- yes → resume from pending files and incomplete steps
- no → start a fresh run (the existing state is discarded in memory; manifest/state are overwritten as Step 0 runs)

There is no third "amend" branch on this prompt — re-amending is done by invoking the separate `ingest-amend` command.

If `--rpm` is explicitly passed on resume, it overrides the value stored in `state.json` and the new value is written back. If omitted, the stored value is used. Same rule applies to `--max-binary-mb`.

---

## Commands

### `uv run ingest <source> <destination>`

Full pipeline execution from scratch.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--rpm INT` | `60` | Maximum API requests per minute. Sleep interval = `60 / rpm` seconds between calls. |
| `--max-binary-mb INT` | `50` | Binary files (`binary-doc`, `binary-image`) larger than this threshold are skipped. Set to `0` to disable the limit. |
| `--locale LOCALE` | (auto) | Force the synthesis locale (e.g. `en`, `fr`, `fr_CA`). Skips auto-detection and interactive prompt. |
| `--prompts-dir PATH` | bundled `prompts/` | Directory containing the five prompt `.txt` files. |

```
0. Abort with a clear error message if ANTHROPIC_API_KEY is not set.
1. Validate source path exists.
2. Create destination directory.
3. Initialize .ingest/manifest.json, state.json, journal.jsonl.
4. Execute Step 0 → manifest population (applies --max-binary-mb classification).
5. Execute Step 1 → all files sequentially, throttled to --rpm.
6. Execute Step 2 → bottom-up per directory.
7. Execute Step 3 → reduce loop until root.
8. Execute Step 4 → index.md.
9. Write final journal entry: pipeline_complete.
10. Print summary: files processed, tokens used, elapsed time, errors, skipped-oversized files.
```

### `uv run ingest-add <source-subdir> <destination>`

Adds a new subdirectory from the original source tree to an existing context.

`<source-subdir>` must be a subdirectory of the `source_root` recorded in `<destination>/.ingest/manifest.json`. The relative path under `source_root` is computed and mirrored under `<destination>` to obtain the destination subdirectory.

**Flags:** same `--rpm`, `--max-binary-mb`, `--locale`, and `--prompts-dir` flags as `ingest`.

```
Pre-conditions:
- destination/.ingest/manifest.json must exist
- <source> prefix must match manifest.source_root
- Files already present in manifest (matched by source_path) are skipped — no duplicate records are appended.
  A warning is printed for each skipped duplicate; use ingest-amend to reprocess them.

1. Resolve destination path: dest = source.replace(source_root, destination_root)
2. Create dest directory if absent.
3. Classify new files; append records to manifest.json with status "pending" for files not already present.
4. Execute Step 1 for new files only, throttled to --rpm.
5. Execute Step 2 for new files only.
6. Re-execute Step 3 upward from the lowest affected directory to root via run_reduce_from_dir(start_dir).
7. Re-execute Step 4 → regenerate index.md.
8. Append journal entries; update state.json.last_updated.
```

Journal event:
```json
{"ts": "ISO8601", "cmd": "ingest-add", "event": "source_added", "new_source": "./source/moduleC", "derived_dest": "./context/moduleC", "files_added": 7}
```

### `uv run ingest-amend <source-subdir> <destination>`

Full reset and reprocessing of a source directory. SHA-256 hashes are checked first: files whose content hash still matches the manifest record are recorded as `file_unchanged` journal events and skipped; only changed (or missing) files are reprocessed.

**Flags:** same `--rpm`, `--max-binary-mb`, `--locale`, and `--prompts-dir` flags as `ingest`.

```
Pre-conditions:
- destination/.ingest/manifest.json must exist.
- At least one manifest record must have a source_path under <source>.
  If no records match, abort with: "No manifest records found for <source>. Nothing to amend."

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

## Locale Handling

Every output file written by Steps 1–3 carries a `locale:` field in its YAML front matter. The pipeline both detects per-file locales and resolves a single synthesis locale for the run.

### Detection — Step 1

`extract.detect_locale(text)` scores the extracted text against built-in French and English stopword sets, returning `"fr"`, `"en"`, or `"und"` (undetermined). The result is stamped into the extract file's front matter. Detection is currently fr/en only; other languages always resolve to `"und"` until additional stopword sets are added.

### Propagation — Step 2

`summarize.summarize_file` reads the `locale:` field from the extract file's front matter (via `_extract_locale`) and copies it verbatim into the corresponding `_summary_*.md`.

### Resolution — between Steps 2 and 3

After all summaries are written, `pipeline._resolve_locale()` decides the run's synthesis locale in this order:

1. **`--locale LOCALE` CLI flag** — if provided, it is used directly. No scan, no prompt.
2. **Single detected locale** — if all `_summary_*.md` files agree on one non-`und` locale, it is used automatically.
3. **Multiple detected locales** — `ui.confirm_locale()` prompts the user with the per-locale file counts and the dominant locale as default; an `auto` choice resolves to `"und"`.
4. **No locale detected** — defaults to `"und"`.

The chosen value is stored in `state.locale` and saved to `state.json`.

### Application — Step 3

`reduce._dominant_locale()` computes a per-directory locale from the inputs' `locale:` fields (most-frequent non-`und`; falls back to `"und"`). That value is written into the reduce file's front matter and also passed to the model as `Language: {locale}` in the user content block, instructing the model to synthesize in that language without translating source content.

### Application — Step 4

The index step does not currently consume `state.locale` directly; `prompts/index.txt` instructs the model to detect the dominant language from the reduce inputs and write the index in that language. The `--locale` flag therefore primarily affects Step 3 outputs.

---

## Implementation Notes

### Python dependencies

```
pymupdf          # PDF extraction
python-docx      # DOCX extraction
python-pptx      # PPTX extraction
openpyxl         # XLSX extraction
anthropic        # Claude API (Sonnet + Haiku)
click            # CLI
rich             # progress bar
```

### Concurrency model

All API calls are strictly sequential with a configurable sleep between each call. No parallel workers. Steps execute in order; within a step, files are processed one at a time.

The `--rpm` flag (default `60`) controls throughput. Sleep interval is computed as `60.0 / rpm` seconds. The `rpm` value is recorded in `state.json` at run start and reused on resume (CLI override takes precedence). The `--max-binary-mb` value is also recorded in `state.json` and reused on resume; a CLI override takes precedence.

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

### Text calls — `call_claude()`

```python
def call_claude(client, model, system_prompt, user_content: str, max_tokens, rpm, state, dest_root):
    sleep_interval = 60.0 / rpm
    for attempt in range(3):
        time.sleep(sleep_interval)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_content}]
            )
            # log tokens to journal and persist state
            state.total_input_tokens += response.usage.input_tokens
            state.total_output_tokens += response.usage.output_tokens
            append_journal(dest_root, journal_event("api_call", model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cumulative_tokens=state.total_input_tokens + state.total_output_tokens))
            save_state(dest_root, state)
            return response.content[0].text
        except RateLimitError:
            if attempt == 2:
                raise
            sleep_interval = 2 * (60.0 / rpm)
        except APIStatusError as e:
            if e.status_code < 500 or attempt == 2:  # only retry on 5xx
                raise
            sleep_interval = 2 * (60.0 / rpm)
```

### Vision calls — `call_claude_vision()`

Used exclusively for `binary-image` files. Takes raw image bytes and encodes them as base64 inline.

```python
def call_claude_vision(client, model, system_prompt, image_data: bytes, media_type: str,
                       prompt_text: str, max_tokens, rpm, state, dest_root):
    b64_data = base64.standard_b64encode(image_data).decode("ascii")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}},
        {"type": "text", "text": prompt_text},
    ]
    # same retry/logging/save_state pattern as call_claude(), using multipart content
```

Media type mapping for supported extensions: `png → image/png`, `jpg/jpeg → image/jpeg`, `gif → image/gif`, `webp → image/webp`.

### Retry and logging rules (both functions)

- On `RateLimitError`: retry up to 2 times (3 attempts total) with doubled sleep interval.
- On `APIStatusError`: retry only on HTTP 5xx responses. 4xx errors (including 400, 401, 403) are not retried and propagate immediately.
- After each successful call: append an `api_call` journal event (model, input_tokens, output_tokens, cumulative_tokens) and call `save_state()`.
- The third failure propagates and is caught by `run_extract_step` as a `file_error` journal event.

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
