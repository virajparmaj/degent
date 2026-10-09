"""Resumable bounded company verification; no research runs or model work."""

from __future__ import annotations

import copy
import csv
import io
import json
import signal
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .collection_http import BudgetReached, CollectionClient
from .companies import CompanyLibrary, domain, normalize, verification_results
from .store import digest, operation_lock, read_json, utc_now, write_json

DEFAULTS = {
    "requests": 20,
    "bytes": 12_000_000,
    "seconds": 120,
    "batch_requests": 4000,
    "batch_bytes": 2_000_000_000,
    "batch_seconds": 10800,
    "concurrency": 2,
}


def status(row):
    states = {s.get("status", "configured") for s in row.get("current_sources", [])}
    return next(
        (
            s
            for s in ("verified", "configured", "unverified", "unavailable", "unsupported")
            if s in states
        ),
        "unsupported",
    )


def atomic_text(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class VerificationBatch:
    def __init__(self, config, directory):
        self.config, self.library = config, CompanyLibrary(config)
        self.directory = Path(directory).resolve()
        if (
            self.directory.parent != self.library.root.resolve()
            or not self.directory.name.startswith("verification-")
        ):
            raise ValueError(
                "Batch must be an existing verification directory in the company library"
            )
        frozen_config = self.directory / "verification-config.json"
        if frozen_config.exists():
            self.config = read_json(frozen_config)
        self.path = self.directory / "progress.json"
        self.state = read_json(self.path)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.start = None
        self.time_before = self.state["usage"]["seconds"]

    @classmethod
    def create(cls, config, ids=None, targets=None, **options):
        library = CompanyLibrary(config)
        budgets = {**DEFAULTS, **{k: v for k, v in options.items() if v is not None}}
        for key, value in budgets.items():
            if (
                type(value) is not int
                or value < 1
                or value > (4 if key == "concurrency" else 10_000_000_000)
            ):
                raise ValueError("Invalid verification budget: " + key)
        with operation_lock(library.root):
            document = (
                read_json(library.path)
                if library.path.exists()
                else {"version": 1, "companies": library.seed()}
            )
            rows = document["companies"]
            target_hash, mappings = None, []
            if targets:
                payload = Path(targets).read_bytes()
                target_hash = digest(payload)
                for target in json.loads(payload)["companies"]:
                    names = {
                        normalize(target["name"]),
                        *(normalize(a) for a in target.get("aliases", [])),
                    }
                    matches = [
                        r
                        for r in rows
                        if names
                        & {normalize(r["name"]), *(normalize(a) for a in r.get("aliases", []))}
                        and domain(r.get("website", "")) == domain(target.get("website", ""))
                    ]
                    if len(matches) != 1:
                        raise ValueError("Target mapping missing/ambiguous: " + target["name"])
                    mappings.append({"target_name": target["name"], "company_id": matches[0]["id"]})
                selected = [m["company_id"] for m in mappings]
                if ids:
                    selected = [i for i in selected if i in ids]
            else:
                selected = ids or [r["id"] for r in rows]
            if (
                len(set(selected)) != len(selected)
                or not set(selected) <= {r["id"] for r in rows}
                or not selected
            ):
                raise ValueError("Choose unique existing company IDs")
            directory = library.root / ("verification-" + utc_now().replace(":", "-"))
            directory.mkdir(parents=True)
            write_json(directory / "companies-before.json", document)
            state = {
                "version": 1,
                "id": directory.name,
                "started_at": utc_now(),
                "status": "prepared",
                "ids": selected,
                "target_list_sha256": target_hash,
                "target_mapping": mappings,
                "starting_scope_sha256": digest(json.dumps(selected)),
                "budgets": budgets,
                "usage": {"requests": 0, "bytes": 0, "seconds": 0},
                "reservations": {},
                "results": {},
                "attempts": {},
                "retry_ids": [],
                "extensions": [],
            }
            write_json(
                directory / "verification-config.json",
                {k: v for k, v in config.items() if k != "_path"},
            )
            write_json(directory / "progress.json", state)
        return cls(config, directory)

    def elapsed(self):
        return self.time_before + (time.monotonic() - self.start if self.start else 0)

    def checkpoint(self):
        self.state["usage"]["seconds"] = self.elapsed()
        self.state["updated_at"] = utc_now()
        write_json(self.path, self.state)

    def cancelled(self):
        return (
            self.stop_event.is_set()
            or (self.directory / "stop-requested").exists()
            or self.elapsed() >= self.state["budgets"]["batch_seconds"]
        )

    def reserve(self, company_id, event):
        with self.lock:
            if self.cancelled():
                raise BudgetReached("batch_stopped_or_runtime_limit")
            usage, bounds = self.state["usage"], self.state["budgets"]
            reserved = sum(r["bytes"] for r in self.state["reservations"].values())
            if usage["requests"] >= bounds["batch_requests"]:
                raise BudgetReached("batch_request_limit")
            if usage["bytes"] + reserved + event["reserved_bytes"] > bounds["batch_bytes"]:
                raise BudgetReached("batch_byte_limit")
            event["reservation_id"] = uuid.uuid4().hex
            usage["requests"] += 1
            self.state["reservations"][event["reservation_id"]] = {
                "company_id": company_id,
                "bytes": event["reserved_bytes"],
            }
            self.checkpoint()

    def finish(self, event):
        with self.lock:
            self.state["reservations"].pop(event["reservation_id"], None)
            self.state["usage"]["bytes"] += event["bytes"]
            self.checkpoint()

    def check_company(self, identity, attempt):
        if self.cancelled():
            return None
        with self.lock:
            self.state.setdefault("active_companies", {})[identity] = self.state["budgets"][
                "seconds"
            ]
            self.checkpoint()
        # Recover a library commit whose report projection was interrupted.
        current = self.library.get(identity)
        committed = next(
            (e for e in current.get("verification_history", []) if e.get("attempt_id") == attempt),
            None,
        )
        if committed:
            recovered = {
                **current,
                **{
                    k: v
                    for k, v in committed.items()
                    if k.startswith("verification_")
                    or k in {"discovery_outcome", "source_conflicts"}
                },
                "current_sources": committed["sources"],
            }
            return self.result(recovered, attempt)
        bounds = self.state["budgets"]
        client = CollectionClient(
            self.config,
            requests=bounds["requests"],
            byte_limit=bounds["bytes"],
            seconds=min(bounds["seconds"], max(0, bounds["batch_seconds"] - self.elapsed())),
            reserve=lambda event: self.reserve(identity, event),
            finish=self.finish,
        )
        client.cancelled = self.cancelled

        def save_evidence(body, metadata):
            path = self.directory / "evidence" / digest(body)
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_bytes(body)
            return str(path)

        client.save_evidence = save_evidence
        checked_path = self.directory / ("checked-" + attempt + ".json")
        client.save_attempt = lambda checked: write_json(checked_path, checked)
        try:
            checked = (
                self.library.persist_verification(identity, read_json(checked_path), attempt)
                if checked_path.exists()
                else self.library.verify(identity, client=client, attempt_id=attempt)
            )
            return self.result(checked, attempt)
        except Exception as exc:
            return {
                "id": identity,
                "name": current["name"],
                "attempt_id": attempt,
                "status": "unverified",
                "reason": "company_exception:" + str(exc),
                "remaining_action": "inspect exception and retry",
                "completed_at": utc_now(),
                "incomplete": True,
                "current_sources": current.get("current_sources", []),
            }

    @staticmethod
    def result(row, attempt):
        return {
            "id": row["id"],
            "name": row["name"],
            "attempt_id": attempt,
            "status": status(row),
            "reason": (
                row.get("discovery_outcome")
                if status(row) == "verified"
                else row.get("verification_remaining_action") or row.get("discovery_outcome")
            ),
            "discovery_outcome": row.get("discovery_outcome"),
            "remaining_action": row.get("verification_remaining_action"),
            "completed_at": utc_now(),
            "current_sources": copy.deepcopy(row.get("current_sources", [])),
            "source_conflicts": row.get("source_conflicts", []),
            "destinations": row.get("verification_destinations", []),
            "excluded_candidates": row.get("verification_exclusions", []),
            "observed_vendor_leads": row.get("verification_vendor_leads", []),
            "attempted_urls": row.get("verification_evidence", []),
            "usage": row.get("verification_usage", {}),
            "incomplete": "budget" in str(row.get("discovery_outcome"))
            or any(
                "cancelled" in str(e) or "batch_" in str(e.get("reason", ""))
                for e in row.get("verification_evidence", [])
            ),
        }

    def run(self, retry_incomplete=False, retry_ids=None, preserve_stop=False):
        with operation_lock(self.directory):
            self.state = read_json(self.path)
            self.time_before = self.state["usage"]["seconds"] + max(
                self.state.get("active_companies", {}).values(), default=0
            )
            self.state["active_companies"] = {}
            # Interrupted reservations remain charged conservatively.
            self.state["usage"]["bytes"] += sum(
                r["bytes"] for r in self.state["reservations"].values()
            )
            self.state["reservations"] = {}
            if not preserve_stop and self.state["status"] in {"stopped", "complete"}:
                (self.directory / "stop-requested").unlink(missing_ok=True)
            requested = set(retry_ids or []) | (
                {i for i, r in self.state["results"].items() if r.get("incomplete")}
                if retry_incomplete
                else set()
            )
            if not requested <= set(self.state["ids"]):
                raise ValueError("Retry IDs must belong to frozen batch")
            previously_pending = set(self.state.get("pending_ids", []))
            pending = [
                i
                for i in self.state["ids"]
                if i not in self.state["results"] or i in requested or i in previously_pending
            ]
            self.state["pending_ids"] = pending.copy()
            self.state["pass_progress"] = {"planned": len(pending), "completed": 0}
            self.start = time.monotonic()
            self.state["status"] = "running"
            self.checkpoint()
            for identity in pending:
                if identity in requested or identity not in self.state["attempts"]:
                    self.state["attempts"][identity] = uuid.uuid4().hex
            self.checkpoint()
            try:
                with ThreadPoolExecutor(max_workers=self.state["budgets"]["concurrency"]) as pool:
                    futures = {
                        pool.submit(self.check_company, i, self.state["attempts"][i]): i
                        for i in pending
                    }
                    for future in as_completed(futures):
                        identity, result = futures[future], future.result()
                        if result is not None:
                            with self.lock:
                                self.state.get("active_companies", {}).pop(identity, None)
                                self.state["results"][identity] = result
                                self.state["pending_ids"].remove(identity)
                                self.state["pass_progress"]["completed"] += 1
                                write_json(
                                    self.directory / ("attempt-" + result["attempt_id"] + ".json"),
                                    result,
                                )
                                self.checkpoint()
                                self.report()
                self.state["status"] = "stopped" if self.cancelled() else "complete"
            finally:
                self.checkpoint()
                self.start = None
                self.time_before = self.state["usage"]["seconds"]
                self.report()
        return self.summary()

    def summary(self):
        return {
            "id": self.state["id"],
            "directory": str(self.directory),
            "status": self.state["status"],
            "total": len(self.state["ids"]),
            "processed": len(self.state["results"]),
            "remaining": len(self.state["ids"]) - len(self.state["results"]),
            "completed": sum(
                not r.get("incomplete", False) for r in self.state["results"].values()
            ),
            "deferred": sum(
                bool(r.get("incomplete", False)) for r in self.state["results"].values()
            ),
            "unprocessed": len(self.state["ids"]) - len(self.state["results"]),
            "counts": dict(Counter(r["status"] for r in self.state["results"].values())),
            "pending_checks": len(self.state.get("pending_ids", [])),
            "pass_progress": self.state.get("pass_progress", {}),
            "usage": self.state["usage"],
            "budgets": self.state["budgets"],
        }

    def report(self):
        before = {
            r["id"]: r for r in read_json(self.directory / "companies-before.json")["companies"]
        }
        rows = []
        for identity in self.state["ids"]:
            row = copy.deepcopy(
                self.state["results"].get(identity)
                or {
                    "id": identity,
                    "name": before[identity]["name"],
                    "status": "deferred",
                    "reason": "not_processed_or_batch_stopped",
                    "remaining_action": "resume frozen batch",
                    "incomplete": True,
                }
            )
            for source in row.get("current_sources", []):
                source["verification_results"] = verification_results(
                    source, row.get("attempted_urls", [])
                )
            old, new = status(before[identity]), row["status"]
            old_ids = {s["id"] for s in before[identity].get("current_sources", [])}
            new_ids = {s["id"] for s in row.get("current_sources", [])}
            row.update(
                retry_pending=identity in self.state.get("pending_ids", []),
                acceptance_outcome=(
                    "deferred"
                    if row.get("incomplete")
                    else "verified" if new == "verified" else "completed_unresolved"
                ),
                before_status=old,
                transition=(
                    "new_success"
                    if old != "verified" and new == "verified"
                    else (
                        "regression"
                        if old == "verified" and new != "verified"
                        else "unchanged_success" if new == "verified" else "unchanged_failure"
                    )
                ),
                added_sources=sorted(new_ids - old_ids),
                removed_sources=sorted(old_ids - new_ids) if new != "deferred" else [],
            )
            rows.append(row)
        report = {
            **self.summary(),
            "target_list_sha256": self.state["target_list_sha256"],
            "started_at": self.state["started_at"],
            "updated_at": utc_now(),
            "before_counts": dict(Counter(status(before[i]) for i in self.state["ids"])),
            "transitions": dict(Counter(r["transition"] for r in rows)),
            "provider_distribution": dict(
                Counter(
                    s["provider"]
                    for r in rows
                    for s in r.get("current_sources", [])
                    if s.get("status") == "verified"
                )
            ),
            "companies": rows,
        }
        write_json(self.directory / "report.json", report)
        buffer = io.StringIO()
        columns = [
            "id",
            "name",
            "before_status",
            "status",
            "acceptance_outcome",
            "transition",
            "reason",
            "remaining_action",
            "attempted_urls",
            "current_sources",
            "source_conflicts",
            "usage",
            "completed_at",
            "added_sources",
            "removed_sources",
        ]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row_index, row in enumerate(rows):
            csv_row = copy.deepcopy(row)
            # Each dimension refers to the shared evidence column; JSON retains full references.
            for source_index, source in enumerate(csv_row.get("current_sources", [])):
                chain = source.get("ownership_chain", [])
                if len(chain) > 2:
                    source["ownership_chain"] = [
                        {
                            "report_file": "report.json",
                            "json_pointer": f"/companies/{row_index}/current_sources/{source_index}/ownership_chain",
                            "reference_count": len(chain),
                        }
                    ]
                for dimension in source.get("verification_results", {}).values():
                    if dimension.get("evidence_refs"):
                        dimension["evidence_refs"] = [{"report_field": "attempted_urls"}]
            writer.writerow(
                {
                    k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                    for k, v in csv_row.items()
                    if k in columns
                }
            )
        atomic_text(self.directory / "report.csv", buffer.getvalue())

        def escape(value):
            return (
                str(value or "")
                .replace("|", "\\|")
                .replace("\n", " ")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        lines = [
            "# Company source verification",
            "",
            f"Processed {report['processed']} / {report['total']}; remaining {report['remaining']}. Status: {report['status']}.",
            "",
            f"Completed checks: {report['completed']}; deferred checks: {report['deferred']}; unprocessed: {report['unprocessed']}.",
            "Before: " + json.dumps(report["before_counts"]),
            "After: " + json.dumps(report["counts"]),
            "Providers: " + json.dumps(report["provider_distribution"]),
            "Budgets: " + json.dumps(report["budgets"]),
            "Usage: " + json.dumps(report["usage"]),
            "",
            "Verification only; inventory completeness is not inferred. No research runs or model calls.",
            "",
            "| Company | Before | Latest | Acceptance | Provider | Reason / next action |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            lines.append(
                "| "
                + " | ".join(
                    escape(v)
                    for v in [
                        row["name"],
                        row["before_status"],
                        row["status"],
                        row["acceptance_outcome"],
                        ", ".join(
                            sorted(
                                {
                                    s["provider"]
                                    for s in row.get("current_sources", [])
                                    if s.get("status") == "verified"
                                }
                            )
                        ),
                        str(row.get("reason")) + "; " + str(row.get("remaining_action")),
                    ]
                )
                + " |"
            )
        atomic_text(self.directory / "report.md", "\n".join(lines) + "\n")
