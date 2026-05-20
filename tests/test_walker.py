from pathlib import Path

import pytest

from ingest_pipeline.walker import classify_file, walk_source, mirror_dirs, dest_path_for


def test_classify_text_extensions() -> None:
    assert classify_file(Path("foo.py")) == "text"
    assert classify_file(Path("README.md")) == "text"
    assert classify_file(Path("data.json")) == "text"
    assert classify_file(Path("query.sql")) == "text"


def test_classify_binary_doc() -> None:
    assert classify_file(Path("report.pdf")) == "binary-doc"
    assert classify_file(Path("doc.docx")) == "binary-doc"
    assert classify_file(Path("slides.pptx")) == "binary-doc"
    assert classify_file(Path("data.xlsx")) == "binary-doc"


def test_classify_image() -> None:
    assert classify_file(Path("photo.png")) == "binary-image"
    assert classify_file(Path("icon.gif")) == "binary-image"
    assert classify_file(Path("bg.webp")) == "binary-image"


def test_classify_svg_as_text() -> None:
    assert classify_file(Path("diagram.svg")) == "text"


def test_classify_skip_names() -> None:
    assert classify_file(Path(".DS_Store")) is None


def test_classify_skip_suffixes() -> None:
    assert classify_file(Path("uv.lock")) is None
    assert classify_file(Path("foo.pyc")) is None


def test_classify_unknown_extension() -> None:
    assert classify_file(Path("binary.bin")) is None
    assert classify_file(Path("archive.tar")) is None


def test_walk_source_skips_git(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "README.md").write_text("# Hello")
    (tmp_path / "main.py").write_text("print('hi')")

    found = list(walk_source(tmp_path))
    names = [p.name for p in found]
    assert "HEAD" not in names
    assert "README.md" in names
    assert "main.py" in names


def test_walk_source_skips_pycache(tmp_path: Path) -> None:
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "foo.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "app.py").write_text("x = 1")

    found = list(walk_source(tmp_path))
    names = [p.name for p in found]
    assert "foo.pyc" not in names
    assert "app.py" in names


def test_mirror_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "file.py").write_text("x")
    (src / "c").mkdir()

    dst = tmp_path / "dst"
    dst.mkdir()
    mirror_dirs(src, dst)

    assert (dst / "a" / "b").is_dir()
    assert (dst / "c").is_dir()


def test_dest_path_for(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    src_file = src_root / "sub" / "foo.py"

    result = dest_path_for(src_file, src_root, dst_root)
    assert result == dst_root / "sub" / "foo.md"
