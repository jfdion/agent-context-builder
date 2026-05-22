import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ingest_pipeline.extract_doc import main, _chunk_text
from ingest_pipeline.config import CHUNK_SIZE_BYTES


# ==================== _chunk_text unit tests ====================


def test_chunk_text_single_chunk_small_text() -> None:
    text = "line one\nline two\nline three"
    chunks = _chunk_text(text, CHUNK_SIZE_BYTES)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_splits_on_line_boundary() -> None:
    line = "x" * 150_000
    text = f"{line}\n{line}"
    chunks = _chunk_text(text, CHUNK_SIZE_BYTES)
    assert len(chunks) == 2
    assert chunks[0] == line
    assert chunks[1] == line


def test_chunk_text_empty_input() -> None:
    chunks = _chunk_text("", CHUNK_SIZE_BYTES)
    assert chunks == [""]


def test_chunk_text_three_chunks() -> None:
    line = "y" * 80_000
    # Three lines each ~80KB; each chunk holds at most 200KB → expect 2 chunks (160KB, 80KB)
    text = f"{line}\n{line}\n{line}"
    chunks = _chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0] == f"{line}\n{line}"
    assert chunks[1] == line


# ==================== CLI tests ====================


@patch("ingest_pipeline.extract_doc.extract_pdf", return_value="Page 1 content\nPage 2 content")
def test_cli_pdf_single_chunk(mock_extract: MagicMock, tmp_path: Path) -> None:
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"fake pdf")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["offset"] == 0
    assert data["total_chunks"] == 1
    assert data["has_more"] is False
    assert "Page 1 content" in data["text"]
    assert data["source_path"] == str(src.resolve())


@patch("ingest_pipeline.extract_doc.extract_pdf")
def test_cli_pdf_two_chunks_offset_zero(mock_extract: MagicMock, tmp_path: Path) -> None:
    line = "z" * 150_000
    mock_extract.return_value = f"{line}\n{line}"

    src = tmp_path / "big.pdf"
    src.write_bytes(b"fake pdf")

    runner = CliRunner()
    result = runner.invoke(main, [str(src), "--offset", "0"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_chunks"] == 2
    assert data["offset"] == 0
    assert data["has_more"] is True
    assert data["text"] == line


@patch("ingest_pipeline.extract_doc.extract_pdf")
def test_cli_pdf_two_chunks_offset_one(mock_extract: MagicMock, tmp_path: Path) -> None:
    line = "z" * 150_000
    mock_extract.return_value = f"{line}\n{line}"

    src = tmp_path / "big.pdf"
    src.write_bytes(b"fake pdf")

    runner = CliRunner()
    result = runner.invoke(main, [str(src), "--offset", "1"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["offset"] == 1
    assert data["has_more"] is False
    assert data["text"] == line


@patch("ingest_pipeline.extract_doc.extract_pdf", return_value="some text")
def test_cli_offset_out_of_range(mock_extract: MagicMock, tmp_path: Path) -> None:
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"fake pdf")

    runner = CliRunner()
    result = runner.invoke(main, [str(src), "--offset", "5"])

    assert result.exit_code == 1
    assert "out of range" in result.output or "out of range" in (result.output + " ")


def test_cli_unsupported_extension(tmp_path: Path) -> None:
    src = tmp_path / "file.bin"
    src.write_bytes(b"data")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 1


@patch("ingest_pipeline.extract_doc.extract_docx", return_value="Docx content here")
def test_cli_docx(mock_extract: MagicMock, tmp_path: Path) -> None:
    src = tmp_path / "report.docx"
    src.write_bytes(b"fake docx")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["text"] == "Docx content here"
    assert data["total_chunks"] == 1


@patch("ingest_pipeline.extract_doc.extract_xlsx", return_value="## Sheet1\n| A | B |")
def test_cli_xlsx(mock_extract: MagicMock, tmp_path: Path) -> None:
    src = tmp_path / "data.xlsx"
    src.write_bytes(b"fake xlsx")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "Sheet1" in data["text"]


@patch("ingest_pipeline.extract_doc.extract_pptx")
def test_cli_pptx_image_only_warning_in_output(mock_extract: MagicMock, tmp_path: Path) -> None:
    def _side_effect(path: Path, warnings: list) -> str:
        warnings.append({"slide": 2, "reason": "image-only slide"})
        return "Slide 1 content"

    mock_extract.side_effect = _side_effect

    src = tmp_path / "deck.pptx"
    src.write_bytes(b"fake pptx")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 0
    # Warning line (JSON) and main JSON output both appear in combined output
    assert '"slide": 2' in result.output
    assert "image-only" in result.output
    # Last line must be valid JSON with the extracted text
    json_line = [line for line in result.output.splitlines() if line.startswith("{")][-1]
    data = json.loads(json_line)
    assert data["text"] == "Slide 1 content"


@patch("ingest_pipeline.extract_doc.extract_pdf", return_value="content")
def test_cli_json_output_has_all_fields(mock_extract: MagicMock, tmp_path: Path) -> None:
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"fake pdf")

    runner = CliRunner()
    result = runner.invoke(main, [str(src)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert set(data.keys()) == {"source_path", "offset", "total_chunks", "has_more", "text"}
