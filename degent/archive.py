from __future__ import annotations

import json
import gzip
import http.client
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from datetime import datetime, timezone
from typing import Any


class ArchiveClient:
    def __init__(self, user_agent: str, timeout: int = 30, retries: int = 2, delay: float = 2.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self._last_request: dict[str, float] = {}
        self.request_count = 0
        self.request_limit: int | None = None
        self.request_events: list[dict[str, Any]] = []
        self.on_request = None

    def request(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        host = urllib.parse.urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        for attempt in range(self.retries + 1):
            if self.request_limit is not None and self.request_count >= self.request_limit:
                return 0, {"error": "discovery_request_cap_reached"}, b""
            req = urllib.request.Request(url, headers=request_headers)
            self.request_count += 1
            event: dict[str, Any] = {"request_number": self.request_count, "attempt": attempt + 1, "url": url, "started_at": datetime.now(timezone.utc).isoformat()}
            if self.on_request:
                self.on_request(event)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    response_headers = dict(response.headers.items())
                    response_headers["X-Degent-Final-URL"] = response.geturl()
                    result = (response.status, response_headers, response.read())
                event.update({"finished_at": datetime.now(timezone.utc).isoformat(), "http_status": result[0], "outcome": "response"})
                self.request_events.append(event)
                self._last_request[host] = time.monotonic()
                return result
            except urllib.error.HTTPError as exc:
                event.update({"finished_at": datetime.now(timezone.utc).isoformat(), "http_status": exc.code, "outcome": "http_error"})
                self.request_events.append(event)
                self._last_request[host] = time.monotonic()
                with exc:
                    retry_after = exc.headers.get("Retry-After", "")
                    if exc.code in (429, 503) and retry_after:
                        try:
                            seconds = int(retry_after)
                        except ValueError:
                            seconds = 61
                        if seconds > 60:
                            raise RuntimeError(f"rate_limited_suspend:{host}:{seconds}s") from exc
                        if attempt < self.retries:
                            time.sleep(seconds)
                            continue
                    if exc.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        time.sleep(min(2 ** attempt, 8))
                        continue
                    error_headers = dict(exc.headers.items())
                    try:
                        error_body = exc.read()
                    except (http.client.HTTPException, OSError) as body_exc:
                        # An unreadable error page must not abort the research run.
                        error_headers["error"] = f"{type(body_exc).__name__}: {body_exc}"
                        event["body_error"] = error_headers["error"][:300]
                        error_body = b""
                    return exc.code, error_headers, error_body
            except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                error = f"{type(exc).__name__}: {exc}"
                event.update({"finished_at": datetime.now(timezone.utc).isoformat(), "http_status": 0, "outcome": "network_error", "error": error[:300]})
                self.request_events.append(event)
                self._last_request[host] = time.monotonic()
                if attempt >= self.retries:
                    return 0, {"error": error}, b""
                time.sleep(min(2 ** attempt, 8))
        return 0, {"error": "retry_exhausted"}, b""

    def wayback(self, pattern: str, start: str, end: str, endpoint: str, resume_key: str = "") -> tuple[int, list[dict[str, Any]], str]:
        query_params = {
            "url": pattern, "from": start.replace("-", ""), "to": end.replace("-", ""),
            "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest,length",
            "filter": "statuscode:200", "showResumeKey": "true",
        }
        if resume_key:
            query_params["resumeKey"] = resume_key
        params = urllib.parse.urlencode(query_params)
        status, headers, body = self.request(endpoint + "?" + params)
        rows: list[dict[str, Any]] = []
        resume_key = ""
        if status == 200:
            try:
                decoded = body.decode("utf-8", errors="replace").strip()
                try:
                    parsed = json.loads(decoded)
                    data = parsed if isinstance(parsed, list) and parsed and isinstance(parsed[0], (list, dict)) else [parsed]
                except json.JSONDecodeError:
                    data = []
                    # CDX pagination appends a resume token after an otherwise valid JSON array.
                    try:
                        parsed, end = json.JSONDecoder().raw_decode(decoded)
                        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
                            data = parsed
                            resume_key = decoded[end:].strip()
                    except json.JSONDecodeError:
                        pass
                    if not data:
                        data = [json.loads(line) for line in decoded.splitlines() if line.strip()]
                columns = ["timestamp", "original", "statuscode", "mimetype", "digest", "length"]
                if data and isinstance(data[0], list) and data[0] and data[0][0] == "timestamp":
                    columns, data = data[0], data[1:]
                for row in data:
                    if isinstance(row, dict):
                        if "resumeKey" in row or "resume_key" in row:
                            resume_key = str(row.get("resumeKey") or row.get("resume_key") or "")
                        else:
                            rows.append(row)
                    elif isinstance(row, list) and len(row) >= 2:
                        rows.append(dict(zip(columns, row)))
                    elif isinstance(row, list) and len(row) == 1 and str(row[0]).startswith("resumeKey"):
                        resume_key = str(row[0]).split(":", 1)[-1].strip()
            except (json.JSONDecodeError, UnicodeDecodeError):
                status = 0
        return status, rows, resume_key

    def commoncrawl_catalogue(self, endpoint: str) -> tuple[int, list[dict[str, Any]]]:
        status, _, body = self.request(endpoint)
        if status != 200:
            return status, []
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return 0, []

    def commoncrawl(self, pattern: str, collection: str, endpoint_template: str) -> tuple[int, list[dict[str, Any]]]:
        endpoint = endpoint_template.format(collection=collection)
        url = endpoint + "?" + urllib.parse.urlencode({"url": pattern, "output": "json"})
        status, _, body = self.request(url)
        rows = []
        if status == 200:
            for line in body.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return status, rows

    def fetch_wayback(self, timestamp: str, original: str) -> tuple[int, dict[str, str], bytes, str]:
        url = "https://web.archive.org/web/" + timestamp + "id_/" + original
        status, headers, body = self.request(url)
        final_url = headers.get("X-Degent-Final-URL", "")
        if status == 200 and final_url and f"/web/{timestamp}" not in final_url:
            return 0, {"error": "wrong_capture_replay", "final_url": final_url}, b"", final_url
        return status, headers, body, url

    def fetch_commoncrawl(self, row: dict[str, Any]) -> tuple[int, dict[str, str], bytes, str]:
        filename = str(row.get("filename", ""))
        offset, length = row.get("offset"), row.get("length")
        if not filename or offset is None or length is None:
            return 0, {"error": "missing_warc_range"}, b"", ""
        url = "https://data.commoncrawl.org/" + filename.lstrip("/")
        start = int(offset)
        end = start + int(length) - 1
        status, headers, body = self.request(url, {"Range": f"bytes={start}-{end}"})
        if status not in (200, 206):
            return status, headers, body, url
        content, target = self._warc_payload_and_target(body)
        if content is None:
            return 0, {"error": "invalid_warc_record"}, body, url
        expected = str(row.get("url", "")).rstrip("/")
        actual = str(target or "").rstrip("/")
        if expected and actual and expected != actual:
            return 0, {"error": "warc_target_mismatch", "expected": expected, "actual": actual}, body, url
        return status, headers, content, url

    @staticmethod
    def _warc_payload_and_target(warc: bytes) -> tuple[bytes | None, str | None]:
        # Parse the bounded WARC response record without requiring optional warcio.
        if warc.startswith(b"\x1f\x8b"):
            try:
                warc = gzip.decompress(warc)
            except OSError:
                return None, None
        boundary = b"\r\n\r\n"
        first = warc.find(boundary)
        if first < 0:
            return None, None
        headers = warc[:first].decode("latin-1", errors="replace")
        match = re.search(r"(?im)^Content-Length:\s*(\d+)\s*$", headers)
        if not match:
            return None, None
        target = re.search(r"(?im)^WARC-Target-URI:\s*(\S+)\s*$", headers)
        record_type = re.search(r"(?im)^WARC-Type:\s*(\S+)\s*$", headers)
        if record_type and record_type.group(1).lower() != "response":
            return None, target.group(1) if target else None
        record_length = int(match.group(1))
        record = warc[first + 4:first + 4 + record_length]
        if len(record) != record_length:
            return None, None
        http_start = record.find(b"HTTP/")
        if http_start < 0:
            return None, target.group(1) if target else None
        http_header_end = record.find(boundary, http_start)
        if http_header_end < 0:
            return None, target.group(1) if target else None
        if b" 200 " not in record[http_start:http_header_end]:
            return None, target.group(1) if target else None
        payload = record[http_header_end + 4:]
        http_headers = record[http_start:http_header_end].decode("latin-1", errors="replace")
        if re.search(r"(?im)^Content-Encoding:\s*gzip\s*$", http_headers):
            try:
                payload = gzip.decompress(payload)
            except OSError:
                return None, target.group(1) if target else None
        return payload, target.group(1) if target else None

    @staticmethod
    def _warc_payload(warc: bytes) -> bytes | None:
        """Compatibility helper used by tests and small diagnostics."""
        return ArchiveClient._warc_payload_and_target(warc)[0]


def collection_for_period(collections: list[dict[str, Any]], start: str, end: str) -> dict[str, Any] | None:
    matches = collections_for_period(collections, start, end)
    return matches[0] if matches else None


def collections_for_period(collections: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    period_start = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    candidates = []
    for collection in collections:
        def parse_date(value: Any) -> date | None:
            parsed = re.search(r"(20\d{2})-(\d{2})-(\d{2})", str(value))
            if not parsed:
                parsed = re.search(r"(20\d{2})(\d{2})(\d{2})", str(value))
            if not parsed:
                return None
            try:
                return date(int(parsed.group(1)), int(parsed.group(2)), int(parsed.group(3)))
            except ValueError:
                return None

        crawl_start = parse_date(collection.get("from"))
        crawl_end = parse_date(collection.get("to"))
        if not crawl_start or not crawl_end:
            continue
        if crawl_start <= end_date and crawl_end >= period_start:
            candidates.append((min(crawl_end, end_date), collection))
    return [collection for _, collection in sorted(candidates, key=lambda item: (item[0], str(item[1].get("id", ""))), reverse=True)]
