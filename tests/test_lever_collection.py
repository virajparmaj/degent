"""Lever pagination/resume and immutable versions through real session accounting."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_company_collection as base_tests
from test_source_coverage import html, posting, structured

from degent.collection import collect
from degent.collection_http import BudgetReached, CollectionClient
from degent.companies import source_from_url, source_identity
from degent.store import read_json, read_jsonl


class LeverCollectionTests(base_tests.CollectionTests):
    # Keep reusable setup/helpers without rerunning the base suite.
    def setUp(self):
        super().setUp()
        self.source = {
            **source_from_url("https://jobs.lever.co/example"),
            "status": "verified",
            "ownership": "verified",
            "collector_capability": "supported",
            "ownership_basis": "fixture official link",
        }
        self.employer["current_sources"] = [self.source]

    def test_lever_multiple_pages_resume_and_accounting(self):
        calls = []

        def response(url, kwargs):
            offset = int(parse_qs(urlsplit(url).query)["skip"][0])
            calls.append(offset)
            return (
                200,
                {},
                json.dumps([posting(i) for i in range(offset, min(offset + 100, 103))]).encode(),
            )

        with self.mock_network(response):
            for _ in range(10):
                result = collect(self.config, "lever")
                if result["status"] == "complete":
                    break
        self.assertEqual(result["status"], "complete")
        self.assertEqual(calls, [0, 100])
        rows = self.rows("lever")
        self.assertEqual(len(rows), 103)
        self.assertEqual(len({r["vacancy_id"] for r in rows}), 103)
        self.assertTrue(all(r["parser_version"] == "lever-public-v1" for r in rows))
        self.assertEqual(result["session"]["usage"]["requests"], 2)
        self.assertEqual(result["session"]["counts"]["pending_extraction"], 103)

    def test_lever_later_failure_retries_only_unfinished_page(self):
        calls = []
        fail = [True]

        def response(url, kwargs):
            offset = int(parse_qs(urlsplit(url).query)["skip"][0])
            calls.append(offset)
            if offset == 100 and fail[0]:
                return 503, {}, b"blocked"
            return (
                200,
                {},
                json.dumps(
                    [posting(i) for i in range(100)] if offset == 0 else [posting(100)]
                ).encode(),
            )

        self.config["collection_chunk_size"] = 1000
        with self.mock_network(response):
            result = collect(self.config, "partial")
            self.assertFalse(
                next(iter(result["session"]["sources"].values()))["inventory_complete"]
            )
            self.assertEqual(len(self.rows("partial")), 100)
            fail[0] = False
            result = collect(self.config, "partial")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(calls, [0, 100, 100])
        self.assertEqual(len(self.rows("partial")), 101)

    def test_lever_empty_is_complete_and_invalid_is_failure(self):
        with self.mock_network(lambda u, k: (200, {}, b"[]")):
            result = collect(self.config, "empty")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["total_saved"], 0)
        for i, payload in enumerate(
            [
                b"<html/>",
                b"{}",
                json.dumps([{**posting(), "id": ""}]).encode(),
                json.dumps([posting(), posting()]).encode(),
            ]
        ):
            with self.mock_network(lambda u, k: (200, {}, payload)):
                result = collect(self.config, "invalid" + str(i))
            self.assertEqual(result["status"], "needs_attention")
            self.assertFalse(
                next(iter(result["session"]["sources"].values()))["inventory_complete"]
            )

    def test_lever_versions_dates_and_gates(self):
        with self.mock_network(lambda u, k: (200, {}, json.dumps([posting()]).encode())):
            collect(self.config, "first")
            collect(self.config, "first")
        with self.mock_network(
            lambda u, k: (200, {}, json.dumps([posting(suffix=" Python preferred.")]).encode())
        ):
            collect(self.config, "second")
            collect(self.config, "second")
        a, b = self.rows("first")[0], self.rows("second")[0]
        self.assertEqual(a["vacancy_id"], b["vacancy_id"])
        self.assertNotEqual(a["document_id"], b["document_id"])
        self.assertEqual(a["collector_first_observed_at"], b["collector_first_observed_at"])
        self.assertIsNone(a["stated_publication_at"])
        self.assertEqual(a["upstream_start_at"], 1672531200000)
        self.source["status"] = "unsupported"
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("must not dispatch")
        ):
            result = collect(self.config, "gated")
        self.assertEqual(result["status"], "needs_attention")

    def test_dom_collection_is_bounded_and_preserves_raw(self):
        source = {
            "provider": "dom",
            "url": "https://example.test/careers",
            "status": "verified",
            "ownership": "verified",
            "collector_capability": "supported",
            "ownership_basis": "fixture",
            "dom_config": {},
        }
        source["id"] = source_identity(source)
        self.employer["current_sources"] = [source]

        def response(url, k):
            return (
                (200, {}, b'<a href="/jobs/1">Job</a>')
                if url.endswith("/careers")
                else (200, {}, html(structured()))
            )

        with self.mock_network(response):
            result = collect(self.config, "dom")
            result = collect(self.config, "dom")
        self.assertEqual(result["status"], "bounded")
        self.assertFalse(next(iter(result["session"]["sources"].values()))["inventory_complete"])
        row = self.rows("dom")[0]
        self.assertTrue(row["raw_source_sha256"])
        self.assertEqual(row["verified_employer"], "Example")

    def test_dom_later_page_failure_resume(self):
        source = {
            "provider": "dom",
            "url": "https://example.test/careers",
            "status": "verified",
            "ownership": "verified",
            "collector_capability": "supported",
            "ownership_basis": "fixture",
            "dom_config": {
                "next_page": ".next",
                "exhaustive": True,
                "advertised_total_selector": ".total",
            },
        }
        source["id"] = source_identity(source)
        self.employer["current_sources"] = [source]
        fail = [True]
        calls = []

        def response(url, k):
            calls.append(url)
            if url.endswith("?page=2"):
                return (
                    (503, {}, b"blocked")
                    if fail[0]
                    else (200, {}, b'<p class="total">2 jobs</p><a href="/jobs/2">Job</a>')
                )
            if url.endswith("/careers"):
                return (
                    200,
                    {},
                    b'<p class="total">2 jobs</p><a href="/jobs/1">Job</a><a class="next" href="?page=2">Next</a>',
                )
            return 200, {}, html(structured(url=url))

        with self.mock_network(response):
            result = collect(self.config, "dom-failure")
            self.assertEqual(result["status"], "needs_attention")
            fail[0] = False
            result = collect(self.config, "dom-failure")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.rows("dom-failure")), 2)
        self.assertEqual(calls.count("https://example.test/jobs/1"), 1)


# unittest inherits test methods; only retain adapter-specific tests.
for name in list(base_tests.CollectionTests.__dict__):
    if name.startswith("test_"):
        setattr(LeverCollectionTests, name, None)

    # Additional tests are attached below to avoid inherited base test discovery.
