"""Explicit optional browser integration. Synthetic pinned-transport fixtures only."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from degent.browser_sources import _render as render
from degent.browser_sources import confinement_available
from degent.collection_http import BudgetReached, CollectionClient

ENABLED = os.environ.get("DEGENT_BROWSER_TESTS") == "1"


@unittest.skipUnless(ENABLED, "Run explicitly with DEGENT_BROWSER_TESTS=1 after browser install")
class BrowserIntegrationTests(unittest.TestCase):
    def fixture(self, payload, response=None):
        client = CollectionClient({"host_delay_seconds": 0}, seconds=30)

        def request(url, headers=None, **kw):
            client.remaining()
            client.request_count += 1
            result = (
                response(url)
                if response
                else (200, {"Content-Type": "text/html", "X-Degent-Final-URL": url}, payload)
            )
            client.bytes_used += len(result[2])
            return result

        client.request = request
        return client

    def test_render_dynamic_html_and_account_requests_bytes(self):
        self.assertTrue(confinement_available())
        payload = b'<body><script src="/script.js"></script></body>'

        def response(url):
            body = (
                b'document.body.insertAdjacentHTML("beforeend", "<a href=/jobs/1>Job</a>")'
                if url.endswith(".js")
                else payload
            )
            return (
                200,
                {
                    "Content-Type": "text/javascript" if url.endswith(".js") else "text/html",
                    "X-Degent-Final-URL": url,
                },
                body,
            )

        c = self.fixture(payload, response)
        body, trace = render("https://fixture.test/careers", c)
        self.assertIn(b"/jobs/1", body)
        self.assertEqual(c.request_count, 2)
        self.assertGreater(c.bytes_used, len(payload))
        self.assertEqual(trace[-1]["policy"], "macos-deny-network-fulfill-v1")

    def test_navigation_redirect_uses_final_url_without_browser_redirect(self):
        def response(url):
            return (
                200,
                {"Content-Type": "text/html", "X-Degent-Final-URL": "https://fixture.test/final"},
                b'<body><a href="/jobs/1">Job</a></body>',
            )

        c = self.fixture(b"", response)
        body, trace = render("https://fixture.test/careers", c)
        self.assertEqual(trace[-1]["final_url"], "https://fixture.test/final")
        self.assertEqual(c.request_count, 1)

    def test_private_fetch_frames_and_custom_ports_are_rejected(self):
        for target in [
            "https://127.0.0.1/x",
            "https://[::1]/x",
            "https://169.254.169.254/x",
            "https://fixture.test:444/x",
            "http://fixture.test/x",
        ]:
            with self.subTest(target=target):
                payload = (
                    '<body><iframe src="'
                    + target
                    + '"></iframe><script>fetch('
                    + repr(target)
                    + ").catch(()=>{});</script></body>"
                ).encode()
                c = self.fixture(payload)
                with self.assertRaisesRegex(RuntimeError, "browser_network_incomplete"):
                    render("https://fixture.test/careers", c)
                self.assertEqual(c.request_count, 1)

    def test_os_boundary_blocks_unintercepted_ipv4_ipv6(self):
        from degent.browser_sources import POLICY_VERSION

        profile = "(version 1) (allow default) (deny network*) (allow network* (local unix-socket) (remote unix-socket))"
        for family, target in [("AF_INET", "('127.0.0.1',9)"), ("AF_INET6", "('::1',9)")]:
            result = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    profile,
                    sys.executable,
                    "-c",
                    f"import socket; s=socket.socket(socket.{family}); s.connect({target})",
                ],
                capture_output=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Operation not permitted", result.stderr)

    def test_load_more_loop_is_explicit_and_cleanup_after_error(self):
        payload = b'<body><a href="/jobs/1">Job</a><button id="more">More</button></body>'
        c = self.fixture(payload)
        with self.assertRaisesRegex(ValueError, "load_more_cycle"):
            render("https://fixture.test/careers", c, {"load_more": "#more"})
        body, _ = render(
            "https://fixture.test/careers", self.fixture(b'<body><a href="/jobs/1">Job</a></body>')
        )
        self.assertIn(b"/jobs/1", body)

    def test_cancellation_and_missing_confinement_are_explicit(self):
        c = self.fixture(b"<body/>")
        c.cancelled = lambda: True
        with self.assertRaises(BudgetReached):
            render("https://fixture.test/careers", c)
        with patch("degent.browser_sources.confinement_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "confinement_unavailable"):
                render("https://fixture.test/careers", self.fixture(b""))

        with patch.dict(sys.modules, {"playwright.sync_api": None}):
            with self.assertRaisesRegex(RuntimeError, "browser_dependency_missing"):
                render("https://fixture.test/careers", self.fixture(b""))
        with patch("playwright.sync_api.sync_playwright") as mock_playwright:
            mock_playwright.return_value.__enter__.return_value.chromium.executable_path = (
                "/missing-degent-test-chromium"
            )
            with self.assertRaisesRegex(RuntimeError, "browser_runtime_missing"):
                render("https://fixture.test/careers", self.fixture(b""))

    def test_rebinding_is_rejected_through_pinned_transport(self):
        client = CollectionClient({"host_delay_seconds": 0, "retry_count": 0}, seconds=30)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with (
            patch("degent.collection_http.socket.getaddrinfo", return_value=addresses),
            patch.object(client, "attempt") as attempt,
        ):
            with self.assertRaises(Exception):
                render("https://fixture.test/careers", client)
            attempt.assert_not_called()

    def test_watchdog_cancels_hung_script_and_reaps_descendants(self):
        from degent import browser_sources

        cancelled = threading.Event()
        killed = []
        real_kill = os.kill
        timer = None

        def response(url):
            nonlocal timer
            timer = threading.Timer(0.6, cancelled.set)
            timer.start()
            return (
                200,
                {"Content-Type": "text/html", "X-Degent-Final-URL": url},
                b"<script>while(true){}</script>",
            )

        def record_kill(pid, sig):
            if sig == 9:
                killed.append(pid)
            return real_kill(pid, sig)

        client = self.fixture(b"", response)
        client.cancelled = cancelled.is_set
        started = time.monotonic()
        try:
            with patch.object(browser_sources.os, "kill", side_effect=record_kill):
                with self.assertRaises(Exception):
                    render("https://fixture.test/careers", client)
        finally:
            if timer:
                timer.cancel()
        self.assertLess(time.monotonic() - started, 15)
        self.assertGreater(len(set(killed)), 1)
        for pid in set(killed):
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
            )
            self.assertTrue(
                not result.stdout.strip() or result.stdout.strip().startswith("Z"), result.stdout
            )
