# agent-context-builder — Developer Instructions

## Quick Reference

```bash
uv run pytest           # run all tests
uv run ingest <src> <dest> [--rpm 60] [--max-binary-mb 50] [--locale fr_CA]
uv run ingest-add <src-subdir> <dest> [--locale fr_CA]
uv run ingest-amend <src-subdir> <dest> [--locale fr_CA]
```

---

## Package Layout

```
src/ingest_pipeline/
  cli.py          Click entry points (ingest, ingest-add, ingest-amend)
  pipeline.py     Orchestrator — steps 0-4, resume logic, locale resolution
  extract.py      Step 1 extractors (text, binary-doc, image) + detect_locale
  extract_doc.py  CLI wrapper for binary-doc extraction (extract-doc command)
  summarize.py    Step 2 — per-file summaries
  reduce.py       Step 3 — bottom-up directory synthesis
  index.py        Step 4 — top-level index.md
  state.py        Manifest / State / Journal dataclasses and I/O
  walker.py       Directory walk, file classification, SHA-256 hashing
  api.py          call_claude / call_claude_vision with retry and token logging
  config.py       Constants, model names, extension maps, load_prompts()
  ui.py           Rich progress bar, confirm prompts, summary panel
prompts/          Editable prompt files loaded at runtime (one per step)
templates/        CLAUDE-ingest-template.md — copied to target projects by /ingest-init
tests/            pytest suite (145 tests)
```

---

## String Construction Policy

**String interpolation is prohibited.** This includes f-strings, `%`-formatting, and `.format()` calls.

Use only:
- **`json.dumps(obj)`** for any structured data emitted to stdout, stderr, or logs
- **Explicit concatenation** (`"prefix" + str(x) + "suffix"`) for plain prose messages
- **`" ".join([...])`** for list assembly

**Why:** String interpolation evaluates expressions inside the template at the call site. An attacker who controls variable content (e.g. a crafted filename, a slide title, a document excerpt) can inject substrings that impersonate log lines, JSON fields, or other structured output. Serialization methods (`json.dumps`) escape all special characters deterministically; concatenation exposes only the operands you explicitly choose.

Examples:

```python
# FORBIDDEN — f-string
click.echo(f"Warning: slide {w['slide']} is image-only", err=True)
msg = f"Error processing {source_path}: {error}"

# FORBIDDEN — .format()
click.echo("Warning: slide {} is image-only".format(w["slide"]), err=True)

# FORBIDDEN — % formatting
click.echo("Error: %s" % error, err=True)

# CORRECT — json.dumps for structured output
click.echo(json.dumps({"slide": w["slide"], "reason": w["reason"]}), err=True)

# CORRECT — concatenation for plain prose
msg = "Error processing " + str(source_path) + ": " + str(error)

# CORRECT — join for list assembly
tags_line = "tags: " + ", ".join(sorted(tags))
```

This policy applies to all new code. When editing existing code, do not introduce interpolation even when refactoring adjacent lines.

---

## Key Design Invariants

- **All Claude calls are pure content transformations.** Python constructs every prompt; Claude returns text; Python writes the file. No control flow decisions are delegated to the model.
- **`state.py` is I/O only.** All pipeline decisions live in `pipeline.py`.
- **`extract.py` is the only format-aware module.** Binary format support (Phases 2/3) touches nothing else.
- **Prompts are loaded once** in `pipeline.py` and passed as a `dict` — the same object reused per step activates Anthropic's `cache_control: ephemeral` prompt caching.
- **SHA-256 content hashing** in `walker.py` drives resumability and change detection. `ingest-amend` skips files whose hash matches the manifest record.
- **Locale resolution** runs after the summarize step in `pipeline.py` via `_resolve_locale()`, before reduce. It calls `detect_locale()` (in `extract.py`) on each `_summary_*.md` file and counts occurrences per BCP-47 tag. Resolution order:
  1. `--locale LOCALE` CLI flag — bypasses detection entirely and sets `state.locale` directly.
  2. Single detected locale — set automatically.
  3. Multiple detected locales — user is prompted interactively via `confirm_locale()` (`ui.py`).
  4. No locale detected — defaults to `"und"` (undetermined).
  The resolved locale is stored in `state.locale` and written into the front matter of all reduce and index files.

---

## Testing

All tests are in `tests/`. Run with `uv run pytest`. No network calls; Claude API is always mocked.

When adding a new feature:
1. Add or extend a test file matching the module name (`test_extract.py`, `test_pipeline_integration.py`, etc.)
2. Mock `call_claude` / `call_claude_vision` — never make real API calls in tests
3. Keep `time.sleep` patched to avoid slow tests

---

## Adding a New File Format

1. Add the extension to the correct set in `config.py` (`TEXT_EXTENSIONS`, `BINARY_DOC_EXTENSIONS`, or `BINARY_IMAGE_EXTENSIONS`)
2. Add an extractor function in `extract.py` (or `extract_binary_doc` dispatcher)
3. Add a test in `tests/test_extract_binary.py`
4. Nothing else needs to change — the pipeline is format-agnostic beyond Step 1
