"""The sole optional Docling import boundary.

Vendor objects are converted to builtin dictionaries inside ``parse`` and
never escape this module.  Importing MasterVault core does not import Docling.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import warnings
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, cast
from unittest.mock import patch

from mastervault.core.errors import UnreadableDocument
from mastervault.document_intelligence.docling_normalizer import normalize_docling_export
from mastervault.document_intelligence.models import DocumentResourceLimits, ParsedDocumentV2
from mastervault.document_intelligence.parser import PdfSource

DOCLING_PARSER_NAME = "docling"
DOCLING_PARSER_PROFILE = "clean-digital-layout-table-v2"
DOCLING_PREFETCH_COMMAND = (
    "python -m mastervault.document_intelligence.fetch_docling_artifacts --output-dir PATH"
)
DOCLING_DOCUMENT_TIMEOUT_SECONDS = 120.0
DOCLING_MAX_SOURCE_BYTES = 50 * 1024 * 1024
DOCLING_MAX_PAGES = 200
DOCLING_HEADING_HIERARCHY_OPTIONS: dict[str, Any] = {
    "enabled": True,
    "use_bookmarks": True,
    "use_numbering": False,
    "use_style": True,
    "max_level": 6,
    "bookmark_match_threshold": 0.8,
}
DOCLING_RESOURCE_LIMITS = DocumentResourceLimits(
    timeout_seconds=DOCLING_DOCUMENT_TIMEOUT_SECONDS,
    max_source_bytes=DOCLING_MAX_SOURCE_BYTES,
    max_pages=DOCLING_MAX_PAGES,
)
DOCLING_COMPONENT_VERSIONS = {
    "docling-slim": "2.118.0",
    "docling-core": "2.91.0",
    "docling-ibm-models": "3.13.3",
    "docling-parse": "7.10.0",
}

_ARTIFACT_MANIFEST_RESOURCE = "docling_artifacts_manifest.json"


def _load_artifact_manifest() -> dict[str, Any]:
    """Load the packaged immutable source/revision/file manifest."""
    resource = files("mastervault.document_intelligence").joinpath(
        _ARTIFACT_MANIFEST_RESOURCE
    )
    manifest = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("profile") != DOCLING_PARSER_PROFILE
        or not isinstance(manifest.get("repositories"), list)
    ):
        raise RuntimeError("packaged Docling artifact manifest is invalid")
    return manifest


DOCLING_ARTIFACT_MANIFEST = _load_artifact_manifest()


def _manifest_files() -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for repository in DOCLING_ARTIFACT_MANIFEST["repositories"]:
        repository_id = repository.get("repository")
        revision = repository.get("revision")
        destination = repository.get("destination")
        source_files = repository.get("files")
        if (
            not isinstance(repository_id, str)
            or not isinstance(revision, str)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
            or not isinstance(destination, str)
            or not isinstance(source_files, list)
        ):
            raise RuntimeError("packaged Docling artifact manifest repository is invalid")
        for source_file in source_files:
            source_path = source_file.get("source_path")
            if not isinstance(source_path, str):
                raise RuntimeError("packaged Docling artifact manifest file is invalid")
            relative = (PurePosixPath(destination) / PurePosixPath(source_path)).as_posix()
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or relative in seen:
                raise RuntimeError("packaged Docling artifact manifest path is unsafe")
            sha256 = source_file.get("sha256")
            size_bytes = source_file.get("size_bytes")
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or not isinstance(size_bytes, int)
                or size_bytes < 1
            ):
                raise RuntimeError("packaged Docling artifact manifest identity is invalid")
            seen.add(relative)
            entries.append(
                {
                    "repository": repository_id,
                    "revision": revision,
                    "source_path": source_path,
                    "relative_path": relative,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                }
            )
    if not entries:
        raise RuntimeError("packaged Docling artifact manifest is empty")
    return tuple(entries)


DOCLING_ARTIFACT_FILES = _manifest_files()
# Compatibility/readability view used by the doctor and tests. The complete
# manifest above also freezes source repositories, immutable revisions and sizes.
DOCLING_REQUIRED_ARTIFACTS = {
    entry["relative_path"]: entry["sha256"] for entry in DOCLING_ARTIFACT_FILES
}
DOCLING_EXPECTED_ARTIFACT_BYTES = sum(
    entry["size_bytes"] for entry in DOCLING_ARTIFACT_FILES
)
_MODEL_IDENTITY_PAYLOAD = json.dumps(
    DOCLING_ARTIFACT_MANIFEST, sort_keys=True, separators=(",", ":")
).encode("utf-8")
DOCLING_MODEL_IDENTITY = f"sha256:{hashlib.sha256(_MODEL_IDENTITY_PAYLOAD).hexdigest()}"


def _convert_with_limits(converter: Any, stream: Any) -> Any:
    """Keep both Docling input ceilings attached to every conversion call."""
    return converter.convert(
        stream,
        raises_on_error=True,
        max_num_pages=DOCLING_MAX_PAGES,
        max_file_size=DOCLING_MAX_SOURCE_BYTES,
    )


@dataclass(frozen=True)
class DoclingDoctorReport:
    ok: bool
    message: str
    component_versions: dict[str, str]
    model_identity: str | None = None
    artifact_bytes: int = 0


def _installed_versions() -> dict[str, str]:
    installed: dict[str, str] = {}
    for package in DOCLING_COMPONENT_VERSIONS:
        try:
            installed[package] = version(package)
        except PackageNotFoundError:
            installed[package] = "missing"
    return installed


def _artifact_root(path: Path | str) -> Path:
    """Resolve one configured root without accepting a symlink or special file."""
    requested = Path(path).expanduser()
    try:
        metadata = os.lstat(requested)
    except FileNotFoundError as exc:
        raise ValueError(f"artifact path is not a directory: {requested}") from exc
    except OSError as exc:
        raise ValueError(f"artifact directory cannot be inspected: {requested} ({exc})") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"artifact directory must not be a symlink: {requested}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"artifact path is not a directory: {requested}")
    return requested.resolve()


def _read_regular_artifact(root: Path, relative: str) -> tuple[str, int]:
    """Hash one no-follow regular file and detect mutation during the read."""
    current = root
    parts = PurePosixPath(relative).parts
    for part in parts[:-1]:
        current /= part
        try:
            directory_metadata = os.lstat(current)
        except OSError as exc:
            raise ValueError(f"missing {relative}") from exc
        if stat.S_ISLNK(directory_metadata.st_mode):
            raise ValueError(f"symlinked artifact path component for {relative}")
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise ValueError(f"non-directory artifact path component for {relative}")

    artifact = root.joinpath(*parts)
    try:
        path_metadata = os.lstat(artifact)
    except OSError as exc:
        raise ValueError(f"missing {relative}") from exc
    if stat.S_ISLNK(path_metadata.st_mode):
        raise ValueError(f"artifact must not be a symlink: {relative}")
    if not stat.S_ISREG(path_metadata.st_mode):
        raise ValueError(f"artifact must be a regular file: {relative}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(artifact, flags)
    except OSError as exc:
        raise ValueError(f"artifact cannot be opened safely: {relative} ({exc})") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or (opened_metadata.st_dev, opened_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ValueError(f"artifact changed while opening: {relative}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        final_metadata = os.fstat(descriptor)
        if (
            final_metadata.st_size != opened_metadata.st_size
            or final_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            raise ValueError(f"artifact changed while hashing: {relative}")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _artifact_report(path: Path) -> tuple[str, int]:
    mismatches: list[str] = []
    total_bytes = 0
    for entry in DOCLING_ARTIFACT_FILES:
        relative = entry["relative_path"]
        try:
            actual_sha, actual_size = _read_regular_artifact(path, relative)
        except ValueError as exc:
            mismatches.append(str(exc))
            continue
        total_bytes += actual_size
        if actual_size != entry["size_bytes"]:
            mismatches.append(
                f"size mismatch for {relative} "
                f"(expected {entry['size_bytes']}, found {actual_size})"
            )
        if actual_sha != entry["sha256"]:
            mismatches.append(
                f"hash mismatch for {relative} "
                f"(expected {entry['sha256']}, found {actual_sha})"
            )
    if mismatches:
        raise ValueError("; ".join(mismatches))
    return DOCLING_MODEL_IDENTITY, total_bytes


def doctor_docling(artifacts_path: Path | str | None) -> DoclingDoctorReport:
    """Read-only compatibility check; never imports Docling or downloads models."""
    installed = _installed_versions()
    mismatched = {
        package: (DOCLING_COMPONENT_VERSIONS[package], actual)
        for package, actual in installed.items()
        if actual != DOCLING_COMPONENT_VERSIONS[package]
    }
    if mismatched:
        details = ", ".join(
            f"{package} expected {expected}, found {actual}"
            for package, (expected, actual) in mismatched.items()
        )
        return DoclingDoctorReport(
            ok=False,
            message=(
                f"Docling optional components are missing or incompatible: {details}. "
                "Install MasterVault with the 'pdf-layout' extra."
            ),
            component_versions=installed,
        )
    if artifacts_path is None:
        return DoclingDoctorReport(
            ok=False,
            message=(
                "No Docling artifacts path is configured. Set "
                "MV_DOCUMENT__DOCLING_ARTIFACTS_PATH to a prefetched directory; run "
                f"`{DOCLING_PREFETCH_COMMAND}` explicitly first. MasterVault never downloads it."
            ),
            component_versions=installed,
        )
    try:
        path = _artifact_root(artifacts_path)
    except ValueError as exc:
        return DoclingDoctorReport(
            ok=False,
            message=f"Configured Docling artifacts path is unsafe or unavailable: {exc}",
            component_versions=installed,
        )
    try:
        model_identity, artifact_bytes = _artifact_report(path)
    except ValueError as exc:
        return DoclingDoctorReport(
            ok=False,
            message=(
                f"Docling artifacts are missing or incompatible: {exc}. Re-run "
                f"`{DOCLING_PREFETCH_COMMAND.replace('PATH', str(path))}`."
            ),
            component_versions=installed,
        )
    return DoclingDoctorReport(
        ok=True,
        message="Docling clean-digital layout/table profile is available offline.",
        component_versions=installed,
        model_identity=model_identity,
        artifact_bytes=artifact_bytes,
    )


class DoclingParser:
    """CPU-only clean-digital adapter with an explicit verified artifact path."""

    name = DOCLING_PARSER_NAME
    parser_version = DOCLING_COMPONENT_VERSIONS["docling-slim"]
    parser_core_version = DOCLING_COMPONENT_VERSIONS["docling-core"]
    profile = DOCLING_PARSER_PROFILE
    resource_limits = DOCLING_RESOURCE_LIMITS

    def __init__(self, artifacts_path: Path | str | None):
        report = doctor_docling(artifacts_path)
        if not report.ok:
            raise UnreadableDocument(report.message)
        self.artifacts_path = _artifact_root(artifacts_path)  # type: ignore[arg-type]
        self.model_identity = report.model_identity or ""

    def parse(self, source: PdfSource) -> ParsedDocumentV2:
        actual_source_sha = hashlib.sha256(source.data).hexdigest()
        if actual_source_sha != source.asset_sha256:
            raise UnreadableDocument(
                f"{source.path.name}: source snapshot hash changed before Docling parsing"
            )
        if len(source.data) > DOCLING_MAX_SOURCE_BYTES:
            raise UnreadableDocument(
                f"{source.path.name}: PDF is {len(source.data)} bytes; the fixed Docling "
                f"profile accepts at most {DOCLING_MAX_SOURCE_BYTES} bytes"
            )
        # Apply offline controls around imports, converter construction and
        # conversion so no eager or lazy vendor path gets a network window.
        with patch.dict(
            os.environ,
            {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            clear=False,
        ):
            try:
                return self._parse_offline(source)
            except UnreadableDocument:
                raise
            except Exception as exc:
                raise UnreadableDocument(
                    f"{source.path.name}: Docling clean-digital parsing failed offline "
                    f"({type(exc).__name__}: {exc}). Verify the artifacts with "
                    "`mvault document doctor --parser docling`; OCR/scanned PDFs are unsupported."
                ) from exc

    def _parse_offline(self, source: PdfSource) -> ParsedDocumentV2:
        # The doctor result is not a capability token: a long-lived parser may
        # outlive a cache mutation. Recheck before any vendor model can open it.
        self._assert_artifact_identity()

        # All imports of Docling and Docling Core are intentionally confined here.
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import (
            HeadingHierarchyOptions,
            PdfPipelineOptions,
            TableFormerMode,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            artifacts_path=self.artifacts_path,
            document_timeout=DOCLING_DOCUMENT_TIMEOUT_SECONDS,
            accelerator_options=AcceleratorOptions(
                device=AcceleratorDevice.CPU,
                num_threads=2,
            ),
            enable_remote_services=False,
            allow_external_plugins=False,
            do_ocr=False,
            do_table_structure=True,
            do_picture_classification=False,
            do_picture_description=False,
            do_chart_extraction=False,
            do_code_enrichment=False,
            do_formula_enrichment=False,
            generate_page_images=False,
            generate_picture_images=False,
            # Style is the useful deterministic signal for the certified
            # business fixture; numbering alone flattens its peer sections.
            generate_parsed_pages=True,
            heading_hierarchy_options=HeadingHierarchyOptions(
                **DOCLING_HEADING_HIERARCHY_OPTIONS
            ),
        )
        # The concrete PDF option types expose these fields at runtime, while
        # Docling annotates their container properties as broader base classes.
        layout_options = cast(Any, options.layout_options)
        table_options = cast(Any, options.table_structure_options)
        layout_options.engine_options.compile_model = False
        table_options.mode = TableFormerMode.FAST
        table_options.do_cell_matching = True
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
        try:
            # Force model initialization from the verified cache, then verify
            # it again at the last boundary before document conversion. This
            # narrows (but cannot eliminate) filesystem TOCTOU; the configured
            # cache remains a trusted, operator-owned immutable directory.
            with warnings.catch_warnings():
                # Docling 2.118 reads its own deprecated field while deciding
                # whether to generate images. MasterVault does not set/use that
                # field; silence only this exact upstream access warning.
                warnings.filterwarnings(
                    "ignore",
                    message=r"This field is deprecated\. Use `generate_page_images=True`.*",
                    category=DeprecationWarning,
                    module=r"docling\.pipeline\.standard_pdf_pipeline",
                )
                converter.initialize_pipeline(InputFormat.PDF)
                self._assert_artifact_identity()
                result = _convert_with_limits(
                    converter,
                    DocumentStream(name=source.path.name, stream=BytesIO(source.data)),
                )
            status = getattr(result.status, "value", str(result.status)).lower()
            if status != "success":
                raise ValueError(f"conversion status was {status}")
            exported = result.document.export_to_dict()
            if not isinstance(exported, dict):
                raise ValueError("Docling export was not a JSON object")
            return normalize_docling_export(
                exported,
                asset_sha256=source.asset_sha256,
                parser_version=self.parser_version,
                parser_core_version=self.parser_core_version,
                model_identity=self.model_identity,
            )
        except Exception as exc:
            raise UnreadableDocument(
                f"{source.path.name}: Docling clean-digital parsing failed offline "
                f"({type(exc).__name__}: {exc}). Verify the artifacts with "
                "`mvault document doctor --parser docling`; OCR/scanned PDFs are unsupported."
            ) from exc

    def _assert_artifact_identity(self) -> None:
        try:
            model_identity, _artifact_bytes = _artifact_report(self.artifacts_path)
        except ValueError as exc:
            raise UnreadableDocument(
                f"Docling artifacts changed after verification: {exc}"
            ) from exc
        if model_identity != self.model_identity:
            raise UnreadableDocument(
                "Docling artifact manifest identity changed after verification"
            )
