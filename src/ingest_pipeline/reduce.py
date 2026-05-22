from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .config import HAIKU_MODEL, MAX_TOKENS
from .api import call_claude
from .state import State, append_journal, journal_event


def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return path


def _extract_locale(text: str) -> str:
    """Read locale from a file's YAML front matter; return 'und' if absent."""
    if not text.startswith("---\n"):
        return "und"
    end_idx = text.find("\n---\n", 4)
    if end_idx == -1:
        return "und"
    for line in text[4:end_idx].splitlines():
        if line.startswith("locale:"):
            return line.split(":", 1)[1].strip()
    return "und"


def _dominant_locale(inputs: list[Path]) -> str:
    counts: Counter = Counter(
        _extract_locale(p.read_text(encoding="utf-8"))
        for p in inputs
    )
    counts.pop("und", None)
    if not counts:
        return "und"
    return counts.most_common(1)[0][0]


def _parse_front_matter_tags(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return []
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return []
    fm_text = content[4:end_idx]
    tags: list[str] = []
    in_tags = False
    for line in fm_text.splitlines():
        stripped = line.strip()
        if stripped == "tags: []":
            return []
        if stripped.startswith("tags:") and not stripped.endswith("[]"):
            in_tags = True
            continue
        if in_tags:
            if stripped.startswith("- "):
                tags.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("-"):
                in_tags = False
    return tags


def _build_reduce_front_matter(dir_path: Path, inputs: list[Path], tags: list[str], locale: str = "und") -> str:
    sources_block = "sources:\n" + "\n".join("  - " + _rel(str(p)) for p in inputs) if inputs else "sources: []"
    tags_block = "tags:\n" + "\n".join("  - " + t for t in tags) if tags else "tags: []"
    reduced_at = datetime.now(timezone.utc).isoformat()
    return "\n".join([
        "---",
        "type: reduce",
        "directory: " + _rel(str(dir_path)),
        "locale: " + locale,
        sources_block,
        tags_block,
        "reduced_at: " + reduced_at,
        "---",
        "",
        "",
    ])


def collect_inputs_for_dir(dir_path: Path) -> list[Path]:
    summaries = sorted(dir_path.glob("_summary_*.md"))
    reduces = [p for p in sorted(dir_path.glob("_reduce_*.md")) if p.name != "_reduce_root.md"]
    return summaries + reduces


def reduce_output_path(dir_path: Path, dest_root: Path) -> Path:
    if dir_path == dest_root:
        return dest_root / "_reduce_root.md"
    return dir_path.parent / ("_reduce_" + dir_path.name + ".md")


def sorted_dirs_bottom_up(dest_root: Path) -> list[Path]:
    dirs_with_summaries: set[Path] = set()
    for summary in dest_root.rglob("_summary_*.md"):
        dirs_with_summaries.add(summary.parent)

    all_dirs: set[Path] = set()
    for d in dirs_with_summaries:
        current = d
        while current != dest_root.parent:
            all_dirs.add(current)
            if current == dest_root:
                break
            current = current.parent

    sorted_dirs = sorted(all_dirs, key=lambda p: (-len(p.parts), str(p)))

    # Ensure dest_root is last
    if dest_root in sorted_dirs:
        sorted_dirs.remove(dest_root)
    sorted_dirs.append(dest_root)

    return sorted_dirs


def reduce_dir(
    dir_path: Path,
    dest_root: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
) -> None:
    output_path = reduce_output_path(dir_path, dest_root)

    if output_path.exists():
        return

    inputs = collect_inputs_for_dir(dir_path)
    if not inputs:
        return

    locale = _dominant_locale(inputs)
    combined = "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in inputs)
    dir_label = dir_path.name if dir_path != dest_root else "(root)"
    user_content = "Directory: " + dir_label + "\nLanguage: " + locale + "\n\nSummaries:\n" + combined

    try:
        synthesis = call_claude(
            client,
            HAIKU_MODEL,
            prompts["reduce"],
            user_content,
            MAX_TOKENS["reduce"],
            rpm,
            state,
            dest_root,
        )
    except Exception as e:
        append_journal(dest_root, journal_event("file_error", directory=str(dir_path), error=str(e)))
        raise

    all_tags: list[str] = []
    seen: set[str] = set()
    for p in inputs:
        for tag in _parse_front_matter_tags(p):
            if tag not in seen:
                seen.add(tag)
                all_tags.append(tag)

    front_matter = _build_reduce_front_matter(dir_path, inputs, all_tags, locale)
    output_path.write_text(front_matter + synthesis, encoding="utf-8")


def run_reduce_step(
    dest_root: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    on_dir: callable = None,
) -> list[str]:
    errors: list[str] = []
    dirs = sorted_dirs_bottom_up(dest_root)
    for dir_path in dirs:
        if on_dir:
            on_dir(str(dir_path))
        try:
            reduce_dir(dir_path, dest_root, prompts, client, rpm, state)
        except Exception as e:
            errors.append(str(dir_path) + ": " + str(e))
    return errors


def run_reduce_from_dir(
    start_dir: Path,
    dest_root: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    on_dir: callable = None,
) -> list[str]:
    chain: list[Path] = []
    current = start_dir
    while True:
        chain.append(current)
        if current == dest_root:
            break
        current = current.parent

    for dir_path in chain:
        output = reduce_output_path(dir_path, dest_root)
        if output.exists():
            output.unlink()

    errors: list[str] = []
    for dir_path in chain:
        if on_dir:
            on_dir(str(dir_path))
        try:
            reduce_dir(dir_path, dest_root, prompts, client, rpm, state)
        except Exception as e:
            errors.append(str(dir_path) + ": " + str(e))
    return errors
