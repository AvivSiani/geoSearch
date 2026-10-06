"""Item citations (`[i3]`) in model-written text, checked by code (Stage 5).

Models cite items by short id. A small model can mangle or invent an id, so
every text a model writes — a summarizer's summary, the final answer — passes
through `strip_invalid_ids` before anyone downstream trusts it. Validity is
always decided against data (the rows given, or the conversation's items),
never by asking a model.
"""

import re

# One or more ids in one bracket: [i3] or [i3, i7] (both styles appear in practice).
_CITATION = re.compile(r"\s?\[\s*(i\d+(?:\s*,\s*i\d+)*)\s*\]")
_ID = re.compile(r"i\d+")


def cited_ids(text: str) -> list[str]:
    """Every cited id, in order of first appearance."""
    seen: dict[str, None] = {}
    for match in _CITATION.finditer(text):
        for item_id in _ID.findall(match.group(1)):
            seen.setdefault(item_id, None)
    return list(seen)


def strip_invalid_ids(text: str, valid: set[str] | frozenset[str]) -> str:
    """Drop cited ids not in `valid`; a bracket left empty disappears with the
    space before it. Kept ids are normalized to one bracket each: `[i3][i7]`."""

    def keep(match: re.Match[str]) -> str:
        ids = [i for i in _ID.findall(match.group(1)) if i in valid]
        if not ids:
            return ""
        lead = " " if match.group(0)[0].isspace() else ""
        return lead + "".join(f"[{i}]" for i in ids)

    return _CITATION.sub(keep, text)
