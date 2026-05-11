from pathlib import Path

import anthropic

from .config import HAIKU_MODEL, MAX_TOKENS
from .api import call_claude
from .state import State


def collect_inputs_for_dir(dir_path: Path) -> list[Path]:
    summaries = sorted(dir_path.glob("_summary_*.md"))
    reduces = [p for p in sorted(dir_path.glob("_reduce_*.md")) if p.name != "_reduce_root.md"]
    return summaries + reduces


def reduce_output_path(dir_path: Path, dest_root: Path) -> Path:
    if dir_path == dest_root:
        return dest_root / "_reduce_root.md"
    return dir_path.parent / f"_reduce_{dir_path.name}.md"


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

    combined = "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in inputs)
    dir_label = dir_path.name if dir_path != dest_root else "(root)"
    user_content = f"Directory: {dir_label}\n\nSummaries:\n{combined}"

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

    front_matter = (
        f"---\n"
        f"type: reduce\n"
        f"directory: {dir_path}\n"
        f"---\n\n"
    )
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
            errors.append(f"{dir_path}: {e}")
    return errors
