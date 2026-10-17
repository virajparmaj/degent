"""Shared exact-context checks for independent gold and interpreted attributes."""

from __future__ import annotations

import re

from .phase_state import finite
from .vocabulary import vocabulary

ATTRIBUTES = (
    "qualifier",
    "experience_range",
    "experience_years",
    "alternatives",
    "alternative_logic",
)
NUMBER_WORDS = dict(
    enumerate(
        (
            "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        ).split()
    )
)


def attribute_errors(row: dict, context: str) -> list[str]:
    errors = []
    qualifier = row.get("qualifier")
    if qualifier is not None and (not isinstance(qualifier, str) or qualifier not in context):
        errors.append("unsupported qualifier context")
    alternatives = row.get("alternatives")
    if not isinstance(alternatives, list) or any(
        not isinstance(a, str) or not a.strip() or a.casefold() not in context.casefold()
        for a in alternatives
    ):
        errors.append("unsupported alternatives context")
    logic = row.get("alternative_logic")
    if alternatives and (
        logic not in {"or", "and"}
        or not re.search(r"\b" + str(logic) + r"\b|and/or", context, re.I)
    ):
        errors.append("unsupported alternative logic")
    if not alternatives and logic is not None:
        errors.append("alternative logic without alternatives")
    years = row.get("experience_range")
    if years is not None and (
        not isinstance(years, list)
        or len(years) != 2
        or all(y is None for y in years)
        or any(y is not None and not finite(y) for y in years)
        or years[0] is not None
        and years[1] is not None
        and years[0] > years[1]
    ):
        errors.append("invalid experience range")
        return errors
    single = row.get("experience_years")
    if single is not None and (not finite(single) or years != [single, single]):
        errors.append("inconsistent experience years/range")
    values = ([v for v in years if v is not None] if years else []) + (
        [single] if finite(single) else []
    )
    if values and not re.search(r"\b(?:years?|yrs?)\b", context, re.I):
        errors.append("experience unit missing")
    for value in values:
        number = str(int(value) if value == int(value) else value)
        variants = [number, NUMBER_WORDS.get(value, "")]
        if not any(
            v and re.search(r"(?<![\w.])" + re.escape(v) + r"(?![\w.])", context, re.I)
            for v in variants
        ):
            errors.append("unsupported experience number")
    return errors


def validate_gold(labels: list[dict], fixed: dict, skills: dict) -> None:
    known, seen = set(vocabulary()) | {k for k, v in skills.items() if v.get("approved")}, set()
    for row in labels:
        concept = row.get("concept")
        if concept and concept not in known:
            raise ValueError(
                "Unknown canonical gold concept; preserve unresolved original_concept instead."
            )
        phrase = row.get("original_concept")
        if not concept and (not isinstance(phrase, str) or not phrase.strip()):
            raise ValueError("Gold needs a known canonical concept or unresolved original phrase.")
        key = (concept or phrase.casefold(), row["evidence_start"], row["evidence_end"])
        if key in seen:
            raise ValueError("Duplicate gold statement.")
        seen.add(key)
        context = row["evidence"]
        # The enclosing preserved unit is an explicit context, including headings.
        units = [
            u
            for u in fixed["selection"]["units"]
            if u["start"] <= row["evidence_start"] and u["end"] >= row["evidence_end"]
        ]
        if units:
            context = units[0]["text"]
        if not isinstance(row.get("attribute_evidence", []), list):
            raise ValueError("Attribute supporting evidence must be a list.")
        for span in row.get("attribute_evidence", []):
            if not isinstance(span, dict):
                raise ValueError("Attribute supporting spans must be objects.")
            start, end = span.get("start"), span.get("end")
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(fixed["full_text"])
                or not span.get("text")
                or fixed["full_text"][start:end] != span["text"]
                or not span.get("relation")
            ):
                raise ValueError(
                    "Attribute supporting span requires exact evidence and explicit relation."
                )
            context += "\n" + span["text"]
        errors = attribute_errors(row, context)
        if errors:
            raise ValueError("Unsupported gold explicit attribute: " + "; ".join(errors))
