"""Verification boundaries and adapters with synthetic evidence only."""

from __future__ import annotations

import copy
import gzip
import io
import json
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from degent.collection import collect
from degent.collection_http import BudgetReached, CollectionClient, validate_public_url
from degent.companies import CompanyLibrary, source_from_url, source_identity
from degent.config import load_config, observation_in_period
from degent.generic_sources import extract, listing, sitemap_urls, validate_config
from degent.lever import api_base, page
from degent.source_verification import verify_sources
from degent.store import digest, read_json, read_jsonl, write_json
from degent.verification_batch import VerificationBatch


def body():
    return (
        "Responsibilities\n"
        + "You will analyze credit risk and collaborate with partners. " * 15
        + "\nQualifications\nTwo years of experience. SQL required."
    )


def posting(i="one", region="global", suffix=""):
    return {
        "id": str(i),
        "text": "Credit Risk Analyst II",
        "description": body() + suffix,
        "lists": [{"text": "Benefits", "content": "<li>Health insurance</li>"}],
        "additional": "Closing section",
        "hostedUrl": f"https://jobs.{ 'eu.' if region == 'eu' else ''}lever.co/example/{i}",
        "categories": {"location": "United States", "commitment": "Full time"},
        "createdAt": 1672531200000,
    }


def structured(url="https://example.test/jobs/1", company="Example", **extra):
    return {
        "@type": "JobPosting",
        "url": url,
        "title": "Credit Risk Analyst II",
        "description": body(),
        "hiringOrganization": {"name": company},
        "jobLocation": {"address": {"addressCountry": "US"}},
        **extra,
    }


def html(*objects):
    return (
        '<script type="application/ld+json">' + json.dumps(list(objects)) + "</script>"
    ).encode()


class FixtureClient(CollectionClient):
    def __init__(self, responses, **options):
        super().__init__({"host_delay_seconds": 0, "retry_count": 0}, **options)
        self.responses = responses

    def request(self, url, headers=None, **kwargs):
        self.remaining()
        event = {"url": url, "reserved_bytes": min(4_000_000, self.byte_limit - self.bytes_used)}
        if self.reserve:
            self.reserve(event)
        self.request_count += 1
        result = (
            self.responses(url)
            if callable(self.responses)
            else self.responses.get(url, (404, {}, b"missing"))
        )
        if isinstance(result, Exception):
            raise result
        status, response_headers, payload = result
        self.bytes_used += len(payload)
        if self.finish:
            self.finish({**event, "bytes": len(payload)})
        return status, response_headers, payload


class TransportBoundaryTests(unittest.TestCase):
    def client(self, **values):
        return CollectionClient({"retry_count": 0, "host_delay_seconds": 0, **values}, requests=20)

    def test_relative_cross_domain_redirect_and_provenance(self):
        client = self.client()
        with (
            patch("degent.collection_http.public_address", return_value="8.8.8.8") as dns,
            patch.object(
                client,
                "attempt",
                side_effect=[
                    (302, {"Location": "/careers"}, b"a"),
                    (301, {"Location": "https://ats.test/jobs"}, b"b"),
                    (200, {}, b"final"),
                ],
            ),
        ):
            status, headers, payload = client.request("https://example.test")
        self.assertEqual(status, 200)
        self.assertEqual(payload, b"final")
        self.assertEqual(headers["X-Degent-Final-URL"], "https://ats.test/jobs")
        self.assertEqual(len(json.loads(headers["X-Degent-Redirects"])), 2)
        self.assertEqual(client.request_count, 3)
        self.assertEqual(client.bytes_used, 7)
        self.assertEqual(dns.call_count, 3)

    def test_unsafe_redirects_never_connect(self):
        targets = [
            "http://public.test/",
            "https://user:secret@public.test/",
            "https://127.0.0.1/",
            "https://169.254.169.254/",
            "https://10.0.0.1/",
            "https://[::1]/",
            "https://[fe80::1]/",
            "https://[fc00::1]/",
            "https://public.test:444/",
        ]
        for target in targets:
            with self.subTest(target=target):
                client = self.client()
                with (
                    patch("degent.collection_http.public_address", return_value="8.8.8.8"),
                    patch.object(
                        client, "attempt", return_value=(302, {"Location": target}, b"")
                    ) as attempt,
                ):
                    with self.assertRaises(ValueError):
                        client.request("https://public.test/")
                self.assertEqual(attempt.call_count, 1)
                self.assertTrue(client.last_response["redirects"][-1]["rejected_reason"])

    def test_cycles_limits_post_and_retry_budgets(self):
        for config, responses in [
            ({}, [(302, {"Location": "/"}, b"")]),
            (
                {"verification_redirect_hops": 1},
                [(302, {"Location": "/a"}, b""), (302, {"Location": "/b"}, b"")],
            ),
        ]:
            client = self.client(**config)
            with (
                patch("degent.collection_http.public_address", return_value="8.8.8.8"),
                patch.object(client, "attempt", side_effect=responses),
            ):
                with self.assertRaises(ValueError):
                    client.request("https://public.test/")
        for status, target in [(302, "/new"), (307, "https://other.test/new")]:
            client = self.client()
            with (
                patch("degent.collection_http.public_address", return_value="8.8.8.8"),
                patch.object(client, "attempt", return_value=(status, {"Location": target}, b"")),
            ):
                with self.assertRaisesRegex(ValueError, "unsafe_post"):
                    client.request("https://public.test/", data={"x": 1})
        client = self.client(retry_count=3)
        client.request_limit = 1
        with (
            patch("degent.collection_http.public_address", return_value="8.8.8.8"),
            patch.object(client, "attempt", return_value=(503, {}, b"failure")),
            patch("degent.collection_http.time.sleep"),
        ):
            with self.assertRaises(BudgetReached):
                client.request("https://public.test/")
        self.assertEqual(client.request_count, 1)

    def test_rebinding_mixed_dns_and_cancellation(self):
        client = self.client()
        with (
            patch(
                "degent.collection_http.socket.getaddrinfo",
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
                ],
            ),
            patch.object(client, "attempt") as attempt,
        ):
            with self.assertRaises(ValueError):
                client.request("https://public.test/")
            attempt.assert_not_called()
        client.cancelled = lambda: True
        with self.assertRaisesRegex(BudgetReached, "cancelled"):
            client.request("https://public.test/")


class ParserCoverageTests(unittest.TestCase):
    def test_lever_regions_ids_empty_errors_and_description(self):
        source = source_from_url("https://jobs.lever.co/example/one")
        eu = source_from_url("https://api.eu.lever.co/v0/postings/example/one")
        self.assertNotEqual(source_identity(source), source_identity(eu))
        self.assertIn("api.eu.lever.co", api_base(eu))
        rows, done = page(json.dumps([posting()]).encode(), source)
        self.assertTrue(done)
        self.assertIn("Benefits", rows[0]["text"])
        self.assertIn("Closing section", rows[0]["text"])
        self.assertIsNone(rows[0]["stated_publication_at"])
        self.assertEqual(page(b"[]", source), ([], True))
        for data in [
            b"<html>shell</html>",
            b"{}",
            b'{"error":"not found"}',
            json.dumps([posting(), posting()]).encode(),
            json.dumps([{**posting(), "id": ""}]).encode(),
            json.dumps([{**posting(), "hostedUrl": "https://jobs.lever.co/other/one"}]).encode(),
        ]:
            with self.subTest(data=data[:60]), self.assertRaises(ValueError):
                page(data, source)
        with self.assertRaises(ValueError):
            page(json.dumps([posting()]).encode(), source, known=["one"])
        _, done = page(json.dumps([posting()]).encode(), source, limit=1)
        self.assertFalse(done)

    def test_matching_jobposting_arrays_graph_ambiguity_dates(self):
        company = {"name": "Example", "aliases": []}
        row = extract(
            html({"@graph": [structured(company="Other"), structured(datePosted="2023-01-01")]}),
            "https://example.test/jobs/1",
            company,
        )
        self.assertEqual(row["publication_date_source"], "JobPosting.datePosted")
        self.assertEqual(row["verified_employer"], "Example")
        for objects in [
            (structured(), structured()),
            (structured(url="https://example.test/jobs/2"),),
            (structured(company="Other"),),
            (structured(description="Too short"),),
        ]:
            with self.subTest(objects=len(objects)), self.assertRaises(ValueError):
                extract(html(*objects), "https://example.test/jobs/1", company)
        row = extract(html(structured()), "https://example.test/jobs/1", company)
        self.assertNotIn("stated_publication_at", {k: v for k, v in row.items() if v})

    def test_embedded_json_is_parsed_and_never_executed(self):
        obj = {
            "job": {
                "title": "Credit Risk Analyst II",
                "descriptionHtml": body(),
                "companyName": "Example",
                "jobUrl": "https://example.test/jobs/1",
            }
        }
        payload = (
            '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(obj) + "</script>"
        ).encode()
        self.assertEqual(
            extract(payload, "https://example.test/jobs/1", {"name": "Example"})["title"],
            "Credit Risk Analyst II",
        )
        with self.assertRaises(ValueError):
            extract(
                b"<script>window.__DATA__ = evil()</script>",
                "https://example.test/jobs/1",
                {"name": "Example"},
            )

    def test_dom_selectors_empty_matches_pagination_and_origins(self):
        config = {
            "job_links": "main a[href]",
            "next_page": "a.next",
            "empty_selector": ".empty",
            "empty_text": "No openings",
        }
        payload = b'<main><a href="/jobs/1">Job</a><a href="/login">login</a><a href="/jobs/1">Duplicate</a></main><a class="next" href="?page=2">Next</a>'
        rows, next_url, empty = listing(payload, "https://example.test/careers", config)
        self.assertEqual(rows, ["https://example.test/jobs/1"])
        self.assertTrue(next_url.endswith("?page=2"))
        self.assertFalse(empty)
        self.assertTrue(
            listing(b'<p class="empty">No openings</p>', "https://example.test/careers", config)[2]
        )
        with self.assertRaises(ValueError):
            listing(
                payload + b'<a class="next" href="?page=3">Next</a>',
                "https://example.test/careers",
                config,
            )
        for cfg in [
            {"javascript": "evil()"},
            {"allowed_origins": ["http://example.test"]},
            {"max_pages": 0},
            {"job_links": "["},
        ]:
            with self.subTest(cfg=cfg), self.assertRaises(Exception):
                validate_config(cfg, "https://example.test")

    def test_dom_fields_require_unique_posting_and_employer(self):
        cfg = {
            "container": "main",
            "fields": {"title": "h1", "description": ".description", "employer": ".employer"},
        }
        payload = (
            '<main><h1>Credit Risk Analyst II</h1><p class="employer">Example</p><div class="description">'
            + body()
            + "</div></main>"
        ).encode()
        self.assertEqual(
            extract(payload, "https://example.test/jobs/1", {"name": "Example"}, cfg)[
                "verified_employer"
            ],
            "Example",
        )
        for altered in [
            payload + payload,
            payload.replace(b"Example", b"Other"),
            payload.replace(b"<h1>", b"<h2>"),
        ]:
            with self.assertRaises(ValueError):
                extract(altered, "https://example.test/jobs/1", {"name": "Example"}, cfg)

    def test_sitemap_namespace_indexes_bounds_gzip_and_entities(self):
        payload = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.test/child.xml</loc></sitemap></sitemapindex>'
        urls, index, bounded = sitemap_urls(payload)
        self.assertTrue(index)
        self.assertFalse(bounded)
        self.assertEqual(len(urls), 1)
        self.assertEqual(sitemap_urls(gzip.compress(payload)), sitemap_urls(payload))
        _, _, bounded = sitemap_urls(
            b"<urlset><url><loc>a</loc></url><url><loc>b</loc></url></urlset>", 1
        )
        self.assertTrue(bounded)
        for payload in [
            b"<broken",
            b'<!DOCTYPE x [<!ENTITY a "x">]><urlset/>',
            b"<html/>",
            gzip.compress(b"x" * 4_000_001),
        ]:
            with self.assertRaises(Exception):
                sitemap_urls(payload)


class VerificationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config.update(
            data_dir=str(self.root / "data"),
            run_dir=str(self.root / "runs"),
            host_delay_seconds=0,
            retry_count=0,
        )
        self.library = CompanyLibrary(self.config)

    def company(
        self, name="Example", website="https://example.test", career="https://jobs.lever.co/example"
    ):
        return self.library.add(name, website, career)

    def test_external_official_link_provider_ownership_and_history(self):
        c = self.company()

        def response(url):
            if url == "https://example.test":
                return 200, {}, b'<a href="https://careers.other.test/roles">Careers</a>'
            if url == "https://careers.other.test/roles":
                return 200, {}, b'<iframe src="https://jobs.lever.co/example"></iframe>'
            if "api.lever.co" in url:
                return 200, {}, b"[]"
            return 404, {}, b""

        first = self.library.verify(c["id"], client=FixtureClient(response), attempt_id="one")
        self.assertEqual(first["source_status"], "verified")
        source = first["current_sources"][0]
        self.assertEqual(source["sampling_result"], "extractor_not_sampled_empty")
        second = self.library.verify(
            c["id"], client=FixtureClient(lambda _: (404, {}, b"")), attempt_id="two"
        )
        self.assertNotEqual(second["source_status"], "verified")
        self.assertEqual(second["verification_history"][1]["sources"][0]["status"], "verified")
        self.assertEqual(len(second["verification_history"]), 3)
        duplicate = self.library.verify(
            c["id"],
            client=FixtureClient(lambda _: self.fail("idempotent retry fetched")),
            attempt_id="two",
        )
        self.assertEqual(len(duplicate["verification_history"]), 3)

    def test_provider_without_ownership_and_misleading_success(self):
        c = self.company()
        checked = self.library.verify(
            c["id"],
            client=FixtureClient(
                lambda u: (200, {}, b"[]") if "api.lever.co" in u else (200, {}, b"<p>Contact</p>")
            ),
        )
        self.assertEqual(checked["source_status"], "unverified")
        self.assertEqual(checked["current_sources"][0]["availability"], "available")
        c["current_sources"][0]["ownership_basis"] = "old assertion"
        result = verify_sources(c, FixtureClient(lambda _: (403, {}, b"blocked")))
        self.assertNotEqual(result["current_sources"][0]["ownership"], "verified")
        self.assertIn("no_page", result["discovery_outcome"])

    def test_company_exceptions_preserve_brand_notes(self):
        for name, kind in [
            ("Discover (Capital One)", "careers"),
            ("Airbase (Paylocity)", "careers"),
            ("QED Investors", "portfolio_jobs"),
            ("Increase", "hiring_info"),
        ]:
            c = {
                "id": normalize_for_test(name),
                "name": name,
                "website": "https://example.test",
                "career_url": "https://example.test/careers",
                "career_link_type": kind,
                "notes": "retained note",
                "current_sources": [],
            }
            result = verify_sources(
                c,
                FixtureClient(
                    lambda _: (200, {}, b'<a href="https://jobs.lever.co/example">Jobs</a>')
                ),
            )
            self.assertFalse(any(s["status"] == "verified" for s in result["current_sources"]))
            self.assertEqual(result["notes"], "retained note")

    def test_atomic_conflict_and_metadata_concurrent_writes(self):
        a = self.library.add("Alpha", "https://alpha.test")
        b = self.library.add("Beta", "https://beta.test")
        saved = read_json(self.library.path)
        saved["custom_metadata"] = {"preserve": True}
        write_json(self.library.path, saved)
        source = {
            **source_from_url("https://jobs.lever.co/shared"),
            "status": "verified",
            "ownership": "verified",
            "ownership_basis": "fixture",
            "collector_capability": "supported",
        }

        def persist(c):
            return self.library.persist_verification(
                c["id"], {**c, "current_sources": [copy.deepcopy(source)]}, c["id"]
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(persist, [a, b]))
        self.assertEqual(sum(r["source_status"] == "verified" for r in results), 1)
        self.assertEqual(sum(bool(r.get("source_conflicts")) for r in results), 1)
        self.assertEqual(read_json(self.library.path)["custom_metadata"], {"preserve": True})
        self.library.add("Third", "https://third.test")
        self.assertEqual(read_json(self.library.path)["custom_metadata"], {"preserve": True})

    def test_batch_resume_counts_exceptions_and_frozen_denominator(self):
        a = self.company()
        b = self.library.add("Other", "https://other.test")
        batch = VerificationBatch.create(self.config, ids=[a["id"], b["id"]], concurrency=1)
        self.library.add("Later", "https://later.test")
        calls = []

        def verify(lib, identity, **kw):
            calls.append(identity)
            if identity == b["id"]:
                raise ValueError("fixture failure")
            return {**lib.get(identity), "discovery_outcome": "done"}

        with patch.object(CompanyLibrary, "verify", verify):
            summary = batch.run()
            again = VerificationBatch(self.config, batch.directory).run()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(again["total"], 2)
        self.assertEqual(len(calls), 2)
        report = read_json(batch.directory / "report.json")
        self.assertEqual(sum(report["counts"].values()), 2)
        self.assertEqual(len(report["companies"]), 2)
        self.assertTrue((batch.directory / "report.csv").exists())
        self.assertTrue((batch.directory / "companies-before.json").exists())
        with patch.object(CompanyLibrary, "verify", verify):
            VerificationBatch(self.config, batch.directory).run(retry_incomplete=True)
        self.assertEqual(len(calls), 3)

    def test_batch_stopped_is_resumable_and_usage_monotonic(self):
        c = self.company()
        batch = VerificationBatch.create(self.config, ids=[c["id"]])
        batch.stop_event.set()
        result = batch.run()
        self.assertEqual(result["remaining"], 1)
        event = {"reserved_bytes": 100}
        batch.stop_event.clear()
        batch.reserve(c["id"], event)
        batch.finish({**event, "bytes": 25})
        self.assertEqual(batch.state["usage"]["requests"], 1)
        self.assertEqual(batch.state["usage"]["bytes"], 25)
        with patch.object(CompanyLibrary, "verify", lambda lib, i, **k: lib.get(i)):
            result = VerificationBatch(self.config, batch.directory).run()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["usage"]["requests"], 1)


def normalize_for_test(name):
    return name.lower().replace(" ", "_")


if __name__ == "__main__":
    unittest.main()


class AdditionalBoundaryTests(unittest.TestCase):
    def test_provider_429_404_and_transient_retry_accounting(self):
        for statuses, expected in [
            ([429, 200], 200),
            ([503, 502, 200], 200),
            ([404], 404),
            ([429, 429, 429], 429),
        ]:
            c = CollectionClient({"retry_count": 2, "host_delay_seconds": 0}, requests=10)
            with (
                patch("degent.collection_http.public_address", return_value="8.8.8.8"),
                patch.object(c, "attempt", side_effect=[(s, {}, b"x") for s in statuses]),
                patch("degent.collection_http.time.sleep"),
            ):
                self.assertEqual(c.request("https://fixture.test")[0], expected)
            self.assertEqual(c.request_count, len(statuses))
            self.assertEqual(c.bytes_used, len(statuses))

    def test_html_200_challenge_cannot_verify_provider(self):
        c = {
            "id": "x",
            "name": "Example",
            "website": "https://example.test",
            "current_sources": [source_from_url("https://jobs.lever.co/example")],
        }
        result = verify_sources(
            c,
            FixtureClient(
                lambda u: (200, {}, b"<title>Just a moment</title>Checking your browser")
            ),
        )
        self.assertNotEqual(result["current_sources"][0]["status"], "verified")
        self.assertEqual(result["verification_destinations"][0]["availability"], "unavailable")

    def test_official_destination_generic_description_sample(self):
        c = {
            "id": "x",
            "name": "Example",
            "website": "https://example.test",
            "career_url": "https://example.test/careers",
            "current_sources": [],
        }

        def response(url):
            if url.endswith("/jobs/1"):
                return 200, {}, html(structured())
            return 200, {}, b'<a href="/careers">Careers</a><a href="/jobs/1">Analyst</a>'

        result = verify_sources(c, FixtureClient(response))
        generic = next(s for s in result["current_sources"] if s["provider"] == "dom")
        self.assertEqual(generic["status"], "verified")
        self.assertEqual(len(generic["samples"]), 1)
        self.assertEqual(generic["inventory_completeness"], "not_checked")

    def test_sitemap_cycles_and_unrelated_origins_remain_bounded(self):
        c = {
            "id": "x",
            "name": "Example",
            "website": "https://example.test",
            "career_url": "https://example.test/careers",
            "current_sources": [],
        }

        def response(url):
            if url.endswith("robots.txt"):
                return (
                    200,
                    {},
                    b"Sitemap: https://example.test/index.xml\nSitemap: https://evil.test/a.xml",
                )
            if url.endswith("index.xml"):
                return (
                    200,
                    {},
                    b"<sitemapindex><sitemap><loc>https://example.test/index.xml</loc></sitemap><sitemap><loc>https://example.test/jobs.xml</loc></sitemap></sitemapindex>",
                )
            if url.endswith("jobs.xml"):
                return (
                    200,
                    {},
                    b"<urlset><url><loc>https://example.test/jobs/1</loc><lastmod>2020-01-01</lastmod></url><url><loc>https://other.test/jobs/2</loc></url></urlset>",
                )
            if url.endswith("/jobs/1"):
                return 200, {}, html(structured())
            return 200, {}, b"<p>Official careers page</p>"

        client = FixtureClient(response)
        result = verify_sources(c, client)
        source = next(s for s in result["current_sources"] if s["provider"] == "sitemap")
        self.assertEqual(source["status"], "verified")
        self.assertLess(client.request_count, 12)
        self.assertFalse(
            any(e["url"] == "https://other.test/jobs/2" for e in result["verification_evidence"])
        )
        self.assertTrue(
            any(
                e.get("reason") == "unrelated_sitemap_origin"
                for e in result["verification_evidence"]
            )
        )

    def test_batch_lock_and_crash_receipt_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            config = load_config()
            config.update(data_dir=str(Path(root) / "data"))
            lib = CompanyLibrary(config)
            c = lib.add("Example", "https://example.test")
            batch = VerificationBatch.create(config, ids=[c["id"]])
            attempt = "durable-attempt"
            batch.state["attempts"][c["id"]] = attempt
            batch.state["active_companies"] = {c["id"]: 5}
            batch.checkpoint()
            write_json(
                batch.directory / ("checked-" + attempt + ".json"),
                {**c, "discovery_outcome": "checked before crash"},
            )
            from degent.store import operation_lock

            with operation_lock(batch.directory):
                with self.assertRaises(RuntimeError):
                    VerificationBatch(config, batch.directory).run()
            with patch.object(
                CompanyLibrary, "verify", side_effect=AssertionError("must replay receipt")
            ):
                result = VerificationBatch(config, batch.directory).run()
            self.assertEqual(result["processed"], 1)
            self.assertGreaterEqual(result["usage"]["seconds"], 5)
            self.assertEqual(lib.get(c["id"])["latest_verification_attempt"], attempt)

    def test_generic_configuration_is_local_and_invalidates_verification(self):
        with tempfile.TemporaryDirectory() as root:
            config = load_config()
            config["data_dir"] = root
            lib = CompanyLibrary(config)
            c = lib.add("Example", "https://example.test")
            with patch.object(
                CollectionClient, "request", side_effect=AssertionError("local configuration")
            ):
                configured = lib.configure_source(
                    c["id"],
                    "https://example.test/careers",
                    "dom",
                    {"fields": {"title": "h1", "description": "main", "employer": ".company"}},
                )
            self.assertEqual(configured["source_status"], "unverified")
            with self.assertRaises(ValueError):
                lib.configure_source(
                    c["id"], "https://example.test/careers", "dom", {"script": "evil()"}
                )


class DiscoveredRegressionTests(unittest.TestCase):
    def test_literal_embeds_without_executing_scripts(self):
        from bs4 import BeautifulSoup

        from degent.source_verification import embedded_ats_links

        soup = BeautifulSoup(
            """<script>window.leverJobsOptions = {accountName: 'explicit-token', includeCss: true}; const BOARD = "known-ashby"; const API = `https://api.ashbyhq.com/posting-api/job-board/${BOARD}`;</script>""",
            "html.parser",
        )
        self.assertEqual(
            embedded_ats_links(soup),
            [
                "https://jobs.lever.co/explicit-token",
                "https://api.ashbyhq.com/posting-api/job-board/known-ashby",
            ],
        )
        self.assertEqual(
            embedded_ats_links(
                BeautifulSoup(
                    "<script>const BOARD = evil(); const API = `https://api.ashbyhq.com/posting-api/job-board/${BOARD}`;</script>",
                    "html.parser",
                )
            ),
            [],
        )

    def test_lowercase_workday_locale_and_board_conflict_alias_guard(self):
        source = source_from_url("https://employer.wd1.myworkdayjobs.com/en-us/External")
        self.assertEqual(source["site"], "External")
        with tempfile.TemporaryDirectory() as root:
            config = load_config()
            config["data_dir"] = root
            lib = CompanyLibrary(config)
            parent = lib.add("Parent", "https://parent.test", "https://jobs.lever.co/parent")
            with self.assertRaises(ValueError):
                lib.add("Unrelated", "https://unrelated.test", "https://jobs.lever.co/parent")
            self.assertEqual(lib.get(parent["id"])["aliases"], [])

    def test_fragment_assets_and_mailto_do_not_hide_postings(self):
        payload = b'<a href="mailto:jobs@example.test">Email</a><a href="/careers/privacy">Privacy</a><a href="/job/123">Analyst</a>'
        rows, _, _ = listing(payload, "https://example.test/careers", {})
        self.assertEqual(rows[0], "https://example.test/job/123")
        c = {
            "id": "x",
            "name": "Example",
            "website": "https://example.test",
            "career_url": "https://example.test/careers",
            "current_sources": [],
        }
        client = FixtureClient(
            lambda url: (
                200,
                {},
                b'<a href="/careers#one">Careers</a><a href="/careers#two">Careers</a><a href="/css/careers.css">Careers</a>',
            )
        )
        result = verify_sources(c, client)
        self.assertFalse(
            any(
                "#" in e.get("url", "") or e.get("url", "").endswith(".css")
                for e in result["verification_evidence"]
            )
        )


class CLIWorkflowTests(unittest.TestCase):
    def test_batch_cli_selected_resume_and_frozen_budget_validation(self):
        from contextlib import redirect_stdout

        from degent.cli import main

        with tempfile.TemporaryDirectory() as root:
            config = load_config()
            config["data_dir"] = str(Path(root) / "data")
            lib = CompanyLibrary(config)
            c = lib.add("Example", "https://example.test")
            config_path = Path(root) / "config.json"
            write_json(config_path, config)

            def command(*args):
                stream = io.StringIO()
                with (
                    patch("sys.argv", ["degent", "--config", str(config_path), *args]),
                    redirect_stdout(stream),
                ):
                    main()
                return json.loads(stream.getvalue())

            with patch.object(CompanyLibrary, "verify", lambda lib, i, **kw: lib.get(i)):
                first = command(
                    "companies", "verify-all", "--companies", c["id"], "--concurrency", "1"
                )
                again = command("companies", "verify-all", "--resume", first["directory"])
            self.assertEqual(first["processed"], 1)
            self.assertEqual(again["processed"], 1)
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                command(
                    "companies", "verify-all", "--resume", first["directory"], "--requests", "5"
                )


class RetryInterruptionTests(unittest.TestCase):
    def test_stopped_explicit_retry_resumes_unfinished_retry_without_new_ids(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = load_config()
            cfg["data_dir"] = root
            lib = CompanyLibrary(cfg)
            a = lib.add("Alpha", "https://alpha.test")
            b = lib.add("Beta", "https://beta.test")
            batch = VerificationBatch.create(cfg, ids=[a["id"], b["id"]], concurrency=1)
            with patch.object(CompanyLibrary, "verify", lambda lib, i, **kw: lib.get(i)):
                batch.run()

            def verify(lib, i, **kw):
                if i == a["id"]:
                    batch.stop_event.set()
                return lib.get(i)

            with patch.object(CompanyLibrary, "verify", verify):
                stopped = batch.run(retry_ids=[a["id"], b["id"]])
            self.assertEqual(stopped["pending_checks"], 1)
            attempt = batch.state["attempts"][b["id"]]
            calls = []
            with patch.object(
                CompanyLibrary, "verify", lambda lib, i, **kw: (calls.append(i) or lib.get(i))
            ):
                resumed = VerificationBatch(cfg, batch.directory).run()
            self.assertEqual(calls, [b["id"]])
            self.assertEqual(resumed["pending_checks"], 0)
            self.assertEqual(read_json(batch.path)["attempts"][b["id"]], attempt)


class PostingIdentityTests(unittest.TestCase):
    def test_tracking_duplicates_and_expired_postings(self):
        from degent.generic_sources import canonical

        self.assertEqual(
            canonical("https://example.test/job/123?trid=abc&utm_source=feed#x"),
            "https://example.test/job/123",
        )
        self.assertNotEqual(
            canonical("https://example.test/jobs?id=1"), canonical("https://example.test/jobs?id=2")
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            extract(
                html(structured(validThrough="2020-01-01")),
                "https://example.test/jobs/1",
                {"name": "Example"},
            )

    def test_official_external_job_link_has_matching_employer_sample(self):
        c = {
            "id": "x",
            "name": "Example",
            "website": "https://example.test",
            "career_url": "https://example.test/careers",
            "current_sources": [],
        }
        target = "https://jobs.gem.test/example/posting123"

        def response(url):
            return (
                (200, {}, html(structured(url=target)))
                if "gem.test" in url
                else (200, {}, f'<a href="{target}">Apply now</a>'.encode())
            )

        checked = verify_sources(c, FixtureClient(response))
        source = next(s for s in checked["current_sources"] if s["provider"] == "dom")
        self.assertEqual(source["status"], "verified")
        self.assertEqual(source["samples"][0]["verified_employer"], "Example")
        self.assertTrue(source["ownership_chain"])


class StructuredEmployerIdentityTests(unittest.TestCase):
    def test_hiring_organization_official_domain_and_escaped_description(self):
        import html as markup

        obj = structured(
            description=markup.escape(body()),
            hiringOrganization={"name": "Ex", "sameAs": "https://example.test/"},
        )
        row = extract(
            html(obj),
            "https://example.test/jobs/1",
            {"name": "Example", "website": "https://example.test"},
        )
        self.assertEqual(row["verified_employer"], "Example")
        self.assertNotIn("&lt;", row["text"])
        obj["hiringOrganization"]["sameAs"] = "https://parent.test/"
        with self.assertRaises(ValueError):
            extract(
                html(obj),
                "https://example.test/jobs/1",
                {"name": "Example", "website": "https://example.test"},
            )


class GenericVacancyIdentityTests(unittest.TestCase):
    def test_dom_and_sitemap_share_vacancy_but_preserve_capture_sources(self):
        from degent.snapshots import SnapshotStore

        with tempfile.TemporaryDirectory() as root:
            cfg = load_config()
            cfg["data_dir"] = root
            store = SnapshotStore(cfg)
            employer = {"id": "example", "name": "Example", "group": "unknown"}
            row = extract(html(structured()), "https://example.test/jobs/1", employer)
            a = {"provider": "dom", "url": "https://example.test/careers", "dom_config": {}}
            b = {"provider": "sitemap", "url": "https://example.test/sitemap.xml", "dom_config": {}}
            first = store.capture(
                cfg, "one", employer, a, row, "2026-10-08T10:00:00+00:00", "first"
            )
            second = store.capture(
                cfg, "one", employer, b, row, "2026-10-08T11:00:00+00:00", "second"
            )
            self.assertEqual(first["vacancy_id"], second["vacancy_id"])
            self.assertEqual(first["document_id"], second["document_id"])
            self.assertNotEqual(first["career_source_id"], second["career_source_id"])
            self.assertEqual(second["content_change"], "unchanged")


class DOMAttributeTests(unittest.TestCase):
    def test_attribute_fields_remain_unique_and_non_executable(self):
        from degent.generic_sources import extract, listing, validate_config

        config = {
            "fields": {
                "title": "h1",
                "description": "article",
                "employer": {"selector": 'meta[property="og:site_name"]', "attribute": "content"},
            }
        }
        body = (
            '<meta property="og:site_name" content="Acme"><h1>Engineer</h1><article>Acme responsibilities qualifications '
            + ("Build reliable systems. " * 50)
            + "</article>"
        ).encode()
        self.assertEqual(
            extract(body, "https://acme.example/jobs/123", {"name": "Acme"}, config)[
                "verified_employer"
            ],
            "Acme",
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            extract(
                body + b'<meta property="og:site_name" content="Acme">',
                "https://acme.example/jobs/123",
                {"name": "Acme"},
                config,
            )
        config["fields"]["employer"]["attribute"] = "onclick"
        with self.assertRaisesRegex(ValueError, "invalid_dom_field_attribute"):
            validate_config(config, "https://acme.example/jobs/123")
        links, _, _ = listing(
            b'<a href="/careers/investing">Teams</a><a href="/careers/details/engineer">Engineer</a>',
            "https://acme.example/careers",
            {},
        )
        self.assertEqual(links[0], "https://acme.example/careers/details/engineer")


class LegalEmployerNameTests(unittest.TestCase):
    def test_legal_suffix_does_not_hide_region_or_parent_attribution(self):
        from degent.collection_http import CollectionClient
        from degent.source_verification import verify_sources

        def check(name):
            client = CollectionClient({"host_delay_seconds": 0}, requests=20)
            client.request = lambda url, **kw: (
                (200, {}, json.dumps({"name": name}).encode())
                if "boards-api" in url
                else (200, {}, b"<html><body>Official company website</body></html>")
            )
            company = {
                "id": "ocrolus",
                "name": "Ocrolus",
                "website": "https://ocrolus.example",
                "current_sources": [
                    {"id": "source-test", "provider": "greenhouse", "board": "ocrolus"}
                ],
            }
            return verify_sources(company, client)["current_sources"][0]

        self.assertEqual(check("Ocrolus Inc.")["ownership"], "verified")
        self.assertNotEqual(check("Ocrolus Montreal")["ownership"], "verified")


class PostingBrandTests(unittest.TestCase):
    def test_explicit_role_headings_and_foreign_brand_are_independent(self):
        from degent.generic_sources import extract

        config = {"fields": {"title": "h1", "description": "article", "employer": "b"}}
        text = (
            "Brand: Acme\nYour Opportunity\n"
            + "Build reliable systems. " * 40
            + "\nYour Skills\nExperience in Python"
        )

        def body(text):
            return ("<b>Acme</b><h1>Engineer</h1><article>" + text + "</article>").encode()

        row = extract(body(text), "https://acme.example/jobs/1", {"name": "Acme"}, config)
        self.assertIn("Your Opportunity", row["text"])
        with self.assertRaisesRegex(ValueError, "brand_mismatch"):
            extract(
                body(text.replace("Brand: Acme", "Brand: Subsidiary")),
                "https://acme.example/jobs/1",
                {"name": "Acme"},
                config,
            )


class ConfiguredDestinationTests(unittest.TestCase):
    def test_generic_identity_uses_canonical_destination(self):
        from degent.companies import source_identity

        a = {
            "provider": "dom",
            "board": "https://acme.example/careers/",
            "url": "https://acme.example/careers/",
        }
        b = {
            "provider": "dom",
            "board": "https://acme.example/careers",
            "url": "https://acme.example/careers",
        }
        self.assertEqual(source_identity(a), source_identity(b))

    def test_manual_fields_survive_destination_redirect_slash(self):
        from degent.collection_http import CollectionClient
        from degent.source_verification import verify_sources

        url = "https://acme.example/careers"
        source = {
            "id": "preserved-source-id",
            "provider": "dom",
            "board": url,
            "url": url,
            "dom_config": {"fields": {"title": "h1", "description": "article", "employer": "b"}},
        }
        listing_body = b'<a href="/careers/job/123">Engineer</a>'
        job = (
            "<b>Acme</b><h1>Engineer</h1><article>Acme responsibilities qualifications "
            + ("Build systems. " * 80)
            + "</article>"
        ).encode()
        client = CollectionClient({"host_delay_seconds": 0}, requests=20)

        def request(target, **kw):
            return (
                200,
                {"X-Degent-Final-URL": target.rstrip("/") + "/"},
                job if "/job/" in target else listing_body,
            )

        client.request = request
        row = verify_sources(
            {
                "id": "acme",
                "name": "Acme",
                "website": "https://acme.example",
                "current_sources": [source],
            },
            client,
        )
        self.assertTrue(
            any(
                s["id"] == "preserved-source-id" and s["status"] == "verified"
                for s in row["current_sources"]
            )
        )


class LatestGenericAttemptTests(unittest.TestCase):
    def test_unsampled_retry_does_not_inherit_ownership_or_samples(self):
        from degent.collection_http import CollectionClient
        from degent.source_verification import verify_sources

        source = {
            "id": "old",
            "provider": "dom",
            "url": "https://acme.example/careers",
            "status": "verified",
            "ownership": "verified",
            "ownership_basis": "old proof",
            "samples": [{"title": "Old Engineer"}],
        }
        client = CollectionClient({"host_delay_seconds": 0}, requests=20)
        client.request = lambda url, **kw: (404, {}, b"missing")
        row = verify_sources(
            {
                "id": "acme",
                "name": "Acme",
                "website": "https://acme.example",
                "current_sources": [source],
            },
            client,
        )
        current = row["current_sources"][0]
        self.assertNotEqual(current["ownership"], "verified")
        self.assertEqual(current["samples"], [])
        self.assertIsNone(current["ownership_basis"])
        self.assertEqual(source["ownership"], "verified")


class DimensionResultTests(unittest.TestCase):
    def test_results_carry_time_reason_and_evidence_without_inventory_claim(self):
        from degent.companies import verification_results

        results = verification_results(
            {
                "last_verified_at": "2026-10-08T00:00:00Z",
                "availability": "available",
                "ownership": "verified",
                "inventory_completeness": "not_checked",
            },
            [{"url": "https://acme.example", "sha256": "abc", "evidence_file": "/local/abc"}],
        )
        self.assertEqual(
            set(results),
            {"availability", "ownership", "collector_capability", "sampling", "inventory"},
        )
        self.assertEqual(results["ownership"]["checked_at"], "2026-10-08T00:00:00Z")
        self.assertEqual(results["availability"]["evidence_refs"][0]["sha256"], "abc")
        self.assertEqual(results["inventory"]["reason_code"], "not_checked")
        self.assertIsNone(results["inventory"]["checked_at"])
        self.assertEqual(results["inventory"]["evidence_refs"], [])


class AcceptanceCountTests(unittest.TestCase):
    def test_completed_deferred_and_unprocessed_reconcile(self):
        from degent.verification_batch import VerificationBatch

        batch = VerificationBatch.__new__(VerificationBatch)
        batch.directory = Path("/local/verification-example")
        batch.state = {
            "id": "example",
            "status": "complete",
            "ids": ["a", "b", "c"],
            "results": {
                "a": {"status": "verified", "incomplete": False},
                "b": {"status": "unsupported", "incomplete": True},
            },
            "usage": {},
            "budgets": {},
        }
        summary = batch.summary()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["deferred"], 1)
        self.assertEqual(
            summary["completed"] + summary["deferred"] + summary["unprocessed"], summary["total"]
        )


class CSVReportSizeTests(unittest.TestCase):
    def test_many_source_dimensions_share_evidence_and_csv_is_readable(self):
        import csv

        from degent.companies import CompanyLibrary
        from degent.config import load_config
        from degent.verification_batch import VerificationBatch

        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config["data_dir"] = temporary
            library = CompanyLibrary(config)
            company = library.add("Acme", "https://acme.example")
            batch = VerificationBatch.create(config, ids=[company["id"]])
            sources = [
                {
                    "id": str(i),
                    "provider": "dom",
                    "url": "https://acme.example/careers",
                    "status": "unverified",
                    "last_verified_at": "2026-10-08",
                }
                for i in range(12)
            ]
            evidence = [
                {
                    "url": "https://acme.example/jobs/" + ("x" * 400) + str(i),
                    "sha256": "a" * 64,
                    "evidence_file": "/local/" + ("b" * 200),
                }
                for i in range(20)
            ]
            for source in sources:
                source["ownership_chain"] = evidence
            batch.state["results"][company["id"]] = {
                "id": company["id"],
                "name": "Acme",
                "status": "unverified",
                "current_sources": sources,
                "attempted_urls": evidence,
                "incomplete": False,
            }
            batch.report()
            with (batch.directory / "report.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            parsed = json.loads(rows[0]["current_sources"])
            self.assertEqual(len(parsed), 12)
            self.assertEqual(
                parsed[0]["verification_results"]["availability"]["evidence_refs"],
                [{"report_field": "attempted_urls"}],
            )
            full = json.loads((batch.directory / "report.json").read_text())
            self.assertEqual(
                len(
                    full["companies"][0]["current_sources"][0]["verification_results"][
                        "availability"
                    ]["evidence_refs"]
                ),
                20,
            )
