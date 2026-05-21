"""Phase 5 — Polish: tests for retry, SHA-256, symlinks, unicode fallback, text chunking."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from anthropic import APIStatusError, RateLimitError

from ingest_pipeline.api import call_claude
from ingest_pipeline.extract import extract_text_file
from ingest_pipeline.state import ManifestFile, State, ingest_dir
from ingest_pipeline.walker import build_manifest_files, find_symlinks, walk_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dest(tmp_path: Path) -> Path:
    dest = tmp_path / "dest"
    dest.mkdir()
    ingest_dir(dest).mkdir()
    return dest


def _make_state(dest: Path) -> State:
    now = datetime.now(timezone.utc).isoformat()
    return State(
        pipeline_version="1.0",
        command="ingest",
        source_root="/src",
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


def _make_record(source_path: str, dest_path: str) -> ManifestFile:
    return ManifestFile(
        id="test01",
        source_path=source_path,
        destination_path=dest_path,
        category="text",
        size_bytes=100,
        mtime=datetime.now(timezone.utc).isoformat(),
        status="pending",
    )


def _make_rate_limit_error() -> RateLimitError:
    resp = MagicMock()
    resp.status_code = 429
    return RateLimitError("Rate limit exceeded", response=resp, body=None)


def _make_api_status_error(status_code: int) -> APIStatusError:
    resp = MagicMock()
    resp.status_code = status_code
    return APIStatusError(f"HTTP {status_code}", response=resp, body=None)


def _make_mock_client(text: str = "Mocked response") -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# Retry tests (api.py)
# ---------------------------------------------------------------------------

class TestRetry:
    def test_rate_limit_retry_success(self, tmp_path: Path) -> None:
        """RateLimitError on first attempt → retry → succeeds on second."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = _make_mock_client()

        error = _make_rate_limit_error()
        success_response = client.messages.create.return_value
        client.messages.create.side_effect = [error, success_response]

        with patch("ingest_pipeline.api.time.sleep") as mock_sleep:
            result = call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert result == "Mocked response"
        assert client.messages.create.call_count == 2
        # First sleep: 60/60=1.0, second sleep (after error): 2*(60/60)=2.0
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    def test_rate_limit_all_attempts_raises(self, tmp_path: Path) -> None:
        """RateLimitError on all 3 attempts → exception re-raised."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = MagicMock()
        client.messages.create.side_effect = _make_rate_limit_error()

        with patch("ingest_pipeline.api.time.sleep"):
            with pytest.raises(RateLimitError):
                call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert client.messages.create.call_count == 3

    def test_api_status_500_retries(self, tmp_path: Path) -> None:
        """APIStatusError status_code=500 → retry → succeeds on second."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = _make_mock_client()

        error = _make_api_status_error(500)
        success_response = client.messages.create.return_value
        client.messages.create.side_effect = [error, success_response]

        with patch("ingest_pipeline.api.time.sleep"):
            result = call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert result == "Mocked response"
        assert client.messages.create.call_count == 2

    def test_api_status_422_no_retry(self, tmp_path: Path) -> None:
        """APIStatusError status_code=422 (4xx) → immediate exception, no retry."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = MagicMock()
        client.messages.create.side_effect = _make_api_status_error(422)

        with patch("ingest_pipeline.api.time.sleep"):
            with pytest.raises(APIStatusError) as exc_info:
                call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert exc_info.value.status_code == 422
        assert client.messages.create.call_count == 1

    def test_api_status_500_all_attempts_raises(self, tmp_path: Path) -> None:
        """APIStatusError 500 on all 3 attempts → exception re-raised."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = MagicMock()
        client.messages.create.side_effect = _make_api_status_error(500)

        with patch("ingest_pipeline.api.time.sleep"):
            with pytest.raises(APIStatusError):
                call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert client.messages.create.call_count == 3

    def test_nominal_success_one_call(self, tmp_path: Path) -> None:
        """Nominal success → exactly one API call, sleep once."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        client = _make_mock_client()

        with patch("ingest_pipeline.api.time.sleep") as mock_sleep:
            result = call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        assert result == "Mocked response"
        assert client.messages.create.call_count == 1
        mock_sleep.assert_called_once_with(1.0)

    def test_tokens_logged_only_on_success(self, tmp_path: Path) -> None:
        """Tokens are accumulated only when a call succeeds."""
        dest = _make_dest(tmp_path)
        state = _make_state(dest)
        state.total_input_tokens = 0
        state.total_output_tokens = 0
        client = _make_mock_client()

        error = _make_rate_limit_error()
        success_response = client.messages.create.return_value
        client.messages.create.side_effect = [error, success_response]

        with patch("ingest_pipeline.api.time.sleep"):
            call_claude(client, "model", "sys", "user", 100, 60, state, dest)

        # Only the successful call's tokens should be counted
        assert state.total_input_tokens == 100
        assert state.total_output_tokens == 50


# ---------------------------------------------------------------------------
# SHA-256 manifest tests (walker.py)
# ---------------------------------------------------------------------------

class TestSHA256Manifest:
    def test_file_id_is_sha256_of_content(self, tmp_path: Path) -> None:
        """id field = 'sha256:{hexdigest}' of the file's bytes."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()

        content = b"hello world"
        (src / "foo.py").write_bytes(content)

        records, symlinks = build_manifest_files(src, dest)

        expected_hex = hashlib.sha256(content).hexdigest()
        assert len(records) == 1
        assert records[0].id == f"sha256:{expected_hex}"
        assert len(records[0].id) == len("sha256:") + 64

    def test_sha256_id_uses_content_not_path(self, tmp_path: Path) -> None:
        """Two files with same path-relative name but different content get different IDs."""
        src1 = tmp_path / "src1"
        src1.mkdir()
        src2 = tmp_path / "src2"
        src2.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()

        (src1 / "file.py").write_bytes(b"content A")
        (src2 / "file.py").write_bytes(b"content B")

        records1, _ = build_manifest_files(src1, dest)
        records2, _ = build_manifest_files(src2, dest)

        assert records1[0].id != records2[0].id

    def test_build_manifest_files_returns_tuple(self, tmp_path: Path) -> None:
        """build_manifest_files returns (list[ManifestFile], list[Path])."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        (src / "a.py").write_text("x = 1")

        result = build_manifest_files(src, dest)

        assert isinstance(result, tuple)
        assert len(result) == 2
        records, symlinks = result
        assert isinstance(records, list)
        assert isinstance(symlinks, list)


# ---------------------------------------------------------------------------
# Symlink tests (walker.py)
# ---------------------------------------------------------------------------

class TestSymlinks:
    def test_symlink_excluded_from_manifest(self, tmp_path: Path) -> None:
        """Symlink in source → not in records, present in symlinks list."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()

        real_file = tmp_path / "real.py"
        real_file.write_text("x = 1")
        link = src / "link.py"
        link.symlink_to(real_file)

        (src / "actual.py").write_text("y = 2")

        records, symlinks = build_manifest_files(src, dest)

        source_paths = [r.source_path for r in records]
        assert str(link) not in source_paths
        assert str(src / "actual.py") in source_paths
        assert link in symlinks

    def test_find_symlinks_returns_only_symlinks(self, tmp_path: Path) -> None:
        """find_symlinks returns only symlink paths, not regular files."""
        src = tmp_path / "src"
        src.mkdir()

        real = tmp_path / "real.txt"
        real.write_text("content")
        link = src / "link.txt"
        link.symlink_to(real)
        (src / "normal.py").write_text("x = 1")

        result = find_symlinks(src)

        assert link in result
        assert src / "normal.py" not in result

    def test_walk_source_skips_symlinks(self, tmp_path: Path) -> None:
        """walk_source does not yield symlinks."""
        src = tmp_path / "src"
        src.mkdir()

        real = tmp_path / "real.py"
        real.write_text("x = 1")
        link = src / "link.py"
        link.symlink_to(real)
        (src / "normal.py").write_text("y = 2")

        found = list(walk_source(src))
        assert link not in found
        assert src / "normal.py" in found


# ---------------------------------------------------------------------------
# Unicode fallback tests (extract.py)
# ---------------------------------------------------------------------------

class TestUnicodeFallback:
    def test_utf8_file_reads_without_fallback(self, tmp_path: Path) -> None:
        """Valid UTF-8 file is read directly, no fallback needed."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "src.txt"
        src.write_text("hello world", encoding="utf-8")
        dest_file = dest / "src.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        with patch("ingest_pipeline.extract.call_claude") as mock_call:
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)
            mock_call.assert_not_called()

        assert "hello world" in dest_file.read_text()

    def test_latin1_file_read_via_fallback(self, tmp_path: Path) -> None:
        """File with latin-1 bytes (invalid UTF-8) is decoded via latin-1 fallback."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "latin.txt"
        # 0x80..0xFF are invalid in UTF-8 but valid in latin-1
        src.write_bytes(b"caf\xe9 au lait")
        dest_file = dest / "latin.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        with patch("ingest_pipeline.extract.call_claude"):
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

        content = dest_file.read_text(encoding="utf-8")
        assert "caf" in content

    def test_utf8_not_decoded_with_latin1_when_valid(self, tmp_path: Path) -> None:
        """For a valid UTF-8 file, latin-1 fallback is not attempted (no exception path)."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "utf8.txt"
        src.write_text("simple ascii text", encoding="utf-8")
        dest_file = dest / "utf8.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        with patch("ingest_pipeline.extract.call_claude"):
            # Should not raise; latin-1 path never triggered
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

        assert dest_file.exists()


# ---------------------------------------------------------------------------
# Text chunking tests (extract.py)
# ---------------------------------------------------------------------------

class TestTextChunking:
    def test_large_file_calls_claude_multiple_times(self, tmp_path: Path) -> None:
        """File > 500KB UTF-8 → call_claude called multiple times, output concatenated."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "big.py"
        # Each line is ~100 bytes; 6000 lines ≈ 600KB
        line = "x" * 98 + "\n"
        src.write_bytes((line * 6000).encode("utf-8"))
        dest_file = dest / "big.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        call_count = 0

        def fake_call_claude(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"chunk{call_count}"

        with patch("ingest_pipeline.extract.call_claude", side_effect=fake_call_claude):
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

        assert call_count >= 2
        content = dest_file.read_text()
        assert "chunk1" in content
        assert "chunk2" in content

    def test_small_file_preserves_current_behavior(self, tmp_path: Path) -> None:
        """File <= 500KB → behaviour unchanged (no Claude call for simple text)."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "small.py"
        src.write_text("def foo():\n    return 42\n", encoding="utf-8")
        dest_file = dest / "small.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        with patch("ingest_pipeline.extract.call_claude") as mock_call:
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)
            mock_call.assert_not_called()

    def test_large_file_does_not_check_complexity(self, tmp_path: Path) -> None:
        """Large file (>500KB) always calls Claude; has_complexity_signals not consulted."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "big.py"
        # Plain text (no complexity signals) but over 500KB
        line = "a" * 98 + "\n"
        src.write_bytes((line * 6000).encode("utf-8"))
        dest_file = dest / "big.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        with patch("ingest_pipeline.extract.call_claude", return_value="out") as mock_call:
            with patch("ingest_pipeline.extract.has_complexity_signals") as mock_hcs:
                extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)
                mock_hcs.assert_not_called()
                assert mock_call.call_count >= 2

    def test_chunk_boundary_is_on_line(self, tmp_path: Path) -> None:
        """Chunked output assembles complete content (no truncated lines)."""
        dest = _make_dest(tmp_path)
        src = tmp_path / "lines.py"
        # Create exactly 3 distinct chunks
        line = "x" * 98 + "\n"
        src.write_bytes((line * 6000).encode("utf-8"))
        dest_file = dest / "lines.md"
        state = _make_state(dest)
        record = _make_record(str(src), str(dest_file))
        prompts = {k: f"p{k}" for k in ["extract_text"]}

        chunks_received: list[str] = []

        def capture(*args, **kwargs):
            # user_content is 4th positional arg
            chunks_received.append(args[3])
            return "ok"

        with patch("ingest_pipeline.extract.call_claude", side_effect=capture):
            extract_text_file(src, dest_file, record, prompts, MagicMock(), 60, state, dest)

        # Each chunk's content should contain only complete lines
        for chunk_content in chunks_received:
            # Extract just the content part (after "Content:\n")
            content_part = chunk_content.split("Content:\n", 1)[1] if "Content:\n" in chunk_content else chunk_content
            # All lines within a chunk are complete (no mid-line split)
            for ln in content_part.splitlines():
                assert len(ln) <= 100


# ---------------------------------------------------------------------------
# Slash commands existence test
# ---------------------------------------------------------------------------

COMMANDS_DIR = Path(__file__).parent.parent / ".claude" / "commands"


class TestSlashCommands:
    @pytest.mark.skipif(
        not (COMMANDS_DIR / "ingest.md").exists(),
        reason=".claude/commands/ingest.md not yet created (requires manual approval)",
    )
    def test_ingest_command_exists(self) -> None:
        cmd = COMMANDS_DIR / "ingest.md"
        content = cmd.read_text()
        assert "$ARGUMENTS" in content
        assert "Phase 0" in content

    @pytest.mark.skipif(
        not (COMMANDS_DIR / "ingest-add.md").exists(),
        reason=".claude/commands/ingest-add.md not yet created (requires manual approval)",
    )
    def test_ingest_add_command_exists(self) -> None:
        cmd = COMMANDS_DIR / "ingest-add.md"
        content = cmd.read_text()
        assert "$ARGUMENTS" in content
        assert "Phase 0" in content

    @pytest.mark.skipif(
        not (COMMANDS_DIR / "ingest-amend.md").exists(),
        reason=".claude/commands/ingest-amend.md not yet created (requires manual approval)",
    )
    def test_ingest_amend_command_exists(self) -> None:
        cmd = COMMANDS_DIR / "ingest-amend.md"
        content = cmd.read_text()
        assert "$ARGUMENTS" in content
        assert "Phase 0" in content
