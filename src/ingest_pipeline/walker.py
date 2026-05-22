import hashlib
from pathlib import Path
from typing import Iterator

from .config import (
    TEXT_EXTENSIONS,
    BINARY_DOC_EXTENSIONS,
    BINARY_IMAGE_EXTENSIONS,
    SKIP_NAMES,
    SKIP_SUFFIXES,
    SKIP_DIRS,
)
from .state import ManifestFile


def classify_file(path: Path) -> str | None:
    if path.name in SKIP_NAMES:
        return None
    if path.suffix.lower() in SKIP_SUFFIXES:
        return None
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in BINARY_DOC_EXTENSIONS:
        return "binary-doc"
    if suffix in BINARY_IMAGE_EXTENSIONS:
        return "binary-image"
    return None


def walk_source(source_root: Path) -> Iterator[Path]:
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def find_symlinks(source_root: Path) -> list[Path]:
    result = []
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            if not any(part in SKIP_DIRS for part in path.parts):
                result.append(path)
    return result


def mirror_dirs(source_root: Path, dest_root: Path) -> None:
    for path in sorted(source_root.rglob("*")):
        if not path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(source_root)
        (dest_root / rel).mkdir(parents=True, exist_ok=True)


def dest_path_for(source_path: Path, source_root: Path, dest_root: Path) -> Path:
    rel = source_path.relative_to(source_root)
    return dest_root / rel.with_suffix(".md")


def build_manifest_files(
    source_root: Path,
    dest_root: Path,
) -> tuple[list[ManifestFile], list[Path]]:
    from datetime import datetime, timezone

    records: list[ManifestFile] = []
    symlinks = find_symlinks(source_root)

    for path in walk_source(source_root):
        category = classify_file(path)
        if category is None:
            continue
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        file_id = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        dest = dest_path_for(path, source_root, dest_root)
        records.append(ManifestFile(
            id=file_id,
            source_path=str(path),
            destination_path=str(dest),
            category=category,
            size_bytes=stat.st_size,
            mtime=mtime,
            status="pending",
        ))
    return records, symlinks
