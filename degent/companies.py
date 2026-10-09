"""Persistent local company catalog. Lookup and addition never fetch postings."""

from __future__ import annotations

import copy
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import digest, operation_lock, read_json, utc_now, write_json

_WRITE_LOCK = threading.RLock()


@contextmanager
def library_lock(root):
    with _WRITE_LOCK:
        deadline = time.monotonic() + 10
        while True:
            manager = operation_lock(root)
            try:
                manager.__enter__()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            manager.__exit__(None, None, None)


PROVIDERS = {"greenhouse", "ashby", "workday", "capital_one", "lever", "dom", "sitemap"}
SEED_VERSION = "jobseek-selected-leads-2026-10-08-v1"
LEADS = {
    "stripe": ("https://stripe.com", "https://boards.greenhouse.io/stripe"),
    "block": ("https://block.xyz", "https://boards.greenhouse.io/block"),
    "paypal": ("https://www.paypal.com", "https://paypal.wd1.myworkdayjobs.com/jobs"),
}


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def public_url(value: str) -> str:
    from .collection_http import validate_public_url

    value = value.strip()
    if value and "://" not in value:
        value = "https://" + value
    validate_public_url(value, resolve=False)
    return value.rstrip("/")


def domain(value: str) -> str:
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


def source_from_url(url: str) -> dict:
    """Derive provider configuration only from explicit URL components."""
    url = public_url(url)
    parsed = urlsplit(url)
    host, parts = parsed.hostname, [p for p in parsed.path.split("/") if p]
    source = {"url": url, "status": "unverified", "provenance": "user supplied URL"}
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        token = (
            parse_qs(parsed.query).get("for", [None])[0]
            if parts[:1] == ["embed"]
            else (parts[0] if parts else None)
        )
        if token:
            source.update(provider="greenhouse", board=token)
    elif host == "boards-api.greenhouse.io" and parts[:2] == ["v1", "boards"] and len(parts) >= 3:
        source.update(provider="greenhouse", board=parts[2])
    elif host in {"jobs.lever.co", "jobs.eu.lever.co", "api.lever.co", "api.eu.lever.co"}:
        token = (
            parts[2]
            if host.startswith("api.") and parts[:2] == ["v0", "postings"] and len(parts) >= 3
            else parts[0] if host.startswith("jobs.") and parts else None
        )
        if token and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token):
            source.update(
                provider="lever", board=token, region="eu" if ".eu." in host else "global"
            )
            source["url"] = (
                "https://jobs." + ("eu." if source["region"] == "eu" else "") + "lever.co/" + token
            )
    elif host == "jobs.ashbyhq.com" and parts:
        source.update(provider="ashby", board=parts[0])
    elif (
        host == "api.ashbyhq.com" and parts[:2] == ["posting-api", "job-board"] and len(parts) >= 3
    ):
        source.update(provider="ashby", board=parts[2])
    elif match := re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host or ""):
        if parts and re.fullmatch(r"[a-z]{2}-[a-z]{2}", parts[0], re.I):
            parts = parts[1:]
        if parts:
            source.update(
                provider="workday",
                company=match[1],
                wd_instance=match[2],
                site=parts[0],
                board=match[1] + "/" + parts[0],
            )
    elif host in {"www.capitalonecareers.com", "capitalonecareers.com"}:
        source.update(
            provider="capital_one",
            board="capitalonecareers",
            keywords=["credit", "underwriting", "quantitative"],
            max_pages=4,
        )
    if source.get("provider") in {"greenhouse", "ashby"} and not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}", source["board"]
    ):
        raise ValueError("Invalid board identifier in URL.")
    if "provider" not in source:
        source.update(
            provider="unknown",
            status="unsupported",
            reason="Supply an explicit Greenhouse, Ashby, Lever, Workday or Capital One URL, or configure a DOM source.",
        )
    source["id"] = source_identity(source)
    return source


def source_identity(source: dict) -> str:
    key = [source.get(k, "") for k in ("provider", "board", "company", "wd_instance", "site")]
    if source.get("provider") in {"dom", "sitemap"}:
        key[1] = str(key[1]).split("#", 1)[0].rstrip("/")
    if source.get("provider") == "lever":
        key.append(source.get("region", "global"))
    if source.get("provider") not in PROVIDERS or source.get("provider") in {"dom", "sitemap"}:
        key.append(source.get("url", "").split("#", 1)[0].rstrip("/"))
    return "source-" + digest(str(key))[:24]


def verification_results(source, evidence=()):
    """Expose separate outcomes with shared attempt timestamps and auditable references."""
    references = [
        {k: e[k] for k in ("url", "sha256", "evidence_file") if e.get(k)}
        for e in evidence
        if e.get("url")
    ]
    stamp = source.get("last_verified_at")
    fields = {
        "availability": "availability",
        "ownership": "ownership",
        "collector_capability": "collector_capability",
        "sampling": "sampling_result",
        "inventory": "inventory_completeness",
    }
    results = {}
    for dimension, field in fields.items():
        outcome = source.get(field, "not_checked")
        results[dimension] = {
            "result": outcome,
            "recorded_at": stamp,
            "checked_at": None if outcome in {"not_checked", "not_attempted", "unknown"} else stamp,
            "reason_code": outcome,
            "reason": (
                source.get("ownership_basis")
                if dimension == "ownership"
                else (
                    source.get("collector_capability_basis")
                    if dimension == "collector_capability"
                    else source.get("verification_outcome")
                )
            ),
            "evidence_refs": [] if dimension == "inventory" else references,
        }
    return results


class CompanyLibrary:
    def __init__(self, config: dict):
        self.config = config
        self.root = Path(
            config.get("library_dir") or Path(config.get("data_dir", "data")) / "company-library"
        )
        self.path = self.root / "companies.json"

    def seed(self) -> list[dict]:
        rows = copy.deepcopy(self.config.get("employers", []))
        for row in rows:
            row.setdefault("aliases", [])
            row.setdefault("website", LEADS.get(row["id"], ("", ""))[0])
            row.setdefault("group", "unknown")
            sources = row.setdefault("current_sources", [])
            if not sources and row["id"] in LEADS:
                src = source_from_url(LEADS[row["id"]][1])
                src.update(provenance=SEED_VERSION + "; notices/Jobseek-JOB-DATA.txt")
                sources.append(src)
            for src in sources:
                src.setdefault("id", source_identity(src))
                src.setdefault("status", "configured")
                if src["status"] == "configured":
                    src.setdefault("ownership_basis", "existing Degent configuration")
                src.setdefault("provenance", "existing Degent configuration")
                if not src.get("url"):
                    if src["provider"] == "greenhouse":
                        src["url"] = "https://boards.greenhouse.io/" + src["board"]
                    elif src["provider"] == "ashby":
                        src["url"] = "https://jobs.ashbyhq.com/" + src["board"]
                    elif src["provider"] == "capital_one":
                        src["url"] = "https://www.capitalonecareers.com"
            row.setdefault(
                "provenance", "Degent configuration; selected Jobseek leads where labeled"
            )
        return rows

    def all(self) -> list[dict]:
        # Reading a catalog has no network side effects or initialization writes.
        return read_json(self.path)["companies"] if self.path.exists() else self.seed()

    def search(self, query: str = "", page: int = 0, size: int = 10) -> dict:
        query = normalize(query)
        rows = [
            r
            for r in self.all()
            if query
            in normalize(
                " ".join([r["name"], r["id"], domain(r.get("website", "")), *r.get("aliases", [])])
            )
        ]
        rows.sort(key=lambda r: r["name"].casefold())
        size = min(50, max(1, int(size)))
        pages = max(1, (len(rows) + size - 1) // size)
        page = min(max(0, int(page)), pages - 1)
        return {
            "items": [self.describe(r) for r in rows[page * size : (page + 1) * size]],
            "total": len(rows),
            "page": page,
            "pages": pages,
        }

    @staticmethod
    def describe(row: dict) -> dict:
        result = copy.deepcopy(row)
        for source in result.get("current_sources", []):
            source["verification_results"] = verification_results(
                source, result.get("verification_evidence") or []
            )
        states = {s.get("status", "configured") for s in row.get("current_sources", [])}
        result["source_status"] = next(
            (
                s
                for s in ("verified", "configured", "unverified", "unavailable", "unsupported")
                if s in states
            ),
            "unsupported",
        )
        result["source_help"] = (
            "Verify career sources before collection."
            if states
            else "Add a supported career-board URL. Imports and archives remain available."
        )
        return result

    def get(self, identity: str) -> dict:
        return (
            next((self.describe(r) for r in self.all() if r["id"] == identity), None)
            or self._missing()
        )

    @staticmethod
    def _missing():
        raise ValueError("Company not found in the local library.")

    def add(
        self, name: str, website: str = "", career_url: str = "", aliases=None, group="unknown"
    ) -> dict:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 150 or not normalize(name):
            raise ValueError("Enter a company name of 1–150 characters.")
        aliases = aliases or []
        if (
            not isinstance(aliases, list)
            or len(aliases) > 30
            or not all(isinstance(a, str) and len(a) <= 150 for a in aliases)
        ):
            raise ValueError("Provide at most 30 short aliases.")
        if not isinstance(group, str) or len(group) > 100:
            raise ValueError("Provide a short manual classification or unknown.")
        website = public_url(website) if website else ""
        source = source_from_url(career_url) if career_url else None
        with library_lock(self.root):
            rows = self.all()
            names = {normalize(v) for v in [name, *aliases]}
            matches = [
                r
                for r in rows
                if names & {normalize(v) for v in [r["name"], *r.get("aliases", [])]}
                or (website and domain(website) == domain(r.get("website", "")))
                or (
                    source
                    and any(s.get("id") == source["id"] for s in r.get("current_sources", []))
                )
            ]
            if len(matches) > 1:
                raise ValueError(
                    "Name/domain/source match different companies. Resolve the conflicting aliases first."
                )
            if (
                matches
                and source
                and not (
                    names
                    & {normalize(v) for v in [matches[0]["name"], *matches[0].get("aliases", [])]}
                )
                and not (website and domain(website) == domain(matches[0].get("website", "")))
            ):
                raise ValueError(
                    "This board belongs to another library company; unrelated names cannot become aliases."
                )
            row = (
                matches[0]
                if matches
                else {
                    "id": "company-" + digest(domain(website) or normalize(name))[:24],
                    "name": name.strip(),
                    "aliases": [],
                    "website": website,
                    "group": group or "unknown",
                    "paths": [],
                    "current_sources": [],
                    "created_at": utc_now(),
                    "provenance": "manual local addition",
                }
            )
            if not matches:
                rows.append(row)
            row["aliases"] = sorted(
                set(
                    [
                        *row.get("aliases", []),
                        *aliases,
                        *([name.strip()] if normalize(name) != normalize(row["name"]) else []),
                    ]
                )
            )
            if website and (not row.get("website") or domain(website) == domain(row["website"])):
                row["website"] = website
            if group and group != "unknown":
                row["group"] = group
            if source and not any(s.get("id") == source["id"] for s in row["current_sources"]):
                if any(
                    source["id"] == s.get("id")
                    for r in rows
                    if r["id"] != row["id"]
                    for s in r.get("current_sources", [])
                ):
                    raise ValueError("This board belongs to another library company.")
                row["current_sources"].append(source)
            if career_url and not row.get("paths"):
                row["paths"] = [career_url.removeprefix("https://").rstrip("/") + "/*"]
            row["updated_at"] = utc_now()
            saved = (
                read_json(self.path)
                if self.path.exists()
                else {"version": 1, "seed_version": SEED_VERSION}
            )
            write_json(self.path, {**saved, "companies": rows})
            return self.describe(row)

    def configure_source(self, identity, url, provider, dom_config):
        from .generic_sources import validate_config

        if provider not in {"dom", "sitemap"}:
            raise ValueError("Generic source provider must be dom or sitemap")
        url = public_url(url)
        validated = validate_config(dom_config, url)
        source = {
            "provider": provider,
            "url": url,
            "board": url,
            "dom_config": validated,
            "status": "unverified",
            "provenance": "manual validated generic source configuration",
        }
        source["id"] = source_identity(source)
        with library_lock(self.root):
            saved = (
                read_json(self.path)
                if self.path.exists()
                else {"version": 1, "seed_version": SEED_VERSION, "companies": self.seed()}
            )
            current = next((r for r in saved["companies"] if r["id"] == identity), None)
            if not current:
                raise ValueError("Company not found")
            if any(
                source_identity(s) == source["id"]
                for r in saved["companies"]
                if r["id"] != identity
                for s in r.get("current_sources", [])
            ):
                raise ValueError("Board belongs to another library company")
            prior = next((s for s in current["current_sources"] if s["id"] == source["id"]), None)
            if prior:
                prior.update(source)
            else:
                current["current_sources"].append(source)
            write_json(self.path, saved)
            return self.describe(current)

    def verify(self, identity: str, *, client=None, attempt_id=None, **budgets) -> dict:
        import uuid

        from .collection_http import CollectionClient
        from .source_verification import verify_sources

        attempt_id = attempt_id or uuid.uuid4().hex
        row = self.get(identity)
        if any(e.get("attempt_id") == attempt_id for e in row.get("verification_history", [])):
            return row
        for key, value in budgets.items():
            if key in {"requests", "bytes", "seconds"} and (type(value) is not int or value < 1):
                raise ValueError("Verification budgets must be positive integers")
        client = client or CollectionClient(
            self.config,
            requests=int(budgets.get("requests", self.config.get("verification_requests", 20))),
            byte_limit=int(budgets.get("bytes", self.config.get("verification_bytes", 12_000_000))),
            seconds=float(budgets.get("seconds", self.config.get("verification_seconds", 120))),
        )
        checked = verify_sources(row, client)
        if hasattr(client, "save_attempt"):
            client.save_attempt(checked)
        return self.persist_verification(identity, checked, attempt_id)

    def persist_verification(self, identity, checked, attempt_id):
        with library_lock(self.root):
            saved = (
                read_json(self.path)
                if self.path.exists()
                else {"version": 1, "seed_version": SEED_VERSION, "companies": self.seed()}
            )
            rows = saved["companies"]
            current = next(r for r in rows if r["id"] == identity)
            if any(
                e.get("attempt_id") == attempt_id for e in current.get("verification_history", [])
            ):
                return self.describe(current)
            owners = {
                source_identity(s): r["name"]
                for r in rows
                if r["id"] != identity
                for s in r.get("current_sources", [])
                if s.get("provider") in PROVIDERS
            }
            accepted, conflicts = [], []
            for source in checked["current_sources"]:
                source = copy.deepcopy(source)
                owner = owners.get(source_identity(source))
                if owner:
                    reason = "Board belongs to another library company: " + owner
                    source.update(
                        status="unsupported",
                        ownership="conflict",
                        verification_outcome=reason,
                        reason=reason,
                    )
                    conflicts.append(source)
                else:
                    accepted.append(source)
            for source in [*accepted, *conflicts]:
                source["verification_results"] = verification_results(
                    source, checked.get("verification_evidence", [])
                )
            updates = {s["id"]: s for s in [*accepted, *conflicts]}
            old = {s["id"]: s for s in current.get("current_sources", [])}
            # Preserve pre-existing fields and the original source ID while updating verification.
            current["current_sources"] = [{**s, **updates.get(s["id"], {})} for s in old.values()]
            current["current_sources"].extend(s for s in accepted if s["id"] not in old)
            event = {
                "attempt_id": attempt_id,
                "at": utc_now(),
                "sources": copy.deepcopy([*accepted, *conflicts]),
                **{
                    k: copy.deepcopy(v)
                    for k, v in checked.items()
                    if (k.startswith("verification_") and k != "verification_history")
                    or k == "discovery_outcome"
                },
            }
            event["source_conflicts"] = conflicts
            history = current.setdefault("verification_history", [])
            if not history:
                history.append(
                    {
                        "attempt_id": "legacy-library-state",
                        "at": None,
                        "kind": "preserved_prior_state",
                        "sources": list(old.values()),
                        "source_conflicts": copy.deepcopy(current.get("source_conflicts", [])),
                    }
                )
            history.append(event)
            current.update(
                discovery_outcome=(
                    "; ".join(sorted({s["verification_outcome"] for s in conflicts}))
                    if conflicts
                    else checked.get("discovery_outcome")
                ),
                source_conflicts=conflicts,
                latest_verification_attempt=attempt_id,
            )
            for key in (
                "verification_evidence",
                "verification_usage",
                "verification_destinations",
                "verification_remaining_action",
                "verification_exclusions",
                "verification_vendor_leads",
            ):
                current[key] = checked.get(key)
            write_json(self.path, {**saved, "companies": rows})
            return self.describe(current)
