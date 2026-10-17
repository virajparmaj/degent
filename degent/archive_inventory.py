"""Free public archive inventory/replays, isolated from study inference and frozen runs."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.error
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .capital_one import CareerHTML, job_posting, official_url
from .metadata import metadata_assertions
from .store import (
    clean_html,
    digest,
    operation_lock,
    read_json,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
)

VERSION = "public-wayback-evidence-v1"


class _ArchiveRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "web.archive.org"
            or parsed.username
            or parsed.password
        ):
            raise urllib.error.URLError("Archive redirect leaves the authorized public host.")
        return super().redirect_request(request, response, code, message, headers, newurl)


def public_get(url: str, directory: Path, *, retry_failed: bool = False) -> tuple[bytes, dict]:
    """Persist one attributable response; reuse it without resetting acquisition history."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "web.archive.org"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Only the free HTTPS Internet Archive endpoints are permitted.")
    key = digest(url)
    history = sorted(directory.glob(key + "-attempt-*.json"))
    receipt_path = history[-1] if history else directory / (key + ".json")
    body_path = receipt_path.with_suffix(".bin")
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        body = body_path.read_bytes() if receipt["bytes"] else b""
        if digest(body) != receipt["body_sha256"]:
            raise ValueError("Saved public archive response changed.")
        if receipt["status"] == 200 or not retry_failed:
            return body, receipt
        attempt = len(history) + 2
        receipt_path = directory / (key + f"-attempt-{attempt:04}.json")
        body_path = receipt_path.with_suffix(".bin")
    directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Degent public historical job research"}
    )
    body, status, headers, final_url, outcome = b"", None, {}, url, "response"
    try:
        with urllib.request.build_opener(_ArchiveRedirect()).open(request, timeout=30) as response:
            body, status, headers, final_url = (
                response.read(),
                response.status,
                dict(response.headers),
                response.url,
            )
    except urllib.error.HTTPError as exc:
        body, status, headers, final_url = exc.read(), exc.code, dict(exc.headers), exc.url
        outcome = type(exc).__name__
    except (urllib.error.URLError, TimeoutError) as exc:
        status, outcome = getattr(exc, "code", None), type(exc).__name__
    receipt = {
        "version": VERSION,
        "request_url": url,
        "retrieved_at": utc_now(),
        "status": status,
        "response_headers": headers,
        "final_url": final_url,
        "bytes": len(body),
        "body_sha256": digest(body),
        "outcome": outcome,
        "free_public_http": True,
        "model_calls": 0,
        "paid_provider_calls": 0,
    }
    body_path.write_bytes(body)
    write_json(receipt_path, receipt)
    return body, receipt


def parse_index(body: bytes) -> tuple[list[dict], str | None]:
    values = json.loads(body)
    if (
        not isinstance(values, list)
        or not values
        or values[0] != ["timestamp", "original", "digest"]
    ):
        raise ValueError("Unexpected archive index schema; cannot claim collection completeness.")
    records, cursor, separator = [], None, False
    for value in values[1:]:
        if value == []:
            separator = True
        elif separator and len(value) == 1 and isinstance(value[0], str):
            cursor = value[0]
        elif not separator and len(value) == 3:
            if not re.fullmatch(r"\d{14}", value[0]):
                raise ValueError("Invalid archive timestamp.")
            records.append(dict(zip(values[0], value)))
        else:
            raise ValueError("Unexpected archive index continuation record.")
    return records, cursor


def index_page(
    year: int,
    directory: Path,
    *,
    retry_failed: bool = False,
    url_pattern: str = "www.capitalonecareers.com/job/*",
    from_month: int = 1,
    to_month: int = 12,
) -> dict:
    """Advance a durable index by one page; page size never limits the selection."""
    if not re.fullmatch(r"www\.capitalonecareers\.com/job/[A-Za-z0-9/*_-]+", url_pattern):
        raise ValueError("Index pattern must stay within official public job paths.")
    if not 1 <= from_month <= to_month <= 12:
        raise ValueError("Archive month range must be ordered within the authorized year.")
    state_path = directory / f"index-{year}.json"
    with operation_lock(directory):
        state = (
            read_json(state_path)
            if state_path.exists()
            else {"year": year, "pages": [], "next_cursor": None, "complete": False}
        )
        if (
            state_path.exists()
            and state.get("url_pattern", "www.capitalonecareers.com/job/*") != url_pattern
        ):
            raise ValueError("Preserved index query changed; use a separate collection.")
        if state_path.exists() and (state.get("from_month", 1), state.get("to_month", 12)) != (
            from_month,
            to_month,
        ):
            raise ValueError("Preserved index month range changed; use a separate collection.")
        if state["complete"]:
            return state
        parameters = {
            "url": url_pattern,
            "from": str(year) if from_month == 1 and to_month == 12 else f"{year}{from_month:02}",
            "to": str(year) if from_month == 1 and to_month == 12 else f"{year}{to_month:02}",
            "output": "json",
            "filter": "statuscode:200",
            "fl": "timestamp,original,digest",
            "collapse": "digest",
            "limit": "100",
            "showResumeKey": "true",
        }
        if state["next_cursor"]:
            parameters["resumeKey"] = state["next_cursor"]
        url = "https://web.archive.org/cdx/search/cdx?" + urlencode(parameters)
        kwargs = {"retry_failed": True} if retry_failed else {}
        body, receipt = public_get(url, directory / "responses", **kwargs)
        if receipt["status"] != 200:
            raise ValueError(
                "Public archive index unavailable; preserved response cannot establish absence."
            )
        records, cursor = parse_index(body)
        if cursor and cursor == state["next_cursor"]:
            raise ValueError("Archive index cursor did not advance.")
        existing = read_jsonl(directory / f"candidates-{year}.jsonl")
        unique = {(r["timestamp"], r["original"], r["digest"]): r for r in existing + records}
        write_jsonl(directory / f"candidates-{year}.jsonl", unique.values())
        state["pages"].append(
            {"request_sha256": digest(url), "records": len(records), "body_sha256": digest(body)}
        )
        state.update(
            url_pattern=url_pattern,
            from_month=from_month,
            to_month=to_month,
            next_cursor=cursor,
            complete=cursor is None,
            candidate_captures_preserved=len(unique),
            index_is_description_evidence=False,
            page_size_is_selection_cap=False,
            sampling_proves_hiring_prevalence=False,
        )
        write_json(state_path, state)
    return state


def verified_wording(receipt: dict, original_url: str) -> str:
    """Require archive transport, actual Memento timestamp and matching original URI."""
    if receipt["status"] != 200 or urlsplit(receipt["final_url"]).hostname != "web.archive.org":
        raise ValueError("Unverified archive transport.")
    headers = {key.casefold(): value for key, value in receipt["response_headers"].items()}
    originals = re.findall(r'<([^>]+)>;\s*rel="original"', headers.get("link", ""))
    if len(originals) != 1 or official_url(originals[0], job=True) != official_url(
        original_url, job=True
    ):
        raise ValueError("Archive original URI does not identify the requested vacancy.")
    if not headers.get("memento-datetime") or not headers.get("x-archive-src"):
        raise ValueError("Archive authentication headers missing.")
    observed = parsedate_to_datetime(headers["memento-datetime"]).astimezone(timezone.utc)
    final_timestamp = re.search(r"/web/(\d{14})(?:id_)?/", receipt["final_url"])
    if not final_timestamp or final_timestamp[1] != observed.strftime("%Y%m%d%H%M%S"):
        raise ValueError("Archive replay timestamp contradicts response observation date.")
    return observed.isoformat()


def replay(candidate: dict, directory: Path, *, retry_failed: bool = False) -> dict:
    """Preserve failed/closed pages too; only authenticated JobPosting bodies become observations."""
    official_url(candidate["original"], job=True)
    url = f"https://web.archive.org/web/{candidate['timestamp']}id_/{candidate['original']}"
    with operation_lock(directory):
        kwargs = {"retry_failed": True} if retry_failed else {}
        body, receipt = public_get(url, directory / "responses", **kwargs)
        parser_hash = digest(Path(__file__).read_bytes())
        attempt = digest(receipt.get("retrieved_at", "") + receipt["body_sha256"] + parser_hash)
        result_path = directory / "replays" / (digest(url) + "-" + attempt[:16] + ".json")
        if result_path.exists():
            return read_json(result_path)
        result = {
            "candidate": candidate,
            "request_sha256": digest(url),
            "accepted": False,
            "receipt": receipt,
            "model_calls": 0,
        }
        try:
            observed = verified_wording(receipt, candidate["original"])
            posting = archive_posting(body, candidate["original"])
            row = _observation(posting, candidate, receipt, observed, directory)
            existing = read_jsonl(directory / "observations.jsonl")
            if not any(r["observation_id"] == row["observation_id"] for r in existing):
                write_jsonl(directory / "observations.jsonl", existing + [row])
            result.update(
                accepted=True,
                observation_id=row["observation_id"],
                title=row["title"],
                verified_historical_wording_at=observed,
                employer_status=row["employer_status"],
            )
        except (ValueError, TypeError, KeyError) as exc:
            result["reason"] = str(exc)
        write_json(result_path, result)
    return result


def archive_posting(body: bytes, url: str) -> dict:
    """Preserve older employer assertions; only explicit Capital One aliases establish it."""
    try:
        posting = job_posting(body, url)
        return {
            **posting,
            "employer_status": "capital_one_source_assertion",
            "employer_assertion": posting["verified_employer"],
        }
    except ValueError as exc:
        if str(exc) != "JobPosting employer is not Capital One":
            raise
    jobs = []
    for script in CareerHTML(body).scripts:
        try:
            value = json.loads(script)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("@type") == "JobPosting":
            jobs.append(value)
    if len(jobs) != 1:
        raise ValueError("Ambiguous archived JobPosting identity.")
    job = jobs[0]
    source_url = official_url(job.get("url") or url, job=True)
    if source_url.split("/")[-1] != official_url(url, job=True).split("/")[-1]:
        raise ValueError("Archived JobPosting identifies a different vacancy.")
    identifier = job.get("identifier")
    req = identifier.get("value") if isinstance(identifier, dict) else identifier
    if not isinstance(req, str) or not re.fullmatch(r"R\d+(?:-\d+)?", req):
        raise ValueError("Missing archived requisition identity.")
    organization = job.get("hiringOrganization")
    employer = organization.get("name") if isinstance(organization, dict) else organization
    alias = employer == "Capital One - US"
    text = clean_html(html.unescape(str(job.get("description") or "")).encode())
    if not text or not job.get("title"):
        raise ValueError("Archived title or description missing.")
    locations = job.get("jobLocation") or []
    locations = [locations] if isinstance(locations, dict) else locations
    if not isinstance(locations, list):
        raise ValueError("Archived locations are malformed.")
    return {
        "title": job["title"],
        "text": text,
        "posting_id": req,
        "source_url": source_url,
        "structured_locations": [
            loc["address"]
            for loc in locations
            if isinstance(loc, dict) and isinstance(loc.get("address"), dict)
        ],
        "stated_publication_at": job.get("datePosted"),
        "publication_date_source": "JobPosting.datePosted",
        "stated_updated_at": job.get("dateModified"),
        "employer_assertion": employer,
        "employer_status": (
            "capital_one_us_source_alias" if alias else "conflicting_employer_assertion"
        ),
        "original_record": job,
    }


def _observation(
    posting: dict, candidate: dict, receipt: dict, observed: str, directory: Path
) -> dict:
    # Use the parser's exact description, not rewritten/summarized or collector-added prose.
    text = posting["text"]
    sha = digest(text)
    path = directory / "text" / (sha + ".txt")
    path.parent.mkdir(exist_ok=True)
    if path.exists() and digest(path.read_bytes()) != sha:
        raise ValueError("Archived description content-addressed file changed.")
    path.write_text(text, encoding="utf-8")
    document = "public-archive:" + digest(posting["posting_id"] + sha)
    row = {
        "observation_id": document + ":" + digest(receipt["request_url"]),
        "document_id": document,
        "vacancy_id": "candidate:" + posting["posting_id"],
        "requisition_id": posting["posting_id"],
        "title": posting["title"],
        "employer": "Capital One",
        "employer_id": "capital_one",
        "employer_status": posting["employer_status"],
        "employer_source_assertion": posting["employer_assertion"],
        "original_posting_record": posting["original_record"],
        "collection_kind": "historical",
        "collection_id": directory.name,
        "identity_status": "unresolved_historical_candidate",
        "source": "public_wayback_jobposting",
        "original_url": candidate["original"],
        "clean_file": str(path.resolve()),
        "clean_sha256": sha,
        "current_capture_at": None,
        "capture_at": receipt["retrieved_at"],
        "retrieved_at": receipt["retrieved_at"],
        "verified_historical_wording_at": observed,
        "wording_date_evidence": {
            "provenance": "verified Memento and original URI with archive storage identity",
            "receipt": receipt,
        },
        "stated_publication_at": posting.get("stated_publication_at"),
        "publication_date_source": posting.get("publication_date_source"),
        "locations_raw": posting.get("structured_locations", []),
        "geography_status": "unknown",
        "description_completeness": "unknown_requires_full_text_review",
        "description_readable": True,
        "comparison_subset": False,
        "parser_version": VERSION + ":capital-one-jobposting-v1",
        "parser_implementation_sha256": digest(Path(__file__).read_bytes()),
        "raw_source_sha256": receipt["body_sha256"],
        "historical_source": {
            "archive_requested_timestamp": candidate["timestamp"],
            "receipt": receipt,
        },
        "inference_authorized": False,
        "human_gold": False,
    }
    metadata_assertions(row, text)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["index", "replay"])
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--url")
    parser.add_argument("--timestamp")
    parser.add_argument("--url-pattern", default="www.capitalonecareers.com/job/*")
    parser.add_argument("--from-month", type=int, default=1)
    parser.add_argument("--to-month", type=int, default=12)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retain failed attempts and retry the public request once.",
    )
    args = parser.parse_args()
    if args.action == "index":
        if args.year not in range(2020, 2027):
            parser.error("Choose an authorized year from 2020 through 2026.")
        result = index_page(
            args.year,
            args.collection,
            retry_failed=args.retry_failed,
            url_pattern=args.url_pattern,
            from_month=args.from_month,
            to_month=args.to_month,
        )
        print(
            json.dumps(
                {key: result[key] for key in ("year", "complete", "candidate_captures_preserved")}
            )
        )
    else:
        if not args.timestamp or not re.fullmatch(r"\d{14}", args.timestamp) or not args.url:
            parser.error("Replay requires an exact 14-digit timestamp and official job URL.")
        result = replay(
            {"timestamp": args.timestamp, "original": args.url},
            args.collection,
            retry_failed=args.retry_failed,
        )
        print(
            json.dumps(
                {key: result[key] for key in ("accepted", "title", "reason") if key in result}
            )
        )


if __name__ == "__main__":
    main()
