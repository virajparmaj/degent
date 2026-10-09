"""Bounded DOM/sitemap/structured extraction. Jobseek conventions: MIT notice."""

from __future__ import annotations

import gzip
import html
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

import soupsieve
from bs4 import BeautifulSoup

from .collection_http import validate_public_url
from .companies import domain, normalize
from .store import body_usability, clean_html, digest

PARSER_VERSION = "posting-structured-dom-v1"
CONFIG_KEYS = {
    "version",
    "allowed_origins",
    "container",
    "job_links",
    "fields",
    "next_page",
    "load_more",
    "empty_selector",
    "empty_text",
    "render",
    "exhaustive",
    "max_pages",
    "advertised_total_selector",
}


def canonical(url):
    parsed = urlsplit(urldefrag(url)[0])
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"trid", "gh_src", "lever-origin"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), urlencode(query), ""))


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def validate_config(config, url):
    if not isinstance(config, dict) or set(config) - CONFIG_KEYS:
        raise ValueError("invalid_dom_configuration")
    result = {
        "version": 1,
        "allowed_origins": [origin(url)],
        "job_links": "a[href]",
        "fields": {},
        "render": False,
        "exhaustive": False,
        "max_pages": 20,
        **config,
    }
    if (
        result["version"] != 1
        or type(result["max_pages"]) is not int
        or not 1 <= result["max_pages"] <= 100
    ):
        raise ValueError("invalid_dom_bounds_or_version")
    for flag in ("render", "exhaustive"):
        if type(result[flag]) is not bool:
            raise ValueError("invalid_dom_boolean")
    origins = result["allowed_origins"]
    if not isinstance(origins, list) or not origins or len(origins) > 20:
        raise ValueError("invalid_dom_origins")
    for item in origins:
        validate_public_url(item, resolve=False)
        if origin(item) != item:
            raise ValueError("allowed_origins_require_exact_origin")
    if not isinstance(result["fields"], dict) or set(result["fields"]) - {
        "title",
        "description",
        "location",
        "employer",
        "employment_type",
        "date_posted",
    }:
        raise ValueError("invalid_dom_fields")
    selectors = [
        result.get(k)
        for k in (
            "container",
            "job_links",
            "next_page",
            "load_more",
            "empty_selector",
            "advertised_total_selector",
        )
    ]
    for field in result["fields"].values():
        if isinstance(field, dict):
            if set(field) != {"selector", "attribute"} or field["attribute"] not in {
                "content",
                "datetime",
                "alt",
                "title",
            }:
                raise ValueError("invalid_dom_field_attribute")
            selectors.append(field["selector"])
        else:
            selectors.append(field)
    for selector in selectors:
        if selector is not None:
            if not isinstance(selector, str) or not 1 <= len(selector) <= 256:
                raise ValueError("invalid_dom_selector")
            soupsieve.compile(selector)
    return result


def allowed(url, origins):
    validate_public_url(url, resolve=False)
    return origin(url) in origins


def job_url(url):
    p = urlsplit(url)
    path = p.path.lower().rstrip("/")
    return (
        (
            bool(
                re.search(
                    r"/(jobs?|careers?|positions?|openings?|opportunities|vacancies|requisitions?)/.+",
                    path,
                )
            )
            or (
                (p.hostname or "").startswith("jobs.")
                and bool(re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]{6,}", path))
            )
        )
        and not re.search(
            r"/(login|sign-?in|talent|privacy|register|search|saved-jobs|job-alerts|job-categories|apply)(/|$)",
            path,
        )
        and not path.endswith((".css", ".js", ".png", ".jpg", ".svg", ".woff", ".pdf"))
    )


def json_objects(soup):
    """Only parse JSON, never evaluate script/assignment expressions."""
    count = 0
    for script in soup.find_all("script")[:100]:
        raw = script.string or script.get_text()
        if len(raw) > 2_000_000:
            continue
        values = []
        if (
            script.get("type") in {"application/ld+json", "application/json"}
            or script.get("id") == "__NEXT_DATA__"
        ):
            try:
                values.append(json.loads(raw))
            except (ValueError, RecursionError):
                continue
        else:
            match = re.search(
                r"(?:window\.)?(?:__DATA__|__INITIAL_STATE__|__APOLLO_STATE__|ashbyAppConfig)\s*=\s*",
                raw,
            )
            if match:
                try:
                    values.append(json.JSONDecoder().raw_decode(raw[match.end() :].lstrip())[0])
                except (ValueError, RecursionError):
                    pass
        stack = [(v, 0) for v in values]
        while stack and count < 5000:
            value, depth = stack.pop()
            count += 1
            if depth > 20:
                continue
            if isinstance(value, dict):
                yield value
                stack.extend((v, depth + 1) for v in value.values() if isinstance(v, (dict, list)))
            elif isinstance(value, list):
                stack.extend((v, depth + 1) for v in value[:1000])


def listing(payload, url, config):
    config = validate_config(config, url)
    soup = BeautifulSoup(payload, "html.parser")
    containers = soup.select(config["container"]) if config.get("container") else [soup]
    if len(containers) != 1:
        raise ValueError("dom_listing_container_ambiguous_or_missing")
    links = []
    for node in containers[0].select(config["job_links"]):
        if node.get("href"):
            target = canonical(urljoin(url, node["href"]))
            if job_url(target):
                try:
                    if allowed(target, config["allowed_origins"]):
                        links.append(target)
                except ValueError:
                    continue
    for obj in json_objects(soup):
        if "JobPosting" in (
            [obj.get("@type")] if isinstance(obj.get("@type"), str) else (obj.get("@type") or [])
        ):
            target = obj.get("url")
            if isinstance(target, str) and allowed(urljoin(url, target), config["allowed_origins"]):
                links.append(canonical(urljoin(url, target)))
    links = list(dict.fromkeys(links))
    links.sort(
        key=lambda u: (
            0
            if re.search(r"/job/|/careers/details/", urlsplit(u).path, re.I)
            else 1 if re.search(r"/(jobs|positions|openings)/", urlsplit(u).path, re.I) else 2
        )
    )
    links = links[:1000]
    next_links = soup.select(config["next_page"]) if config.get("next_page") else []
    if len(next_links) > 1:
        raise ValueError("dom_pagination_ambiguous")
    next_url = (
        urljoin(url, next_links[0].get("href", ""))
        if next_links and next_links[0].get("href")
        else None
    )
    if next_url and not allowed(next_url, config["allowed_origins"]):
        raise ValueError("dom_pagination_origin_rejected")
    empty = soup.select(config["empty_selector"]) if config.get("empty_selector") else []
    explicit_empty = bool(
        empty
        and (
            not config.get("empty_text")
            or config["empty_text"].casefold() in empty[0].get_text().casefold()
        )
    )
    return links, next_url, explicit_empty


def advertised_total(payload, config):
    selector = config.get("advertised_total_selector")
    if not selector:
        return None
    nodes = BeautifulSoup(payload, "html.parser").select(selector)
    if len(nodes) != 1:
        raise ValueError("dom_advertised_total_missing_or_ambiguous")
    text = nodes[0].get_text(" ", strip=True).replace(",", "")
    matches = re.findall(r"\d+", text)
    if len(matches) != 1 or int(matches[0]) > 100_000:
        raise ValueError("dom_advertised_total_invalid")
    return int(matches[0])


def employer_matches(value, company):
    if isinstance(value, dict):
        name = value.get("name", "")
        references = [value.get("url"), value.get("sameAs")]
        for reference in references:
            candidates = reference if isinstance(reference, list) else [reference]
            for url in candidates:
                if isinstance(url, str) and name and company.get("website"):
                    try:
                        validate_public_url(url, resolve=False)
                        if domain(url) == domain(company["website"]):
                            return True
                    except ValueError:
                        pass
        value = name
    return normalize(str(value)) in {
        normalize(company["name"]),
        *(normalize(a) for a in company.get("aliases", [])),
    }


def extract(payload, url, company, config=None):
    soup = BeautifulSoup(payload, "html.parser")
    candidates = []
    for obj in json_objects(soup):
        types = obj.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list) or "JobPosting" not in types:
            # Recognized embedded JSON must identify both this URL and this employer.
            title = obj.get("title") or obj.get("jobTitle")
            description = (
                obj.get("descriptionHtml") or obj.get("jobDescription") or obj.get("description")
            )
            employer = (
                obj.get("hiringOrganization") or obj.get("companyName") or obj.get("employer")
            )
            target = obj.get("jobUrl") or obj.get("url")
            if not (
                isinstance(title, str)
                and isinstance(description, str)
                and isinstance(target, str)
                and employer_matches(employer, company)
            ):
                continue
            obj = {
                **obj,
                "@type": "JobPosting",
                "title": title,
                "description": description,
                "hiringOrganization": employer,
                "url": target,
            }
        target = obj.get("url") or obj.get("mainEntityOfPage")
        if isinstance(target, dict):
            target = target.get("@id")
        if target and canonical(urljoin(url, str(target))) != canonical(url):
            continue
        if employer_matches(obj.get("hiringOrganization"), company):
            candidates.append(obj)
    if len(candidates) > 1:
        raise ValueError("ambiguous_jobposting_url_employer")
    if candidates:
        obj = candidates[0]
        expiry = obj.get("validThrough")
        if expiry:
            try:
                expires = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires < datetime.now(timezone.utc):
                    raise ValueError("jobposting_expired_validThrough")
            except (TypeError, OverflowError) as exc:
                raise ValueError("invalid_jobposting_validThrough") from exc
        title, description = obj.get("title"), obj.get("description")
        if not isinstance(title, str) or not isinstance(description, str):
            raise ValueError("invalid_jobposting_title_description")
        location = obj.get("jobLocation") or obj.get("applicantLocationRequirements") or {}
        text = clean_html(html.unescape(description).encode())
        if not title.strip() or re.fullmatch(
            r"(?:careers|jobs|open (?:roles|positions)|join us)", title.strip(), re.I
        ):
            raise ValueError("jobposting_requires_meaningful_posting_title")
        row = {
            "title": title,
            "text": text,
            "location": json.dumps(location, ensure_ascii=False),
            "employment_type": obj.get("employmentType"),
            "stated_publication_at": obj.get("datePosted"),
            "publication_date_source": "JobPosting.datePosted" if obj.get("datePosted") else None,
            "original_record": obj,
        }
    else:
        config = validate_config(config or {}, url)
        fields = config["fields"]
        if not {"title", "description", "employer"} <= fields.keys():
            raise ValueError("missing_matching_jobposting_or_dom_configuration")
        containers = soup.select(config["container"]) if config.get("container") else [soup]
        if len(containers) != 1:
            raise ValueError("dom_posting_container_ambiguous_or_missing")
        data = {}
        for name, selector in fields.items():
            spec = selector if isinstance(selector, dict) else {"selector": selector}
            nodes = containers[0].select(spec["selector"])
            if len(nodes) != 1:
                raise ValueError("dom_field_missing_or_ambiguous:" + name)
            value = (
                nodes[0].get(spec["attribute"], "")
                if spec.get("attribute")
                else nodes[0].get_text("\n", strip=True)
            )
            if not isinstance(value, str) or not value.strip():
                raise ValueError("dom_field_missing_or_ambiguous:" + name)
            data[name] = value.strip()
        if not job_url(url) or re.fullmatch(
            r"(?:careers|jobs|open (?:roles|positions)|join us)", data["title"], re.I
        ):
            raise ValueError("dom_requires_posting_specific_url_and_title")
        brand = re.search(r"(?im)^Brand:\s*([^\n]+)", data["description"])
        if brand and not employer_matches(brand[1].strip(), company):
            raise ValueError("dom_posting_brand_mismatch")
        if not employer_matches(data["employer"], company):
            raise ValueError("dom_employer_mismatch")
        row = {
            "title": data["title"],
            "text": data["description"],
            "location": data.get("location", ""),
            "employment_type": data.get("employment_type"),
            "stated_publication_at": data.get("date_posted"),
            "publication_date_source": (
                "configured DOM date_posted" if data.get("date_posted") else None
            ),
            "original_record": data,
        }
    # Check actual description, not a collector-added company label.
    candidate_text = re.sub(r"(?im)^\s*Your Opportunity\s*$", "Responsibilities", row["text"])
    usable, reason = body_usability(candidate_text, company["name"])
    # Structured hiringOrganization is independent employer evidence even if body omits its name.
    if reason == "employer_not_found":
        usable, reason = body_usability(company["name"] + "\n" + candidate_text, company["name"])
    if not usable:
        raise ValueError("incomplete_description:" + reason)
    row.update(
        source_url=canonical(url),
        posting_id=canonical(url),
        verified_employer=company["name"],
        parser_version=PARSER_VERSION,
    )
    return row


def sitemap_urls(payload, maximum=1000):
    if payload.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            payload = stream.read(4_000_001)
    if (
        len(payload) > 4_000_000
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        raise ValueError("unsafe_or_oversized_sitemap")
    root = ET.fromstring(payload)
    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in {"urlset", "sitemapindex"}:
        raise ValueError("invalid_sitemap_root")
    urls = [n.text.strip() for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "loc" and n.text]
    return urls[:maximum], kind == "sitemapindex", len(urls) > maximum
