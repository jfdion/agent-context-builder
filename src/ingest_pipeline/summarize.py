import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .config import HAIKU_MODEL, MAX_TOKENS
from .api import call_claude
from .state import ManifestFile, State, append_journal, journal_event


def _summary_path(dest_path: Path) -> Path:
    return dest_path.parent / f"_summary_{dest_path.stem}.md"


def _extract_tags(text: str) -> list[str]:
    match = re.search(r"##\s+Key Topics\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if not match:
        return []
    tags = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            tags.append(stripped[2:].strip())
    return tags


def _build_front_matter(source_summary: str, tags: list[str], summarized_at: str) -> str:
    if tags:
        tags_block = "tags:\n" + "\n".join(f"  - {t}" for t in tags)
    else:
        tags_block = "tags: []"
    return (
        f"---\n"
        f"type: summary\n"
        f"source_summary: {source_summary}\n"
        f"{tags_block}\n"
        f"summarized_at: {summarized_at}\n"
        f"---\n\n"
    )


def summarize_file(
    record: ManifestFile,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
) -> None:
    dest_path = Path(record.destination_path)
    summary_path = _summary_path(dest_path)

    if summary_path.exists():
        return

    extracted_text = dest_path.read_text(encoding="utf-8")
    user_content = f"Source: {record.source_path}\n\nExtracted content:\n{extracted_text}"

    try:
        summary = call_claude(
            client,
            HAIKU_MODEL,
            prompts["summarize"],
            user_content,
            MAX_TOKENS["summarize"],
            rpm,
            state,
            dest_root,
        )
    except Exception as e:
        append_journal(dest_root, journal_event("file_error", file=record.source_path, error=str(e)))
        raise

    tags = _extract_tags(summary)
    summarized_at = datetime.now(timezone.utc).isoformat()
    front_matter = _build_front_matter(record.source_path, tags, summarized_at)
    summary_path.write_text(front_matter + summary, encoding="utf-8")


def run_summarize_step(
    records: list[ManifestFile],
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
    on_file: callable = None,
) -> list[str]:
    errors: list[str] = []
    for record in records:
        if on_file:
            on_file(record.source_path)
        try:
            summarize_file(record, prompts, client, rpm, state, dest_root)
        except Exception as e:
            errors.append(f"{record.source_path}: {e}")
    return errors
