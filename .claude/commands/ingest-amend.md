Orchestrate re-processing an amended source directory in parallel. You (Sonnet) orchestrate; Haiku sub-agents process files; Opus rebuilds the index.

**Arguments:** $ARGUMENTS — parse SOURCE (first arg) and DESTINATION (second arg), resolve both to absolute paths. Optional: `--locale LOCALE` (e.g. `fr`, `fr_CA`) — if provided, skip interactive locale detection and use this value for all front matter and reduce/index steps.

---

## Phase 0 — Load, Validate, and Reset (you, Sonnet)

1. Verify `DESTINATION/.ingest/manifest.json` exists. If not, abort: "No manifest found. Run /ingest first."

2. Read `DESTINATION/.ingest/manifest.json`.

3. Collect `affected` = all manifest records whose `source_path` starts with SOURCE (resolved absolute path). If empty, abort: "No manifest records found for SOURCE. Nothing to amend."

4. Collect `dest_subdirs` = unique set of parent directories of all `destination_path` values in `affected`.

5. Delete existing outputs for affected records:
   - For each record: delete `destination_path` if it exists; delete `{dest_path.parent}/_summary_{dest_path.stem}.md` if it exists.
   - For each dir in `dest_subdirs`, walk upward to DESTINATION: at each level delete any `_reduce_*.md` file present. (These will all be regenerated.)
   - Delete `DESTINATION/index.md` if it exists.

6. Reset each affected record's `status` to `"pending"` in the manifest. Write `manifest.json`.

7. Read `DESTINATION/.ingest/state.json` if it exists; reset `current_step` to 0 and `completed_steps` to []. Write it back.

8. Append to `DESTINATION/.ingest/journal.jsonl`:
```json
{"ts": "<ISO8601>", "event": "amend_start", "scope": "<SOURCE>", "files_reset": <N>, "cmd": "ingest-amend"}
```

9. Read prompt files and hold in memory:
   - `prompts/extract_text.txt` → EXTRACT_TEXT_PROMPT
   - `prompts/extract_image.txt` → EXTRACT_IMAGE_PROMPT
   - `prompts/summarize.txt` → SUMMARIZE_PROMPT
   - `prompts/reduce.txt` → REDUCE_PROMPT
   - `prompts/index.txt` → INDEX_PROMPT

---

## Phase 1 — Extract (parallel Haiku agents)

Group all affected records with status `pending` into batches of at most 10. Spawn all Haiku extract agents **simultaneously** (model="haiku").

Template:

---
You are an extraction sub-agent. Process each file below using your Bash, Read, and Write tools.

**Text extraction rules:**
{{EXTRACT_TEXT_PROMPT}}

**Image analysis rules:**
{{EXTRACT_IMAGE_PROMPT}}

**Files to process:**
{{LIST: source_path | destination_path | category | sha256}}

For each file, recompute the SHA-256 from current contents with `sha256sum`.

*Text files:*
1. Read source (try UTF-8, fall back to latin-1, then UTF-8 with replace).
2. If the content exceeds 500,000 bytes (UTF-8-encoded):
   - Split into chunks of ≤500,000 bytes on line boundaries.
   - Apply the text extraction rules above to each chunk separately (restore structure, convert tables to pipe format, remove repeated headers/footers/page numbers).
   - Concatenate the reformatted chunks with a blank line between them. Go to step 4.
3. Else, detect complexity signals:
   - Any line that starts with `|`, 3+ lines with 2+ consecutive mid-line spaces, same line >10 chars 3+ times.
   - If any signal present: apply the text extraction rules above to rewrite as clean Markdown.
   - If no signal: use as-is.
4. Write destination_path:
```
---
source: <source_path>
category: text
extracted_at: <ISO8601 now>
ingest_id: <fresh sha256 without prefix>
---

<content>
```

*Binary-image files:*
1. Read image (vision). Apply image analysis rules.
2. Write destination_path:
```
---
source: <source_path>
category: binary-image
extracted_at: <ISO8601 now>
ingest_id: <fresh sha256 without prefix>
---

<image analysis output>
```

*Binary-doc files:*
1. Run: `.venv/bin/python -m ingest_pipeline.extract_doc "<source_path>" --offset 0`
   Quote the path to handle spaces and special characters in filenames.
2. Parse the JSON from stdout. Note `has_more`.
3. Apply the text extraction rules above to the `text` field to reformat it as clean Markdown — restore headings with `#`, convert aligned columns to pipe tables, remove repeated page artifacts (headers, footers, page numbers).
4. If `has_more` is true, run again with `--offset 1`, apply extraction rules to that chunk, and continue incrementing until `has_more` is false.
5. Concatenate all reformatted chunks with a blank line between them.
6. Any stderr output from the CLI is informational; log but do not fail.
7. Write to destination_path:
```
---
source: <source_path>
category: binary-doc
extracted_at: <ISO8601 now>
ingest_id: <fresh sha256 without prefix>
---

<concatenated reformatted text>
```

Report back: JSON list of `{"source_path": "...", "status": "extracted"|"failed", "error": null|"..."}`.
---

After all extract agents complete, update manifest.json with reported statuses. Append step journal event.

---

## Phase 2 — Summarize (parallel Haiku agents)

Group all affected records with status `extracted` into batches of at most 10. Spawn all Haiku summarize agents **simultaneously** (model="haiku").

Template:

---
You are a summarization sub-agent. For each file, read the extracted .md and write its summary.

**Summarization rules:**
{{SUMMARIZE_PROMPT}}

**Files to summarize:**
{{LIST: destination_path}}

For each file at path P:
1. Read P. Extract `source:` from front matter.
2. Write `{P.parent}/_summary_{P.stem}.md`:
```
---
type: summary
source_summary: <source field>
tags:
  - <tag>
summarized_at: <ISO8601 now>
---

<prose summary per rules>

## Key Topics
- <topic>
```

Report back: JSON list of `{"path": "...", "status": "ok"|"failed", "error": null|"..."}`.
---

---

## Phase 3 — Re-Reduce Upward Chain (parallel Haiku agents per level)

For each directory in `dest_subdirs`, build its upward chain to DESTINATION (inclusive). Take the union of all these chains to get the full set of directories needing re-reduction.

Sort by depth descending (deepest first). Group directories at the same depth into parallel batches.

At each depth level, spawn one Haiku agent per directory **simultaneously** (model="haiku"). Wait for a level to complete before starting the next.

Template:

---
You are a reduction sub-agent.

**Reduction rules:**
{{REDUCE_PROMPT}}

**Directory:** {{DIR_PATH}}
**Input files (read all):**
{{LIST: _summary_*.md files in this dir + any _reduce_*.md at this level that represent subdirs}}
**Output file:** {{OUTPUT_PATH}}
(Non-root: `{dir.parent}/_reduce_{dir.name}.md`. Root: `DESTINATION/_reduce_root.md`.)

1. Read all input files.
2. Collect and deduplicate tags from all front matter.
3. Write OUTPUT_PATH:
```
---
type: reduce
directory: {{DIR_PATH}}
sources:
  - <path>
tags:
  - <merged tags>
reduced_at: <ISO8601 now>
---

<synthesis per reduction rules>

## Components
- <name>: <one-line description>

## Key Topics
- <topic>
```

Report back: `{"status": "ok"|"failed", "output": "{{OUTPUT_PATH}}", "error": null|"..."}`.
---

---

## Phase 4 — Re-Index (one Opus agent)

Collect all `DESTINATION/_reduce_*.md` paths. Spawn one Opus agent (model="opus"):

---
You are a knowledge-base indexing agent.

**Indexing rules:**
{{INDEX_PROMPT}}

**Top-level reduce files — read each:**
{{LIST of DESTINATION/_reduce_*.md paths}}

**Write to:** DESTINATION/index.md

```
---
type: index
generated_at: <ISO8601 now>
---

<index per the indexing rules>
```

Report back: `{"status": "ok"|"failed", "error": null|"..."}`.
---

Update state.json: current_step 4, completed_steps [0,1,2,3,4].

---

## Final Output

Print:
- Files re-extracted and re-summarized (counts)
- Any errors from sub-agents
