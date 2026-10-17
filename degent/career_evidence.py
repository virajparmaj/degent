"""Conservative experience-route proposals; titles never establish career eligibility."""

from __future__ import annotations

import re

from .evidence import select_units

VERSION = "experience-route-screen-v1"
YEAR = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<max>\d+(?:\.\d+)?))?\s*\+?\s*years?\b", re.I
)
EDUCATION = re.compile(r"Ph\.?D\.?|doctorate|doctoral|master[’']?s?|\bMBA\b|bachelor[’']?s?", re.I)
LEADERSHIP = re.compile(
    r"\b(?:manage|managing|supervise|supervising|lead|leading|oversee|overseeing)\b"
    r".{0,60}\b(?:a team of|teams? of|direct reports|people|staff|associates|employees)\b|"
    r"\b(?:people (?:leadership|management)|hiring and (?:managing|developing)|"
    r"performance (?:reviews|evaluations) (?:for|of))\b",
    re.I,
)
EXPERIENCE = re.compile(r"experienc|professional|working|work\b", re.I)


def _evidence(unit: dict) -> dict:
    return {"start": unit["start"], "end": unit["end"], "text": unit["text"]}


def _routes(unit: dict) -> list[dict]:
    """Keep alternatives per statement; overlapping skill requirements are not added."""
    text = unit["text"]
    if not EXPERIENCE.search(text):
        return []
    branches = _experience_alternatives(text)
    result = []
    for branch in branches:
        years = [float(match.group("min")) for match in YEAR.finditer(branch)]
        # A degree-only alternative is eligible to review, not an explicit zero-year claim.
        if not years and not (len(branches) > 1 and EDUCATION.search(branch)):
            continue
        result.append(
            {
                "branch_text": branch,
                "minimum_years": max(years) if years else None,
                "years_stated": bool(years),
                "education_route": bool(EDUCATION.search(branch)),
                "statement_evidence": _evidence(unit),
            }
        )
    return result


def _experience_alternatives(text: str) -> list[str]:
    """Separate tenure/degree routes, not 'Engineering or Mathematics' or skill lists."""
    boundaries = []
    for match in re.finditer(r"\s+or\s+", text, re.I):
        prefix = text[: match.start()]
        if prefix.count("(") != prefix.count(")"):
            continue
        following = text[match.end() :]
        if re.match(
            r"(?:bachelor|master|Ph\.?D|doctorate|doctoral|MBA|at least|\d+\s*\+?\s*years?)",
            following,
            re.I,
        ):
            boundaries.append((match.start(), match.end()))
    branches, cursor = [], 0
    for start, end in boundaries:
        branches.append(text[cursor:start])
        cursor = end
    return [*branches, text[cursor:]]


def career_evidence(text: str) -> dict:
    """Report required/preferred tenure, alternatives and explicit leadership cues."""
    required, preferred, ambiguous, leadership = [], [], [], []
    qualification_section = False
    for unit in select_units(text)["units"]:
        if unit["heading"]:
            qualification_section = unit["statement_scope"] == "qualification"
            continue
        if not unit["selected"]:
            continue
        if LEADERSHIP.search(unit["text"]):
            heading = (unit.get("section") or "").casefold()
            if "preferred" not in heading and "desired" not in heading:
                leadership.append(_evidence(unit))
        routes = _routes(unit)
        if not routes:
            continue
        heading = (unit.get("section") or "").casefold()
        if "preferred" in heading or "desired" in heading:
            preferred.append(routes)
        elif qualification_section:
            required.append(routes)
        else:
            ambiguous.append(routes)
    return _summary(required, preferred, ambiguous, leadership)


def _summary(required: list, preferred: list, ambiguous: list, leadership: list) -> dict:
    bound = None
    if required:
        bounds = [
            min(route["minimum_years"] if route["years_stated"] else 0 for route in routes)
            for routes in required
        ]
        bound = max(bounds)
    status = "within_year_screen" if bound is not None and bound <= 5 else "experience_unstated"
    if bound is not None and bound > 5:
        status = "above_year_screen"
    if leadership:
        status = "people_leadership_requires_semantic_review"
    return {
        "version": VERSION,
        "status": status,
        "required_route_screen_years": bound,
        "required_statements": required,
        "preferred_statements": preferred,
        "unclassified_experience_statements": ambiguous,
        "leadership_evidence": leadership,
        "year_screen_is_final_career_assessment": False,
        "degree_only_alternative_is_explicit_zero_years": False,
        "title_used_for_eligibility": False,
        "reviewer": "offline_source_screen",
        "human_gold": False,
    }
