from pathlib import Path

import click

from .pipeline import run_ingest, run_ingest_add, run_ingest_amend, PipelineError


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
@click.option(
    "--max-binary-mb",
    default=None,
    type=int,
    help="Max binary file size in MB to process (0 = unlimited). Default: 50.",
)
def ingest(source: Path, destination: Path, rpm: int, prompts_dir: Path | None, max_binary_mb: int | None) -> None:
    """Ingest SOURCE directory into DESTINATION knowledge base."""
    try:
        run_ingest(source, destination, rpm, prompts_dir, max_binary_mb=max_binary_mb)
    except PipelineError as e:
        raise click.ClickException(str(e)) from e


@click.command("ingest-add")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("destination", type=click.Path(file_okay=False, path_type=Path))
@click.option("--rpm", default=60, show_default=True, help="API requests per minute.")
@click.option(
    "--prompts-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing prompt .txt files.",
)
@click.option(
    "--max-binary-mb",
    default=None,
    type=int,
    help="Max binary file size in MB to process (0 = unlimited). Default: 50.",
)
def ingest_add(source: Path, destination: Path, rpm: int, prompts_dir: Path | None, max_binary_mb: int | None) -> None:
    """Add new files from SOURCE to DESTINATION knowledge base."""
    try:
        run_ingest_add(source, destination, rpm, prompts_dir, max_binary_mb=max_binary_mb)
    except PipelineError as e:
        raise click.ClickException(str(e)) from e


@click.command("ingest-amend")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("destination", type=click.Path(file_okay=False, path_type=Path))
@click.option("--rpm", default=60, show_default=True, help="API requests per minute.")
@click.option(
    "--prompts-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing prompt .txt files.",
)
@click.option(
    "--max-binary-mb",
    default=None,
    type=int,
    help="Max binary file size in MB to process (0 = unlimited). Default: 50.",
)
def ingest_amend(source: Path, destination: Path, rpm: int, prompts_dir: Path | None, max_binary_mb: int | None) -> None:
    """Re-process amended files from SOURCE in DESTINATION knowledge base."""
    try:
        run_ingest_amend(source, destination, rpm, prompts_dir, max_binary_mb=max_binary_mb)
    except PipelineError as e:
        raise click.ClickException(str(e)) from e
