"""Bounded basic-evidence dispatch, isolated from extraction phases and human gold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from .openrouter import ENDPOINT, NoRedirect, local_settings
from .phase_ledger import commit_reservation, reservations, usage_errors
from .phase_state import finite
from .store import digest, operation_lock, read_json, utc_now, write_json


def _immutable(path: Path, value: dict) -> None:
    if path.exists():
        if read_json(path) != value:
            raise ValueError("Immutable basic-evidence artifact changed.")
    else:
        write_json(path, value)


def _proposal(directory: Path) -> dict:
    for name, sha in read_json(directory / "receipt.json")["files_sha256"].items():
        if digest((directory / name).read_bytes()) != sha:
            raise ValueError("Frozen basic-evidence proposal changed.")
    manifest = read_json(directory / "manifest.json")
    for row in manifest["documents"]:
        if digest(Path(row["source_file"]).read_bytes()) != row["source_sha256"]:
            raise ValueError("Frozen basic-evidence source changed.")
    return manifest


def authorize(proposal: Path, execution: Path, approval: dict) -> None:
    """Record the user's explicit approval of an exact frozen numerical allowance."""
    manifest = _proposal(proposal)
    expected = {
        "manifest_sha256": digest((proposal / "manifest.json").read_bytes()),
        "caps": manifest["caps"],
        "model_identity": manifest["model_identity"],
    }
    if (
        any(approval.get(k) != v for k, v in expected.items())
        or approval.get("approved") is not True
    ):
        raise ValueError("Exact numerical allowance and model approval are required.")
    if (
        approval.get("reviewer_kind") != "human"
        or not approval.get("approval_source")
        or not approval.get("reviewer")
    ):
        raise ValueError(
            "Direct human approval provenance is required; never infer approval from a proposal."
        )
    with operation_lock(execution):
        _immutable(
            execution / "execution-manifest.json",
            {
                "proposal_directory": str(proposal.resolve()),
                **expected,
                "purpose": manifest["purpose"],
                "human_gold": False,
            },
        )
        _immutable(execution / "authorization.json", approval)


def usage(execution: Path) -> dict:
    result = {
        "calls": 0,
        "retry_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "pending": 0,
        "valid_outputs": 0,
        "invalid_outputs": 0,
        "descriptions": [],
    }
    for reservation in reservations(execution):
        path = execution / "calls" / reservation["call_id"]
        terminal = path / "completion.json"
        completion = read_json(terminal) if terminal.exists() else None
        if completion and completion["reservation_sha256"] != digest(
            (path / "reservation.json").read_bytes()
        ):
            raise ValueError("Completion reservation binding changed.")
        if (
            completion
            and (path / "raw.json").exists()
            and completion.get("raw_sha256") != digest((path / "raw.json").read_bytes())
        ):
            raise ValueError("Raw basic-evidence response changed.")
        result["calls"] += 1
        result["retry_calls"] += reservation["attempt"] > 1
        result["descriptions"].append(reservation["document_id"])
        result["pending"] += completion is None
        if completion:
            result["valid_outputs"] += completion["valid"]
            result["invalid_outputs"] += not completion["valid"]
        native = completion.get("usage", {}) if completion else {}
        for field in ["input_tokens", "output_tokens", "cost_usd"]:
            # Missing native usage is charged at its durable reservation, not zero.
            value = native.get(field)
            valid = finite(value) and (field == "cost_usd" or type(value) is int)
            result[field] += value if valid else reservation[field]
    result["descriptions"] = sorted(set(result["descriptions"]))
    return result


def validate_references(output: dict, text: str) -> tuple[list[dict], list[str]]:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    categories = {
        "duty",
        "required_qualification",
        "preferred_qualification",
        "mandatory_condition",
        "people_leadership",
        "technical_project_leadership",
        "company_marketing",
    }
    errors = []
    spans = []
    if (
        not isinstance(output, dict)
        or set(output) != {"references", "uncertainties"}
        or not isinstance(output["references"], list)
        or not isinstance(output["uncertainties"], list)
    ):
        return [], ["invalid_output_schema"]
    for ref in output["references"]:
        if not isinstance(ref, dict) or set(ref) != {"category", "first_line", "last_line", "note"}:
            errors.append("invalid_reference_schema")
            continue
        first, last = ref["first_line"], ref["last_line"]
        if (
            ref["category"] not in categories
            or type(first) is not int
            or type(last) is not int
            or not 1 <= first <= last <= len(lines)
            or not isinstance(ref["note"], str)
        ):
            errors.append("unsupported_source_reference")
            continue
        start, end = offsets[first - 1], offsets[last]
        spans.append({**ref, "start": start, "end": end, "text": text[start:end]})
    if any(not isinstance(item, str) for item in output["uncertainties"]):
        errors.append("invalid_uncertainty")
    return spans, errors


def public_transport(payload: dict, key: str) -> dict:
    request = Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response_limit")
        data = json.loads(raw)
        # Never serialize a credential echoed by an upstream diagnostic.
        return json.loads(json.dumps(data).replace(key, "[redacted]"))
    except HTTPError as exc:
        raise RuntimeError(f"Provider HTTP {exc.code}; no fallback or automatic retry.") from None
    except (URLError, OSError, ValueError):
        raise RuntimeError(
            "Provider transport/JSON failure; reservation remains charged."
        ) from None


def run(execution: Path, *, operation_limit: int = 25, transport=None) -> dict:
    with operation_lock(execution):
        execution_manifest = read_json(execution / "execution-manifest.json")
        proposal = Path(execution_manifest["proposal_directory"])
        manifest = _proposal(proposal)
        approval = read_json(execution / "authorization.json")
        if approval.get("approved") is not True or approval["manifest_sha256"] != digest(
            (proposal / "manifest.json").read_bytes()
        ):
            raise ValueError("Exact approved proposal binding is required.")
        if (
            approval["caps"] != manifest["caps"]
            or approval["model_identity"] != manifest["model_identity"]
        ):
            raise ValueError("Approval caps/model differ from the frozen proposal.")
        caps = manifest["caps"]
        used = usage(execution)
        if used["pending"]:
            return {"status": "blocked_pending_reservation", "usage": used}
        prior = reservations(execution)
        if any(
            (execution / "calls" / r["call_id"] / "completion.json").exists()
            and read_json(execution / "calls" / r["call_id"] / "completion.json").get(
                "terminal_fault"
            )
            for r in prior
        ):
            return {"status": "blocked_terminal_usage_fault", "usage": used}
        done = {r["document_id"] for r in prior}
        count = 0
        # Credentials are read only after provenance, source and authorization checks.
        key = local_settings().get("OPENROUTER_API_KEY", "") if transport is None else None
        if transport is None and not key:
            raise ValueError("Workspace OpenRouter credential is missing.")
        for document in manifest["documents"]:
            sha = document["source_sha256"]
            if sha in done:
                continue
            used = usage(execution)
            price = manifest["provider_rates"]
            input_tokens = document["reserved_input_tokens"]
            output_tokens = caps["output_per_call"]
            cost = (
                input_tokens * price["input_per_million"]
                + output_tokens * price["output_per_million"]
            ) / 1e6
            increments = {
                "calls": 1,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
            }
            if any(used[k] + v > caps[k] for k, v in increments.items()):
                return {"status": "blocked_cumulative_cap", "usage": used}
            reservation = {
                "call_id": uuid4().hex,
                "document_id": sha,
                "source_sha256": sha,
                "attempt": 1,
                "request_sha256": document["request_sha256"],
                "execution_manifest_sha256": digest(
                    (execution / "execution-manifest.json").read_bytes()
                ),
                "authorization_sha256": digest((execution / "authorization.json").read_bytes()),
                "reserved_at": utc_now(),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
            }
            path = commit_reservation(execution, reservation)
            write_json(
                path / "dispatch.json",
                {
                    "started_at": utc_now(),
                    "reservation_sha256": digest((path / "reservation.json").read_bytes()),
                },
            )
            payload = read_json(proposal / "requests" / (sha + ".json"))
            if (
                digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                != document["request_sha256"]
            ):
                raise ValueError("Request hash differs from approved manifest; dispatch blocked.")
            try:
                response = (transport or public_transport)(payload, key)
            except RuntimeError as exc:
                _immutable(
                    path / "completion.json",
                    {
                        "reservation_sha256": digest((path / "reservation.json").read_bytes()),
                        "valid": False,
                        "usage": {},
                        "error": str(exc),
                        "completed_at": utc_now(),
                    },
                )
                return {"status": "transport_failed_no_automatic_retry", "usage": usage(execution)}
            _immutable(path / "raw.json", response)
            native = response.get("usage", {})
            native = {
                "input_tokens": native.get("prompt_tokens"),
                "output_tokens": native.get("completion_tokens"),
                "cost_usd": native.get("cost"),
                "reported_model": response.get("model"),
                "generation_id": response.get("id"),
            }
            errors = usage_errors(reservation, native, manifest["model_identity"]["model"])
            if not native["reported_model"]:
                errors.append({"reason": "missing_provider_model_identity"})
            try:
                choice = response["choices"][0]
                output = json.loads(choice["message"]["content"])
                spans, reference_errors = validate_references(
                    output, Path(document["source_file"]).read_text()
                )
                errors.extend({"reason": e} for e in reference_errors)
                if choice["finish_reason"] != "stop":
                    errors.append({"reason": "truncated_output"})
            except (ValueError, KeyError, IndexError, TypeError):
                output = {}
                spans = []
                errors.append({"reason": "invalid_provider_output"})
            _immutable(
                path / "evidence.json",
                {
                    "source_sha256": sha,
                    "model": manifest["model_identity"]["model"],
                    "references": spans,
                    "uncertainties": output.get("uncertainties", []),
                    "exact_offset_validation_is_semantic_quality": False,
                    "human_gold": False,
                },
            )
            terminal = any(
                e["reason"]
                in {
                    "invalid_provider_usage",
                    "provider_exceeded_reservation",
                    "provider_model_identity_mismatch",
                    "missing_provider_model_identity",
                }
                for e in errors
            )
            _immutable(
                path / "completion.json",
                {
                    "reservation_sha256": digest((path / "reservation.json").read_bytes()),
                    "raw_sha256": digest((path / "raw.json").read_bytes()),
                    "valid": not errors,
                    "errors": errors,
                    "usage": native,
                    "terminal_fault": terminal,
                    "completed_at": utc_now(),
                },
            )
            count += 1
            write_json(execution / "usage.json", usage(execution))
            if terminal:
                return {"status": "blocked_terminal_usage_fault", "usage": usage(execution)}
            if count >= operation_limit:
                break
        result = {
            "status": "tranche_complete",
            "usage": usage(execution),
            "human_gold": False,
            "final_adjudication_complete": False,
        }
        write_json(execution / "usage.json", result["usage"])
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--operation-limit", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(run(args.execution, operation_limit=args.operation_limit)))


if __name__ == "__main__":
    main()
