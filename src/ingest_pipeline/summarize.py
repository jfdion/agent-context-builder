from pathlib import Path

import anthropic

from .config import HAIKU_MODEL, MAX_TOKENS
from .api import call_claude
from .state import ManifestFile, State


def _summary_path(dest_path: Path) -> Path:
    return dest_path.parent / f"_summary_{dest_path.stem}.md"


def _build_front_matter(record: ManifestFile, source_path: str) -> str:
    return (
        f"---\n"
        f"type: summary\n"
        f"source: {source_path}\n"
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

    front_matter = _build_front_matter(record, record.source_path)
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
