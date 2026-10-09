"""Bounded public HTTPS transport used only by company verification/collection."""

from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urldefrag, urljoin, urlsplit

from .store import utc_now


class BudgetReached(RuntimeError):
    pass


def public_address(host: str, timeout=5) -> str:
    """Bound DNS waiting and pin a validated address through the TLS connection."""
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        rows = result.get(timeout=max(0.01, timeout))
    except queue.Empty as exc:
        raise ValueError("Public source DNS lookup timed out.") from exc
    if isinstance(rows, OSError):
        raise rows
    addresses = [ipaddress.ip_address(r[4][0]) for r in rows]
    if not addresses or any(not a.is_global for a in addresses):
        raise ValueError("Local/private network sources are not supported.")
    return str(addresses[0])


def validate_public_url(url: str, resolve=True) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port not in {None, 443}
        or not host
        or host.lower() == "localhost"
        or host.endswith(".local")
    ):
        raise ValueError("Use a public HTTPS URL without credentials or a custom port.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Use a public website domain.")
    else:
        if not address.is_global:
            raise ValueError("Local/private network sources are not supported.")
    if resolve:
        public_address(host)
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, address):
        super().__init__()
        self.address = address

    def https_open(self, request):
        address = self.address

        class Connection(http.client.HTTPSConnection):
            def connect(self):
                self.sock = socket.create_connection((address, self.port), self.timeout)
                # SNI and certificate checks remain bound to the original public hostname.
                self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

        return self.do_open(Connection, request, context=self._context)


class CollectionClient:
    def __init__(
        self, config, requests=100, byte_limit=50_000_000, seconds=120, reserve=None, finish=None
    ):
        self.config = config
        self.request_count, self.request_limit = 0, requests
        self.byte_limit, self.bytes_used = byte_limit, 0
        self.deadline = time.monotonic() + seconds
        self.reserve, self.finish = reserve, finish
        self.last, self.request_events = {}, []
        self.on_request = None
        self.cancelled = lambda: False
        self.redirect_limit = int(config.get("verification_redirect_hops", 5))
        self.last_response = {}
        self.browser_usage = {"navigations": 0, "actions": 0}
        self.on_browser_event = lambda: None

    def remaining(self):
        if self.cancelled():
            raise BudgetReached("cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetReached("runtime_limit")
        if self.request_limit is not None and self.request_count >= self.request_limit:
            raise BudgetReached("request_limit")
        if self.bytes_used >= self.byte_limit:
            raise BudgetReached("byte_limit")
        return remaining

    def read_body(self, response, allowance):
        chunks, length, read_error = [], 0, None
        while length < allowance:
            if time.monotonic() >= self.deadline or self.cancelled():
                read_error = "cancelled or runtime_limit"
                break
            try:
                chunk = response.read(min(65536, allowance - length))
            except (OSError, http.client.HTTPException) as exc:
                read_error = str(exc)
                break
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        headers = dict(response.headers.items())
        headers["X-Degent-Final-URL"] = response.geturl()
        status = response.status
        if read_error:
            status = 0
            headers["error"] = "incomplete response: " + read_error
        if length >= allowance:
            status = 0
            headers["error"] = "response_byte_limit; inventory incomplete"
        return status, headers, b"".join(chunks)

    def attempt(self, url, address, headers, data, allowance, remaining):
        payload = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": self.config.get("archives", {}).get(
                    "user_agent", "degent-research/0.1"
                ),
                **({"Content-Type": "application/json"} if payload else {}),
                **(headers or {}),
            },
        )
        # A direct pinned connection prevents proxies or DNS rebinding reaching local services.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect, PublicHTTPSHandler(address)
        )
        try:
            with opener.open(
                request,
                timeout=min(float(self.config.get("request_timeout_seconds", 30)), remaining),
            ) as response:
                return self.read_body(response, allowance)
        except urllib.error.HTTPError as exc:
            with exc:
                return self.read_body(exc, allowance)
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            return 0, {"error": str(exc)[:300]}, b""

    def _request_hop(self, url, headers=None, *, data=None):
        remaining = self.remaining()
        validate_public_url(url, resolve=False)
        host = urlsplit(url).hostname
        wait = max(
            0,
            float(self.config.get("host_delay_seconds", 2))
            - (time.monotonic() - self.last.get(host, 0)),
        )
        if time.monotonic() + wait >= self.deadline:
            raise BudgetReached("runtime_limit")
        if wait:
            time.sleep(wait)
        retries = int(self.config.get("retry_count", 2))
        for attempt in range(retries + 1):
            remaining = self.remaining()
            address = public_address(host, timeout=min(5, remaining))
            allowance = min(
                int(self.config.get("collection_response_bytes", 8_000_000)),
                self.byte_limit - self.bytes_used,
            )
            event = {
                "url": url,
                "at": utc_now(),
                "attempt": attempt + 1,
                "reserved_bytes": allowance,
            }
            self.request_count += 1
            if self.reserve:
                self.reserve(event)
            if self.on_request:
                self.on_request(event)
            status, response_headers, received = self.attempt(
                url, address, headers, data, allowance, remaining
            )
            self.last[host] = time.monotonic()
            self.bytes_used += len(received)
            event.update(http_status=status, bytes=len(received))
            self.request_events.append(event)
            if self.finish:
                self.finish(event)
            if status not in {429, 500, 502, 503, 504} or attempt == retries:
                return status, response_headers, received
            delay = max(float(self.config.get("host_delay_seconds", 2)), min(2**attempt, 8))
            if time.monotonic() + delay >= self.deadline:
                raise BudgetReached("runtime_limit")
            time.sleep(delay)
        raise RuntimeError("request_failed")

    def request(self, url, headers=None, *, data=None):
        original, chain, seen = url, [], set()
        try:
            while True:
                url = urldefrag(url)[0]
                validate_public_url(url, resolve=False)
                if url in seen:
                    raise ValueError("redirect_cycle")
                seen.add(url)
                status, response_headers, body = self._request_hop(url, headers, data=data)
                self.last_response = {
                    "original_url": original,
                    "final_url": url,
                    "redirects": chain,
                    "status": status,
                    "retrieved_at": utc_now(),
                }
                if status not in {301, 302, 303, 307, 308}:
                    response_headers["X-Degent-Original-URL"] = original
                    response_headers["X-Degent-Final-URL"] = url
                    response_headers["X-Degent-Redirects"] = json.dumps(chain)
                    return status, response_headers, body
                location = next(
                    (v for k, v in response_headers.items() if k.lower() == "location"), None
                )
                if not location:
                    raise ValueError("redirect_missing_location")
                target = urljoin(url, location)
                hop = {"url": url, "status": status, "location": location, "target": target}
                chain.append(hop)
                if len(chain) > self.redirect_limit:
                    raise ValueError("redirect_limit")
                validate_public_url(target, resolve=False)
                if data is not None and (
                    status not in {307, 308} or urlsplit(target).netloc != urlsplit(url).netloc
                ):
                    raise ValueError("unsafe_post_redirect")
                if urlsplit(target).netloc != urlsplit(url).netloc:
                    headers = {
                        k: v
                        for k, v in (headers or {}).items()
                        if k.lower() not in {"authorization", "cookie", "proxy-authorization"}
                    }
                url = target
        except (ValueError, RuntimeError, OSError) as exc:
            if chain:
                chain[-1]["rejected_reason"] = str(exc)
            self.last_response = {
                "original_url": original,
                "final_url": url,
                "redirects": chain,
                "error": str(exc),
                "retrieved_at": utc_now(),
            }
            raise
