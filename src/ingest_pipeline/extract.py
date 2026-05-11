import re
from collections import Counter
from pathlib import Path

import anthropic

from .config import HAIKU_MODEL, MAX_TOKENS
from .api import call_claude
from .state import ManifestFile, State


def has_complexity_signals(text: str) -> bool:
    lines = text.splitlines()

    # Pipe tables
    if any("|" in line and line.strip().startswith("|") for line in lines):
        return True

    # Aligned columns: two or more consecutive spaces appearing mid-line
    aligned_count = sum(1 for line in lines if re.search(r"\S {3,}\S", line))
    if aligned_count >= 3:
        return True

    # Repeated non-trivial lines (≥3 occurrences of same non-empty line)
    non_trivial = [line.strip() for line in lines if len(line.strip()) > 10]
    counts = Counter(non_trivial)
    if any(count >= 3 for count in counts.values()):
        return True

    return False


def _build_front_matter(record: ManifestFile) -> str:
    return (
        f"---\n"
        f"source: {record.source_path}\n"
        f"category: {record.category}\n"
        f"size_bytes: {record.size_bytes}\n"
        f"---\n\n"
    )


def extract_text_file(
    source_path: Path,
    dest_path: Path,
    record: ManifestFile,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
) -> None:
    raw_text = source_path.read_text(encoding="utf-8", errors="replace")
    front_matter = _build_front_matter(record)

    if has_complexity_signals(raw_text):
        user_content = f"Source: {record.source_path}\n\nContent:\n{raw_text}"
        content = call_claude(
            client,
            HAIKU_MODEL,
            prompts["extract_text"],
            user_content,
            MAX_TOKENS["extract"],
            rpm,
            state,
            dest_root,
        )
    else:
        content = raw_text

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(front_matter + content, encoding="utf-8")


def extract_binary_doc(
    source_path: Path,
    dest_path: Path,
    record: ManifestFile,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
) -> None:
    raise NotImplementedError("Phase 2: binary document extraction not yet implemented")


def extract_image(
    source_path: Path,
    dest_path: Path,
    record: ManifestFile,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
) -> None:
    raise NotImplementedError("Phase 3: image extraction not yet implemented")


def extract_file(
    record: ManifestFile,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    dest_root: Path,
) -> None:
    source_path = Path(record.source_path)
    dest_path = Path(record.destination_path)

    if record.category == "text":
        extract_text_file(source_path, dest_path, record, prompts, client, rpm, state, dest_root)
    elif record.category == "binary_doc":
        extract_binary_doc(source_path, dest_path, record, prompts, client, rpm, state, dest_root)
    elif record.category == "image":
        extract_image(source_path, dest_path, record, prompts, client, rpm, state, dest_root)
    else:
        raise ValueError(f"Unknown category: {record.category}")
