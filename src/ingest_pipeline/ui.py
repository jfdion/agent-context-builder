from contextlib import contextmanager
from collections.abc import Callable, Iterator
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .state import State, ManifestFile

console = Console()


def print_warning(message: str) -> None:
    console.print(f"[yellow]Warning: {message}[/yellow]")


def confirm_resume(step: int) -> bool:
    return click.confirm(
        f"Found existing run at step {step}. Resume?",
        default=True,
    )


def confirm_locale(locales: dict[str, int]) -> str:
    """Prompt user to choose synthesis language when multiple locales are detected."""
    distribution = ", ".join(f"{lang} ({count})" for lang, count in sorted(locales.items()))
    console.print(f"[yellow]Multiple locales detected: {distribution}[/yellow]")
    choices = sorted(locales.keys()) + ["auto"]
    dominant = max(locales, key=lambda k: locales[k])
    choice = click.prompt(
        "Select synthesis language",
        type=click.Choice(choices),
        default=dominant,
    )
    return choice if choice != "auto" else "und"


@contextmanager
def pipeline_progress() -> Iterator[Callable[[str], None]]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Initialising...", total=None)
        yield lambda description: progress.update(task, description=description)


def print_summary(
    state: State,
    records: list[ManifestFile],
    errors: list[str],
    elapsed: float,
) -> None:
    skipped_oversized = [r for r in records if r.status == "skipped-oversized"]

    lines = [
        f"Files processed : {len(records)}",
        f"Errors          : {len(errors)}",
        f"Input tokens    : {state.total_input_tokens:,}",
        f"Output tokens   : {state.total_output_tokens:,}",
        f"Elapsed         : {elapsed:.1f}s",
    ]

    if skipped_oversized:
        lines.append("")
        lines.append("Skipped (oversized):")
        for r in skipped_oversized:
            size_mb = r.size_bytes / (1024 * 1024)
            lines.append(f"  {r.source_path} ({size_mb:.1f} MB)")

    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors:
            lines.append(f"  {e}")

    style = "red" if errors else "green"
    console.print(Panel("\n".join(lines), title="Ingest Complete", style=style))
