"""Optional renderer: OS-denied browser networking, pinned Python fetch/fulfill."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .collection_http import BudgetReached, validate_public_url
from .generic_sources import origin, validate_config

BROWSER_LOCK = threading.Lock()
POLICY_VERSION = "macos-deny-network-fulfill-v1"


def confinement_available():
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        return False
    code = "import socket; s=socket.socket(); s.connect(('127.0.0.1',9))"
    result = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1) (allow default) (deny network*) (allow network* (local unix-socket) (remote unix-socket))",
            sys.executable,
            "-c",
            code,
        ],
        capture_output=True,
        timeout=5,
    )
    return result.returncode != 0 and b"Operation not permitted" in result.stderr


def policy_hash():
    import hashlib

    return hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).with_name("collection_http.py").read_bytes()
        + Path(__file__).parent.parent.joinpath("tests/test_browser_sources.py").read_bytes()
    ).hexdigest()


def render(url, client, config=None):
    root = Path(
        client.config.get("library_dir")
        or Path(client.config.get("data_dir", "data")) / "company-library"
    )
    receipt = root / "browser-policy-tested.json"
    if (
        not receipt.exists()
        or json.loads(receipt.read_text()).get("policy_sha256") != policy_hash()
    ):
        raise RuntimeError("browser_policy_not_tested: run companies browser-check")
    try:
        return _render(url, client, config)
    except (BudgetReached, ValueError, RuntimeError, OSError):
        raise
    except Exception as exc:
        raise RuntimeError("browser_render_failed:" + str(exc)[:800]) from exc


def _render(url, client, config=None):
    cfg = validate_config(config or {}, url)
    validate_public_url(url, resolve=False)
    if not confinement_available():
        raise RuntimeError("browser_confinement_unavailable: rendering disabled")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "browser_dependency_missing: install .[browser] and playwright install chromium"
        ) from exc
    timeout = max(0.01, client.remaining())
    if not BROWSER_LOCK.acquire(timeout=timeout):
        raise BudgetReached("browser_concurrency_wait_limit")
    browser = context = None
    watchdog_stop = threading.Event()
    watchdog = None
    trace, failures, cache = [], [], {}
    try:
        with (
            tempfile.TemporaryDirectory(prefix="degent-browser-") as temporary,
            sync_playwright() as pw,
        ):
            executable = Path(pw.chromium.executable_path)
            if not executable.exists():
                raise RuntimeError(
                    "browser_runtime_missing: run python -m playwright install chromium"
                )
            wrapper = Path(temporary) / "chromium"
            pidfile = Path(temporary) / "browser.pid"
            wrapper.write_text(
                "#!/bin/sh\necho $$ > "
                + shlex.quote(str(pidfile))
                + "\nexec /usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*) (allow network* (local unix-socket) (remote unix-socket))' "
                + shlex.quote(str(executable))
                + ' "$@"\n'
            )
            wrapper.chmod(0o700)

            def kill_tree(pid):
                children = subprocess.run(
                    ["/usr/bin/pgrep", "-P", str(pid)], capture_output=True, text=True
                )
                for child in children.stdout.split():
                    kill_tree(int(child))
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            def guard():
                while not watchdog_stop.wait(0.1):
                    if time.monotonic() >= client.deadline or client.cancelled():
                        if pidfile.exists():
                            kill_tree(int(pidfile.read_text().strip()))
                        return

            watchdog = threading.Thread(target=guard, daemon=True)
            watchdog.start()
            try:
                browser = pw.chromium.launch(
                    executable_path=str(wrapper),
                    timeout=min(30000, client.remaining() * 1000),
                    headless=True,
                    args=[
                        "--disable-quic",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--disable-background-networking",
                        "--disable-extensions",
                    ],
                )
                context = browser.new_context(service_workers="block", accept_downloads=False)
                context.set_default_timeout(min(15000, client.remaining() * 1000))
                context.route_web_socket("**/*", lambda route: route.close())
                navigation = {"redirect": None, "count": 0}

                def handle(route):
                    request = route.request
                    entry = {
                        "url": request.url,
                        "kind": request.resource_type,
                        "method": request.method,
                    }
                    trace.append(entry)
                    try:
                        validate_public_url(request.url, resolve=False)
                        if origin(request.url) not in cfg["allowed_origins"]:
                            raise ValueError("browser_origin_rejected")
                        if request.resource_type in {
                            "image",
                            "media",
                            "font",
                            "eventsource",
                            "websocket",
                            "manifest",
                            "other",
                        }:
                            raise ValueError("browser_unnecessary_resource_blocked")
                        if request.method not in {"GET", "HEAD"}:
                            raise ValueError("browser_method_rejected")
                        if request.url in cache:
                            status, headers, body = cache.pop(request.url)
                        else:
                            status, headers, body = client.request(
                                request.url,
                                headers={"Accept": request.headers.get("accept", "*/*")},
                            )
                        final = headers.get("X-Degent-Final-URL", request.url)
                        entry.update(status=status, final_url=final, bytes=len(body))
                        if origin(final) not in cfg["allowed_origins"]:
                            raise ValueError("browser_redirect_origin_rejected")
                        if status != 200:
                            raise ValueError("browser_http_" + str(status))
                        if request.is_navigation_request() and final != request.url:
                            cache[final] = (status, headers, body)
                            navigation["redirect"] = final
                            route.fulfill(
                                status=200,
                                content_type="text/html",
                                body="<html><body></body></html>",
                            )
                            return
                        if final != request.url:
                            # Synthetic subresource redirects would retain the wrong URL/base.
                            raise ValueError(
                                "browser_subresource_redirect_requires_explicit_final_url"
                            )
                        headers = {
                            k: v
                            for k, v in headers.items()
                            if k.lower()
                            not in {
                                "content-length",
                                "transfer-encoding",
                                "connection",
                                "content-encoding",
                                "set-cookie",
                            }
                            and not k.startswith("X-Degent-")
                        }
                        route.fulfill(status=200, headers=headers, body=body)
                    except (ValueError, RuntimeError, OSError) as exc:
                        entry["rejected_reason"] = str(exc)
                        if not str(exc).startswith("browser_unnecessary"):
                            failures.append(str(exc))
                        route.abort()

                context.route("**/*", handle)
                page = context.new_page()
                page.on("popup", lambda popup: popup.close())
                page.on("download", lambda download: download.cancel())
                page.on(
                    "requestfailed",
                    lambda request: (
                        failures.append("browser_failed_request:" + request.url)
                        if request.url.startswith("http:")
                        else None
                    ),
                )
                current = url
                while True:
                    if client.browser_usage["navigations"] >= 5:
                        raise BudgetReached("browser_navigation_limit")
                    navigation["count"] += 1
                    client.browser_usage["navigations"] += 1
                    client.on_browser_event()
                    navigation["redirect"] = None
                    try:
                        page.goto(
                            current,
                            wait_until="domcontentloaded",
                            timeout=min(30000, client.remaining() * 1000),
                        )
                    except Exception:
                        if not navigation["redirect"]:
                            raise
                    if not navigation["redirect"]:
                        break
                    current = navigation["redirect"]
                if cfg.get("job_links"):
                    try:
                        page.locator(cfg["job_links"]).first.wait_for(
                            state="attached", timeout=min(3000, client.remaining() * 1000)
                        )
                    except Exception:
                        pass
                actions, hashes = 0, set()
                while cfg.get("load_more") and page.locator(cfg["load_more"]).count():
                    client.remaining()
                    if client.browser_usage["actions"] >= 10:
                        trace.append({"outcome": "browser_action_limit", "bounded": True})
                        break
                    before = page.content()
                    if before in hashes:
                        raise ValueError("browser_load_more_cycle")
                    hashes.add(before)
                    button = page.locator(cfg["load_more"])
                    if button.count() != 1:
                        raise ValueError("browser_load_more_ambiguous")
                    if not button.is_enabled() or not button.is_visible():
                        break
                    client.browser_usage["actions"] += 1
                    client.on_browser_event()
                    button.click(timeout=min(10000, client.remaining() * 1000))
                    page.wait_for_timeout(min(500, client.remaining() * 1000))
                    actions += 1
                if failures:
                    raise RuntimeError(
                        "browser_network_incomplete: " + "; ".join(sorted(set(failures)))
                    )
                payload = page.content().encode()
                if len(payload) > 4_000_000:
                    raise BudgetReached("rendered_dom_byte_limit")
                trace.append(
                    {
                        "policy": POLICY_VERSION,
                        "final_url": page.url,
                        "actions": actions,
                        "navigations": navigation["count"],
                    }
                )
                return payload, trace
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
                    watchdog_stop.set()
                    if watchdog:
                        watchdog.join(timeout=1)
    finally:
        BROWSER_LOCK.release()
