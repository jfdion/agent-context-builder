Orchestrate the full ingest pipeline in parallel. You (Sonnet) orchestrate; Haiku sub-agents process files; Opus builds the index.

**Arguments:** $ARGUMENTS — parse SOURCE (first arg) and DESTINATION (second arg), resolve both to absolute paths.

---

## Phase 0 — Setup (you, Sonnet)

Do this work yourself with Bash, Read, and Write tools.

1. `mkdir -p DESTINATION/.ingest`

2. Walk SOURCE recursively. Skip any path containing these directory names: `node_modules .git __pycache__ .ingest .venv venv .tox dist build .mypy_cache`. Skip files named `.DS_Store`, `.gitignore`, `.gitkeep` or with extensions `.lock` `.pyc`.

3. Classify each remaining file by extension (case-insensitive):
   - **text**: `.txt .md .csv .json .yaml .yml .xml .html .java .py .js .ts .sql .rst .toml .ini .cfg .sh .bash .zsh .go .rb .rs .c .h .cpp .hpp .cs .kt .swift .svg`
   - **binary-image**: `.png .jpg .jpeg .gif .webp`
   - **binary-doc**: `.pdf .docx .pptx .xlsx` — mark status `pending`; extracted via `extract_doc` CLI
   - Unrecognized extensions: skip entirely

4. Mirror the SOURCE directory tree under DESTINATION: for each subdirectory found recursively under SOURCE, create the corresponding path under DESTINATION with `mkdir -p`.

5. For each classifiable file: `sha256sum FILE` and `stat -c%s FILE` for size.

6. Write `DESTINATION/.ingest/manifest.json`:
```json
{
  "version": "1.0",
  "source_root": "<SOURCE absolute>",
  "destination_root": "<DESTINATION absolute>",
  "created_at": "<ISO8601>",
  "files": [
    {
      "id": "sha256:<hash>",
      "source_path": "<absolute source path>",
      "destination_path": "<DESTINATION>/<relative path with .md suffix>",
      "category": "<text|binary-image|binary-doc>",
      "size_bytes": 0,
      "mtime": "<ISO8601 from stat>",
      "status": "pending"
    }
  ]
}
```
Note: `destination_path` always uses `.md` as the extension regardless of the source extension.

7. Write `DESTINATION/.ingest/state.json`:
```json
{
  "pipeline_version": "1.0",
  "command": "ingest",
  "source_root": "<SOURCE>",
  "destination_root": "<DESTINATION>",
  "started_at": "<ISO8601>",
  "last_updated": "<ISO8601>",
  "rpm": 0,
  "current_step": 0,
  "completed_steps": [0],
  "pending_files": [],
  "completed_files": [],
  "failed_files": [],
  "total_input_tokens": 0,
  "total_output_tokens": 0,
  "max_binary_mb": 50
}
```

8. Start `DESTINATION/.ingest/journal.jsonl` with the step_complete event:
```json
{"ts": "<ISO8601>", "event": "step_complete", "step": 0, "file_count": <N>, "cmd": "ingest"}
```

9. Read and hold in memory the five prompt files — you will embed their content into sub-agent instructions:
   - `prompts/extract_text.txt` → store as EXTRACT_TEXT_PROMPT
   - `prompts/extract_image.txt` → store as EXTRACT_IMAGE_PROMPT
   - `prompts/summarize.txt` → store as SUMMARIZE_PROMPT
   - `prompts/reduce.txt` → store as REDUCE_PROMPT
   - `prompts/index.txt` → store as INDEX_PROMPT

---

## Phase 1 — Extract (parallel Haiku agents)

Collect all manifest records with status `pending`. Group them into batches of at most 10 files each.

Spawn ALL batches as Haiku agents **simultaneously in a single message** (one Agent tool call per batch, all in the same response). Use `model="haiku"` for every extract agent.

Construct each agent's prompt by filling in this template:

---
You are an extraction sub-agent. Use your Bash, Read, and Write tools to process each file in the list below.

**Text extraction rules:**
{{EXTRACT_TEXT_PROMPT}}

**Image analysis rules:**
{{EXTRACT_IMAGE_PROMPT}}

**Files to process:**
{{LIST — one row per file: source_path | destination_path | category | ingest_id}}

For each file:

*If category is `text`:*
1. Read the source file (handle encoding: try UTF-8, fall back to latin-1, then UTF-8 with replace).
2. If the content exceeds 500,000 bytes (UTF-8-encoded):
   - Split into chunks of ≤500,000 bytes on line boundaries.
   - Apply the text extraction rules above to each chunk separately (restore structure, convert tables to pipe format, remove repeated headers/footers/page numbers).
   - Concatenate the reformatted chunks with a blank line between them. Go to step 4.
3. Else, detect complexity signals:
   - Any line that starts with `|` (pipe table)
   - 3 or more lines containing two or more consecutive spaces mid-line
   - The same line (more than 10 characters) appearing verbatim 3 or more times
   - If any signal present: apply the text extraction rules above to rewrite as clean Markdown.
   - If no signal: use the content as-is without modification.
4. Write to destination_path:
```
---
source: <source_path>
category: text
extracted_at: <ISO8601 now>
ingest_id: <ingest_id value>
---

<content>
```

*If category is `binary-image`:*
1. Read the image with your vision capability.
2. Apply the image analysis rules above.
3. Write to destination_path:
```
---
source: <source_path>
category: binary-image
extracted_at: <ISO8601 now>
ingest_id: <ingest_id value>
---

<image analysis output>
```

*If category is `binary-doc`:*
1. Run: `.venv/bin/python -m ingest_pipeline.extract_doc "<source_path>" --offset 0`
   Quote the path to handle spaces and special characters in filenames.
2. Parse the JSON from stdout. Note `has_more`.
3. Apply the text extraction rules above to the `text` field to reformat it as clean Markdown — restore headings with `#`, convert aligned columns to pipe tables, remove repeated page artifacts (headers, footers, page numbers).
4. If `has_more` is true, run again with `--offset 1`, apply the text extraction rules to that chunk, and continue incrementing the offset until `has_more` is false.
5. Concatenate all reformatted chunks with a blank line between them.
6. Any stderr output from the CLI is informational (e.g. image-only slides); log but do not fail.
7. Write to destination_path:
```
---
source: <source_path>
category: binary-doc
extracted_at: <ISO8601 now>
ingest_id: <ingest_id value>
---

<concatenated reformatted text>
```

Report back: a JSON list of `{"source_path": "...", "status": "extracted"|"failed", "error": null|"..."}` for each file.
---

After all extract agents complete, update manifest.json with reported statuses. Update state.json: current_step 1, completed_steps [0,1]. Append step_complete journal event.

---

## Phase 2 — Summarize (parallel Haiku agents)

Collect all manifest records with status `extracted`. Group into batches of at most 10.
Spawn all Haiku summarize agents **simultaneously** (model="haiku").

Template:

---
You are a summarization sub-agent. For each extracted file below, read it and write its summary file.

**Summarization rules:**
{{SUMMARIZE_PROMPT}}

**Files to summarize:**
{{LIST — one row per file: destination_path (the extracted .md)}}

For each file at path P:
1. Read P.
2. Extract the `source:` field from the front matter.
3. Write `{P.parent}/_summary_{P.stem}.md`:
```
---
type: summary
source_summary: <source field from front matter>
tags:
  - <tag1>
  - <tag2>
summarized_at: <ISO8601 now>
---

<prose summary following the summarization rules above>

## Key Topics
- <topic>
```

Include 3–10 lowercase tags covering theme, domain, and scope.

Report back: a JSON list of `{"path": "...", "status": "ok"|"failed", "error": null|"..."}` for each file.
---

After all summarize agents complete, update state.json: current_step 2, completed_steps [0,1,2]. Append step_complete journal event.

---

## Phase 3 — Reduce (parallel Haiku agents, by depth level)

Find all directories under DESTINATION (excluding `.ingest`) that contain at least one `_summary_*.md` file. Compute each directory's depth (number of path components relative to DESTINATION). Sort unique depth values **descending** (deepest first).

For each depth level, spawn one Haiku agent per directory **simultaneously** (model="haiku"). Wait for all agents at one level to complete before starting the next level.

The output path for a reduce depends on the directory:
- If dir == DESTINATION → write `DESTINATION/_reduce_root.md`
- Otherwise → write `{dir.parent}/_reduce_{dir.name}.md`

Template:

---
You are a reduction sub-agent. Synthesize all summaries and child reduce files in one directory into a single reduce file.

**Reduction rules:**
{{REDUCE_PROMPT}}

**Directory:** {{DIR_PATH}}
**Input files (read all of these):**
{{LIST of _summary_*.md and _reduce_*.md files in this directory}}
**Output file:** {{OUTPUT_PATH}}

Steps:
1. Read all input files.
2. Collect all tags from each file's front matter (union, deduplicated).
3. Write OUTPUT_PATH:
```
---
type: reduce
directory: {{DIR_PATH}}
sources:
  - <input_path_1>
  - <input_path_2>
tags:
  - <merged tags>
reduced_at: <ISO8601 now>
---

<synthesis following the reduction rules above>

## Components
- <filename or subdir name>: <one-line description>

## Key Topics
- <topic>
```

Report back: `{"status": "ok"|"failed", "output": "{{OUTPUT_PATH}}", "error": null|"..."}`.
---

After all depth levels complete, update state.json: current_step 3, completed_steps [0,1,2,3]. Append step_complete journal event.

---

## Phase 4 — Index (one Opus agent)

Collect all `DESTINATION/_reduce_*.md` file paths. Spawn **one** Opus agent (model="opus"):

---
You are a knowledge-base indexing agent. Generate a comprehensive index from the top-level reduce documents.

**Indexing rules:**
{{INDEX_PROMPT}}

**Top-level reduce files — read each one:**
{{LIST of DESTINATION/_reduce_*.md paths}}

**Write to:** DESTINATION/index.md

Output format:
```
---
type: index
generated_at: <ISO8601 now>
---

<index following the indexing rules above>
```

Report back: `{"status": "ok"|"failed", "error": null|"..."}`.
---

After the index agent completes, update state.json: current_step 4, completed_steps [0,1,2,3,4]. Append:
```json
{"ts": "<ISO8601>", "event": "pipeline_complete", "total_files": <N>, "cmd": "ingest"}
```

---

## Final Output

Print a summary:
- Files extracted and summarized (counts by category)
- Any per-file errors reported by sub-agents
- Steps completed
