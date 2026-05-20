import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .config import SONNET_MODEL, MAX_TOKENS
from .api import call_claude
from .state import State, load_manifest, _journal_path


def _format_size(size_bytes: int) -> str:
    """Format size in human-readable format (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _build_non_processed_section(dest_root: Path) -> str:
    """Build the Non-processed Documents section from manifest and journal."""
    try:
        manifest = load_manifest(dest_root)
    except Exception:
        return ""

    source_root = Path(manifest.source_root)

    # Collect non-processed files from manifest
    oversized = []
    skipped = []
    failed = []

    for f in manifest.files:
        if f.status == "skipped-oversized":
            rel_path = Path(f.source_path).relative_to(source_root)
            oversized.append((str(rel_path), f.size_bytes))
        elif f.status == "skipped":
            rel_path = Path(f.source_path).relative_to(source_root)
            skipped.append((str(rel_path), f.category))
        elif f.status == "failed":
            rel_path = Path(f.source_path).relative_to(source_root)
            failed.append(str(rel_path))

    # Collect symlinks from journal
    symlinks = []
    journal_path = _journal_path(dest_root)
    if journal_path.exists():
        try:
            for line in journal_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") == "file_skipped" and event.get("reason") == "symlink":
                    source = event.get("source")
                    if source:
                        rel_path = Path(source).relative_to(source_root)
                        symlinks.append(str(rel_path))
        except Exception:
            pass

    # Build section only if there are non-processed files
    if not (oversized or skipped or failed or symlinks):
        return ""

    lines = ["## Non-processed Documents", ""]

    if oversized:
        lines.append("### Oversized Files")
        lines.append("")
        for path, size in sorted(oversized):
            lines.append(f"- `{path}` ({_format_size(size)})")
        lines.append("")

    if skipped:
        lines.append("### Skipped Files")
        lines.append("")
        for path, category in sorted(skipped):
            lines.append(f"- `{path}` (category: {category})")
        lines.append("")

    if failed:
        lines.append("### Failed Extractions")
        lines.append("")
        for path in sorted(failed):
            lines.append(f"- `{path}`")
        lines.append("")

    if symlinks:
        lines.append("### Symbolic Links")
        lines.append("")
        for path in sorted(symlinks):
            lines.append(f"- `{path}`")
        lines.append("")

    return "\n".join(lines)


def run_index_step(
    dest_root: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    on_progress: callable = None,
) -> list[str]:
    index_path = dest_root / "index.md"

    if index_path.exists():
        return []

    if on_progress:
        on_progress("building index")

    top_level_reduces = sorted(dest_root.glob("_reduce_*.md"))
    if not top_level_reduces:
        return ["No reduce files found at dest root — skipping index"]

    combined = "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in top_level_reduces)
    user_content = f"Knowledge base top-level summaries:\n\n{combined}"

    try:
        index_content = call_claude(
            client,
            SONNET_MODEL,
            prompts["index"],
            user_content,
            MAX_TOKENS["index"],
            rpm,
            state,
            dest_root,
        )
    except Exception as e:
        return [f"Index generation failed: {e}"]

    # Append non-processed documents section if any exist
    non_processed_section = _build_non_processed_section(dest_root)
    if non_processed_section:
        index_content = index_content.rstrip() + "\n\n" + non_processed_section

    front_matter = (
        f"---\n"
        f"type: index\n"
        f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"---\n\n"
    )
    index_path.write_text(front_matter + index_content, encoding="utf-8")
    return []
