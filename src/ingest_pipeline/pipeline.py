import hashlib
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .config import PIPELINE_VERSION, load_prompts
from .state import (
    Manifest, ManifestFile, State,
    ingest_dir, save_manifest, load_manifest, save_state, load_state,
    append_journal, journal_event,
)
from .walker import build_manifest_files, find_symlinks, mirror_dirs
from .extract import extract_file, run_extract_step
from .summarize import run_summarize_step
from .reduce import run_reduce_step, run_reduce_from_dir, _extract_locale
from .index import run_index_step
from .ui import pipeline_progress, print_warning, print_summary, confirm_resume, confirm_locale


class PipelineError(Exception):
    """Raised when the pipeline cannot proceed due to a configuration or precondition failure."""


def _file_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _collect_locale_distribution(dest_root: Path) -> dict[str, int]:
    counts: Counter = Counter()
    for summary_path in dest_root.rglob("_summary_*.md"):
        locale = _extract_locale(summary_path.read_text(encoding="utf-8"))
        if locale != "und":
            counts[locale] += 1
    return dict(counts)


def _resolve_locale(dest_root: Path, state: State, forced_locale: str | None = None) -> str:
    if forced_locale is not None:
        state.locale = forced_locale
        return forced_locale
    locales = _collect_locale_distribution(dest_root)
    if not locales:
        return "und"
    if len(locales) == 1:
        state.locale = next(iter(locales))
        return state.locale
    chosen = confirm_locale(locales)
    state.locale = chosen
    return chosen


def _make_state(source: Path, dest: Path, rpm: int, max_binary_mb: int = 50, cmd: str = "ingest") -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version=PIPELINE_VERSION,
        command=cmd,
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
        max_binary_mb=max_binary_mb,
    )


def _init_pipeline(
    prompts_dir: Path | None,
) -> tuple[dict[str, str], anthropic.Anthropic]:
    if prompts_dir is None:
        prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    prompts = load_prompts(prompts_dir)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise PipelineError("ANTHROPIC_API_KEY environment variable is not set.")
    return prompts, anthropic.Anthropic()


def _step_walk_source(
    source: Path,
    dest: Path,
    state: State,
    on_progress: Callable[[str], None],
    on_warning: Callable[[str], None],
    cmd: str,
) -> tuple[Manifest, list[ManifestFile]]:
    if 0 not in state.completed_steps:
        on_progress("Step 0: Scanning source...")
        mirror_dirs(source, dest)
        records, symlinks = build_manifest_files(source, dest)
        for symlink_path in symlinks:
            on_warning(f"Symlink skipped: {symlink_path}")
            append_journal(dest, journal_event("file_skipped", step=0, source=str(symlink_path), reason="symlink", cmd=state.command))
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
        append_journal(dest, journal_event("step_complete", step=0, file_count=len(records), cmd=state.command))
    else:
        manifest = load_manifest(dest)
        records = manifest.files

    return manifest, records


def _step_extract(
    records: list[ManifestFile],
    manifest: Manifest,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    state: State,
    dest: Path,
    on_progress: Callable[[str], None],
) -> list[str]:
    if 1 not in state.completed_steps:
        on_progress("Step 1: Extracting...")
        extract_errors = run_extract_step(
            records, prompts, client, state.rpm, state, dest,
            on_file=lambda p: on_progress(f"Extract: {Path(p).name}"),
        )
        save_manifest(dest, manifest)
        state.current_step = 1
        state.completed_steps.append(1)
        save_state(dest, state)
        append_journal(dest, journal_event("step_complete", step=1, cmd=state.command))
        return extract_errors
    return []


def _step_summarize(
    records: list[ManifestFile],
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    state: State,
    dest: Path,
    on_progress: Callable[[str], None],
) -> list[str]:
    if 2 not in state.completed_steps:
        on_progress("Step 2: Summarizing...")

        def on_summarize(path: str) -> None:
            on_progress(f"Summarize: {Path(path).name}")

        extracted_records = [r for r in records if r.status in ("extracted", "pending")]
        summarize_errors = run_summarize_step(
            extracted_records, prompts, client, state.rpm, state, dest, on_file=on_summarize
        )
        state.current_step = 2
        state.completed_steps.append(2)
        save_state(dest, state)
        append_journal(dest, journal_event("step_complete", step=2, cmd=state.command))
        return summarize_errors
    return []


def _step_reduce(
    dest: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    state: State,
    on_progress: Callable[[str], None],
) -> list[str]:
    if 3 not in state.completed_steps:
        on_progress("Step 3: Reducing...")

        def on_reduce(path: str) -> None:
            on_progress(f"Reduce: {Path(path).name}")

        reduce_errors = run_reduce_step(dest, prompts, client, state.rpm, state, on_dir=on_reduce)
        state.current_step = 3
        state.completed_steps.append(3)
        save_state(dest, state)
        append_journal(dest, journal_event("step_complete", step=3, cmd=state.command))
        return reduce_errors
    return []


def _step_index(
    dest: Path,
    prompts: dict[str, str],
    client: anthropic.Anthropic,
    state: State,
    on_progress: Callable[[str], None],
) -> list[str]:
    if 4 not in state.completed_steps:
        on_progress("Step 4: Indexing...")
        index_errors = run_index_step(dest, prompts, client, state.rpm, state)
        state.current_step = 4
        state.completed_steps.append(4)
        save_state(dest, state)
        append_journal(dest, journal_event("step_complete", step=4, cmd=state.command))
        return index_errors
    return []


def run_ingest(
    source: Path,
    dest: Path,
    rpm: int,
    prompts_dir: Path | None = None,
    max_binary_mb: int | None = None,
    locale: str | None = None,
    cmd: str = "ingest",
) -> None:
    start_time = time.monotonic()
    prompts, client = _init_pipeline(prompts_dir)

    dest.mkdir(parents=True, exist_ok=True)
    ingest_dir(dest).mkdir(parents=True, exist_ok=True)

    # Resume logic
    existing_state = load_state(dest)
    if existing_state is not None and existing_state.current_step < 4:
        resume = confirm_resume(existing_state.current_step)
        if not resume:
            existing_state = None
        elif max_binary_mb is not None:
            existing_state.max_binary_mb = max_binary_mb

    effective_max_binary_mb = max_binary_mb if max_binary_mb is not None else 50
    state = existing_state or _make_state(source, dest, rpm, max_binary_mb=effective_max_binary_mb, cmd=cmd)

    all_errors: list[str] = []

    with pipeline_progress() as on_progress:
        manifest, records = _step_walk_source(source, dest, state, on_progress, print_warning, cmd)
        all_errors.extend(_step_extract(records, manifest, prompts, client, state, dest, on_progress))
        all_errors.extend(_step_summarize(records, prompts, client, state, dest, on_progress))

    _resolve_locale(dest, state, forced_locale=locale)

    with pipeline_progress() as on_progress:
        all_errors.extend(_step_reduce(dest, prompts, client, state, on_progress))
        all_errors.extend(_step_index(dest, prompts, client, state, on_progress))

    elapsed = time.monotonic() - start_time
    print_summary(state, records, all_errors, elapsed)



def run_ingest_add(
    source: Path,
    dest: Path,
    rpm: int,
    prompts_dir: Path | None = None,
    max_binary_mb: int | None = None,
    locale: str | None = None,
) -> None:
    start_time = time.monotonic()
    prompts, client = _init_pipeline(prompts_dir)

    if not (ingest_dir(dest) / "manifest.json").exists():
        raise PipelineError(f"No manifest found at {dest}. Run 'ingest' first.")

    manifest = load_manifest(dest)

    source_resolved = source.resolve()
    source_root_resolved = Path(manifest.source_root).resolve()

    try:
        relative = source_resolved.relative_to(source_root_resolved)
    except ValueError:
        raise PipelineError(
            f"Source '{source}' is not under manifest source root '{manifest.source_root}'."
        )

    dest_subdir = dest / relative
    dest_subdir.mkdir(parents=True, exist_ok=True)
    mirror_dirs(source, dest_subdir)

    new_records, symlinks = build_manifest_files(source, dest_subdir)
    for symlink_path in symlinks:
        print_warning(f"Symlink skipped: {symlink_path}")
        append_journal(dest, journal_event("file_skipped", step=0, source=str(symlink_path), reason="symlink", cmd="ingest-add"))

    existing_source_paths = {str(Path(r.source_path).resolve()) for r in manifest.files}

    added_records: list[ManifestFile] = []
    for record in new_records:
        if str(Path(record.source_path).resolve()) in existing_source_paths:
            print_warning(f"{record.source_path} already in manifest, skipping.")
        else:
            manifest.files.append(record)
            added_records.append(record)

    save_manifest(dest, manifest)

    now = datetime.now(timezone.utc).isoformat()
    effective_max_binary_mb = max_binary_mb if max_binary_mb is not None else 50
    state = load_state(dest)
    if state is None:
        state = _make_state(source, dest, rpm, max_binary_mb=effective_max_binary_mb, cmd="ingest-add")
    else:
        state.last_updated = now
        state.command = "ingest-add"
        if max_binary_mb is not None:
            state.max_binary_mb = max_binary_mb

    append_journal(
        dest,
        journal_event(
            "source_added",
            new_source=str(source),
            derived_dest=str(dest_subdir),
            files_added=len(added_records),
            cmd="ingest-add",
        ),
    )

    all_errors: list[str] = []

    with pipeline_progress() as on_progress:
        on_progress("Step 1: Extracting...")
        extract_errors = run_extract_step(added_records, prompts, client, rpm, state, dest)
        all_errors.extend(extract_errors)
        save_manifest(dest, manifest)

        on_progress("Step 2: Summarizing...")
        extracted_records = [r for r in added_records if r.status in ("extracted", "pending")]
        summarize_errors = run_summarize_step(extracted_records, prompts, client, rpm, state, dest)
        all_errors.extend(summarize_errors)

    _resolve_locale(dest, state, forced_locale=locale)

    with pipeline_progress() as on_progress:
        on_progress("Step 3: Reducing...")

        def on_reduce_add(path: str) -> None:
            on_progress(f"Reduce: {Path(path).name}")

        reduce_errors = run_reduce_from_dir(
            start_dir=dest_subdir,
            dest_root=dest,
            prompts=prompts,
            client=client,
            rpm=rpm,
            state=state,
            on_dir=on_reduce_add,
        )
        all_errors.extend(reduce_errors)

        on_progress("Step 4: Indexing...")
        index_path = dest / "index.md"
        if index_path.exists():
            index_path.unlink()
        index_errors = run_index_step(dest, prompts, client, rpm, state)
        all_errors.extend(index_errors)

    save_state(dest, state)
    elapsed = time.monotonic() - start_time
    print_summary(state, manifest.files, all_errors, elapsed)


def run_ingest_amend(
    source: Path,
    dest: Path,
    rpm: int,
    prompts_dir: Path | None = None,
    max_binary_mb: int | None = None,
    locale: str | None = None,
) -> None:
    start_time = time.monotonic()
    prompts, client = _init_pipeline(prompts_dir)

    if not (ingest_dir(dest) / "manifest.json").exists():
        raise PipelineError(f"No manifest found at {dest}. Run 'ingest' first.")

    manifest = load_manifest(dest)

    source_resolved = source.resolve()
    affected = [
        r for r in manifest.files
        if str(Path(r.source_path).resolve()).startswith(str(source_resolved))
    ]

    if not affected:
        raise PipelineError(
            f"No manifest records found for {source}. Nothing to amend."
        )

    # Partition affected records into changed vs unchanged by content hash
    changed: list[ManifestFile] = []
    unchanged: list[ManifestFile] = []
    for record in affected:
        src_path = Path(record.source_path)
        if src_path.exists() and _file_hash(src_path) == record.id:
            unchanged.append(record)
        else:
            changed.append(record)

    # Delete generated output only for changed files
    for record in changed:
        dest_path = Path(record.destination_path)
        if dest_path.exists():
            dest_path.unlink()
        summary_path = dest_path.parent / f"_summary_{dest_path.stem}.md"
        if summary_path.exists():
            summary_path.unlink()
        record.status = "pending"

    dest_subdirs: set[Path] = {Path(r.destination_path).parent for r in changed} or {
        Path(r.destination_path).parent for r in affected
    }

    for dest_subdir in dest_subdirs:
        current = dest_subdir
        while True:
            for reduce_file in current.glob("_reduce_*.md"):
                reduce_file.unlink()
            if current == dest:
                break
            current = current.parent

    index_path = dest / "index.md"
    if index_path.exists():
        index_path.unlink()

    save_manifest(dest, manifest)

    now = datetime.now(timezone.utc).isoformat()
    effective_max_binary_mb = max_binary_mb if max_binary_mb is not None else 50
    state = load_state(dest)
    if state is None:
        state = _make_state(source, dest, rpm, max_binary_mb=effective_max_binary_mb, cmd="ingest-amend")
    else:
        state.current_step = 0
        state.completed_steps = []
        state.command = "ingest-amend"
        state.last_updated = now
        if max_binary_mb is not None:
            state.max_binary_mb = max_binary_mb

    append_journal(
        dest,
        journal_event(
            "amend_start",
            scope=str(source),
            files_reset=len(changed),
            files_unchanged=len(unchanged),
            cmd="ingest-amend",
        ),
    )
    for record in unchanged:
        append_journal(dest, journal_event(
            "file_unchanged", step=1, source=record.source_path, reason="content_hash_match", cmd="ingest-amend",
        ))

    all_errors: list[str] = []

    with pipeline_progress() as on_progress:
        on_progress("Step 1: Extracting...")
        extract_errors = run_extract_step(changed, prompts, client, rpm, state, dest)
        all_errors.extend(extract_errors)
        save_manifest(dest, manifest)

        on_progress("Step 2: Summarizing...")
        extracted_records = [r for r in changed if r.status in ("extracted", "pending")]
        summarize_errors = run_summarize_step(extracted_records, prompts, client, rpm, state, dest)
        all_errors.extend(summarize_errors)

    _resolve_locale(dest, state, forced_locale=locale)

    with pipeline_progress() as on_progress:
        on_progress("Step 3: Reducing...")

        def on_reduce_amend(path: str) -> None:
            on_progress(f"Reduce: {Path(path).name}")

        for dest_subdir in dest_subdirs:
            reduce_errors = run_reduce_from_dir(
                start_dir=dest_subdir,
                dest_root=dest,
                prompts=prompts,
                client=client,
                rpm=rpm,
                state=state,
                on_dir=on_reduce_amend,
            )
            all_errors.extend(reduce_errors)

        on_progress("Step 4: Indexing...")
        index_errors = run_index_step(dest, prompts, client, rpm, state)
        all_errors.extend(index_errors)

    save_state(dest, state)
    elapsed = time.monotonic() - start_time
    print_summary(state, manifest.files, all_errors, elapsed)
