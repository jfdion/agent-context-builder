import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ManifestFile:
    id: str
    source_path: str
    destination_path: str
    category: str
    size_bytes: int
    mtime: str
    status: str


@dataclass
class Manifest:
    version: str
    source_root: str
    destination_root: str
    created_at: str
    files: list[ManifestFile]


@dataclass
class State:
    pipeline_version: str
    command: str
    source_root: str
    destination_root: str
    started_at: str
    last_updated: str
    rpm: int
    current_step: int
    completed_steps: list[int]
    pending_files: list[str]
    completed_files: list[str]
    failed_files: list[str]
    total_input_tokens: int = 0
    total_output_tokens: int = 0


def ingest_dir(dest_root: Path) -> Path:
    return dest_root / ".ingest"


def _manifest_path(dest_root: Path) -> Path:
    return ingest_dir(dest_root) / "manifest.json"


def _state_path(dest_root: Path) -> Path:
    return ingest_dir(dest_root) / "state.json"


def _journal_path(dest_root: Path) -> Path:
    return ingest_dir(dest_root) / "journal.jsonl"


def save_manifest(dest_root: Path, manifest: Manifest) -> None:
    data = asdict(manifest)
    _manifest_path(dest_root).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_manifest(dest_root: Path) -> Manifest:
    data = json.loads(_manifest_path(dest_root).read_text(encoding="utf-8"))
    files = [ManifestFile(**f) for f in data["files"]]
    return Manifest(
        version=data["version"],
        source_root=data["source_root"],
        destination_root=data["destination_root"],
        created_at=data["created_at"],
        files=files,
    )


def save_state(dest_root: Path, state: State) -> None:
    state.last_updated = datetime.now(timezone.utc).isoformat()
    _state_path(dest_root).write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def load_state(dest_root: Path) -> State | None:
    path = _state_path(dest_root)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return State(**data)


def journal_event(event_type: str, **kwargs) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **kwargs,
    }


def append_journal(dest_root: Path, event: dict) -> None:
    with _journal_path(dest_root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
