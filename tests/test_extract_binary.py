from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, PropertyMock
from datetime import datetime, timezone

import pytest

from ingest_pipeline.extract import (
    _extract_pdf,
    _extract_docx,
    _extract_pptx,
    _extract_xlsx,
    extract_binary_doc,
    extract_image,
)
from ingest_pipeline.state import ManifestFile, State, ingest_dir


def _make_record(source_path: str, dest_path: str, category: str, size_bytes: int = 100) -> ManifestFile:
    return ManifestFile(
        id="test01",
        source_path=source_path,
        destination_path=dest_path,
        category=category,
        size_bytes=size_bytes,
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


# ==================== PDF Tests ====================


@patch("ingest_pipeline.extract.fitz")
def test_extract_pdf_simple(mock_fitz: MagicMock) -> None:
    """Test PDF extraction with simple text on 2 pages."""
    mock_doc = MagicMock()
    mock_page1 = MagicMock()
    mock_page1.get_text.return_value = "Page 1 content\nSome text here"
    mock_page2 = MagicMock()
    mock_page2.get_text.return_value = "Page 2 content\nMore text here"
    mock_doc.__iter__ = Mock(return_value=iter([mock_page1, mock_page2]))
    mock_doc.__len__ = Mock(return_value=2)
    mock_fitz.open.return_value = mock_doc

    result = _extract_pdf(Path("/fake/doc.pdf"))

    assert "Page 1 content" in result
    assert "Page 2 content" in result
    assert "Some text here" in result
    mock_doc.close.assert_called_once()


@patch("ingest_pipeline.extract.fitz")
def test_extract_pdf_strips_headers_footers(mock_fitz: MagicMock) -> None:
    """Test PDF extraction strips repeated headers/footers appearing on >=80% of pages."""
    mock_doc = MagicMock()
    pages = []
    # 5 pages with repeated header and footer
    for i in range(5):
        page = MagicMock()
        page.get_text.return_value = f"Header Text\nPage {i+1} unique content\nFooter Text"
        pages.append(page)

    mock_doc.__iter__ = Mock(return_value=iter(pages))
    mock_doc.__len__ = Mock(return_value=5)
    mock_fitz.open.return_value = mock_doc

    result = _extract_pdf(Path("/fake/doc.pdf"))

    # Headers and footers should be stripped (appear on all 5 pages = 100% >= 80%)
    assert "Header Text" not in result
    assert "Footer Text" not in result
    # Unique content should remain
    assert "Page 1 unique content" in result
    assert "Page 5 unique content" in result


@patch("ingest_pipeline.extract.fitz")
@patch("ingest_pipeline.extract.call_claude")
def test_extract_pdf_chunking_large(
    mock_call_claude: MagicMock,
    mock_fitz: MagicMock,
    tmp_path: Path,
) -> None:
    """Test PDF with text > 200KB is chunked and each chunk calls Claude."""
    # Create a large text (over 200KB)
    large_line = "x" * 1000  # 1KB per line
    lines = [f"{large_line} {i}" for i in range(250)]  # ~250KB total
    large_text = "\n".join(lines)

    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = large_text
    mock_doc.__iter__ = Mock(return_value=iter([mock_page]))
    mock_doc.__len__ = Mock(return_value=1)
    mock_fitz.open.return_value = mock_doc

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "large.pdf"
    src.write_bytes(b"fake pdf content")
    dest_file = dest / "large.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    mock_call_claude.side_effect = ["Chunk 1 result", "Chunk 2 result"]

    extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    # Should call Claude twice (for 2 chunks)
    assert mock_call_claude.call_count == 2
    content = dest_file.read_text(encoding="utf-8")
    assert "Chunk 1 result" in content
    assert "Chunk 2 result" in content


# ==================== DOCX Tests ====================


@patch("ingest_pipeline.extract.Document")
@patch("ingest_pipeline.extract.call_claude")
def test_extract_docx_simple(
    mock_call_claude: MagicMock,
    mock_document: MagicMock,
    tmp_path: Path,
) -> None:
    """Test DOCX extraction with simple paragraphs."""
    mock_doc = MagicMock()
    mock_para1 = MagicMock()
    mock_para1.text = "First paragraph"
    mock_para2 = MagicMock()
    mock_para2.text = "Second paragraph"
    mock_doc.paragraphs = [mock_para1, mock_para2]
    mock_document.return_value = mock_doc

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")
    dest_file = dest / "doc.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    mock_call_claude.return_value = "Extracted docx content"

    extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    mock_call_claude.assert_called_once()
    content = dest_file.read_text(encoding="utf-8")
    assert "Extracted docx content" in content
    assert "source:" in content


# ==================== PPTX Tests ====================


@patch("ingest_pipeline.extract.Presentation")
@patch("ingest_pipeline.extract.call_claude")
def test_extract_pptx_with_text(
    mock_call_claude: MagicMock,
    mock_presentation: MagicMock,
    tmp_path: Path,
) -> None:
    """Test PPTX extraction with text slides."""
    mock_prs = MagicMock()

    # Slide 1 with text
    slide1 = MagicMock()
    shape1 = MagicMock()
    shape1.text_frame = MagicMock()
    shape1.text = "Slide 1 title"
    slide1.shapes = [shape1]
    slide1.has_notes_slide = False

    # Slide 2 with text and speaker notes
    slide2 = MagicMock()
    shape2 = MagicMock()
    shape2.text_frame = MagicMock()
    shape2.text = "Slide 2 content"
    slide2.shapes = [shape2]
    slide2.has_notes_slide = True
    slide2.notes_slide.notes_text_frame.text = "Speaker notes here"

    mock_prs.slides = [slide1, slide2]
    mock_presentation.return_value = mock_prs

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "pres.pptx"
    src.write_bytes(b"fake pptx")
    dest_file = dest / "pres.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    mock_call_claude.return_value = "Extracted pptx content"

    extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    mock_call_claude.assert_called_once()
    content = dest_file.read_text(encoding="utf-8")
    assert "Extracted pptx content" in content


@patch("ingest_pipeline.extract.Presentation")
@patch("ingest_pipeline.extract.call_claude")
@patch("ingest_pipeline.extract.append_journal")
def test_extract_pptx_image_only_slide_warning(
    mock_append_journal: MagicMock,
    mock_call_claude: MagicMock,
    mock_presentation: MagicMock,
    tmp_path: Path,
) -> None:
    """Test PPTX with image-only slide generates warning and skips empty slide."""
    mock_prs = MagicMock()

    # Slide 1 with text
    slide1 = MagicMock()
    shape1 = MagicMock()
    shape1.text_frame = MagicMock()
    shape1.text = "Slide 1 text"
    slide1.shapes = [shape1]
    slide1.has_notes_slide = False

    # Slide 2 image-only (no text_frame)
    slide2 = MagicMock()
    shape2_no_text = MagicMock(spec=[])  # No text_frame attribute
    slide2.shapes = [shape2_no_text]
    slide2.has_notes_slide = False

    # Slide 3 with text
    slide3 = MagicMock()
    shape3 = MagicMock()
    shape3.text_frame = MagicMock()
    shape3.text = "Slide 3 text"
    slide3.shapes = [shape3]
    slide3.has_notes_slide = False

    mock_prs.slides = [slide1, slide2, slide3]
    mock_presentation.return_value = mock_prs

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "pres.pptx"
    src.write_bytes(b"fake pptx")
    dest_file = dest / "pres.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    mock_call_claude.return_value = "Extracted pptx content"

    extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    # Check that journal warning was logged for slide 2
    warning_calls = [
        call for call in mock_append_journal.call_args_list
        if len(call[0]) > 1 and call[0][1].get("event") == "file_skipped"
    ]
    assert len(warning_calls) == 1
    assert warning_calls[0][0][1]["slide"] == 2
    assert warning_calls[0][0][1]["reason"] == "image-only slide"
    assert warning_calls[0][0][1]["step"] == 1


# ==================== XLSX Tests ====================


@patch("ingest_pipeline.extract.load_workbook")
@patch("ingest_pipeline.extract.call_claude")
def test_extract_xlsx_simple(
    mock_call_claude: MagicMock,
    mock_load_workbook: MagicMock,
    tmp_path: Path,
) -> None:
    """Test XLSX extraction with 2 sheets."""
    mock_wb = MagicMock()
    mock_wb.sheetnames = ["Sheet1", "Sheet2"]

    # Sheet1 data
    sheet1 = MagicMock()
    sheet1.iter_rows.return_value = [
        ("Name", "Age"),
        ("Alice", 30),
        ("Bob", 25),
    ]

    # Sheet2 data
    sheet2 = MagicMock()
    sheet2.iter_rows.return_value = [
        ("Product", "Price"),
        ("Apple", 1.5),
    ]

    mock_wb.__getitem__ = lambda self, key: sheet1 if key == "Sheet1" else sheet2
    mock_load_workbook.return_value = mock_wb

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "data.xlsx"
    src.write_bytes(b"fake xlsx")
    dest_file = dest / "data.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    mock_call_claude.return_value = "Extracted xlsx content"

    extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    mock_call_claude.assert_called_once()
    content = dest_file.read_text(encoding="utf-8")
    assert "Extracted xlsx content" in content


@patch("ingest_pipeline.extract.load_workbook")
def test_extract_xlsx_empty_cells(mock_load_workbook: MagicMock) -> None:
    """Test XLSX with empty cells (None values) are handled correctly."""
    mock_wb = MagicMock()
    mock_wb.sheetnames = ["Sheet1"]

    sheet1 = MagicMock()
    sheet1.iter_rows.return_value = [
        ("Name", None, "Age"),
        ("Alice", "", 30),
        (None, "Bob", None),
    ]

    mock_wb.__getitem__ = lambda self, key: sheet1
    mock_load_workbook.return_value = mock_wb

    result = _extract_xlsx(Path("/fake/data.xlsx"))

    # None values should become empty strings
    assert "| Name |  | Age |" in result
    assert "| Alice |  | 30 |" in result
    assert "|  | Bob |  |" in result


# ==================== Error Handling Tests ====================


@patch("ingest_pipeline.extract.fitz")
def test_extract_pdf_error_propagates(mock_fitz: MagicMock, tmp_path: Path) -> None:
    """Test that PDF extraction error propagates to caller."""
    mock_fitz.open.side_effect = Exception("Corrupted PDF")

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "bad.pdf"
    src.write_bytes(b"bad pdf")
    dest_file = dest / "bad.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    with pytest.raises(Exception, match="Corrupted PDF"):
        extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)


@patch("ingest_pipeline.extract.Presentation")
@patch("ingest_pipeline.extract.call_claude")
def test_extract_pptx_call_claude_error_propagates(
    mock_call_claude: MagicMock,
    mock_presentation: MagicMock,
    tmp_path: Path,
) -> None:
    """Test that call_claude error on PPTX propagates."""
    mock_prs = MagicMock()
    slide = MagicMock()
    shape = MagicMock()
    shape.text_frame = MagicMock()
    shape.text = "Some text"
    slide.shapes = [shape]
    slide.has_notes_slide = False
    mock_prs.slides = [slide]
    mock_presentation.return_value = mock_prs

    mock_call_claude.side_effect = Exception("API error")

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "pres.pptx"
    src.write_bytes(b"fake pptx")
    dest_file = dest / "pres.md"
    record = _make_record(str(src), str(dest_file), "binary-doc")
    state = _make_state(dest)
    prompts = {"extract_text": "Extract text prompt"}

    with pytest.raises(Exception, match="API error"):
        extract_binary_doc(src, dest_file, record, prompts, MagicMock(), 60, state, dest)


# ==================== Edge Cases ====================


# ==================== Image Tests ====================


@pytest.mark.parametrize("extension,expected_media_type", [
    (".png", "image/png"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".gif", "image/gif"),
    (".webp", "image/webp"),
])
@patch("ingest_pipeline.extract.call_claude_vision")
def test_extract_image_media_type_mapping(
    mock_vision: MagicMock,
    extension: str,
    expected_media_type: str,
    tmp_path: Path,
) -> None:
    """Test that each image extension maps to the correct media_type."""
    mock_vision.return_value = "## Diagram Type\nflowchart\n\n## Summary\nA test.\n\n## Representation\n- node"

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / f"img{extension}"
    src.write_bytes(b"\x89PNG\r\n")  # fake image bytes
    dest_file = dest / f"img{extension}.md"
    record = _make_record(str(src), str(dest_file), "binary-image")
    state = _make_state(dest)
    prompts = {"extract_image": "Analyze this image."}

    extract_image(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    _, kwargs = mock_vision.call_args
    positional_args = mock_vision.call_args[0]
    # media_type is the 5th positional argument (index 4)
    assert positional_args[4] == expected_media_type


@patch("ingest_pipeline.extract.call_claude_vision")
def test_extract_image_nominal_png(mock_vision: MagicMock, tmp_path: Path) -> None:
    """Test nominal PNG extraction: front matter + Claude response in output."""
    mock_vision.return_value = "## Diagram Type\narchitecture diagram\n\n## Summary\nShows services.\n\n## Representation\n- Service A"

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "diagram.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    dest_file = dest / "diagram.png.md"
    record = _make_record(str(src), str(dest_file), "binary-image")
    state = _make_state(dest)
    prompts = {"extract_image": "Analyze this image."}

    extract_image(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    content = dest_file.read_text(encoding="utf-8")
    assert "source:" in content
    assert "category: binary-image" in content
    assert "extracted_at:" in content
    assert "ingest_id:" in content
    assert "## Diagram Type" in content
    assert "## Summary" in content
    assert "## Representation" in content
    mock_vision.assert_called_once()


@patch("ingest_pipeline.extract.call_claude_vision")
def test_extract_image_nominal_jpg(mock_vision: MagicMock, tmp_path: Path) -> None:
    """Test nominal JPG extraction uses image/jpeg media_type."""
    mock_vision.return_value = "## Diagram Type\nphoto\n\n## Summary\nA photo.\n\n## Representation\n- N/A"

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    dest_file = dest / "photo.jpg.md"
    record = _make_record(str(src), str(dest_file), "binary-image")
    state = _make_state(dest)
    prompts = {"extract_image": "Analyze this image."}

    extract_image(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    positional_args = mock_vision.call_args[0]
    assert positional_args[4] == "image/jpeg"


@patch("ingest_pipeline.extract.call_claude_vision")
def test_extract_image_throttle_rpm(mock_vision: MagicMock, tmp_path: Path) -> None:
    """Test that rpm is forwarded to call_claude_vision (throttle executes inside api.py)."""
    mock_vision.return_value = "## Diagram Type\nN/A\n\n## Summary\nN/A\n\n## Representation\nN/A"

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "img.png"
    src.write_bytes(b"\x89PNG\r\n")
    dest_file = dest / "img.png.md"
    record = _make_record(str(src), str(dest_file), "binary-image")
    state = _make_state(dest)
    prompts = {"extract_image": "Analyze this image."}

    extract_image(src, dest_file, record, prompts, MagicMock(), 30, state, dest)

    positional_args = mock_vision.call_args[0]
    # rpm is the 8th positional argument (index 7)
    assert positional_args[7] == 30


@patch("ingest_pipeline.extract.call_claude_vision")
def test_extract_image_token_logging(mock_vision: MagicMock, tmp_path: Path) -> None:
    """Test that call_claude_vision is called with state and dest_root for token logging."""
    mock_vision.return_value = "## Diagram Type\nN/A\n\n## Summary\nN/A\n\n## Representation\nN/A"

    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()

    src = tmp_path / "img.png"
    src.write_bytes(b"\x89PNG\r\n")
    dest_file = dest / "img.png.md"
    record = _make_record(str(src), str(dest_file), "binary-image")
    state = _make_state(dest)
    prompts = {"extract_image": "Analyze this image."}

    extract_image(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

    positional_args = mock_vision.call_args[0]
    # state is the 9th arg (index 8), dest_root is 10th (index 9)
    assert positional_args[8] is state
    assert positional_args[9] == dest


@patch("ingest_pipeline.extract.load_workbook")
def test_extract_xlsx_merged_cells(mock_load_workbook: MagicMock) -> None:
    """Test XLSX with merged cells (value appears once)."""
    mock_wb = MagicMock()
    mock_wb.sheetnames = ["Sheet1"]

    sheet1 = MagicMock()
    # Simulate merged cells: master cell has value, slaves have None
    sheet1.iter_rows.return_value = [
        ("Merged Title", None, None),  # 3 cells merged
        ("A", "B", "C"),
    ]

    mock_wb.__getitem__ = lambda self, key: sheet1
    mock_load_workbook.return_value = mock_wb

    result = _extract_xlsx(Path("/fake/data.xlsx"))

    # Master cell value appears, slaves are empty
    assert "| Merged Title |  |  |" in result
    assert "| A | B | C |" in result
