"""Exercise the dashboard company/continuation API through its loopback HTTP boundary."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from degent.collection import collect
from degent.collection_http import CollectionClient
from degent.config import load_config
from degent.dashboard import make_server
from degent.store import write_json


class CompanyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config = load_config()
        config.update(data_dir=str(root / "data"), run_dir=str(root / "runs"))
        path = root / "config.json"
        write_json(path, config)
        self.server = make_server(0, root, path)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, payload=None):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers = {
                "Content-Type": "application/json",
                "Origin": self.base,
                "X-Degent-Token": self.server.app.token,
            }
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_company_search_add_detail_saved_scope_and_budget_continuation(self):
        with patch.object(
            CollectionClient, "request", side_effect=AssertionError("search/add cannot collect")
        ):
            company = self.request(
                "/api/companies",
                {
                    "name": "New HTTP Company",
                    "website": "https://http-company.test",
                    "aliases": ["NHC"],
                },
            )
            result = self.request("/api/companies?search=NHC&size=1&page=0")
            self.assertEqual(result["items"][0]["id"], company["id"])
            detail = self.request("/api/company?id=" + company["id"])
            self.assertEqual(detail["source_status"], "unsupported")
            self.request(
                "/api/runs",
                {
                    "run_id": "http-run",
                    "employers": [company["id"]],
                    "periods": [],
                    "sources": ["current"],
                },
            )
            state = self.request("/api/state?run=http-run")
            self.assertEqual(state["config"]["employers"][0]["id"], company["id"])
            self.assertIsNone(state["config"]["current_document_cap"])
            collect(self.server.app.config("http-run"), "http-run")
            continued = self.request(
                "/api/collection/continue",
                {"run_id": "http-run", "additions": {"requests": 10, "seconds": 20}},
            )
            self.assertEqual(continued["usage"]["requests"], 0)
            self.assertEqual(continued["budgets"]["requests"], 110)
            self.request(
                "/api/companies",
                {"name": "NHC", "career_url": "https://boards.greenhouse.io/http-company"},
            )
            saved = self.request("/api/state?run=http-run")
            self.assertEqual(saved["config"]["employers"][0]["current_sources"], [])
            self.assertEqual(saved["collection"]["status"], "paused")

    def test_separate_source_probe_and_explicit_scope_revision(self):
        company = self.request(
            "/api/companies",
            {"name": "Probe Company", "career_url": "https://boards.greenhouse.io/probe"},
        )
        calls = []

        def request(client, url, **kwargs):
            calls.append(url)
            return 200, {}, b'{"name":"Probe Company"}'

        with patch.object(CollectionClient, "request", request):
            checked = self.request("/api/company/verify", {"id": company["id"]})
        self.assertEqual(checked["source_status"], "verified")
        self.assertEqual(calls, ["https://boards-api.greenhouse.io/v1/boards/probe"])
        self.request(
            "/api/runs",
            {
                "run_id": "parent",
                "sources": ["current"],
                "periods": [],
                "employers": [company["id"]],
            },
        )
        revised = self.request(
            "/api/runs",
            {
                "run_id": "child",
                "parent_run_id": "parent",
                "sources": ["current"],
                "periods": [],
                "employers": [company["id"], "stripe"],
            },
        )
        self.assertEqual(revised["run_id"], "child")
        self.assertEqual(self.server.app.config("child")["scope_revision"]["added"], ["stripe"])


if __name__ == "__main__":
    unittest.main()


class VerificationBatchHTTPTests(CompanyHTTPTests):
    test_company_search_add_detail_saved_scope_and_budget_continuation = None
    test_separate_source_probe_and_explicit_scope_revision = None

    def test_async_start_status_stop_and_resume_loopback(self):
        from unittest.mock import Mock

        from degent.verification_batch import VerificationBatch

        company = self.request(
            "/api/companies", {"name": "Async Example", "website": "https://async-example.test"}
        )
        release = threading.Event()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = lambda: release.wait(5) or 0
        try:
            with patch("degent.dashboard.subprocess.Popen", return_value=process) as launch:
                started = self.request("/api/company-verifications", {"ids": [company["id"]]})
                self.assertEqual(started["total"], 1)
                self.assertEqual(self.request("/api/company-verifications")["processed"], 0)
                self.assertEqual(self.request("/api/companies?search=Async")["total"], 1)
                stopped = self.request("/api/company-verifications/stop", {})
                self.assertEqual(stopped["id"], started["id"])
                directory = Path(started["directory"])
                self.assertTrue((directory / "stop-requested").exists())
                self.assertIn("--resume", launch.call_args.args[0])
                self.assertFalse((self.server.app.runs / "new-research").exists())
        finally:
            release.set()

    def test_verification_endpoint_rejects_invalid_ids_and_origin(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request("/api/company-verifications", {"ids": ["not-a-library-id"]})
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            with urllib.request.urlopen(
                urllib.request.Request(self.base + "/api/company-verifications", data=b"{}"),
                timeout=5,
            ):
                pass
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()
