"""CLI for binary-doc extraction — used by agent-only commands via Bash."""
import json
import sys
from pathlib import Path

import click

from .config import BINARY_DOC_EXTENSIONS, CHUNK_SIZE_BYTES
from .extract import extract_docx, extract_pdf, extract_pptx, extract_xlsx


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE_BYTES) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        line_bytes = len((line + "\n").encode("utf-8"))
        if current_size + line_bytes > chunk_size and current:
            chunks.append("\n".join(current))
            current, current_size = [line], line_bytes
        else:
            current.append(line)
            current_size += line_bytes
    if current:
        chunks.append("\n".join(current))
    return chunks if chunks else [""]


@click.command()
@click.argument("source_path", type=click.Path(exists=True))
@click.option("--offset", default=0, type=int, help="Chunk index to return (0-based).")
def main(source_path: str, offset: int) -> None:
    """Extract text from a binary document and return one chunk as JSON.

    Output fields: source_path, offset, total_chunks, has_more, text.
    Warnings (e.g. image-only slides) are written to stderr.
    Exit code 1 on unsupported extension or out-of-range offset.
    """
    path = Path(source_path).resolve()
    suffix = path.suffix.lower()

    if suffix not in BINARY_DOC_EXTENSIONS:
        click.echo(f"Unsupported extension: {suffix}", err=True)
        sys.exit(1)

    warnings: list[dict] = []
    if suffix == ".pdf":
        raw = extract_pdf(path)
    elif suffix == ".docx":
        raw = extract_docx(path)
    elif suffix == ".pptx":
        raw = extract_pptx(path, warnings)
    else:
        raw = extract_xlsx(path)

    for w in warnings:
        click.echo(json.dumps(w), err=True)

    chunks = _chunk_text(raw)
    total = len(chunks)

    if offset >= total:
        click.echo(f"Offset {offset} out of range (total chunks: {total})", err=True)
        sys.exit(1)

    click.echo(json.dumps({
        "source_path": str(path),
        "offset": offset,
        "total_chunks": total,
        "has_more": offset < total - 1,
        "text": chunks[offset],
    }))


if __name__ == "__main__":
    main()
