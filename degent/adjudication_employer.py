"""Verify optional raw application evidence without aliasing employer assertions."""

from __future__ import annotations

import re
from pathlib import Path

from .store import digest


def validate_application_evidence(original: dict, proof: dict, requisition: str | None) -> None:
    """Bind every application link to the original response and exact requisition.

    This validates evidence only. The agent must separately explain its employer
    interpretation, preserving a conflicting structured organization assertion.
    """
    raw = Path(proof["raw_source_file"]).read_bytes()
    if digest(raw) != original.get("raw_source_sha256") or digest(raw) != proof.get(
        "raw_source_sha256"
    ):
        raise ValueError("Employer application evidence source hash changed.")
    source = raw.decode("utf-8", errors="strict")
    if proof.get("decoded_source_sha256") != digest(source) or proof.get("offset_unit") != (
        "unicode_codepoints_in_decoded_source"
    ):
        raise ValueError("Employer application evidence encoding or offsets changed.")
    organization = original.get("original_posting_record", {}).get("hiringOrganization")
    if proof.get("hiring_organization_assertion") != organization:
        raise ValueError("Original employer assertion must be retained exactly.")
    conflict = isinstance(organization, dict) and organization.get("name") == "People Center"
    if proof.get("conflicting_employer_assertion_retained") is not conflict:
        raise ValueError("Conflicting employer assertion cannot silently disappear.")
    if not requisition or not proof.get("resolution") or not proof.get("source_spans"):
        raise ValueError("Employer evidence requires a requisition, links and interpretation.")
    for span in proof["source_spans"]:
        start, end, text = span["start"], span["end"], span["text"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(source)
            or source[start:end] != text
            or not re.fullmatch(
                r"https://capitalone\.wd1\.myworkdayjobs\.com/Capital_One/job/"
                r'[^"<> ]+_' + re.escape(requisition) + r"(?:-\d+)?/apply",
                text,
            )
        ):
            raise ValueError("Employer link must match raw offsets and this exact requisition.")
