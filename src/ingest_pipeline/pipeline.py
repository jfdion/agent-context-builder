import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .config import PIPELINE_VERSION, load_prompts
from .state import (
    Manifest, ManifestFile, State,
    ingest_dir, save_manifest, load_manifest, save_state, load_state,
    append_journal, journal_event,
)
from .walker import build_manifest_files, mirror_dirs
from .extract import extract_file
from .summarize import run_summarize_step
from .reduce import run_reduce_step
from .index import run_index_step

console = Console()


def _make_state(source: Path, dest: Path, rpm: int) -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version=PIPELINE_VERSION,
        command="ingest",
        source_root=str(source),
        destination_root=str(dest),
        started_at=now,
        last_updated=now,
        rpm=rpm,
        current_step=0,
        completed_steps=[],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


def run_ingest(
    source: Path,
    dest: Path,
    rpm: int,
    prompts_dir: Path | None = None,
) -> None:
    start_time = time.monotonic()

    if prompts_dir is None:
        prompts_dir = Path(__file__).parent.parent.parent / "prompts"

    # Load prompts (raises FileNotFoundError if any missing)
    prompts = load_prompts(prompts_dir)

    # Check for API key
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise click.ClickException("ANTHROPIC_API_KEY environment variable is not set.")

    dest.mkdir(parents=True, exist_ok=True)
    ingest_dir(dest).mkdir(parents=True, exist_ok=True)

    # Resume logic
    existing_state = load_state(dest)
    if existing_state is not None and existing_state.current_step < 4:
        resume = click.confirm(
            f"Found existing run at step {existing_state.current_step}. Resume?",
            default=True,
        )
        if not resume:
            existing_state = None

    state = existing_state or _make_state(source, dest, rpm)
    client = anthropic.Anthropic()

    all_errors: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Initialising...", total=None)

        # Step 0: Walk source, build manifest, mirror dirs
        if 0 not in state.completed_steps:
            progress.update(task, description="Step 0: Scanning source...")
            mirror_dirs(source, dest)
            records = build_manifest_files(source, dest)
            manifest = Manifest(
                version=PIPELINE_VERSION,
                source_root=str(source),
                destination_root=str(dest),
                created_at=datetime.now(timezone.utc).isoformat(),
                files=records,
            )
            save_manifest(dest, manifest)
            state.pending_files = [r.source_path for r in records]
            state.current_step = 0
            state.completed_steps.append(0)
            save_state(dest, state)
            append_journal(dest, journal_event("step_complete", step=0, file_count=len(records)))
        else:
            manifest = load_manifest(dest)
            records = manifest.files

        # Step 1: Extract
        if 1 not in state.completed_steps:
            progress.update(task, description="Step 1: Extracting...")

            def on_extract(path: str) -> None:
                progress.update(task, description=f"Extract: {Path(path).name}")

            extract_errors: list[str] = []
            for record in records:
                dest_path = Path(record.destination_path)
                if dest_path.exists():
                    continue
                on_extract(record.source_path)
                try:
                    extract_file(record, prompts, client, state.rpm, state, dest)
                    record.status = "extracted"
                    state.completed_files.append(record.source_path)
                except NotImplementedError as e:
                    record.status = "skipped"
                    extract_errors.append(f"{record.source_path}: {e}")
                except Exception as e:
                    record.status = "failed"
                    state.failed_files.append(record.source_path)
                    extract_errors.append(f"{record.source_path}: {e}")
                    append_journal(dest, journal_event("extract_error", file=record.source_path, error=str(e)))

            save_manifest(dest, manifest)
            all_errors.extend(extract_errors)
            state.current_step = 1
            state.completed_steps.append(1)
            save_state(dest, state)
            append_journal(dest, journal_event("step_complete", step=1))

        # Step 2: Summarize
        if 2 not in state.completed_steps:
            progress.update(task, description="Step 2: Summarizing...")

            def on_summarize(path: str) -> None:
                progress.update(task, description=f"Summarize: {Path(path).name}")

            extracted_records = [r for r in records if r.status in ("extracted", "pending")]
            summarize_errors = run_summarize_step(
                extracted_records, prompts, client, state.rpm, state, dest, on_file=on_summarize
            )
            all_errors.extend(summarize_errors)
            state.current_step = 2
            state.completed_steps.append(2)
            save_state(dest, state)
            append_journal(dest, journal_event("step_complete", step=2))

        # Step 3: Reduce
        if 3 not in state.completed_steps:
            progress.update(task, description="Step 3: Reducing...")

            def on_reduce(path: str) -> None:
                progress.update(task, description=f"Reduce: {Path(path).name}")

            reduce_errors = run_reduce_step(dest, prompts, client, state.rpm, state, on_dir=on_reduce)
            all_errors.extend(reduce_errors)
            state.current_step = 3
            state.completed_steps.append(3)
            save_state(dest, state)
            append_journal(dest, journal_event("step_complete", step=3))

        # Step 4: Index
        if 4 not in state.completed_steps:
            progress.update(task, description="Step 4: Indexing...")
            index_errors = run_index_step(dest, prompts, client, state.rpm, state)
            all_errors.extend(index_errors)
            state.current_step = 4
            state.completed_steps.append(4)
            save_state(dest, state)
            append_journal(dest, journal_event("step_complete", step=4))

    elapsed = time.monotonic() - start_time
    _print_summary(state, records, all_errors, elapsed)


def _print_summary(
    state: State,
    records: list[ManifestFile],
    errors: list[str],
    elapsed: float,
) -> None:
    lines = [
        f"Files processed : {len(records)}",
        f"Errors          : {len(errors)}",
        f"Input tokens    : {state.total_input_tokens:,}",
        f"Output tokens   : {state.total_output_tokens:,}",
        f"Elapsed         : {elapsed:.1f}s",
    ]
    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors:
            lines.append(f"  {e}")

    style = "red" if errors else "green"
    console.print(Panel("\n".join(lines), title="Ingest Complete", style=style))
