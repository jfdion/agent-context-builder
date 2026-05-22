#!/usr/bin/env bash
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
COMMANDS_SRC="${REPO_DIR}/.claude/commands"
TEMPLATE_SRC="${REPO_DIR}/templates/CLAUDE-ingest-template.md"
COMMANDS_LINK="${CLAUDE_DIR}/commands"
TEMPLATE_LINK="${CLAUDE_DIR}/CLAUDE-ingest-template.md"
VENV_DIR="${REPO_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
WRAPPER_DIR="${HOME}/.local/bin"
WRAPPER="${WRAPPER_DIR}/ingest-extract"

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo "  [info] $*"; }
success() { echo "  [ok]   $*"; }
warn()    { echo "  [warn] $*"; }
die()     { echo "  [err]  $*" >&2; exit 1; }

make_symlink() {
  local src="$1" dst="$2" label="$3"

  [[ -e "$src" ]] || die "Source not found: $src"

  if [[ -e "$dst" && ! -L "$dst" ]]; then
    die "${label} — real file/directory exists at $dst (will not overwrite). Remove it manually and re-run."
  fi

  [[ -L "$dst" ]] && rm "$dst"
  ln -s "$src" "$dst"
  success "${label} → $src"
}

# ─── Main ─────────────────────────────────────────────────────────────────────
echo
echo "agent-context-builder — install"
echo "────────────────────────────────"

mkdir -p "${CLAUDE_DIR}"

# ── venv ──────────────────────────────────────────────────────────────────────
if [[ ! -x "${VENV_PYTHON}" ]]; then
  info "venv not found — running uv sync..."
  if ! command -v uv &>/dev/null; then
    die "uv is not installed. Install it with:
       curl -LsSf https://astral.sh/uv/install.sh | sh
    or visit: https://docs.astral.sh/uv/getting-started/installation/"
  fi
  (cd "${REPO_DIR}" && uv sync)
  success "venv created via uv sync"
else
  success "venv already exists"
fi

# ── shell wrapper ──────────────────────────────────────────────────────────────
mkdir -p "${WRAPPER_DIR}"
cat > "${WRAPPER}" << WRAPPER
#!/usr/bin/env bash
exec "${VENV_PYTHON}" -m ingest_pipeline.extract_doc "\$@"
WRAPPER
chmod +x "${WRAPPER}"
success "wrapper → ${WRAPPER}"

# ── symlinks ───────────────────────────────────────────────────────────────────
make_symlink "${COMMANDS_SRC}" "${COMMANDS_LINK}" "commands"
make_symlink "${TEMPLATE_SRC}" "${TEMPLATE_LINK}" "CLAUDE-ingest-template.md"

# ── PATH hint ─────────────────────────────────────────────────────────────────
if [[ ":${PATH}:" != *":${WRAPPER_DIR}:"* ]]; then
  warn "${WRAPPER_DIR} is not in PATH — add to your shell profile:"
  echo "       export PATH=\"\${HOME}/.local/bin:\${PATH}\""
fi

echo
echo "Done. Commands available in all Claude Code sessions:"
echo "  /ingest-init              — initialize current directory"
echo "  /ingest [source] [dest]   — run the ingest pipeline"
echo
echo "Python tool available globally:"
echo "  ingest-extract <source> [options]"
echo