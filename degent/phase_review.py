"""Human decisions and preflight gates; preparation never grants authority."""

from __future__ import annotations

import copy
import csv
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .model_runner import method_signature, normalization_signature, request_for
from .openrouter import payload_for
from .phase_labels import validate_gold
from .phase_state import (
    calls,
    decisions,
    file_hash,
    fingerprint,
    finite,
    human,
    immutable,
    latest,
    record_decision,
    reviewed_documents,
    usage,
)
from .store import digest, operation_lock, read_json, read_jsonl, utc_now, write_json
from .taxonomy import approve, load_taxonomy
from .vocabulary import SCOPES, STATUSES

ATTRIBUTES = (
    "qualifier",
    "experience_range",
    "experience_years",
    "alternatives",
    "alternative_logic",
)


def package(directory: Path) -> dict:
    return {d["document_id"]: d for d in read_json(directory / "review-package.json")["documents"]}


def validate_inputs(directory: Path) -> dict:
    from .phases import implementation

    manifest = read_json(directory / "phase-manifest.json")
    if not manifest or manifest["implementation"] != implementation():
        raise ValueError("Phase implementation changed; create a new calibration revision.")
    if manifest["model_signature"] != method_signature(manifest["model_identity"]):
        raise ValueError("Model, prompt or selector changed; new manifest required.")
    for name, expected in {**manifest["parent_inputs"], **manifest["files"]}.items():
        path = Path(name) if Path(name).is_absolute() else directory / name
        if file_hash(path) != expected:
            raise ValueError("Frozen source/selection/configuration changed: " + path.name)
    parent = read_json(Path(manifest["selection_directory"]) / "manifest.json")
    for name, expected in parent["files"].items():
        if file_hash(Path(manifest["selection_directory"]) / name) != expected:
            raise ValueError("Parent selection evidence changed: " + name)
    docs = package(directory)
    if set(docs) != set(manifest["review_document_ids"]):
        raise ValueError("Review package membership changed.")
    for doc in docs.values():
        if (
            digest(doc["full_text"]) != doc["source_sha256"]
            or file_hash(Path(doc["clean_file"])) != doc["source_sha256"]
        ):
            raise ValueError("Source description changed.")
    for doc in docs.values():
        observation = doc["source_observation"]
        for field, hash_field in (
            ("raw_file", "raw_sha256"),
            ("raw_source_file", "raw_source_sha256"),
        ):
            if observation.get(field) and observation.get(hash_field):
                if file_hash(Path(observation[field])) != observation[hash_field]:
                    raise ValueError("Raw source provenance changed.")
    _manifest_targets(manifest, docs, parent)
    target = [docs[d] for d in manifest["document_ids"]]
    if any(d["decision"] != "include" or not d["inference_target"] for d in target):
        raise ValueError("Only included frozen targets may be dispatched.")
    if len({d["vacancy_id"] for d in target}) != len(target) or len(
        {d["source_sha256"] for d in target}
    ) != len(target):
        raise ValueError("Duplicate target identity or text.")
    if manifest["phase"] == "evaluation":
        reserved = manifest["calibration_reserved"]
        if {d["vacancy_id"] for d in target} & {d["vacancy_id"] for d in reserved} or {
            d["source_sha256"] for d in target
        } & {d["source_sha256"] for d in reserved}:
            raise ValueError("Calibration/evaluation split leakage.")
    decisions(directory)  # Corrupt review history is never treated as absence of review.
    return manifest


def _span(span: dict, source: str) -> None:
    start, end = span.get("start"), span.get("end")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(source)
        or source[start:end] != span.get("text")
    ):
        raise ValueError("Review evidence must be an exact full-text span.")


def _scope(doc: dict, fixed: dict, target: bool) -> None:
    if doc.get("decision") not in {"include", "exclude", "review"}:
        raise ValueError("Explicit scope decision required.")
    if not target:
        return
    if doc["decision"] != "include":
        if not isinstance(doc.get("notes"), str) or not doc["notes"].strip():
            raise ValueError("Unresolved/excluded scope decisions require a human reason.")
        for span in doc.get("review_evidence", []):
            _span(span, fixed["full_text"])
        return  # Preserve dissent/uncertainty, then block approval in readiness.
    checks = (
        "us_geography_confirmed",
        "identity_resolved",
        "description_complete",
        "selection_complete",
        "seniority_reviewed",
        "uncertainties_accepted",
    )
    if doc["decision"] != "include" or any(doc.get(k) is not True for k in checks):
        raise ValueError(
            "Unresolved pilot/evaluation scope, identity or completeness blocks approval."
        )
    if doc.get("reviewed_level") not in {
        "entry",
        "mid",
        "senior",
        "management",
        "executive",
        "internship",
        "unknown",
    }:
        raise ValueError("Explicit reviewed seniority (including unknown) required.")
    _metadata_review(doc, fixed)
    for key in ("duty_evidence", "geography_evidence"):
        if not doc.get(key):
            raise ValueError("Exact duties and US geography evidence required.")
        for span in doc[key]:
            _span(span, fixed["full_text"])
        allowed = fixed[key]
        if not all(
            any(span["start"] >= ref["start"] and span["end"] <= ref["end"] for ref in allowed)
            for span in doc[key]
        ):
            raise ValueError(
                "Evidence must refer to preserved duty/geography spans, not boilerplate."
            )


def _labels(doc: dict, fixed: dict, dispatched: set[str]) -> None:
    if fixed["document_id"] in dispatched:
        raise ValueError(
            "Independent gold cannot be revised after dispatch; preserve corrections separately."
        )
    if any(
        doc.get(k) is not True
        for k in ("independent_full_text", "omission_audited", "conventions_reviewed")
    ):
        raise ValueError("Full-text independent labels and omission/convention review required.")
    if not isinstance(doc.get("labels"), list) or not isinstance(
        doc.get("selector_omissions"), list
    ):
        raise ValueError("Labels and selector omissions must be explicit lists.")
    for label in doc["labels"]:
        if (
            not isinstance(label, dict)
            or not isinstance(label.get("status"), str)
            or not isinstance(label.get("statement_scope"), str)
        ):
            raise ValueError("Gold labels must be objects with string status and context.")
        if label.get("concept") is not None and not isinstance(label["concept"], str):
            raise ValueError("Canonical concept IDs must be strings or null.")
        _span(
            {
                "start": label.get("evidence_start"),
                "end": label.get("evidence_end"),
                "text": label.get("evidence"),
            },
            fixed["full_text"],
        )
        if label.get("status") not in STATUSES or label.get("statement_scope") not in SCOPES:
            raise ValueError("Invalid strength/context convention.")
        if not (label.get("concept") or label.get("original_concept")) or not all(
            k in label for k in ATTRIBUTES
        ):
            raise ValueError(
                "Concept and explicit attributes (null/empty where unstated) required."
            )
        if label.get("negated") is not (label["status"] == "not_required"):
            raise ValueError("Negation must agree with not_required status.")


def execution_binding(directory: Path) -> dict:
    manifest = read_json(directory / "phase-manifest.json")
    return {
        "candidate_manifest_sha256": file_hash(directory / "phase-manifest.json"),
        "model_identity": manifest["model_identity"],
        "model_signature": manifest["model_signature"],
        "normalization_signature": normalization_signature(load_taxonomy(directory)["skills"]),
        "taxonomy_sha256": file_hash(directory / "taxonomy.json"),
        "scope_sha256": fingerprint(
            {d: reviewed_documents(directory, "scope").get(d) for d in manifest["document_ids"]}
        ),
        "labels_sha256": fingerprint(
            {d: reviewed_documents(directory, "labels").get(d) for d in label_targets(directory)}
        ),
        "supplements_sha256": fingerprint(reviewed_documents(directory, "supplements")),
        "document_ids": manifest["document_ids"],
        "selection_version": manifest["selection_version"],
        "documents": manifest["documents"],
    }


def _review_documents(directory: Path, payload: dict, manifest: dict) -> None:
    fixed = package(directory)
    dispatched = {c["reservation"]["document_id"] for c in calls(directory)}
    dispatched_hashes = {fixed[d]["source_sha256"] for d in dispatched}
    dispatched |= {d for d, row in fixed.items() if row["source_sha256"] in dispatched_hashes}
    seen = set()
    if not isinstance(payload.get("documents"), list):
        raise ValueError("Human review documents must be a list.")
    for doc in payload["documents"]:
        if not isinstance(doc, dict):
            raise ValueError("Each reviewed document must be an object.")
        identity = doc.get("document_id")
        if (
            identity in seen
            or identity not in fixed
            or doc.get("source_sha256") != fixed[identity]["source_sha256"]
        ):
            raise ValueError("Duplicate, unapproved or changed review source.")
        seen.add(identity)
        if payload["kind"] == "scope":
            _scope(doc, fixed[identity], identity in manifest["document_ids"])
        elif payload["kind"] == "labels":
            _labels(doc, fixed[identity], dispatched)
            validate_gold(doc["labels"], fixed[identity], load_taxonomy(directory)["skills"])
            from .phase_revision import validate_reference_gold

            validate_reference_gold(manifest, doc)
        elif payload["kind"] == "supplements":
            if identity not in {r["document_id"] for r in manifest["calibration_supplements"]}:
                raise ValueError(
                    "Only the nine frozen calibration supplements can receive assignment decisions."
                )
            _scope(doc, fixed[identity], True)
        else:
            from .phase_inference import completed_outputs

            raw = completed_outputs(directory).get(identity)
            if not raw or doc.get("raw_sha256") != raw["raw_sha256"]:
                raise ValueError("Audit must bind this phase's valid raw output.")
            if any(
                doc.get(k) is not True
                for k in ("omissions_reviewed", "attribution_reviewed", "findings_resolved")
            ):
                raise ValueError("Every output needs resolved omission and attribution audit.")
            if not isinstance(doc.get("corrections"), list):
                raise ValueError(
                    "Corrections remain separate from raw output and independent gold."
                )
    if not seen:
        raise ValueError("Empty human review import.")


def validate_review(directory: Path, payload: dict) -> dict:
    """Validate a review/export without creating a decision, taxonomy or execution manifest."""
    manifest = validate_inputs(directory)
    human(payload, file_hash(directory / "phase-manifest.json"))
    kind = payload.get("kind")
    if latest(directory, "completion"):
        raise ValueError("Completed calibration is frozen; use a new revision.")
    if kind in {"scope", "labels", "audit", "supplements"}:
        _review_documents(directory, payload, manifest)
    elif kind == "taxonomy":
        if calls(directory) or (directory / "execution-manifest.json").exists():
            raise ValueError("Frozen taxonomy changes require a new calibration revision.")
        _approve_taxonomy(directory, payload, manifest, persist=False)
    elif kind == "approval":
        _approve_execution(directory, payload, persist=False)
    else:
        raise ValueError(
            "Review interface supports evidence, taxonomy and manifest decisions only."
        )
    return manifest


def review(directory: Path, payload: dict, expected_history: str | None = None) -> dict:
    with operation_lock(directory):
        manifest = validate_inputs(directory)
        human(payload, file_hash(directory / "phase-manifest.json"))
        kind = payload.get("kind")
        if expected_history is not None:
            history = decisions(directory)
            if any(row["payload"] == payload for row in history):
                return {"status": "already_recorded", "decision_sha256": fingerprint(payload)}
            if fingerprint(history) != expected_history:
                raise ValueError("Another reviewer changed decisions; reload before submitting.")
        if latest(directory, "completion") and kind not in {"limitations"}:
            raise ValueError(
                "Completed calibration is frozen; use a new revision for further changes."
            )
        if kind in {"scope", "labels", "audit", "supplements"}:
            _review_documents(directory, payload, manifest)
        elif kind == "taxonomy":
            if calls(directory) or (directory / "execution-manifest.json").exists():
                raise ValueError("Frozen taxonomy changes require a new calibration revision.")
            _approve_taxonomy(directory, payload, manifest)
        elif kind == "approval":
            _approve_execution(directory, payload)
        elif kind == "spending":
            _authorize(directory, payload)
        elif kind == "completion":
            _complete(directory, payload)
        elif kind == "recover-raw":
            from .phase_inference import recover_raw

            recover_raw(directory, payload)
        elif kind == "reconcile":
            from .phase_inference import reconcile

            reconcile(directory, payload)
        elif kind == "limitations":
            from .phase_evaluation import evaluate_phase

            result = evaluate_phase(directory)
            if (
                result["status"] != "passed"
                or type(payload.get("coverage_limitations_accepted")) is not bool
                or payload.get("frozen_result_sha256") != result.get("frozen_result_sha256")
                or not payload.get("notes")
            ):
                raise ValueError(
                    "Passing scoped evaluation and explicit coverage-limit acceptance required."
                )
        else:
            raise ValueError("Unknown phase decision kind.")
        record_decision(directory, payload)
        return reports(directory)


def readiness(directory: Path) -> list[str]:
    manifest = read_json(directory / "phase-manifest.json")
    targets = set(manifest["document_ids"])
    blockers = []
    for kind in ("scope", "labels"):
        required = label_targets(directory) if kind == "labels" else targets
        missing = required - reviewed_documents(directory, kind).keys()
        if missing:
            blockers.append(
                f"{kind}: {len(missing)} target descriptions require independent human review"
            )
    tax = load_taxonomy(directory)
    docs = package(directory)
    blockers.extend(_scope_blockers(directory, manifest, docs))
    if not tax.get("version") or not latest(directory, "taxonomy"):
        blockers.append("Human-approved stable role/skill definitions required")
    for doc in targets:
        assignment = tax.get("assignments", {}).get(doc, {})
        if (
            assignment.get("source_sha256") != docs[doc]["source_sha256"]
            or assignment.get("scope_decision", "included") != "included"
            or assignment.get("role_group_id") not in tax.get("groups", {})
        ):
            blockers.append("Taxonomy role assignment missing or excluded: " + doc)
    if manifest["phase"] == "calibration":
        missing = {
            d["document_id"] for d in manifest["calibration_supplements"]
        } - reviewed_documents(directory, "supplements").keys()
        if missing:
            blockers.append(
                f"supplements: {len(missing)} source-bound assignment approvals required"
            )
    return blockers


def _approve_execution(directory: Path, payload: dict, persist: bool = True) -> None:
    blockers = readiness(directory)
    flags = (
        "definitions_and_conventions_approved",
        "duplicate_calibration_text_review",
        "evaluation_assignments_approved",
    )
    if blockers or any(payload.get(k) is not True for k in flags):
        raise ValueError(
            "Approval blocked: "
            + "; ".join(
                blockers
                or [
                    "Definitions, identity duplicate and frozen evaluation assignment reviews required"
                ]
            )
        )
    if payload.get("taxonomy_sha256") != file_hash(directory / "taxonomy.json"):
        raise ValueError("Approval must bind exact reviewed taxonomy.")
    manifest = read_json(directory / "phase-manifest.json")
    peer = Path(manifest["evaluation_run"])
    validate_inputs(peer)  # Evaluation membership must already be frozen before any pilot approval.
    reserved = manifest["calibration_reserved"]
    for group in _duplicate_groups(reserved):
        matching = [r for r in reserved if r["vacancy_id"] in group]
        decision = next(
            (
                d
                for d in payload.get("identity_decisions", [])
                if sorted(d.get("vacancy_ids", [])) == group
            ),
            {},
        )
        if (
            decision.get("source_sha256") != matching[0]["source_sha256"]
            or decision.get("decision") != "retain_distinct_requisitions_single_pilot_capture"
            or not decision.get("rationale")
        ):
            raise ValueError(
                "Exact duplicate calibration-text identity decision required; retain distinct requisitions and one pilot capture."
            )
    binding = execution_binding(directory)
    binding["evaluation_manifest_sha256"] = file_hash(peer / "phase-manifest.json")
    if persist:
        immutable(directory / "execution-manifest.json", binding)


def _authorize(directory: Path, payload: dict) -> None:
    if preflight(directory, require_spending=False)["status"] != "ready":
        raise ValueError(
            "Complete provenance, approval and prior-phase gates before authorizing spending."
        )
    if calls(directory) or latest(directory, "spending"):
        raise ValueError(
            "Allowance is frozen; consumption cannot be reset or extended in this phase."
        )
    if not latest(directory, "approval") or payload.get("spending_authorized") is not True:
        raise ValueError(
            "Separate pilot/evaluation approval and explicit spending authorization required."
        )
    if payload.get("execution_manifest_sha256") != file_hash(directory / "execution-manifest.json"):
        raise ValueError("Spending must bind the approved execution manifest.")
    manifest = read_json(directory / "phase-manifest.json")
    limits = payload.get("caps", {})
    if set(limits) != set(manifest["caps"]) or any(
        not finite(limits[k]) or not 0 < limits[k] <= ceiling
        for k, ceiling in manifest["caps"].items()
    ):
        raise ValueError("Limits must be positive and cannot exceed the frozen phase ceilings.")
    if any(type(limits[k]) is not int for k in limits if k != "cost_usd"):
        raise ValueError("Call and token caps must be integers.")
    price = payload.get("pricing", {})
    stamp = datetime.fromisoformat(price.get("verified_at") or "")
    now = datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.date() != now.date() or stamp > now:
        raise ValueError("Provider rates must be verified and dated on the authorization UTC date.")
    if price.get("model") != manifest["model_identity"]["model"] or not str(
        price.get("source_url", "")
    ).startswith("https://openrouter.ai/"):
        raise ValueError("Dated OpenRouter source and exact approved model required.")
    if any(not finite(price.get(k)) for k in ("input_per_million", "output_per_million")):
        raise ValueError("Finite conservative provider/context-tier rates required.")
    reservations = reservation_estimate(directory, price)
    if any(
        reservations[k] > limits[k]
        for k in ("calls", "input_tokens", "output_tokens", "cost_usd", "documents")
    ):
        raise ValueError("Conservative first pass plus four largest retries exceeds allowance.")


def reservation_estimate(directory: Path, pricing: dict | None = None) -> dict:
    manifest = read_json(directory / "phase-manifest.json")
    pricing = (
        pricing
        or latest(directory, "spending").get("pricing")
        or {"input_per_million": 0.2, "output_per_million": 0.75}
    )
    docs = package(directory)
    inputs = []
    for doc in manifest["document_ids"]:
        request = request_for(docs[doc]["selection"])
        inputs.append(
            len(
                json.dumps(
                    payload_for(request, manifest["model_identity"]["model"], {"pricing": pricing})
                ).encode()
            )
            + 512
        )
    retry = manifest["caps"]["retry_calls"]
    total_input = sum(inputs) + sum(sorted(inputs, reverse=True)[:retry])
    total_output = (len(inputs) + retry) * manifest["caps"]["output_per_call"]
    return {
        "documents": len(inputs),
        "calls": len(inputs) + retry,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": (
            total_input * pricing["input_per_million"]
            + total_output * pricing["output_per_million"]
        )
        / 1e6,
        "pricing_status": (
            "verified_by_human" if latest(directory, "spending") else "offline_reference_only"
        ),
    }


def _complete(directory: Path, payload: dict, freeze: bool = True) -> None:
    manifest = read_json(directory / "phase-manifest.json")
    if manifest["phase"] != "calibration":
        raise ValueError("Only calibration has a method-freeze completion action.")
    from .phase_inference import completed_outputs

    if set(completed_outputs(directory)) != set(manifest["document_ids"]):
        raise ValueError("All 12 pilot outputs must be valid.")
    if set(manifest["review_document_ids"]) - reviewed_documents(directory, "labels").keys():
        raise ValueError(
            "Independent labels for all original 20 and nine calibration supplements required."
        )
    if set(manifest["document_ids"]) - reviewed_documents(directory, "audit").keys():
        raise ValueError("Every pilot description must be audited.")
    if (
        usage(directory)["pending"]
        or any(payload.get(k) is not True for k in ("findings_resolved", "method_frozen"))
        or "limitations" not in payload
    ):
        raise ValueError("Unresolved dispatches/findings or unfrozen method block completion.")
    from .phase_ledger import call_artifacts, terminal_faults

    if terminal_faults(directory):
        raise ValueError("Terminal usage/model fault blocks calibration completion.")
    _approved_binding(directory)
    frozen = {
        "execution_manifest_sha256": file_hash(directory / "execution-manifest.json"),
        "calibration_labels_sha256": fingerprint(reviewed_documents(directory, "labels")),
        "audits_sha256": fingerprint(reviewed_documents(directory, "audit")),
        "completion_decision_sha256": fingerprint(payload),
        "usage": usage(directory),
        "call_artifacts": [call_artifacts(c) for c in calls(directory)],
    }
    if freeze:
        immutable(directory / "method-freeze.json", frozen)
    elif read_json(directory / "method-freeze.json") != frozen:
        raise ValueError("Frozen calibration method/usage/artifacts changed.")


def _approved_binding(directory: Path) -> None:
    fixed = read_json(directory / "execution-manifest.json")
    current = execution_binding(directory)
    if not fixed or any(fixed.get(k) != value for k, value in current.items()):
        raise ValueError("Source, method, taxonomy, gold or scope changed since approval.")
    evaluation = Path(read_json(directory / "phase-manifest.json")["evaluation_run"])
    validate_inputs(evaluation)
    if fixed["evaluation_manifest_sha256"] != file_hash(evaluation / "phase-manifest.json"):
        raise ValueError("Held-out candidates changed after approval.")


def preflight(
    directory: Path, require_spending: bool = True, allow_pending: str | None = None
) -> dict:
    blockers = []
    try:
        manifest = validate_inputs(directory)
        blockers.extend(readiness(directory))
        if not latest(directory, "approval"):
            blockers.append("Exact manifest approval required")
        else:
            _approved_binding(directory)
        if require_spending and not latest(directory, "spending"):
            blockers.append("Separate explicit spending approval and dated provider rates required")
        consumed = usage(directory)
        from .phase_ledger import terminal_faults

        blockers.extend(terminal_faults(directory))
        if set(consumed["pending"]) - ({allow_pending} if allow_pending else set()):
            blockers.append("Unresolved dispatch reservations block further calls")
        if manifest["phase"] == "evaluation":
            parent = Path(manifest["calibration_run"])
            validate_inputs(parent)
            if not latest(parent, "completion"):
                blockers.append("Phase 2 audited completion and frozen method required")
            else:
                _complete(parent, latest(parent, "completion"), freeze=False)
                if normalization_signature(
                    load_taxonomy(parent)["skills"]
                ) != normalization_signature(load_taxonomy(directory)["skills"]):
                    blockers.append("Evaluation must use the frozen calibration normalization")
    except (ValueError, KeyError, OSError) as exc:
        blockers.append(str(exc))
    return {
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "spending_enabled": bool(require_spending and not blockers),
        "phase4_enabled": False,
    }


def reports(directory: Path) -> dict:
    manifest = read_json(directory / "phase-manifest.json")
    docs = package(directory)
    target = [docs[d] for d in manifest["document_ids"]]
    from .scoped_selection import features

    covered = Counter(f for d in target for f in features(d, d["full_text"]))
    available = Counter(
        f
        for d in read_jsonl(Path(manifest["selection_directory"]) / "proposed-dispatch.jsonl")
        for f in features(d, Path(d["clean_file"]).read_text())
    )
    result = {
        "phase": manifest["phase"],
        "manifest_sha256": file_hash(directory / "phase-manifest.json"),
        "counts": {
            "inference_targets": len(target),
            "review_descriptions": len(docs),
            "cohorts": dict(Counter(d["cohort"] for d in target)),
            "scope_cases_outside_inference": manifest["scope_review_cases"],
        },
        "coverage": dict(sorted(covered.items())),
        "unrepresented_features": sorted(available.keys() - covered.keys()),
        "identity": {
            "target_distinct_vacancies": len({d["vacancy_id"] for d in target}),
            "target_distinct_hashes": len({d["source_sha256"] for d in target}),
            "calibration_duplicate_text_groups": _duplicate_groups(
                manifest["calibration_reserved"]
            ),
        },
        "usage": usage(directory),
        "reservation_estimate": reservation_estimate(directory),
        "preflight": preflight(directory),
        "rebuilt_at": utc_now(),
        "legacy_broad_40_score": "unavailable",
        "phase4_enabled": False,
    }
    write_json(directory / "phase-report.json", result)
    _coverage_reports(directory, result, available)
    return result


def _duplicate_groups(rows: list[dict]) -> list:
    groups = {}
    for row in rows:
        groups.setdefault(row["source_sha256"], []).append(row["vacancy_id"])
    return [sorted(v) for v in groups.values() if len(v) > 1]


def _coverage_reports(directory: Path, result: dict, available: Counter) -> None:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["feature", "included_selection", "phase_targets", "scope_review_status"])
    for feature in sorted(available.keys() | result["coverage"].keys()):
        writer.writerow(
            [
                feature,
                available[feature],
                result["coverage"].get(feature, 0),
                (
                    "requires_human_review"
                    if not latest(directory, "approval")
                    else "approved_frozen_manifest"
                ),
            ]
        )
    temporary = directory / "phase-coverage.csv.tmp"
    temporary.write_text(stream.getvalue())
    temporary.replace(directory / "phase-coverage.csv")
    write_json(directory / "phase-usage.json", result["usage"])
    try:
        validate_inputs(directory)
        preservation = {
            "status": "verified",
            "source_selection_and_legacy_packet_hashes": "unchanged",
        }
    except (ValueError, OSError, KeyError) as exc:
        preservation = {"status": "blocked", "reason": str(exc)}
    write_json(directory / "phase-preservation.json", preservation)


def _approve_taxonomy(directory: Path, payload: dict, manifest: dict, persist: bool = True) -> None:
    if set(payload.get("assignments", {})) - set(manifest["document_ids"]):
        raise ValueError("Taxonomy assignments must refer only to the approved phase targets.")
    docs = package(directory)
    for doc, assignment in payload.get("assignments", {}).items():
        evidence = assignment.get("evidence")
        if (
            not isinstance(evidence, str)
            or not evidence
            or not any(evidence in span["text"] for span in docs[doc]["duty_evidence"])
        ):
            raise ValueError(
                "Role assignment needs exact preserved direct duty evidence; titles and boilerplate cannot establish it."
            )
    if manifest["phase"] == "evaluation":
        parent = Path(manifest["calibration_run"])
        completion = latest(parent, "completion")
        if not completion:
            raise ValueError(
                "Phase 2 audited method freeze is required before evaluation taxonomy approval."
            )
        _complete(parent, completion, freeze=False)
        frozen = load_taxonomy(parent)
        if any(payload.get(kind) != frozen[kind] for kind in ("groups", "skills")):
            raise ValueError(
                "Evaluation must inherit exactly the frozen calibration role/skill definitions."
            )
    approve(directory, copy.deepcopy(payload), persist=persist)


def _manifest_targets(manifest: dict, docs: dict, parent: dict) -> None:
    from .openrouter import APPROVED_MODELS, model_identity
    from .phase_matching import POLICY
    from .phases import CAPS, THRESHOLDS

    phase = manifest["phase"]
    if (
        manifest["model_identity"] != model_identity()
        or manifest["approved_models"] != list(APPROVED_MODELS)
        or manifest["caps"] != CAPS[phase]
        or manifest["thresholds"] != THRESHOLDS
        or manifest.get("matching_policy") != POLICY
        or manifest["selection_version"] != parent["version"]
        or manifest["selection_signature"] != parent["selection_signature"]
    ):
        raise ValueError("Frozen phase policy, model, thresholds or selection identity changed.")
    identities = manifest["document_ids"]
    if len(identities) != CAPS[phase]["documents"] or len(set(identities)) != len(identities):
        raise ValueError(
            "Exactly 12 pilot or 40 held-out targets required; no shrinking or expansion."
        )
    expected = [
        {k: docs[d][k] for k in ("document_id", "vacancy_id", "source_sha256", "clean_file")}
        for d in identities
    ]
    if manifest["documents"] != expected:
        raise ValueError("Manifest document/source identity does not match frozen evidence.")


def label_targets(directory: Path) -> set[str]:
    """Identical calibration text must be labeled before either requisition is exposed."""
    manifest = read_json(directory / "phase-manifest.json")
    docs = package(directory)
    hashes = {docs[d]["source_sha256"] for d in manifest["document_ids"]}
    return {d for d, row in docs.items() if row["source_sha256"] in hashes}


def _metadata_review(doc: dict, fixed: dict) -> None:
    import re

    level = doc["reviewed_level"]
    if level == "unknown":
        if not doc.get("seniority_unknown_reason"):
            raise ValueError("Unknown seniority needs an explicit accepted uncertainty reason.")
    else:
        span = doc.get("seniority_evidence", {})
        _span(span, fixed["full_text"])
        patterns = {
            "entry": r"entry|junior|associate|analyst I\b",
            "mid": r"\bII\b|mid|intermediate",
            "senior": r"senior|lead|principal|staff",
            "management": r"manager|director|head|vice president",
            "executive": r"chief|executive|president",
            "internship": r"intern",
        }
        if not re.search(patterns[level], span["text"], re.I):
            raise ValueError(
                "Seniority evidence does not support reviewed level; retain unknown if unresolved."
            )
    functions = doc.get("reviewed_functions")
    if functions != fixed["functions"]:
        raise ValueError(
            "Reviewed functions must match frozen scoped assignment; changed scope requires a new revision."
        )
    for function in functions:
        evidence = doc.get("function_evidence", {}).get(function, [])
        if not evidence:
            raise ValueError("Every reviewed function requires exact direct duty evidence.")
        for span in evidence:
            _span(span, fixed["full_text"])
            if not any(
                span["start"] >= ref["start"]
                and span["end"] <= ref["end"]
                and ref.get("kind", function) == function
                for ref in fixed["duty_evidence"]
            ):
                raise ValueError("Function evidence must refer to preserved direct domain duties.")


def _scope_blockers(directory: Path, manifest: dict, docs: dict) -> list[str]:
    blockers = []
    scopes = reviewed_documents(directory, "scope")
    for doc in manifest["document_ids"]:
        row = scopes.get(doc)
        if row and row.get("decision") != "include":
            blockers.append("Human scope is excluded/unresolved: " + doc)
        elif row:
            _scope(row, docs[doc], True)
    if manifest["phase"] == "calibration":
        supplements = reviewed_documents(directory, "supplements")
        for expected in manifest["calibration_supplements"]:
            row = supplements.get(expected["document_id"])
            if row and row.get("decision") != "include":
                blockers.append(
                    "Calibration supplement assignment unresolved: " + expected["document_id"]
                )
            elif row:
                _scope(row, docs[expected["document_id"]], True)
    return blockers
