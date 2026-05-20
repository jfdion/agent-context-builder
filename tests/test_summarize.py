import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest_pipeline.state import ManifestFile, State, ingest_dir
from ingest_pipeline.summarize import summarize_file, run_summarize_step, _extract_tags


def _make_record(source_path: str, dest_path: str) -> ManifestFile:
    return ManifestFile(
        id="sum01",
        source_path=source_path,
        destination_path=dest_path,
        category="text",
        size_bytes=100,
        mtime=datetime.now(timezone.utc).isoformat(),
        status="extracted",
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
        current_step=2,
        completed_steps=[0, 1],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


def _setup_dest(tmp_path: Path) -> tuple[Path, Path]:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    return dest, dest


MOCK_SUMMARY = """\
This module provides utility functions for the project.

## Key Topics
- Python
- utility functions
- helper methods
"""

PROMPTS = {name: f"Prompt {name}" for name in ["extract_text", "summarize", "reduce", "index"]}


# --- _extract_tags ---

def test_extract_tags_parses_key_topics() -> None:
    tags = _extract_tags(MOCK_SUMMARY)
    assert tags == ["Python", "utility functions", "helper methods"]


def test_extract_tags_empty_when_no_section() -> None:
    tags = _extract_tags("No topics section here.")
    assert tags == []


def test_extract_tags_empty_list_when_section_empty() -> None:
    text = "Prose.\n\n## Key Topics\n\n## Other Section\n"
    tags = _extract_tags(text)
    assert tags == []


# --- summarize_file nominal ---

def test_summarize_file_writes_correct_front_matter(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)
    extracted = dest / "utils.md"
    extracted.write_text("def util(): pass\n", encoding="utf-8")

    record = _make_record("/src/utils.py", str(extracted))
    state = _make_state(dest_root)

    with patch("ingest_pipeline.summarize.call_claude", return_value=MOCK_SUMMARY):
        summarize_file(record, PROMPTS, MagicMock(), 60, state, dest_root)

    summary_path = dest / "_summary_utils.md"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "source_summary:" in content
    assert "summarized_at:" in content
    assert "tags:" in content
    assert "- Python" in content
    assert MOCK_SUMMARY in content


def test_summarize_file_skips_if_already_exists(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)
    extracted = dest / "utils.md"
    extracted.write_text("def util(): pass\n", encoding="utf-8")
    summary = dest / "_summary_utils.md"
    summary.write_text("already done", encoding="utf-8")

    record = _make_record("/src/utils.py", str(extracted))
    state = _make_state(dest_root)

    with patch("ingest_pipeline.summarize.call_claude") as mock_call:
        summarize_file(record, PROMPTS, MagicMock(), 60, state, dest_root)
        mock_call.assert_not_called()


def test_summarize_file_tags_empty_when_no_key_topics(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)
    extracted = dest / "readme.md"
    extracted.write_text("# README\n\nHello world.\n", encoding="utf-8")

    record = _make_record("/src/readme.md", str(extracted))
    state = _make_state(dest_root)

    with patch("ingest_pipeline.summarize.call_claude", return_value="No topics section."):
        summarize_file(record, PROMPTS, MagicMock(), 60, state, dest_root)

    content = (dest / "_summary_readme.md").read_text(encoding="utf-8")
    assert "tags: []" in content


# --- summarize_file API error ---

def test_summarize_file_api_error_writes_journal_and_raises(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)
    extracted = dest / "utils.md"
    extracted.write_text("def util(): pass\n", encoding="utf-8")

    record = _make_record("/src/utils.py", str(extracted))
    state = _make_state(dest_root)

    with patch("ingest_pipeline.summarize.call_claude", side_effect=RuntimeError("API failure")):
        with pytest.raises(RuntimeError, match="API failure"):
            summarize_file(record, PROMPTS, MagicMock(), 60, state, dest_root)

    journal_path = ingest_dir(dest_root) / "journal.jsonl"
    assert journal_path.exists()
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert any(e["event"] == "file_error" for e in events)
    error_event = next(e for e in events if e["event"] == "file_error")
    assert "API failure" in error_event["error"]


# --- run_summarize_step ---

def test_run_summarize_step_empty_records(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)
    state = _make_state(dest_root)
    errors = run_summarize_step([], PROMPTS, MagicMock(), 60, state, dest_root)
    assert errors == []


def test_run_summarize_step_processes_files(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)

    for name in ["a.md", "b.md"]:
        (dest / name).write_text(f"content of {name}", encoding="utf-8")

    records = [
        _make_record(f"/src/{name}", str(dest / name))
        for name in ["a.md", "b.md"]
    ]
    state = _make_state(dest_root)

    with patch("ingest_pipeline.summarize.call_claude", return_value=MOCK_SUMMARY):
        errors = run_summarize_step(records, PROMPTS, MagicMock(), 60, state, dest_root)

    assert errors == []
    assert (dest / "_summary_a.md").exists()
    assert (dest / "_summary_b.md").exists()


def test_run_summarize_step_collects_errors_and_continues(tmp_path: Path) -> None:
    dest, dest_root = _setup_dest(tmp_path)

    for name in ["a.md", "b.md"]:
        (dest / name).write_text("content", encoding="utf-8")

    records = [
        _make_record(f"/src/{name}", str(dest / name))
        for name in ["a.md", "b.md"]
    ]
    state = _make_state(dest_root)

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("first fails")
        return MOCK_SUMMARY

    with patch("ingest_pipeline.summarize.call_claude", side_effect=side_effect):
        errors = run_summarize_step(records, PROMPTS, MagicMock(), 60, state, dest_root)

    assert len(errors) == 1
    assert "first fails" in errors[0]
    assert (dest / "_summary_b.md").exists()
