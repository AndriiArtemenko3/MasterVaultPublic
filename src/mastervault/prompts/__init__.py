"""Versioned prompt files, one directory per contract id.

Layout: mastervault/prompts/<contract_id>/v<N>.md — YAML header between `---`
fences ({contract, version, tier, output_model, variables}) followed by a
Jinja2 body rendered with StrictUndefined. Load through
`mastervault.prompts.registry.load`, never by raw path, so installed wheels
and editable checkouts behave identically.
"""

from mastervault.prompts.registry import PromptSpec, load

__all__ = ["PromptSpec", "load"]
