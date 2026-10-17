"""Fail-closed storage shared by isolated, human-authorized study phases."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from .store import digest, read_json


def fingerprint(value: object) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))


def immutable(path: Path, value: object) -> str:
    """Create once, flush data and directory; an interrupted write cannot become approval."""
    raw = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"Frozen artifact differs: {path.name}; prepare a new revision.")
        return digest(raw)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest(raw)


def file_hash(path: Path) -> str:
    return digest(path.read_bytes())


def finite(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def human(payload: dict, manifest_hash: str) -> None:
    if (
        not isinstance(payload, dict)
        or payload.get("approved") is not True
        or not isinstance(payload.get("reviewer"), str)
        or not payload["reviewer"].strip()
    ):
        raise ValueError("Explicit human approval and named reviewer required.")
    if payload.get("manifest_sha256") != manifest_hash:
        raise ValueError("Human decision must bind the exact candidate manifest.")
    if not isinstance(payload.get("reviewed_at"), str):
        raise ValueError("Review timestamp must be an ISO string with a timezone.")
    stamp = datetime.fromisoformat(payload["reviewed_at"])
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError("Review timestamp must have a timezone and cannot be in the future.")


def decisions(directory: Path) -> list[dict]:
    paths = sorted((directory / "decisions").glob("*.json"))
    result, previous = [], None
    for number, path in enumerate(paths):
        row = read_json(path)
        if (
            row.get("sequence") != number
            or row.get("previous_sha256") != previous
            or path.name != f"{number:06d}-{fingerprint(row)[:16]}.json"
        ):
            raise ValueError("Decision history is incomplete or changed.")
        result.append(row)
        previous = file_hash(path)
    return result


def record_decision(directory: Path, payload: dict) -> dict:
    previous = decisions(directory)
    paths = sorted((directory / "decisions").glob("*.json"))
    row = {
        "sequence": len(previous),
        "previous_sha256": file_hash(paths[-1]) if paths else None,
        "payload": payload,
    }
    immutable(directory / "decisions" / f"{len(previous):06d}-{fingerprint(row)[:16]}.json", row)
    return row


def latest(directory: Path, kind: str) -> dict:
    matches = [row["payload"] for row in decisions(directory) if row["payload"]["kind"] == kind]
    return matches[-1] if matches else {}


def reviewed_documents(directory: Path, kind: str) -> dict:
    result = {}
    for row in decisions(directory):
        payload = row["payload"]
        if payload["kind"] == kind:
            result.update({doc["document_id"]: doc for doc in payload.get("documents", [])})
    return result


def calls(directory: Path) -> list[dict]:
    from .phase_ledger import reservations

    result = []
    for indexed in reservations(directory):
        path = directory / "calls" / indexed["call_id"]
        reservation = read_json(path / "reservation.json")
        if reservation != indexed:
            raise ValueError("Reservation differs from durable dispatch inventory.")
        if not reservation or reservation.get("call_id") != path.name:
            raise ValueError("Incomplete durable reservation; dispatch remains blocked.")
        dispatch_path = path / "dispatch.json"
        if dispatch_path.exists():
            dispatch = read_json(dispatch_path)
            if dispatch != {
                "reservation_sha256": file_hash(path / "reservation.json"),
                "request_sha256": reservation["request_sha256"],
            }:
                raise ValueError("Dispatch intent/provenance changed.")
        elif (path / "raw.json").exists() or (path / "completion.json").exists():
            raise ValueError("Received output lacks durable dispatch intent.")
        completed = (
            read_json(path / "completion.json") if (path / "completion.json").exists() else {}
        )
        if completed:
            if (
                completed.get("reservation_sha256") != file_hash(path / "reservation.json")
                or completed.get("raw_sha256") != file_hash(path / "raw.json")
                or completed.get("usage") != read_json(path / "raw.json").get("usage")
            ):
                raise ValueError("Completion/usage provenance changed; dispatch blocked.")
        completed = _other_terminal(directory, path, completed, dispatch_path)
        result.append({"reservation": reservation, "completion": completed, "directory": path})
    return result


def usage(directory: Path) -> dict:
    totals = {
        "calls": 0,
        "retry_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "pending": [],
        "unknown_usage": [],
        "documents": [],
    }
    documents = set()
    for call in calls(directory):
        reservation, completed = call["reservation"], call["completion"]
        totals["calls"] += 1
        totals["retry_calls"] += reservation["attempt"] > 1
        documents.add(reservation["document_id"])
        unknown = not completed or any(
            not finite(completed.get("usage", {}).get(key))
            for key in ("input_tokens", "output_tokens", "cost_usd")
        )
        for key in ("input_tokens", "output_tokens", "cost_usd"):
            reported = completed.get("usage", {}).get(key)
            totals[key] += (
                max(reservation[key], reported if finite(reported) else 0) if unknown else reported
            )
        if not completed:
            totals["pending"].append(reservation["call_id"])
        elif unknown:
            totals["unknown_usage"].append(reservation["call_id"])
    totals["documents"] = sorted(documents)
    return totals


def reject_broad(config: dict, run_id: str | None = None, directory: Path | None = None) -> None:
    from .store import safe_id

    target = directory or Path(config.get("run_dir", ".")) / safe_id(run_id or "")
    if config.get("phase_only") or (target / "phase-manifest.json").exists():
        raise ValueError(
            "Isolated phase run: collection, broad extraction and method changes are forbidden."
        )


def _other_terminal(directory: Path, path: Path, completed: dict, dispatch_path: Path) -> dict:
    if (path / "reconciliation.json").exists():
        reconciled = read_json(path / "reconciliation.json")
        human(reconciled, file_hash(directory / "phase-manifest.json"))
        if completed or reconciled.get("reservation_sha256") != file_hash(
            path / "reservation.json"
        ):
            raise ValueError("Reconciliation provenance changed.")
        receipt = reconciled.get("provider_receipt", {})
        if (
            fingerprint(receipt) != reconciled.get("provider_receipt_sha256")
            or receipt.get("outcome") != "terminal_no_output"
        ):
            raise ValueError("Invalid provider reconciliation evidence.")
        completed = {"valid": False, "reconciled_no_output": True, "usage": {}}
    if (path / "cancelled.json").exists():
        cancelled = read_json(path / "cancelled.json")
        if (
            completed
            or dispatch_path.exists()
            or cancelled.get("reservation_sha256") != file_hash(path / "reservation.json")
        ):
            raise ValueError("Pre-dispatch cancellation provenance changed.")
        completed = {
            "valid": False,
            "reconciled_no_output": True,
            "cancelled": True,
            "usage": {},
        }
    return completed
