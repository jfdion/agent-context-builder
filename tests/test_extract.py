from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest_pipeline.extract import has_complexity_signals, extract_text_file, extract_file
from ingest_pipeline.state import ManifestFile, State, ingest_dir
from datetime import datetime, timezone


def _make_record(source_path: str, dest_path: str) -> ManifestFile:
    return ManifestFile(
        id="test01",
        source_path=source_path,
        destination_path=dest_path,
        category="text",
        size_bytes=100,
        mtime=datetime.now(timezone.utc).isoformat(),
        status="pending",
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
        current_step=0,
        completed_steps=[],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


def test_has_complexity_signals_pipe_table() -> None:
    text = "| col1 | col2 |\n|------|------|\n| a    | b    |"
    assert has_complexity_signals(text) is True


def test_has_complexity_signals_aligned_columns() -> None:
    # Three lines with 3+ spaces mid-line
    text = "Name   Value\nFoo    123\nBar    456"
    assert has_complexity_signals(text) is True


def test_has_complexity_signals_repeated_lines() -> None:
    repeated = "Copyright 2024 ACME Corp"
    text = "\n".join([repeated] * 3)
    assert has_complexity_signals(text) is True


def test_has_complexity_signals_plain_text() -> None:
    text = "This is a simple paragraph.\nIt has no complex structures.\nJust plain text."
    assert has_complexity_signals(text) is False


def test_extract_text_file_simple(tmp_path: Path, mock_client: MagicMock, prompts_dir: Path) -> None:
    src = tmp_path / "simple.py"
    src.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    dest_file = dest / "simple.md"

    prompts = {name: f"Prompt {name}" for name in ["extract_text", "extract_image", "summarize", "reduce", "index"]}
    record = _make_record(str(src), str(dest_file))
    state = _make_state(dest)

    extract_text_file(src, dest_file, record, prompts, mock_client, 60, state, dest)

    # Simple text — should NOT call Claude
    mock_client.messages.create.assert_not_called()
    content = dest_file.read_text(encoding="utf-8")
    assert "source:" in content
    assert "def hello():" in content


def test_extract_text_file_complex_calls_claude(tmp_path: Path, mock_client: MagicMock) -> None:
    src = tmp_path / "table.md"
    src.write_text("| col1 | col2 |\n|------|------|\n| a    | b    |\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    dest_file = dest / "table.md"

    prompts = {name: f"Prompt {name}" for name in ["extract_text", "extract_image", "summarize", "reduce", "index"]}
    record = _make_record(str(src), str(dest_file))
    state = _make_state(dest)

    with patch("ingest_pipeline.extract.call_claude", return_value="Extracted content") as mock_call:
        extract_text_file(src, dest_file, record, prompts, mock_client, 60, state, dest)
        mock_call.assert_called_once()

    content = dest_file.read_text(encoding="utf-8")
    assert "Extracted content" in content


def test_extract_file_dispatches_to_text(tmp_path: Path, mock_client: MagicMock) -> None:
    src = tmp_path / "foo.py"
    src.write_text("x = 1\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    dest_file = dest / "foo.md"

    prompts = {name: f"Prompt {name}" for name in ["extract_text", "extract_image", "summarize", "reduce", "index"]}
    record = _make_record(str(src), str(dest_file))
    state = _make_state(dest)

    extract_file(record, prompts, mock_client, 60, state, dest)
    assert dest_file.exists()


def test_extract_file_binary_doc_raises(tmp_path: Path, mock_client: MagicMock) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    record = ManifestFile(
        id="x", source_path="/src/doc.pdf", destination_path=str(dest / "doc.md"),
        category="binary_doc", size_bytes=0,
        mtime=datetime.now(timezone.utc).isoformat(), status="pending",
    )
    state = _make_state(dest)
    with pytest.raises(NotImplementedError):
        extract_file(record, {}, mock_client, 60, state, dest)
