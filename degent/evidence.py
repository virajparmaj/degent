"""Local source units and explicit dating for the comparison study."""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .store import digest

SELECTOR_VERSION = "source-units-v2"
KNOWLEDGE = re.compile(r"\b(analyst|analytics?|risk|fraud|credit|underwriting|quantitative|model(?:ing|ling)?|data|scientist|science|research|product manage(?:r|ment)|engineer(?:ing)?|developer|software|machine learning|artificial intelligence|AI|ML|cyber|technology)\b", re.I)
UNRELATED = re.compile(r"\b(branch ambassador|cafe ambassador|café ambassador|teller|customer service representative|customer care|concierge|retail banker|administrative assistant|recruiter)\b", re.I)
HEADINGS = (
    ("qualification", re.compile(r"^(?:basic|minimum|preferred|desired|essential|additional)?\s*(?:qualifications?|requirements?|skills?|experience)(?:\s+(?:and|&)\s+(?:skills|experience|qualifications))?\s*:?$", re.I)),
    ("qualification", re.compile(r"^(?:what (?:you.ll|you will|we) (?:need|bring|look for)|who you are|nice to have|a plus)\s*:?$", re.I)),
    ("responsibility", re.compile(r"^(?:(?:general|key|specific|your)\s+)?(?:responsibilities|duties)(?:\s+include[^:]*)?\s*:?$|^(?:what you.ll do|what you will do|about the role|job description|the role|role summary|summary)\s*:?$", re.I)),
    ("company_expectation", re.compile(r"^(?:our culture|our values|how we work|who thrives here)\s*:?$", re.I)),
    ("company_description", re.compile(r"^(?:about us|about capital one|benefits|compensation|salary|equal opportunity|legal|privacy)\s*:?$", re.I)),
    ("hiring_process", re.compile(r"^(?:hiring process|application process|recruiting process)\s*:?$", re.I)),
)
# Only explicit template paragraphs are removed, not arbitrary repeated text.
BOILERPLATE = re.compile(r"^(?:The minimum and maximum full.time annual salaries|Candidates hired to work in other locations|Capital One offers a comprehensive|No agencies please\.|If you have visited our website in search|For technical support or questions about Capital One.s recruiting|Capital One does not provide, endorse|Capital One will consider for employment qualified applicants|Capital One is an equal opportunity employer|All qualified applicants will receive consideration|Capital One promotes a drug.free workplace|Capital One is a federally registered service mark|Capital One Financial is made up of several|Please note that any position posted in Canada|Capital One, (?:a Fortune 500|one of the)|We offer a broad spectrum of financial products|We nurture a work environment|(?:Learn more|Read more|Click here).{0,50}(?:benefits|privacy)|(?:Apply now|Save job|Share this job|Back to search|Related jobs)\s*$|https?://\S+\s*$)", re.I)


def knowledge_scope(title: str, text: str = "") -> dict:
    if KNOWLEDGE.search(title):
        return {"decision": "in_scope", "reason": "knowledge-role title; confirm duties", "evidence": title}
    if UNRELATED.search(title):
        return {"decision": "out_of_scope", "reason": "explicit unrelated retail/service/administrative function", "evidence": title}
    # Missing information is a retrieval/review candidate, never a title rejection.
    if text and KNOWLEDGE.search(text):
        return {"decision": "ambiguous", "reason": "knowledge duties need human scope review", "evidence": KNOWLEDGE.search(text).group()}
    return {"decision": "ambiguous", "reason": "unfamiliar title; retrieve and review duties", "evidence": title}


def readable(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "empty_description"
    if re.search(r"enable javascript|checking your browser|consent required|this job is no longer available|position has been filled", text, re.I):
        return False, "shell_or_closed_description"
    return True, "readable_description"


def select_units(text: str, source_hash: str | None = None) -> dict:
    """Offsets refer to preserved text, including its original whitespace."""
    source_hash = source_hash or digest(text)
    lines = text.splitlines()
    metadata_prefix = len(lines) >= 3 and lines[2].startswith("Location:")
    units, scope, heading, offset = [], "unknown", None, 0
    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        start = offset + len(raw) - len(raw.lstrip())
        end = offset + len(raw.rstrip())
        offset += len(raw)
        if not stripped:
            continue
        is_heading = False
        for kind, pattern in HEADINGS:
            if pattern.search(stripped):
                scope, heading, is_heading = kind, stripped, True
                break
        boilerplate = bool(BOILERPLATE.search(stripped))
        # Exclude only explicit benefit/pay/navigation lines. Unknown prose stays for review.
        compensation = bool(re.search(r"(?:401\s*\(?k\)?|health.{0,20}(?:benefits|insurance)|paid time off|annual (?:base )?salar|hourly (?:pay|rate)|^[\w .,/()_-]{1,100}:\s*(?:\$|USD)\s*\d)", stripped, re.I))
        expectation = bool(re.search(r"\b(?:must|required|preferred|experience|proficien\w*|ability|you will|you.ll|responsib\w*|duties|sponsor|authorization|willing|eligible to work|build\w*|design\w*|develop\w*|deliver\w*|lead\w*|manag\w*|analy\w*|collaborat\w*|partner\w*|hold|accountab\w*|ensure|drive|support|perform|maintain)\b", stripped, re.I))
        boilerplate = boilerplate or compensation and not expectation
        introduction = bool(re.match(r"(?:Capital One is a high.tech company|Data is at the center of everything we do\. As a startup|We believe our portfolio of businesses)", stripped, re.I))
        direct_expectation = bool(re.search(r"\b(?:you|your|must|qualifications|we are seeking|this role|the candidate)\b", stripped, re.I))
        if introduction and not direct_expectation:
            boilerplate = True
        if direct_expectation and re.match(r"Capital One, (?:a Fortune 500|one of the)|Capital One is a high.tech company", stripped, re.I):
            boilerplate = False  # Mixed introduction/expectation paragraphs must survive intact.
        selected = not boilerplate and scope != "company_description"
        if scope == "company_description" and heading and re.fullmatch(r"about (?:us|capital one)\s*:?", heading, re.I) and not is_heading:
            selected = not boilerplate  # Expand unfamiliar role prose under an introduction.
        if expectation and not boilerplate:
            selected = True  # An explicit employer expectation survives a broad heading.
        metadata = metadata_prefix and len(units) < 3
        selected = selected and not metadata
        # A notice may mention a tool; preserve its separate context, not a qualification.
        if not is_heading and re.search(r"(?:screen|process|review).{0,40}(?:application|candidate)", stripped, re.I) and re.search(r"\b(?:hiring|recruit(?:ing|ment)?|candidates?|job applications?)\b", stripped, re.I):
            unit_scope = "hiring_process"
        else:
            unit_scope = scope
        if selected and scope == "company_description":
            unit_scope = "unknown"  # Heading cannot turn explicit expectations into company copy.
        units.append({
            "unit_id": "u" + str(len(units)), "source_sha256": source_hash,
            "start": start, "end": end, "text": text[start:end],
            "section": heading, "statement_scope": unit_scope,
            "heading": is_heading, "selected": selected,
            "selection_reason": "posting_metadata" if metadata else "explicit_boilerplate" if boilerplate else "company_description" if not selected else "context_and_requirements",
        })
    chosen = [u for u in units if u["selected"]]
    return {
        "selector_version": SELECTOR_VERSION, "source_sha256": source_hash,
        "units": units, "selected_unit_ids": [u["unit_id"] for u in chosen],
        "selected_characters": sum(len(u["text"]) for u in chosen),
        "full_characters": len(text),
        "selection_status": "structured" if any(u["heading"] and u["statement_scope"] == "qualification" for u in chosen) else "expanded_unknown_structure",
        "coverage_claim": "selection recall requires full-text gold labels",
    }


def role_profile(selection: dict, title: str) -> dict:
    # No skill-vector representation is used to define the roles being compared.
    duties = [u for u in selection["units"] if u["selected"] and not u["heading"]
              and u["statement_scope"] in {"responsibility", "unknown"} and u["text"] != title]
    return {"title": title, "text": title + "\n" + "\n".join(u["text"] for u in duties),
            "evidence_units": [u["unit_id"] for u in duties],
            "source_sha256": selection["source_sha256"], "version": SELECTOR_VERSION}


def date_assertion(value, provenance, timezone="America/Chicago") -> dict:
    result = {"raw": value, "provenance": provenance, "date": None, "uncertainty": "missing"}
    if not isinstance(value, str) or not value.strip():
        return result
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            result.update(date=date.fromisoformat(value).isoformat(), uncertainty="source_date_only")
        else:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                result["uncertainty"] = "timezone_ambiguous"
            else:
                result.update(date=stamp.astimezone(ZoneInfo(timezone)).date().isoformat(), uncertainty="source_timestamp")
    except ValueError:
        result["uncertainty"] = "invalid"
    return result


def period_for(day: str | None) -> tuple[str, str]:
    if not day:
        return "UNKNOWN", "UNKNOWN"
    d = date.fromisoformat(day)
    if d.year < 2022:
        return "PRE-2022", "PRE-2022"
    if d.year < 2025:
        return f"{d.year}-H{1 if d.month <= 6 else 2}", "2022–2024"
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}", "2025–2026" if d.year <= 2026 else str(d.year)


def period_definitions(as_of: str) -> list[dict]:
    today = date.fromisoformat(as_of)
    result = [{"id": "PRE-2022", "start": None, "end": "2021-12-31", "kind": "completed"}]
    for year in range(2022, max(2026, today.year) + 1):
        step = 6 if year <= 2024 else 3
        for month in range(1, 13, step):
            endmonth = month + step - 1
            start = date(year, month, 1)
            end = date(year, endmonth, monthrange(year, endmonth)[1])
            result.append({"id": f"{year}-{'H' if step == 6 else 'Q'}{(month-1)//step+1}",
                           "start": start.isoformat(), "end": end.isoformat(),
                           "kind": "future" if start > today else "partial" if end > today else "completed"})
    result.append({"id": "UNKNOWN", "start": None, "end": None, "kind": "unknown"})
    return result


def decorate_observation(row: dict, config: dict, text: str) -> dict:
    from .classify import role_level
    from .metadata import metadata_assertions
    row.setdefault("collection_id", "current")
    row.setdefault("collection_kind", "current")
    metadata_assertions(row, text)
    usable, reason = readable(text)
    scope = knowledge_scope(row.get("title", ""), text)
    us_addresses = [a for a in row.get("structured_locations", []) if str(a.get("addressCountry", "")).casefold() in {"us", "usa", "united states", "united states of america"}]
    if us_addresses:
        row.update(geography_status="us_location", geography_evidence=str(us_addresses))
    publication = date_assertion(row.get("stated_publication_at"), row.get("publication_date_source"), config["timezone"])
    update = date_assertion(row.get("stated_updated_at"), row.get("update_date_source"), config["timezone"])
    pp, pe = period_for(publication["date"])
    cp, ce = period_for(row.get("capture_date"))
    if publication["date"] and row.get("capture_date") and publication["date"] > row["capture_date"]:
        publication["uncertainty"] = "publication_after_capture_needs_review"
        pp, pe = "UNKNOWN", "UNKNOWN"
    eligible = usable and scope["decision"] != "out_of_scope" and row.get("geography_status") in {"us_location", "remote_us_eligible"}
    inclusion = ("excluded" if not usable or scope["decision"] == "out_of_scope" or row.get("geography_status") == "outside_us_scope"
                 else "review" if scope["decision"] == "ambiguous" or not eligible else "included")
    raw_employment = row.get("employment_type_raw", row.get("employment_type"))
    employment_aliases = {"fulltime": "full-time", "parttime": "part-time", "contract": "contract",
                          "contractor": "contract", "temporary": "temporary", "intern": "internship",
                          "internship": "internship", "perdiem": "per-diem", "volunteer": "volunteer"}
    employment = employment_aliases.get(re.sub(r"[ _-]", "", raw_employment).casefold(), "unknown") if isinstance(raw_employment, str) else "unknown"
    row.update(
        study_version=1, classification_version="knowledge-scope-v1", scope_decision=scope, inclusion_decision=inclusion,
        inclusion_reason=reason if not usable else "outside US scope" if row.get("geography_status") not in {"us_location", "remote_us_eligible"} else scope["reason"],
        eligible=eligible, eligible_for_primary=eligible and bool(row.get("capture_date")),
        description_readable=usable, description_completeness="unreviewed",
        body_text_complete=False, body_complete=eligible,
        body_status=reason if not usable else "scope_review" if scope["decision"] == "ambiguous" else row.get("geography_status"),
        level="internship" if re.search(r"\bintern(?:ship)?\b", row.get("title", ""), re.I) else role_level(row.get("title", "")), role_track="unassigned", role_subtype="unassigned",
        role_group_id=None, taxonomy_version=None,
        seniority_evidence={"method": "title_classifier", "quote": row.get("title", ""), "review_status": "unreviewed"},
        publication_date_evidence=publication, update_date_evidence=update,
        publication_period_id=pp, publication_era=pe, capture_period_id=cp, capture_era=ce,
        wording_validity={"observed_at": row.get("current_capture_at"), "valid_from": None, "valid_through": None,
                          "evidence": "wording captured at observation; original publication wording unproven"},
        structured_locations=(row.get("structured_locations") or []),
        first_observation_basis="collector index" if row.get("collector_first_observed_at") else "earliest preserved study observation",
        brand_id=row.get("brand_id") or row["employer_id"] + ":primary-brand",
        employment_type_raw=raw_employment, employment_type=employment,
        employment_type_evidence={"raw": raw_employment, "provenance": "provider employmentType assertion",
                                  "uncertainty": "recognized_source_category" if employment != "unknown" else "missing_or_unrecognized_source_category"},
    )
    row.setdefault("content_version_id", row["document_id"])
    row.setdefault("collection_id", "current")
    row.setdefault("collection_kind", "current")
    row.setdefault("identity_status", "confirmed_source_identity")
    if row["collection_kind"] == "historical":
        # Retrieval is modern; only authenticated archive observations date historical wording.
        historical = row["historical_source"]
        wording = date_assertion(row.get("verified_historical_wording_at"), historical.get("wording_valid_at_provenance"), config["timezone"])
        wp, we = period_for(wording["date"])
        row.update(description_completeness=historical["description_completeness"],
                   body_text_complete=historical["description_completeness"] == "complete",
                   wording_date_evidence=wording, wording_period_id=wp, wording_era=we,
                   wording_validity={"observed_at": row.get("verified_historical_wording_at"),
                                     "valid_from": None, "valid_through": None,
                                     "evidence": historical.get("wording_valid_at_provenance"),
                                     "historical_eligible": bool(wording["date"])},
                   eligible=usable and bool(row.get("comparison_subset")),
                   eligible_for_primary=False,
                   inclusion_decision="review" if row.get("comparison_subset") else "preserved_outside_focused_subset")
        if publication["date"] and wording["date"] and publication["date"] > wording["date"]:
            publication["uncertainty"] = "publication_after_verified_wording_needs_review"
            row.update(publication_period_id="UNKNOWN", publication_era="UNKNOWN")
    else:
        row["wording_period_id"], row["wording_era"] = cp, ce
        row["verified_historical_wording_at"] = None
    return row
