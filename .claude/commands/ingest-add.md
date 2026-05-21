Orchestrate adding a new source subdirectory to an existing knowledge base in parallel. You (Sonnet) orchestrate; Haiku sub-agents process files; Opus rebuilds the index.

**Arguments:** $ARGUMENTS — parse SOURCE (first arg) and DESTINATION (second arg), resolve both to absolute paths.

---

## Phase 0 — Load and Validate (you, Sonnet)

1. Verify `DESTINATION/.ingest/manifest.json` exists. If not, abort: "No manifest found. Run /ingest first."

2. Read `DESTINATION/.ingest/manifest.json`. Extract `source_root`.

3. Verify that SOURCE resolves to a path under `source_root`. If not, abort: "SOURCE is not under the manifest's source_root."

4. Compute `dest_subdir = DESTINATION / SOURCE.relative_to(source_root)`. Run `mkdir -p dest_subdir`.

5. Walk SOURCE (same skip rules: skip `node_modules .git __pycache__ .ingest .venv venv .tox dist build .mypy_cache` dirs; `.DS_Store .gitignore .gitkeep` names; `.lock .pyc` extensions).

6. Classify new files (text / binary-image / binary-doc — all three categories get status `pending`). Mirror directory structure under `dest_subdir`.

7. For each classifiable file, compute `sha256sum` and `stat -c%s`. Build new manifest records (status `pending`).

8. Check for duplicates: skip any new record whose resolved `source_path` already exists in the manifest (print a warning per duplicate). Append only the non-duplicate records to `manifest.json` and write it back.

9. Append to `DESTINATION/.ingest/journal.jsonl`:
```json
{"ts": "<ISO8601>", "event": "source_added", "new_source": "<SOURCE>", "derived_dest": "<dest_subdir>", "files_added": <N>, "cmd": "ingest-add"}
```

10. Read prompt files and hold in memory:
    - `prompts/extract_text.txt` → EXTRACT_TEXT_PROMPT
    - `prompts/extract_image.txt` → EXTRACT_IMAGE_PROMPT
    - `prompts/summarize.txt` → SUMMARIZE_PROMPT
    - `prompts/reduce.txt` → REDUCE_PROMPT
    - `prompts/index.txt` → INDEX_PROMPT

---

## Phase 1 — Extract (parallel Haiku agents)

Group all newly added records with status `pending` into batches of at most 10. Spawn all Haiku extract agents **simultaneously** (model="haiku").

Template:

---
You are an extraction sub-agent. Process the files below using your Bash, Read, and Write tools.

**Text extraction rules:**
{{EXTRACT_TEXT_PROMPT}}

**Image analysis rules:**
{{EXTRACT_IMAGE_PROMPT}}

**Files to process:**
{{LIST: source_path | destination_path | category | ingest_id}}

*Text files:*
1. Read source file (try UTF-8, fall back to latin-1, then UTF-8 with replace).
2. If the content exceeds 500,000 bytes (UTF-8-encoded):
   - Split into chunks of ≤500,000 bytes on line boundaries.
   - Apply the text extraction rules above to each chunk separately (restore structure, convert tables to pipe format, remove repeated headers/footers/page numbers).
   - Concatenate the reformatted chunks with a blank line between them. Go to step 4.
3. Else, detect complexity signals:
   - Any line that starts with `|`, 3+ lines with 2+ consecutive spaces mid-line, same line >10 chars appearing 3+ times.
   - If any signal present: apply the text extraction rules above to rewrite as clean Markdown.
   - If no signal: use as-is.
4. Write destination_path:
```
---
source: <source_path>
category: text
extracted_at: <ISO8601 now>
ingest_id: <ingest_id>
---

<content>
```

*Binary-image files:*
1. Read image (vision).
2. Apply image analysis rules.
3. Write destination_path:
```
---
source: <source_path>
category: binary-image
extracted_at: <ISO8601 now>
ingest_id: <ingest_id>
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
ingest_id: <ingest_id>
---

<concatenated reformatted text>
```

Report back: JSON list of `{"source_path": "...", "status": "extracted"|"failed", "error": null|"..."}`.
---

After all extract agents complete, update manifest.json statuses. Append step journal event.

---

## Phase 2 — Summarize (parallel Haiku agents)

Group all newly extracted records into batches of at most 10. Spawn all Haiku summarize agents **simultaneously** (model="haiku").

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

Build the upward directory chain from `dest_subdir` to DESTINATION (inclusive). For each directory in the chain, delete its existing reduce file before regenerating it:
- Non-root: `{dir.parent}/_reduce_{dir.name}.md`
- DESTINATION root: `DESTINATION/_reduce_root.md`

Then process the chain **bottom-up**, one level at a time. At each level, spawn one Haiku agent per directory **simultaneously** (model="haiku").

Template (same as full ingest reduce):

---
You are a reduction sub-agent.

**Reduction rules:**
{{REDUCE_PROMPT}}

**Directory:** {{DIR_PATH}}
**Input files (read all):**
{{LIST: _summary_*.md in this dir + any _reduce_*.md at this level from subdirs}}
**Output file:** {{OUTPUT_PATH}}

1. Read all inputs.
2. Collect and deduplicate all tags from front matter.
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

Delete `DESTINATION/index.md` if it exists.
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

---

## Final Output

Print:
- Files added and extracted (counts)
- Any errors from sub-agents
