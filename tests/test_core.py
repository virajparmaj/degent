from __future__ import annotations

import gzip
import json
import collections
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from degent.archive import ArchiveClient, collection_for_period
from degent.compare import _requirement_changes, _requirement_shares, build_report
from degent.config import employers, load_config, periods
from degent.cli import _title_from_body, canonical_vacancy_url, discovery_queries, fetch, select_fetch_records, vacancy_identity
from degent.classify import classify_geography, classify_role
from degent.extract import codex_failure_status, extraction_cache_key, load_aliases, rule_candidates, validate_output
from degent.store import body_usability, clean_html, read_jsonl, write_json, write_jsonl


class CoreTests(unittest.TestCase):
    def test_gzip_archive_html_preserves_title_and_description(self):
        html = (
            b"<h1>Credit Risk Analyst II</h1><p>Affirm</p>"
            b"<p>Responsibilities: Evaluate credit risk using SQL.</p>"
            b"<p>Qualifications: Experience with quantitative analysis.</p>"
        )
        compressed = gzip.compress(html)
        self.assertEqual(clean_html(compressed), clean_html(html))
        self.assertEqual(
            _title_from_body(compressed, "https://example.test/jobs/123"),
            "Credit Risk Analyst II",
        )

    def test_fetch_tracking_variants_do_not_consume_unique_or_pair_slots(self):
        config = load_config()
        record = {
            "employer_id": "affirm", "period_id": "2023-H1", "capture_date": "2023-02-01",
            "capture_at": "20230201000000", "source": "wayback",
            "original_url": "https://example.test/jobs/credit-risk-analyst-1",
        }
        rows = [record, {**record, "original_url": record["original_url"] + "?utm_source=board"}]
        rows.append({**record, "period_id": "2023-H2", "capture_date": "2023-08-01", "capture_at": "20230801000000"})
        rows.append({**rows[-1], "original_url": record["original_url"] + "?gh_src=another"})
        selected = select_fetch_records(rows, employers(config), 150)
        self.assertEqual(len(selected), 2)
        self.assertEqual({r["period_id"] for r in selected}, {"2023-H1", "2023-H2"})

    def test_html_parser_removes_scripts_and_rejects_shells(self):
        clean = clean_html(b"<h1>Credit Risk Analyst</h1><script>fake text</script><p>Responsibilities</p>")
        self.assertIn("Credit Risk Analyst", clean)
        self.assertNotIn("fake text", clean)
        ok, reason = body_usability("Enable JavaScript " + "x" * 1000, "Affirm")
        self.assertFalse(ok)
        self.assertEqual(reason, "javascript_or_consent_shell")
        shell = clean_html(b'<title>Credit Risk Analyst</title><code style="display: none">Responsibilities Qualifications ' + b"x" * 1000 + b"</code>")
        self.assertNotIn("Qualifications", shell)
        self.assertFalse(body_usability(shell, "Affirm")[0])

    def test_warc_record_payload_and_target_are_validated(self):
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<h1>Analyst</h1>"
        header = ("WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: https://jobs.example/role/1\r\nContent-Length: " + str(len(response)) + "\r\n\r\n").encode()
        payload, target = ArchiveClient._warc_payload_and_target(gzip.compress(header + response))
        self.assertEqual(payload, b"<h1>Analyst</h1>")
        self.assertEqual(target, "https://jobs.example/role/1")
        self.assertIsNone(ArchiveClient._warc_payload(b"not warc"))

    def test_wayback_redirect_to_a_different_capture_is_rejected(self):
        class Response(BytesIO):
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def geturl(self): return "https://web.archive.org/web/20240101000000id_/https://jobs.example/role"
        client = ArchiveClient("test", retries=0, delay=0)
        with patch("urllib.request.urlopen", return_value=Response(b"content")):
            status, headers, body, _ = client.fetch_wayback("20240201000000", "https://jobs.example/role")
        self.assertEqual(status, 0)
        self.assertEqual(headers["error"], "wrong_capture_replay")
        self.assertEqual(body, b"")

    def test_commoncrawl_target_url_mismatch_is_rejected(self):
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<h1>Analyst</h1>"
        header = ("WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: https://jobs.example/other\r\nContent-Length: " + str(len(response)) + "\r\n\r\n").encode()
        compressed = gzip.compress(header + response)
        class Response(BytesIO):
            status = 206
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def geturl(self): return "https://data.commoncrawl.org/example.warc.gz"
        client = ArchiveClient("test", retries=0, delay=0)
        with patch("urllib.request.urlopen", return_value=Response(compressed)):
            status, headers, _, _ = client.fetch_commoncrawl({"filename": "example.warc.gz", "offset": 0, "length": len(compressed), "url": "https://jobs.example/expected"})
        self.assertEqual(status, 0)
        self.assertEqual(headers["error"], "warc_target_mismatch")

    def test_evidence_quotes_are_exact_and_offsets_are_computed(self):
        source = "Qualifications: 3+ years of Python experience or R. SQL preferred."
        output = {"document_id": "d1", "requirements": [{"category": "skill", "phrase": "3+ years of Python", "status": "required", "explicit": True, "evidence": "Python"}]}
        valid, errors = validate_output(output, "d1", source)
        self.assertTrue(valid, errors)
        self.assertEqual(output["requirements"][0]["evidence_start"], source.index("Python"))
        invalid = {"document_id": "d1", "requirements": [{"evidence": "Python programming"}]}
        valid, errors = validate_output(invalid, "d1", source)
        self.assertFalse(valid)
        self.assertIn("not_exact_source_quote", [error["reason"] for error in errors])

    def test_rule_candidates_preserve_alternatives_negation_and_scoped_years(self):
        result = rule_candidates("d", "You have 3+ years of Python or R experience. No SQL experience required. Python is preferred.")
        python = next(r for r in result["requirements"] if r["concept"] == "python")
        self.assertEqual(python["experience_years"], 3.0)
        self.assertEqual(python["status"], "required")
        self.assertIn("R experience", python["alternatives"])
        sql = next(r for r in result["requirements"] if r["concept"] == "sql")
        self.assertTrue(sql["negated"])
        self.assertEqual(sql["status"], "not_required")

    def test_rule_candidates_load_versioned_aliases(self):
        result = rule_candidates("d", "R programming is required.", load_aliases())
        row = next(r for r in result["requirements"] if r["concept"] == "r")
        self.assertEqual(row["phrase"], "R programming is required.")

    def test_model_phrase_must_be_an_exact_quote_containing_evidence(self):
        source = "Experience with Python is required."
        forged = {"document_id": "d", "requirements": [{"category": "skill", "phrase": "Python is mandatory", "status": "required", "explicit": True, "evidence": "Python"}]}
        valid, errors = validate_output(forged, "d", source)
        self.assertFalse(valid)
        self.assertIn("not_exact_source_quote", [error["reason"] for error in errors])
        quoted = {"document_id": "d", "requirements": [{"category": "skill", "phrase": source, "status": "required", "explicit": True, "evidence": "Python"}]}
        valid, errors = validate_output(quoted, "d", source)
        self.assertTrue(valid, errors)
        self.assertEqual(quoted["requirements"][0]["evidence_start"], source.index("Python"))

    def test_extraction_cache_key_tracks_source_parser_prompt_and_model(self):
        base = {"source_sha256": "source", "parser_version": "html-text-v1", "prompt_sha256": "prompt", "schema_sha256": "schema", "alias_sha256": "alias", "method": "codex", "model": "gpt-6-luna", "effort": "high"}
        key = extraction_cache_key(**base)
        self.assertEqual(key, extraction_cache_key(**base))
        self.assertNotEqual(key, extraction_cache_key(**{**base, "model": "gpt-6.1-sol"}))
        self.assertNotEqual(key, extraction_cache_key(**{**base, "source_sha256": "different"}))
        self.assertNotEqual(key, extraction_cache_key(**{**base, "review_model": "gpt-6.1-sol"}))

    def test_codex_failures_have_specific_nonretrying_statuses(self):
        self.assertEqual(codex_failure_status("usage limit reached", 1), "usage_limit")
        self.assertEqual(codex_failure_status("Timed out after 180 seconds", -1), "timeout")
        self.assertEqual(codex_failure_status("Model not found", 1), "model_unavailable")

    def test_collection_selection_uses_period_end_date(self):
        rows = [{"id": "CC-MAIN-A", "from": "2025-01-02T00:00:00", "to": "2025-01-19T00:00:00"}, {"id": "CC-MAIN-B", "from": "2025-03-12T00:00:00", "to": "2025-03-26T00:00:00"}]
        self.assertEqual(collection_for_period(rows, "2025-01-01", "2025-03-31")["id"], "CC-MAIN-B")
        self.assertIsNone(collection_for_period(rows, "2025-04-01", "2025-06-30"))

    def test_wayback_resume_key_is_exposed_and_reused(self):
        client = ArchiveClient("test", retries=0, delay=0)
        first = b'["timestamp","original","statuscode","mimetype","digest","length"]\n["20230101000000","https://jobs.example/1","200","text/html","abc","100"]\n{"resumeKey":"next-page"}\n'
        seen = []
        def fake_request(url, headers=None):
            seen.append(url)
            return 200, {}, first
        with patch.object(client, "request", side_effect=fake_request):
            status, rows, resume = client.wayback("jobs.example/*", "2023-01-01", "2023-06-30", "https://archive.example/cdx")
            client.wayback("jobs.example/*", "2023-01-01", "2023-06-30", "https://archive.example/cdx", resume)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(resume, "next-page")
        self.assertIn("resumeKey=next-page", seen[1])

    def test_long_retry_after_suspends_host_without_polling(self):
        client = ArchiveClient("test", retries=2, delay=0)
        headers = {"Retry-After": "120"}
        error = urllib.error.HTTPError("https://archive.example", 429, "limited", headers, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "rate_limited_suspend"):
                client.request("https://archive.example/query")
        self.assertEqual(client.request_count, 1)

    def test_report_marks_every_period_and_missing_periods_unavailable(self):
        config = load_config()
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "demo"
            run_dir.mkdir()
            report = build_report(run_dir, config)
            self.assertEqual(len(report["coverage"]), len(periods(config)))
            self.assertEqual(report["coverage"][0]["status"], "no_eligible_complete_descriptions")
            self.assertTrue((run_dir / "coverage.csv").exists())

    def test_report_emits_sourced_same_vacancy_changes(self):
        config = load_config()
        config["comparison_review"] = "accepted" # This test exercises the explicit exploratory view.
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "demo"
            run_dir.mkdir()
            write_json(run_dir / "manifest.json", {"body_attempts": []})
            observations = []
            requirements = []
            for suffix, capture_date, capture_at, status, years in (("a", "2023-06-01", "20230601000000", "required", 3), ("b", "2023-12-01", "20231201000000", "preferred", 5)):
                doc_id = "doc-" + suffix
                observations.append({"document_id": doc_id, "observation_id": "obs-" + suffix, "vacancy_id": "affirm:posting-id:123", "employer_id": "affirm", "employer": "Affirm", "title": "Credit Risk Analyst", "role_track": "credit_risk_analytics", "role_subtype": "general", "level": "mid", "capture_date": capture_date, "capture_at": capture_at, "eligible": True, "body_complete": True, "archive_url": "https://archive.example/" + suffix})
                requirements.append({"document_id": doc_id, "accepted": True, "requirements": [{"category": "skill", "concept": "python", "phrase": f"{years}+ years Python", "evidence": "Python", "status": status, "experience_years": years, "explicit": True}]})
            write_jsonl(run_dir / "observations.jsonl", observations)
            write_jsonl(run_dir / "requirements.jsonl", requirements)
            report = build_report(run_dir, config)
            self.assertEqual(len(report["same_vacancy_changes"]), 1)
            self.assertIn("experience_years", report["same_vacancy_changes"][0]["modified"][0]["fields"])
            text = (run_dir / "evidence-report.md").read_text()
            self.assertIn("before source", text)
            self.assertIn("after source", text)
            self.assertTrue((run_dir / "same-vacancy-changes.csv").exists())
            self.assertTrue((run_dir / "employer-period-coverage.csv").exists())

    def test_discovery_query_plan_covers_primary_and_alternate_namespaces(self):
        config = load_config()
        config["employers"] = [e for e in employers(config) if e["id"] != "capital_one"]
        collections = [{"id": "CC-MAIN-2026-39", "from": "2026-09-04T00:00:00", "to": "2026-09-17T00:00:00"}]
        queries = discovery_queries(config, collections)
        self.assertEqual(len(queries), 150)
        self.assertEqual(sum(q["source"] == "wayback" for q in queries), 72)
        self.assertEqual(sum(q["source"] == "commoncrawl" for q in queries), 78)

    def test_discovery_queries_every_intersecting_commoncrawl_collection(self):
        config = load_config()
        collections = [
            {"id": "CC-early", "from": "2023-01-01", "to": "2026-10-07"},
            {"id": "CC-middle", "from": "2023-01-01", "to": "2026-10-07"},
            {"id": "CC-late", "from": "2023-01-01", "to": "2026-10-07"},
        ]
        queries = discovery_queries(config, collections)
        target = [q for q in queries if q["employer_id"] == "stripe" and q["period_id"] == "2025-Q1" and q["source"] == "commoncrawl" and q["path"] == "stripe.com/jobs/*"]
        self.assertEqual({q["collection"] for q in target}, {"CC-early", "CC-middle", "CC-late"})

    def test_fetch_selection_is_bounded_and_deduplicates_identical_capture(self):
        config = load_config()
        records = []
        for employer in employers(config):
            for year, period in (("2023", "2023-H1"), ("2024", "2024-H1"), ("2025", "2025-Q1"), ("2026", "2026-Q1")):
                for idx in range(8):
                    url = f"https://jobs.example/{employer['id']}/credit-risk-analyst-{idx}"
                    records.append({"employer_id": employer["id"], "period_id": period, "capture_date": year + "-02-01", "capture_at": year + "0201000000", "original_url": url, "source": "commoncrawl", "source_record": {"status": "200"}})
        records.append(dict(records[0]))
        records.append({"employer_id": "stripe", "period_id": "2023-H1", "capture_date": "2023-02-02", "capture_at": "20230202000000", "original_url": "https://jobs.example/stripe/credit-risk-404", "source": "commoncrawl", "source_record": {"status": "404"}})
        selected = select_fetch_records(records, employers(config), 150)
        self.assertLessEqual(len(selected), 150)
        self.assertLessEqual(max(collections.Counter(r["employer_id"] for r in selected).values()), 25)
        keys = [(r["source"], r["capture_at"], r["original_url"]) for r in selected]
        self.assertEqual(len(keys), len(set(keys)))

    def test_fetch_prefers_latest_capture_within_each_year(self):
        config = load_config()
        rows = [
            {"employer_id": "stripe", "period_id": "2023-H1", "capture_date": "2023-02-01", "capture_at": "20230201000000", "original_url": "https://jobs.example/credit-risk-analyst", "source": "commoncrawl", "source_record": {"status": "200"}},
            {"employer_id": "stripe", "period_id": "2023-H1", "capture_date": "2023-06-20", "capture_at": "20230620000000", "original_url": "https://jobs.example/credit-risk-analyst", "source": "commoncrawl", "source_record": {"status": "200"}},
        ]
        selected = select_fetch_records(rows, employers(config), 150)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["capture_at"], "20230620000000")

    def test_global_fetch_cap_is_fair_after_employer_reallocation(self):
        config = load_config()
        rows = []
        for employer in employers(config):
            for year, period in (("2023", "2023-H1"), ("2024", "2024-H1"), ("2025", "2025-Q1"), ("2026", "2026-Q1")):
                for number in range(15):
                    rows.append({"employer_id": employer["id"], "period_id": period, "capture_date": f"{year}-02-{number + 1:02d}", "capture_at": f"{year}02{number + 1:02d}000000", "original_url": f"https://jobs.example/{employer['id']}/credit-risk-analyst-{number}", "source": "commoncrawl", "source_record": {"status": "200"}})
        caps = {employer["id"]: 60 for employer in employers(config)}
        selected = select_fetch_records(rows, employers(config), 150, caps)
        counts = collections.Counter(row["employer_id"] for row in selected)
        self.assertEqual(len(selected), 150)
        self.assertEqual(len(counts), len(employers(config)))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_role_gate_uses_title_to_reject_false_positive_context_matches(self):
        config = load_config()
        self.assertFalse(classify_role("Sales Content Manager", "Fraud analytics and credit risk are discussed here.", config)["eligible"])
        self.assertFalse(classify_role("Senior Machine Learning Engineer, Checkout Underwriting", "Credit underwriting model work.", config)["eligible"])
        accepted = classify_role("Credit Risk Analyst II", "Responsibilities include underwriting analytics and portfolio monitoring.", config)
        self.assertTrue(accepted["eligible"])
        self.assertEqual(accepted["tracks"], ["credit_risk_analytics"])

    def test_geography_gate_requires_posting_specific_us_evidence(self):
        self.assertEqual(classify_geography("https://jobs.example/credit-analyst-chicago-illinois-united-states", "") ["status"], "us_location")
        remote = classify_geography("https://jobs.example/remote-credit-analyst", "Location: Remote, US only")
        self.assertEqual(remote["status"], "remote_us_eligible")
        self.assertEqual(classify_geography("https://jobs.example/credit-analyst", "Location: Chicago, IL")["status"], "us_location")
        self.assertEqual(classify_geography("https://jobs.example/credit-analyst-dublin-ireland", "" )["status"], "outside_us_scope")
        self.assertEqual(classify_geography("https://jobs.example/credit-analyst", "Responsibilities include credit analysis.")["status"], "unknown")

    def test_job_title_prefers_archive_metadata_over_site_logo_heading(self):
        payload = b'''<html><head><title>Stripe logo Jobs</title><meta property="og:title" content="Credit Risk Analyst II"></head><body><h1 class="site-logo">Stripe Jobs</h1><h2>Role details</h2></body></html>'''
        self.assertEqual(_title_from_body(payload, "https://example.test/jobs/82736"), "Credit Risk Analyst II")

    def test_job_posting_jsonld_title_has_priority(self):
        payload = b'''<html><head><meta property="og:title" content="Generic Careers"></head><body><script type="application/ld+json">{"@type":"JobPosting","title":"Fraud Strategy Analyst"}</script></body></html>'''
        self.assertEqual(_title_from_body(payload, "https://example.test/jobs/123"), "Fraud Strategy Analyst")

    def test_apply_mirror_and_tracking_url_share_vacancy_identity(self):
        base = "https://boards.greenhouse.io/affirm/jobs/5722427003"
        mirror = base + "/apply?utm_source=archive"
        first = vacancy_identity("affirm", base)
        second = vacancy_identity("affirm", mirror)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], "5722427003")
        self.assertEqual(canonical_vacancy_url(mirror), base)

    def test_explicit_source_id_is_employer_scoped(self):
        first = vacancy_identity("affirm", "https://example.test/a", {"job_id": "42"})
        second = vacancy_identity("sofi", "https://example.test/a", {"job_id": "42"})
        self.assertNotEqual(first[0], second[0])

    def test_fetch_rate_limit_suspends_and_checkpoints_attempt(self):
        config = load_config()
        with tempfile.TemporaryDirectory() as temporary:
            config = dict(config)
            config.update({"run_dir": str(Path(temporary) / "runs"), "data_dir": str(Path(temporary) / "data"), "body_attempt_cap": 1, "per_employer_body_attempts": 1})
            run_id = "rate-limit"
            run_dir = Path(config["run_dir"]) / run_id
            run_dir.mkdir(parents=True)
            record = {"source": "wayback", "employer_id": "stripe", "period_id": "2025-Q1", "capture_date": "2025-02-01", "capture_at": "20250201000000", "original_url": "https://stripe.example/jobs/credit-risk-analyst", "archive_url": "https://web.archive.org/web/20250201000000id_/https://stripe.example/jobs/credit-risk-analyst", "source_record": {"statuscode": "200"}}
            (run_dir / "archive-records.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            with patch("degent.cli.ArchiveClient.fetch_wayback", side_effect=RuntimeError("rate_limited_suspend:archive.example:120s")):
                result = fetch(config, run_id)
            manifest = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "suspended_rate_limit")
            self.assertEqual(manifest["body_attempts"][0]["status"], "suspended_rate_limit")
            self.assertIn("original_url", manifest["body_attempts"][0])
            self.assertEqual(result["attempts"], 1)

    def test_jsonl_reader_preserves_duplicate_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "obs.jsonl"
            path.write_text('{"document_id":"same","capture_date":"2025-01-01"}\n{"document_id":"same","capture_date":"2025-04-01"}\n', encoding="utf-8")
            self.assertEqual(len(read_jsonl(path)), 2)

    def test_requirement_shares_have_denominators_and_do_not_bridge_missing_periods(self):
        config = load_config()
        config["comparison_review"] = "accepted" # This test exercises the explicit exploratory view.
        observations = []
        extractions = {}
        for period_id, capture in (("2023-H1", "2023-06-01"), ("2023-H2", "2023-12-01")):
            for number in range(5):
                doc_id = f"{period_id}-{number}"
                observations.append({"document_id": doc_id, "vacancy_id": f"vacancy-{period_id}-{number}", "employer_id": "affirm", "role_track": "credit_risk_analytics", "role_subtype": "general", "level": "entry", "capture_date": capture, "capture_at": capture})
                requirements = [{"concept": "python", "explicit": True, "status": "required"}] if period_id == "2023-H1" else []
                extractions[doc_id] = {"accepted": True, "requirements": requirements}
        rows = _requirement_shares(observations, extractions, config)
        python_rows = [r for r in rows if r["concept"] == "python"]
        h1 = next(r for r in python_rows if r["period"] == "2023-H1")
        h2 = next(r for r in python_rows if r["period"] == "2023-H2")
        h1_to_h2_gap = next(r for r in python_rows if r["period"] == "2024-H1")
        self.assertEqual(h1["eligible_vacancies"], 5)
        self.assertEqual(h1["share_percent"], 100.0)
        self.assertEqual(h2["share_percent"], 0.0)
        self.assertEqual(h2["difference_percentage_points"], -100.0)
        self.assertIsNone(h1_to_h2_gap["share_percent"])
        self.assertIsNone(h1_to_h2_gap["difference_percentage_points"])

    def test_same_vacancy_diff_detects_status_and_experience_changes(self):
        before = [{"category": "skill", "concept": "python", "phrase": "3+ years of Python experience required", "evidence": "3+ years of Python experience", "status": "required", "experience_years": 3, "explicit": True}]
        after = [{"category": "skill", "concept": "python", "phrase": "5+ years of Python experience preferred", "evidence": "5+ years of Python experience", "status": "preferred", "experience_years": 5, "explicit": True}]
        changes = _requirement_changes(before, after)
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["no_longer_stated"], [])
        self.assertEqual(changes["modified"][0]["fields"]["status"], {"before": "required", "after": "preferred"})
        self.assertEqual(changes["modified"][0]["fields"]["experience_years"], {"before": 3, "after": 5})


if __name__ == "__main__":
    unittest.main()
