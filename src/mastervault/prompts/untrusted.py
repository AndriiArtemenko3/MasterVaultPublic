"""Structural delimiting for text that came out of a document, not the operator.

Document bodies, claim statements and wiki text all reach a prompt as raw
content. Interpolating them straight into instructions leaves nothing marking
where the operator's instructions stop and the corpus's text begins, so a
document reading "ignore the above and ..." arrives in the same voice as the
task itself.

`fence` wraps that text in explicit, named markers and neutralises any copy of
the closing marker inside the text, so a document cannot close its own fence
and continue as instructions. Two properties are mechanical:

- the fenced block always opens and closes exactly once, and
- no attacker-supplied byte can produce a line equal to the closing marker.

What this does NOT do is make the model obey the boundary. Delimiting removes
the *structural* ambiguity; whether the model then treats fenced text as data
is model behaviour, and nothing here enforces it. Prompt injection is not
solved by this module -- see SECURITY.md for what is and is not enforced.
"""

from __future__ import annotations

import re

BEGIN_MARKER = "<<<BEGIN UNTRUSTED {label}>>>"
END_MARKER = "<<<END UNTRUSTED {label}>>>"

#: What a forged marker inside the payload is rewritten to. Keeps the text
#: readable while making it impossible to terminate the fence early.
_NEUTRALISED = "<<!"


_LABEL_SAFE_RE = re.compile(r"[^A-Z0-9 _-]")


def _markers(label: str) -> tuple[str, str]:
    """Markers for `label`, reduced to characters that cannot forge a marker.

    The label is normally a literal, but it is part of the delimiter, so a
    caller that ever derived one from data could otherwise emit a second BEGIN
    marker or a stray END. Restricting it keeps "opens and closes exactly once"
    an invariant of the function rather than a property of its call sites.
    """
    upper = _LABEL_SAFE_RE.sub("", label.upper().strip()) or "TEXT"
    return BEGIN_MARKER.format(label=upper), END_MARKER.format(label=upper)


def neutralise(text: str) -> str:
    """Defang any `<<<`-style marker the payload contains."""
    return text.replace("<<<", _NEUTRALISED)


def fence(text: str, label: str = "DOCUMENT") -> str:
    """Wrap untrusted text in named markers it cannot break out of."""
    begin, end = _markers(label)
    return f"{begin}\n{neutralise(text)}\n{end}"
