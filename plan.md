# Implementation Plan — MVP First

## Package Layout

```
agent-context-builder/
├── pyproject.toml            ← add [project.scripts]
├── prompts/
│   ├── extract_text.txt
│   ├── extract_image.txt     ← placeholder in Phase 1
│   ├── summarize.txt
│   ├── reduce.txt
│   └── index.txt
└── src/
    └── ingest_pipeline/
        ├── __init__.py
        ├── cli.py            ← Click entry points
        ├── config.py         ← constants, model names, prompt loader
        ├── state.py          ← manifest/state/journal I/O
        ├── walker.py         ← directory walk + file classification
        ├── api.py            ← call_claude wrapper (throttle + caching + token log)
        ├── extract.py        ← Step 1 extractors by category
        ├── summarize.py      ← Step 2
        ├── reduce.py         ← Step 3
        ├── index.py          ← Step 4
        ├── pipeline.py       ← orchestrator (runs steps 0–4, resume logic)
        └── plantuml_val.py   ← Phase 5
```

`[project.scripts]`: `ingest`, `ingest-add`, `ingest-amend` → `ingest_pipeline.cli:*`

---

## Phase 1 — End-to-End on Text Files ✅ DONE (2026-05-11)

**Goal:** `uv run ingest ./docs ./context` works on `.txt`/`.md`/`.py`/`.json` etc.

All 17 source files and 35 passing tests delivered.

| # | File | Status |
|---|------|--------|
| 1 | `pyproject.toml` | ✅ `[project.scripts]`, `[build-system]`, pytest config |
| 2 | `prompts/*.txt` | ✅ All five prompt files (image is placeholder) |
| 3 | `config.py` | ✅ Model names, max-tokens table, extension maps, `load_prompts()` |
| 4 | `state.py` | ✅ Dataclasses + manifest/state/journal I/O |
| 5 | `walker.py` | ✅ `classify_file`, `walk_source`, `mirror_dirs`, `dest_path_for` |
| 6 | `api.py` | ✅ `call_claude` — throttle + `cache_control: ephemeral` + token logging |
| 7 | `extract.py` | ✅ `extract_text_file` (real); binary-doc/image → `NotImplementedError` |
| 8 | `summarize.py` | ✅ `summarize_file`, `run_summarize_step` |
| 9 | `reduce.py` | ✅ `reduce_dir`, `sorted_dirs_bottom_up`, `run_reduce_step` |
| 10 | `index.py` | ✅ `run_index_step` (Sonnet) |
| 11 | `pipeline.py` | ✅ `run_ingest`: steps 0–4, resume prompt, rich progress, summary panel |
| 12 | `cli.py` | ✅ `ingest` (real); `ingest-add`/`ingest-amend` → `NotImplementedError` |
| 13 | `tests/` | ✅ 35 tests — all pass (`uv run pytest`) |

---

## Phase 2 — Binary-Doc (PDF, DOCX, PPTX, XLSX) ← NEXT

Only `extract.py` changes. Add four format-specific functions:
- **PDF** via `pymupdf` — footer frequency analysis (≥80% of pages), always calls Claude
- **DOCX** via `python-docx` — explicit footer sections
- **PPTX** via `python-pptx` — speaker notes under `## Speaker Notes`
- **XLSX** via `openpyxl` — each sheet as markdown table under `## {Sheet Name}`

Everything downstream is already format-agnostic.

---

## Phase 3 — Images

`extract.py` gets a real `extract_image`: base64-encode → Claude Haiku vision API. SVG treated as `image/svg+xml`.

`plantuml_val.py` created: `validate_plantuml(block) -> (bool, str)` — non-blocking, logs `plantuml_warning` journal event on failure, writes the file regardless.

`prompts/extract_image.txt` gets the real prompt.

---

## Phase 4 — ingest-add / ingest-amend

`pipeline.py` gets two new functions:
- `run_ingest_add`: locate manifest from parent dirs, classify new files, run Steps 1–4 for affected scope only
- `run_ingest_amend`: delete generated files for scope, reset manifest records, re-run Steps 1–4

`reduce.py` gets `run_reduce_from_dir(start_dir)` — same as `run_reduce_step` but starts from a specific directory instead of the full tree.

`cli.py` stubs replaced with real calls.

---

## Phase 5 — Polish

- **Retry logic** in `api.py`: on `RateLimitError` or 5xx, wait `2 × (60/rpm)` and retry up to 3 times
- **Real SHA-256** content hashing in `state.py` (Phase 1 uses path hash)
- **Large file chunking** in `extract.py` (> 500 KB raw text)
- **Unicode fallback** (`latin-1` on `UnicodeDecodeError`)
- **Symlink handling** in `walker.py` (skip + log)
- **`.claude/commands/`** slash command wrappers
- **`ANTHROPIC_API_KEY` check** at startup with clear error message

---

## Key Design Decisions

- **Prompts loaded once** in `pipeline.py` and passed as a `dict` — same string object reused per step, which is what makes Anthropic's `cache_control: ephemeral` effective
- **`state.py` is pure I/O, no logic** — all decisions live in `pipeline.py`
- **`extract.py` is the only format-aware module** — Phases 2 and 3 touch nothing else
- **Resumability = one `Path.exists()` check** per file before processing — no extra infrastructure
