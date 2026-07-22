"""Raw-file discovery + text extraction for the ingest pipeline.

Three input shapes reach the pipeline as plain text: `.md` and `.txt` are read
as-is; `.pdf` text is extracted page-by-page with pypdf. Anything else is not
an ingestible unit and `discover_units` silently skips it — the pipeline
enumerates a directory tree that may contain non-document files (`.DS_Store`,
images, etc.).
"""

from __future__ import annotations

from pathlib import Path

from mastervault.core.errors import UnreadableDocument

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
    # `is_file()` follows symlinks, so a symlinked file inside the tree would
    # otherwise be discovered and its target's contents read (and sent to the
    # configured LLM provider). Ingest only what genuinely lives under `root`.
    return sorted(
        p
        for p in root.rglob("*")
        if not p.is_symlink() and p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def read_raw_text(path: Path | str) -> str:
    """Extract plain text from one ingestible file.

    Every failure mode is funnelled into UnreadableDocument so a corrupt PDF or
    a binary file with a `.txt` suffix produces one actionable line naming the
    file, instead of a pypdf or codec traceback from three layers down.
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableDocument(
            f"{path.name}: not valid UTF-8 text (byte 0x{exc.object[exc.start]:02x} at offset "
            f"{exc.start}). It is most likely a binary file with a text suffix; convert it "
            "to UTF-8 text or PDF before ingesting."
        ) from exc
    except OSError as exc:
        raise UnreadableDocument(f"{path.name}: cannot be read ({exc.strerror or exc}).") from exc


def _read_pdf(path: Path) -> str:
    # pypdf raises its own hierarchy plus assorted stdlib errors on malformed
    # input; none of them is a contract this package wants to re-export.
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except UnreadableDocument:
        raise
    except Exception as exc:
        raise UnreadableDocument(
            f"{path.name}: not a readable PDF ({type(exc).__name__}: {exc}). "
            "Re-export it, or convert it to .md/.txt before ingesting."
        ) from exc
    return "\n\n".join(pages).strip()
