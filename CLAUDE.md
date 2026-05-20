# Agent Context Builder — Claude Instructions

## Context Traversal Workflow

When answering questions about ingested knowledge, follow this order strictly:

1. **Review `context/index.md`** — it is always the entry point. The index contains the directory structure, key concepts, and file purposes. Extract what you can before reading anything else.
2. **Read summaries next** — look for `_summary_<name>.md` files in the relevant subdirectory. These are pre-generated and token-efficient. A summary alone is often sufficient.
3. **Escalate to the full `.md` only if the summary has gaps** — the `.md` extraction files contain the full text of the source document. Read them only when the summary does not cover what is needed.
4. **Never read binary or source files** — files under `source/` and any binary format (`.pdf`, `.jpg`, `.png`, `.zip`, `.docx`, etc.) must never be read directly.

## Hard Guardrail: Binary and Source Files

**Before attempting to read any file matching these patterns, stop and ask the user for confirmation:**
- Any path beside `/context`
- Any file with extensions: `.pdf`, `.jpg`, `.jpeg`, `.png`, `.gif`, `.zip`, `.docx`, `.xlsx`, `.bin`, `.inl`, `.h`, `.cpp` (when a `.md` counterpart exists in `context/`)

**Confirmation prompt to use:**
> "The workflow for this project instructs me to use pre-extracted `.md` files instead of reading `<filename>` directly. Should I proceed with reading the source file anyway, or check the `.md` counterpart in `context/` first?"

Only proceed with reading the binary or source file if the user explicitly confirms, and only after verifying no `.md` counterpart exists.

## Rationale

The ingest pipeline extracts and summarizes all source content into `context/`. Reading source or binary files directly is redundant, token-wasteful, and bypasses the prepared knowledge base.
