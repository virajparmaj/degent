"""Derived company-boilerplate removal with reversible original-source offsets."""

from __future__ import annotations

import re

from .evidence import BOILERPLATE, select_units
from .store import digest

VERSION = "capital-one-analysis-text-v1"
INTRO = re.compile(
    r"^(?:(?:Here )?[Aa]t Capital One, (?:we (?:are|believe|think)|we[’']re|data is)|"
    r"Capital One(?:, a Fortune| is (?:a |an |on a mission|experiencing))|"
    r"Data is at the center|As a startup, we disrupted|When we launched as a startup|"
    r"Fast.forward a few years|We believe our portfolio|We offer a broad spectrum|"
    r"We nurture a work environment|Still founder.led|"
    r"Our investments in technology infrastructure|For years, Capital One|"
    r"From informing customers|We are committed to continuing to build|"
    r"We measure our efforts by|We are succeeding because|"
    r"Product Management at Capital One is a booming)",
    re.I,
)
ROLE = re.compile(
    r"\b(?:you|your|this (?:role|position)|the candidate|applicants?|"
    r"qualifications?|requirements?|must|shall|sponsor|employment authorization|"
    r"remote employee|remote.eligible|(?:required|preferred) experience)\b",
    re.I,
)
SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s+[A-Z]|$)|$)")


def company_removals(text: str) -> list[dict]:
    """Remove explicit templates, retaining role expectations in mixed paragraphs."""
    removals = []
    for unit in select_units(text)["units"]:
        if BOILERPLATE.search(unit["text"]) and not (
            ROLE.search(unit["text"]) and INTRO.search(unit["text"])
        ):
            removals.append(_span(unit["start"], unit["end"], text, "explicit_template"))
            continue
        if not INTRO.search(unit["text"]):
            continue
        # An unambiguously company-only paragraph can contain continuation sentences.
        if not ROLE.search(unit["text"]):
            removals.append(_span(unit["start"], unit["end"], text, "company_introduction"))
            continue
        for match in SENTENCE.finditer(unit["text"]):
            sentence = match.group().strip()
            if INTRO.search(sentence) and not ROLE.search(sentence):
                start = (
                    unit["start"] + match.start() + len(match.group()) - len(match.group().lstrip())
                )
                removals.append(_span(start, start + len(sentence), text, "company_intro_sentence"))
    return removals


def _span(start: int, end: int, text: str, reason: str) -> dict:
    return {"start": start, "end": end, "text": text[start:end], "reason": reason}


def analysis_text(text: str) -> dict:
    """Return cleaned content, retained span mapping, and every removed exact span."""
    return _derive(text, company_removals(text))


def reviewed_analysis_text(
    text: str, removals: list[dict], retained_role_spans: list[dict]
) -> dict:
    """Apply explicit full-text review, preserving cited duties and requirements.

    This validates review evidence; it does not assert that a rule-based screen
    performed semantic review. Callers must supply honest reviewer provenance.
    """
    for span in [*removals, *retained_role_spans]:
        start, end = span.get("start"), span.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or span.get("text") != text[start:end]
        ):
            raise ValueError("Reviewed omission evidence differs from original text.")
    for removal in removals:
        if not removal.get("reason"):
            raise ValueError("Reviewed company removal needs an explicit reason.")
        if any(
            removal["start"] < span["end"] and removal["end"] > span["start"]
            for span in retained_role_spans
        ):
            raise ValueError("Company removal overlaps retained role requirements or duties.")
    result = _derive(text, removals)
    result.update(
        full_text_omission_review_complete=True,
        retained_role_evidence=retained_role_spans,
        version=VERSION + ":agent-reviewed-v1",
    )
    return result


def _derive(text: str, removals: list[dict]) -> dict:
    removed = sorted(removals, key=lambda span: span["start"])
    retained, pieces, source_cursor, derived_cursor = [], [], 0, 0
    for span in [*removed, {"start": len(text), "end": len(text)}]:
        if span["start"] < source_cursor:
            raise ValueError("Overlapping company-boilerplate removal spans.")
        piece = text[source_cursor : span["start"]]
        if piece:
            pieces.append(piece)
            retained.append(
                {
                    "source_start": source_cursor,
                    "source_end": span["start"],
                    "derived_start": derived_cursor,
                    "derived_end": derived_cursor + len(piece),
                }
            )
            derived_cursor += len(piece)
        source_cursor = span["end"]
    cleaned = "".join(pieces)
    return {
        "version": VERSION,
        "source_sha256": digest(text),
        "text": cleaned,
        "analysis_sha256": digest(cleaned),
        "retained_spans": retained,
        "removed_spans": removed,
        "offset_basis": "original_preserved_text",
        "full_text_omission_review_complete": False,
    }


def masked_text(text: str, removals: list[dict]) -> str:
    """Keep source offsets identical when reusing a source-bound duty classifier."""
    pieces, cursor = [], 0
    for span in removals:
        pieces.append(text[cursor : span["start"]])
        pieces.append("".join("\n" if char == "\n" else " " for char in span["text"]))
        cursor = span["end"]
    pieces.append(text[cursor:])
    return "".join(pieces)
