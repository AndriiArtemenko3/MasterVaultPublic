"""Prompt registry: load/validate claim_extraction v1, StrictUndefined, schema section."""

from __future__ import annotations

import pytest
from jinja2 import exceptions as jinja_exceptions

from mastervault.contracts.base import schema_section
from mastervault.contracts.claims import ClaimExtractionOut
from mastervault.core.errors import PromptInvalidError, PromptNotFoundError
from mastervault.prompts import registry

VARIABLES = {
    "title": "Refund FAQ",
    "source_type": "faq",
    "domain": "operations",
    "body": "Refunds are issued within 30 days.",
}


def test_load_claim_extraction_v1():
    spec = registry.load("claim_extraction", version=1)
    assert spec.contract_id == "claim_extraction"
    assert spec.version == 1
    assert spec.tier == "small"
    assert spec.output_model is ClaimExtractionOut
    assert spec.variables == ("title", "source_type", "domain", "body")


def test_load_defaults_to_latest_version():
    spec = registry.load("claim_extraction")
    versions = registry.available_versions("claim_extraction")
    assert spec.version == max(versions)


def test_render_substitutes_all_variables():
    spec = registry.load("claim_extraction")
    rendered = spec.render(VARIABLES)
    assert "Refund FAQ" in rendered
    assert "Refunds are issued within 30 days." in rendered
    assert "{{" not in rendered


def test_strict_undefined_raises_on_missing_variable():
    spec = registry.load("claim_extraction")
    incomplete = {k: v for k, v in VARIABLES.items() if k != "body"}
    with pytest.raises(jinja_exceptions.UndefinedError, match="body"):
        spec.render(incomplete)


def test_unknown_contract_raises():
    with pytest.raises(PromptNotFoundError, match="no-such-contract"):
        registry.load("no-such-contract")


def test_unknown_version_raises():
    with pytest.raises(PromptNotFoundError, match="v99"):
        registry.load("claim_extraction", version=99)


def test_schema_section_rendered_from_output_model():
    section = schema_section(ClaimExtractionOut)
    assert "## Output format" in section
    assert '"claims"' in section
    assert '"affects_candidates"' in section
    # Field descriptions travel into the schema, so prompt guidance and model agree.
    assert "kebab-case" in section


# -- header validation (drives _parse_spec directly with crafted text) --------

GOOD_HEADER = (
    "---\n"
    "contract: claim_extraction\n"
    "version: 1\n"
    "tier: small\n"
    "output_model: mastervault.contracts.claims.ClaimExtractionOut\n"
    "variables: [title]\n"
    "---\n"
    "Body {{ title }}\n"
)


def test_parse_spec_accepts_valid_text():
    spec = registry._parse_spec(GOOD_HEADER, "claim_extraction", 1, source="test")
    assert spec.render({"title": "x"}).startswith("Body x")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (("contract: claim_extraction", "contract: other"), "directory"),
        (("version: 1", "version: 7"), "filename version"),
        (("tier: small", "tier: gigantic"), "tier"),
        (
            (
                "output_model: mastervault.contracts.claims.ClaimExtractionOut",
                "output_model: mastervault.contracts.claims.Missing",
            ),
            "not found",
        ),
        (("Body {{ title }}", "Body {{ undeclared_var }}"), "does not render"),
    ],
)
def test_parse_spec_rejects_bad_headers(mutation, match):
    old, new = mutation
    with pytest.raises(PromptInvalidError, match=match):
        registry._parse_spec(GOOD_HEADER.replace(old, new), "claim_extraction", 1, source="test")
