"""Synthetic contracts modeled on the verified official career HTML/JobPosting."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from degent.archive import ArchiveClient
from degent.capital_one import BASE, job_posting, listing_jobs, official_url, preselect
from degent.classify import classify_role
from degent.cli import vacancy_identity
from degent.config import load_config, observation_in_period
from degent.dashboard import Dashboard
from degent.extraction_run import refresh_observations
from degent.review import effective_extractions
from degent.sources import collect_current, observation
from degent.store import read_json, read_jsonl, write_json
from degent.workflow import run_pipeline


def url(number=1):
    return f"{BASE}/job/mclean/senior-business-analyst/1732/{number}"


def description():
    return (
        "Capital One\nResponsibilities\n"
        + "You will analyze consumer credit risk and explain credit performance to partners.\n" * 8
        + "Basic Qualifications\nAt least two years of analysis experience.\nSQL required.\n"
        "Preferred Qualifications\nBachelor degree in a quantitative field.\n"
    )


def detail(number=1, title="Senior Business Analyst", **changes):
    job = {
        "@type": "JobPosting",
        "title": title,
        "description": description(),
        "url": url(number),
        "hiringOrganization": {"name": "Capital One"},
        "identifier": {"value": f"R{number:06}"},
        "datePosted": "2023-01-01",
        "jobLocation": [
            {
                "address": {
                    "addressLocality": "McLean",
                    "addressRegion": "Virginia",
                    "addressCountry": "United States",
                }
            }
        ],
    }
    job.update(changes)
    return ('<script type="application/ld+json">' + json.dumps(job) + "</script>").encode()


def listing(rows, page=1, pages=1, keyword="credit"):
    links = "".join(
        f'<li><a href="{u}"><h2>{title}</h2><span class="job-location">{loc}</span></a></li>'
        for u, title, loc in rows
    )
    return (
        f'<section id="search-results" data-keywords="{keyword}" data-current-page="{page}" '
        f'data-total-pages="{pages}" data-total-results="{len(rows)}">'
        f'<section id="search-results-list"><ul>{links}</ul></section></section>'
    ).encode()


class CapitalOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.employer = next(e for e in self.config["employers"] if e["id"] == "capital_one")
        self.employer["current_sources"][0].update(keywords=["credit"], max_pages=2)
        self.config.update(
            run_dir=str(self.root / "runs"),
            data_dir=str(self.root / "data"),
            current_request_cap=8,
            current_document_cap=2,
            sources=["current"],
        )
        self.config["employers"] = [self.employer]
        self.config["roles"].update(tracks=["credit_risk_analytics"], entry_mid_levels=["senior"])
        self.directory = self.root / "runs" / "pilot"

    def request_mock(self, replies):
        def request(client, address, *args):
            self.assertLess(client.request_count, client.request_limit)
            client.request_count += 1
            client.on_request({"url": address})
            response = replies.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        return request

    def test_seniority_and_quantitative_degree_do_not_pool_tracks(self):
        role = classify_role("Senior Business Analyst", description(), self.config)
        self.assertTrue(role["eligible"])
        self.assertEqual(role["level"], "senior")
        self.assertEqual(role["subtype"], "general")  # "Capital One" is not capital analytics.
        title = "Senior Associate, Quantitative Analyst - Commercial Credit Modeling Team"
        role = classify_role(title, description(), self.config)
        self.assertEqual(role["level"], "senior")
        self.assertEqual(role["reason"], "track_outside_scope")
        quant = copy.deepcopy(self.config)
        quant["roles"]["tracks"] = ["quantitative_credit_risk_capital_modeling"]
        self.assertTrue(classify_role(title, description(), quant)["eligible"])
        self.assertFalse(
            preselect({"title": "Senior Business Analyst", "location": "McLean, VA"}, quant)[0]
        )
        self.assertFalse(classify_role("Senior Business Analyst", description(), quant)["eligible"])
        self.assertEqual(
            classify_role("Senior Manager, Credit Risk", description(), self.config)["level"],
            "management",
        )
        self.assertFalse(
            classify_role("Credit Risk Analyst", description(), self.config)["eligible"]
        )

    def test_capital_one_template_credit_paragraph_is_not_role_specific_evidence(self):
        template = (
            "Capital One\nGeneral Responsibilities:\n"
            "Analysis: Identify business challenges.\n"
            "Credit Risk: Support step-change improvements in credit performance by creating risk models.\n"
            "Strategic & analytic orientation: Strong quantitative orientation in marketing & credit risk management.\n"
            "Basic Qualifications\nDegree in a quantitative field.\n"
        )
        for title in [
            "Sr. Business Analyst, Premium Products",
            "Sr. Business Analyst, Spend Experiences",
            "Sr. Business Analyst - People Strategy & Analytics",
        ]:
            result = classify_role(title, template, self.config)
            self.assertEqual(result["reason"], "template_only_credit_context")
            self.assertFalse(result["eligible"])
        self.assertTrue(
            classify_role(
                "Senior Business Analyst",
                "Our team projects consumer credit risk losses.\n" + template,
                self.config,
            )["eligible"]
        )
        result = classify_role(
            "Senior Staff Auditor - Credit Risk Management", description(), self.config
        )
        self.assertIn("audit_compliance_or_investigation_function", result["matched_exclusions"])
        self.assertFalse(
            preselect(
                {
                    "title": "Senior Staff Auditor - Credit Risk Management",
                    "location": "McLean, VA",
                },
                self.config,
            )[0]
        )

    def test_ambiguous_description_explains_tracks(self):
        role = classify_role(
            "Senior Analyst", description() + "\nFraud analytics responsibilities.", self.config
        )
        self.assertFalse(role["eligible"])
        self.assertEqual(role["reason"], "no_unique_track_match")
        self.assertIn("disambiguation", role["explanation"])
        self.assertIn("fraud_analytics_strategy", role["tracks"])

    def test_title_and_available_description_prefilter(self):
        for title in [
            "Senior Software Engineer - Credit",
            "Credit Analyst I",
            "Senior Manager, Commercial Credit Review",
            "Senior Associate, Quantitative Analyst - Commercial Credit Modeling Team",
        ]:
            self.assertFalse(
                preselect({"title": title, "location": "McLean, VA"}, self.config)[0], title
            )
        self.assertTrue(
            preselect({"title": "Senior Business Analyst", "location": "McLean, VA"}, self.config)[
                0
            ]
        )
        self.assertFalse(
            preselect(
                {
                    "title": "Senior Business Analyst",
                    "text": "Marketing conversion analysis.",
                    "description_available": True,
                },
                self.config,
            )[0]
        )
        self.assertFalse(
            preselect(
                {"title": "Senior Credit Risk Analyst", "location": "Toronto, Canada"}, self.config
            )[0]
        )

    def test_parser_preserves_requisition_date_and_complete_body(self):
        row = job_posting(detail(), url())
        self.assertEqual(row["posting_id"], "R000001")
        self.assertEqual(row["stated_publication_at"], "2023-01-01")
        self.assertIn("Preferred Qualifications", row["text"])
        self.assertIn("United States", row["location"])
        item = observation(
            self.config, "pilot", self.employer, row, "capital_one", "2026-10-08T15:00:00+00:00"
        )
        self.assertTrue(item["body_complete"])
        self.assertIsNone(item["archive_capture_at"])
        self.assertIsNone(item["archive_url"])
        self.assertEqual(item["capture_date"], "2026-10-08")
        self.assertEqual(item["requisition_id"], "R000001")
        self.assertFalse(
            observation_in_period(
                item, {"id": "2023-H1", "start": "2023-01-01", "end": "2026-12-31"}
            )
        )
        self.assertTrue(
            observation_in_period(
                item, {"id": "CURRENT-2026-10-08", "start": "2026-10-08", "end": "2026-10-08"}
            )
        )

    def test_source_validation_rejects_shell_wrong_employer_and_missing_id(self):
        for payload in [
            b"<h1>Job not found</h1>",
            detail(hiringOrganization={"name": "Other Bank"}),
            detail(identifier={}),
            detail(url=url(99)),
        ]:
            with self.assertRaises(ValueError):
                job_posting(payload, url())
        for address in [
            "https://capitalonecareers.com.evil.test/job/x/y/1/2",
            "http://www.capitalonecareers.com/job/x/y/1/2",
            "https://user@www.capitalonecareers.com/job/x/y/1/2",
        ]:
            with self.assertRaises(ValueError):
                official_url(address, job=True)

    def test_listing_must_honor_keyword_and_page(self):
        payload = listing([(url() + "?utm_source=x", "Senior Credit Risk Analyst", "McLean, VA")])
        rows, pages, _ = listing_jobs(payload, "credit", 1)
        self.assertEqual(rows[0]["source_url"], url())
        self.assertEqual(pages, 1)
        for keyword, page in [("fraud", 1), ("credit", 2)]:
            with self.assertRaises(ValueError):
                listing_jobs(payload, keyword, page)
        self.assertEqual(vacancy_identity("capital_one", url() + "?src=x")[1], "1")

    def test_dedup_prefilter_limits_and_resume_have_no_extra_requests(self):
        self.config["current_document_cap"] = 1
        rows = [
            (url(), "Senior Credit Risk Analyst", "McLean, VA"),
            (url() + "?utm_source=x", "Senior Credit Risk Analyst", "McLean, VA"),
            (url(2), "Senior Manager, Credit Risk", "McLean, VA"),
            (url(3), "Senior Credit Risk Analyst", "McLean, VA"),
        ]
        replies = [(200, {}, listing(rows)), (200, {}, detail())]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = collect_current(self.config, "pilot")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["requests_used"], 2)
        self.assertEqual(result["total_saved"], 1)
        with patch.object(
            ArchiveClient, "request", side_effect=AssertionError("No budget for another document")
        ):
            self.assertEqual(collect_current(self.config, "pilot")["saved"], 0)
        self.assertEqual(len(read_jsonl(self.directory / "observations.jsonl")), 1)
        decisions = read_jsonl(self.directory / "current-selection.jsonl")
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[1]["status"], "rejected_before_retrieval")

    def test_repeated_requisition_capture_deduplicates_across_site_links(self):
        rows = [
            (url(), "Senior Credit Risk Analyst", "McLean, VA"),
            (url(2), "Senior Credit Risk Analyst", "McLean, VA"),
        ]
        replies = [
            (200, {}, listing(rows)),
            (200, {}, detail()),
            (200, {}, detail(2, identifier={"value": "R000001"})),
        ]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = collect_current(self.config, "pilot")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(
            result["total_saved"], 2
        )  # Both actual downloaded bodies consume allowance.
        decisions = read_jsonl(self.directory / "current-selection.jsonl")
        self.assertTrue(decisions[-1]["duplicate"])
        self.assertEqual(decisions[0]["document_id"], decisions[1]["document_id"])

    def test_request_cap_survives_interruption_and_resume(self):
        self.config["current_request_cap"] = 1
        replies = [(200, {}, listing([(url(), "Senior Credit Risk Analyst", "McLean, VA")]))]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = collect_current(self.config, "pilot")
        self.assertEqual(result["saved"], 0)
        self.assertEqual(next(iter(result["sources"].values()))["status"], "request_limit")
        with patch.object(
            ArchiveClient, "request", side_effect=AssertionError("Saved cap must survive resume")
        ):
            collect_current(self.config, "pilot")
        self.assertEqual(read_json(self.directory / "manifest.json")["current_requests_used"], 1)

    def test_cached_final_body_resumes_after_interrupt_without_retrieval(self):
        self.config["current_document_cap"] = 1
        replies = [
            (200, {}, listing([(url(), "Senior Credit Risk Analyst", "McLean, VA")])),
            (200, {}, detail()),
        ]
        with (
            patch.object(ArchiveClient, "request", self.request_mock(replies)),
            patch("degent.sources.observation", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                collect_current(self.config, "pilot")
        with patch.object(
            ArchiveClient, "request", side_effect=AssertionError("Body already cached")
        ):
            self.assertEqual(collect_current(self.config, "pilot")["saved"], 1)
        self.assertEqual(read_json(self.directory / "manifest.json")["current_documents_saved"], 1)

    def test_failed_source_is_explicit_and_raw_rejections_count_against_cap(self):
        replies = [
            (200, {}, listing([(url(), "Senior Credit Risk Analyst", "McLean, VA")])),
            (200, {}, b"<h1>Job not found</h1>"),
        ]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = collect_current(self.config, "pilot")
        self.assertEqual(result["total_saved"], 1)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(next(iter(result["sources"].values()))["status"], "partial_failure")
        self.assertEqual(
            read_jsonl(self.directory / "current-selection.jsonl")[0]["status"], "parse_failed"
        )

    def test_http_failure_and_unsupported_sources_are_reported(self):
        replies = [(503, {"error": "provider unavailable"}, b"")]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = collect_current(self.config, "pilot")
        self.assertEqual(next(iter(result["sources"].values()))["status"], "unavailable")
        self.employer["current_sources"] = [{"provider": "not-implemented"}]
        with patch.object(
            ArchiveClient, "request", side_effect=AssertionError("Unsupported provider")
        ):
            result = collect_current(self.config, "pilot")
        self.assertTrue(any(v["status"] == "unsupported" for v in result["sources"].values()))
        self.employer["current_sources"] = []
        self.assertEqual(
            collect_current(self.config, "pilot")["sources"]["capital_one:current"]["status"],
            "unsupported",
        )

    def test_complete_pipeline_leaves_review_pending_and_checks_raw_hash(self):
        self.config["periods"] = [
            {
                "id": "CURRENT-2026-10-08",
                "start": "2026-01-01",
                "end": "2026-12-31",
                "kind": "partial",
            }
        ]
        replies = [
            (200, {}, listing([(url(), "Senior Credit Risk Analyst", "McLean, VA")])),
            (200, {}, detail()),
        ]
        with patch.object(ArchiveClient, "request", self.request_mock(replies)):
            result = run_pipeline(self.config, "pilot")
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse((self.directory / "reviews.jsonl").exists())
        docs = effective_extractions(self.directory)
        self.assertTrue(docs[0]["accepted"])
        self.assertTrue(all(r["human_review"] == "pending" for r in docs[0]["requirements"]))
        obs = read_jsonl(self.directory / "observations.jsonl")[0]
        Path(obs["raw_source_file"]).write_bytes(b"tampered")
        self.assertEqual(
            refresh_observations(self.directory, self.config)[0]["body_status"],
            "raw_source_hash_mismatch",
        )

    def test_new_run_catalog_budget_and_old_run_preservation(self):
        config_file = self.root / "config.json"
        write_json(config_file, self.config)
        app = Dashboard(self.root, config_file)
        app.create_run(
            {
                "run_id": "old",
                "sources": ["current"],
                "periods": [],
                "employers": ["capital_one"],
                "tracks": ["credit_risk_analytics"],
                "levels": ["senior"],
                "current_document_cap": 1,
                "current_request_cap": 3,
            }
        )
        old = (app.run_dir("old") / "dashboard-config.json").read_bytes()
        app.create_run(
            {"run_id": "new", "sources": ["current"], "periods": [], "current_document_cap": 10}
        )
        self.assertEqual((app.run_dir("old") / "dashboard-config.json").read_bytes(), old)
        self.assertEqual(app.config("old")["current_request_cap"], 3)
        self.assertTrue(all(p["id"].startswith("CURRENT-") for p in app.config("old")["periods"]))
        self.assertIn("capital_one", [e["id"] for e in app.state("old")["catalog"]["employers"]])
        for value in [0, -1, True]:
            with self.assertRaises(ValueError):
                app.create_run(
                    {
                        "run_id": "invalid",
                        "sources": ["current"],
                        "periods": [],
                        "current_document_cap": value,
                    }
                )


if __name__ == "__main__":
    unittest.main()
