"""Exact, offline requisition evidence from preserved official posting HTML.

This only verifies source identity fields. It does not resolve employer conflicts,
adjudicate eligibility, merge postings, or alter imported metadata.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from .capital_one import CareerHTML, official_url
from .store import digest


def preserved_identity(original: dict) -> dict:
    path = Path(original["raw_source_file"])
    raw = path.read_bytes()
    if digest(raw) != original["raw_source_sha256"]:
        raise ValueError("Preserved identity source hash changed.")
    compressed = raw.startswith(b"\x1f\x8b")
    body = gzip.decompress(raw) if compressed else raw
    source = body.decode("utf-8")
    target = official_url(original["original_url"], job=True)
    matches = []
    for index, script in enumerate(CareerHTML(body).scripts):
        try:
            job = json.loads(script)
        except ValueError:
            continue
        if not isinstance(job, dict) or job.get("@type") != "JobPosting":
            continue
        declared = official_url(job.get("url", ""), job=True)
        if declared.rsplit("/", 1)[-1] != target.rsplit("/", 1)[-1]:
            continue
        identifier = job.get("identifier")
        req = identifier.get("value") if isinstance(identifier, dict) else identifier
        if not isinstance(req, str) or not re.fullmatch(r"R\d+(?:-\d+)?", req):
            raise ValueError("Missing exact source requisition.")
        if job.get("title") != original["title"]:
            raise ValueError("Preserved identity title conflicts with observation.")
        start = source.index(script)
        application = []
        for match in re.finditer(
            r'https://capitalone\.wd1\.myworkdayjobs\.com/Capital_One/job/[^"<> ]+/apply',
            source,
        ):
            if re.search(r"_" + re.escape(req) + r"(?:-\d+)?/apply$", match.group()):
                application.append(
                    {"start": match.start(), "end": match.end(), "text": match.group()}
                )
        matches.append(
            {
                "identity_method_sha256": digest(Path(__file__).read_bytes()),
                "raw_source_file": str(path),
                "raw_source_sha256": digest(raw),
                "decoded_source_sha256": digest(source),
                "encoding": "utf-8-strict",
                "decompression": "gzip" if compressed else "none",
                "offset_unit": "unicode_codepoints_in_decoded_source",
                "selector": f"script[type=application/ld+json][{index}] -> JobPosting",
                "source_span": {"start": start, "end": start + len(script), "text": script},
                "requisition_id": req,
                "original_url": original["original_url"],
                "source_url": job["url"],
                "url_match_basis": "official_job_site_id_and_exact_title_and_requisition",
                "title": job["title"],
                "hiring_organization_assertion": job.get("hiringOrganization"),
                "employer_conflict_resolved": False,
                "source_application_evidence": application,
            }
        )
    if len(matches) != 1:
        raise ValueError("Ambiguous or missing matching preserved JobPosting identity.")
    return matches[0]


def asserted_requisition(original: dict, assertion: dict) -> str | None:
    proof = assertion.get("raw_identity_evidence")
    imported = original.get("requisition_id")
    if proof is None:
        return imported
    if proof != preserved_identity(original):
        raise ValueError("Raw identity evidence differs from the preserved source.")
    if imported is not None and imported != proof["requisition_id"]:
        raise ValueError("Raw and imported requisitions conflict; preserve unresolved identity.")
    return proof["requisition_id"]
