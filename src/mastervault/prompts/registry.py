"""Prompt registry: locate, parse, and validate versioned prompt files.

A prompt file is YAML frontmatter + a Jinja2 body:

    ---
    contract: claim_extraction
    version: 1
    tier: small
    output_model: mastervault.contracts.claims.ClaimExtractionOut
    variables: [title, source_type, domain, body]
    ---
    <jinja2 body>

Loading validates everything that can rot silently: the header parses, its
identity fields match the file location, the output_model imports and is a
pydantic BaseModel, and the body renders under StrictUndefined given exactly
the declared variables (so an undeclared `{{ var }}` fails at load, not at
dispatch time). Files resolve through importlib.resources, so the registry
works from an installed wheel as well as a source checkout.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined
from jinja2 import exceptions as jinja_exceptions
from pydantic import BaseModel

from mastervault.core.errors import PromptInvalidError, PromptNotFoundError
from mastervault.vaultfs.frontmatter import FrontmatterError, parse_frontmatter

_PROMPTS_PACKAGE = "mastervault.prompts"
_VERSION_FILE_RE = re.compile(r"^v(\d+)\.md$")
_VALID_TIERS = ("small", "medium", "large")

_env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


@dataclass(frozen=True)
class PromptSpec:
    contract_id: str
    version: int
    tier: str
    output_model: type[BaseModel]
    variables: tuple[str, ...]
    body: str  # raw Jinja2 template source (schema section NOT included)

    def render(self, variables: Mapping[str, Any]) -> str:
        """Render the body. StrictUndefined: a missing variable raises UndefinedError."""
        return _env.from_string(self.body).render(dict(variables))


def _import_output_model(dotted: str, source: str) -> type[BaseModel]:
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise PromptInvalidError(f"{source}: output_model {dotted!r} is not a dotted path")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PromptInvalidError(f"{source}: cannot import output_model module: {exc}") from exc
    model = getattr(module, attr, None)
    if model is None:
        raise PromptInvalidError(f"{source}: output_model attribute not found: {dotted!r}")
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise PromptInvalidError(f"{source}: output_model {dotted!r} is not a pydantic BaseModel")
    return model


def _parse_spec(text: str, contract_id: str, version: int, source: str) -> PromptSpec:
    try:
        header, body = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise PromptInvalidError(f"{source}: bad header: {exc}") from exc

    for key in ("contract", "version", "tier", "output_model"):
        if key not in header:
            raise PromptInvalidError(f"{source}: header missing required key {key!r}")
    if header["contract"] != contract_id:
        raise PromptInvalidError(
            f"{source}: header contract {header['contract']!r} != directory {contract_id!r}"
        )
    if int(header["version"]) != version:
        raise PromptInvalidError(
            f"{source}: header version {header['version']!r} != filename version v{version}"
        )
    tier = header["tier"]
    if tier not in _VALID_TIERS:
        raise PromptInvalidError(f"{source}: tier {tier!r} not in {_VALID_TIERS}")

    raw_vars = header.get("variables", [])
    if not isinstance(raw_vars, list) or not all(isinstance(v, str) for v in raw_vars):
        raise PromptInvalidError(f"{source}: variables must be a list of names")
    variables = tuple(raw_vars)

    output_model = _import_output_model(str(header["output_model"]), source)

    body = body.lstrip("\n")
    spec = PromptSpec(
        contract_id=contract_id,
        version=version,
        tier=tier,
        output_model=output_model,
        variables=variables,
        body=body,
    )
    # Prove the body renders given exactly the declared variables.
    try:
        spec.render({name: f"<{name}>" for name in variables})
    except jinja_exceptions.TemplateError as exc:
        raise PromptInvalidError(
            f"{source}: body does not render with declared variables {list(variables)}: {exc}"
        ) from exc
    return spec


def available_versions(contract_id: str) -> dict[int, Any]:
    """Map version number -> traversable file for one contract's prompt dir."""
    root = resources.files(_PROMPTS_PACKAGE) / contract_id
    if not root.is_dir():
        raise PromptNotFoundError(
            f"no prompt directory for contract {contract_id!r} under {_PROMPTS_PACKAGE}"
        )
    versions: dict[int, Any] = {}
    for entry in root.iterdir():
        m = _VERSION_FILE_RE.match(entry.name)
        if m and entry.is_file():
            versions[int(m.group(1))] = entry
    if not versions:
        raise PromptNotFoundError(f"prompt directory for {contract_id!r} has no v<N>.md files")
    return versions


def load(contract_id: str, version: int | None = None) -> PromptSpec:
    """Load + validate one prompt. version=None -> highest available version."""
    versions = available_versions(contract_id)
    if version is None:
        version = max(versions)
    if version not in versions:
        raise PromptNotFoundError(
            f"contract {contract_id!r} has no v{version}.md (available: {sorted(versions)})"
        )
    text = versions[version].read_text(encoding="utf-8")
    return _parse_spec(text, contract_id, version, source=f"{contract_id}/v{version}.md")
