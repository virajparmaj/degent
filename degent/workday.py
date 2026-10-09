"""Workday CXS conventions adapted from Jobseek (MIT; notices/Jobseek-MIT.txt)."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .store import clean_html

PAGE_SIZE = 20


def api_base(source: dict) -> str:
    for key in ("company", "site", "wd_instance"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(source.get(key, ""))):
            raise ValueError("Invalid Workday tenant/site configuration.")
    if not re.fullmatch(r"wd\d+", source["wd_instance"]):
        raise ValueError("Invalid Workday instance.")
    return f"https://{source['company']}.{source['wd_instance']}.myworkdayjobs.com/wday/cxs/{source['company']}/{source['site']}"


def page(payload: bytes, offset: int, known: list[str], total=None) -> tuple[list[dict], int, bool]:
    data = json.loads(payload)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("jobPostings"), list)
        or type(data.get("total")) is not int
        or data["total"] < 0
    ):
        raise ValueError("Malformed Workday inventory.")
    if total is not None and data["total"] != total:
        raise ValueError("Workday inventory changed during pagination; refresh for reconciliation.")
    rows = data["jobPostings"]
    paths = []
    for row in rows:
        path = row.get("externalPath") if isinstance(row, dict) else None
        if (
            not isinstance(path, str)
            or not path.startswith("/job/")
            or urlsplit(path).netloc
            or ".." in path
        ):
            raise ValueError("Workday row lacks a valid vacancy path.")
        paths.append(path)
    if len(set([*known, *paths])) != len(known) + len(paths):
        raise ValueError("Duplicate Workday pagination identities; incomplete inventory.")
    next_offset = offset + len(rows)
    done = next_offset >= data["total"]
    if (not rows and not done) or next_offset > data["total"] or len(rows) > PAGE_SIZE:
        raise ValueError("Workday returned an incomplete or inconsistent page.")
    return rows, data["total"], done


def detail(payload: bytes, source: dict, path: str) -> dict:
    data = json.loads(payload)
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if (
        not isinstance(info, dict)
        or not info.get("jobReqId")
        or not info.get("title")
        or not info.get("jobDescription")
    ):
        raise ValueError("Workday detail lacks vacancy identity/title/description.")
    external = info.get("externalUrl") or ""
    host = f"{source['company']}.{source['wd_instance']}.myworkdayjobs.com"
    if external:
        parts = urlsplit(external)
        if parts.hostname != host or not parts.path.endswith(path):
            raise ValueError("Workday detail identifies a different source/vacancy.")
    req = str(info["jobReqId"])
    # Requisition is an assertion, not the provider posting identity.
    if not re.search("_" + re.escape(req) + r"(?:-\d+)?$", path.rsplit("/", 1)[-1]):
        raise ValueError("Workday path and requisition assertion disagree.")
    country = info.get("country") or {}
    country = country.get("descriptor", "") if isinstance(country, dict) else str(country)
    locations = [info.get("location"), *(info.get("additionalLocations") or []), country]
    return {
        "posting_id": path,
        "requisition_id": req,
        "source_url": f"https://{host}/{source['site']}{path}",
        "title": info["title"],
        "text": clean_html(info["jobDescription"].encode()),
        "location": "; ".join(str(v) for v in locations if v),
        "employment_type": info.get("timeType"),
        "stated_publication_at": None,
        "publication_date_source": None,
        "upstream_start_at": info.get("startDate"),
        "upstream_start_date_source": "workday.startDate (upstream assertion; not original publication)",
        "stated_updated_at": info.get("lastUpdated"),
        "update_date_source": "workday.lastUpdated" if info.get("lastUpdated") else None,
        "original_record": data,
    }
