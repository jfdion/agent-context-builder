from pathlib import Path

import click

from .pipeline import run_ingest


@click.command()
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("destination", type=click.Path(file_okay=False, path_type=Path))
@click.option("--rpm", default=60, show_default=True, help="API requests per minute.")
@click.option(
    "--prompts-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing prompt .txt files.",
)
def ingest(source: Path, destination: Path, rpm: int, prompts_dir: Path | None) -> None:
    """Ingest SOURCE directory into DESTINATION knowledge base."""
    run_ingest(source, destination, rpm, prompts_dir)


@click.command("ingest-add")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path))
def ingest_add(paths: tuple[Path, ...]) -> None:
    """Add new files to an existing knowledge base."""
    raise NotImplementedError("Phase 4: ingest-add not yet implemented")


@click.command("ingest-amend")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path))
def ingest_amend(paths: tuple[Path, ...]) -> None:
    """Re-process amended files in an existing knowledge base."""
    raise NotImplementedError("Phase 4: ingest-amend not yet implemented")
