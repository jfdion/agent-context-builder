import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest_pipeline.state import State, ingest_dir
from ingest_pipeline.reduce import (
    sorted_dirs_bottom_up,
    collect_inputs_for_dir,
    reduce_dir,
)


def _make_state(dest_root: Path) -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version="1.0",
        command="ingest",
        source_root="/src",
        destination_root=str(dest_root),
        started_at=now,
        last_updated=now,
        rpm=60,
        current_step=3,
        completed_steps=[0, 1, 2],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


PROMPTS = {name: f"Prompt {name}" for name in ["extract_text", "summarize", "reduce", "index"]}

SUMMARY_CONTENT = """\
---
type: summary
source_summary: /src/utils.py
tags:
  - Python
  - utility
summarized_at: 2026-01-01T00:00:00+00:00
---

This is a summary.
"""

MOCK_SYNTHESIS = "Reduced synthesis content."


# --- sorted_dirs_bottom_up ---

def test_sorted_dirs_bottom_up_single_level(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "_summary_foo.md").write_text("x", encoding="utf-8")

    result = sorted_dirs_bottom_up(dest)
    assert result == [dest]


def test_sorted_dirs_bottom_up_two_levels(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    sub = dest / "subdir"
    sub.mkdir(parents=True)
    (sub / "_summary_bar.md").write_text("x", encoding="utf-8")

    result = sorted_dirs_bottom_up(dest)
    assert result.index(sub) < result.index(dest)


def test_sorted_dirs_bottom_up_three_levels_leaves_before_root(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    leaf = dest / "a" / "b"
    leaf.mkdir(parents=True)
    (leaf / "_summary_deep.md").write_text("x", encoding="utf-8")

    result = sorted_dirs_bottom_up(dest)
    assert result.index(leaf) < result.index(dest)
    leaf_parent = dest / "a"
    assert result.index(leaf) < result.index(leaf_parent)
    assert result.index(leaf_parent) < result.index(dest)


def test_sorted_dirs_bottom_up_empty_dest(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    result = sorted_dirs_bottom_up(dest)
    assert result == [dest]


# --- collect_inputs_for_dir ---

def test_collect_inputs_for_dir_nominal(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    sub = dest / "sub"
    sub.mkdir(parents=True)

    s1 = dest / "_summary_a.md"
    s2 = dest / "_summary_b.md"
    r1 = dest / "_reduce_sub.md"
    s1.write_text("summary a", encoding="utf-8")
    s2.write_text("summary b", encoding="utf-8")
    r1.write_text("reduce sub", encoding="utf-8")

    inputs = collect_inputs_for_dir(dest)
    assert s1 in inputs
    assert s2 in inputs
    assert r1 in inputs
    # _reduce_root.md should be excluded
    root_reduce = dest / "_reduce_root.md"
    root_reduce.write_text("root reduce", encoding="utf-8")
    inputs2 = collect_inputs_for_dir(dest)
    assert root_reduce not in inputs2


def test_collect_inputs_for_dir_empty(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    inputs = collect_inputs_for_dir(dest)
    assert inputs == []


def test_collect_inputs_for_dir_summaries_before_reduces(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    s = dest / "_summary_z.md"
    r = dest / "_reduce_aaa.md"
    s.write_text("s", encoding="utf-8")
    r.write_text("r", encoding="utf-8")

    inputs = collect_inputs_for_dir(dest)
    assert inputs.index(s) < inputs.index(r)


# --- reduce_dir nominal ---

def test_reduce_dir_nominal(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    (dest / "_summary_a.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
    state = _make_state(dest)

    with patch("ingest_pipeline.reduce.call_claude", return_value=MOCK_SYNTHESIS):
        reduce_dir(dest, dest, PROMPTS, MagicMock(), 60, state)

    output = dest / "_reduce_root.md"
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "type: reduce" in content
    assert "sources:" in content
    assert "tags:" in content
    assert "reduced_at:" in content
    assert MOCK_SYNTHESIS in content


def test_reduce_dir_skips_if_output_exists(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    (dest / "_summary_a.md").write_text("x", encoding="utf-8")
    (dest / "_reduce_root.md").write_text("already done", encoding="utf-8")

    state = _make_state(dest)
    with patch("ingest_pipeline.reduce.call_claude") as mock_call:
        reduce_dir(dest, dest, PROMPTS, MagicMock(), 60, state)
        mock_call.assert_not_called()


def test_reduce_dir_skips_if_no_inputs(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    state = _make_state(dest)
    with patch("ingest_pipeline.reduce.call_claude") as mock_call:
        reduce_dir(dest, dest, PROMPTS, MagicMock(), 60, state)
        mock_call.assert_not_called()

    assert not (dest / "_reduce_root.md").exists()


def test_reduce_dir_collects_tags_from_summaries(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    (dest / "_summary_a.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
    state = _make_state(dest)

    with patch("ingest_pipeline.reduce.call_claude", return_value=MOCK_SYNTHESIS):
        reduce_dir(dest, dest, PROMPTS, MagicMock(), 60, state)

    content = (dest / "_reduce_root.md").read_text(encoding="utf-8")
    assert "- Python" in content
    assert "- utility" in content


# --- reduce_dir API error ---

def test_reduce_dir_api_error_writes_journal_and_raises(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    (dest / "_summary_a.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
    state = _make_state(dest)

    with patch("ingest_pipeline.reduce.call_claude", side_effect=RuntimeError("API down")):
        with pytest.raises(RuntimeError, match="API down"):
            reduce_dir(dest, dest, PROMPTS, MagicMock(), 60, state)

    journal_path = ingest_dir(dest) / "journal.jsonl"
    assert journal_path.exists()
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert any(e["event"] == "file_error" for e in events)
    error_event = next(e for e in events if e["event"] == "file_error")
    assert "API down" in error_event["error"]
