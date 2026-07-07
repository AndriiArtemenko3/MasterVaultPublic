"""Raw-file discovery + text extraction for the ingest pipeline.

Three input shapes reach the pipeline as plain text: `.md` and `.txt` are read
as-is; `.pdf` text is extracted page-by-page with pypdf. Anything else is not
an ingestible unit and `discover_units` silently skips it — the pipeline
enumerates a directory tree that may contain non-document files (`.DS_Store`,
images, etc.).
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".pdf")


def discover_units(root: Path | str) -> list[Path]:
    """Enumerate ingestible files under `root`.

    A file path returns `[root]` (or `[]` when its suffix is unsupported); a
    directory returns every supported file under it, sorted for a
    deterministic plan.
    """
    root = Path(root)
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_SUFFIXES else []
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def read_raw_text(path: Path | str) -> str:
    """Extract plain text from one ingestible file."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    return path.read_text(encoding="utf-8")
