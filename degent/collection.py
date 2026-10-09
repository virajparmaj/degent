"""Inventory-driven manual capture sessions integrated with Degent's run files."""

from __future__ import annotations

import copy
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from .collection_http import BudgetReached, CollectionClient
from .companies import PROVIDERS, source_identity
from .snapshots import SnapshotStore
from .store import append_jsonl, digest, read_json, read_jsonl, utc_now, write_json

DEFAULT_BUDGETS = {"requests": 100, "bytes": 50_000_000, "seconds": 120, "storage": 100_000_000}


def budgets(config):
    return {
        "requests": (int(config.get("current_request_cap", 100))
                     if config.get("current_request_cap", 100) is not None else None),
        "bytes": int(config.get("collection_byte_cap", 50_000_000)),
        "seconds": int(config.get("collection_runtime_seconds", 120)),
        "storage": int(config.get("collection_storage_cap", 100_000_000)),
    }


def active_session(manifest):
    return (
        manifest.get("collection_sessions", [])[-1] if manifest.get("collection_sessions") else None
    )


def new_session(config, manifest):
    old = active_session(manifest)
    if old and old.get("status") not in {"complete", "bounded", "needs_attention"}:
        raise ValueError("Resume or extend the unfinished collection session before refreshing.")
    session = {
        "id": uuid.uuid4().hex,
        "started_at": utc_now(),
        "status": "prepared",
        "budgets": budgets(config),
        "usage": {"requests": 0, "bytes": 0, "seconds": 0, "storage": 0},
        "extensions": [],
        "sources": {},
        "turn": 0,
        "document_limit": config.get("current_document_cap"),
        "scope_sha256": digest(json.dumps(config["employers"], sort_keys=True)),
    }
    for employer in config["employers"]:
        for source in employer.get("current_sources", []) or [{"provider": "unknown"}]:
            key = employer["id"] + ":" + source_identity(source)
            session["sources"][key] = {
                "company_id": employer["id"],
                "source": copy.deepcopy(source),
                "status": "prepared",
                "next_index": 0,
                "offset": 0,
                "inventory_complete": False,
                "inventory_bounded": False,
                "discovered": 0,
                "retrieved": 0,
                "eligible": 0,
                "seen_paths": [],
                "counts": {},
                "restrictions": {
                    k: source[k]
                    for k in ("keywords", "max_pages", "searchText", "appliedFacets")
                    if k in source
                },
            }
    from .sources import current_period

    period = current_period(config)
    session["observation_period"] = period
    if not any(p["id"] == period["id"] for p in manifest.setdefault("observation_periods", [])):
        manifest["observation_periods"].append(period)
    manifest.setdefault("collection_sessions", []).append(session)
    return session


EXTRACTION_BUDGETS = {
    "model_calls": "model_call_cap",
    "extraction_documents": "extract_document_cap",
    "semantic_review_calls": "semantic_review_call_cap",
}


def extend(config, run_id, additions):
    from .cli import manifest_for, run_directory, save_manifest

    directory = run_directory(config, run_id)
    manifest = manifest_for(directory, run_id)
    session = active_session(manifest)
    collection_keys = {"requests", "bytes", "seconds", "storage", "documents"}
    if not additions or set(additions) - collection_keys - EXTRACTION_BUDGETS.keys():
        raise ValueError("Choose a supported collection or extraction budget extension.")
    if not session or (
        session.get("status") in {"complete", "bounded"} and set(additions) & collection_keys
    ):
        raise ValueError(
            "No paused collection session to extend. Completed sessions can extend extraction budgets."
        )
    for key, value in additions.items():
        if type(value) is not int or not 1 <= value <= 1_000_000_000:
            raise ValueError("Budget extensions must be positive integers.")
        if key == "documents" and session.get("document_limit") is None:
            raise ValueError("This session has no document limit.")
        if key == "requests" and session["budgets"]["requests"] is None:
            raise ValueError("This session has no request-count limit; extend another resource.")
    for key, value in additions.items():
        if key in EXTRACTION_BUDGETS:
            field = EXTRACTION_BUDGETS[key]
            overrides = manifest.setdefault("extraction_budgets", {})
            overrides[field] = int(overrides.get(field, config[field])) + value
        elif key == "documents":
            session["document_limit"] += value
        else:
            session["budgets"][key] += value
    session["extensions"].append({"at": utc_now(), "additions": additions})
    if set(additions) & collection_keys:
        session.update(status="paused", pause_reason="extension_recorded")
    save_manifest(directory, manifest)
    return session


class Collector:
    def __init__(self, config, run_id, directory, manifest, session):
        from .cli import save_manifest

        self.config, self.run_id, self.directory, self.manifest, self.session = (
            config,
            run_id,
            directory,
            manifest,
            session,
        )
        self.checkpoint = lambda: save_manifest(directory, manifest)
        self.store = SnapshotStore(config, self.reserve_storage)
        self.root = directory / "collections" / session["id"]
        self.root.mkdir(parents=True, exist_ok=True)
        self.observations = read_jsonl(directory / "observations.jsonl")
        self.ids = {o["observation_id"] for o in self.observations}
        self.client = CollectionClient(
            config,
            **{
                "requests": (max(0, session["budgets"]["requests"] - session["usage"]["requests"])
                             if session["budgets"]["requests"] is not None else None),
                "byte_limit": max(0, session["budgets"]["bytes"] - session["usage"]["bytes"]),
                "seconds": max(0, session["budgets"]["seconds"] - session["usage"]["seconds"]),
            },
            reserve=self.reserve,
            finish=self.finish,
        )
        self.client.browser_usage = session.setdefault(
            "browser_usage", {"navigations": 0, "actions": 0}
        )
        self.client.on_browser_event = self.checkpoint

    def reserve_storage(self, count):
        if self.session["usage"]["storage"] + count > self.session["budgets"]["storage"]:
            raise BudgetReached("storage_limit")
        self.session["usage"]["storage"] += count
        self.checkpoint()

    def reserve(self, event):
        usage = self.session["usage"]
        usage["requests"] += 1
        usage["bytes"] += event["reserved_bytes"]
        self.manifest["current_requests_used"] = self.manifest.get("current_requests_used", 0) + 1
        self.session["active_request"] = event.copy()
        self.checkpoint()

    def finish(self, event):
        self.session["usage"]["bytes"] -= event["reserved_bytes"] - event["bytes"]
        self.session.pop("active_request", None)
        self.checkpoint()

    def response(self, key, url, *, data=None, headers=None):
        path = self.root / (digest(key) + ".json")
        if path.exists():
            cached = read_json(path)
            raw = Path(cached["raw_file"])
            if digest(raw.read_bytes()) != cached["raw_sha256"]:
                raise ValueError("Saved response hash mismatch.")
            return cached, raw.read_bytes()
        status, headers, body = self.client.request(
            url,
            **({"data": data} if data is not None else {}),
            **({"headers": headers} if headers else {}),
        )
        raw = self.store.blob(body)
        metadata = {
            "status": status,
            "headers": headers,
            "raw_file": raw,
            "raw_sha256": digest(body),
            "observed_at": utc_now(),
            "url": url,
        }
        # Persist exact error response too, but only successful requests become resume caches.
        if status != 200:
            append_jsonl(self.root / "failed-responses.jsonl", [metadata])
            raise ValueError(headers.get("error") or f"provider HTTP {status}")
        write_json(path, metadata)
        return metadata, body

    def save_row(self, employer, source, row, metadata, info):
        posting = str(row.get("posting_id") or row.get("source_url") or "")
        if not posting or not row.get("source_url") or not row.get("title"):
            raise ValueError("Posting lacks source identity, URL or title.")
        observation_key = self.session["id"] + employer["id"] + source_identity(source) + posting
        if self.config.get("study_version") == 1:
            observation_key += row["source_url"]
        event_id = digest(observation_key)
        item = self.store.event(event_id)
        if not item:
            row = {
                **row,
                "_session_id": self.session["id"],
                "raw_source_file": metadata["raw_file"],
                "raw_source_sha256": metadata["raw_sha256"],
            }
            item = self.store.capture(
                self.config, self.run_id, employer, source, row, metadata["observed_at"], event_id
            )
        date = item["capture_date"]
        period = {"id": "CURRENT-" + date, "start": date, "end": date, "kind": "partial"}
        if not any(
            p["id"] == period["id"] for p in self.manifest.setdefault("observation_periods", [])
        ):
            self.manifest["observation_periods"].append(period)
        if event_id not in self.ids:
            self.reserve_storage(len(json.dumps(item).encode()) + 1)
            append_jsonl(self.directory / "observations.jsonl", [item])
            self.ids.add(event_id)
            self.observations.append(item)
        # Counts are derived from events, never incremented in a vulnerable second write.
        return item

    def feed_step(self, employer, source, info, key):
        from .sources import feed_jobs, feed_url

        metadata, payload = self.response(key + ":feed", feed_url(source))
        rows = feed_jobs(payload, source)
        upstream = json.loads(payload)
        total = (upstream.get("meta") or {}).get("total", len(upstream["jobs"]))
        if (
            type(total) is not int
            or len(rows) != len(upstream["jobs"])
            or total != len(rows)
            or upstream.get("next")
            or upstream.get("nextPage")
        ):
            raise ValueError("Feed inventory is malformed or truncated; completeness is unproven.")
        if any(
            not r.get("posting_id") or not r.get("source_url") or not r.get("title") for r in rows
        ):
            raise ValueError("Feed row missing posting identity/title/URL; inventory incomplete.")
        if len({r["posting_id"] for r in rows}) != len(rows):
            raise ValueError("Feed contains duplicate provider identities.")
        info.update(discovered=len(rows), inventory_complete=True)
        index = info["next_index"]
        if index < len(rows):
            self.save_row(employer, source, rows[index], metadata, info)
            info["next_index"] = index + 1
        if info["next_index"] >= len(rows):
            info["status"] = "complete"

    def workday_step(self, employer, source, info, key):
        from .workday import PAGE_SIZE, api_base, detail, page

        pending = info.get("pending", [])
        index = info["next_index"]
        if index < len(pending):
            path = pending[index]["externalPath"]
            info["active_posting"] = path
            metadata, body = self.response(key + ":detail:" + path, api_base(source) + path)
            info.setdefault("detail_captures", {})[path] = {**metadata, "parsed": False}
            self.checkpoint()
            row = detail(body, source, path)
            self.save_row(employer, source, row, metadata, info)
            info["detail_captures"][path]["parsed"] = True
            info.setdefault("failed_postings", {}).pop(path, None)
            info.pop("active_posting", None)
            info["next_index"] += 1
        elif not info["inventory_complete"]:
            metadata, body = self.response(
                key + ":page:" + str(info["offset"]),
                api_base(source) + "/jobs",
                data={
                    "limit": PAGE_SIZE,
                    "offset": info["offset"],
                    "searchText": "",
                    "appliedFacets": {},
                },
            )
            rows, total, done = page(
                body, info["offset"], info["seen_paths"], info.get("advertised_total")
            )
            info.update(pending=rows, next_index=0, advertised_total=total, inventory_complete=done)
            info["offset"] += len(rows)
            info["seen_paths"] += [r["externalPath"] for r in rows]
            info["discovered"] = len(info["seen_paths"])
        if info["inventory_complete"] and info["next_index"] >= len(info.get("pending", [])):
            info["status"] = "complete"

    def lever_step(self, employer, source, info, key):
        from .lever import PAGE_SIZE, api_base, page

        pending = info.get("pending", [])
        if info["next_index"] < len(pending):
            metadata, body = self.response(
                key + ":lever:" + str(info.get("page_offset", 0)),
                api_base(source)
                + "?limit="
                + str(PAGE_SIZE)
                + "&skip="
                + str(info.get("page_offset", 0)),
                headers={"Accept": "application/json"},
            )
            self.save_row(employer, source, pending[info["next_index"]], metadata, info)
            info["next_index"] += 1
        elif not info["inventory_complete"]:
            offset = info["offset"]
            metadata, body = self.response(
                key + ":lever:" + str(offset),
                api_base(source) + "?limit=" + str(PAGE_SIZE) + "&skip=" + str(offset),
                headers={"Accept": "application/json"},
            )
            rows, done = page(body, source, info["seen_paths"])
            info.update(pending=rows, next_index=0, page_offset=offset, inventory_complete=done)
            info["seen_paths"] += [r["posting_id"] for r in rows]
            info["offset"] += len(rows)
            info["discovered"] = len(info["seen_paths"])
        if info["inventory_complete"] and info["next_index"] >= len(info.get("pending", [])):
            info["status"] = "complete"

    def generic_response(self, key, url, cfg):
        if not cfg.get("render"):
            return self.response(key, url)
        from .browser_sources import render

        path = self.root / (digest(key + ":rendered") + ".json")
        if path.exists():
            metadata = read_json(path)
            body = Path(metadata["raw_file"]).read_bytes()
            if digest(body) != metadata["raw_sha256"]:
                raise ValueError("Rendered response hash mismatch")
            return metadata, body
        body, trace = render(url, self.client, cfg)
        metadata = {
            "status": 200,
            "headers": {"X-Degent-Final-URL": trace[-1]["final_url"]},
            "raw_file": self.store.blob(body),
            "raw_sha256": digest(body),
            "observed_at": utc_now(),
            "url": url,
            "browser_trace": trace,
        }
        write_json(path, metadata)
        return metadata, body

    def generic_step(self, employer, source, info, key):
        from .generic_sources import (
            allowed,
            extract,
            job_url,
            listing,
            sitemap_urls,
            validate_config,
        )

        cfg = validate_config(source.get("dom_config", {}), source["url"])
        pending = info.get("pending", [])
        if info["next_index"] < len(pending):
            url = pending[info["next_index"]]
            info["active_posting"] = url
            metadata, body = self.generic_response(key + ":detail:" + url, url, cfg)
            info.setdefault("detail_captures", {})[url] = {**metadata, "parsed": False}
            row = extract(body, metadata["headers"].get("X-Degent-Final-URL", url), employer, cfg)
            self.save_row(employer, source, row, metadata, info)
            info["detail_captures"][url]["parsed"] = True
            info.pop("active_posting", None)
            info["next_index"] += 1
        elif not info.get("inventory_finished"):
            if source["provider"] == "sitemap":
                queue = info.setdefault("sitemap_queue", [[source["url"], 0]])
                if not queue:
                    info.update(inventory_finished=True, inventory_bounded=True, status="bounded")
                    return
                url, depth = queue[0]
                if depth > 2:
                    info.update(
                        inventory_finished=True,
                        inventory_bounded=True,
                        status="bounded",
                        reason="sitemap_depth_bound",
                    )
                    return
            else:
                url = info.get("next_page") or source["url"]
            visited = info.setdefault("visited_pages", [])
            if url in visited:
                raise ValueError("generic_pagination_cycle")
            if len(visited) >= (6 if source["provider"] == "sitemap" else cfg["max_pages"]):
                info.update(inventory_finished=True, inventory_bounded=True)
                return
            metadata, body = self.generic_response(key + ":listing:" + url, url, cfg)
            if source["provider"] == "sitemap":
                links, index, capped = sitemap_urls(
                    body, max(0, 1000 - info.get("sitemap_urls_examined", 0))
                )
                info["sitemap_urls_examined"] = info.get("sitemap_urls_examined", 0) + len(links)
                info["sitemap_queue"].pop(0)
                if index:
                    for child in links:
                        if (
                            allowed(child, cfg["allowed_origins"])
                            and child not in visited
                            and all(q[0] != child for q in info["sitemap_queue"])
                        ):
                            info["sitemap_queue"].append([child, depth + 1])
                    visited.append(url)
                    info.update(pending=[], next_index=0, inventory_bounded=True)
                    if capped or info["sitemap_urls_examined"] >= 1000:
                        info.update(inventory_finished=True, status="bounded")
                    return
                rows = [u for u in links if allowed(u, cfg["allowed_origins"]) and job_url(u)]
                next_url, empty = None, not rows
                info["inventory_bounded"] = True
            else:
                rows, next_url, empty = listing(body, url, cfg)
                from .generic_sources import advertised_total

                total = advertised_total(body, cfg)
                if total is not None:
                    if (
                        info.get("advertised_total") is not None
                        and info["advertised_total"] != total
                    ):
                        raise ValueError("dom_inventory_total_changed; refresh for reconciliation")
                    info["advertised_total"] = total
            if not rows and not empty:
                raise ValueError("dom_no_postings_or_explicit_empty_state")
            if set(rows) & set(info["seen_paths"]):
                raise ValueError("generic_duplicate_pagination_postings")
            visited.append(url)
            info.update(
                pending=rows,
                next_index=0,
                next_page=next_url,
                inventory_finished=(
                    (not info["sitemap_queue"] or capped)
                    if source["provider"] == "sitemap"
                    else not next_url
                ),
                inventory_bounded=info["inventory_bounded"]
                or not cfg["exhaustive"]
                or info.get("advertised_total") is None
                or cfg["render"]
                or source["provider"] == "sitemap",
            )
            info["seen_paths"] += rows
            info["discovered"] = len(info["seen_paths"])
        if info.get("inventory_finished") and info["next_index"] >= len(info.get("pending", [])):
            if not info["inventory_bounded"] and len(info["seen_paths"]) != info.get(
                "advertised_total"
            ):
                raise ValueError("dom_inventory_total_mismatch; completeness unproven")
            info.update(
                inventory_complete=not info["inventory_bounded"],
                status="bounded" if info["inventory_bounded"] else "complete",
            )

    def capital_candidate(self, employer, source, info, key):
        from .capital_one import job_posting, preselect

        candidate = info["pending"][info["next_index"]]
        selected, reason = preselect(candidate, self.config)
        if candidate["site_job_id"] not in info["seen_paths"]:
            decision = {
                **candidate,
                "selected": selected,
                "selection_reason": reason,
                "at": utc_now(),
            }
            if selected:
                info["active_posting"] = candidate["site_job_id"]
                metadata, body = self.response(
                    key + ":detail:" + candidate["site_job_id"], candidate["source_url"]
                )
                info.setdefault("detail_captures", {})[candidate["site_job_id"]] = {
                    **metadata,
                    "parsed": False,
                }
                self.checkpoint()
                row = job_posting(body, candidate["source_url"])
                item = self.save_row(employer, source, row, metadata, info)
                info["detail_captures"][candidate["site_job_id"]]["parsed"] = True
                info.setdefault("failed_postings", {}).pop(candidate["site_job_id"], None)
                info.pop("active_posting", None)
                decision.update(
                    status="eligible" if item["body_complete"] else "rejected_after_retrieval",
                    document_id=item["document_id"],
                    reason=item["body_status"],
                )
            else:
                decision["status"] = "rejected_before_retrieval"
            decision["session_id"] = self.session["id"]
            old = read_jsonl(self.directory / "current-selection.jsonl")
            previous = next((r for r in reversed(old) if r.get("session_id") == self.session["id"]
                             and r["site_job_id"] == candidate["site_job_id"]), None)
            if previous is None or any(previous.get(k) != decision.get(k) for k in ("status", "document_id", "selected")):
                append_jsonl(self.directory / "current-selection.jsonl", [decision])
            info["seen_paths"].append(candidate["site_job_id"])
        info["next_index"] += 1
        return

    def capital_step(self, employer, source, info, key):
        from .capital_one import BASE, job_posting, listing_jobs, preselect

        census = source.get("inventory_mode") == "all"
        keywords = [""] if census else source.get("keywords", ["credit", "underwriting", "quantitative"])
        if (
            not isinstance(keywords, list)
            or not keywords
            or not all(isinstance(k, str) and (census or 1 <= len(k) <= 100) for k in keywords)
        ):
            raise ValueError("Invalid Capital One role search configuration.")
        max_pages = None if census else int(source.get("max_pages", 4))
        if max_pages is not None and not 1 <= max_pages <= 20:
            raise ValueError("Invalid Capital One page bound.")
        pending = info.get("pending", [])
        index = info["next_index"]
        if index < len(pending) and (not census or info.get("inventory_enumeration_complete")):
            return self.capital_candidate(employer, source, info, key)
        keyword_index = info.get("keyword_index", 0)
        if keyword_index >= len(keywords):
            if census:
                advertised = info.get("advertised_total", 0)
                unique = len(set(info.get("inventory_ids", [])))
                if unique != advertised or info.get("inventory_drift"):
                    info.update(status="unavailable", inventory_complete=False,
                                reason="Inventory changed or unique listing count does not reconcile.")
                    return
            info.update(
                inventory_complete=not info["inventory_bounded"],
                status="bounded" if info["inventory_bounded"] else "complete",
            )
            return
        number = info.get("page", 1)
        keyword = keywords[keyword_index]
        metadata, body = self.response(
            key + ":listing:" + keyword + ":" + str(number),
            BASE + "/search-jobs?" + urlencode({"k": keyword, "p": number}),
        )
        rows, pages, total = listing_jobs(body, keyword, number)
        if census:
            ids = [r["site_job_id"] for r in rows]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate identities on inventory page.")
            signatures = info.setdefault("page_signatures", {})
            signature = digest(json.dumps(ids))
            if signature in signatures.values() and str(number) not in signatures:
                raise ValueError("Inventory pagination repeated a page.")
            signatures[str(number)] = signature
            if "advertised_total" in info and info["advertised_total"] != total:
                info["inventory_drift"] = True
            info["advertised_total"] = total
            info["advertised_pages"] = pages
            info.setdefault("inventory_ids", []).extend(ids)
            info.setdefault("enumeration_started_at", metadata["observed_at"])
            info["enumeration_latest_at"] = metadata["observed_at"]
            existing_inventory = read_jsonl(self.directory / "inventory.jsonl")
            existing_keys = {(r["session_id"], r["page"], r["site_job_id"]) for r in existing_inventory}
            append_jsonl(self.directory / "inventory.jsonl", [
                {**r, "company_id": employer["id"], "source_id": key,
                 "session_id": self.session["id"], "page": number,
                 "observed_at": metadata["observed_at"]} for r in rows
                if (self.session["id"], number, r["site_job_id"]) not in existing_keys
            ])
        info.update(pending=rows, next_index=0)
        info["discovered"] += len(rows)
        info["inventory_bounded"] |= max_pages is not None and pages > max_pages
        info["selection_note"] = (
            "All-listing enumeration, scoped descriptions; a changing live inventory is not an atomic census."
            if census else "Role-keyword source searches; inventory covers configured searches, not all company jobs. Full descriptions pass research gates."
        )
        if number >= (pages if max_pages is None else min(pages, max_pages)):
            info.update(keyword_index=keyword_index + 1, page=1)
            if census:
                full = {r["site_job_id"]: r for r in read_jsonl(self.directory / "inventory.jsonl")
                        if r["session_id"] == self.session["id"] and r["source_id"] == key}
                info.update(inventory_enumeration_complete=True, pending=list(full.values()), next_index=0)
        else:
            info["page"] = number + 1

    def process_source(self, key, info):
        source = info["source"]
        employer = next(e for e in self.config["employers"] if e["id"] == info["company_id"])
        if source.get("provider") not in PROVIDERS:
            info.update(
                status="unsupported",
                reason="Supply a supported career-board URL in the company library, then create a scope continuation.",
            )
        elif source.get("status", "configured") not in {"verified", "configured"} or (
            source.get("status") == "verified" and not source.get("ownership_basis")
        ):
            info.update(
                status="unverified",
                reason="Verify the company's source before creating a new run with that source configuration.",
            )
        elif source["provider"] in {"lever", "dom", "sitemap"} and (
            source.get("status") != "verified"
            or source.get("ownership") != "verified"
            or source.get("collector_capability") != "supported"
        ):
            info.update(
                status="unverified",
                reason="Verify ownership and collector capability before collection.",
            )
        else:
            info["status"] = "processing"
            try:
                if source["provider"] in {"greenhouse", "ashby"}:
                    self.feed_step(employer, source, info, key)
                elif source["provider"] == "workday":
                    self.workday_step(employer, source, info, key)
                elif source["provider"] == "capital_one":
                    self.capital_step(employer, source, info, key)
                elif source["provider"] == "lever":
                    self.lever_step(employer, source, info, key)
                elif source["provider"] in {"dom", "sitemap"}:
                    self.generic_step(employer, source, info, key)
                else:
                    raise ValueError("No explicit collector dispatch")
                if info["status"] != "unavailable":
                    info.pop("reason", None)
            except BudgetReached:
                raise
            except (ValueError, RuntimeError, OSError, TypeError, KeyError, AttributeError) as exc:
                if self.config.get("study_version") == 1 and info.get("active_posting") and source["provider"] == "capital_one":
                    if time.monotonic() >= self.client.deadline:
                        raise BudgetReached("runtime_limit") from exc
                    if self.client.bytes_used >= self.client.byte_limit:
                        raise BudgetReached("byte_limit") from exc
                    identity = info.pop("active_posting")
                    info.setdefault("failed_postings", {})[identity] = str(exc)
                    candidate = info["pending"][info["next_index"]]
                    append_jsonl(self.directory / "current-selection.jsonl", [{**candidate, "selected": True,
                        "status": "unavailable_description", "reason": str(exc), "session_id": self.session["id"], "at": utc_now()}])
                    if identity not in info["seen_paths"]:
                        info["seen_paths"].append(identity)
                    info["next_index"] += 1
                    info["status"] = "processing"
                    return True
                info.update(status="unavailable", reason=str(exc), last_failure_at=utc_now())
                if info.get("active_posting"):
                    info.setdefault("failed_postings", {})[info["active_posting"]] = str(exc)
                return False
        return True

    def run_steps(self, infos):
        halted, steps = set(), 0
        while steps < max(1, min(1000, int(self.config.get("collection_chunk_size", 25)))):
            if time.monotonic() >= self.client.deadline:
                raise BudgetReached("runtime_limit")
            candidates = [
                (k, i)
                for k, i in infos
                if i["status"] not in {"complete", "bounded", "unsupported", "unverified"}
                and k not in halted
            ]
            if not candidates:
                break
            limit = self.session.get("document_limit")
            captured = [
                r for r in self.observations if r.get("collection_session_id") == self.session["id"]
            ]
            if limit is not None and len(captured) >= limit:
                raise BudgetReached("document_limit")
            position = self.session["turn"] % len(infos)
            key, info = infos[position]
            self.session["turn"] += 1
            if (key, info) not in candidates:
                continue
            if not self.process_source(key, info):
                halted.add(key)
            steps += 1
            self.checkpoint()
        self.set_outcome(infos)

    def set_outcome(self, infos):
        states = {i["status"] for _, i in infos}
        if states <= {"complete"}:
            status = "needs_attention" if self.config.get("study_version") == 1 and any(i.get("failed_postings") for _, i in infos) else "complete"
        elif states <= {"complete", "bounded"}:
            status = "bounded"
        elif states & {"unsupported", "unverified", "unavailable"} and "processing" not in states:
            status = "needs_attention"
        else:
            status = "paused"
        self.session.update(
            status=status, pause_reason="chunk_limit" if status == "paused" else None
        )

    def run(self):
        session = self.session
        infos = list(session["sources"].items())
        start = time.monotonic()
        cpu_start = time.process_time()
        available_seconds = max(0, session["budgets"]["seconds"] - session["usage"]["seconds"])
        # Charge the remaining allowance before work; an interrupted process cannot reset time.
        prior_seconds = session["usage"]["seconds"]
        session["usage"]["seconds"] += available_seconds
        session.update(status="collecting", pause_reason=None)
        self.checkpoint()
        try:
            if available_seconds <= 0:
                raise BudgetReached("runtime_limit")
            self.run_steps(infos)
            while (self.config.get("collection_auto_chunks")
                   and session.get("pause_reason") == "chunk_limit"):
                session.update(status="collecting", pause_reason=None)
                self.run_steps(infos)
        except BudgetReached as exc:
            session.update(status="paused", pause_reason=str(exc))
        except KeyboardInterrupt:
            session.update(status="paused", pause_reason="cancelled")
            if self.config.get("study_version") != 1:
                raise
        finally:
            if session.get("active_request"):
                session.setdefault("uncertain_requests", []).append({**session.pop("active_request"), "outcome": "interrupted; reserved bytes retained, actual bytes unknown"})
            session["usage"]["seconds"] = prior_seconds + min(
                available_seconds, time.monotonic() - start
            )
            if self.config.get("study_version") == 1:
                import resource
                import sys
                session.setdefault("resource_intervals", []).append({
                    "ended_at": utc_now(), "elapsed_seconds": time.monotonic()-start,
                    "cpu_seconds": time.process_time()-cpu_start,
                    "process_peak_ram_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                    "scope": "this collection invocation; peak RAM is whole Python process, not per-stage allocation",
                })
            self.summarize()
            self.checkpoint()
        return {
            "saved": len(self.observations) - self.before,
            "total_saved": self.manifest["current_documents_saved"],
            "requests_used": self.manifest.get("current_requests_used", 0),
            "sources": self.manifest["current_sources"],
            "status": session["status"],
            "pause_reason": session.get("pause_reason"),
            "session": session,
        }

    def summarize(self):
        rows = [
            r for r in self.observations if r.get("collection_session_id") == self.session["id"]
        ]
        for key, info in self.session["sources"].items():
            selected = [
                r
                for r in rows
                if r["employer_id"] == info["company_id"]
                and r.get("career_source_id") == source_identity(info["source"])
            ]
            info["retrieved"] = (
                len(info["detail_captures"]) if info.get("detail_captures") else len(selected)
            )
            info["unparsed_bodies"] = sum(
                not r["parsed"] for r in info.get("detail_captures", {}).values()
            )
            info["unavailable_bodies"] = len(
                set(info.get("failed_postings", {})) - set(info.get("detail_captures", {}))
            )
            info["eligible"] = sum(r.get("body_complete", False) for r in selected)
            info["counts"] = dict(Counter(r["content_change"] for r in selected))
        accepted = {
            r["document_id"]
            for r in read_jsonl(self.directory / "requirements.jsonl")
            if r.get("accepted")
        }
        self.session["counts"] = {
            "discovered": sum(i["discovered"] for i in self.session["sources"].values()),
            "retrieved": sum(i["retrieved"] for i in self.session["sources"].values()),
            "eligible": sum(r.get("body_complete", False) for r in rows),
            "distinct_vacancies": len({r["vacancy_id"] for r in rows}),
            "new_versions": sum(r.get("new_content_version", False) for r in rows),
            "unchanged": sum(r.get("content_change") == "unchanged" for r in rows),
            "changed": sum(r.get("content_change") == "changed" for r in rows),
            "rejected": sum(not r.get("body_complete") for r in rows)
            + sum(i["unparsed_bodies"] for i in self.session["sources"].values()),
            "unavailable_bodies": sum(
                i["unavailable_bodies"] for i in self.session["sources"].values()
            ),
            "unavailable_sources": sum(
                i["status"] == "unavailable" for i in self.session["sources"].values()
            ),
            "pending_extraction": len(
                {r["document_id"] for r in rows if r.get("body_complete")} - accepted
            ),
        }
        self.manifest["current_sources"] = {
            k: {f: v for f, v in i.items() if f not in {"pending", "seen_paths", "detail_captures"}}
            for k, i in self.session["sources"].items()
        }
        self.manifest["current_documents_saved"] = len(
            [r for r in self.observations if r.get("collection_session_id")]
        )


def collect(config, run_id, refresh=False):
    from .cli import manifest_for, run_directory

    directory = run_directory(config, run_id)
    manifest = manifest_for(directory, run_id)
    session = (
        new_session(config, manifest)
        if refresh or not active_session(manifest)
        else active_session(manifest)
    )
    collector = Collector(config, run_id, directory, manifest, session)
    collector.before = len(collector.observations)
    if session["status"] in {"complete", "bounded"}:
        collector.summarize()
        return {
            "status": session["status"],
            "saved": 0,
            "total_saved": manifest.get("current_documents_saved", 0),
            "sources": manifest["current_sources"],
            "session": session,
        }
    return collector.run()


def retry_failed(config, run_id):
    """Explicitly retry only missing descriptions; never reset consumed allowances."""
    from .cli import manifest_for, run_directory, save_manifest
    directory = run_directory(config, run_id)
    manifest = manifest_for(directory, run_id)
    session = active_session(manifest)
    if not session or config.get("study_version") != 1:
        raise ValueError("Select an existing comparison study.")
    inventory = read_jsonl(directory / "inventory.jsonl")
    count = 0
    for key, info in session["sources"].items():
        missing = set(info.get("failed_postings", {}))
        if not missing:
            continue
        if not info.get("inventory_enumeration_complete"):
            raise ValueError("Finish inventory enumeration before targeted retry.")
        candidates = {r["site_job_id"]:r for r in inventory if r["session_id"] == session["id"] and r["source_id"] == key and r["site_job_id"] in missing}
        info.setdefault("retry_history", []).append({"at": utc_now(), "failures": info["failed_postings"]})
        info.update(pending=list(candidates.values()), next_index=0, status="processing", failed_postings={})
        info["seen_paths"] = [identity for identity in info["seen_paths"] if identity not in candidates]
        count += len(candidates)
    if not count:
        raise ValueError("No failed descriptions to retry.")
    session.update(status="paused", pause_reason="targeted_retry_prepared")
    save_manifest(directory, manifest)
    return {"retry_descriptions": count, "usage": session["usage"]}
