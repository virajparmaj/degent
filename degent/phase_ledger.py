"""Ordered, durable dispatch inventory and terminal fault checks."""

from __future__ import annotations

import os
from pathlib import Path

from .phase_state import file_hash, fingerprint, immutable
from .store import read_json

USAGE_FIELDS = ("input_tokens", "output_tokens", "cost_usd")
TERMINAL_REASONS = {
    "invalid_provider_usage",
    "provider_exceeded_reservation",
    "provider_model_identity_mismatch",
}


def _head(directory: Path, value: dict) -> None:
    path = directory / "call-ledger-head.json"
    temporary = directory / "call-ledger-head.json.tmp"
    # The head only advances after a complete immutable entry exists.
    if temporary.exists():
        temporary.unlink()
    immutable(temporary, value)
    temporary.replace(path)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reservations(directory: Path) -> list[dict]:
    paths = sorted((directory / "call-ledger").glob("*.json"))
    head_path = directory / "call-ledger-head.json"
    head = read_json(head_path) if head_path.exists() else {"count": 0, "sha256": None}
    previous, result = None, []
    for number, path in enumerate(paths):
        entry = read_json(path)
        if (
            entry.get("sequence") != number
            or entry.get("previous_sha256") != previous
            or path.name != f"{number:06d}-{fingerprint(entry)[:16]}.json"
        ):
            raise ValueError(
                "Dispatch inventory corrupt or incomplete; restore exact preserved ledger artifacts."
            )
        result.append(entry["reservation"])
        previous = file_hash(path)
    if head != {"count": len(result), "sha256": previous}:
        raise ValueError(
            "Dispatch inventory/head mismatch; manual storage recovery required before resume."
        )
    actual = (
        {p.name for p in (directory / "calls").iterdir()}
        if (directory / "calls").exists()
        else set()
    )
    if actual != {r["call_id"] for r in result}:
        raise ValueError(
            "Dispatch artifact missing or unindexed; restore exact reservation, never reset usage."
        )
    return result


def commit_reservation(directory: Path, reservation: dict) -> Path:
    previous = reservations(directory)
    paths = sorted((directory / "call-ledger").glob("*.json"))
    entry = {
        "sequence": len(previous),
        "previous_sha256": file_hash(paths[-1]) if paths else None,
        "reservation": reservation,
    }
    entry_hash = immutable(
        directory / "call-ledger" / f"{len(previous):06d}-{fingerprint(entry)[:16]}.json", entry
    )
    _head(directory, {"count": len(previous) + 1, "sha256": entry_hash})
    path = directory / "calls" / reservation["call_id"]
    immutable(path / "reservation.json", reservation)
    return path


def call_artifacts(call: dict) -> dict:
    path = call["directory"]
    terminal = (
        "cancelled.json"
        if call["completion"].get("cancelled")
        else (
            "reconciliation.json"
            if call["completion"].get("reconciled_no_output")
            else "completion.json"
        )
    )
    return {
        "reservation": file_hash(path / "reservation.json"),
        "dispatch_intent_sha256": (
            file_hash(path / "dispatch.json") if (path / "dispatch.json").exists() else None
        ),
        "terminal_artifact": terminal,
        "terminal_sha256": file_hash(path / terminal),
    }


def usage_errors(reservation: dict, native: dict, model: str) -> list[dict]:
    from .phase_state import finite

    errors = []
    for key in USAGE_FIELDS:
        value = native.get(key)
        if value is not None and (
            not finite(value) or key.endswith("tokens") and type(value) is not int
        ):
            errors.append({"reason": "invalid_provider_usage", "field": key})
        elif finite(value) and value > reservation[key]:
            errors.append({"reason": "provider_exceeded_reservation", "field": key})
    if native.get("reported_model") and native["reported_model"] != model:
        errors.append({"reason": "provider_model_identity_mismatch"})
    return errors


def terminal_faults(directory: Path) -> list[str]:
    from .phase_state import calls, latest, usage

    manifest = read_json(directory / "phase-manifest.json")
    faults = []
    for call in calls(directory):
        completion = call["completion"]
        errors = usage_errors(
            call["reservation"], completion.get("usage", {}), manifest["model_identity"]["model"]
        )
        errors += completion.get("errors", [])
        if any(e.get("reason") in TERMINAL_REASONS for e in errors):
            faults.append("Terminal provider/usage fault: " + call["reservation"]["call_id"])
    limits = latest(directory, "spending").get("caps")
    if limits:
        used = usage(directory)
        for key in (*USAGE_FIELDS, "calls", "retry_calls"):
            if used[key] > limits[key]:
                faults.append("Cumulative allowance exceeded: " + key)
        if len(used["documents"]) > limits["documents"]:
            faults.append("Cumulative description allowance exceeded")
    return faults
