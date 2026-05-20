"""Tests for Phase 4: run_reduce_from_dir, run_ingest_add, run_ingest_amend."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import click

from ingest_pipeline.reduce import run_reduce_from_dir
from ingest_pipeline.pipeline import run_ingest_add, run_ingest_amend, PipelineError
from ingest_pipeline.state import (
    State, Manifest, ManifestFile,
    ingest_dir, save_manifest, load_manifest, load_state,
    append_journal, journal_event,
)


PROMPTS = {name: f"Prompt {name}" for name in ["extract_text", "extract_image", "summarize", "reduce", "index"]}
SUMMARY_CONTENT = "---\ntype: summary\nsource_summary: /src/f.py\ntags: []\nsummarized_at: 2026-01-01T00:00:00+00:00\n---\n\nSummary."


def _make_state(dest: Path, cmd: str = "ingest-add") -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version="1.0",
        command=cmd,
        source_root=str(dest / "src"),
        destination_root=str(dest),
        started_at=now,
        last_updated=now,
        rpm=60,
        current_step=0,
        completed_steps=[],
        pending_files=[],
        completed_files=[],
        failed_files=[],
    )


def _make_manifest(source_root: Path, dest_root: Path, files=None) -> Manifest:
    return Manifest(
        version="1.0",
        source_root=str(source_root),
        destination_root=str(dest_root),
        created_at=datetime.now(timezone.utc).isoformat(),
        files=files or [],
    )


def _mock_client() -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text="# Summary\n\nContent.")]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    client.messages.create.return_value = response
    return client


def _setup_dest(dest: Path, source_root: Path, files=None) -> None:
    ingest_dir(dest).mkdir(parents=True, exist_ok=True)
    save_manifest(dest, _make_manifest(source_root, dest, files or []))


# =====================================================================
# run_reduce_from_dir
# =====================================================================

class TestRunReduceFromDir:

    def test_two_level_tree_deletes_and_regenerates(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        module_a = dest / "moduleA"
        module_a.mkdir(parents=True)
        ingest_dir(dest).mkdir()

        # Pre-existing reduce files (should be deleted and regenerated)
        (dest / "_reduce_moduleA.md").write_text("old moduleA", encoding="utf-8")
        (dest / "_reduce_root.md").write_text("old root", encoding="utf-8")

        # Summary in moduleA so reduce_dir produces output
        (module_a / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")

        state = _make_state(dest)
        with patch("ingest_pipeline.reduce.call_claude", return_value="new synthesis"):
            errors = run_reduce_from_dir(
                start_dir=module_a,
                dest_root=dest,
                prompts=PROMPTS,
                client=MagicMock(),
                rpm=60,
                state=state,
            )

        assert errors == []
        assert "old moduleA" not in (dest / "_reduce_moduleA.md").read_text()
        assert "old root" not in (dest / "_reduce_root.md").read_text()
        assert "new synthesis" in (dest / "_reduce_moduleA.md").read_text()

    def test_start_dir_equals_dest_root(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()
        ingest_dir(dest).mkdir()

        (dest / "_reduce_root.md").write_text("old root", encoding="utf-8")
        (dest / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")

        state = _make_state(dest)
        with patch("ingest_pipeline.reduce.call_claude", return_value="regenerated"):
            errors = run_reduce_from_dir(
                start_dir=dest,
                dest_root=dest,
                prompts=PROMPTS,
                client=MagicMock(),
                rpm=60,
                state=state,
            )

        assert errors == []
        content = (dest / "_reduce_root.md").read_text()
        assert "old root" not in content
        assert "regenerated" in content

    def test_no_summaries_reduce_dir_called_but_no_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        module_a = dest / "moduleA"
        module_a.mkdir(parents=True)
        ingest_dir(dest).mkdir()

        state = _make_state(dest)
        with patch("ingest_pipeline.reduce.call_claude") as mock_call:
            errors = run_reduce_from_dir(
                start_dir=module_a,
                dest_root=dest,
                prompts=PROMPTS,
                client=MagicMock(),
                rpm=60,
                state=state,
            )

        assert errors == []
        mock_call.assert_not_called()
        assert not (dest / "_reduce_moduleA.md").exists()
        assert not (dest / "_reduce_root.md").exists()

    def test_only_chain_dirs_processed_not_siblings(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        module_a = dest / "moduleA"
        module_b = dest / "moduleB"
        module_a.mkdir(parents=True)
        module_b.mkdir()
        ingest_dir(dest).mkdir()

        (module_a / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
        # moduleB has a reduce file that should NOT be touched
        (dest / "_reduce_moduleB.md").write_text("untouched B", encoding="utf-8")

        state = _make_state(dest)
        with patch("ingest_pipeline.reduce.call_claude", return_value="synthesis"):
            run_reduce_from_dir(
                start_dir=module_a,
                dest_root=dest,
                prompts=PROMPTS,
                client=MagicMock(),
                rpm=60,
                state=state,
            )

        assert (dest / "_reduce_moduleB.md").read_text() == "untouched B"



# =====================================================================
# run_ingest_add
# =====================================================================

class TestRunIngestAdd:

    def test_nominal_adds_records_and_runs_pipeline(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        new_dir = source_root / "moduleA"
        new_dir.mkdir(parents=True)
        (new_dir / "foo.py").write_text("def foo(): pass", encoding="utf-8")
        (new_dir / "bar.py").write_text("def bar(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root)

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_add(new_dir, dest, rpm=60, prompts_dir=prompts_dir)

        manifest = load_manifest(dest)
        assert len(manifest.files) == 2
        assert (dest / "index.md").exists()

    def test_duplicate_files_warns_and_skips(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        foo_src = source_root / "foo.py"
        foo_src.write_text("x", encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()

        existing = ManifestFile(
            id="abc123",
            source_path=str(foo_src),
            destination_path=str(dest / "foo.md"),
            category="text",
            size_bytes=1,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [existing])

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_add(source_root, dest, rpm=60, prompts_dir=prompts_dir)

        # Still only 1 record — duplicate was not added
        manifest = load_manifest(dest)
        assert len(manifest.files) == 1

    def test_source_outside_source_root_raises(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            pytest.raises(PipelineError),
        ):
            run_ingest_add(other_dir, dest, rpm=60, prompts_dir=prompts_dir)

    def test_no_manifest_raises(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        # No manifest created

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            pytest.raises(PipelineError),
        ):
            run_ingest_add(source_root, dest, rpm=60, prompts_dir=prompts_dir)

    def test_no_api_key_raises(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root)

        import os
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(PipelineError),
        ):
            run_ingest_add(source_root, dest, rpm=60, prompts_dir=prompts_dir)

    def test_journal_contains_source_added_event(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "foo.py").write_text("def foo(): pass", encoding="utf-8")
        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root)

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_add(source_root, dest, rpm=60, prompts_dir=prompts_dir)

        journal_path = ingest_dir(dest) / "journal.jsonl"
        events = [json.loads(line) for line in journal_path.read_text().splitlines()]
        assert any(e["event"] == "source_added" for e in events)



# =====================================================================
# run_ingest_amend
# =====================================================================

class TestRunIngestAmend:

    def test_nominal_resets_records_and_reruns(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        module_a = source_root / "moduleA"
        module_a.mkdir(parents=True)
        foo_src = module_a / "foo.py"
        foo_src.write_text("def foo(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest_module_a = dest / "moduleA"
        dest_module_a.mkdir(parents=True)

        foo_dest = dest_module_a / "foo.md"
        foo_dest.write_text("extracted content", encoding="utf-8")
        (dest_module_a / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")

        record = ManifestFile(
            id="abc",
            source_path=str(foo_src),
            destination_path=str(foo_dest),
            category="text",
            size_bytes=foo_src.stat().st_size,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [record])

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

        assert foo_dest.exists()
        assert (dest / "index.md").exists()
        manifest = load_manifest(dest)
        assert manifest.files[0].status in ("extracted", "pending")

    def test_no_matching_records_raises_with_exact_message(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        module_a = source_root / "moduleA"
        module_a.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root, [])

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            pytest.raises(PipelineError) as exc_info,
        ):
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

        assert "Nothing to amend" in str(exc_info.value)

    def test_partially_absent_files_no_error(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        module_a = source_root / "moduleA"
        module_a.mkdir(parents=True)
        foo_src = module_a / "foo.py"
        foo_src.write_text("def foo(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest_module_a = dest / "moduleA"
        dest_module_a.mkdir(parents=True)

        foo_dest = dest_module_a / "foo.md"
        # foo_dest does NOT exist — already deleted manually

        record = ManifestFile(
            id="abc",
            source_path=str(foo_src),
            destination_path=str(foo_dest),
            category="text",
            size_bytes=foo_src.stat().st_size,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [record])

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            # Should not raise — missing files are handled gracefully
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

    def test_extract_error_writes_file_error_journal_event(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        module_a = source_root / "moduleA"
        module_a.mkdir(parents=True)
        foo_src = module_a / "foo.py"
        bar_src = module_a / "bar.py"
        foo_src.write_text("def foo(): pass", encoding="utf-8")
        bar_src.write_text("def bar(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest_module_a = dest / "moduleA"
        dest_module_a.mkdir(parents=True)

        foo_dest = dest_module_a / "foo.md"
        bar_dest = dest_module_a / "bar.md"

        records = [
            ManifestFile(
                id="r1",
                source_path=str(foo_src),
                destination_path=str(foo_dest),
                category="text",
                size_bytes=foo_src.stat().st_size,
                mtime="2026-01-01T00:00:00+00:00",
                status="extracted",
            ),
            ManifestFile(
                id="r2",
                source_path=str(bar_src),
                destination_path=str(bar_dest),
                category="text",
                size_bytes=bar_src.stat().st_size,
                mtime="2026-01-01T00:00:00+00:00",
                status="extracted",
            ),
        ]
        _setup_dest(dest, source_root, records)

        call_count = 0
        def extract_side_effect(record, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Extract failed")
            Path(record.destination_path).write_text("extracted", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.extract.extract_file", side_effect=extract_side_effect),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

        journal_path = ingest_dir(dest) / "journal.jsonl"
        events = [json.loads(line) for line in journal_path.read_text().splitlines()]
        assert any(e["event"] == "file_error" for e in events)

    def test_journal_contains_amend_start_event(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        module_a = source_root / "moduleA"
        module_a.mkdir(parents=True)
        foo_src = module_a / "foo.py"
        foo_src.write_text("def foo(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest_module_a = dest / "moduleA"
        dest_module_a.mkdir(parents=True)

        foo_dest = dest_module_a / "foo.md"

        record = ManifestFile(
            id="abc",
            source_path=str(foo_src),
            destination_path=str(foo_dest),
            category="text",
            size_bytes=foo_src.stat().st_size,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [record])

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

        journal_path = ingest_dir(dest) / "journal.jsonl"
        events = [json.loads(line) for line in journal_path.read_text().splitlines()]
        assert any(e["event"] == "amend_start" for e in events)

    def test_generated_files_deleted_before_reprocessing(self, tmp_path: Path, prompts_dir: Path) -> None:
        source_root = tmp_path / "source"
        module_a = source_root / "moduleA"
        module_a.mkdir(parents=True)
        foo_src = module_a / "foo.py"
        foo_src.write_text("def foo(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest_module_a = dest / "moduleA"
        dest_module_a.mkdir(parents=True)

        foo_dest = dest_module_a / "foo.md"
        foo_dest.write_text("OLD extracted content", encoding="utf-8")
        (dest_module_a / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
        (dest / "_reduce_moduleA.md").write_text("old reduce", encoding="utf-8")
        (dest / "_reduce_root.md").write_text("old root reduce", encoding="utf-8")
        (dest / "index.md").write_text("old index", encoding="utf-8")

        record = ManifestFile(
            id="abc",
            source_path=str(foo_src),
            destination_path=str(foo_dest),
            category="text",
            size_bytes=foo_src.stat().st_size,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [record])

        tracked_deletions = []
        original_unlink = Path.unlink

        def tracking_unlink(self, *args, **kwargs):
            tracked_deletions.append(self.name)
            return original_unlink(self, *args, **kwargs)

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(Path, "unlink", tracking_unlink),
        ):
            run_ingest_amend(module_a, dest, rpm=60, prompts_dir=prompts_dir)

        assert "foo.md" in tracked_deletions
        assert "_summary_foo.md" in tracked_deletions
        assert "index.md" in tracked_deletions


# =====================================================================
# Non-processed Documents Section in Index
# =====================================================================

class TestIndexNonProcessedDocuments:

    def test_no_non_processed_section_absent(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When all files are extracted, no non-processed section should appear."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "foo.py").write_text("def foo(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()
        foo_dest = dest / "foo.md"

        record = ManifestFile(
            id="abc",
            source_path=str(source_root / "foo.py"),
            destination_path=str(foo_dest),
            category="text",
            size_bytes=100,
            mtime="2026-01-01T00:00:00+00:00",
            status="extracted",
        )
        _setup_dest(dest, source_root, [record])

        foo_dest.write_text("extracted content", encoding="utf-8")
        (dest / "_summary_foo.md").write_text(SUMMARY_CONTENT, encoding="utf-8")
        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            from ingest_pipeline.state import load_state
            state = load_state(dest) or _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" not in index_content

    def test_skipped_oversized_section_present(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When a file is skipped-oversized, it should appear in the section with human-readable size."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "large.pdf").write_text("x" * 100, encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()

        record = ManifestFile(
            id="abc",
            source_path=str(source_root / "large.pdf"),
            destination_path=str(dest / "large.md"),
            category="binary-doc",
            size_bytes=13_000_000,  # 12.4 MB
            mtime="2026-01-01T00:00:00+00:00",
            status="skipped-oversized",
        )
        _setup_dest(dest, source_root, [record])

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Oversized Files" in index_content
        assert "large.pdf" in index_content
        assert "12.4 MB" in index_content

    def test_binary_image_skipped_section_present(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When a binary-image file is skipped, it should appear in the section."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "diagram.png").write_text("PNG data", encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()

        record = ManifestFile(
            id="abc",
            source_path=str(source_root / "diagram.png"),
            destination_path=str(dest / "diagram.md"),
            category="binary-image",
            size_bytes=5000,
            mtime="2026-01-01T00:00:00+00:00",
            status="skipped",
        )
        _setup_dest(dest, source_root, [record])

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Skipped Files" in index_content
        assert "diagram.png" in index_content
        assert "category: binary-image" in index_content

    def test_symlink_in_journal_section_present(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When a symlink is logged in journal, it should appear in the section."""
        source_root = tmp_path / "source"
        source_root.mkdir()

        dest = tmp_path / "dest"
        dest.mkdir()
        _setup_dest(dest, source_root, [])

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        # Write symlink event to journal
        append_journal(
            dest,
            journal_event("file_skipped", source=str(source_root / "symlink.txt"), reason="symlink", cmd="ingest-init"),
        )

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Symbolic Links" in index_content
        assert "symlink.txt" in index_content

    def test_failed_extraction_section_present(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When a file extraction fails, it should appear in the section."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        (source_root / "broken.py").write_text("def broken(): pass", encoding="utf-8")

        dest = tmp_path / "dest"
        dest.mkdir()

        record = ManifestFile(
            id="abc",
            source_path=str(source_root / "broken.py"),
            destination_path=str(dest / "broken.md"),
            category="text",
            size_bytes=100,
            mtime="2026-01-01T00:00:00+00:00",
            status="failed",
        )
        _setup_dest(dest, source_root, [record])

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Failed Extractions" in index_content
        assert "broken.py" in index_content

    def test_mixed_non_processed_all_sections_present(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When multiple types of non-processed files exist, all sections should appear."""
        source_root = tmp_path / "source"
        source_root.mkdir()

        dest = tmp_path / "dest"
        dest.mkdir()

        records = [
            ManifestFile(
                id="r1",
                source_path=str(source_root / "large.pdf"),
                destination_path=str(dest / "large.md"),
                category="binary-doc",
                size_bytes=60_000_000,  # 57.2 MB
                mtime="2026-01-01T00:00:00+00:00",
                status="skipped-oversized",
            ),
            ManifestFile(
                id="r2",
                source_path=str(source_root / "image.png"),
                destination_path=str(dest / "image.md"),
                category="binary-image",
                size_bytes=1000,
                mtime="2026-01-01T00:00:00+00:00",
                status="skipped",
            ),
            ManifestFile(
                id="r3",
                source_path=str(source_root / "failed.py"),
                destination_path=str(dest / "failed.md"),
                category="text",
                size_bytes=100,
                mtime="2026-01-01T00:00:00+00:00",
                status="failed",
            ),
        ]
        _setup_dest(dest, source_root, records)

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        append_journal(
            dest,
            journal_event("file_skipped", source=str(source_root / "link.txt"), reason="symlink", cmd="ingest-init"),
        )

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Oversized Files" in index_content
        assert "large.pdf" in index_content
        assert "57.2 MB" in index_content
        assert "### Skipped Files" in index_content
        assert "image.png" in index_content
        assert "### Failed Extractions" in index_content
        assert "failed.py" in index_content
        assert "### Symbolic Links" in index_content
        assert "link.txt" in index_content

    def test_journal_absent_no_error(self, tmp_path: Path, prompts_dir: Path) -> None:
        """When journal.jsonl does not exist, should not raise an exception."""
        source_root = tmp_path / "source"
        source_root.mkdir()

        dest = tmp_path / "dest"
        dest.mkdir()

        record = ManifestFile(
            id="abc",
            source_path=str(source_root / "large.pdf"),
            destination_path=str(dest / "large.md"),
            category="binary-doc",
            size_bytes=60_000_000,
            mtime="2026-01-01T00:00:00+00:00",
            status="skipped-oversized",
        )
        _setup_dest(dest, source_root, [record])

        # Delete journal to simulate absence
        journal_path = ingest_dir(dest) / "journal.jsonl"
        if journal_path.exists():
            journal_path.unlink()

        (dest / "_reduce_root.md").write_text("# Summary\n\nContent.", encoding="utf-8")

        with (
            patch("ingest_pipeline.pipeline.anthropic.Anthropic", return_value=_mock_client()),
            patch("ingest_pipeline.api.time.sleep"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            from ingest_pipeline.index import run_index_step
            state = _make_state(dest)
            errors = run_index_step(dest, PROMPTS, _mock_client(), 60, state)

        # No errors, section still appears for oversized file
        assert errors == []
        index_content = (dest / "index.md").read_text(encoding="utf-8")
        assert "## Non-processed Documents" in index_content
        assert "### Oversized Files" in index_content
        # But no symlinks section since journal is absent
        assert "### Symbolic Links" not in index_content
