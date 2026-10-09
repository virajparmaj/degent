"""Behavior checks for reviewed, dated requirement research."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from degent.archive import ArchiveClient
from degent.classify import classify_geography, classify_role
from degent.cli import extract, manifest_for, run_directory
from degent.comparisons import requirement_shares
from degent.config import load_config
from degent.dashboard import Dashboard
from degent.extract import rule_candidates, validate_output
from degent.extraction_run import reserved_call
from degent.review import document_reviewed, effective_extractions, extraction_identity
from degent.sources import collect_current, feed_jobs, import_postings
from degent.store import append_jsonl, read_json, read_jsonl, write_json, write_jsonl
from degent.workflow import run_pipeline


class MeaningTests(unittest.TestCase):
    def labels(self, text):
        rows = rule_candidates("test", text)["requirements"]
        self.assertTrue(
            validate_output({"document_id": "test", "requirements": rows}, "test", text)[0]
        )
        return {(r["concept"], r["status"], r["statement_scope"]) for r in rows}

    def test_agency_does_not_imply_every_work_habit(self):
        self.assertEqual(
            self.labels("Qualifications: High agency required."),
            {("initiative", "required", "qualification")},
        )

    def test_negation_and_independence_have_different_meanings(self):
        rows = self.labels(
            "Qualifications\nNo Python experience required.\nWork independently without supervision."
        )
        self.assertIn(("python", "not_required", "qualification"), rows)
        self.assertIn(("independent_work", "required", "qualification"), rows)

    def test_recruiter_ai_and_company_product_are_not_applicant_requirements(self):
        rows = self.labels(
            "We use AI tools to screen candidate applications.\nAbout us\nWe build AI tools for clients."
        )
        self.assertTrue(rows)
        self.assertFalse(any(scope == "qualification" for _, _, scope in rows))
        self.assertTrue(any(scope == "hiring_process" for _, _, scope in rows))

    def test_culture_and_qualifications_remain_separate(self):
        rows = self.labels(
            "Our culture\nWe value high agency.\nWhat We Look For\nCommunication skills required.\nNice to have\nSQL."
        )
        self.assertIn(("initiative", "unclear", "company_expectation"), rows)
        self.assertIn(("communication", "required", "qualification"), rows)
        self.assertIn(("sql", "preferred", "qualification"), rows)

    def test_remote_country_and_incidental_words(self):
        self.assertEqual(
            classify_geography("", "Location: Remote US")["status"], "remote_us_eligible"
        )
        self.assertEqual(
            classify_geography("", "Location: Remote UK")["status"], "outside_us_scope"
        )
        self.assertEqual(
            classify_geography("", "Location: Remote\nWork IN or outside our offices.")["status"],
            "unknown",
        )

    def test_seniority_is_not_silently_pooled(self):
        config = load_config()
        self.assertFalse(classify_role("Senior Credit Risk Analyst", "", config)["eligible"])
        config["roles"]["entry_mid_levels"] = ["senior"]
        self.assertEqual(classify_role("Senior Credit Risk Analyst", "", config)["level"], "senior")

    def test_unknown_concept_is_not_accepted_as_known(self):
        output = rule_candidates("test", "Qualifications: SQL required.")
        output["requirements"][0]["concept"] = "invented_skill"
        self.assertFalse(validate_output(output, "test", "Qualifications: SQL required.")[0])

    def test_provider_schema_requires_every_property(self):
        schema = read_json(
            Path(__file__).resolve().parent.parent / "schemas/requirement-v2.schema.json"
        )
        item = schema["properties"]["requirements"]["items"]
        self.assertEqual(set(item["required"]), set(item["properties"]))

    def test_standard_cdx_json_and_pagination(self):
        data = [
            ["timestamp", "original", "statuscode"],
            ["20240101000000", "https://example.test/job", "200"],
        ]
        client = ArchiveClient("test", retries=0, delay=0)
        for suffix, expected in [("", ""), ("\n\nnext-page-key", "next-page-key")]:
            with (
                self.subTest(suffix=suffix),
                patch.object(
                    client, "request", return_value=(200, {}, (json.dumps(data) + suffix).encode())
                ),
            ):
                status, rows, resume = client.wayback(
                    "example.test/*", "2024-01-01", "2024-06-30", "https://example.test/cdx"
                )
                self.assertEqual((status, len(rows), resume), (200, 1, expected))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config.update(
            run_dir=str(self.root / "runs"), data_dir=str(self.root / "data"), sources=["imports"]
        )
        self.config["employers"] = [
            next(e for e in self.config["employers"] if e["id"] == "affirm")
        ]
        self.config["periods"] = [
            {"id": "2025-Q1", "start": "2025-01-01", "end": "2025-03-31", "kind": "completed"}
        ]
        self.run = "check"
        self.directory = run_directory(self.config, self.run)
        self.row = {
            "employer_id": "affirm",
            "title": "Credit Risk Analyst II",
            "location": "Remote US",
            "source_url": "https://example.test/jobs/42",
            "text": "Responsibilities\n"
            + (
                "Evaluate credit risk using evidence and explain results to business partners.\n"
                * 9
            )
            + "Qualifications\nSQL required.\nCommunication skills required.",
        }

    def import_rows(self, rows):
        path = self.root / "import.json"
        write_json(path, rows)
        return import_postings(self.config, self.run, path)

    def test_undated_and_attested_dates_do_not_become_publication_dates(self):
        self.import_rows([self.row])
        dated = {
            **self.row,
            "captured_at": "2025-02-02T00:00:00+02:00",
            "capture_date_source": "Saved by the user",
        }
        self.import_rows([dated])
        first, second = read_jsonl(self.directory / "observations.jsonl")
        self.assertTrue(first["body_complete"])
        self.assertFalse(first["eligible_for_primary"])
        self.assertIsNone(first["capture_at"])
        self.assertEqual(second["capture_at"], "20250201220000")
        self.assertEqual(second["evidence_basis"], "user_attested_capture")
        self.assertIsNone(second["stated_publication_at"])
        self.assertNotEqual(first["raw_file"], second["raw_file"])
        self.assertTrue(Path(first["raw_file"]).exists())

    def test_bad_import_validates_entire_batch_before_writing(self):
        invalid = {**self.row, "captured_at": "2025-01-01"}
        with self.assertRaises(ValueError):
            self.import_rows([self.row, invalid])
        self.assertEqual(read_jsonl(self.directory / "observations.jsonl"), [])

    def test_import_deduplication_and_cumulative_limit(self):
        self.config["import_document_cap"] = 1
        self.assertEqual(self.import_rows([self.row])["imported"], 1)
        self.assertEqual(self.import_rows([self.row])["imported"], 0)
        self.assertEqual(
            self.import_rows([{**self.row, "source_url": "https://example.test/jobs/43"}])[
                "imported"
            ],
            0,
        )
        self.assertEqual(len(read_jsonl(self.directory / "observations.jsonl")), 1)

    def test_feed_dates_are_preserved_without_inventing_publication(self):
        jobs = feed_jobs(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": 42,
                            "content": "<h2>Qualifications</h2><p>SQL</p>",
                            "location": {"name": "US"},
                            "updated_at": "2025-01-01",
                        }
                    ]
                }
            ).encode(),
            {"provider": "greenhouse"},
        )
        self.assertIsNone(jobs[0]["stated_publication_at"])
        self.assertEqual(jobs[0]["stated_updated_at"], "2025-01-01")
        ashby = feed_jobs(
            json.dumps({"jobs": [{"publishedAt": "2025-01-01"}]}).encode(), {"provider": "ashby"}
        )
        self.assertIsNone(ashby[0]["stated_publication_at"])
        self.assertIn("last published", ashby[0]["update_date_source"])

    def test_current_feed_resume_uses_cache_and_document_limit(self):
        self.config["current_document_cap"] = 1
        self.config["current_request_cap"] = 1
        payload = {
            "jobs": [
                dict(
                    id=i,
                    title=self.row["title"],
                    location={"name": "Remote US"},
                    absolute_url=f"https://example.test/jobs/{i}",
                    content=self.row["text"],
                )
                for i in range(2)
            ]
        }

        def request(client, *args):
            client.request_count += 1
            client.on_request({})
            return 200, {}, json.dumps(payload).encode()

        with patch.object(ArchiveClient, "request", request):
            result = collect_current(self.config, self.run)
        self.assertEqual(result["saved"], 1)
        with patch.object(
            ArchiveClient, "request", side_effect=AssertionError("must reuse cached feed")
        ):
            self.assertEqual(collect_current(self.config, self.run)["saved"], 0)
        self.assertEqual(read_json(self.directory / "manifest.json")["current_requests_used"], 1)

    def test_model_calls_are_reserved_before_runner_crash(self):
        manifest = manifest_for(self.directory, self.run)
        self.config["model_call_cap"] = 1

        def crash(*args):
            self.assertEqual(read_json(self.directory / "manifest.json")["model_calls_used"], 1)
            raise OSError("interrupted")

        with self.assertRaises(OSError):
            reserved_call(self.directory, manifest, self.config, crash, text="x", doc_id="d")
        with self.assertRaisesRegex(RuntimeError, "model_call_limit"):
            reserved_call(self.directory, manifest, self.config, crash, text="x", doc_id="d")

    def test_provider_failure_never_accepts_rule_fallback(self):
        self.import_rows([self.row])
        with patch("degent.cli.codex_extract", return_value=(None, "usage limit", 1)) as runner:
            result = extract(self.config, self.run, True)
        self.assertEqual(result["pause_reason"], "usage_limit")
        self.assertEqual(runner.call_count, 1)
        self.assertFalse(read_jsonl(self.directory / "requirements.jsonl")[-1]["accepted"])

    def test_document_approval_survives_effective_labels_and_expires_on_reextraction(self):
        self.import_rows([self.row])
        extract(self.config, self.run, False)
        write_json(self.directory / "dashboard-config.json", self.config)
        app = Dashboard(self.root)
        record = effective_extractions(self.directory)[0]
        app.review_document(
            self.run,
            {"document_id": record["document_id"], "extraction_id": extraction_identity(record)},
        )
        self.assertTrue(document_reviewed(effective_extractions(self.directory)[0]))
        original = read_jsonl(self.directory / "requirements.jsonl")[-1]
        original["cache_key"] = "new-version"
        append_jsonl(self.directory / "requirements.jsonl", [original])
        self.assertFalse(document_reviewed(effective_extractions(self.directory)[0]))

    def test_empty_extraction_needs_document_approval(self):
        row = {"document_id": "empty", "accepted": True, "requirements": []}
        write_jsonl(self.directory / "requirements.jsonl", [row])
        self.assertFalse(document_reviewed(effective_extractions(self.directory)[0]))
        append_jsonl(
            self.directory / "document-reviews.jsonl", [{"extraction_id": extraction_identity(row)}]
        )
        self.assertTrue(document_reviewed(effective_extractions(self.directory)[0]))

    def test_rules_pipeline_stops_for_human_review_and_resumes_without_reextracting(self):
        self.import_rows(
            [{**self.row, "captured_at": "2025-02-01T10:00:00Z", "capture_date_source": "Fixture"}]
        )
        result = run_pipeline(self.config, self.run)
        self.assertEqual(result["status"], "needs_review")
        record = effective_extractions(self.directory)[0]
        append_jsonl(
            self.directory / "document-reviews.jsonl",
            [{"extraction_id": extraction_identity(record)}],
        )
        self.assertEqual(run_pipeline(self.config, self.run)["status"], "complete")
        self.assertEqual(len(read_jsonl(self.directory / "requirements.jsonl")), 1)
        self.assertTrue((self.directory / "requirement-combinations.csv").exists())


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.config["periods"] = [
            {"id": "a", "start": "2025-01-01", "end": "2025-03-31", "kind": "completed"},
            {"id": "b", "start": "2025-04-01", "end": "2025-06-30", "kind": "completed"},
        ]
        self.config.update(combinations=[["sql", "communication"]], watched_concepts=["sql"])
        self.obs, self.docs = [], {}
        for period, stamp in [("a", "2025-02-01"), ("b", "2025-05-01")]:
            for i in range(5):
                key = f"{period}-{i}"
                self.obs.append(
                    dict(
                        document_id=key,
                        vacancy_id=key,
                        employer_id="affirm",
                        role_track="credit_risk_analytics",
                        role_subtype="general",
                        level="mid",
                        capture_date=stamp,
                        capture_at=stamp,
                    )
                )
                self.docs[key] = dict(
                    accepted=True,
                    document_review="approved",
                    method_signature="v2",
                    requirements=[self.requirement("sql"), self.requirement("communication")],
                )

    def requirement(self, concept):
        return dict(
            concept=concept,
            phrase=concept,
            explicit=True,
            status="required",
            statement_scope="qualification",
        )

    def rows(self, mode="concept"):
        return [
            r
            for r in requirement_shares(self.obs, self.docs, self.config, mode)
            if r["concept"] == ("sql" if mode == "concept" else "sql + communication")
        ]

    def test_pending_and_unknown_are_not_absence(self):
        self.docs["a-0"]["document_review"] = "pending"
        self.docs["a-1"]["requirements"] = [dict(self.requirement("sql"), status="unclear")]
        row = self.rows()[0]
        self.assertEqual(
            (row["assessed_vacancies"], row["unresolved_vacancies"], row["count"]), (3, 2, 3)
        )
        self.assertIsNone(row["share_percent"])

    def test_unmapped_education_does_not_make_sql_absence_unknown(self):
        self.docs["a-0"]["requirements"] = [dict(self.requirement(None), category="education")]
        self.assertEqual(self.rows()[0]["assessed_vacancies"], 5)
        self.docs["a-0"]["requirements"][0]["category"] = "technical"
        self.assertEqual(self.rows()[0]["assessed_vacancies"], 4)

    def test_combinations_require_same_posting(self):
        self.docs["a-0"]["requirements"] = [self.requirement("sql")]
        self.docs["a-1"]["requirements"] = [self.requirement("communication")]
        self.assertEqual(self.rows("combination")[0]["count"], 3)

    def test_latest_unextracted_version_cannot_fall_back(self):
        newer = {
            **self.obs[0],
            "document_id": "new",
            "capture_at": "2025-03-01",
            "capture_date": "2025-03-01",
        }
        self.obs.append(newer)
        self.assertEqual(self.rows()[0]["assessed_vacancies"], 4)

    def test_mixed_methods_suppress_percentages(self):
        self.docs["a-0"]["method_signature"] = "old"
        row = self.rows()[0]
        self.assertIsNone(row["share_percent"])
        self.assertEqual(row["unavailable_reason"], "mixed_extraction_versions")

    def test_equal_quarters_compare_but_unequal_windows_do_not(self):
        self.assertEqual(self.rows()[1]["difference_percentage_points"], 0)
        self.config["periods"][1]["end"] = "2025-09-30"
        self.assertIsNone(self.rows()[1]["difference_percentage_points"])

    def test_required_and_preferred_are_distinct(self):
        self.docs["a-0"]["requirements"][0]["status"] = "preferred"
        self.config["comparison_status"] = "required"
        self.assertEqual(self.rows()[0]["count"], 4)
        self.config["comparison_status"] = "preferred"
        self.assertEqual(self.rows()[0]["count"], 1)

    def test_job_tasks_need_an_explicit_stated_view(self):
        self.config.update(comparison_scope="responsibility", comparison_status="required")
        for record in self.docs.values():
            record["requirements"] = [
                dict(self.requirement("sql"), status="unclear", statement_scope="responsibility")
            ]
        self.assertEqual(self.rows()[0]["count"], 0)
        self.config["comparison_status"] = "stated"
        self.assertEqual(self.rows()[0]["count"], 5)

    def test_import_date_claims_do_not_mix_with_archive_dates(self):
        self.obs[0]["evidence_basis"] = "user_attested_capture"
        rows = self.rows()
        self.assertEqual(
            {r["evidence_basis"] for r in rows}, {"archive_capture", "user_attested_capture"}
        )
        self.assertTrue(all(r["share_percent"] is None for r in rows if r["period"] == "a"))


if __name__ == "__main__":
    unittest.main()
