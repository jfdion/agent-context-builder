import pytest
from pathlib import Path

from ingest_pipeline.config import load_prompts, TEXT_EXTENSIONS, BINARY_DOC_EXTENSIONS


def test_load_prompts_success(prompts_dir: Path) -> None:
    prompts = load_prompts(prompts_dir)
    assert set(prompts.keys()) == {"extract_text", "extract_image", "summarize", "reduce", "index"}
    assert "Prompt for extract_text." in prompts["extract_text"]


def test_load_prompts_missing_file(tmp_path: Path) -> None:
    d = tmp_path / "prompts"
    d.mkdir()
    # Only write some of them
    (d / "extract_text.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_prompts(d)


def test_extensions_disjoint() -> None:
    assert TEXT_EXTENSIONS.isdisjoint(BINARY_DOC_EXTENSIONS)
