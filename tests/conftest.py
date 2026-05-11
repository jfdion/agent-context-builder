from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    for name in ["extract_text", "extract_image", "summarize", "reduce", "index"]:
        (d / f"{name}.txt").write_text(f"Prompt for {name}.", encoding="utf-8")
    return d


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text="Mocked response")]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    client.messages.create.return_value = response
    return client


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    d = tmp_path / "source"
    d.mkdir()
    return d


@pytest.fixture
def dest_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dest"
    d.mkdir()
    return d
