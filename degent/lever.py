"""Lever public postings; small conventions adapted from Jobseek (MIT notice)."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

from .store import clean_html

PAGE_SIZE = 100
PARSER_VERSION = "lever-public-v1"


def api_base(source):
    token = str(source.get("board", ""))
    region = source.get("region", "global")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token) or region not in {"global", "eu"}:
        raise ValueError("Invalid Lever region/board")
    return f"https://api.{ 'eu.' if region == 'eu' else ''}lever.co/v0/postings/{quote(token)}"


def page(payload, source, known=(), limit=PAGE_SIZE):
    data = json.loads(payload)
    if not isinstance(data, list) or len(data) > limit:
        raise ValueError("invalid_lever_feed: expected bounded JSON list")
    ids, rows = set(known), []
    host = "jobs." + ("eu." if source.get("region") == "eu" else "") + "lever.co"
    for job in data:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
            raise ValueError("lever_missing_posting_id")
        if job["id"] in ids:
            raise ValueError("lever_duplicate_posting_id")
        ids.add(job["id"])
        url = job.get("hostedUrl", "")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path.rstrip("/") != f"/{source['board']}/{job['id']}"
        ):
            raise ValueError("lever_posting_url_mismatch")
        if not isinstance(job.get("text"), str) or not job["text"].strip():
            raise ValueError("lever_missing_title")
        sections = [job.get("description") or job.get("descriptionPlain") or ""]
        for section in job.get("lists") or []:
            if not isinstance(section, dict):
                raise ValueError("lever_invalid_description_section")
            sections += [section.get("text") or "", section.get("content") or ""]
        sections += [
            job.get("additional") or job.get("additionalPlain") or "",
            job.get("salaryDescription") or job.get("salaryDescriptionPlain") or "",
        ]
        cat = job.get("categories") or {}
        if not isinstance(cat, dict):
            raise ValueError("lever_invalid_categories")
        rows.append(
            {
                "posting_id": job["id"],
                "title": job["text"],
                "source_url": url,
                "text": clean_html("\n".join(sections).encode()),
                "location": "; ".join(cat.get("allLocations") or [cat.get("location") or ""]),
                "employment_type": cat.get("commitment"),
                "original_record": job,
                "stated_publication_at": None,
                "publication_date_source": None,
                "upstream_start_at": job.get("createdAt"),
                "upstream_start_date_source": (
                    "lever.createdAt (upstream creation assertion)"
                    if job.get("createdAt")
                    else None
                ),
                "stated_updated_at": job.get("updatedAt"),
                "update_date_source": "lever.updatedAt" if job.get("updatedAt") else None,
            }
        )
    return rows, len(data) < limit
