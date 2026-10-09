from __future__ import annotations

import http.client
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from degent.archive import ArchiveClient
from degent.cli import discover
from degent.config import load_config
from degent.store import read_jsonl, write_json


class Response(BytesIO):
    status = 200
    headers = {}

    def geturl(self):
        return "https://archive.example/query"


class TruncatedBody(BytesIO):
    def read(self, *args):
        raise http.client.IncompleteRead(b"partial")


class TruncatedResponse(Response):
    def read(self, *args):
        raise http.client.IncompleteRead(b"partial")


class ArchiveTransportTests(unittest.TestCase):
    def test_truncated_http_error_preserves_status_and_records_failure(self):
        body = TruncatedBody()
        error = urllib.error.HTTPError(
            "https://archive.example/query", 404, "Not Found", {}, body
        )
        client = ArchiveClient("test", retries=2, delay=0)
        with patch("urllib.request.urlopen", side_effect=error) as request:
            status, headers, payload = client.request("https://archive.example/query")
        self.assertEqual(status, 404)
        self.assertEqual(payload, b"")
        self.assertIn("IncompleteRead", headers["error"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(client.request_events[0]["http_status"], 404)
        self.assertIn("IncompleteRead", client.request_events[0]["body_error"])
        self.assertTrue(body.closed)

    def test_truncated_success_is_retried_without_accepting_partial_bytes(self):
        client = ArchiveClient("test", retries=1, delay=0)
        with (
            patch("urllib.request.urlopen", side_effect=[TruncatedResponse(), Response(b"full")]),
            patch("time.sleep"),
        ):
            status, _, payload = client.request("https://archive.example/query")
        self.assertEqual((status, payload), (200, b"full"))
        self.assertEqual(client.request_count, 2)
        self.assertEqual(
            [e["outcome"] for e in client.request_events], ["network_error", "response"]
        )

    def test_truncated_success_respects_retry_and_request_caps(self):
        for request_limit, expected_requests, reason in (
            (None, 2, "IncompleteRead"),
            (1, 1, "discovery_request_cap_reached"),
        ):
            with self.subTest(request_limit=request_limit):
                client = ArchiveClient("test", retries=1, delay=0)
                client.request_limit = request_limit
                with (
                    patch("urllib.request.urlopen", side_effect=lambda *a, **k: TruncatedResponse()),
                    patch("time.sleep"),
                ):
                    status, headers, payload = client.request("https://archive.example/query")
                self.assertEqual((status, payload), (0, b""))
                self.assertIn(reason, headers["error"])
                self.assertEqual(client.request_count, expected_requests)
                self.assertEqual(len(client.request_events), expected_requests)

    def test_discovery_continues_after_truncated_error_and_keeps_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config()
            config.update(run_dir=temporary, retry_count=0, host_delay_seconds=0)
            config["employers"] = config["employers"][:1]
            directory = Path(temporary) / "test"
            directory.mkdir()
            write_json(directory / "commoncrawl-catalogue.json", [])
            query = {
                "source": "commoncrawl", "employer_id": config["employers"][0]["id"],
                "period_id": "2025-Q1", "path": "example.test/*", "collection": "CC-test",
                "start": "2025-01-01", "end": "2025-03-31",
            }
            error = urllib.error.HTTPError(
                "https://archive.example/query", 404, "Not Found", {}, TruncatedBody()
            )
            candidate = b'{"url":"https://example.test/jobs/1","timestamp":"20250201000000","status":"200"}\n'
            with (
                patch("degent.cli.discovery_queries", return_value=[query, {**query, "path": "other.test/*"}]),
                patch("urllib.request.urlopen", side_effect=[error, Response(candidate)]),
            ):
                result = discover(config, "test")
            self.assertEqual((result["status"], result["records_total"]), ("complete", 1))
            self.assertEqual(result["discovery_requests_used"], 2)
            outcomes = read_jsonl(directory / "query-results.jsonl")
            self.assertEqual([r["http_status"] for r in outcomes], [404, 200])
            self.assertIn("body_error", outcomes[0]["network_events"][0])


if __name__ == "__main__":
    unittest.main()
