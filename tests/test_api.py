from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

from ingest_pipeline.api import call_claude
from ingest_pipeline.state import State, ingest_dir


def _make_dest(tmp_path: Path) -> Path:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    return dest


def _make_state(dest: Path) -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version="1.0",
        command="ingest",
        source_root="/src",
        destination_root=str(dest),
        started_at=now,
        last_updated=now,
        rpm=60,
        current_step=0,
        completed_steps=[],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


def test_call_claude_returns_text(tmp_path: Path, mock_client: MagicMock) -> None:
    dest = _make_dest(tmp_path)
    state = _make_state(dest)

    with patch("ingest_pipeline.api.time.sleep"):
        result = call_claude(
            mock_client, "claude-haiku-4-5-20251001",
            "System prompt", "User content",
            1024, 60, state, dest,
        )

    assert result == "Mocked response"


def test_call_claude_accumulates_tokens(tmp_path: Path, mock_client: MagicMock) -> None:
    dest = _make_dest(tmp_path)
    state = _make_state(dest)
    state.total_input_tokens = 0
    state.total_output_tokens = 0

    with patch("ingest_pipeline.api.time.sleep"):
        call_claude(mock_client, "model", "sys", "user", 100, 60, state, dest)
        call_claude(mock_client, "model", "sys", "user", 100, 60, state, dest)

    assert state.total_input_tokens == 200
    assert state.total_output_tokens == 100


def test_call_claude_sleeps_correctly(tmp_path: Path, mock_client: MagicMock) -> None:
    dest = _make_dest(tmp_path)
    state = _make_state(dest)

    with patch("ingest_pipeline.api.time.sleep") as mock_sleep:
        call_claude(mock_client, "model", "sys", "user", 100, 30, state, dest)
        mock_sleep.assert_called_once_with(2.0)  # 60 / 30 = 2.0


def test_call_claude_uses_cache_control(tmp_path: Path, mock_client: MagicMock) -> None:
    dest = _make_dest(tmp_path)
    state = _make_state(dest)

    with patch("ingest_pipeline.api.time.sleep"):
        call_claude(mock_client, "model", "System prompt", "User", 100, 60, state, dest)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    system = call_kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "System prompt"


def test_call_claude_writes_journal(tmp_path: Path, mock_client: MagicMock) -> None:
    import json
    dest = _make_dest(tmp_path)
    state = _make_state(dest)

    with patch("ingest_pipeline.api.time.sleep"):
        call_claude(mock_client, "mymodel", "sys", "user", 100, 60, state, dest)

    journal_path = ingest_dir(dest) / "journal.jsonl"
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    entry = json.loads(lines[0])
    assert entry["event"] == "api_call"
    assert entry["model"] == "mymodel"
