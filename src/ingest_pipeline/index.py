from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .config import SONNET_MODEL, MAX_TOKENS
from .api import call_claude
from .state import State


def run_index_step(
    dest_root: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    rpm: int,
    state: State,
    on_progress: callable = None,
) -> list[str]:
    index_path = dest_root / "_index.md"

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

    front_matter = (
        f"---\n"
        f"type: index\n"
        f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"---\n\n"
    )
    index_path.write_text(front_matter + index_content, encoding="utf-8")
    return []
