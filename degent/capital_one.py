"""Bounded collection from Capital One's official TalentBrew career site.

Listing searches provide titles/locations, not descriptions. Screen those first;
classify the complete JobPosting description before accepting any evidence.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from .classify import classify_geography, classify_role, role_level
from .store import append_jsonl, clean_html, digest, read_json, safe_id, utc_now, write_json

BASE = "https://www.capitalonecareers.com"
PROVIDER = "capital_one"


def official_url(url: str, *, job: bool = False) -> str:
    parsed = urlsplit(urljoin(BASE, url))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"capitalonecareers.com", "www.capitalonecareers.com"}
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Not an official capitalonecareers.com HTTPS source")
    if job and not re.fullmatch(r"/job/[^/]+/[^/]+/\d+/\d+/?", parsed.path):
        raise ValueError("Not a Capital One job detail URL")
    return urlunsplit(("https", "www.capitalonecareers.com", parsed.path.rstrip("/"), "", ""))


class CareerHTML(HTMLParser):
    def __init__(self, payload: bytes):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.script = None
        self.results = {}
        self.feed(payload.decode("utf-8", errors="replace"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.script = []
        if attrs.get("id") == "search-results":
            self.results = attrs

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None


def listing_jobs(payload: bytes, keyword: str, page: int) -> tuple[list[dict], int, int]:
    parser = CareerHTML(payload)
    attrs = parser.results
    if not attrs or attrs.get("data-keywords", "").casefold() != keyword.casefold():
        raise ValueError(
            "Search metadata missing or keyword ignored; refusing an unfiltered listing"
        )
    if int(attrs.get("data-current-page", 0)) != page:
        raise ValueError("Search page did not advance; refusing repeated first-page results")
    pages = int(attrs.get("data-total-pages", 0))
    total = int(attrs.get("data-total-results", 0))
    source = payload.decode("utf-8", errors="replace")
    section = re.search(
        r'<section\b[^>]*id=["\']search-results-list["\'][^>]*>(.*?)</section>', source, re.S | re.I
    )
    if not section:
        raise ValueError("Search results list missing")
    rows = []
    for link, content in re.findall(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', section[1], re.S | re.I
    ):
        title = re.search(r"<h2[^>]*>(.*?)</h2>", content, re.S | re.I)
        location = re.search(
            r'<span[^>]*class=["\'][^"\']*job-location[^"\']*["\'][^>]*>(.*?)</span>',
            content,
            re.S | re.I,
        )
        if not title:
            continue
        url = official_url(html.unescape(link), job=True)
        rows.append(
            {
                "title": clean_html(title[1].encode()),
                "source_url": url,
                "site_job_id": urlsplit(url).path.split("/")[-1],
                "location": clean_html(location[1].encode()) if location else "",
                "text": "",
                "description_available": False,
            }
        )
    if total and not rows:
        raise ValueError("Search claims jobs but contains no parseable job links")
    return rows, pages, total


def preselect(row: dict, config: dict) -> tuple[bool, str]:
    if config.get("study_version") == 1:
        from .evidence import knowledge_scope
        geo = classify_geography("", "Location: " + row.get("location", ""))
        if geo["status"] == "outside_us_scope":
            from .classify import US_STATES
            location = row.get("location", "").casefold()
            if not any(re.search(r"\b" + re.escape(s) + r"\b", location) for s in US_STATES) and not re.search(r"united states|\bUSA\b", location, re.I):
                return False, "outside_us_scope"
        scope = knowledge_scope(row["title"], row.get("text", ""))
        return scope["decision"] != "out_of_scope", scope["reason"]
    title = row["title"]
    role = classify_role(title, row.get("text", ""), config)
    level = role_level(title)
    levels = config["roles"]["entry_mid_levels"]
    if level not in levels and (
        level != "unknown" or set(levels) <= {"senior", "management", "executive"}
    ):
        return False, "seniority_outside_scope: " + level
    if role.get("matched_exclusions"):
        return False, role["explanation"]
    if (
        classify_geography("", "Location: " + row.get("location", ""))["status"]
        == "outside_us_scope"
    ):
        return False, "outside_us_scope"
    if role["eligible"]:
        return True, "Title/available description matches selected track; confirm full description"
    if role["reason"] == "track_outside_scope":
        return False, role["explanation"]
    # The official search matches description content too. Generic analyst titles
    # need one body lookup; unrelated roles never consume a detail request.
    if not row.get("description_available") and re.search(
        r"\b(business analyst|credit analyst|risk analyst|quantitative analyst|quantitative analysis)\b",
        title,
        re.I,
    ):
        if re.search(r"business analyst", title, re.I) and not set(config["roles"]["tracks"]) & {
            "credit_risk_analytics",
            "fraud_analytics_strategy",
        }:
            return False, "business_analytics_title_outside_selected_modeling_track"
        if (
            re.search(r"quantitative", title, re.I)
            and "quantitative_credit_risk_capital_modeling" not in config["roles"]["tracks"]
        ):
            return False, "quantitative_title_outside_selected_track"
        return (
            True,
            "Description unavailable in listing; analyst title requires credit/model context check",
        )
    return False, role["explanation"]


def job_posting(payload: bytes, url: str) -> dict:
    parser = CareerHTML(payload)
    postings = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
                postings.append(value)
            if "@graph" in value:
                visit(value["@graph"])

    for script in parser.scripts:
        try:
            visit(json.loads(script))
        except json.JSONDecodeError:
            continue
    if len(postings) != 1:
        raise ValueError("Expected one complete JobPosting; page may be closed or a shell")
    job = postings[0]
    organization = job.get("hiringOrganization")
    employer = organization.get("name", "") if isinstance(organization, dict) else ""
    if not re.fullmatch(r"Capital One", str(employer), re.I):
        raise ValueError("JobPosting employer is not Capital One")
    source_url = official_url(job.get("url") or url, job=True)
    if source_url.split("/")[-1] != official_url(url, job=True).split("/")[-1]:
        raise ValueError("JobPosting URL identifies a different vacancy")
    identifier = job.get("identifier") or {}
    posting_id = str(identifier.get("value", "") if isinstance(identifier, dict) else identifier)
    if not re.fullmatch(r"R\d+(?:-\d+)?", posting_id):
        raise ValueError("Missing Capital One requisition ID (R-number)")
    locations = job.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    if not isinstance(locations, list):
        raise ValueError("JobPosting locations are malformed")
    addresses = [
        loc["address"]
        for loc in locations
        if isinstance(loc, dict) and isinstance(loc.get("address"), dict)
    ]
    location = "; ".join(
        ", ".join(
            str(a[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if a.get(k)
        )
        for a in addresses
    )
    text = clean_html(html.unescape(str(job.get("description") or "")).encode())
    title = str(job.get("title") or "").strip()
    if not title or not text:
        raise ValueError("JobPosting title/description missing")
    return {
        "title": title,
        "text": text,
        "location": location,
        "structured_locations": addresses,
        "source_url": source_url,
        "posting_id": posting_id,
        "site_job_id": source_url.split("/")[-1],
        "verified_employer": employer,
        "employment_type": job.get("employmentType"),
        "stated_publication_at": job.get("datePosted"),
        "stated_updated_at": job.get("dateModified"),
        "update_date_source": "JobPosting.dateModified" if job.get("dateModified") else None,
        "publication_date_source": "JobPosting.datePosted" if job.get("datePosted") else None,
        "original_record": job,
    }


class CareerCollector:
    def __init__(
        self,
        config,
        run_id,
        source,
        client,
        directory,
        manifest,
        info,
        checkpoint,
        store_row,
        document_count,
    ):
        self.config, self.source, self.client = config, source, client
        self.directory, self.manifest, self.info = directory, manifest, info
        self.checkpoint, self.store_row, self.document_count = checkpoint, store_row, document_count
        self.root = Path(config["data_dir"]) / safe_id(run_id) / "current-capital-one"
        self.root.mkdir(parents=True, exist_ok=True)
        self.maximum = int(config.get("current_document_cap", 60))
        self.max_pages = int(source.get("max_pages", 4))

    def stop(self, status, **details):
        self.info.update(status=status, **details)
        self.checkpoint()

    def request_available(self):
        if self.client.request_count >= self.client.request_limit:
            self.stop("request_limit")
            return False
        return True

    def listing(self, keyword, page):
        key = keyword + ":" + str(page)
        cached = self.directory / ("listing-capital-one-" + digest(key)[:16] + ".json")
        if cached.exists():
            return read_json(cached)
        if self.document_count() >= self.maximum:
            self.stop("document_limit")
            return None
        if not self.request_available():
            return None
        url = BASE + "/search-jobs?" + urlencode({"k": keyword, "p": page})
        status, headers, payload = self.client.request(url)
        try:
            if status != 200:
                raise ValueError(headers.get("error") or f"HTTP {status}")
            official_url(headers.get("X-Degent-Final-URL", url))
            rows, pages, total = listing_jobs(payload, keyword, page)
        except ValueError as exc:
            self.stop("unavailable", reason=str(exc), failed_url=url)
            return None
        raw = self.root / ("listing-" + digest(key)[:16] + ".html")
        raw.write_bytes(payload)
        data = {
            "jobs": rows,
            "total_pages": pages,
            "total_postings": total,
            "source_url": url,
            "retrieved_at": utc_now(),
            "sha256": digest(payload),
            "raw_file": str(raw),
        }
        write_json(cached, data)
        return data

    def fetch_body(self, candidate, decision, raw):
        if not self.request_available():
            return None
        attempt = {
            "site_job_id": candidate["site_job_id"],
            "url": candidate["source_url"],
            "title": candidate["title"],
            "status": "reserved",
            "at": utc_now(),
        }
        self.manifest.setdefault("current_body_attempts", []).append(attempt)
        self.checkpoint()
        status, headers, payload = self.client.request(candidate["source_url"])
        observed = utc_now()
        attempt.update(http_status=status, retrieved_at=observed)
        if status != 200:
            attempt.update(status="failed", reason=headers.get("error") or f"HTTP {status}")
            decision.update(status="retrieval_failed", reason=attempt["reason"])
            return None
        try:
            official_url(headers.get("X-Degent-Final-URL", candidate["source_url"]), job=True)
        except ValueError as exc:
            attempt.update(status="failed", reason=str(exc))
            self.stop("unavailable", reason=str(exc))
            return None
        # Reserve a saved capture before writing immutable bytes. Interrupted
        # reservations remain charged, and the same capture can resume in its slot.
        write_json(raw.with_suffix(".json"), {"retrieved_at": observed, "sha256": digest(payload)})
        raw.write_bytes(payload)
        attempt["status"] = "retrieved"
        self.manifest["current_documents_saved"] = self.document_count()
        self.checkpoint()
        return payload, observed

    def capture(self, candidate, decision):
        raw = self.root / ("detail-" + candidate["site_job_id"] + ".html")
        meta = raw.with_suffix(".json")
        if not meta.exists() and self.document_count() >= self.maximum:
            self.stop("document_limit")
            return
        if raw.exists() and meta.exists():
            payload, metadata = raw.read_bytes(), read_json(meta)
            if digest(payload) != metadata.get("sha256"):
                decision.update(
                    status="parse_failed",
                    reason="cached_raw_source_hash_mismatch",
                    raw_file=str(raw),
                )
                return
            captured = payload, metadata["retrieved_at"]
        else:
            captured = self.fetch_body(candidate, decision, raw)
        if captured is None:
            return
        payload, observed = captured
        try:
            row = job_posting(payload, candidate["source_url"])
            row.update(raw_source_file=str(raw), raw_source_sha256=digest(payload))
            item, saved = self.store_row(row, PROVIDER, observed, save_rejected=True)
            decision.update(
                status="eligible" if item["body_complete"] else "rejected_after_retrieval",
                reason=item["body_status"],
                classification=item["role_classification"],
                geography=item["geography_status"],
                document_id=item["document_id"],
                duplicate=not saved,
            )
        except ValueError as exc:
            decision.update(status="parse_failed", reason=str(exc), raw_file=str(raw))

    def screen(self, candidate, keyword, page):
        key = candidate["site_job_id"]
        if key in self.info["decisions"]:
            return True
        selected, reason = preselect(candidate, self.config)
        decision = {
            **candidate,
            "keyword": keyword,
            "page": page,
            "selected": selected,
            "selection_reason": reason,
            "at": utc_now(),
        }
        if selected:
            self.capture(candidate, decision)
            if self.info["status"] != "processing":
                return False
        else:
            decision["status"] = "rejected_before_retrieval"
        self.info["decisions"][key] = decision
        append_jsonl(self.directory / "current-selection.jsonl", [decision])
        self.checkpoint()
        return True

    def run(self):
        keywords = self.source.get("keywords", ["credit", "underwriting", "quantitative"])
        if (
            not isinstance(keywords, list)
            or not keywords
            or not all(isinstance(k, str) and 1 <= len(k) <= 100 for k in keywords)
            or not 1 <= self.max_pages <= 20
        ):
            raise ValueError("Invalid Capital One keyword/page configuration")
        for key in ("pages", "decisions", "truncated_searches"):
            self.info.setdefault(key, {})
        self.info.update(
            status="processing",
            selection_note=(
                "Listings have titles/locations only; full descriptions are classified after title screening. "
                "Search coverage is bounded, not exhaustive."
            ),
        )
        for keyword in keywords:
            for page in range(1, self.max_pages + 1):
                data = self.listing(keyword, page)
                if data is None:
                    return
                self.info["pages"][keyword + ":" + str(page)] = {
                    k: data[k]
                    for k in ("total_pages", "total_postings", "source_url", "retrieved_at")
                }
                if data["total_pages"] > self.max_pages:
                    self.info["truncated_searches"][keyword] = {
                        "available_pages": data["total_pages"],
                        "configured_pages": self.max_pages,
                    }
                for candidate in data["jobs"]:
                    if not self.screen(candidate, keyword, page):
                        return
                if page >= data["total_pages"]:
                    break
        failures = sum(
            d["status"] in {"parse_failed", "retrieval_failed"}
            for d in self.info["decisions"].values()
        )
        status = (
            "partial_failure"
            if failures
            else "complete_bounded" if self.info["truncated_searches"] else "complete"
        )
        self.stop(status, failed_documents=failures)


def collect(
    config,
    run_id,
    employer,
    source,
    client,
    directory,
    manifest,
    info,
    save_manifest,
    store_row,
    document_count,
):
    """Collect an official source within the run's shared request/document budget."""
    if employer["id"] != "capital_one":
        raise ValueError("Capital One adapter requires employer capital_one")
    CareerCollector(
        config,
        run_id,
        source,
        client,
        directory,
        manifest,
        info,
        save_manifest,
        store_row,
        document_count,
    ).run()
