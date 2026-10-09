from __future__ import annotations

import re
from typing import Any

TRACK_TERMS = {
    "fraud_analytics_strategy": (
        "fraud analytics",
        "fraud strategy",
        "fraud detection",
        "fraud risk",
        "transaction monitoring",
    ),
    "credit_risk_analytics": (
        "credit risk",
        "credit analytics",
        "underwriting",
        "credit policy",
        "credit strategy",
    ),
    "quantitative_credit_risk_capital_modeling": (
        "quantitative",
        "model validation",
        "capital modeling",
        "capital analytics",
        "pricing model",
        "risk model",
    ),
}
EXCLUDE_TERMS = (
    "investigator",
    "aml case",
    "financial planning",
    "fp&a",
    "trading",
    "portfolio manager",
    "intern",
    "staffing",
    "machine learning infrastructure",
)
US_STATES = (
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
)
NON_US_MARKERS = (
    "ireland",
    "dublin",
    "united kingdom",
    "england",
    "singapore",
    "india",
    "canada",
    "australia",
    "mexico",
    "brazil",
    "france",
    "germany",
    "spain",
    "netherlands",
    "china",
    "japan",
    "philippines",
    "poland",
    "czech republic",
    "switzerland",
    "israel",
)
US_STATE_ABBREVIATIONS = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}


def classify_geography(url: str, text: str) -> dict[str, str]:
    """Conservatively require posting-specific evidence for a US location or US remote scope."""
    url_text = url.lower().replace("_", "-")
    if re.search(r"united-states(?:-of-america)?|\busa\b|\bu\.s\.a\b", url_text):
        return {"status": "us_location", "evidence": "posting URL identifies the United States"}
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    location_lines = []
    for index, line in enumerate(lines):
        if re.match(
            r"^(location|locations|job location|work location|workplace|remote location|remote)\s*[:\-–]?\s*",
            line,
            re.I,
        ):
            if re.fullmatch(r"locations?:?", line, re.I) and index + 1 < len(lines):
                line += ": " + lines[index + 1]
            location_lines.append(line)
    snippets = location_lines + [url_text]
    for snippet in snippets:
        lowered = snippet.lower()
        if any(marker in lowered for marker in NON_US_MARKERS) or re.search(r"\bUK\b", snippet):
            return {"status": "outside_us_scope", "evidence": snippet[:240]}
    for snippet in snippets:
        lowered = snippet.lower()
        has_us = bool(
            re.search(
                r"\b(united states|u\.s\.|usa|us only|within the us)\b|(?:,|:)\s*US(?:\b|$)",
                snippet,
                re.I,
            )
        )
        if snippet in location_lines:
            has_us = has_us or bool(re.search(r"\bUS\b", snippet))
        has_state = any(re.search(r"\b" + re.escape(state) + r"\b", lowered) for state in US_STATES)
        if snippet in location_lines:
            # Two-letter abbreviations need a city/state separator and uppercase text.
            has_state = has_state or any(
                re.search(r",\s*" + abbreviation + r"\b", snippet)
                for abbreviation in US_STATE_ABBREVIATIONS
            )
        if has_us or has_state:
            remote = "remote" in lowered or "remote" in url_text
            return {
                "status": "remote_us_eligible" if remote and has_us else "us_location",
                "evidence": snippet[:240],
            }
    return {
        "status": "unknown",
        "evidence": "no posting-specific US location or US-remote evidence",
    }


def role_level(title: str) -> str:
    # Compound titles retain their highest explicit level (Senior Associate is senior).
    for level, pattern in (
        ("executive", r"\b(vp|vice president|chief)\b"),
        ("management", r"\b(manager|director|head)\b"),
        ("senior", r"\b(senior|sr\.?|lead|principal|staff|iii)\b"),
        ("entry", r"\b(entry|junior|associate|analyst i|analyst 1)\b"),
        ("mid", r"\b(mid|ii|2)\b"),
    ):
        if re.search(pattern, title, re.I):
            return level
    return "unknown"


def classify_role(title: str, text: str, config: dict[str, Any]) -> dict[str, Any]:
    title_lower = title.lower()
    material = (title + " " + text).lower()
    specific_material = material
    if "capital one" in material and re.search(r"business analyst", title_lower):
        # These standard template lines occur in HR/product/marketing postings.
        # They cannot, by themselves, establish the posting's credit-risk function.
        specific_text = re.split(r"basic qualifications\s*:?", text, maxsplit=1, flags=re.I)[0]
        specific_text = re.sub(
            r"(?im)^.*credit risk:\s*support step.change improvements[^\n]*", "", specific_text
        )
        specific_text = re.sub(
            r"(?im)^.*strategic\s*&\s*analytic orientation:[^\n]*", "", specific_text
        )
        specific_material = (title + " " + specific_text).lower()
    roles = config.get("roles", {})
    levels = roles.get("entry_mid_levels", ["entry", "mid"])
    level = role_level(title)
    excluded = [
        str(term).lower()
        for term in roles.get("exclude", EXCLUDE_TERMS)
        if str(term).lower() in title_lower
        and not (str(term).lower() == "manager" and "management" in levels)
    ]
    if re.search(
        r"\b(software|machine learning|ml|platform|infrastructure)\s+(engineer|engineering|developer)\b",
        title_lower,
    ):
        excluded.append("engineering_or_ml_title")
    if re.search(r"\b(auditor|audit|compliance|investigator)\b", title_lower):
        excluded.append("audit_compliance_or_investigation_function")
    if level != "unknown" and level not in levels:
        excluded.append("seniority_outside_scope")
    if level == "unknown" and (
        roles.get("require_explicit_level") or set(levels) <= {"senior", "management", "executive"}
    ):
        excluded.append("seniority_unknown")

    # A quantitative degree/orientation is not a quantitative modeling function.
    credit_terms = TRACK_TERMS["credit_risk_analytics"] + (
        "credit loss",
        "credit reporting",
        "credit performance",
        "credit modeling",
        "credit modelling",
        "loss forecasting",
    )
    quant_terms = tuple(
        t for t in TRACK_TERMS["quantitative_credit_risk_capital_modeling"] if t != "quantitative"
    ) + (
        "credit modeling",
        "credit modelling",
        "quantitative modeling",
        "quantitative modelling",
    )
    quant_title = bool(
        re.search(
            r"quantitative|model validation|capital (?:model|analytic)|pricing model|credit model|risk model",
            title_lower,
        )
    )
    credit = [term for term in credit_terms if term in specific_material]
    fraud = [term for term in TRACK_TERMS["fraud_analytics_strategy"] if term in specific_material]
    quant = [term for term in quant_terms if term in specific_material]
    title_credit = [term for term in credit_terms if term in title_lower]
    title_fraud = [term for term in TRACK_TERMS["fraud_analytics_strategy"] if term in title_lower]
    matches = []
    if quant_title and (credit or quant):
        matches = ["quantitative_credit_risk_capital_modeling"]
    elif title_credit:
        matches = ["credit_risk_analytics"]
    elif title_fraud:
        matches = ["fraud_analytics_strategy"]
    else:
        if credit:
            matches.append("credit_risk_analytics")
        if fraud:
            matches.append("fraud_analytics_strategy")
        # Analysts whose work explicitly develops/validates credit models need review
        # unless their title identifies a quantitative function.
        if quant and not re.search(r"business analyst", title_lower):
            matches.append("quantitative_credit_risk_capital_modeling")
    matches = list(dict.fromkeys(matches))
    evidence = {
        "credit": credit,
        "fraud": fraud,
        "modeling": quant,
        "quantitative_title": quant_title,
    }
    result = {
        "eligible": False,
        "tracks": matches,
        "level": level,
        "level_evidence": title,
        "subtype": "unknown",
        "track_evidence": evidence,
        "classification_version": "title-context-v4",
    }
    if excluded:
        return {
            **result,
            "reason": "excluded_term",
            "matched_exclusions": excluded,
            "explanation": "Title excluded: " + ", ".join(excluded),
        }
    if not re.search(r"\b(analyst|analysis|quantitative|risk|model)\b", title_lower):
        return {
            **result,
            "reason": "title_not_role_family",
            "explanation": "Title does not identify an analytics, risk, or modeling function.",
        }
    if len(matches) != 1:
        if (
            specific_material != material
            and any(term in material for term in credit_terms)
            and not matches
        ):
            return {
                **result,
                "reason": "template_only_credit_context",
                "explanation": "Credit wording occurs only in standard business-analyst template/qualifications; no posting-specific credit-risk function established. Human disambiguation required.",
            }
        return {
            **result,
            "reason": "no_unique_track_match",
            "explanation": (
                "Multiple role tracks need human disambiguation."
                if matches
                else "No explicit credit, fraud, or risk/capital modeling context; quantitative qualifications alone are insufficient."
            ),
        }
    if matches[0] not in roles.get("tracks", list(TRACK_TERMS)):
        return {
            **result,
            "reason": "track_outside_scope",
            "explanation": "Classified as " + matches[0] + "; this run selected other tracks.",
        }
    subtype = "general"
    for needle, label in (
        ("model validation", "model_validation"),
        ("capital modeling", "capital_analytics"),
        ("capital analytics", "capital_analytics"),
        ("pricing model", "pricing_analytics"),
        ("fraud strategy", "fraud_strategy"),
        ("fraud analytics", "fraud_analytics"),
        ("credit policy", "credit_policy"),
        ("underwriting", "underwriting_analytics"),
        ("credit modeling", "credit_modeling"),
        ("credit loss", "credit_loss_forecasting"),
    ):
        if needle in material:
            subtype = label
            break
    return {
        **result,
        "eligible": True,
        "reason": "candidate",
        "subtype": subtype,
        "explanation": "Title and role context identify "
        + matches[0]
        + "; explicit level: "
        + level
        + ".",
    }
