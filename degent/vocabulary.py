"""Versioned phrase candidates; context remains visible for human review."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "config" / "vocabulary-v2.json"
VERSION = "requirements-v2"
SCOPES = {
    "qualification",
    "responsibility",
    "company_expectation",
    "company_description",
    "hiring_process",
    "unknown",
}
STATUSES = {"required", "preferred", "not_required", "unclear"}


def vocabulary() -> dict[str, dict[str, Any]]:
    data = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["concepts"]}


def section_kind(line: str) -> tuple[str, str] | None:
    heading = line.strip().strip("#*: -").lower().replace("’", "'")
    if len(heading) > 90:
        return None
    groups = [
        (
            "hiring_process",
            "unclear",
            r"^(apply for|application|recruitment|hiring process|privacy notice|equal opportunity)",
        ),
        (
            "company_expectation",
            "unclear",
            r"^(how we work|our values|our culture|who thrives here)",
        ),
        (
            "company_description",
            "unclear",
            r"^(about us|about (?!the role)[a-z ]+$|who we are|benefits|what we offer)",
        ),
        ("qualification", "preferred", r"^(nice to have|preferred|bonus|ideally)"),
        (
            "qualification",
            "required",
            r"^(requirements|qualifications|basic qualifications|minimum qualifications|what you.ll (need|bring)|what you need|who you are|what we.re looking for|what we are looking for|what we look for)",
        ),
        (
            "responsibility",
            "unclear",
            r"^((?:(?:general|key|specific|your)\s+)?responsibilities|what you.ll do|what you will do|you will|the role|about the role)",
        ),
    ]
    for scope, strength, pattern in groups:
        if re.search(pattern, heading):
            return scope, strength
    return None


def context(phrase: str, scope: str, strength: str) -> tuple[str, str]:
    lower = phrase.lower().replace("’", "'")
    if re.search(
        r"(screen|review|match|process).{0,40}(application|candidate)|talent matching", lower
    ):
        return "hiring_process", "unclear"
    if re.search(r"\b(we|our company)\b.{0,30}\b(build|develop|sell|provide)\b", lower):
        scope = "company_description"
    if re.search(
        r"\b(no|without)\b.{0,45}\b(experience|knowledge|proficiency)\b.{0,15}\brequired\b|\bnot (?:a )?(?:requirement|required)\b",
        lower,
    ):
        return "qualification", "not_required"
    if re.search(r"\b(preferred|ideally|nice to have|bonus|a plus)\b", lower):
        return "qualification" if scope in {"unknown", "qualification"} else scope, "preferred"
    if re.search(r"\b(required|must|minimum|you have|experience with|proficiency)\b", lower):
        return "qualification" if scope in {"unknown", "qualification"} else scope, "required"
    return scope, strength


def candidates(document_id: str, text: str, aliases: dict[str, str]) -> dict[str, Any]:
    concepts = vocabulary()
    patterns = {phrase.lower(): key for key, row in concepts.items() for phrase in row["phrases"]}
    patterns.update(aliases)
    rows = []
    scope, strength, section = "unknown", "unclear", None
    for line in text.splitlines():
        heading = section_kind(line)
        if heading:
            scope, strength = heading
            section = line.strip()
        for phrase in re.split(r"(?<=[.!?;])\s+|\s+but\s+", line):
            phrase = phrase.strip()
            if not phrase:
                continue
            current_scope, status = context(phrase, scope, strength)
            seen = set()
            for term, concept in sorted(patterns.items(), key=lambda x: -len(x[0])):
                flags = 0 if term == "r" else re.I
                match = re.search(
                    r"(?<!\w)" + re.escape("R" if term == "r" else term) + r"(?!\w)", phrase, flags
                )
                if not match or concept in seen:
                    continue
                if concept == "ownership" and re.search(r"equity|shares|stock", phrase, re.I):
                    continue
                seen.add(concept)
                years = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s+years?", phrase, re.I)
                row = {
                    "category": concepts.get(concept, {}).get("category", "technical"),
                    "phrase": phrase,
                    "concept": concept,
                    "status": status,
                    "explicit": True,
                    "evidence": match.group(0),
                    "section": section,
                    "qualifier": None,
                    "experience_years": float(years.group(1)) if years else None,
                    "alternatives": [
                        v.strip().rstrip(".,;:")
                        for v in re.findall(
                            r"\b(?:or|and/or)\s+([A-Za-z][A-Za-z+#. -]{0,24})", phrase
                        )
                    ],
                    "negated": status == "not_required",
                    "statement_scope": current_scope,
                    "match_basis": "exact_phrase",
                    "task": None,
                    "tools": [],
                    "review_reason": "Phrase match needs meaning review; check scope, strength, and any experience or alternatives.",
                }
                # Without clear clause boundaries, mixed requirements require review.
                if re.search(r"\b(required|must)\b", phrase, re.I) and re.search(
                    r"\b(preferred|bonus)\b", phrase, re.I
                ):
                    row["status"] = "unclear"
                rows.append(row)
    return {"document_id": document_id, "requirements": rows, "status": "rule_candidates"}
