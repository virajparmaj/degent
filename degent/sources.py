"""Public ATS feeds and explicit imports, with independent date provenance."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .archive import ArchiveClient
from .classify import classify_geography, classify_role
from .store import (
    append_jsonl,
    body_usability,
    clean_html,
    digest,
    read_json,
    read_jsonl,
    safe_id,
    utc_now,
    write_json,
)


def current_period(config: dict) -> dict:
    today = datetime.now(ZoneInfo(config.get("timezone", "America/Chicago"))).date()
    return {
        "id": "CURRENT-" + today.isoformat(),
        "start": today.isoformat(),
        "end": today.isoformat(),
        "kind": "partial",
    }


def feed_url(source: dict) -> str:
    board = str(source.get("board", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", board):
        raise ValueError("A public job board needs a simple board identifier.")
    if source.get("provider") == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true"
    if source.get("provider") == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}"
    raise ValueError("Supported current sources are Greenhouse and Ashby public job feeds.")


def feed_jobs(payload: bytes, source: dict) -> list[dict]:
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("The feed did not return a jobs list.")
    result = []
    for job in data["jobs"]:
        if not isinstance(job, dict):
            continue
        if source["provider"] == "greenhouse":
            text = clean_html(html.unescape(str(job.get("content") or "")).encode())
            location = str((job.get("location") or {}).get("name", ""))
            row = {
                "title": job.get("title"),
                "text": text,
                "location": location,
                "source_url": job.get("absolute_url"),
                "posting_id": str(job.get("id") or ""),
                "underlying_job_id": job.get("internal_job_id"),
                "stated_publication_at": job.get("first_published"),
                "publication_date_source": (
                    "greenhouse.first_published" if job.get("first_published") else None
                ),
                "stated_updated_at": job.get("updated_at"),
                "update_date_source": "greenhouse.updated_at",
            }
        else:
            row = {
                "title": job.get("title"),
                "text": job.get("descriptionPlain")
                or clean_html(str(job.get("descriptionHtml") or "").encode()),
                "location": job.get("location", ""),
                "source_url": job.get("jobUrl"),
                "posting_id": str(job.get("id") or job.get("jobUrl") or ""),
                "employment_type": job.get("employmentType"),
                "stated_publication_at": None,
                "publication_date_source": None,
                "stated_updated_at": job.get("publishedAt"),
                "update_date_source": "ashby.publishedAt (last published)",
            }
        row["original_record"] = job
        result.append(row)
    return result


def observation(
    config: dict, run_id: str, employer: dict, row: dict, provider: str, observed_at: str,
    *, identity: dict | None = None, persist: bool = True,
) -> dict:
    title = str(row.get("title") or "").strip()
    text = f"{employer['name']}\n{title}\nLocation: {row.get('location') or 'Unknown'}\n{row.get('text') or ''}"
    url = str(row.get("source_url") or "")
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
    capture = row.get("captured_at") if provider == "import" else observed_at
    time_basis = (
        "user_attested_capture"
        if capture and provider == "import"
        else "collector_capture" if capture else "undated_import"
    )
    capture_at = (
        datetime.fromisoformat(capture.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .strftime("%Y%m%d%H%M%S")
        if capture
        else None
    )
    capture_date = (
        datetime.fromisoformat(capture.replace("Z", "+00:00"))
        .astimezone(ZoneInfo(config.get("timezone", "America/Chicago")))
        .date()
        .isoformat()
        if capture
        else None
    )
    posting_id = str(row.get("posting_id") or "")
    # Reuse the same public posting identity as archived ATS pages where possible.
    from .cli import vacancy_identity

    vacancy_id, source_id, canonical_url = vacancy_identity(
        employer["id"], url, {"requisition_id": posting_id} if provider == "capital_one" else None
    )
    if not source_id and posting_id:
        vacancy_id = f"{employer['id']}:{provider}:posting-id:{posting_id}"
    identity_url = (
        f"requisition:{posting_id}" if provider == "capital_one" and posting_id else canonical_url
    )
    identity = identity or {}
    vacancy_id = identity.get("vacancy_id", vacancy_id)
    doc_id = identity.get("document_id") or digest(employer["id"] + identity_url + text)[:24]
    obs_id = identity.get("observation_id") or digest(doc_id + str(capture_at) + provider)[:24]
    root = Path(config["data_dir"]) / safe_id(run_id)
    raw_path, clean_path = root / "raw" / (
        obs_id + "-" + digest(raw)[:12] + ".json"
    ), root / "clean" / (doc_id + ".txt")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    if persist:
        raw_path.write_bytes(raw)
        clean_path.write_text(text, encoding="utf-8")
    usable, reason = body_usability(text, employer["name"])
    if provider == "capital_one":
        # Verify source content/structured employer, never the collector-added label.
        usable, reason = body_usability(str(row.get("text") or ""), employer["name"])
        if row.get("verified_employer") != employer["name"]:
            usable, reason = False, "employer_not_verified"
    role = classify_role(title, text, config)
    geography = classify_geography(url, text)
    eligible = role["eligible"] and geography["status"] in {"us_location", "remote_us_eligible"}
    result = {
        "document_id": doc_id,
        "observation_id": obs_id,
        "vacancy_id": vacancy_id,
        "source_posting_id": posting_id or source_id,
        "canonical_url": canonical_url,
        "employer_id": employer["id"],
        "employer": employer["name"],
        "brand": employer["name"],
        "business_group": employer.get("group", "unknown"),
        "source": provider,
        "evidence_basis": time_basis,
        "original_url": url,
        "archive_url": None,
        "archive_capture_at": None,
        "current_capture_at": observed_at if provider != "import" else None,
        "requisition_id": posting_id if provider == "capital_one" else None,
        "site_job_id": row.get("site_job_id"),
        "verified_employer": row.get("verified_employer"),
        "raw_source_file": row.get("raw_source_file"),
        "raw_source_sha256": row.get("raw_source_sha256"),
        "capture_at": capture_at,
        "capture_date": capture_date,
        "capture_date_source": (
            row.get("capture_date_source") if provider == "import" else "collector clock"
        ),
        "stated_publication_at": row.get("stated_publication_at"),
        "publication_date_source": row.get("publication_date_source"),
        "stated_updated_at": row.get("stated_updated_at"),
        "update_date_source": row.get("update_date_source"),
        "retrieved_at": observed_at,
        "collector_first_observed_at": observed_at,
        "raw_sha256": digest(raw),
        "clean_sha256": digest(text),
        "content_format": "posting-json",
        "parser_version": (
            "capital-one-jobposting-v1" if provider == "capital_one" else "public-feed-v1"
        ),
        "title": title,
        "title_source": provider + " title",
        "employment_type": row.get("employment_type", "unknown"),
        "location": row.get("location"),
        "geography_status": geography["status"],
        "geography_evidence": geography["evidence"],
        "body_text_complete": usable,
        "body_complete": usable and eligible,
        "eligible": role["eligible"],
        "eligible_for_primary": usable and eligible and capture_date is not None,
        "body_status": (
            reason
            if not usable
            else role["reason"] if not role["eligible"] else geography["status"]
        ),
        "role_classification": role,
        "role_track": role["tracks"][0] if role["eligible"] else None,
        "role_subtype": role.get("subtype", "unknown"),
        "level": role["level"],
        "raw_file": str(raw_path),
        "clean_file": str(clean_path),
    }
    if config.get("study_version") == 1:
        from .evidence import decorate_observation
        result["structured_locations"] = row.get("structured_locations", [])
        decorate_observation(result, config, text)
    return result


def collect_current(config: dict, run_id: str) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    if config.get("collection_version") == 2:
        from .collection import collect
        return collect(config, run_id)
    return _legacy_collect_current(config, run_id)


def _legacy_collect_current(config: dict, run_id: str) -> dict:
    from .cli import manifest_for, run_directory, save_manifest
    from .capital_one import collect as collect_capital_one

    directory = run_directory(config, run_id)
    manifest = manifest_for(directory, run_id)
    outcomes = manifest.setdefault("current_sources", {})
    used = int(manifest.get("current_requests_used", 0))
    cap = int(config.get("current_request_cap", 12))
    client = ArchiveClient(
        config["archives"]["user_agent"],
        int(config["request_timeout_seconds"]),
        int(config["retry_count"]),
        float(config["host_delay_seconds"]),
    )
    client.request_limit = max(0, cap - used)

    def checkpoint():
        save_manifest(directory, manifest)

    def reserve(event):
        manifest["current_requests_used"] = used + client.request_count
        manifest.setdefault("current_request_reservations", []).append(event.copy())
        checkpoint()

    client.on_request = reserve
    observations = read_jsonl(directory / "observations.jsonl")
    before = len(observations)
    maximum = int(config.get("current_document_cap", 60))

    def document_count():
        # Count raw detail captures too, including rejected/unparseable responses
        # and a body written immediately before interruption.
        raw_root = Path(config["data_dir"]) / safe_id(run_id) / "current-capital-one"
        bodies = len(list(raw_root.glob("detail-*.json")))
        feeds = len(
            {r["document_id"] for r in observations if r.get("source") in {"greenhouse", "ashby"}}
        )
        capital = len(
            {
                r.get("site_job_id") or r["document_id"]
                for r in observations
                if r.get("source") == "capital_one"
            }
        )
        return max(int(manifest.get("current_documents_saved", 0)), feeds + max(bodies, capital))

    def store_row(employer, row, provider, observed, save_rejected=False):
        role = classify_role(str(row.get("title") or ""), str(row.get("text") or ""), config)
        if not save_rejected and not role["eligible"]:
            return None, False
        # Dedup before writing raw files, including repeated retrieval timestamps.
        text = f"{employer['name']}\n{str(row.get('title') or '').strip()}\nLocation: {row.get('location') or 'Unknown'}\n{row.get('text') or ''}"
        from .cli import canonical_vacancy_url

        doc_id = digest(
            employer["id"] + canonical_vacancy_url(str(row.get("source_url") or "")) + text
        )[:24]
        existing = next(
            (
                r
                for r in observations
                if r["document_id"] == doc_id
                or (
                    provider == "capital_one"
                    and r.get("source") == provider
                    and r.get("requisition_id") == row.get("posting_id")
                    and r.get("clean_sha256") == digest(text)
                )
            ),
            None,
        )
        if existing:
            return existing, False
        item = observation(config, run_id, employer, row, provider, observed)
        append_jsonl(directory / "observations.jsonl", [item])
        observations.append(item)
        manifest["current_documents_saved"] = document_count()
        checkpoint()
        return item, True

    for employer in config["employers"]:
        sources = employer.get("current_sources", [])
        if not sources:
            outcomes[employer["id"] + ":current"] = {
                "status": "unsupported",
                "reason": "No current-source adapter configured for this employer.",
            }
            checkpoint()
        for source in sources:
            provider = source.get("provider", "unknown")
            key = employer["id"] + ":" + provider + ":" + str(source.get("board", ""))
            info = outcomes.setdefault(key, {})
            if info.get("status") in {"complete", "complete_bounded", "partial_failure"}:
                continue
            if provider == "capital_one":
                try:
                    collect_capital_one(
                        config,
                        run_id,
                        employer,
                        source,
                        client,
                        directory,
                        manifest,
                        info,
                        checkpoint,
                        lambda *a, **kw: store_row(employer, *a, **kw),
                        document_count,
                    )
                except (ValueError, RuntimeError) as exc:
                    info.update(status="unavailable", reason=str(exc))
                    checkpoint()
                continue
            if provider not in {"greenhouse", "ashby"}:
                info.update(status="unsupported", reason="No adapter for provider " + provider)
                checkpoint()
                continue
            cached = directory / ("feed-" + safe_id(key) + ".json")
            if cached.exists():
                data = read_json(cached)
                rows, observed = data["jobs"], data["observed_at"]
            else:
                if client.request_count >= client.request_limit:
                    info.update(
                        status="request_limit",
                        reason="Saved current request cap exhausted (including retries).",
                    )
                    checkpoint()
                    continue
                try:
                    status, headers, body = client.request(feed_url(source))
                    observed = utc_now()
                    if status != 200:
                        raise ValueError(headers.get("error") or f"HTTP {status}")
                    rows = feed_jobs(body, source)
                except (ValueError, RuntimeError) as exc:
                    info.update(status="unavailable", reason=str(exc))
                    checkpoint()
                    continue
                write_json(
                    cached,
                    {
                        "jobs": rows,
                        "observed_at": observed,
                        "source_url": feed_url(source),
                        "feed_sha256": digest(body),
                    },
                )
            info.update(total_postings=len(rows), observed_at=observed, status="processing")
            for index in range(int(info.get("next_index", 0)), len(rows)):
                if document_count() >= maximum:
                    info["status"] = "document_limit"
                    break
                store_row(employer, rows[index], provider, observed)
                info["next_index"] = index + 1
                checkpoint()
            if info.get("next_index", 0) >= len(rows):
                info["status"] = "complete"
            checkpoint()
    manifest["current_documents_saved"] = document_count()
    checkpoint()
    return {
        "saved": len(observations) - before,
        "total_saved": document_count(),
        "requests_used": manifest.get("current_requests_used", 0),
        "sources": outcomes,
    }


def current_selections(directory: Path) -> list[dict]:
    """Show the latest eligibility audit beside immutable collection decisions."""
    decisions = {r["site_job_id"]: r for r in read_jsonl(directory / "current-selection.jsonl")}
    observations = {
        r.get("site_job_id"): r
        for r in read_jsonl(directory / "observations.jsonl")
        if r.get("source") == "capital_one"
    }
    for key, decision in decisions.items():
        row = observations.get(key)
        if row:
            decision.update(
                status="eligible" if row.get("body_complete") else "rejected_after_retrieval",
                reason=row.get("body_status"),
                classification=row.get("role_classification"),
                geography=row.get("geography_status"),
                document_id=row["document_id"],
            )
    return list(decisions.values())


def import_postings(config: dict, run_id: str, path: Path) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    from .cli import run_directory

    if path.stat().st_size > 5_000_000:
        raise ValueError("Import exceeds 5 MB. Split the file into smaller batches.")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= int(
        config.get("import_document_cap", 60)
    ):
        raise ValueError("Import must be a JSON list within the configured document limit.")
    company_map = {e["id"]: e for e in config["employers"]}
    for row in rows:
        if not isinstance(row, dict) or row.get("employer_id") not in company_map:
            raise ValueError("Each import needs an employer_id in this run.")
        if any(
            not isinstance(row.get(k), str) or not row[k].strip()
            for k in ("title", "text", "source_url", "location")
        ):
            raise ValueError("Each import needs title, text, source_url, and location strings.")
        if (
            urlsplit(row["source_url"]).scheme not in {"https", "http"}
            or not urlsplit(row["source_url"]).netloc
        ):
            raise ValueError("Source URLs must be HTTP or HTTPS.")
        if row.get("captured_at"):
            timestamp = datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None or not row.get("capture_date_source"):
                raise ValueError(
                    "Dated imports need a timezone and capture_date_source explaining the date."
                )
            if timestamp > datetime.fromisoformat(utc_now()):
                raise ValueError("A capture cannot be in the future.")
    directory = run_directory(config, run_id)
    existing = read_jsonl(directory / "observations.jsonl")
    seen = {r["observation_id"] for r in existing}
    remaining = int(config.get("import_document_cap", 60)) - len(
        {r["observation_id"] for r in existing if r.get("source") == "import"}
    )
    saved = 0
    for row in rows:
        if saved >= remaining:
            break
        item = observation(
            config, run_id, company_map[row["employer_id"]], row, "import", utc_now()
        )
        if item["observation_id"] not in seen:
            append_jsonl(directory / "observations.jsonl", [item])
            seen.add(item["observation_id"])
            saved += 1
    return {
        "imported": saved,
        "provided": len(rows),
        "remaining": remaining - saved,
        "status": "document_limit" if saved >= remaining else "complete",
        "date_note": "Undated imports are excluded from time comparisons; supplied capture dates are user attestations.",
    }
