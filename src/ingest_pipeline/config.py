from pathlib import Path

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
PIPELINE_VERSION = "1.0"
CHUNK_SIZE_BYTES: int = 200_000

MAX_TOKENS: dict[str, int] = {
    "extract": 4096,
    "image": 1024,
    "summarize": 1024,
    "reduce": 2048,
    "index": 4096,
}

TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".xml", ".html", ".java", ".py", ".js", ".ts",
    ".sql", ".rst", ".toml", ".ini", ".cfg", ".sh",
    ".bash", ".zsh", ".go", ".rb", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".kt", ".swift", ".svg",
})

BINARY_DOC_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx",
})

BINARY_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})

SKIP_NAMES: frozenset[str] = frozenset({
    ".DS_Store", ".gitignore", ".gitkeep",
})

SKIP_SUFFIXES: frozenset[str] = frozenset({
    ".lock", ".pyc",
})

SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", "__pycache__", ".ingest",
    ".venv", "venv", ".tox", "dist", "build", ".mypy_cache",
})


def load_prompts(prompts_dir: Path) -> dict[str, str]:
    names = ["extract_text", "extract_image", "summarize", "reduce", "index"]
    prompts: dict[str, str] = {}
    for name in names:
        path = prompts_dir / (name + ".txt")
        prompts[name] = path.read_text(encoding="utf-8")
    return prompts
