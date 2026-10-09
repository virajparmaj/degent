"""Deterministic company/session/evidence contracts; no live network or model calls."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from degent.collection import collect, extend
from degent.collection_http import BudgetReached, CollectionClient, validate_public_url
from degent.companies import CompanyLibrary, source_from_url
from degent.config import load_config, observation_in_period
from degent.dashboard import Dashboard
from degent.extract import rule_candidates
from degent.extraction_run import extract_run, refresh_observations
from degent.review import effective_extractions, extraction_identity
from degent.snapshots import SnapshotStore
from degent.store import append_jsonl, digest, read_json, read_jsonl, write_json
from degent.workday import api_base, detail, page
from degent.workflow import run_pipeline


def description(suffix="SQL required."):
    return (
        "Responsibilities\n"
        + "You will analyze credit risk and collaborate with business partners.\n" * 9
        + "Qualifications\nTwo years of analysis experience.\n"
        + suffix
    )


def feed(count=1, suffix="SQL required."):
    return json.dumps(
        {
            "jobs": [
                {
                    "id": i,
                    "title": "Credit Risk Analyst II",
                    "absolute_url": f"https://boards.greenhouse.io/example/jobs/{i}",
                    "location": {"name": "United States"},
                    "content": description(suffix),
                    "first_published": "2023-01-01",
                    "updated_at": "2026-09-01",
                }
                for i in range(1, count + 1)
            ]
        }
    ).encode()


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config.update(
            data_dir=str(self.root / "data"),
            run_dir=str(self.root / "runs"),
            collection_version=2,
            current_document_cap=None,
            current_request_cap=20,
            collection_runtime_seconds=120,
            host_delay_seconds=0,
            retry_count=0,
            collection_chunk_size=25,
            sources=["current"],
        )
        self.source = {
            **source_from_url("https://boards.greenhouse.io/example"),
            "status": "configured",
            "ownership_basis": "fixture employer source assertion",
        }
        self.employer = {
            "id": "example",
            "name": "Example",
            "group": "unknown",
            "paths": [],
            "website": "https://example.com",
            "current_sources": [self.source],
        }
        self.config["employers"] = [self.employer]
        self.config["roles"].update(tracks=["credit_risk_analytics"], entry_mid_levels=["mid"])
        self.config["periods"] = [
            {"id": "CURRENT-2026", "start": "2026-01-01", "end": "2026-12-31", "kind": "partial"}
        ]

    def rows(self, run="one"):
        return read_jsonl(self.root / "runs" / run / "observations.jsonl")

    def mock_network(self, responder):
        def request(client, url, headers=None, **kwargs):
            if client.request_count >= client.request_limit:
                raise BudgetReached("request_limit")
            if client.bytes_used >= client.byte_limit:
                raise BudgetReached("byte_limit")
            client.request_count += 1
            event = {
                "url": url,
                "reserved_bytes": min(8_000_000, client.byte_limit - client.bytes_used),
            }
            if client.reserve:
                client.reserve(event)
            result = responder(url, kwargs)
            if isinstance(result, BaseException):
                raise result
            status, headers, body = result
            client.bytes_used += len(body)
            if client.finish:
                client.finish({**event, "bytes": len(body)})
            return status, headers, body

        return patch.object(CollectionClient, "request", request)

    def complete(self, run="one", payload=None):
        payload = payload or feed()
        with self.mock_network(lambda u, kw: (200, {}, payload)):
            for _ in range(20):
                result = collect(self.config, run)
                if result["status"] != "paused":
                    return result
        self.fail("Collection did not finish within fixture steps")

    def test_more_than_sixty_and_collection_independent_of_models(self):
        self.config.update(model_call_cap=0, extract_document_cap=0)
        calls = []
        with self.mock_network(lambda u, k: (calls.append(u) or 200, {}, feed(73))):
            a = collect(self.config, "one")
            self.assertEqual(a["status"], "paused")
            self.assertEqual(a["pause_reason"], "chunk_limit")
            self.assertEqual(len(self.rows()), 25)
            collect(self.config, "one")
            c = collect(self.config, "one")
        self.assertEqual(c["status"], "complete")
        self.assertEqual(c["session"]["counts"]["pending_extraction"], 73)
        self.assertEqual(len(self.rows()), 73)
        self.assertEqual(len(calls), 1)
        self.assertEqual(c["session"]["counts"]["discovered"], 73)
        self.assertEqual(len({r["vacancy_id"] for r in self.rows()}), 73)

    def test_unchanged_capture_reuses_bytes_and_records_new_observation(self):
        self.complete()
        a = self.rows()[0]
        self.complete("two")
        b = self.rows("two")[0]
        self.assertNotEqual(a["observation_id"], b["observation_id"])
        self.assertEqual(a["document_id"], b["document_id"])
        self.assertEqual(a["clean_file"], b["clean_file"])
        self.assertEqual(a["raw_source_file"], b["raw_source_file"])
        self.assertEqual(a["collector_first_observed_at"], b["collector_first_observed_at"])
        self.assertEqual(b["content_change"], "unchanged")
        self.assertFalse(b["new_content_version"])
        self.assertEqual(len(SnapshotStore(self.config).history(a["vacancy_id"])), 2)
        self.assertEqual(len(a["raw_sha256"]), 64)
        self.assertEqual(digest(Path(a["raw_source_file"]).read_bytes()), a["raw_source_sha256"])

    def test_changed_words_keep_old_evidence_and_require_fresh_review(self):
        self.complete()
        old = self.rows()[0]
        self.complete("two", feed(suffix="Python preferred."))
        new = self.rows("two")[0]
        self.assertEqual(old["vacancy_id"], new["vacancy_id"])
        self.assertNotEqual(old["document_id"], new["document_id"])
        self.assertEqual(new["content_change"], "changed")
        self.assertIn("SQL required.", Path(old["clean_file"]).read_text())
        self.assertNotIn("SQL required.", Path(new["clean_file"]).read_text())
        extract_run(self.config, "one", False, None)
        extract_run(self.config, "two", False, None)
        a = effective_extractions(self.root / "runs/one")[0]
        b = effective_extractions(self.root / "runs/two")[0]
        self.assertNotEqual(extraction_identity(a), extraction_identity(b))
        self.assertEqual(b["document_review"], "pending")

    def test_cross_run_model_cache_binding_validation_and_configuration(self):
        self.complete()
        calls = []

        def model(text, doc, *args):
            calls.append(doc)
            return rule_candidates(doc, text), "", 0

        self.config["semantic_review_call_cap"] = 0
        extract_run(self.config, "one", True, model)
        first = effective_extractions(self.root / "runs/one")[0]
        append_jsonl(
            self.root / "runs/one/document-reviews.jsonl",
            [{"extraction_id": extraction_identity(first)}],
        )
        self.complete("two")
        extract_run(self.config, "two", True, model)
        second = effective_extractions(self.root / "runs/two")[0]
        self.assertEqual(len(calls), 1)
        self.assertTrue(second["reused_artifact"])
        self.assertEqual(second["document_review"], "pending")
        self.config["codex_model"] = "different-fixture-model"
        extract_run(self.config, "two", True, model)
        self.assertEqual(len(calls), 2)
        # Identical text from another provider posting is rebound, never vacancy-merged.
        self.complete("three", feed(2))
        extract_run(self.config, "three", True, model)
        docs = effective_extractions(self.root / "runs/three")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len({r["document_id"] for r in docs}), 2)
        self.assertTrue(all(r["document_review"] == "pending" for r in docs))
        self.assertTrue(
            all(r["source_sha256"] == self.rows("three")[0]["clean_sha256"] for r in docs)
        )

    def test_cache_quotes_are_revalidated_and_parser_key_changes(self):
        self.complete()
        extract_run(self.config, "one", False, None)
        store = SnapshotStore(self.config)
        record = effective_extractions(self.root / "runs/one")[0]
        with store.connect() as db:
            cached = store.artifact(record["cache_key"])
            cached["requirements"][0]["phrase"] = "fabricated source quote"
            db.execute(
                "UPDATE artifacts SET artifact=? WHERE key=?",
                (json.dumps(cached), record["cache_key"]),
            )
        self.complete("two")
        extract_run(self.config, "two", False, None)
        result = effective_extractions(self.root / "runs/two")[0]
        self.assertFalse(result.get("reused_artifact", False))
        rows = self.rows("two")
        rows[0]["parser_version"] = "new-parser"
        from degent.store import write_jsonl

        write_jsonl(self.root / "runs/two/observations.jsonl", rows)
        extract_run(self.config, "two", False, None)
        self.assertNotEqual(
            effective_extractions(self.root / "runs/two")[0]["cache_key"], result["cache_key"]
        )

    def test_interrupted_event_projection_resumes_without_refetch_or_duplicate(self):
        import degent.collection as collection_module

        original = collection_module.append_jsonl

        def crash(path, rows):
            if path.name == "observations.jsonl":
                raise KeyboardInterrupt()
            return original(path, rows)

        with (
            self.mock_network(lambda u, k: (200, {}, feed())),
            patch("degent.collection.append_jsonl", crash),
        ):
            with self.assertRaises(KeyboardInterrupt):
                collect(self.config, "one")
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("already fetched")
        ):
            result = collect(self.config, "one")
            collect(self.config, "one")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(SnapshotStore(self.config).history(self.rows()[0]["vacancy_id"])), 1)

    def test_fair_source_progress_and_cumulative_budget_extensions(self):
        other = copy.deepcopy(self.employer)
        other["id"], other["name"] = "second", "Second"
        other["current_sources"][0]["board"] = "second"
        self.config["employers"].append(other)
        self.config["current_request_cap"] = 1
        with self.mock_network(lambda u, k: (200, {}, feed(2))):
            result = collect(self.config, "one")
        self.assertEqual(result["pause_reason"], "request_limit")
        self.assertEqual(len(self.rows()), 1)
        before = result["session"]["usage"].copy()
        extended = extend(self.config, "one", {"requests": 1})
        self.assertEqual(extended["usage"], before)
        self.assertEqual(extended["budgets"]["requests"], 2)
        with self.mock_network(lambda u, k: (200, {}, feed(2))):
            resumed = collect(self.config, "one")
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["requests_used"], 2)
        self.assertEqual(len(self.rows()), 4)

    def test_refresh_is_explicit_and_preserves_session_history(self):
        self.complete()
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("resume completed session")
        ):
            self.assertEqual(collect(self.config, "one")["saved"], 0)
        with self.mock_network(lambda u, k: (200, {}, feed())):
            result = collect(self.config, "one", refresh=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(
            len(read_json(self.root / "runs/one/manifest.json")["collection_sessions"]), 2
        )

    def test_unsupported_and_unverified_never_become_empty_success(self):
        self.source.update(provider="unknown", status="unsupported")
        with patch.object(CollectionClient, "request", side_effect=AssertionError("unsupported")):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(next(iter(result["sources"].values()))["inventory_complete"])
        self.source.update(provider="greenhouse", status="unverified")
        with patch.object(CollectionClient, "request", side_effect=AssertionError("unverified")):
            self.assertEqual(collect(self.config, "two")["status"], "needs_attention")

    def test_complete_empty_inventory_is_distinct(self):
        result = self.complete(payload=json.dumps({"jobs": []}).encode())
        self.assertEqual(result["status"], "complete")
        self.assertTrue(next(iter(result["sources"].values()))["inventory_complete"])
        self.assertEqual(result["session"]["counts"]["eligible"], 0)

    def test_all_current_dates_remain_separate_and_raw_integrity_is_checked(self):
        self.complete()
        row = self.rows()[0]
        self.assertEqual(row["stated_publication_at"], "2023-01-01")
        self.assertEqual(row["stated_updated_at"], "2026-09-01")
        self.assertIsNone(row["archive_capture_at"])
        self.assertFalse(
            observation_in_period(row, {"id": "2023", "start": "2020-01-01", "end": "2030-01-01"})
        )
        self.assertEqual(Path(row["raw_source_file"]).read_bytes(), feed())
        Path(row["raw_source_file"]).write_bytes(b"corrupt")
        self.assertEqual(
            refresh_observations(self.root / "runs/one", self.config)[0]["body_status"],
            "raw_source_hash_mismatch",
        )

    def test_optional_limit_has_no_sixty_ceiling_and_can_extend(self):
        self.config["current_document_cap"] = 1
        with self.mock_network(lambda u, k: (200, {}, feed(2))):
            result = collect(self.config, "one")
        self.assertEqual(result["pause_reason"], "document_limit")
        extend(self.config, "one", {"documents": 1})
        with patch.object(CollectionClient, "request", side_effect=AssertionError("cached")):
            self.assertEqual(collect(self.config, "one")["status"], "complete")

    def test_runtime_and_storage_budgets_pause_honestly(self):
        self.config["collection_runtime_seconds"] = 0
        self.assertEqual(collect(self.config, "one")["pause_reason"], "runtime_limit")
        self.config["collection_runtime_seconds"] = 120
        self.config["collection_storage_cap"] = 1
        with self.mock_network(lambda u, k: (200, {}, feed())):
            result = collect(self.config, "two")
        self.assertEqual(result["pause_reason"], "storage_limit")
        self.assertEqual(result["session"]["usage"]["storage"], 0)

    def test_workday_later_page_failure_is_incomplete_without_false_closure(self):
        self.source.clear()
        self.source.update(
            source_from_url("https://paypal.wd1.myworkdayjobs.com/jobs"),
            status="configured",
            ownership_basis="fixture assertion",
        )
        self.config["collection_chunk_size"] = 100
        self.config["current_request_cap"] = 100

        def responder(url, kwargs):
            if url.endswith("/jobs"):
                offset = kwargs["data"]["offset"]
                if offset:
                    return 503, {}, b"failed page"
                return (
                    200,
                    {},
                    json.dumps(
                        {
                            "total": 21,
                            "jobPostings": [
                                {"externalPath": f"/job/US/Analyst_R{i}"} for i in range(20)
                            ],
                        }
                    ).encode(),
                )
            req = url.rsplit("_", 1)[-1]
            return (
                200,
                {},
                json.dumps(
                    {
                        "jobPostingInfo": {
                            "jobReqId": req,
                            "title": "Credit Risk Analyst II",
                            "jobDescription": description(),
                            "location": "United States",
                            "startDate": "2023-01-01",
                        }
                    }
                ).encode(),
            )

        with self.mock_network(responder):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "needs_attention")
        info = next(iter(result["sources"].values()))
        self.assertFalse(info["inventory_complete"])
        self.assertEqual(info["offset"], 20)
        self.assertEqual(len(self.rows()), 20)
        self.assertTrue(all(r["stated_publication_at"] is None for r in self.rows()))
        self.assertTrue(all(r["upstream_start_at"] == "2023-01-01" for r in self.rows()))
        self.assertFalse(any("closed" in r for r in self.rows()))

        def final_page(url, kwargs):
            if url.endswith("/jobs"):
                self.assertEqual(kwargs["data"]["offset"], 20)
                return (
                    200,
                    {},
                    b'{"total":21,"jobPostings":[{"externalPath":"/job/US/Analyst_R20"}]}',
                )
            return responder(url, kwargs)

        with self.mock_network(final_page):
            resumed = collect(self.config, "one")
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(len(self.rows()), 21)

    def test_workday_rejects_ambiguous_identity_and_inconsistent_inventory(self):
        source = source_from_url("https://paypal.wd1.myworkdayjobs.com/jobs")
        for body in [
            b'{"total":5,"jobPostings":[]}',
            b'{"total":1,"jobPostings":[{}]}',
            b'{"total":1,"jobPostings":[{"externalPath":"/job/a"}]}',
        ]:
            with self.assertRaises(ValueError):
                page(body, 0, ["/job/a"] if b"/job/a" in body else [])
        with self.assertRaises(ValueError):
            detail(
                json.dumps(
                    {
                        "jobPostingInfo": {
                            "jobReqId": "R1",
                            "title": "A",
                            "jobDescription": "body",
                            "externalUrl": "https://evil.test/jobs/a",
                        }
                    }
                ).encode(),
                source,
                "/job/a_R10",
            )

    def test_no_research_outcome_keyword_selection(self):
        result = self.complete(payload=feed(2, suffix="Qualifications\nCommunication required."))
        self.assertEqual(result["session"]["counts"]["retrieved"], 2)
        self.assertFalse(any("AI" in Path(r["clean_file"]).read_text() for r in self.rows()))

    def test_pipeline_pause_and_independent_extraction(self):
        self.config["collection_chunk_size"] = 1
        with self.mock_network(lambda u, k: (200, {}, feed(3))):
            result = run_pipeline(self.config, "one")
        self.assertEqual(result["status"], "paused")
        self.assertNotIn("extract", result["stages"])
        self.assertEqual(extract_run(self.config, "one", False, None)["extracted"], 1)

    def test_import_cannot_supply_internal_identity_or_disable_file_preservation(self):
        from degent.sources import import_postings

        path = self.root / "import.json"
        payload = [
            {
                "employer_id": "example",
                "title": "Credit Risk Analyst II",
                "location": "United States",
                "source_url": "https://example.com/job/1",
                "text": description(),
                "_document_id": "../../outside",
                "_observation_id": "../../outside",
                "_no_files": True,
            }
        ]
        write_json(path, payload)
        self.assertEqual(import_postings(self.config, "one", path)["imported"], 1)
        row = self.rows()[0]
        self.assertNotEqual(row["document_id"], "../../outside")
        self.assertTrue(Path(row["clean_file"]).is_file())
        self.assertTrue(Path(row["raw_file"]).is_relative_to(self.root / "data/one"))

    def test_extraction_budget_extensions_preserve_scope_and_call_usage(self):
        self.config.update(model_call_cap=1, extract_document_cap=1, semantic_review_call_cap=0)
        payload = json.loads(feed(2))
        payload["jobs"][1]["content"] = description("Python preferred.")
        self.complete(payload=json.dumps(payload).encode())
        directory = self.root / "runs/one"
        write_json(directory / "dashboard-config.json", self.config)
        frozen = (directory / "dashboard-config.json").read_bytes()
        calls = []

        def model(text, document, *args):
            calls.append(document)
            return rule_candidates(document, text), "", 0

        extract_run(self.config, "one", True, model)
        self.assertEqual(len(calls), 1)
        session = extend(self.config, "one", {"model_calls": 1, "extraction_documents": 1})
        self.assertEqual(session["status"], "complete")
        self.assertEqual(read_json(directory / "manifest.json")["model_calls_used"], 1)
        self.assertEqual(load_config(directory / "dashboard-config.json")["model_call_cap"], 2)
        self.assertEqual((directory / "dashboard-config.json").read_bytes(), frozen)
        extract_run(self.config, "one", True, model)
        self.assertEqual(len(calls), 2)
        self.assertEqual(read_json(directory / "manifest.json")["model_calls_used"], 2)

    def test_malformed_feed_rows_do_not_claim_complete_inventory(self):
        with self.mock_network(lambda u, k: (200, {}, b'{"jobs":[null]}')):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "needs_attention")
        self.assertFalse(next(iter(result["sources"].values()))["inventory_complete"])

    def test_private_dns_results_are_rejected_before_opening_connection(self):
        import socket

        from degent.collection_http import public_address

        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                public_address("private-rebinding.test")

    def test_crashed_request_reservation_stays_charged_after_restart(self):
        self.config["current_request_cap"] = 1
        with self.mock_network(lambda u, k: KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                collect(self.config, "one")
        manifest = read_json(self.root / "runs/one/manifest.json")
        session = manifest["collection_sessions"][-1]
        self.assertEqual(session["usage"]["requests"], 1)
        self.assertGreater(session["usage"]["bytes"], 0)
        with self.mock_network(lambda u, k: (200, {}, feed())):
            self.assertEqual(collect(self.config, "one")["pause_reason"], "request_limit")
        extend(self.config, "one", {"requests": 1})
        with self.mock_network(lambda u, k: (200, {}, feed())):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["requests_used"], 2)

    def test_incomplete_read_retains_partial_bytes_for_budget_accounting(self):
        class Reply:
            status = 200
            headers = {}
            count = 0

            def geturl(self):
                return "https://example.com/feed"

            def read(self, count):
                self.count += 1
                if self.count == 1:
                    return b"partial response"
                raise OSError("connection lost")

        client = CollectionClient(self.config)
        status, headers, body = client.read_body(Reply(), 100)
        self.assertEqual(status, 0)
        self.assertEqual(body, b"partial response")
        self.assertIn("incomplete", headers["error"])

    def test_response_bytes_are_bounded_and_never_parsed_as_complete(self):
        import urllib.request
        from contextlib import contextmanager

        class Reply:
            status = 200
            headers = {}

            def geturl(self):
                return "https://example.com/feed"

            def read(self, count):
                return b"x" * count

        class Opener:
            @contextmanager
            def open(self, *args, **kwargs):
                yield Reply()

        client = CollectionClient(self.config, requests=1, byte_limit=100, seconds=10)
        with (
            patch("degent.collection_http.validate_public_url"),
            patch.object(urllib.request, "build_opener", return_value=Opener()),
        ):
            status, headers, body = client.request("https://example.com/feed")
        self.assertEqual(status, 0)
        self.assertEqual(len(body), 100)
        self.assertIn("incomplete", headers["error"])
        self.assertEqual(client.bytes_used, 100)

    def test_reports_separate_current_capture_from_old_publication_assertion(self):
        from degent.compare import build_report

        self.config["periods"].append(
            {"id": "2023-H1", "start": "2023-01-01", "end": "2023-06-30", "kind": "completed"}
        )
        self.complete()
        result = build_report(self.root / "runs/one", self.config)
        historical = next(r for r in result["coverage"] if r["period"] == "2023-H1")
        self.assertEqual(historical["complete_eligible_descriptions"], 0)
        self.assertEqual(
            result["retrieval_summary"]["collection_sessions"][0]["status"], "complete"
        )
        self.assertIn("stated_updated_at", (self.root / "runs/one/observations.csv").read_text())

    def test_reverted_content_uses_latest_capture_not_latest_created_version(self):
        self.complete("one", feed(suffix="SQL required."))
        self.complete("two", feed(suffix="Python required."))
        self.complete("three", feed(suffix="SQL required."))
        self.complete("four", feed(suffix="SQL required."))
        self.assertEqual(self.rows("three")[0]["content_change"], "changed")
        self.assertEqual(self.rows("four")[0]["content_change"], "unchanged")

    def test_capital_one_new_session_retains_role_search_restrictions(self):
        from test_capital_one import detail as capital_detail
        from test_capital_one import listing, url

        self.employer.update(id="capital_one", name="Capital One")
        self.source.clear()
        self.source.update(
            provider="capital_one",
            board="capitalonecareers",
            status="configured",
            keywords=["credit"],
            max_pages=1,
        )
        payload = listing([(url(), "Credit Risk Analyst II", "United States")], pages=2)
        with self.mock_network(
            lambda u, k: (
                200,
                {},
                payload if "/search-jobs?" in u else capital_detail(title="Credit Risk Analyst II"),
            )
        ):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "bounded")
        info = next(iter(result["sources"].values()))
        self.assertFalse(info["inventory_complete"])
        self.assertTrue(info["inventory_bounded"])
        self.assertEqual(info["restrictions"]["keywords"], ["credit"])
        self.assertEqual(info["retrieved"], 1)
        self.assertEqual(self.rows()[0]["requisition_id"], "R000001")

    def test_workday_unparseable_body_remains_raw_and_counts_as_rejected(self):
        self.source.clear()
        self.source.update(
            source_from_url("https://paypal.wd1.myworkdayjobs.com/jobs"), status="configured"
        )

        def responder(url, kwargs):
            if url.endswith("/jobs"):
                return 200, {}, b'{"total":1,"jobPostings":[{"externalPath":"/job/US/Analyst_R1"}]}'
            return 200, {}, b'{"jobPostingInfo":{}}'

        with self.mock_network(responder):
            result = collect(self.config, "one")
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["session"]["counts"]["retrieved"], 1)
        self.assertEqual(result["session"]["counts"]["rejected"], 1)
        metadata = next(iter(result["session"]["sources"].values()))["detail_captures"][
            "/job/US/Analyst_R1"
        ]
        self.assertEqual(Path(metadata["raw_file"]).read_bytes(), b'{"jobPostingInfo":{}}')


class CompanyLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config.update(data_dir=str(self.root / "data"), run_dir=str(self.root / "runs"))
        self.library = CompanyLibrary(self.config)

    def test_search_add_alias_domain_dedup_pagination_and_restart(self):
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("lookup/add must stay local")
        ):
            a = self.library.add("New Company", "https://new-company.test", aliases=["NC"])
            b = self.library.add("NC", "https://www.new-company.test/")
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(self.library.search("NC")["items"][0]["id"], a["id"])
            self.assertEqual(self.library.search("new-company.test")["total"], 1)
            self.assertEqual(
                CompanyLibrary(self.config).get(a["id"])["source_status"], "unsupported"
            )
            self.assertEqual(self.library.search(size=2)["pages"], 4)
            self.library.add("New Company", career_url="https://boards.greenhouse.io/new")
        row = self.library.get(a["id"])
        self.assertEqual(row["source_status"], "unverified")
        self.assertEqual(row["group"], "unknown")

    def test_unicode_company_names_and_aliases_preserve_identity(self):
        first = self.library.add("株式会社 Example", aliases=["例"])
        repeated = self.library.add("例")
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(self.library.search("株式会社")["total"], 1)

    def test_source_identity_never_guesses_tokens_and_rejects_private_urls(self):
        self.assertEqual(source_from_url("https://company.test/careers")["provider"], "unknown")
        for url in [
            "http://company.test",
            "https://127.0.0.1/x",
            "https://user@company.test",
            "https://[::1]/",
            "https://localhost/x",
        ]:
            with self.assertRaises(ValueError):
                source_from_url(url)
        self.assertEqual(
            source_from_url("https://boards-api.greenhouse.io/v1/boards/block/jobs")["board"],
            "block",
        )

    def test_saved_scope_library_changes_and_scope_continuation(self):
        config_path = self.root / "config.json"
        write_json(config_path, self.config)
        app = Dashboard(self.root, config_path)
        company = app.library.add("New Company", "https://new-company.test")
        app.create_run(
            {
                "run_id": "one",
                "sources": ["current"],
                "periods": [],
                "employers": [company["id"]],
                "current_document_cap": 75,
            }
        )
        before = app.config("one")
        app.library.add("New Company", career_url="https://boards.greenhouse.io/new")
        self.assertEqual(app.config("one")["employers"], before["employers"])
        app.create_run(
            {
                "run_id": "two",
                "parent_run_id": "one",
                "sources": ["current"],
                "periods": [],
                "employers": [company["id"], "stripe"],
            }
        )
        after = app.config("two")
        self.assertEqual(after["scope_revision"]["added"], ["stripe"])
        self.assertTrue(
            after["employers"][0]["current_sources"] or after["employers"][1]["current_sources"]
        )
        self.assertEqual(app.config("one")["current_document_cap"], 75)

    def test_cli_search_add_show_and_current_continuation(self):
        from degent.cli import main

        config_path = self.root / "config.json"
        write_json(config_path, self.config)

        def command(*args):
            output = io.StringIO()
            with (
                patch("sys.argv", ["degent", "--config", str(config_path), *args]),
                redirect_stdout(output),
            ):
                main()
            return json.loads(output.getvalue())

        a = command(
            "companies", "add", "Manual Co", "--website", "https://manual.test", "--alias", "MC"
        )
        self.assertEqual(command("companies", "search", "MC")["items"][0]["id"], a["id"])
        self.assertEqual(command("companies", "show", a["id"])["source_status"], "unsupported")
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("unsupported company")
        ):
            result = command("--run-id", "cli-one", "current", "--companies", a["id"])
        self.assertEqual(result["status"], "needs_attention")
        extended = command("--run-id", "cli-one", "continue", "--requests", "5")
        self.assertEqual(extended["extensions"][0]["additions"], {"requests": 5})
        result = command(
            "--run-id",
            "cli-two",
            "scope",
            "--from-run",
            "cli-one",
            "--companies",
            a["id"] + ",stripe",
        )
        self.assertEqual(result["run_id"], "cli-two")
        self.assertEqual(
            read_json(self.root / "runs/cli-two/dashboard-config.json")["scope_revision"]["added"],
            ["stripe"],
        )

    def test_bounded_official_career_link_can_verify_workday_ownership(self):
        company = self.library.add("Career Company", website="https://career-company.test")
        calls = []

        def probe(client, url, **kwargs):
            calls.append(url)
            if url == "https://career-company.test":
                return 200, {}, b'<a href="https://careers.career-company.test/">Careers</a>'
            if url == "https://careers.career-company.test/":
                return (
                    200,
                    {},
                    b'<a href="https://careercompany.wd1.myworkdayjobs.com/jobs">Open roles</a>',
                )
            return 200, {}, b'{"jobPostings":[],"total":0}'

        with patch.object(CollectionClient, "request", probe):
            result = self.library.verify(company["id"])
        self.assertEqual(result["source_status"], "verified")
        self.assertEqual(len(calls), 3)
        self.assertTrue(result["current_sources"][0]["ownership_basis"])

    def test_verification_rejects_another_company_board_linked_by_career_page(self):
        company = self.library.add(
            "Subsidiary",
            website="https://subsidiary.test",
            career_url="https://subsidiary.test/careers",
        )
        parent = self.library.get("paypal")

        def probe(client, url, **kwargs):
            if "subsidiary.test" in url:
                return 200, {}, b'<a href="https://paypal.wd1.myworkdayjobs.com/jobs">Jobs</a>'
            return 200, {}, b'{"jobPostings":[],"total":0}'

        with patch.object(CollectionClient, "request", probe):
            result = self.library.verify(company["id"])
        self.assertEqual(result["source_status"], "unsupported")
        self.assertEqual(len(result["current_sources"]), 1)
        self.assertEqual(result["source_conflicts"][0]["status"], "unsupported")
        self.assertIn("PayPal", result["discovery_outcome"])
        self.assertEqual(self.library.get("paypal"), parent)

    def test_verification_does_not_claim_ownership_from_catalog_membership(self):
        row = self.library.get("paypal")
        self.assertNotIn("ownership_basis", row["current_sources"][0])

        def probe(client, url, **kwargs):
            if url.endswith("/jobs"):
                return 200, {}, b'{"jobPostings":[],"total":0}'
            return 200, {}, b"<a href='/contact'>Contact</a>"

        with patch.object(CollectionClient, "request", probe):
            checked = self.library.verify("paypal")
        self.assertEqual(checked["current_sources"][0]["status"], "unverified")
        self.assertTrue(checked["current_sources"][0]["last_verified_at"])


if __name__ == "__main__":
    unittest.main()
