"""Bounded source verification, independent of research inventory/model extraction."""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from .collection_http import BudgetReached
from .companies import domain, normalize, source_from_url, source_identity
from .generic_sources import (
    canonical,
    extract,
    job_url,
    json_objects,
    listing,
    origin,
    sitemap_urls,
)
from .store import digest, utc_now

LEADS_FILE = Path(__file__).resolve().parent.parent / "config" / "company-source-leads.json"
EXCLUDED = re.compile(
    r"/(?:login|sign-?in|register|talent(?:-community)?|privacy|cookies|terms|apply)(?:/|$|\?)",
    re.I,
)
ATS_URL = re.compile(
    r'https://(?:[a-z0-9_.-]+\.)?(?:greenhouse\.io|ashbyhq\.com|lever\.co|myworkdayjobs\.com)/[^\s"<>\\]+',
    re.I,
)


def embedded_ats_links(soup):
    """Recognize bounded literal recruiting config; never evaluate JavaScript."""
    result = []
    for script in soup.find_all("script")[:100]:
        text = script.string or script.get_text()
        if len(text) > 2_000_000:
            continue
        match = re.search(
            r"window\.leverJobsOptions\s*=\s*\{[^}]{0,1000}accountName\s*:\s*['\"]([A-Za-z0-9_-]{1,128})['\"]",
            text,
        )
        if match:
            result.append("https://jobs.lever.co/" + match[1])
        constants = dict(
            re.findall(
                r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]{0,63})\s*=\s*['\"]([A-Za-z0-9_.-]{1,128})['\"]\s*;",
                text,
            )
        )
        for template in re.findall(
            r"`(https://(?:api\.ashbyhq\.com/posting-api/job-board/|api\.(?:eu\.)?lever\.co/v0/postings/|boards-api\.greenhouse\.io/v1/boards/)\$\{([A-Za-z_$][A-Za-z0-9_$]{0,63})\})`",
            text,
        ):
            name = re.search(r"\$\{([^}]+)\}", template[0])[1]
            if name in constants:
                result.append(template[0].replace("${" + name + "}", constants[name]))
    return result


def exception_kind(company):
    name = normalize(company["name"])
    if name.startswith("discover"):
        return "shared_parent_board_requires_discover_filter"
    if name.startswith("airbase"):
        return "shared_parent_board_requires_airbase_attribution"
    return {
        "portfolio_jobs": "portfolio_jobs_not_employer_inventory",
        "hiring_info": "hiring_info_not_job_inventory",
    }.get(company.get("career_link_type"))


def verify_sources(company: dict, client) -> dict:
    company = copy.deepcopy(company)
    for generic in company.get("current_sources", []):
        if generic.get("provider") in {"dom", "sitemap"}:
            generic.update(
                status="unverified",
                collector_capability="requires_configuration",
                sampling_result="not_attempted",
                ownership="unverified",
                ownership_basis=None,
                ownership_evidence=None,
                availability="unknown",
                verification_outcome="not_checked_in_latest_attempt",
                samples=[],
                last_verified_at=utc_now(),
            )
    start = time.monotonic()
    evidence, exclusions, destinations, pages = [], [], [], {}
    owned, queue, seen = {}, [], set()
    external_posting_links = []
    website = company.get("website", "")
    initial_source_ids = {s["id"] for s in company.get("current_sources", [])}
    special = exception_kind(company)
    sources = company.setdefault("current_sources", [])

    def add_source(source, basis=None):
        if not any(s["id"] == source["id"] for s in sources):
            sources.append(source)
        if basis:
            owned[source["id"]] = basis

    def enqueue(url, basis, priority=2):
        if url:
            url = urldefrag(url)[0]
        if (
            not url
            or canonical(url) in seen
            or any(canonical(q[1]) == canonical(url) for q in queue)
        ):
            return
        if (
            urlsplit(url)
            .path.lower()
            .endswith((".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".woff", ".pdf"))
        ):
            exclusions.append({"url": url, "reason": "non_page_asset"})
            return
        if EXCLUDED.search(url):
            exclusions.append({"url": url, "reason": "login_talent_or_non_inventory_link"})
            return
        queue.append((priority, url, basis))
        queue.sort(key=lambda q: q[0])

    def fetch(url, **kwargs):
        at = utc_now()
        try:
            status, headers, body = client.request(url, **kwargs)
            final = headers.get("X-Degent-Final-URL", url)
            item = {
                "url": url,
                "final_url": final,
                "status": status,
                "retrieved_at": at,
                "sha256": digest(body),
                "bytes": len(body),
                "redirects": json.loads(headers.get("X-Degent-Redirects", "[]")),
            }
            if headers.get("error"):
                item["reason"] = headers["error"]
            evidence.append(item)
            # Batch clients persist bounded exact verification bytes separately from research.
            if hasattr(client, "save_evidence"):
                item["evidence_file"] = client.save_evidence(body, item)
            pages[url] = (status, headers, body)
            pages[final] = (status, headers, body)
            return status, headers, body
        except (ValueError, RuntimeError, OSError) as exc:
            evidence.append(
                {
                    "url": url,
                    "retrieved_at": at,
                    "reason": str(exc),
                    **getattr(client, "last_response", {}),
                }
            )
            raise

    # Independently inspect the supplied website and explicit career destination.
    enqueue(website, "official company website", 0)
    explicit = company.get("career_url")
    if explicit:
        enqueue(explicit, "supplied careers destination; ownership pending", 0)
        try:
            add_source(source_from_url(explicit))
        except ValueError as exc:
            exclusions.append({"url": explicit, "reason": str(exc)})
    for source in list(sources):
        if source.get("url") and (
            source["provider"] == "unknown" or domain(source["url"]) == domain(website)
        ):
            enqueue(source["url"], "supplied source destination; ownership pending", 1)
    if LEADS_FILE.exists():
        for lead in json.loads(LEADS_FILE.read_text()).get("leads", []):
            if lead["company_id"] == company["id"]:
                candidate = source_from_url(lead["url"])
                candidate["provenance"] = lead["provenance"]
                add_source(candidate)
    budget_error = None
    initial_urls = {canonical(u) for u in (website, explicit) if u}
    while queue and len(seen) < 8:
        if owned and initial_urls <= seen:
            break
        _, url, basis = queue.pop(0)
        seen.add(canonical(url))
        try:
            status, headers, body = fetch(url)
            final = headers.get("X-Degent-Final-URL", url)
            soup = BeautifulSoup(body, "html.parser")
            challenge = bool(
                re.search(
                    r"checking your browser|just a moment|captcha|access denied|enable javascript and cookies",
                    soup.get_text(" ", strip=True)[:4000],
                    re.I,
                )
            )
            trusted = (
                (domain(url) == domain(website) or domain(url).endswith("." + domain(website)))
                if website
                else False
            )
            trusted = trusted or basis.startswith("linked from official")
            destinations.append(
                {
                    "url": url,
                    "final_url": final,
                    "availability": (
                        "available" if status == 200 and not challenge else "unavailable"
                    ),
                    "ownership": (
                        "verified"
                        if trusted and not (special or "").startswith("shared_parent")
                        else "ambiguous"
                    ),
                    "official_destination": trusted,
                    "destination_domain": domain(final),
                    "inventory_employer_attribution": special or "not_checked",
                    "basis": basis,
                    "reason": special or ("challenge_page" if challenge else f"HTTP {status}"),
                }
            )
            if status != 200 or challenge:
                continue
            direct = source_from_url(final)
            if direct["provider"] != "unknown":
                add_source(
                    direct,
                    (
                        {"url": url, "final_url": final, "sha256": digest(body), "basis": basis}
                        if trusted and not special
                        else None
                    ),
                )
            links = [
                (n.get("href") or n.get("src"), n.get_text(" ", strip=True))
                for n in soup.find_all(["a", "iframe"])[:2000]
            ]
            links += [
                (n.get("action"), "Search jobs form")
                for n in soup.find_all("form", action=True)
                if n.get("method", "get").lower() == "get"
                and re.search(r"job|career|search", n["action"], re.I)
            ]
            # Explicit provider URLs in literal JSON/config strings are leads, not executed scripts.
            links += [(u, "literal recruiting configuration") for u in embedded_ats_links(soup)]
            links += [
                (u.replace("&amp;", "&"), "embedded ATS URL")
                for u in ATS_URL.findall(body.decode(errors="replace"))[:100]
            ]
            for obj in json_objects(soup):
                for key in ("url", "sameAs", "careersUrl", "jobBoardUrl", "iframeUrl"):
                    value = obj.get(key)
                    if isinstance(value, str):
                        links.append((value, "structured URL"))
            for value, label in links:
                if not value:
                    continue
                absolute = urljoin(final, value)
                try:
                    candidate_url = absolute
                    if absolute.startswith("http://") and urlsplit(absolute).hostname in {
                        "boards.greenhouse.io",
                        "job-boards.greenhouse.io",
                        "jobs.lever.co",
                        "jobs.eu.lever.co",
                        "jobs.ashbyhq.com",
                    }:
                        candidate_url = "https://" + absolute[len("http://") :]
                        exclusions.append(
                            {
                                "url": absolute,
                                "reason": "explicit_http_ATS_lead; HTTPS candidate only, HTTP not fetched",
                            }
                        )
                    source = source_from_url(candidate_url)
                    if re.search(r"talent community|newsletter|sign up|subscribe", label, re.I):
                        exclusions.append(
                            {"url": absolute, "reason": "talent_community_or_signup_not_inventory"}
                        )
                        continue
                    if EXCLUDED.search(absolute):
                        if (
                            re.search(r"/login(?:/|$|\?)", absolute, re.I)
                            and source["id"] in initial_source_ids
                            and source["provider"] != "unknown"
                            and trusted
                            and not special
                        ):
                            owned[source["id"]] = {
                                "url": final,
                                "link": absolute,
                                "sha256": digest(body),
                                "basis": "official login link establishes ATS relationship; public inventory availability probed independently",
                            }
                        exclusions.append(
                            {"url": absolute, "reason": "login_talent_or_non_inventory_link"}
                        )
                        continue
                    if source["provider"] != "unknown":
                        source["provenance"] = "explicit link at " + final
                        add_source(
                            source,
                            (
                                {
                                    "url": final,
                                    "link": absolute,
                                    "sha256": digest(body),
                                    "basis": "official career link",
                                }
                                if trusted and not special
                                else None
                            ),
                        )
                    elif job_url(absolute) and trusted and not special:
                        external_posting_links.append(
                            {"url": absolute, "from_url": final, "sha256": digest(body)}
                        )
                    elif (
                        not job_url(absolute)
                        or re.search(r"open-(?:opportunities|positions)|search", absolute, re.I)
                    ) and re.search(
                        r"career|open (?:roles|positions)|jobs|join (?:us|our)",
                        label + " " + absolute,
                        re.I,
                    ):
                        enqueue(
                            absolute,
                            (
                                "linked from official " + final
                                if trusted
                                else "unverified destination link " + final
                            ),
                            (
                                1
                                if re.search(
                                    r"jobs\.|open-(?:opportunities|positions)|search|job-search",
                                    absolute + " " + label,
                                    re.I,
                                )
                                else 3
                            ),
                        )
                except (ValueError, TypeError) as exc:
                    exclusions.append({"url": absolute, "reason": str(exc)})
        except BudgetReached as exc:
            budget_error = str(exc)
            break
        except (ValueError, RuntimeError, OSError) as exc:
            exclusions.append({"url": url, "reason": str(exc)})

    # Probe recognized providers before spending remaining allowance on generic fallback.
    for source in sources:
        if source["provider"] in {"unknown", "dom", "sitemap"}:
            continue
        source.update(
            last_verified_at=utc_now(),
            availability="unknown",
            ownership="unverified",
            collector_capability="supported",
            sampling_result="not_attempted",
            samples=[],
            inventory_completeness="not_checked",
            collector_capability_basis="implemented native adapter; description sampling reported separately",
            availability_kind={
                "greenhouse": "provider_board_metadata",
                "ashby": "board_shell",
                "lever": "public_posting_list",
                "workday": "public_posting_list",
                "capital_one": "official_careers_html",
            }.get(source["provider"]),
        )
        source["ownership_basis"] = None  # prior success remains in history, never fresh proof
        try:
            provider = source["provider"]
            ownership = owned.get(source["id"])
            valid = False
            if provider == "greenhouse":
                status, headers, body = fetch(
                    "https://boards-api.greenhouse.io/v1/boards/" + source["board"]
                )
                data = json.loads(body) if status == 200 else {}
                valid = (
                    isinstance(data, dict)
                    and isinstance(data.get("name"), str)
                    and bool(data["name"])
                )
                if valid and normalize(
                    re.sub(
                        r"(?:,?\s+)(?:inc\.?|incorporated|corp\.?|corporation|llc|ltd\.?)$",
                        "",
                        data["name"],
                        flags=re.I,
                    )
                ) in {
                    normalize(company["name"]),
                    *(normalize(a) for a in company.get("aliases", [])),
                }:
                    ownership = {
                        "url": evidence[-1]["url"],
                        "sha256": digest(body),
                        "basis": "matching provider board name",
                        "name": data["name"],
                    }
            elif provider == "workday":
                from .workday import api_base

                status, headers, body = fetch(
                    api_base(source) + "/jobs",
                    data={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
                )
                data = json.loads(body) if status == 200 else {}
                valid = (
                    isinstance(data, dict)
                    and isinstance(data.get("jobPostings"), list)
                    and type(data.get("total")) is int
                    and data["total"] >= 0
                )
                source["sampling_result"] = (
                    "extractor_not_sampled_empty"
                    if valid and data["total"] == 0
                    else "not_attempted"
                )
            elif provider == "lever":
                from .lever import api_base, page

                status, headers, body = fetch(
                    api_base(source) + "?limit=2&skip=0", headers={"Accept": "application/json"}
                )
                rows, done = page(body, source, limit=2) if status == 200 else ([], False)
                valid = status == 200
                source["sampling_result"] = (
                    "extractor_not_sampled_empty"
                    if valid and not rows
                    else "parsed" if valid else "failed"
                )
                source["sample_titles"] = [r["title"] for r in rows]
                source["samples"] = [
                    {
                        "url": r["source_url"],
                        "title": r["title"],
                        "description_sha256": digest(r["text"]),
                        "description_characters": len(r["text"]),
                        "ownership_basis": ownership,
                        "description_excerpt": r["text"][:250],
                    }
                    for r in rows
                ]
            elif provider == "ashby":
                board_url = "https://jobs.ashbyhq.com/" + source["board"]
                status, headers, body = pages.get(board_url) or fetch(board_url)
                soup = BeautifulSoup(body, "html.parser")
                titles = [n.get_text(" ", strip=True) for n in soup.find_all(["title", "h1"])]
                titles += [n.get("content", "") for n in soup.select('meta[property="og:title"]')]
                valid = (
                    status == 200
                    and (b"ashby" in body.lower())
                    and not re.search(
                        r"just a moment|checking your browser|job board not found",
                        soup.get_text(" ")[:4000],
                        re.I,
                    )
                )
                names = {
                    normalize(company["name"]),
                    *(normalize(a) for a in company.get("aliases", [])),
                }

                def board_title(title):
                    value = re.sub(
                        r"^(?:jobs|careers|open positions)\s+(?:at|with)\s+", "", title, flags=re.I
                    )
                    value = re.sub(r"(?:\s+|\s*[|–-]\s*)(?:jobs|careers)$", "", value, flags=re.I)
                    return normalize(value)

                if any(board_title(title) in names for title in titles):
                    ownership = {
                        "url": source["url"],
                        "sha256": digest(body),
                        "basis": "matching Ashby board heading",
                        "titles": titles,
                    }
                source["provider_probe_kind"] = "board_shell; full inventory API not requested"
                source["provider_api_availability"] = "not_probed"
            elif provider == "capital_one":
                status, headers, body = pages.get(source["url"]) or fetch(source["url"])
                valid = status == 200 and b"capital one" in body.lower() and b"job" in body.lower()
                if company["id"] == "capital_one" and valid:
                    ownership = {
                        "url": source["url"],
                        "sha256": digest(body),
                        "basis": "official Capital One careers destination",
                    }
            if special:
                ownership = None
                source["collector_capability"] = "unsupported"
            source.update(
                availability="available" if valid else "unavailable",
                ownership="verified" if ownership else "ambiguous",
                ownership_evidence=ownership,
            )
            if valid and ownership:
                source.update(
                    status="verified",
                    ownership_basis=ownership["basis"],
                    verification_outcome="provider availability and current ownership checked",
                )
            elif valid:
                source.update(
                    status="unsupported" if special else "unverified",
                    verification_outcome=special
                    or "provider responds; current company ownership missing",
                )
            else:
                source.update(
                    status="unavailable",
                    verification_outcome=f"invalid_provider_response_or_HTTP_{status}",
                )
        except BudgetReached as exc:
            budget_error = str(exc)
            source.update(
                status="unverified",
                verification_outcome="verification_budget_exhausted:" + str(exc),
            )
        except (ValueError, RuntimeError, OSError, TypeError, AttributeError, KeyError) as exc:
            source.update(
                status="unavailable", availability="unavailable", verification_outcome=str(exc)
            )

    if not any(s.get("status") == "verified" for s in sources) and not special and not budget_error:
        company["verification_external_posting_links"] = external_posting_links
        generic_fallback(company, sources, pages, destinations, owned, fetch, client, evidence)
    if not budget_error:
        budget_error = next(
            (
                e.get("reason")
                for e in evidence
                if any(
                    k in str(e.get("reason", ""))
                    for k in ("request_limit", "byte_limit", "runtime_limit", "cancelled", "batch_")
                )
            ),
            None,
        )
    for source in sources:
        if source["provider"] == "unknown":
            dest = next(
                (d for d in destinations if canonical(d["url"]) == canonical(source["url"])), {}
            )
            source.update(
                availability=dest.get("availability", "unknown"),
                ownership=dest.get("ownership", "ambiguous"),
                collector_capability="unsupported",
                sampling_result="not_attempted",
                inventory_completeness="not_checked",
                last_verified_at=utc_now(),
                verification_outcome=special or source.get("reason") or "adapter_missing",
            )
    company.update(
        verification_evidence=evidence,
        verification_destinations=destinations,
        verification_exclusions=exclusions[:200],
        verification_usage={
            "requests": client.request_count,
            "bytes": client.bytes_used,
            "seconds": time.monotonic() - start,
            "browser": client.browser_usage,
        },
        discovery_outcome=(
            "verification_budget_exhausted:" + budget_error
            if budget_error
            else special
            or (
                "inspected career sources; outcomes recorded"
                if any(d.get("availability") == "available" for d in destinations)
                or any(
                    s.get("availability") == "available" and s.get("provider") != "unknown"
                    for s in sources
                )
                else "no_page_successfully_inspected"
            )
        ),
    )
    company["verification_remaining_action"] = remaining_action(
        company, pages, evidence, budget_error, special
    )
    return company


def remaining_action(company, pages, evidence, budget_error, special):
    sources = company["current_sources"]
    if any(s.get("status") == "verified" for s in sources):
        return "none"
    if special:
        return special
    if budget_error:
        return "budget_exhausted:" + budget_error + "; retry with an explicitly recorded allowance"
    reasons = [e.get("reason", "") for e in evidence if e.get("reason")]
    browser = next((r for r in reasons if "browser_" in r), None)
    vendor_patterns = {
        "jobs.gem.com": "Gem",
        "apply.workable.com": "Workable",
        "smartrecruiters.com": "SmartRecruiters",
        "icims.com": "iCIMS",
        "eightfold.ai": "Eightfold",
        "jobvite.com": "Jobvite",
        "hibob.com": "HiBob",
        "bamboohr.com": "BambooHR",
        "paylocity.com": "Paylocity",
        "phenompeople.com": "Phenom",
        "successfactors.com": "SAP SuccessFactors",
        "ukg.com": "UKG",
        "ultipro.com": "UKG",
        "teamtailor.com": "Teamtailor",
    }
    raw = " ".join(
        payload.decode(errors="replace").lower() for _, _, payload in list(pages.values())[:20]
    )
    vendors = sorted({label for marker, label in vendor_patterns.items() if marker in raw})
    company["verification_vendor_leads"] = vendors
    failed = next(
        (
            s.get("verification_outcome")
            for s in sources
            if s.get("provider") in {"dom", "sitemap"} and s.get("sampling_result") == "failed"
        ),
        None,
    )
    if any(
        "posting_HTTP_403" in str(sample.get("reason", ""))
        for s in sources
        for sample in s.get("samples", [])
    ):
        return "blocked_posting_HTTP_403; recheck public access before validating the extractor"
    if failed:
        return (
            failed
            + "; inspect posting URL/container and configure complete employer-specific fields"
            + ("; observed vendor: " + ", ".join(vendors) if vendors else "")
        )
    native = [s for s in sources if s.get("provider") not in {"unknown", "dom", "sitemap"}]
    if any(s.get("availability") == "available" for s in native):
        return "ownership_ambiguous: provider responds; obtain a current official relationship or matching employer identity"
    if native:
        return (
            "; ".join(sorted({s.get("verification_outcome", "invalid_feed") for s in native}))
            + "; locate the current official recruiting destination"
        )
    available = [
        d
        for d in company.get("verification_destinations", [])
        if d.get("availability") == "available"
    ]
    if not available:
        return (
            "blocked_or_unreachable: "
            + (
                "; ".join(reasons[:3])
                or "; ".join(
                    d.get("reason", "unknown") for d in company.get("verification_destinations", [])
                )
            )
            + "; recheck public access/destination"
        )
    if browser:
        return browser + "; resolve browser dependency/configuration or use an explicit public feed"
    if vendors:
        return (
            "adapter_or_configuration_missing: observed "
            + ", ".join(vendors)
            + "; configure posting-specific DOM/JSON-LD or implement the observed vendor adapter"
        )
    return "no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor"


def generic_fallback(company, sources, pages, destinations, owned, fetch, client, evidence):
    from .collection_http import validate_public_url

    candidates = []
    origins = {
        origin(d["final_url"])
        for d in destinations
        if d["ownership"] == "verified" and d["availability"] == "available"
    }
    origins.update(
        origin(link["url"]) for link in company.get("verification_external_posting_links", [])
    )
    for dest in destinations:
        if dest["ownership"] != "verified" or dest["availability"] != "available":
            continue
        url = dest["final_url"]
        if not re.search(r"career|jobs|join|work", url, re.I) and not any(
            s.get("provider") in {"dom", "sitemap"} and s.get("url") == url for s in sources
        ):
            continue
        body = pages[url][2]
        cfg = {"allowed_origins": sorted(origins)}
        existing = next(
            (
                s
                for s in sorted(
                    sources, key=lambda s: not bool(s.get("dom_config", {}).get("fields"))
                )
                if s.get("provider") in {"dom", "sitemap"}
                and canonical(s.get("url", "")) == canonical(url)
            ),
            None,
        )
        if existing:
            cfg.update(existing.get("dom_config", {}))
        try:
            links, _, empty = listing(body, url, cfg)
            if links:
                candidates.append((url, links, cfg, "dom"))
            elif empty and existing:
                existing.update(
                    availability="available",
                    ownership="verified",
                    collector_capability="requires_sampling",
                    sampling_result="extractor_not_sampled_empty",
                    verification_outcome="explicit_empty_state; extractor not sampled",
                    last_verified_at=utc_now(),
                )
            elif not empty and re.search(
                rb"__NEXT_DATA__|id=[\"'](?:root|app)|enable javascript", body, re.I
            ):
                from .browser_sources import render

                try:
                    rendered, trace = render(url, client, cfg)
                    evidence.append(
                        {"url": url, "kind": "browser", "trace": trace, "sha256": digest(rendered)}
                    )
                    links, _, empty = listing(rendered, url, cfg)
                    if links:
                        cfg["render"] = True
                        candidates.append((url, links, cfg, "dom"))
                except (RuntimeError, ValueError, OSError) as exc:
                    evidence.append({"url": url, "kind": "browser", "reason": str(exc)})
        except (RuntimeError, ValueError, OSError) as exc:
            evidence.append({"url": url, "reason": str(exc)})
    # Discover sitemaps only if DOM produced no posting candidates.
    if not candidates:
        sitemap_queue, visited, examined = [], set(), 0
        for site in sorted(origins)[:2]:
            try:
                status, _, body = fetch(site + "/robots.txt")
                if status == 200:
                    sitemap_queue += [
                        (u.strip(), 0)
                        for u in re.findall(
                            r"^Sitemap:\s*(\S+)", body.decode(errors="replace"), re.I | re.M
                        )
                    ]
            except (RuntimeError, ValueError, OSError):
                pass
        for url, (_, _, body) in list(pages.items())[:16]:
            soup = BeautifulSoup(body, "html.parser")
            sitemap_queue += [
                (urljoin(url, n.get("href", "")), 0)
                for n in soup.find_all("a", href=True)
                if "sitemap" in n["href"].lower()
            ]
        while sitemap_queue and len(visited) < 6 and examined < 1000:
            url, depth = sitemap_queue.pop(0)
            if url in visited or depth > 2:
                continue
            visited.add(url)
            try:
                validate_public_url(url, resolve=False)
                if origin(url) not in origins:
                    evidence.append({"url": url, "reason": "unrelated_sitemap_origin"})
                    continue
                status, _, body = fetch(url)
                if status != 200:
                    continue
                urls, index, capped = sitemap_urls(body, 1000 - examined)
                examined += len(urls)
                if index:
                    sitemap_queue += [(u, depth + 1) for u in urls]
                else:
                    links = [u for u in urls if origin(u) in origins and job_url(u)]
                    if links:
                        candidates.append(
                            (url, links, {"allowed_origins": sorted(origins)}, "sitemap")
                        )
                        break
            except (RuntimeError, ValueError, OSError, ETError) as exc:
                evidence.append({"url": url, "reason": str(exc)})
    candidates.sort(
        key=lambda candidate: (
            0
            if re.search(r"jobs\.|open-(?:opportunities|positions)|search", candidate[0], re.I)
            else 1
        )
    )
    for url, links, cfg, provider in candidates[:2]:
        source = {
            "provider": provider,
            "url": url,
            "board": url,
            "dom_config": cfg,
            "provenance": "bounded official destination discovery",
            "status": "unverified",
            "availability": "available",
            "ownership": "verified",
            "ownership_basis": "current official destination and matching sampled posting employer",
            "ownership_chain": company.get("verification_external_posting_links", []),
            "collector_capability": "requires_configuration",
            "inventory_completeness": "not_checked",
            "last_verified_at": utc_now(),
            "sampling_result": "failed",
            "samples": [],
        }
        prior = next(
            (
                s
                for s in sources
                if s.get("provider") == provider
                and canonical(s.get("url", "")) == canonical(url)
                and s.get("dom_config", {}).get("fields") == cfg.get("fields")
            ),
            None,
        )
        source["id"] = prior["id"] if prior else source_identity(source)
        for link in links[:2]:
            try:
                status, headers, body = fetch(link)
                if status != 200:
                    raise ValueError("posting_HTTP_" + str(status))
                if cfg.get("render"):
                    from .browser_sources import render

                    body, trace = render(link, client, cfg)
                    evidence.append(
                        {
                            "url": link,
                            "kind": "browser_sample",
                            "trace": trace,
                            "sha256": digest(body),
                        }
                    )
                    if hasattr(client, "save_evidence"):
                        evidence[-1]["evidence_file"] = client.save_evidence(body, evidence[-1])
                row = extract(body, headers.get("X-Degent-Final-URL", link), company, cfg)
                source["samples"].append(
                    {
                        "url": link,
                        "title": row["title"],
                        "description_sha256": digest(row["text"]),
                        "description_characters": len(row["text"]),
                        "verified_employer": row["verified_employer"],
                    }
                )
            except (ValueError, RuntimeError, OSError) as exc:
                source["samples"].append({"url": link, "reason": str(exc)})
        passed = sum("title" in sample for sample in source["samples"])
        if passed and all("title" in sample for sample in source["samples"]):
            source.update(
                status="verified",
                collector_capability="supported",
                sampling_result="passed",
                verification_outcome="posting-specific description and employer samples passed",
            )
        else:
            source["verification_outcome"] = "generic_extractor_sampling_failed: " + "; ".join(
                s.get("reason", "passed") for s in source["samples"]
            )
        old = next((s for s in sources if s["id"] == source["id"]), None)
        if old:
            old.update(source)
        else:
            sources.append(source)


# XML parsing failures are ordinary source outcomes.
from xml.etree.ElementTree import ParseError as ETError
