#!/usr/bin/env bash
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
COMMANDS_SRC="${REPO_DIR}/.claude/commands"
TEMPLATE_SRC="${REPO_DIR}/templates/CLAUDE-ingest-template.md"
COMMANDS_LINK="${CLAUDE_DIR}/commands"
TEMPLATE_LINK="${CLAUDE_DIR}/CLAUDE-ingest-template.md"

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo "  [info] $*"; }
success() { echo "  [ok]   $*"; }
warn()    { echo "  [warn] $*"; }
die()     { echo "  [err]  $*" >&2; exit 1; }

make_symlink() {
  local src="$1" dst="$2" label="$3"

  [[ -e "$src" ]] || die "Source not found: $src"

  if [[ -L "$dst" ]]; then
    local existing
    existing="$(readlink "$dst")"
    if [[ "$existing" == "$src" ]]; then
      success "${label} — symlink already in place"
      return
    else
      warn "${label} — existing symlink points to '${existing}', replacing..."
      rm "$dst"
    fi
  elif [[ -e "$dst" ]]; then
    warn "${label} — path exists and is not a symlink: $dst"
    read -rp "    Overwrite? [y/N] " confirm
    [[ "${confirm,,}" == "y" ]] || { info "Skipped."; return; }
    rm -rf "$dst"
  fi

  ln -s "$src" "$dst"
  success "${label} → $src"
}

# ─── Main ─────────────────────────────────────────────────────────────────────
echo
echo "agent-context-builder — install"
echo "────────────────────────────────"

mkdir -p "${CLAUDE_DIR}"

make_symlink "${COMMANDS_SRC}"  "${COMMANDS_LINK}"  "commands"
make_symlink "${TEMPLATE_SRC}"  "${TEMPLATE_LINK}"  "CLAUDE-ingest-template.md"

echo
echo "Done. Commands available in all Claude Code sessions:"
echo "  /ingest-init              — initialize current directory"
echo "  /ingest [source] [dest]   — run the ingest pipeline"
echo