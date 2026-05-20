"""Integration test: full pipeline on a temp source directory with mocked Claude client."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest_pipeline.pipeline import run_ingest
from ingest_pipeline.state import load_state, ingest_dir


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text="# Summary\n\nThis is a test summary.")]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    client.messages.create.return_value = response
    return client


@pytest.fixture
def small_source(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    (src / "subdir").mkdir(parents=True)
    (src / "README.md").write_text("# Hello\n\nThis is the readme.", encoding="utf-8")
    (src / "main.py").write_text("def main():\n    print('hello')\n", encoding="utf-8")
    (src / "subdir" / "utils.py").write_text("def util():\n    pass\n", encoding="utf-8")
    return src


def test_full_pipeline_runs(tmp_path: Path, small_source: Path, prompts_dir: Path) -> None:
    dest = tmp_path / "dest"
    mock_client = _make_mock_client()

    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    state = load_state(dest)
    assert state is not None
    assert 4 in state.completed_steps
    assert state.total_input_tokens > 0


def test_pipeline_creates_index(tmp_path: Path, small_source: Path, prompts_dir: Path) -> None:
    dest = tmp_path / "dest"
    mock_client = _make_mock_client()

    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    assert (dest / "index.md").exists()


def test_pipeline_resumable(tmp_path: Path, small_source: Path, prompts_dir: Path) -> None:
    dest = tmp_path / "dest"
    mock_client = _make_mock_client()

    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    call_count_first = mock_client.messages.create.call_count

    # Second run should skip all already-existing files
    mock_client2 = _make_mock_client()
    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client2),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        patch("ingest_pipeline.ui.confirm_resume", return_value=False),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    # All output files already exist — no new API calls expected
    assert mock_client2.messages.create.call_count == 0


def test_pipeline_mirrors_directory_structure(tmp_path: Path, small_source: Path, prompts_dir: Path) -> None:
    dest = tmp_path / "dest"
    mock_client = _make_mock_client()

    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    assert (dest / "subdir").is_dir()
    assert (dest / "subdir" / "utils.md").exists()


def test_pipeline_creates_manifest(tmp_path: Path, small_source: Path, prompts_dir: Path) -> None:
    dest = tmp_path / "dest"
    mock_client = _make_mock_client()

    with (
        patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=mock_client),
        patch("ingest_pipeline.api.time.sleep"),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        run_ingest(small_source, dest, rpm=60, prompts_dir=prompts_dir)

    manifest_path = ingest_dir(dest) / "manifest.json"
    assert manifest_path.exists()

    import json
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(data["files"]) == 3
