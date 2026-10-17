"""Bounded phase dispatch with durable reservations and no cross-run output cache."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .model_runner import canonical_validation, expand_wire, request_for
from .openrouter import OpenRouterRunner, payload_for
from .phase_labels import attribute_errors
from .phase_ledger import commit_reservation, terminal_faults, usage_errors
from .phase_review import package, preflight
from .phase_state import calls, file_hash, fingerprint, finite, immutable, latest, usage
from .store import operation_lock, read_json, utc_now
from .taxonomy import load_taxonomy


def completed_outputs(directory: Path) -> dict:
    """Only verified completions from this immutable phase may be reused."""
    result = {}
    execution = (
        read_json(directory / "execution-manifest.json")
        if (directory / "execution-manifest.json").exists()
        else {}
    )
    for call in calls(directory):
        reservation, completion = call["reservation"], call["completion"]
        if not completion or completion.get("reconciled_no_output"):
            continue
        if completion.get("reservation_sha256") != file_hash(
            call["directory"] / "reservation.json"
        ):
            raise ValueError("Completion lost its reservation provenance.")
        raw_path = call["directory"] / "raw.json"
        if completion.get("raw_sha256") != file_hash(raw_path):
            raise ValueError("Raw phase output changed.")
        if reservation["execution_manifest_sha256"] != file_hash(
            directory / "execution-manifest.json"
        ):
            raise ValueError("Mixed execution manifests in one phase.")
        doc = reservation["document_id"]
        if doc not in execution["document_ids"]:
            raise ValueError("Unapproved document in dispatch history.")
        raw = read_json(raw_path)
        fixed = package(directory)[doc]
        record, errors = expand_wire(
            raw["output"],
            doc,
            fixed["selection"],
            fixed["full_text"],
            load_taxonomy(directory)["skills"],
        )
        valid, validation_errors = canonical_validation(record, fixed["full_text"])
        if completion.get("valid") and (
            errors
            or validation_errors
            or not valid
            or raw["finish_reason"] not in {"stop", "completed"}
        ):
            raise ValueError("Accepted raw output is invalid or truncated.")
        if completion.get("valid"):
            result[doc] = {
                **record,
                "raw_sha256": completion["raw_sha256"],
                "call_id": reservation["call_id"],
                "execution_manifest_sha256": reservation["execution_manifest_sha256"],
            }
    return result


def _reserve(directory: Path, doc: str, request: dict) -> tuple[Path, dict]:
    authorization = latest(directory, "spending")
    caps, pricing = authorization["caps"], authorization["pricing"]
    used = usage(directory)
    history = [c for c in calls(directory) if c["reservation"]["document_id"] == doc]
    attempt = len(history) + 1
    if (
        attempt > 1 + caps["retries_per_document"]
        or attempt > 1
        and used["retry_calls"] >= caps["retry_calls"]
    ):
        raise ValueError("Retry allowance exhausted; no automatic extensions.")
    model = read_json(directory / "phase-manifest.json")["model_identity"]["model"]
    input_tokens = len(json.dumps(payload_for(request, model, {"pricing": pricing})).encode()) + 512
    output_tokens = caps["output_per_call"]
    cost = (
        input_tokens * pricing["input_per_million"] + output_tokens * pricing["output_per_million"]
    ) / 1e6
    increments = {
        "calls": 1,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
    }
    if used["pending"] or any(used[k] + v > caps[k] for k, v in increments.items()):
        raise ValueError("Cumulative allowance exhausted or dispatch unresolved.")
    if len(set(used["documents"]) | {doc}) > caps["documents"]:
        raise ValueError("Distinct-description allowance exhausted.")
    identity = uuid4().hex
    reservation = {
        "call_id": identity,
        "document_id": doc,
        "attempt": attempt,
        "request_sha256": fingerprint(request),
        "source_sha256": package(directory)[doc]["source_sha256"],
        "execution_manifest_sha256": file_hash(directory / "execution-manifest.json"),
        "authorization_sha256": fingerprint(authorization),
        "reserved_at": utc_now(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
    }
    path = commit_reservation(directory, reservation)
    return path, reservation


def _complete_call(
    directory: Path, path: Path, reservation: dict, response, recovered_raw=None
) -> bool:
    fixed = package(directory)[reservation["document_id"]]
    raw = {
        "output": response.output,
        "usage": response.usage,
        "finish_reason": response.finish_reason,
        "received_at": utc_now(),
    }
    raw_hash = immutable(path / "raw.json", recovered_raw or raw)
    record, errors = expand_wire(
        response.output,
        fixed["document_id"],
        fixed["selection"],
        fixed["full_text"],
        load_taxonomy(directory)["skills"],
    )
    valid, validation_errors = canonical_validation(record, fixed["full_text"])
    errors += validation_errors
    for row in record["requirements"]:
        if attribute_errors(row, row["context_evidence"]):
            errors.append({"reason": "unsupported_explicit_attribute"})
    if response.finish_reason not in {"stop", "completed"}:
        errors.append({"reason": "truncated_or_unresolved_finish"})
    expected = read_json(directory / "phase-manifest.json")["model_identity"]["model"]
    errors += usage_errors(reservation, response.usage, expected)
    accepted = bool(valid and not errors)
    immutable(
        path / "completion.json",
        {
            "reservation_sha256": file_hash(path / "reservation.json"),
            "raw_sha256": raw_hash,
            "valid": accepted,
            "errors": errors,
            "usage": response.usage,
            "completed_at": utc_now(),
        },
    )
    return accepted


def run_phase(directory: Path, runner=None, retry: bool = False) -> dict:
    with operation_lock(directory):
        check = preflight(directory)
        if check["status"] != "ready":
            return check
        manifest = read_json(directory / "phase-manifest.json")
        price = latest(directory, "spending")["pricing"]
        successful = completed_outputs(directory)
        if set(successful) == set(manifest["document_ids"]):
            return {
                "status": "outputs_complete_pending_audit",
                "documents": len(successful),
                "usage": usage(directory),
                "phase4_enabled": False,
            }
        # Credentials are only read after every review, provenance and spending gate.
        runner = runner or OpenRouterRunner(
            manifest["model_identity"]["model"], settings={"pricing": price}
        )
        if runner.identity != manifest["model_identity"]:
            raise ValueError("Runner identity must match the single frozen approved model.")
        for doc in manifest["document_ids"]:
            if doc in successful:
                continue
            check = preflight(directory)
            if check["status"] != "ready":
                return check
            previous = [c for c in calls(directory) if c["reservation"]["document_id"] == doc]
            if previous and not retry:
                return {
                    "status": "blocked",
                    "blockers": ["Invalid/truncated response requires explicit bounded --retry"],
                    "usage": usage(directory),
                }
            request = request_for(package(directory)[doc]["selection"])
            request["max_output_tokens"] = latest(directory, "spending")["caps"]["output_per_call"]
            stopped = _dispatch_one(directory, doc, request, runner)
            if stopped:
                return stopped
        return {
            "status": "outputs_complete_pending_audit",
            "documents": len(completed_outputs(directory)),
            "usage": usage(directory),
            "phase4_enabled": False,
        }


def reconcile(directory: Path, payload: dict) -> None:
    """Record a human-verified terminal no-output receipt; never refund its reservation."""
    from .phase_state import fingerprint

    matching = [
        c for c in calls(directory) if c["reservation"]["call_id"] == payload.get("call_id")
    ]
    if len(matching) != 1 or matching[0]["completion"]:
        raise ValueError(
            "Reconcile exactly one unresolved dispatch; no completed output replacement."
        )
    call = matching[0]
    receipt = payload.get("provider_receipt", {})
    model = read_json(directory / "phase-manifest.json")["model_identity"]["model"]
    if (
        not receipt.get("generation_id")
        or receipt.get("model") != model
        or receipt.get("outcome") != "terminal_no_output"
        or receipt.get("dispatch_terminal") is not True
        or not str(receipt.get("source_url", "")).startswith("https://openrouter.ai/")
        or (call["directory"] / "raw.json").exists()
    ):
        raise ValueError(
            "Provider evidence must confirm terminal no-output outcome. Received raw outputs require manual investigation and cannot be discarded."
        )
    immutable(
        call["directory"] / "reconciliation.json",
        {
            **payload,
            "reservation_sha256": file_hash(call["directory"] / "reservation.json"),
            "provider_receipt_sha256": fingerprint(receipt),
        },
    )


def recover_raw(directory: Path, payload: dict) -> None:
    """Finalize an existing exact durable response; never contact a provider."""
    from .model_runner import ModelResponse

    matching = [
        c for c in calls(directory) if c["reservation"]["call_id"] == payload.get("call_id")
    ]
    if len(matching) != 1 or matching[0]["completion"] or terminal_faults(directory):
        raise ValueError("Recovery requires one unresolved raw response and no terminal fault.")
    call = matching[0]
    check = preflight(directory, allow_pending=call["reservation"]["call_id"])
    if check["status"] != "ready":
        raise ValueError("Raw response recovery blocked: " + "; ".join(check["blockers"]))
    path = call["directory"]
    if payload.get("raw_sha256") != file_hash(path / "raw.json"):
        raise ValueError("Recovery must bind the exact preserved raw response hash.")
    raw = read_json(path / "raw.json")
    response = ModelResponse(raw["output"], raw["usage"], raw["finish_reason"])
    _complete_call(directory, path, call["reservation"], response, recovered_raw=raw)


def _dispatch_one(directory: Path, doc: str, request: dict, runner) -> dict | None:
    try:
        path, reservation = _reserve(directory, doc, request)
    except ValueError as exc:
        return {"status": "blocked", "blockers": [str(exc)], "usage": usage(directory)}
    check = preflight(directory, allow_pending=reservation["call_id"])
    if check["status"] != "ready":
        immutable(
            path / "cancelled.json",
            {
                "reservation_sha256": file_hash(path / "reservation.json"),
                "reason": "pre_dispatch_gate_failed",
                "blockers": check["blockers"],
            },
        )
        return check  # No provider invocation; original reservation is still retained.
    immutable(
        path / "dispatch.json",
        {
            "reservation_sha256": file_hash(path / "reservation.json"),
            "request_sha256": fingerprint(request),
        },
    )
    try:
        response = runner.extract(request)
    except Exception:
        return {
            "status": "blocked",
            "blockers": [
                "Dispatch outcome unknown; reservation retained. Manual provider reconciliation required."
            ],
            "usage": usage(directory),
        }
    if not _complete_call(directory, path, reservation, response):
        return {
            "status": "blocked",
            "blockers": ["Invalid evidence, usage or truncated output; dispatch stopped"],
            "usage": usage(directory),
        }
    return None
