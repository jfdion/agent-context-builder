from pathlib import Path
from datetime import datetime, timezone

from ingest_pipeline.state import (
    Manifest, ManifestFile, State,
    save_manifest, load_manifest,
    save_state, load_state,
    append_journal, journal_event,
    ingest_dir,
)


def _make_dest(tmp_path: Path) -> Path:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    return dest


def _sample_manifest(dest: Path) -> Manifest:
    return Manifest(
        version="1.0",
        source_root="/src",
        destination_root=str(dest),
        created_at="2024-01-01T00:00:00+00:00",
        files=[
            ManifestFile(
                id="abc123",
                source_path="/src/foo.py",
                destination_path=str(dest / "foo.md"),
                category="text",
                size_bytes=1024,
                mtime="2024-01-01T00:00:00+00:00",
                status="pending",
            )
        ],
    )


def _sample_state(dest: Path) -> State:
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


def test_manifest_roundtrip(tmp_path: Path) -> None:
    dest = _make_dest(tmp_path)
    manifest = _sample_manifest(dest)
    save_manifest(dest, manifest)
    loaded = load_manifest(dest)
    assert loaded.version == manifest.version
    assert len(loaded.files) == 1
    assert loaded.files[0].id == "abc123"
    assert loaded.files[0].category == "text"


def test_state_roundtrip(tmp_path: Path) -> None:
    dest = _make_dest(tmp_path)
    state = _sample_state(dest)
    state.total_input_tokens = 500
    save_state(dest, state)
    loaded = load_state(dest)
    assert loaded is not None
    assert loaded.pipeline_version == "1.0"
    assert loaded.rpm == 60
    assert loaded.total_input_tokens == 500


def test_load_state_returns_none_when_missing(tmp_path: Path) -> None:
    dest = _make_dest(tmp_path)
    assert load_state(dest) is None


def test_journal_append(tmp_path: Path) -> None:
    dest = _make_dest(tmp_path)
    append_journal(dest, journal_event("test_event", key="value"))
    append_journal(dest, journal_event("second_event"))

    journal_path = ingest_dir(dest) / "journal.jsonl"
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    import json
    first = json.loads(lines[0])
    assert first["event"] == "test_event"
    assert first["key"] == "value"
    assert "timestamp" in first
