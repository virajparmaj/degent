from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .archive import ArchiveClient, collections_for_period
from .classify import classify_geography, classify_role
from .compare import build_report
from .config import DEFAULT_CONFIG, employers, load_config, periods
from .extract import (
    codex_extract,
    codex_failure_status,
    extraction_cache_key,
    rule_candidates,
    validate_output,
)
from .store import (
    append_jsonl,
    body_usability,
    clean_html,
    digest,
    operation_lock,
    read_jsonl,
    read_json,
    safe_id,
    utc_now,
    write_json,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parent.parent


def run_directory(config: dict[str, Any], run_id: str) -> Path:
    path = Path(config.get("run_dir", "runs")) / safe_id(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_for(run_dir: Path, run_id: str) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "run_id": run_id,
            "created_at": utc_now(),
            "status": "in_progress",
            "queries": [],
            "body_attempts": [],
            "counts": {},
        }
    return manifest


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    write_json(run_dir / "manifest.json", manifest)


def check_codex() -> dict[str, Any]:
    import subprocess

    try:
        version = subprocess.run(["codex", "--version"], text=True, capture_output=True, timeout=10)
        login = subprocess.run(
            ["codex", "login", "status"], text=True, capture_output=True, timeout=15
        )
        return {
            "installed": version.returncode == 0,
            "version": version.stdout.strip(),
            "login_status_exit": login.returncode,
            "login_status": (login.stdout + login.stderr).strip()[:500],
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"installed": False, "error": str(exc)}


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("study_version") == 1:
        return {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 11),
                "archives": {}, "archive_checks": "disabled for current-only study", "provider": (config.get("inference", {}).get("identity") or {}).get("provider", "pending"),
                "configured_employers": len(employers(config)), "configured_periods": len(periods(config)),
                "data_dir": config["data_dir"], "run_dir": config["run_dir"],
                "source_check": "frozen official source verification; live retrieval outcomes are in collection"}
    archives = config["archives"]
    client = ArchiveClient(
        archives["user_agent"],
        timeout=min(10, int(config["request_timeout_seconds"])),
        retries=0,
        delay=0,
    )
    paths = {}
    for name, url in (
        ("wayback", archives["wayback_cdx"] + "?" + "url=example.com&output=json"),
        ("commoncrawl_catalogue", archives["commoncrawl_catalogue"]),
    ):
        try:
            status, headers, body = client.request(url)
            paths[name] = {
                "status_code": status,
                "reachable": 200 <= status < 400,
                "bytes": len(body),
                "error": headers.get("error"),
            }
        except Exception as exc:  # noqa: BLE001 - doctor reports diagnostic result
            paths[name] = {"reachable": False, "error": str(exc)}
    result = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "codex": check_codex(),
        "archives": paths,
        "firecrawl_cli_installed": shutil.which("firecrawl") is not None,
        "config": config["_path"],
        "configured_employers": len(employers(config)),
        "configured_periods": len(periods(config)),
        "data_dir": config["data_dir"],
        "run_dir": config["run_dir"],
    }
    return result


def discovery_queries(
    config: dict[str, Any], collections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    wayback_queries = []
    cc_groups: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    employer_list = [e for e in employers(config) if e.get("paths")]
    period_list = periods(config)
    for employer in employer_list:
        for period_index, period in enumerate(period_list):
            path = employer["paths"][period_index % len(employer["paths"])]
            wayback_queries.append(
                {
                    "source": "wayback",
                    "employer_id": employer["id"],
                    "period_id": period["id"],
                    "path": path,
                    "start": period["start"],
                    "end": period["end"],
                }
            )
            cc_groups.append(
                (
                    employer,
                    period,
                    collections_for_period(collections, period["start"], period["end"]),
                )
            )
    # Schedule in source rounds so a request cap reaches every employer and year
    # before spending requests on later Common Crawl collections or alternate paths.
    queries = []
    pair_order = [(period, employer) for period in period_list for employer in employer_list]
    by_pair = {(query["period_id"], query["employer_id"]): query for query in wayback_queries}
    collections_by_pair = {
        (period["id"], employer["id"]): matches for employer, period, matches in cc_groups
    }
    queries.extend(
        by_pair[(period["id"], employer["id"])]
        for period in period_list
        for employer in employer_list
    )
    maximum_collections = max((len(items) for _, _, items in cc_groups), default=0)
    for collection_rank in range(maximum_collections):
        for period, employer in pair_order:
            matches = collections_by_pair[(period["id"], employer["id"])]
            if collection_rank < len(matches):
                collection = matches[collection_rank]
                queries.append(
                    {
                        "source": "commoncrawl",
                        "employer_id": employer["id"],
                        "period_id": period["id"],
                        "path": employer["paths"][
                            period_list.index(period) % len(employer["paths"])
                        ],
                        "start": period["start"],
                        "end": period["end"],
                        "collection": collection.get("id") or collection.get("name"),
                    }
                )
            elif collection_rank == 0 and not matches:
                queries.append(
                    {
                        "source": "commoncrawl",
                        "employer_id": employer["id"],
                        "period_id": period["id"],
                        "path": employer["paths"][
                            period_list.index(period) % len(employer["paths"])
                        ],
                        "start": period["start"],
                        "end": period["end"],
                        "collection": None,
                    }
                )
    # Alternate ATS/career namespaces are last priority within the same bounded budget.
    for period_index, period in enumerate(period_list):
        for employer in employer_list:
            paths = employer["paths"]
            if len(paths) < 2:
                continue
            path = paths[(period_index + 1) % len(paths)]
            if path == paths[period_index % len(paths)]:
                continue
            matches = collections_for_period(collections, period["start"], period["end"])
            if matches:
                collection = matches[0]
                queries.append(
                    {
                        "source": "commoncrawl",
                        "employer_id": employer["id"],
                        "period_id": period["id"],
                        "path": path,
                        "start": period["start"],
                        "end": period["end"],
                        "collection": collection.get("id") or collection.get("name"),
                    }
                )
    return queries


def discover(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_dir = run_directory(config, run_id)
    manifest = manifest_for(run_dir, run_id)
    archive_config = config["archives"]
    client = ArchiveClient(
        archive_config["user_agent"],
        int(config["request_timeout_seconds"]),
        int(config["retry_count"]),
        float(config["host_delay_seconds"]),
    )
    request_cap = int(config["discovery_request_cap"])
    prior_requests = int(manifest.get("discovery_network_requests", 0))
    prior_events = list(manifest.get("discovery_request_events", []))
    client.request_limit = max(0, request_cap - prior_requests)

    def reserve_discovery(event):
        manifest["discovery_network_requests"] = prior_requests + client.request_count
        manifest["discovery_request_events"] = prior_events + client.request_events + [event]
        save_manifest(run_dir, manifest)

    client.on_request = reserve_discovery
    catalogue_path = run_dir / "commoncrawl-catalogue.json"
    if catalogue_path.exists():
        collections = json.loads(catalogue_path.read_text(encoding="utf-8"))
        catalogue_status = int(manifest.get("catalogue_status", 200))
    else:
        catalogue_status, collections = client.commoncrawl_catalogue(
            archive_config["commoncrawl_catalogue"]
        )
        manifest["discovery_network_requests"] = prior_requests + client.request_count
        manifest["discovery_request_events"] = prior_events + client.request_events
    if catalogue_status == 200:
        write_json(catalogue_path, collections)
    query_plan = discovery_queries(config, collections)
    old_results = {item["query_id"]: item for item in manifest["queries"] if item.get("query_id")}
    query_cap = int(config["discovery_request_cap"])
    result_by_id = {
        r.get("query_id"): r
        for r in read_jsonl(run_dir / "query-results.jsonl")
        if r.get("query_id")
    }
    # Persist each outcome immediately so interruption is resumable and capped.
    for query in query_plan:
        query_id = "|".join(
            str(query.get(k, ""))
            for k in ("source", "employer_id", "period_id", "path", "collection")
        )
        previous_result = result_by_id.get(query_id)
        if previous_result and not previous_result.get("resume_key"):
            continue
        if client.request_count >= client.request_limit:
            break
        employer = next(e for e in employers(config) if e["id"] == query["employer_id"])
        outcome: dict[str, Any] = {
            **query,
            "query_id": query_id,
            "queried_at": utc_now(),
            "status": "not_queried",
            "http_status": None,
            "records_found": 0,
        }
        event_start = len(client.request_events)
        if query["source"] == "wayback":
            row_records = []
            resume_key = previous_result.get("resume_key", "") if previous_result else ""
            total_found = int(previous_result.get("records_found", 0)) if previous_result else 0
            while True:
                if client.request_count >= client.request_limit:
                    outcome.update(
                        {
                            "status": "pagination_pending_request_cap",
                            "resume_key": resume_key,
                            "records_found": total_found,
                        }
                    )
                    break
                try:
                    status, rows, next_resume_key = client.wayback(
                        query["path"],
                        query["start"],
                        query["end"],
                        archive_config["wayback_cdx"],
                        resume_key,
                    )
                except RuntimeError as exc:
                    outcome.update(
                        {
                            "status": "rate_limited_suspend",
                            "suspend_reason": str(exc),
                            "resume_key": resume_key,
                        }
                    )
                    status, rows, next_resume_key = 0, [], resume_key
                total_found += len(rows)
                outcome.update(
                    {
                        "http_status": status,
                        "records_found": total_found,
                        "status": "ok" if status == 200 else "blocked_or_failed",
                        "resume_key": next_resume_key,
                    }
                )
                if outcome.get("suspend_reason"):
                    outcome["status"] = "rate_limited_suspend"
                for row in rows:
                    timestamp = str(row.get("timestamp", ""))
                    original = str(row.get("original", ""))
                    capture_date = (
                        f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
                        if len(timestamp) >= 8
                        else None
                    )
                    row_records.append(
                        {
                            "source": "wayback",
                            "employer_id": employer["id"],
                            "employer": employer["name"],
                            "business_group": employer["group"],
                            "period_id": query["period_id"],
                            "capture_at": timestamp,
                            "capture_date": capture_date,
                            "original_url": original,
                            "archive_url": "https://web.archive.org/web/"
                            + timestamp
                            + "id_/"
                            + original,
                            "status_code": row.get("statuscode"),
                            "digest": row.get("digest"),
                            "length": row.get("length"),
                            "source_record": row,
                        }
                    )
                if rows:
                    existing = {
                        (r.get("source"), r.get("capture_at"), r.get("original_url"))
                        for r in read_jsonl(run_dir / "archive-records.jsonl")
                    }
                    append_jsonl(
                        run_dir / "archive-records.jsonl",
                        [
                            r
                            for r in row_records
                            if (r.get("source"), r.get("capture_at"), r.get("original_url"))
                            not in existing
                        ],
                    )
                    row_records = []
                result_by_id[query_id] = outcome
                outcome["network_events"] = client.request_events[event_start:]
                write_jsonl(
                    run_dir / "query-results.jsonl",
                    sorted(result_by_id.values(), key=lambda row: row["query_id"]),
                )
                manifest["queries"] = list(result_by_id.values())
                manifest["discovery_network_requests"] = prior_requests + client.request_count
                manifest["discovery_requests_used"] = manifest["discovery_network_requests"]
                manifest["discovery_request_events"] = prior_events + client.request_events
                save_manifest(run_dir, manifest)
                if status != 200 or not next_resume_key:
                    break
                resume_key = next_resume_key
                outcome["resume_key"] = resume_key
        else:
            row_records = []
            if not query.get("collection"):
                outcome.update({"status": "no_intersecting_collection"})
            else:
                try:
                    status, rows = client.commoncrawl(
                        query["path"],
                        query["collection"],
                        archive_config["commoncrawl_index_template"],
                    )
                except RuntimeError as exc:
                    status, rows = 0, []
                    outcome["suspend_reason"] = str(exc)
                outcome.update(
                    {
                        "http_status": status,
                        "records_found": len(rows),
                        "status": "ok" if status == 200 else "blocked_or_failed",
                    }
                )
                if outcome.get("suspend_reason"):
                    outcome["status"] = "rate_limited_suspend"
                for row in rows:
                    timestamp = str(row.get("timestamp", ""))
                    capture_date = (
                        f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
                        if len(timestamp) >= 8
                        else None
                    )
                    locator = (
                        archive_config["commoncrawl_index_template"].format(
                            collection=query["collection"]
                        )
                        + "?"
                        + urllib.parse.urlencode({"url": row.get("url", ""), "output": "json"})
                    )
                    row_records.append(
                        {
                            "source": "commoncrawl",
                            "employer_id": employer["id"],
                            "employer": employer["name"],
                            "business_group": employer["group"],
                            "period_id": query["period_id"],
                            "capture_at": timestamp,
                            "capture_date": capture_date,
                            "original_url": row.get("url"),
                            "archive_url": locator,
                            "archive_collection": query["collection"],
                            "digest": row.get("digest"),
                            "length": row.get("length"),
                            "source_record": row,
                        }
                    )
        existing = {
            (r.get("source"), r.get("capture_at"), r.get("original_url"))
            for r in read_jsonl(run_dir / "archive-records.jsonl")
        }
        fresh = [
            r
            for r in row_records
            if (r.get("source"), r.get("capture_at"), r.get("original_url")) not in existing
        ]
        append_jsonl(run_dir / "archive-records.jsonl", fresh)
        result_by_id[query_id] = outcome
        outcome["network_events"] = client.request_events[event_start:]
        append_jsonl(run_dir / "query-results.jsonl", [outcome])
        manifest["queries"] = list(result_by_id.values())
        manifest["discovery_network_requests"] = prior_requests + client.request_count
        manifest["discovery_requests_used"] = manifest["discovery_network_requests"]
        manifest["discovery_request_events"] = prior_events + client.request_events
        manifest["status"] = "in_progress"
        save_manifest(run_dir, manifest)
        if outcome.get("status") == "rate_limited_suspend":
            manifest["status"] = "suspended_rate_limit"
            save_manifest(run_dir, manifest)
            break
    manifest["counts"]["archive_records"] = len(read_jsonl(run_dir / "archive-records.jsonl"))
    manifest["counts"]["queries"] = len(result_by_id)
    manifest["catalogue_status"] = catalogue_status
    manifest["coverage"] = _discovery_coverage(config, result_by_id, query_plan)
    completed_ids = {
        "|".join(
            str(query.get(k, ""))
            for k in ("source", "employer_id", "period_id", "path", "collection")
        )
        for query in query_plan
    }
    pagination_pending = any(
        result_by_id.get(query_id, {}).get("resume_key")
        or result_by_id.get(query_id, {}).get("status") == "pagination_pending_request_cap"
        for query_id in completed_ids
    )
    if completed_ids.issubset(result_by_id) and not pagination_pending:
        manifest["status"] = "complete"
    elif manifest.get("status") != "suspended_rate_limit":
        manifest["status"] = "in_progress"
    # Collapse any duplicate checkpoint lines from an interrupted overlapping invocation.
    write_jsonl(
        run_dir / "query-results.jsonl",
        sorted(result_by_id.values(), key=lambda row: row["query_id"]),
    )
    archive_records = read_jsonl(run_dir / "archive-records.jsonl")
    deduplicated_records: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for record in archive_records:
        key = (record.get("source"), record.get("capture_at"), record.get("original_url"))
        deduplicated_records.setdefault(key, record)
    write_jsonl(run_dir / "archive-records.jsonl", deduplicated_records.values())
    manifest["counts"]["archive_records"] = len(deduplicated_records)
    save_manifest(run_dir, manifest)
    write_json(run_dir / "coverage-matrix.json", manifest["coverage"])
    return {
        "run_dir": str(run_dir),
        "status": manifest["status"],
        "queries": len(result_by_id),
        "planned_queries": len(query_plan),
        "records_total": manifest["counts"]["archive_records"],
        "discovery_requests_used": manifest.get("discovery_requests_used", 0),
        "coverage_status_counts": dict(Counter(row["status"] for row in manifest["coverage"])),
        "coverage_matrix": str(run_dir / "coverage-matrix.json"),
    }


def _discovery_coverage(
    config: dict[str, Any], outcomes: dict[str, dict[str, Any]], query_plan: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for employer in employers(config):
        for period in periods(config):
            relevant = [
                o
                for o in outcomes.values()
                if o.get("employer_id") == employer["id"] and o.get("period_id") == period["id"]
            ]
            expected = sum(
                1
                for q in query_plan
                if q.get("employer_id") == employer["id"] and q.get("period_id") == period["id"]
            )
            inventory_status = (
                "complete"
                if len(relevant) >= expected
                else "partially_inspected" if relevant else "uninspected"
            )
            if not relevant:
                status = "uninspected"
            elif any(o.get("records_found", 0) for o in relevant):
                status = "records_found"
            elif any(
                o.get("status") in {"blocked_or_failed", "rate_limited_suspend"} for o in relevant
            ):
                status = "blocked"
            elif any(o.get("status") == "no_intersecting_collection" for o in relevant):
                status = "partial_no_collection"
            else:
                status = "inspected_empty"
            rows.append(
                {
                    "employer_id": employer["id"],
                    "period_id": period["id"],
                    "period_kind": period["kind"],
                    "queries_completed": len(relevant),
                    "expected_queries": expected,
                    "inventory_status": inventory_status,
                    "blocked_queries": sum(
                        o.get("status") in {"blocked_or_failed", "rate_limited_suspend"}
                        for o in relevant
                    ),
                    "records_found": sum(o.get("records_found", 0) for o in relevant),
                    "status": status,
                }
            )
    return rows


def _title_from_body(payload: bytes, fallback_url: str) -> str:
    from html import unescape
    from html.parser import HTMLParser

    from .store import html_payload

    payload = html_payload(payload)

    class Metadata(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.meta: dict[str, str] = {}
            self.headings: list[tuple[str, str]] = []
            self.heading_tag: str | None = None
            self.heading_attrs: dict[str, str] = {}
            self.heading_text: list[str] = []
            self.script_type = ""
            self.script_text: list[str] = []
            self.json_ld: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attributes = {key.lower(): value or "" for key, value in attrs}
            if tag.lower() == "meta":
                key = (
                    attributes.get("property")
                    or attributes.get("name")
                    or attributes.get("itemprop")
                    or ""
                ).lower()
                value = attributes.get("content", "").strip()
                if key and value:
                    self.meta[key] = value
            elif tag.lower() in {"h1", "h2"} and self.heading_tag is None:
                self.heading_tag = tag.lower()
                self.heading_attrs = attributes
                self.heading_text = []
            elif tag.lower() == "script":
                self.script_type = attributes.get("type", "").lower()
                self.script_text = []

        def handle_endtag(self, tag: str) -> None:
            if self.heading_tag == tag.lower():
                value = " ".join(" ".join(self.heading_text).split())
                self.headings.append(
                    (
                        value,
                        self.heading_attrs.get("class", "")
                        + " "
                        + self.heading_attrs.get("id", ""),
                    )
                )
                self.heading_tag = None
            elif tag.lower() == "script":
                if "ld+json" in self.script_type and self.script_text:
                    self.json_ld.append("".join(self.script_text))
                self.script_type = ""

        def handle_data(self, data: str) -> None:
            if self.heading_tag:
                self.heading_text.append(data)
            if self.script_type:
                self.script_text.append(data)

    parser = Metadata()
    parser.feed(payload.decode("utf-8", errors="replace"))

    def normalized(value: Any) -> str:
        return " ".join(unescape(str(value)).split()).strip()

    def job_titles(node: Any) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            kind = node.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(value).lower() == "jobposting" for value in kinds) and node.get("title"):
                found.append(normalized(node["title"]))
            for value in node.values():
                found.extend(job_titles(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(job_titles(value))
        return found

    for raw in parser.json_ld:
        try:
            candidates = job_titles(json.loads(raw))
        except json.JSONDecodeError:
            candidates = []
        if candidates:
            return candidates[0]
    for key in ("og:title", "twitter:title", "title"):
        value = normalized(parser.meta.get(key, ""))
        if value:
            return value.split(" | ")[0].strip()
    for value, attributes in parser.headings:
        if value and not re.search(r"logo|navigation|menu", attributes, re.I):
            return value
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", payload.decode("utf-8", errors="replace"), re.I | re.S
    )
    if title_match:
        value = normalized(re.sub(r"<[^>]+>", "", title_match.group(1)))
        if value:
            return value.split("|")[0].split(" - ")[0].strip()
    slug = fallback_url.rstrip("/").split("/")[-1].split("?", 1)[0]
    slug = re.sub(r"[-_/]+\d+$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip() or fallback_url


def canonical_vacancy_url(url: str) -> str:
    """Drop tracking parameters and apply mirrors while preserving the posting path."""
    parsed = urllib.parse.urlsplit(url.strip())
    path = re.sub(r"/(?:apply|application)/?$", "", parsed.path.rstrip("/"), flags=re.I)
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/"), "", "")
    )


def vacancy_identity(
    employer_id: str, url: str, source_record: dict[str, Any] | None = None
) -> tuple[str, str | None, str]:
    """Return a source-scoped requisition identity, verified posting id, and canonical URL."""
    record = source_record or {}
    canonical_url = canonical_vacancy_url(url)
    explicit = next(
        (
            str(record[key]).strip()
            for key in ("job_id", "jobId", "requisition_id", "requisitionId", "posting_id")
            if record.get(key) not in (None, "")
        ),
        None,
    )
    if explicit:
        return f"{employer_id}:source-id:{explicit}", explicit, canonical_url
    path = urllib.parse.urlsplit(canonical_url).path
    patterns = (
        r"/job/[^/]+/[^/]+/\d+/(\d+)(?:/|$)",
        r"/jobs/(\d+)(?:/|$)",
        r"/listing/[^/]+/(\d+)(?:/|$)",
        r"/careers/job/(\d+)(?:-|/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, path, re.I)
        if match:
            posting_id = match.group(1)
            return f"{employer_id}:posting-id:{posting_id}", posting_id, canonical_url
    return f"{employer_id}:url:{canonical_url}", None, canonical_url


def select_fetch_records(
    records: list[dict[str, Any]],
    employers_config: list[dict[str, Any]],
    limit: int = 150,
    per_employer_caps: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    selected_by_employer: dict[str, list[dict[str, Any]]] = {}
    strong_terms = (
        "fraud-analytics",
        "fraud-strategy",
        "credit-risk-analyst",
        "credit-strategy-analyst",
        "quantitative-analyst",
        "model-validation",
        "capital-model",
        "capital-analytics",
        "risk-model",
    )
    broad_terms = (
        "fraud",
        "credit",
        "risk",
        "quant",
        "capital",
        "model",
        "analytics",
        "underwrit",
        "pricing",
    )
    per_employer_caps = per_employer_caps or {}
    for employer in employers_config:
        rows = [
            r
            for r in records
            if r.get("employer_id") == employer["id"]
            and (
                r.get("source") != "commoncrawl"
                or str(r.get("source_record", {}).get("status", r.get("status_code", ""))) == "200"
            )
        ]
        cap = per_employer_caps.get(employer["id"], 25)
        per_year_cap = min(5, cap // 4)
        pair_cap = max(0, cap - per_year_cap * 4)
        # Spread distinct URLs across years, then reserve remaining attempts for possible paired captures.
        employer_selected = []
        for year in ("2023", "2024", "2025", "2026"):
            year_rows = [r for r in rows if str(r.get("capture_date", "")).startswith(year)]
            unique_urls: dict[str, dict[str, Any]] = {}
            for row in sorted(
                year_rows,
                key=lambda r: (
                    r.get("capture_date", ""),
                    r.get("capture_at", ""),
                    r.get("original_url", ""),
                ),
                reverse=True,
            ):
                url = canonical_vacancy_url(str(row.get("original_url", "")))
                if url not in unique_urls:
                    unique_urls[url] = row
            candidates = list(unique_urls.values())

            def relevance(row: dict[str, Any]) -> tuple[int, str]:
                url = str(row.get("original_url", "")).lower()
                if any(
                    word in url
                    for word in (
                        "manager",
                        "director",
                        "engineer",
                        "developer",
                        "account-executive",
                        "legal",
                        "cyber",
                    )
                ):
                    return 3, url
                if any(word in url for word in strong_terms):
                    return 0, url
                if any(word in url for word in broad_terms):
                    return 1, url
                return 2, url

            candidates.sort(key=relevance)
            employer_selected.extend(candidates[:per_year_cap])
        selected_keys = {
            (r.get("source"), r.get("capture_at"), r.get("original_url")) for r in employer_selected
        }
        selected_periods: dict[str, set[str]] = defaultdict(set)
        for row in employer_selected:
            selected_periods[canonical_vacancy_url(str(row.get("original_url", "")))].add(
                str(row.get("period_id", ""))
            )
        pair_candidates = [
            r
            for r in rows
            if (r.get("source"), r.get("capture_at"), r.get("original_url")) not in selected_keys
            and str(r.get("period_id", ""))
            not in selected_periods.get(
                canonical_vacancy_url(str(r.get("original_url", ""))), set()
            )
        ]
        pair_candidates.sort(
            key=lambda r: (str(r.get("original_url", "")), str(r.get("capture_date", "")))
        )
        # A tracking URL or another archive provider is not another vacancy-period.
        paired = []
        for row in pair_candidates:
            url = canonical_vacancy_url(str(row.get("original_url", "")))
            period = str(row.get("period_id", ""))
            if period in selected_periods[url]:
                continue
            selected_periods[url].add(period)
            paired.append(row)
            if len(paired) >= pair_cap:
                break
        employer_selected.extend(paired[:pair_cap])
        selected_by_employer[employer["id"]] = employer_selected
    unique = []
    seen = set()
    employer_ids = [employer["id"] for employer in employers_config]
    max_employer_rows = max((len(rows) for rows in selected_by_employer.values()), default=0)
    for row_index in range(max_employer_rows):
        for employer_id in employer_ids:
            rows = selected_by_employer.get(employer_id, [])
            if row_index >= len(rows):
                continue
            row = rows[row_index]
            key = (row.get("source"), row.get("capture_at"), row.get("original_url"))
            if key not in seen:
                seen.add(key)
                unique.append(row)
                if len(unique) >= limit:
                    return unique
    return unique


def fetch(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_dir = run_directory(config, run_id)
    manifest = manifest_for(run_dir, run_id)
    archive_config = config["archives"]
    client = ArchiveClient(
        archive_config["user_agent"],
        int(config["request_timeout_seconds"]),
        int(config["retry_count"]),
        float(config["host_delay_seconds"]),
    )
    records = read_jsonl(run_dir / "archive-records.jsonl")
    if "body_budget_reallocation" not in manifest:
        attempts_by_employer = Counter(
            item.get("employer_id") for item in manifest.get("body_attempts", [])
        )
        selected_baseline = select_fetch_records(
            records, employers(config), int(config["body_attempt_cap"])
        )
        available_selected = Counter(item.get("employer_id") for item in selected_baseline)
        expanded_caps = {
            employer["id"]: int(config["body_attempt_cap"]) for employer in employers(config)
        }
        expanded_pool = select_fetch_records(
            records,
            employers(config),
            int(config["body_attempt_cap"]) * len(employers(config)),
            expanded_caps,
        )
        expanded_available = Counter(item.get("employer_id") for item in expanded_pool)
        unused = {}
        for employer in employers(config):
            employer_id = employer["id"]
            baseline_n = min(
                int(config["per_employer_body_attempts"]), available_selected[employer_id]
            )
            unused_n = max(0, int(config["per_employer_body_attempts"]) - baseline_n)
            if unused_n:
                reason = (
                    "no eligible indexed HTTP 200 candidates in the configured discovery sample"
                    if baseline_n == 0
                    else "the initial per-employer reservation exceeded the eligible unique candidates found"
                )
                unused[employer_id] = {
                    "reserved": int(config["per_employer_body_attempts"]),
                    "eligible_unique_candidates": baseline_n,
                    "already_attempted": attempts_by_employer[employer_id],
                    "unused": unused_n,
                    "reason": reason,
                }
        remaining_unallocated = sum(item["unused"] for item in unused.values())
        reallocated = {employer["id"]: 0 for employer in employers(config)}
        headroom = {
            employer["id"]: max(
                0, expanded_available[employer["id"]] - available_selected[employer["id"]]
            )
            for employer in employers(config)
        }
        while remaining_unallocated and any(headroom.values()):
            progressed = False
            for employer in employers(config):
                employer_id = employer["id"]
                if remaining_unallocated <= 0:
                    break
                if headroom[employer_id] > 0:
                    reallocated[employer_id] += 1
                    headroom[employer_id] -= 1
                    remaining_unallocated -= 1
                    progressed = True
            if not progressed:
                break
        manifest["body_budget_reallocation"] = {
            "recorded_at": utc_now(),
            "unused_initial_reservations": unused,
            "reassigned_attempts": reallocated,
            "unassigned_unused_attempts": remaining_unallocated,
            "reassignment_rationale": "Reassigned only after recording unused employer reservations; extra attempts were round-robin allocated to employers with additional unique HTTP 200 candidates in the bounded archive sample.",
        }
        save_manifest(run_dir, manifest)
    reassigned = manifest["body_budget_reallocation"].get("reassigned_attempts", {})
    per_employer_caps = {
        employer["id"]: int(config["per_employer_body_attempts"])
        + int(reassigned.get(employer["id"], 0))
        for employer in employers(config)
    }
    chosen = select_fetch_records(
        records, employers(config), int(config["body_attempt_cap"]), per_employer_caps
    )
    finished = {x.get("record_key") for x in manifest.get("body_attempts", [])}
    used_by_employer = Counter(
        item.get("employer_id") for item in manifest.get("body_attempts", [])
    )
    selected_with_budget = []
    for row in chosen:
        if len(selected_with_budget) + len(manifest.get("body_attempts", [])) >= int(
            config["body_attempt_cap"]
        ):
            break
        key = digest(
            json.dumps([row.get("source"), row.get("capture_at"), row.get("original_url")])
        )
        if key in finished or used_by_employer[row.get("employer_id")] >= per_employer_caps.get(
            row.get("employer_id"), int(config["per_employer_body_attempts"])
        ):
            continue
        selected_with_budget.append(row)
        used_by_employer[row.get("employer_id")] += 1
        if len(selected_with_budget) + len(manifest.get("body_attempts", [])) >= int(
            config["body_attempt_cap"]
        ):
            break
    chosen = selected_with_budget
    observations: list[dict[str, Any]] = []
    root = Path(config["data_dir"]) / safe_id(run_id)
    suspended = False
    for row in chosen:
        record_key = digest(
            json.dumps([row.get("source"), row.get("capture_at"), row.get("original_url")])
        )
        if record_key in finished:
            continue
        employer = next(e for e in employers(config) if e["id"] == row["employer_id"])
        attempt = {
            "record_key": record_key,
            "attempted_at": utc_now(),
            "source": row["source"],
            "employer_id": row["employer_id"],
            "period_id": row.get("period_id"),
            "capture_at": row.get("capture_at"),
            "capture_date": row.get("capture_date"),
            "original_url": row.get("original_url"),
            "archive_url": row.get("archive_url"),
            "archive_collection": row.get("archive_collection"),
            "archive_record": row.get("source_record", {}),
            "status": "failed",
        }
        attempt["status"] = "reserved"
        manifest.setdefault("body_attempts", []).append(attempt)
        save_manifest(run_dir, manifest)
        event_start = len(client.request_events)
        try:
            if row["source"] == "wayback":
                status, headers, body, fetch_url = client.fetch_wayback(
                    str(row["capture_at"]), str(row["original_url"])
                )
            else:
                status, headers, body, fetch_url = client.fetch_commoncrawl(
                    row.get("source_record", {})
                )
        except RuntimeError as exc:
            status, headers, body, fetch_url = 0, {"error": str(exc)}, b"", ""
            attempt.update({"status": "suspended_rate_limit", "reason": str(exc)})
            suspended = True
        attempt.update({"http_status": status, "fetch_url": fetch_url})
        if suspended:
            pass
        elif status in (200, 206) and body:
            text = clean_html(body)
            title = _title_from_body(body, str(row.get("original_url", "")))
            usable, reason = body_usability(text, employer["name"], title=None)
            classification = classify_role(title, text, config)
            geography = classify_geography(str(row.get("original_url", "")), text)
            doc_id = digest(body)[:24]
            observation_id = digest(record_key + doc_id)[:24]
            raw_path = root / "raw" / (doc_id + ".bin")
            clean_path = root / "clean" / (doc_id + ".txt")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
            clean_path.parent.mkdir(parents=True, exist_ok=True)
            clean_path.write_text(text, encoding="utf-8")
            vacancy_id, source_posting_id, canonical_url = vacancy_identity(
                employer["id"], str(row.get("original_url", "")), row.get("source_record", {})
            )
            brand = (
                "Square"
                if employer["id"] == "block"
                and "square" in str(row.get("original_url", "")).lower()
                else employer["name"]
            )
            body_complete = bool(
                usable
                and classification["eligible"]
                and geography["status"] in {"us_location", "remote_us_eligible"}
            )
            body_status = (
                reason
                if not usable
                else (
                    classification["reason"]
                    if not classification["eligible"]
                    else geography["status"]
                )
            )
            observation = {
                "document_id": doc_id,
                "observation_id": observation_id,
                "vacancy_id": vacancy_id,
                "source_posting_id": source_posting_id,
                "canonical_url": canonical_url,
                "employer_id": employer["id"],
                "employer": employer["name"],
                "brand": brand,
                "business_group": employer["group"],
                "source": row["source"],
                "original_url": row.get("original_url"),
                "archive_url": row.get("archive_url"),
                "archive_collection": row.get("archive_collection"),
                "archive_record": row.get("source_record", {}),
                "period_id": row.get("period_id"),
                "capture_at": row.get("capture_at"),
                "capture_date": row.get("capture_date"),
                "stated_publication_at": None,
                "publication_date_source": None,
                "retrieved_at": utc_now(),
                "collector_first_observed_at": utc_now(),
                "raw_sha256": digest(body),
                "clean_sha256": digest(text),
                "parser_version": "html-text-v2",
                "title": title,
                "title_source": "archived_html_metadata_or_heading",
                "geography_status": geography["status"],
                "geography_evidence": geography["evidence"],
                "body_complete": body_complete,
                "body_status": body_status,
                "role_classification": classification,
                "role_track": classification["tracks"][0] if classification["eligible"] else None,
                "role_subtype": classification.get("subtype", "unknown"),
                "level": classification["level"],
                "eligible": classification["eligible"],
                "eligible_for_primary": body_complete,
                "raw_file": str(raw_path),
                "clean_file": str(clean_path),
            }
            observations.append(observation)
            attempt.update(
                {
                    "status": "usable" if observation["body_complete"] else "rejected",
                    "reason": observation["body_status"],
                    "document_id": doc_id,
                    "observation_id": observation_id,
                }
            )
        else:
            attempt.update(
                {
                    "status": "failed",
                    "reason": headers.get("error") or ("http_error" if status else "network_error"),
                }
            )
        attempt["network_events"] = client.request_events[event_start:]
        finished.add(record_key)
        if observations:
            append_jsonl(run_dir / "observations.jsonl", [observations.pop()])
        save_manifest(run_dir, manifest)
        if suspended:
            manifest["status"] = "suspended_rate_limit"
            save_manifest(run_dir, manifest)
            break
    manifest["counts"]["body_attempts"] = len(manifest["body_attempts"])
    manifest["counts"]["observations"] = len(read_jsonl(run_dir / "observations.jsonl"))
    save_manifest(run_dir, manifest)
    return {
        "run_dir": str(run_dir),
        "attempts": len(manifest["body_attempts"]),
        "observations": manifest["counts"]["observations"],
    }


def extract(config: dict[str, Any], run_id: str, use_codex: bool = False) -> dict[str, Any]:
    if config.get("study_version") == 1:
        from .study import extract as study_extract
        return study_extract(config, run_id)
    from .extraction_run import extract_run

    return extract_run(config, run_id, use_codex, codex_extract)


def report(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_dir = run_directory(config, run_id)
    if config.get("study_version") == 1:
        from .study_metrics import report as study_report
        result = study_report(run_dir, config)
        return {"run_id": run_id, "status": result["status"], "distinct_vacancies": result["distinct_vacancies"],
                "requirement_share_rows": len(result["requirement_shares"]), "visualization": str(run_dir / "role-baseline.html")}
    result = build_report(run_dir, config)
    return {
        "run_id": run_id,
        "periods_reported": len(result["coverage"]),
        "eligible_period_observations": sum(
            row["complete_eligible_descriptions"] for row in result["coverage"]
        ),
        "accepted_requirement_documents": result["accepted_requirement_documents"],
        "same_vacancy_changes": len(result["same_vacancy_changes"]),
        "requirement_share_rows": len(result["requirement_shares"]),
        "next_targeted_query": result["next_targeted_query"],
        "evidence_report": str(run_dir / "evidence-report.md"),
        "coverage_csv": str(run_dir / "coverage.csv"),
        "requirements_csv": str(run_dir / "requirements.csv"),
        "manifest": str(run_dir / "manifest.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="degent", description="Auditable historical fintech job-requirement research"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--run-id", default=date.today().isoformat())
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("discover")
    subparsers.add_parser("fetch")
    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument(
        "--codex",
        action="store_true",
        help="Use existing Codex ChatGPT login for bounded extraction",
    )
    subparsers.add_parser("report")
    study_parser = subparsers.add_parser("study", help="Provider-neutral Capital One comparison study")
    study_parser.add_argument("action", choices=["create", "prepare", "groups", "approve-taxonomy", "gold", "evaluate", "audit-packet", "review-document", "retry-failed", "model"])
    from .openrouter import APPROVED_MODELS, DEFAULT_MODEL
    study_parser.add_argument("--model", choices=APPROVED_MODELS, default=DEFAULT_MODEL)
    study_parser.add_argument("--seed-run")
    study_parser.add_argument("--file", type=Path)
    study_parser.add_argument("--vectors", type=Path)
    current_parser = subparsers.add_parser("current")
    current_parser.add_argument(
        "--companies", help="Comma-separated local company IDs (new runs only)"
    )
    current_parser.add_argument("--document-limit", type=int)
    current_parser.add_argument("--requests", type=int)
    subparsers.add_parser("refresh", help="Start a new manual current capture session")
    continuation = subparsers.add_parser(
        "continue", help="Extend a paused collection budget without resetting usage"
    )
    for name in (
        "requests",
        "bytes",
        "seconds",
        "storage",
        "documents",
        "model_calls",
        "extraction_documents",
        "semantic_review_calls",
    ):
        continuation.add_argument("--" + name.replace("_", "-"), type=int)
    companies = subparsers.add_parser("companies", help="Search/manage the local company library")
    company_actions = companies.add_subparsers(dest="company_action", required=True)
    search = company_actions.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--page", type=int, default=0)
    search.add_argument("--size", type=int, default=10)
    addition = company_actions.add_parser("add")
    addition.add_argument("name")
    addition.add_argument("--website", default="")
    addition.add_argument("--career-url", default="")
    addition.add_argument("--alias", action="append", default=[])
    addition.add_argument("--group", default="unknown")
    company_actions.add_parser("show").add_argument("id")
    verification = company_actions.add_parser("verify")
    verification.add_argument("id")
    for name in ("requests", "bytes", "seconds"):
        verification.add_argument("--" + name, type=int)
    company_actions.add_parser(
        "browser-check",
        help="Run isolated browser policy integration tests and save a hash-bound readiness receipt",
    )
    configure = company_actions.add_parser("configure-source")
    configure.add_argument("id")
    configure.add_argument("--url", required=True)
    configure.add_argument("--provider", choices=["dom", "sitemap"], default="dom")
    configure.add_argument("--dom-config", type=Path, required=True)
    batch_parser = company_actions.add_parser("verify-all")
    batch_parser.add_argument("--targets", type=Path)
    batch_parser.add_argument("--companies")
    batch_parser.add_argument("--resume", type=Path)
    batch_parser.add_argument("--retry-incomplete", action="store_true")
    batch_parser.add_argument("--retry-ids")
    batch_parser.add_argument("--refresh", action="store_true")
    batch_parser.add_argument("--keep-stop-request", action="store_true", help=argparse.SUPPRESS)
    for name in (
        "requests",
        "bytes",
        "seconds",
        "batch_requests",
        "batch_bytes",
        "batch_seconds",
        "concurrency",
    ):
        batch_parser.add_argument("--" + name.replace("_", "-"), type=int)
    scope = subparsers.add_parser("scope", help="Create an explicit study scope continuation")
    scope.add_argument("--from-run", required=True)
    scope.add_argument("--companies", required=True)

    subparsers.add_parser("audit")
    subparsers.add_parser("suggest")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--codex", action="store_true")
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("path", type=Path)
    evaluation_parser = subparsers.add_parser("evaluate")
    evaluation_parser.add_argument("path", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    from .companies import CompanyLibrary

    library = CompanyLibrary(config)
    if args.operation == "companies":
        try:
            action = args.company_action
            if action == "configure-source":
                result = library.configure_source(
                    args.id, args.url, args.provider, json.loads(args.dom_config.read_text())
                )
                print(json.dumps(result, indent=2))
                return
            if action == "browser-check":
                import os
                import subprocess

                from .browser_sources import policy_hash

                checked = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-p",
                        "test_browser_sources.py",
                        "-v",
                    ],
                    env={**os.environ, "DEGENT_BROWSER_TESTS": "1"},
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if (
                    checked.returncode
                    or "Ran 0 tests" in checked.stderr
                    or "skipped=" in checked.stderr
                ):
                    raise RuntimeError("Browser policy checks failed: " + checked.stderr[-3500:])
                receipt = {
                    "policy_sha256": policy_hash(),
                    "tested_at": utc_now(),
                    "command": "DEGENT_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_browser_sources.py -v",
                    "output": checked.stderr,
                }
                write_json(library.root / "browser-policy-tested.json", receipt)
                print(json.dumps(receipt, indent=2))
                return
            if action == "verify-all":
                import signal

                from .verification_batch import VerificationBatch

                if args.resume and (
                    args.targets
                    or args.companies
                    or args.refresh
                    or any(
                        getattr(args, k) is not None
                        for k in (
                            "requests",
                            "bytes",
                            "seconds",
                            "batch_requests",
                            "batch_bytes",
                            "batch_seconds",
                            "concurrency",
                        )
                    )
                ):
                    raise ValueError("Resume preserves frozen scope and budgets")
                if (args.retry_incomplete or args.retry_ids) and not args.resume:
                    raise ValueError("Retry requires --resume")
                batch = (
                    VerificationBatch(config, args.resume)
                    if args.resume
                    else VerificationBatch.create(
                        config,
                        ids=args.companies.split(",") if args.companies else None,
                        targets=args.targets,
                        **{
                            k: getattr(args, k)
                            for k in (
                                "requests",
                                "bytes",
                                "seconds",
                                "batch_requests",
                                "batch_bytes",
                                "batch_seconds",
                                "concurrency",
                            )
                        },
                    )
                )
                previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                for sig in previous:
                    signal.signal(sig, lambda *_: batch.stop_event.set())
                try:
                    result = batch.run(
                        args.retry_incomplete,
                        args.retry_ids.split(",") if args.retry_ids else None,
                        preserve_stop=args.keep_stop_request,
                    )
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
                print(json.dumps(result, indent=2))
                return
            result = (
                library.search(args.query, args.page, args.size)
                if action == "search"
                else (
                    library.add(args.name, args.website, args.career_url, args.alias, args.group)
                    if action == "add"
                    else (
                        library.verify(
                            args.id,
                            **{
                                k: getattr(args, k)
                                for k in ("requests", "bytes", "seconds")
                                if getattr(args, k, None) is not None
                            },
                        )
                        if action == "verify"
                        else library.get(args.id)
                    )
                )
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return
        except (ValueError, RuntimeError) as exc:
            parser.error(str(exc))
    if args.operation == "scope":
        from .dashboard import Dashboard

        result = Dashboard(Path.cwd(), Path(args.config)).create_run(
            {
                "run_id": args.run_id,
                "parent_run_id": args.from_run,
                "employers": args.companies.split(","),
            }
        )
        print(json.dumps(result, indent=2))
        return
    if args.operation == "study" and args.action == "create":
        from .study import create
        try:
            result = create(config, args.run_id, Path(config["run_dir"]) / safe_id(args.seed_run) if args.seed_run else None)
            print(json.dumps(result, indent=2))
            return
        except (ValueError, RuntimeError) as exc:
            parser.error(str(exc))
    directory = run_directory(config, args.run_id)
    saved_config = directory / "dashboard-config.json"
    from .sources import collect_current, import_postings
    from .workflow import audit_evidence, evaluate, run_pipeline, suggestions

    handlers = {
        "doctor": lambda: doctor(config),
        "discover": lambda: discover(config, args.run_id),
        "fetch": lambda: fetch(config, args.run_id),
        "extract": lambda: extract(config, args.run_id, args.codex),
        "report": lambda: report(config, args.run_id),
    }
    from .collection import collect, extend

    handlers.update(
        {
            "refresh": lambda: collect(config, args.run_id, refresh=True),
            "continue": lambda: extend(
                config,
                args.run_id,
                {
                    k: getattr(args, k)
                    for k in (
                        "requests",
                        "bytes",
                        "seconds",
                        "storage",
                        "documents",
                        "model_calls",
                        "extraction_documents",
                        "semantic_review_calls",
                    )
                    if getattr(args, k) is not None
                },
            ),
            "run": lambda: run_pipeline(config, args.run_id, args.codex),
            "current": lambda: collect_current(config, args.run_id),
            "import": lambda: import_postings(config, args.run_id, args.path),
            "audit": lambda: audit_evidence(config, args.run_id),
            "suggest": lambda: suggestions(config, []),
            "evaluate": lambda: evaluate(config, args.path, directory / "evaluation.json"),
        }
    )
    def study_action():
        if config.get("study_version") != 1:
            raise ValueError("Create a separate study; legacy evidence and policies are preserved.")
        from .study import prepare, review_document, configure_model
        from .taxonomy import approve, propose
        from .study_evaluation import audit_packet, evaluate as evaluate_study, gold_packet
        from .collection import retry_failed
        if args.action in {"approve-taxonomy", "evaluate", "review-document"} and not args.file:
            raise ValueError("This action requires --file with human decisions/labels.")
        return {
            "model": lambda: configure_model(config, args.run_id, args.model),
            "prepare": lambda: prepare(config, args.run_id),
            "groups": lambda: propose(directory, read_json(args.vectors) if args.vectors else None),
            "approve-taxonomy": lambda: approve(directory, read_json(args.file)),
            "gold": lambda: gold_packet(directory),
            "evaluate": lambda: evaluate_study(directory, args.file),
            "audit-packet": lambda: audit_packet(directory),
            "review-document": lambda: review_document(config, args.run_id, read_json(args.file)),
            "retry-failed": lambda: retry_failed(config, args.run_id),
        }[args.action]()
    handlers["study"] = study_action
    try:
        with operation_lock(directory):
            if saved_config.exists():
                if args.operation == "current" and (
                    args.companies or args.document_limit is not None or args.requests is not None
                ):
                    raise ValueError(
                        "Saved scope/budgets are frozen. Use scope, continue, or refresh explicitly."
                    )
                config = load_config(saved_config)
                if config.get("study_version") == 1 and args.operation in {"discover", "fetch", "import", "evaluate"}:
                    raise ValueError("This frozen study uses current official collection and study-specific evaluation; archives/imports require a separate run.")
                if args.operation == "refresh" and config.get("collection_version") != 2:
                    raise ValueError(
                        "Legacy run preserved. Create a scope continuation for manual refresh sessions."
                    )
            elif args.operation != "doctor":
                if args.operation in {"continue", "refresh"}:
                    raise ValueError("Create/collect a run before continuation or refresh.")
                config.update(collection_version=2, current_document_cap=None)
                if args.operation == "current":
                    selected = (
                        args.companies.split(",")
                        if args.companies
                        else [e["id"] for e in config["employers"]]
                    )
                    config["employers"] = [library.get(i) for i in selected]
                    if args.document_limit is not None:
                        if args.document_limit < 1:
                            raise ValueError("Optional document limit must be positive.")
                        config["current_document_cap"] = args.document_limit
                    if args.requests is not None:
                        if args.requests < 1:
                            raise ValueError("Request allowance must be positive.")
                        config["current_request_cap"] = args.requests
                else:
                    config["employers"] = [library.get(e["id"]) for e in config["employers"]]
                from .sources import current_period

                current = current_period(config)
                if (
                    args.operation == "current"
                    or (args.operation == "run" and "current" in config.get("sources", []))
                ) and not any(p["id"] == current["id"] for p in config["periods"]):
                    config["periods"].append(current)
                write_json(saved_config, {k: v for k, v in config.items() if k != "_path"})
            if config.get("study_version") == 1 and args.operation in {"current", "refresh", "run"}:
                import signal
                def stop_study(*_):
                    raise KeyboardInterrupt
                previous = signal.signal(signal.SIGTERM, stop_study)
                try:
                    result = handlers[args.operation]()
                finally:
                    signal.signal(signal.SIGTERM, previous)
            else:
                result = handlers[args.operation]()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (RuntimeError, ValueError) as exc:
        print(
            json.dumps({"status": "suspended", "reason": str(exc), "run_id": args.run_id}),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
