"""Loopback-only dashboard and sequential, interruptible pipeline controller."""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .companies import CompanyLibrary
from .config import DEFAULT_CONFIG, load_config
from .review import effective_extractions, extraction_identity
from .store import append_jsonl, digest, operation_lock, read_json, read_jsonl, utc_now, write_json

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
EXPORTS = (
    "evidence-report.md",
    "report.json",
    "coverage.csv",
    "employer-period-coverage.csv",
    "observations.csv",
    "requirements.csv",
    "same-vacancy-changes.csv",
    "requirement-shares.csv",
    "manifest.json",
    "reviews.jsonl",
    "review-packet.md",
    "requirement-combinations.csv",
    "wording-shares.csv",
    "feasibility.json",
    "activity.jsonl",
    "evaluation.json",
    "document-reviews.jsonl",
    "model-outputs.jsonl",
    "current-selection.jsonl",
    "inventory.jsonl",
    "source-units.jsonl",
    "role-profiles.jsonl",
    "role-proposals.json",
    "taxonomy.json",
    "gold-packet.json",
    "audit-packet.json",
    "token-estimates.json",
    "mention-counts.csv",
    "experience-proficiency.csv",
    "experience-summary.csv",
    "mention-summary.csv",
    "study-policy.json",
    "company-source.json",
    "role-baseline.html",
)
OPERATIONS = {
    "doctor",
    "discover",
    "fetch",
    "extract",
    "report",
    "current",
    "refresh",
    "run",
    "audit",
}


class Dashboard:
    def __init__(self, root: Path = ROOT, config_path: Path = DEFAULT_CONFIG):
        self.root = root.resolve()
        self.base = load_config(config_path)
        for key in ("data_dir", "run_dir", "library_dir", "snapshot_dir"):
            if self.base.get(key) and not Path(self.base[key]).is_absolute():
                self.base[key] = str(self.root / self.base[key])
        self.library = CompanyLibrary(self.base)
        self.runs = self.root / self.base.get("run_dir", "runs")
        self.runs.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.job: dict = {}
        self.verification_directory = None

    def verification_state(self):
        if not self.verification_directory:
            directories = sorted(
                self.library.root.glob("verification-*/progress.json"), reverse=True
            )
            if not directories:
                return None
            self.verification_directory = directories[0].parent
        from .verification_batch import VerificationBatch

        batch = VerificationBatch(self.base, self.verification_directory)
        return {**batch.summary(), "results": list(batch.state["results"].values())}

    def start_verification(self, payload):
        from .verification_batch import VerificationBatch

        if self.busy():
            raise ValueError("Wait for the active operation")
        if payload.get("resume"):
            if not self.verification_directory:
                self.verification_state()
            if not self.verification_directory:
                raise ValueError("No verification batch to resume")
            batch = VerificationBatch(self.base, self.verification_directory)
        else:
            ids = payload.get("ids")
            if ids is not None and (
                not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
            ):
                raise ValueError("Expected company IDs")
            batch = VerificationBatch.create(self.base, ids=ids)
        self.verification_directory = batch.directory
        with operation_lock(batch.directory):
            (batch.directory / "stop-requested").unlink(missing_ok=True)
        log = (batch.directory / "worker.log").open("a", encoding="utf-8")
        effective_config = batch.directory / "verification-config.json"
        write_json(effective_config, {k: v for k, v in self.base.items() if k != "_path"})
        command = [
            sys.executable,
            "-m",
            "degent",
            "--config",
            str(effective_config),
            "companies",
            "verify-all",
            "--resume",
            str(batch.directory),
            "--keep-stop-request",
        ]
        if payload.get("retry_incomplete"):
            command.append("--retry-incomplete")
        try:
            self.process = subprocess.Popen(
                command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        finally:
            log.close()
        self.job = {
            "operation": "company-verification",
            "status": "running",
            "directory": str(batch.directory),
        }

        def watch():
            code = self.process.wait()
            with self.lock:
                self.job.update(status="completed" if code == 0 else "failed", exit_code=code)

        threading.Thread(target=watch, daemon=True).start()
        return batch.summary()

    def stop_verification(self):
        if self.verification_directory:
            (self.verification_directory / "stop-requested").touch()
        return self.verification_state()

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
            raise ValueError(
                "Use a run name with 1–80 letters, numbers, dots, dashes or underscores."
            )
        directory = self.runs / run_id
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.runs.resolve()):
            raise ValueError("Invalid run directory.")
        return directory

    def config(self, run_id: str) -> dict:
        saved = self.run_dir(run_id) / "dashboard-config.json"
        return load_config(saved) if saved.exists() else copy.deepcopy(self.base)

    def read(self, directory: Path, filename: str, fallback):
        file = directory / filename
        return read_json(file) if file.exists() else fallback

    def run_list(self) -> list[str]:
        return [
            p.name
            for p in sorted(self.runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if p.is_dir() and not p.is_symlink() and (p / "manifest.json").exists()
        ]

    def phase_state(self, run_id: str) -> dict:
        from .phase_workspace import state

        return {**state(self.run_dir(run_id)), "token": self.token}

    def phase_action(self, payload: dict, action: str) -> dict:
        from . import phase_workspace as workspace

        directory = self.run_dir(payload["run_id"])
        kind, identity = payload.get("kind", ""), payload.get("document_id", "")
        if action == "draft":
            return workspace.save_draft(directory, payload["decision"], kind, identity, payload["revision"])
        if action == "recover-draft":
            return workspace.recover_draft(directory, kind, identity)
        if action == "validate":
            with operation_lock(directory):
                return workspace.validate_export(directory, payload["decision"])
        if action == "submit":
            return workspace.submit(directory, payload["decision"], payload["history_sha256"])
        if action == "import":
            return workspace.import_drafts(directory, payload["decision"], payload["revisions"])
        raise ValueError("Unknown offline phase-review action.")

    def observations(self, directory: Path) -> list[dict]:
        return read_jsonl(directory / "observations.jsonl")

    def requirements(self, directory: Path) -> list[dict]:
        observations = {
            o["document_id"]: o
            for o in self.observations(directory)
            if o.get("body_complete") and o.get("eligible")
        }
        decisions = {r["requirement_id"]: r for r in read_jsonl(directory / "reviews.jsonl")}
        result = []
        for extraction in effective_extractions(directory):
            observation = observations.get(extraction["document_id"])
            if not extraction.get("accepted") or not observation:
                continue
            for requirement in extraction.get("requirements", []):
                identity = requirement["requirement_id"]
                decision = decisions.get(identity, {})
                result.append(
                    {
                        **observation,
                        **requirement,
                        "id": identity,
                        "extraction_method": extraction.get("extraction_method"),
                        "source_sha256": extraction.get("source_sha256"),
                        "validation_errors": extraction.get("validation_errors", []),
                        "revision": decision.get("revision", 0),
                        "review_note": decision.get("note", ""),
                    }
                )
        return result

    def state(self, run_id: str) -> dict:
        from .review import document_reviewed
        from .vocabulary import vocabulary

        directory = self.run_dir(run_id)
        config = self.config(run_id)
        manifest = self.read(directory, "manifest.json", {})
        config["periods"] = list(
            {
                p["id"]: p for p in [*config["periods"], *(manifest.get("observation_periods", []) if config.get("study_version") != 1 else [])]
            }.values()
        )
        observations = self.observations(directory)
        eligible = [o for o in observations if o.get("eligible") and o.get("body_complete")]
        requirements = self.requirements(directory)
        extractions = effective_extractions(directory)
        eligible_ids = {o["document_id"] for o in eligible}
        accepted = [
            r for r in extractions if r.get("accepted") and r["document_id"] in eligible_ids
        ]
        queries = list(
            {r["query_id"]: r for r in read_jsonl(directory / "query-results.jsonl")}.values()
        )
        attempts = manifest.get("body_attempts", [])
        current_attempts = manifest.get("current_body_attempts", [])
        from .sources import current_selections

        selection = current_selections(directory)
        report = self.read(directory, "report.json", {})
        candidate_path = directory / "archive-records.jsonl"
        candidate_count = 0
        if candidate_path.exists():
            with candidate_path.open(encoding="utf-8") as stream:
                candidate_count = sum(line.endswith("\n") for line in stream if line.strip())
        counts = {
            "candidates": candidate_count,
            "queries": len(queries),
            "blocked": sum(
                r.get("status") in {"blocked_or_failed", "rate_limited_suspend"} for r in queries
            ),
            "empty_queries": sum(
                r.get("status") == "ok" and not r.get("records_found") for r in queries
            ),
            "current_screened": len(selection),
            "current_pre_rejected": sum(
                r.get("status") == "rejected_before_retrieval" for r in selection
            ),
            "current_detail_attempts": len(current_attempts),
            "current_saved": manifest.get("current_documents_saved", 0),
            "attempts": len(attempts) + len(current_attempts),
            "retrieved": len(observations),
            "eligible": len(eligible),
            "rejected": len(observations) - len(eligible),
            "failed": sum(
                r.get("status") in {"failed", "suspended_rate_limit"}
                for r in attempts + current_attempts
            ),
            "vacancies": len({o.get("vacancy_id") or o["document_id"] for o in eligible}),
            "documents": len(accepted),
            "requirements": len(requirements),
            "pending": sum(r["human_review"] != "approved" for r in requirements),
            "documents_pending_review": sum(not document_reviewed(r) for r in accepted),
            "approved": sum(r["human_review"] == "approved" for r in requirements),
            "changes": len(report.get("same_vacancy_changes", [])),
            "cohorts": len(
                {
                    tuple(
                        r.get(k)
                        for k in (
                            "employer_id",
                            "brand",
                            "role_group_id" if config.get("study_version") == 1 else "role_track",
                            "employment_type" if config.get("study_version") == 1 else "role_subtype",
                            "assessment_basis",
                            "time_basis",
                            "level",
                            "period",
                        )
                    )
                    for r in report.get("requirement_shares", [])
                    if r.get("share_percent") is not None
                }
            ),
        }
        used = manifest.get(
            "discovery_network_requests", manifest.get("discovery_requests_used", 0)
        )
        config_keys = (
            "employers",
            "periods",
            "roles",
            "archives",
            "discovery_request_cap",
            "body_attempt_cap",
            "extract_document_cap",
            "semantic_review_call_cap",
            "codex_model",
            "review_model",
            "sources",
            "current_request_cap",
            "current_document_cap",
            "model_call_cap",
            "comparison_review",
            "comparison_status",
            "comparison_scope",
            "collection_version",
            "collection_byte_cap",
            "collection_runtime_seconds",
            "collection_storage_cap",
            "collection_chunk_size",
            "study_version",
            "comparison_minimum",
            "token_planning",
        )
        source_files = [
            directory / name
            for name in (
                "observations.jsonl",
                "requirements.jsonl",
                "reviews.jsonl",
                "document-reviews.jsonl",
                "query-results.jsonl",
                "taxonomy.json",
                "evaluation.json",
            )
        ]
        report_path = directory / "report.json"
        stale = report_path.exists() and any(
            f.exists() and f.stat().st_mtime_ns > report_path.stat().st_mtime_ns
            for f in source_files
        )
        if config.get("study_version") == 1:
            from .study_metrics import analysis_signature
            stale |= report_path.exists() and report.get("analysis_signature") != analysis_signature(directory)
            stale |= report_path.exists() and report.get("collection_signature") != digest(json.dumps(manifest.get("collection_sessions", []), sort_keys=True))
        else:
            stale |= report_path.exists() and report.get("retrieval_summary", {}).get("collection_sessions", []) != manifest.get("collection_sessions", [])
        collection = (
            copy.deepcopy(manifest.get("collection_sessions", [])[-1])
            if manifest.get("collection_sessions")
            else None
        )
        if collection:
            for info in collection.get("sources", {}).values():
                for field in ("pending", "seen_paths", "detail_captures"):
                    info.pop(field, None)
            current_docs = {
                o["document_id"]
                for o in eligible
                if o.get("collection_session_id") == collection["id"]
            }
            extracted_docs = {r["document_id"] for r in accepted}
            collection.setdefault("counts", {}).update(
                pending_extraction=len(current_docs - extracted_docs),
                pending_review=sum(
                    r["document_id"] in current_docs and not document_reviewed(r) for r in accepted
                ),
            )
        vocabulary_view = vocabulary()
        if config.get("study_version") == 1:
            from .taxonomy import load_taxonomy
            vocabulary_view.update({k:{**v, "label":v["name"]} for k,v in load_taxonomy(directory)["skills"].items()})
        return {
            "run_id": run_id,
            "runs": self.run_list(),
            "token": self.token,
            "config": {**{k: config.get(k) for k in config_keys}, "inference": {
                k:config.get("inference", {}).get(k) for k in ("identity", "enabled", "paid")}},
            "vocabulary": vocabulary_view,
            "workflow": manifest.get("workflow", {}),
            "activity": read_jsonl(directory / "activity.jsonl")[-6:],
            "current_sources": {
                key: {k: v for k, v in info.items() if k != "decisions"}
                for key, info in manifest.get("current_sources", {}).items()
            },
            "model_calls_used": manifest.get("inference_usage", {}).get("calls", manifest.get("model_calls_used", 0)),
            "current_requests_used": manifest.get("current_requests_used", 0),
            "extraction_status": (
                "stopped_by_limit"
                if str(manifest.get("extraction_pause_reason", "")).endswith("_limit")
                else manifest.get("extraction_status")
            ),
            "extraction_pause_reason": manifest.get("extraction_pause_reason"),
            "extraction_issues": [
                {"document_id": r["document_id"], "errors": r.get("validation_errors", [])}
                for r in extractions
                if not r.get("accepted")
            ][-3:],
            "feasibility": self.read(directory, "feasibility.json", None),
            "collection": collection,
            "catalog": {
                **{k: self.base[k] for k in ("periods", "roles")},
                "employers": [self.library.describe(r) for r in self.library.all()],
            },
            "exists": (directory / "manifest.json").exists(),
            "counts": counts,
            "discovery_used": used,
            "discovery_left": max(0, config["discovery_request_cap"] - used),
            "body_left": max(0, config["body_attempt_cap"] - len(attempts)),
            "method": (
                ((config.get("inference", {}).get("identity") or {}).get("provider", "provider_pending")) if config.get("study_version") == 1 else (
                    "codex" if config.get("dashboard_codex", manifest.get("codex_extraction_enabled")) else "rules")
            ),
            "job": self.job_state(directory),
            "doctor": self.read(directory, "doctor.json", None),
            "coverage": ([{**r, "employer_id": "capital_one", "period_id": r["period"], "records_found": r["eligible_distinct_vacancies"], "blocked_queries": 0, "inventory_status": r["capture_coverage"]} for r in report.get("coverage", [])]
                         if config.get("study_version") == 1 else self.read(directory, "coverage-matrix.json", manifest.get("coverage", []))),
            "preview_queries": queries[:2],
            "preview_requirements": requirements[:3],
            "preview_observations": observations[:3],
            "report": report,
            "report_stale": stale,
            "exports": [name for name in EXPORTS if (directory / name).is_file()],
            "attempt_reasons": dict(Counter(a.get("reason", "unknown") for a in attempts)),
        }

    def job_state(self, directory: Path) -> dict:
        if self.job:
            return copy.deepcopy(self.job)
        saved = self.read(directory, "dashboard-job.json", {})
        if saved.get("status") == "running":
            saved.update(status="stopped", detail="The app closed. Resume from saved checkpoints.")
        return saved

    def records(self, run_id: str, kind: str, query: dict) -> dict:
        directory = self.run_dir(run_id)
        if kind == "queries":
            data = list(
                {r["query_id"]: r for r in read_jsonl(directory / "query-results.jsonl")}.values()
            )
            data = [{**r, "id": digest(r["query_id"])[:24], "title": r["path"]} for r in data]
        elif kind == "captures":
            observations = self.observations(directory)
            seen = {o["observation_id"] for o in observations}
            data = [{**o, "id": o["observation_id"]} for o in observations]
            attempts = self.read(directory, "manifest.json", {}).get("body_attempts", [])
            data += [
                {
                    **a,
                    "id": a["record_key"],
                    "title": a.get("original_url"),
                    "body_status": a.get("reason"),
                }
                for a in attempts
                if a.get("observation_id") not in seen
            ]
        elif kind == "selections":
            from .sources import current_selections

            data = [
                {
                    **r,
                    "id": r["site_job_id"],
                    "employer_id": "capital_one",
                    "original_url": r.get("source_url"),
                }
                for r in current_selections(directory)
            ]
        elif kind == "requirements":
            data = self.requirements(directory)
        elif kind in {"changes", "cohorts", "combinations", "wording"}:
            report = self.read(directory, "report.json", {})
            key = {
                "changes": "same_vacancy_changes",
                "cohorts": "requirement_shares",
                "combinations": "requirement_combinations",
                "wording": "wording_shares",
            }[kind]
            data = [
                {**r, "id": str(i), "title": r.get("concept") or r.get("vacancy_id")}
                for i, r in enumerate(report.get(key, []))
            ]
        elif kind == "coverage":
            data = [
                {**r, "id": str(i), "title": r["employer_id"] + " / " + r["period_id"]}
                for i, r in enumerate(self.state(run_id)["coverage"])
            ]
        else:
            raise ValueError("Unknown record collection.")
        for field in ("employer_id", "period_id", "category", "statement_scope", "status"):
            value = query.get(field, [""])[0]
            if value:
                data = [r for r in data if r.get(field, r.get("period")) == value]
        if kind == "requirements" and query.get("unknown", [""])[0] == "1":
            data = [r for r in data if not r.get("concept") or r.get("match_basis") == "unresolved"]
        if query.get("pending", [""])[0] == "1":
            data = [r for r in data if r.get("human_review") != "approved"]
        search = query.get("search", [""])[0].lower()
        data = [r for r in data if search in json.dumps(r, ensure_ascii=False).lower()]
        size = min(6, max(1, int(query.get("size", ["4"])[0])))
        pages = max(1, (len(data) + size - 1) // size)
        page = min(pages - 1, max(0, int(query.get("page", ["0"])[0])))
        return {
            "items": data[page * size : (page + 1) * size],
            "page": page,
            "pages": pages,
            "total": len(data),
        }

    def source(self, run_id: str, observation_id: str, page: int, anchor: str = "") -> dict:
        directory = self.run_dir(run_id)
        observation = next(
            (o for o in self.observations(directory) if o["observation_id"] == observation_id), None
        )
        if not observation:
            raise ValueError("Source observation does not exist.")
        file = (self.root / observation["clean_file"]).resolve()
        data_root = (self.root / self.config(run_id)["data_dir"]).resolve()
        snapshot_root = (
            self.root / self.config(run_id).get("snapshot_dir", str(data_root / "snapshots"))
        ).resolve()
        if not file.is_relative_to(data_root) and not file.is_relative_to(snapshot_root):
            raise ValueError("Source lies outside the configured data directory.")
        text = file.read_text(encoding="utf-8") if file.exists() else ""
        size = 640
        pages = max(1, (len(text) + size - 1) // size)
        if anchor and anchor in text:
            page = text.index(anchor) // size
        page = min(pages - 1, max(0, page))
        history = []
        if observation.get("career_source_id"):
            from .snapshots import SnapshotStore

            history = [
                {
                    k: r.get(k)
                    for k in (
                        "observation_id",
                        "document_id",
                        "current_capture_at",
                        "collector_first_observed_at",
                        "content_change",
                        "raw_source_sha256",
                    )
                }
                for r in SnapshotStore(self.config(run_id)).history(observation["vacancy_id"])
            ]
        raw = Path(observation.get("raw_source_file") or "")
        raw_ok = not observation.get("raw_source_sha256") or (
            raw.is_file() and digest(raw.read_bytes()) == observation["raw_source_sha256"]
        )
        return {
            "history": history,
            "text": text[page * size : (page + 1) * size],
            "page": page,
            "pages": pages,
            "source_sha256": digest(text),
            "verified": raw_ok and digest(text) == observation.get("clean_sha256"),
            "missing": not file.exists(),
        }

    def create_run(self, payload: dict) -> dict:
        if payload.get("preset") == "capital-one-knowledge":
            from .study import create
            return create(self.base, payload["run_id"])
        run_id = payload["run_id"]
        directory = self.run_dir(run_id)
        if directory.exists():
            raise ValueError(
                "That run already exists. Choose a new name to preserve its evidence and limits."
            )
        parent = payload.get("parent_run_id")
        if parent and not (self.run_dir(parent) / "dashboard-config.json").exists():
            raise ValueError("The parent study must be a saved run.")
        config = copy.deepcopy(self.config(parent) if parent else self.base)
        catalog = {
            "employers": self.library.all(),
            "periods": list(
                {p["id"]: p for p in [*self.base["periods"], *config["periods"]]}.values()
            ),
        }
        for field in ("employers", "periods"):
            requested = payload.get(field, [row["id"] for row in config[field]])
            allowed = {row["id"] for row in catalog[field]}
            if (
                not isinstance(requested, list)
                or (
                    not requested
                    and not (
                        field == "periods"
                        and payload.get("sources", config.get("sources")) == ["current"]
                    )
                )
                or not set(requested).issubset(allowed)
            ):
                raise ValueError(f"Select at least one configured {field} value.")
            config[field] = [copy.deepcopy(row) for row in catalog[field] if row["id"] in requested]
        tracks = payload.get("tracks", config["roles"]["tracks"])
        if (
            not isinstance(tracks, list)
            or not tracks
            or not set(tracks).issubset(self.base["roles"]["tracks"])
        ):
            raise ValueError("Select at least one configured role track.")
        config["roles"]["tracks"] = tracks
        for key, limit in (
            ("discovery_request_cap", 300),
            ("body_attempt_cap", 150),
            ("extract_document_cap", 30),
            ("semantic_review_call_cap", 10),
            ("current_request_cap", 10000),
            ("model_call_cap", 1000),
            ("collection_byte_cap", 1_000_000_000),
            ("collection_storage_cap", 1_000_000_000),
            ("collection_runtime_seconds", 3600),
        ):
            value = payload.get(
                key,
                config.get(
                    key,
                    {
                        "collection_byte_cap": 50_000_000,
                        "collection_storage_cap": 100_000_000,
                        "collection_runtime_seconds": 120,
                    }.get(key),
                ),
            )
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f"{key} must be an integer from 1 to {limit}.")
            config[key] = value
        limit = payload.get(
            "current_document_cap", config.get("current_document_cap") if parent else None
        )
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("Optional document limit must be a positive integer or empty.")
        config.update(collection_version=2, current_document_cap=limit)
        requested_sources = payload.get("source_ids")
        if requested_sources is not None:
            allowed_sources = {
                s["id"] for e in config["employers"] for s in e.get("current_sources", [])
            }
            if (
                not isinstance(requested_sources, list)
                or not set(requested_sources) <= allowed_sources
            ):
                raise ValueError("Choose career sources from the selected companies.")
            for e in config["employers"]:
                e["current_sources"] = [
                    s for s in e.get("current_sources", []) if s["id"] in requested_sources
                ]
        if parent:
            previous = self.config(parent)
            config["scope_revision"] = {
                "parent_run_id": parent,
                "created_at": utc_now(),
                "before": previous["employers"],
                "after": copy.deepcopy(config["employers"]),
                "added": sorted(
                    set(e["id"] for e in config["employers"])
                    - set(e["id"] for e in previous["employers"])
                ),
                "removed": sorted(
                    set(e["id"] for e in previous["employers"])
                    - set(e["id"] for e in config["employers"])
                ),
            }
        from .sources import current_period

        selected_sources = payload.get("sources", config.get("sources", ["archives", "current"]))
        if (
            not isinstance(selected_sources, list)
            or not selected_sources
            or not set(selected_sources).issubset({"archives", "current", "imports"})
        ):
            raise ValueError("Choose archives, current feeds, or imports.")
        config["sources"] = selected_sources
        if "current" in selected_sources and payload.get("include_current_period", True):
            snapshot_period = current_period(config)
            if not any(p["id"] == snapshot_period["id"] for p in config["periods"]):
                config["periods"].append(snapshot_period)
        levels = payload.get("levels", config["roles"]["entry_mid_levels"])
        if (
            not isinstance(levels, list)
            or not levels
            or not set(levels).issubset({"entry", "mid", "senior", "management", "executive"})
        ):
            raise ValueError("Choose supported seniority levels.")
        config["roles"]["entry_mid_levels"] = levels
        for key, allowed, default in (
            ("comparison_review", {"reviewed", "accepted"}, "reviewed"),
            (
                "comparison_status",
                {"required", "preferred", "required_or_preferred", "stated"},
                "required_or_preferred",
            ),
            (
                "comparison_scope",
                {"qualification", "responsibility", "company_expectation"},
                "qualification",
            ),
        ):
            value = payload.get(key, config.get(key, default))
            if value not in allowed:
                raise ValueError("Unsupported comparison setting: " + key)
            config[key] = value
        config["dashboard_codex"] = (
            payload.get("method", "codex" if config.get("dashboard_codex") else "rules") == "codex"
        )
        config.pop("_path", None)
        directory.mkdir(parents=True)
        write_json(directory / "dashboard-config.json", config)
        write_json(
            directory / "manifest.json",
            {
                "run_id": run_id,
                "created_at": utc_now(),
                "status": "prepared",
                "queries": [],
                "body_attempts": [],
                "counts": {},
            },
        )
        return {"run_id": run_id}

    def study_action(self, run_id: str, payload: dict) -> dict:
        if self.busy():
            raise ValueError("Wait for the active operation before updating study artifacts.")
        config = self.config(run_id)
        if config.get("study_version") != 1:
            raise ValueError("Select a Capital One knowledge-role study.")
        directory = self.run_dir(run_id)
        from .phase_state import reject_broad
        reject_broad(config, directory=directory)
        from .study import prepare
        from .taxonomy import approve, propose
        from .study_evaluation import gold_packet, evaluate, audit_packet
        action = payload.get("action")
        with operation_lock(directory):
            if action == "prepare":
                return prepare(config, run_id)
            if action == "groups":
                result = propose(directory)
                return {"status": result["status"], "clusters": len(result["clusters"]), "method": result["method"]}
            if action == "approve-taxonomy":
                return approve(directory, payload["decisions"])
            if action == "gold":
                result = gold_packet(directory)
                return {"status": result["status"], "documents": len(result["documents"])}
            if action == "evaluate":
                path = directory / "submitted-gold-labels.json"
                write_json(path, payload["decisions"])
                return evaluate(directory, path)
            if action == "audit-packet":
                result = audit_packet(directory)
                return {"documents": len(result["documents"])}
            if action == "retry-failed":
                from .collection import retry_failed
                return retry_failed(config, run_id)
        raise ValueError("Unknown study action.")

    def continue_collection(self, run_id, payload):
        from .collection import extend

        if self.busy():
            raise ValueError("Wait for the active operation before extending a budget.")
        with operation_lock(self.run_dir(run_id)):
            return extend(self.config(run_id), run_id, payload.get("additions", {}))

    def busy(self) -> bool:
        return self.job.get("status") == "running" or (
            self.process is not None and self.process.poll() is None
        )

    def start(self, run_id: str, operation: str, codex: bool = False) -> dict:
        from .phase_state import reject_broad
        reject_broad(self.config(run_id), directory=self.run_dir(run_id))
        if operation not in OPERATIONS:
            raise ValueError("Unknown pipeline operation.")
        if self.busy():
            raise ValueError(
                "A pipeline operation is already running. Stop it or wait for it to finish."
            )
        state = self.state(run_id)
        if not state["exists"]:
            raise ValueError("Create a run before starting the pipeline.")
        if state["config"].get("study_version") == 1 and operation in {"discover", "fetch"}:
            raise ValueError("This study collects official current inventory; archives remain outside its scope.")
        if operation == "discover" and state["discovery_left"] == 0:
            raise ValueError("The discovery request cap is exhausted.")
        if operation == "fetch" and (not state["counts"]["candidates"] or not state["body_left"]):
            raise ValueError("Recovery needs discovered candidates and available body attempts.")
        if operation == "extract" and not state["counts"]["eligible"]:
            raise ValueError("No complete eligible observations are available for extraction.")
        directory = self.run_dir(run_id)
        saved_config = directory / "dashboard-config.json"
        if not saved_config.exists():
            write_json(saved_config, {k: v for k, v in self.config(run_id).items() if k != "_path"})
        command = [
            sys.executable,
            "-m",
            "degent",
            "--config",
            str(saved_config),
            "--run-id",
            run_id,
            operation,
        ]
        if operation in {"extract", "run"} and codex:
            command.append("--codex")
        log = (directory / "dashboard-worker.log").open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        finally:
            log.close()
        self.job = {
            "run_id": run_id,
            "operation": operation,
            "status": "running",
            "started_at": utc_now(),
        }
        write_json(directory / "dashboard-job.json", self.job)
        threading.Thread(
            target=self._watch, args=(self.process, directory, operation), daemon=True
        ).start()
        return copy.deepcopy(self.job)

    def _watch(self, process: subprocess.Popen, directory: Path, operation: str):
        code = process.wait()
        with self.lock:
            if self.process is not process:
                return
            output = (directory / "dashboard-worker.log").read_text(encoding="utf-8")
            if self.job.get("status") != "stopped":
                self.job.update(
                    status="completed" if code == 0 else "failed",
                    exit_code=code,
                    finished_at=utc_now(),
                )
                self.job["detail"] = (
                    output[-1800:]
                    if code
                    else "Operation completed. Evidence availability is reported separately."
                )
            if operation == "doctor" and code == 0:
                try:
                    write_json(directory / "doctor.json", json.loads(output))
                except json.JSONDecodeError:
                    self.job.update(status="failed", detail="Readiness output was not valid JSON.")
            write_json(directory / "dashboard-job.json", self.job)

    def stop(self) -> dict:
        if self.job.get("operation") == "company-verification":
            return self.stop_verification()
        if self.busy():
            if self.process is not None and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.job.update(
                status="stopped",
                finished_at=utc_now(),
                detail="Stopped. Saved checkpoints remain available for resume.",
            )
            write_json(self.run_dir(self.job["run_id"]) / "dashboard-job.json", self.job)
            timer = threading.Timer(3, self._kill_if_running, args=(self.process,))
            timer.daemon = True
            timer.start()
        return copy.deepcopy(self.job)

    @staticmethod
    def _kill_if_running(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def review(self, run_id: str, payload: dict) -> dict:
        try:
            with operation_lock(self.run_dir(run_id)):
                return self._save_review(run_id, payload)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    def _save_review(self, run_id: str, payload: dict) -> dict:
        from .phase_state import reject_broad
        reject_broad(self.config(run_id), directory=self.run_dir(run_id))
        if self.busy():
            raise ValueError("Wait for the active operation to finish before reviewing evidence.")
        requirement = next(
            (
                r
                for r in self.requirements(self.run_dir(run_id))
                if r["id"] == payload.get("requirement_id")
            ),
            None,
        )
        if not requirement:
            raise ValueError(
                "This extraction is no longer current or eligible. Refresh the review queue."
            )
        if payload.get("revision") != requirement["revision"]:
            raise ValueError("This decision changed in another window. Refresh before saving.")
        status = payload.get("status")
        note = payload.get("note", "")
        if (
            status not in {"required", "preferred", "not_required", "unclear"}
            or not isinstance(note, str)
            or len(note) > 1500
        ):
            raise ValueError("Choose a supported status and a note of at most 1,500 characters.")
        source = self.source(run_id, requirement["observation_id"], 0)
        if not source["verified"] or requirement.get("source_sha256") != source["source_sha256"]:
            raise ValueError(
                "Source content changed since extraction. Extract again before approving."
            )
        decision = {
            "requirement_id": requirement["id"],
            "document_id": requirement["document_id"],
            "source_sha256": source["source_sha256"],
            "previous_status": requirement["status"],
            "status": status,
            "note": note.strip(),
            "revision": requirement["revision"] + 1,
            "reviewed_at": utc_now(),
            "reviewer": "local-user",
        }
        from .vocabulary import SCOPES, vocabulary
        known = vocabulary()
        if self.config(run_id).get("study_version") == 1:
            from .taxonomy import load_taxonomy
            known.update(load_taxonomy(self.run_dir(run_id))["skills"])

        concept = payload.get("concept", requirement.get("concept"))
        if concept is not None and concept not in known:
            raise ValueError("Select a known concept or leave unfamiliar wording unresolved.")
        scope = payload.get("statement_scope", requirement.get("statement_scope", "qualification"))
        if scope not in SCOPES or type(payload.get("excluded", False)) is not bool:
            raise ValueError("Select a supported statement scope and exclusion decision.")
        decision.update(
            concept=concept, statement_scope=scope, excluded=payload.get("excluded", False)
        )
        if concept:
            decision["category"] = known[concept]["category"]
        append_jsonl(self.run_dir(run_id) / "reviews.jsonl", [decision])
        return decision

    def review_document(self, run_id: str, payload: dict) -> dict:
        from .phase_state import reject_broad
        reject_broad(self.config(run_id), directory=self.run_dir(run_id))
        if self.busy():
            raise ValueError("Wait for the active operation before reviewing a document.")
        directory = self.run_dir(run_id)
        with operation_lock(directory):
            if self.config(run_id).get("study_version") == 1:
                from .study import review_document
                return review_document(self.config(run_id), run_id, payload)
            record = next(
                (
                    r
                    for r in effective_extractions(directory)
                    if r["document_id"] == payload.get("document_id") and r.get("accepted")
                ),
                None,
            )
            if not record or payload.get("extraction_id") != extraction_identity(record):
                raise ValueError("The extraction changed. Refresh before approving this document.")
            obs = next(
                (
                    o
                    for o in self.observations(directory)
                    if o["document_id"] == record["document_id"] and o.get("body_complete")
                ),
                None,
            )
            if not obs:
                raise ValueError("The description is no longer eligible.")
            source = self.source(run_id, obs["observation_id"], 0)
            if not source["verified"] or source["source_sha256"] != record.get("source_sha256"):
                raise ValueError("Source changed. Extract again before approving.")
            decision = {
                "document_id": record["document_id"],
                "extraction_id": extraction_identity(record),
                "source_sha256": source["source_sha256"],
                "reviewed_at": utc_now(),
                "reviewer": "local-user",
            }
            append_jsonl(directory / "document-reviews.jsonl", [decision])
            return decision

    def import_rows(self, run_id: str, payload: dict) -> dict:
        import tempfile

        from .sources import import_postings

        if self.busy():
            raise ValueError("Wait for the active operation before importing.")
        with operation_lock(self.run_dir(run_id)):
            with tempfile.TemporaryDirectory(prefix="degent-import-") as temporary:
                path = Path(temporary) / "import.json"
                path.write_text(json.dumps(payload.get("postings")))
                return import_postings(self.config(run_id), run_id, path)


class Handler(BaseHTTPRequestHandler):
    server_version = "Degent/1"

    @property
    def app(self) -> Dashboard:
        return self.server.app

    def log_message(self, format, *args):
        pass

    def respond(self, status: int, payload, mime="application/json", filename=None):
        body = (
            json.dumps(payload, ensure_ascii=False).encode()
            if mime == "application/json"
            else payload
        )
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.valid_host():
            self.respond(403, {"error": "Loopback access only."})
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        run_id = query.get("run", self.app.run_list()[:1] or ["new-research"])[0]
        try:
            with self.app.lock:
                if url.path == "/api/phases":
                    from .phase_workspace import run_inventory
                    self.respond(200, {"runs": run_inventory(self.app.runs)})
                elif url.path == "/api/phase/state":
                    self.respond(200, self.app.phase_state(run_id))
                elif url.path == "/api/phase/document":
                    from .phase_workspace import document
                    self.respond(200, document(self.app.run_dir(run_id), query.get("id", [""])[0], query.get("mode", ["evidence"])[0]))
                elif url.path == "/api/phase/draft":
                    from .phase_workspace import load_draft
                    self.respond(200, load_draft(self.app.run_dir(run_id), query.get("kind", [""])[0], query.get("id", [""])[0]))
                elif url.path == "/api/phase/taxonomy-template":
                    from .phase_workspace import taxonomy_template
                    self.respond(200, taxonomy_template(self.app.run_dir(run_id)))
                elif url.path == "/api/state":
                    self.respond(200, self.app.state(run_id))
                elif url.path == "/api/companies":
                    self.respond(
                        200,
                        self.app.library.search(
                            query.get("search", [""])[0],
                            int(query.get("page", ["0"])[0]),
                            int(query.get("size", ["10"])[0]),
                        ),
                    )
                elif url.path == "/api/company-verifications":
                    self.respond(200, self.app.verification_state())
                elif url.path == "/api/company":
                    self.respond(200, self.app.library.get(query.get("id", [""])[0]))
                elif url.path == "/api/suggestions":
                    from .workflow import suggestions

                    self.respond(200, suggestions(self.app.base, query.get("selected", [])))
                elif url.path == "/api/document-review":
                    records = effective_extractions(self.app.run_dir(run_id))
                    self.respond(
                        200,
                        [
                            {
                                "document_id": r["document_id"],
                                "extraction_id": extraction_identity(r),
                                "accepted": r.get("accepted"),
                            }
                            for r in records
                        ],
                    )
                elif url.path == "/api/records":
                    self.respond(
                        200, self.app.records(run_id, query.get("kind", ["captures"])[0], query)
                    )
                elif url.path == "/api/source":
                    self.respond(
                        200,
                        self.app.source(
                            run_id,
                            query.get("id", [""])[0],
                            int(query.get("page", ["0"])[0]),
                            query.get("anchor", [""])[0],
                        ),
                    )
                elif url.path == "/api/export":
                    filename = query.get("file", [""])[0]
                    if filename not in EXPORTS:
                        raise ValueError("Unknown export.")
                    file = self.app.run_dir(run_id) / filename
                    self.respond(200, file.read_bytes(), "application/octet-stream", filename)
                elif url.path in {"/", "/index.html", "/app.js", "/styles.css", "/phase.html", "/phase.js", "/phase.css"}:
                    file = WEB / ("index.html" if url.path == "/" else url.path.lstrip("/"))
                    self.respond(
                        200, file.read_bytes(), mimetypes.guess_type(file)[0] or "text/plain"
                    )
                else:
                    self.respond(404, {"error": "Not found."})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception:
            self.respond(
                500,
                {
                    "error": "Could not read local evidence. Retry after the active operation finishes."
                },
            )

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if (
            not self.valid_host()
            or origin != expected
            or not secrets.compare_digest(self.headers.get("X-Degent-Token", ""), self.app.token)
        ):
            self.respond(403, {"error": "Invalid local session."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= (5_500_000 if self.path == "/api/import" or self.path.startswith("/api/phase/") else 16000):
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
            with self.app.lock:
                if self.path.startswith("/api/phase/"):
                    result = self.app.phase_action(payload, self.path.removeprefix("/api/phase/"))
                elif self.path == "/api/company-verifications":
                    result = self.app.start_verification(payload)
                elif self.path == "/api/company-verifications/stop":
                    result = self.app.stop_verification()
                elif self.path == "/api/companies":
                    result = self.app.library.add(
                        payload.get("name", ""),
                        payload.get("website", ""),
                        payload.get("career_url", ""),
                        payload.get("aliases"),
                        payload.get("group", "unknown"),
                    )
                elif self.path == "/api/company/verify":
                    if self.app.busy():
                        raise ValueError("Wait for the active operation before verifying sources.")
                    result = self.app.library.verify(payload["id"])
                elif self.path == "/api/collection/continue":
                    result = self.app.continue_collection(payload["run_id"], payload)
                elif self.path == "/api/runs":
                    result = self.app.create_run(payload)
                elif self.path == "/api/study":
                    result = self.app.study_action(payload["run_id"], payload)
                elif self.path == "/api/jobs":
                    result = self.app.start(
                        payload["run_id"], payload["operation"], payload.get("codex") is True
                    )
                elif self.path == "/api/stop":
                    result = self.app.stop()
                elif self.path == "/api/import":
                    result = self.app.import_rows(payload["run_id"], payload)
                elif self.path == "/api/document-review":
                    result = self.app.review_document(payload["run_id"], payload)
                elif self.path == "/api/review":
                    result = self.app.review(payload["run_id"], payload)
                else:
                    self.respond(404, {"error": "Not found."})
                    return
            self.respond(200, result)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception:
            self.respond(
                500,
                {
                    "error": "The local operation failed. Saved evidence was preserved; retry after checking the run."
                },
            )


def make_server(port=0, root=ROOT, config_path=DEFAULT_CONFIG):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = Dashboard(root, config_path)
    return server


def main():
    parser = argparse.ArgumentParser(description="Degent local research dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    server = make_server(args.port, config_path=args.config)

    def shutdown(*_):
        with server.app.lock:
            server.app.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
