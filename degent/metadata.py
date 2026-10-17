"""Source-backed metadata assertions, with explicit unresolved boundaries."""
from __future__ import annotations

import re

US = {"us", "usa", "united states", "united states of america"}


def metadata_assertions(row: dict, text: str) -> None:
    """Derive from posting headers, never from pay/EEO city or country mentions."""
    header = next((line.strip() for line in text.splitlines()[:4]
                   if "United States of America," in line), None)
    locations = row.get("locations_raw", row.get("structured_locations", []))
    assertions = []
    if header:
        start = text.index(header)
        parts = header.split("United States of America,", 1)[1].strip().split(",")
        if len(parts) == 2 and all(p.strip() for p in parts):
            address = {"addressLocality": parts[0].strip(), "addressRegion": parts[1].strip(),
                       "addressCountry": "United States"}
            assertions.append({"kind": "description_header", "value": address,
                               "start": start, "end": start + len(header), "quote": header})
            row.update(structured_locations=[address], location=header, geography_status="us_location",
                       geography_evidence=header)
    if not assertions:
        for location in locations:
            address = location.get("address", location) if isinstance(location, dict) else None
            if address and address.get("addressCountry", "").casefold() in US:
                assertions.append({"kind": "source_structured_location", "value": address,
                                   "provenance": "preserved source location assertion"})
        if assertions:
            row.update(structured_locations=[a["value"] for a in assertions], geography_status="us_location")
    invalid = any(isinstance(x, str) and x.strip().casefold() == "add" for x in locations)
    legacy = any(isinstance(x, str) for x in locations)
    if row.get("collection_kind") == "historical" and legacy and not assertions:
        row.update(location=None, structured_locations=[], geography_status="unknown",
                   geography_evidence="Legacy location assertion needs source review")
    raw_strings = [x for x in locations if isinstance(x, str)]
    conflicts = bool(header and raw_strings and not invalid
                     and any(x.split(",")[0].strip().casefold() not in header.casefold() for x in raw_strings))
    row["location_assertions"] = assertions
    row["location_review"] = ("corrected_from_exact_header" if invalid and assertions
                              else "header_supersedes_conflicting_legacy_assertion" if conflicts
                              else "unknown_invalid_source_location" if invalid else "source_assertion_unreviewed")
    row["remote_eligibility"] = "unknown"
    remote = [line.strip() for line in text.splitlines()
              if re.search(r"remote.eligible|remote work|work remotely|remote position", line, re.I)]
    row["remote_evidence"] = remote
    if remote:
        row["remote_eligibility"] = "source_mentions_remote_conditions_unreviewed"
    row["metadata_review_status"] = "human_role_seniority_employment_geography_review_pending"
