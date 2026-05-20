Orchestrate re-processing an amended source directory in parallel. You (Sonnet) orchestrate; Haiku sub-agents process files; Opus rebuilds the index.

**Arguments:** $ARGUMENTS — parse SOURCE (first arg) and DESTINATION (second arg), resolve both to absolute paths.

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

Group all affected records with status `pending` and category `text` or `binary-image` into batches of at most 10. Spawn all Haiku extract agents **simultaneously** (model="haiku").

For each binary-doc record in affected, mark it `skipped-agent` in the manifest.

Template:

---
You are an extraction sub-agent. Process each file below using your Read and Write tools.

**Text extraction rules:**
{{EXTRACT_TEXT_PROMPT}}

**Image analysis rules:**
{{EXTRACT_IMAGE_PROMPT}}

**Files to process:**
{{LIST: source_path | destination_path | category | sha256}}

For each file, recompute the SHA-256 from current contents with `sha256sum`.

*Text files:*
1. Read source (try UTF-8, fall back to latin-1, then UTF-8 with replace).
2. Check complexity: lines starting with `|`, 3+ lines with 2+ consecutive mid-line spaces, same line >10 chars 3+ times.
3. If complex: rewrite as clean Markdown per the text extraction rules. If not: use as-is.
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
- Files skipped as `skipped-agent` (binary-docs)
- Any errors from sub-agents
