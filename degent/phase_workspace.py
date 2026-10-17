"""Offline phase review, durable draft history and explicit decision submission."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .phase_review import (
    label_targets,
    package,
    preflight,
    review,
    validate_inputs,
    validate_review,
)
from .phase_state import (
    calls,
    decisions,
    file_hash,
    fingerprint,
    immutable,
    latest,
    reviewed_documents,
    usage,
)
from .store import operation_lock, read_json, utc_now
from .taxonomy import load_taxonomy
from .vocabulary import vocabulary

DOCUMENT_KINDS = {"scope", "labels", "supplements", "audit"}
REVIEW_KINDS = DOCUMENT_KINDS | {"taxonomy", "approval"}


def run_inventory(runs: Path) -> list[dict]:
    result = []
    for directory in sorted(runs.iterdir(), key=lambda p: p.name):
        if directory.is_symlink() or not (directory / "phase-manifest.json").is_file():
            continue
        try:
            manifest = read_json(directory / "phase-manifest.json")
            validate_inputs(directory)
            result.append({"run_id": directory.name, "phase": manifest["phase"], "current": True})
        except (ValueError, KeyError, OSError) as exc:
            result.append({"run_id": directory.name, "current": False, "reason": str(exc)})
    return sorted(result, key=lambda r: (not r["current"], r["run_id"]))


def _exposed(directory: Path) -> set[str]:
    docs = package(directory)
    return {docs[c["reservation"]["document_id"]]["source_sha256"] for c in calls(directory)}


def history_hash(directory: Path) -> str:
    return fingerprint(decisions(directory))


def state(directory: Path) -> dict:
    manifest = validate_inputs(directory)
    docs = package(directory)
    gold, scopes = reviewed_documents(directory, "labels"), reviewed_documents(directory, "scope")
    supplement_reviews = reviewed_documents(directory, "supplements")
    audits = reviewed_documents(directory, "audit")
    assignments = load_taxonomy(directory)["assignments"]
    early = label_targets(directory)
    exposed = _exposed(directory)
    supplements = {d["document_id"] for d in manifest["calibration_supplements"]}
    queues = {
        "pilot": manifest["document_ids"],
        "pre_exposure_gold": sorted(early),
        "remaining_calibration": sorted(set(docs) - early),
        "supplements": sorted(supplements),
    }
    if manifest["phase"] == "evaluation":
        queues = {"held_out": manifest["document_ids"]}
    return {
        "run_id": directory.name,
        "phase": manifest["phase"],
        "token": None,
        "binding": _binding(directory),
        "history_sha256": history_hash(directory),
        "model": manifest["model_identity"],
        "caps": manifest["caps"],
        "usage": usage(directory),
        "candidate_frozen": True,
        "execution_approved": bool(latest(directory, "approval")),
        "scope_review_cases": manifest["scope_review_cases"],
        "spending_authorized": bool(latest(directory, "spending")),
        "dispatch_available": False,
        "preflight": preflight(directory),
        "queues": queues,
        "taxonomy": load_taxonomy(directory),
        "concepts": {**vocabulary(), **load_taxonomy(directory)["skills"]},
        "identity_requests": read_json(directory / "identity-review-requests.json"),
        "documents": [
            {
                "document_id": d,
                "title": r.get("title", d),
                "vacancy_id": r["vacancy_id"],
                "cohort": r["cohort"],
                "inference_target": r["inference_target"],
                "gold_recorded": d in gold,
                "scope_decision": scopes.get(d, {}).get("decision"),
                "supplement_decision": supplement_reviews.get(d, {}).get("decision"),
                "audit_recorded": d in audits,
                "role_assigned": d in assignments,
                "gold_locked": r["source_sha256"] in exposed,
                "functions": r["functions"],
                "level_cue": r["level"],
            }
            for d, r in docs.items()
        ],
    }


def document(directory: Path, identity: str, mode: str = "evidence") -> dict:
    validate_inputs(directory)
    fixed = package(directory).get(identity)
    if not fixed:
        raise ValueError("Document is outside the frozen review package.")
    if mode not in {"evidence", "audit"}:
        raise ValueError("Choose independent evidence or the separate output audit.")
    observation = fixed["source_observation"]
    result = {
        k: copy.deepcopy(fixed[k])
        for k in (
            "document_id",
            "vacancy_id",
            "source_sha256",
            "full_text",
            "selection",
            "functions",
            "level",
            "duty_evidence",
            "geography_evidence",
            "cohort",
            "inference_target",
        )
    }
    result["provenance"] = {
        k: observation.get(k)
        for k in (
            "title",
            "original_url",
            "source_url",
            "retrieved_at",
            "capture_at",
            "publication_date",
            "body_complete",
        )
    }
    result["identical_text_documents"] = [
        d for d, r in package(directory).items() if r["source_sha256"] == fixed["source_sha256"]
    ]
    if mode == "audit":
        result["model_output"] = audit_output(directory, identity)
    return result


def audit_output(directory: Path, identity: str) -> dict:
    from .phase_inference import completed_outputs

    docs, labels = package(directory), reviewed_documents(directory, "labels")
    fixed = docs[identity]
    same = {d for d, row in docs.items() if row["source_sha256"] == fixed["source_sha256"]}
    if same - labels.keys() or fixed["source_sha256"] not in _exposed(directory):
        raise ValueError(
            "Independent gold for every identical-text record must be locked before audit."
        )
    output = completed_outputs(directory).get(identity)
    if not output:
        raise ValueError("No valid output from this phase run is available to audit.")
    return output


def _binding(directory: Path, identity: str = "") -> dict:
    manifest = read_json(directory / "phase-manifest.json")
    result = {
        "manifest_sha256": file_hash(directory / "phase-manifest.json"),
        "selection_signature": manifest["selection_signature"],
        "selection_version": manifest["selection_version"],
        "model_signature": manifest["model_signature"],
    }
    if identity:
        result["source_sha256"] = package(directory)[identity]["source_sha256"]
    return result


def _target(directory: Path, kind: str, identity: str) -> Path:
    if kind not in REVIEW_KINDS or (kind in DOCUMENT_KINDS) != bool(identity):
        raise ValueError("Choose a supported review kind and matching document/global target.")
    docs = package(directory)
    if identity and identity not in docs:
        raise ValueError("Draft document is outside the frozen review package.")
    if kind == "supplements" and identity not in {
        d["document_id"]
        for d in read_json(directory / "phase-manifest.json")["calibration_supplements"]
    }:
        raise ValueError("Only frozen calibration supplements may receive assignment decisions.")
    if kind == "audit":
        audit_output(directory, identity)
    return directory / "review-drafts" / fingerprint([kind, identity])


def _draft_history(target: Path) -> tuple[list, dict]:
    rows, previous = [], None
    for n, path in enumerate(sorted((target / "history").glob("*.json")), 1):
        row = read_json(path)
        if (
            row["revision"] != n
            or row["previous_sha256"] != previous
            or path.name != f"{n:06d}-{fingerprint(row)[:16]}.json"
        ):
            raise ValueError(
                "Draft history is incomplete or changed; restore preserved draft artifacts."
            )
        rows.append(row)
        previous = file_hash(path)
    head_path = target / "head.json"
    head = read_json(head_path) if head_path.exists() else {"revision": 0, "sha256": None}
    if head != {"revision": len(rows), "sha256": previous}:
        raise ValueError(
            "Interrupted draft save; recover the complete draft history before editing."
        )
    return rows, head


def _template(directory: Path, kind: str, identity: str) -> dict:
    value = read_json(directory / f"{kind}-template.json")
    if identity:
        row = next((d for d in value["documents"] if d["document_id"] == identity), None)
        if row is None:
            raise ValueError("No frozen template for this document/kind.")
        recorded = reviewed_documents(directory, kind).get(identity)
        value["documents"] = [copy.deepcopy(recorded or row)]
        manifest = read_json(directory / "phase-manifest.json")
        reference = manifest.get("revision", {}).get("independent_gold_reference")
        if kind == "labels" and manifest["phase"] == "calibration" and reference and not recorded:
            prior = copy.deepcopy(read_json(Path(reference))[identity])
            if prior["source_sha256"] != package(directory)[identity]["source_sha256"]:
                raise ValueError("Prior independent gold source changed; no automatic migration.")
            prior.update(
                independent_full_text=False,
                omission_audited=False,
                conventions_reviewed=False,
                selector_omissions=[],
            )
            value["documents"] = [prior]
            value["independent_gold_reference_sha256"] = file_hash(Path(reference))
    if kind == "taxonomy":
        tax = load_taxonomy(directory)
        manifest = read_json(directory / "phase-manifest.json")
        if manifest["phase"] == "evaluation":
            parent = Path(manifest["calibration_run"])
            if latest(parent, "completion"):
                validate_inputs(parent)
                frozen = load_taxonomy(parent)
                tax = {**tax, "groups": frozen["groups"], "skills": frozen["skills"]}
                value["definitions_inherited"] = True
        value.update({k: copy.deepcopy(tax[k]) for k in ("groups", "skills", "assignments")})
    if kind == "audit":
        value["documents"][0]["raw_sha256"] = audit_output(directory, identity)["raw_sha256"]
    if kind == "approval":
        taxonomy = directory / "taxonomy.json"
        value["taxonomy_sha256"] = file_hash(taxonomy) if taxonomy.exists() else None
        value["identity_decisions"] = [
            copy.deepcopy(g)
            for g in read_json(directory / "identity-review-requests.json")["groups"]
        ]
    value.update(_binding(directory))
    return value


def taxonomy_template(directory: Path) -> dict:
    """Refresh an unapproved proposal with the exact final calibration definitions."""
    validate_inputs(directory)
    return _template(directory, "taxonomy", "")


def load_draft(directory: Path, kind: str, identity: str = "") -> dict:
    validate_inputs(directory)
    target = _target(directory, kind, identity)
    rows, head = _draft_history(target)
    binding = _binding(directory, identity)
    payload = copy.deepcopy(rows[-1]["payload"]) if rows else _template(directory, kind, identity)
    stale = bool(rows and rows[-1]["binding"] != binding)
    locked = bool(latest(directory, "completion")) or (
        kind == "labels" and binding.get("source_sha256") in _exposed(directory)
    )
    return {
        "payload": payload,
        "revision": head["revision"],
        "binding": binding,
        "stale": stale,
        "read_only": stale or locked,
    }


def _check_payload(directory: Path, payload: dict, kind: str, identity: str) -> None:
    if payload.get("kind") != kind:
        raise ValueError("Draft review kind changed.")
    expected = _binding(directory)
    if any(payload.get(k) != v for k, v in expected.items()):
        raise ValueError("Draft source, selection, manifest or method binding changed; reload.")
    if identity:
        rows = payload.get("documents")
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("document_id") != identity:
            raise ValueError("Save one source-bound document per durable draft.")
        if rows[0].get("source_sha256") != package(directory)[identity]["source_sha256"]:
            raise ValueError("Draft source hash changed.")


def _durable_head(target: Path, value: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / "head.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target / "head.json")
    descriptor = os.open(target, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _save(directory: Path, payload: dict, kind: str, identity: str, revision: int) -> dict:
    current = load_draft(directory, kind, identity)
    if current["read_only"]:
        raise ValueError(
            "Gold is locked or draft is stale; preserve it and use the reviewed revision path."
        )
    if type(revision) is not int or revision != current["revision"]:
        raise ValueError("Another window saved this draft; reload before editing.")
    if payload.get("approved") is not False:
        raise ValueError("Drafts must explicitly remain unapproved.")
    _check_payload(directory, payload, kind, identity)
    target = _target(directory, kind, identity)
    _, head = _draft_history(target)
    row = {
        "revision": revision + 1,
        "previous_sha256": head["sha256"],
        "binding": current["binding"],
        "payload": payload,
        "saved_at": utc_now(),
    }
    sha = immutable(target / "history" / f"{revision+1:06d}-{fingerprint(row)[:16]}.json", row)
    _durable_head(target, {"revision": revision + 1, "sha256": sha})
    return {"status": "draft_saved", "revision": revision + 1, "approved": False}


def save_draft(directory: Path, payload: dict, kind: str, identity: str, revision: int) -> dict:
    with operation_lock(directory):
        validate_inputs(directory)
        return _save(directory, payload, kind, identity, revision)


def recover_draft(directory: Path, kind: str, identity: str = "") -> dict:
    """Advance an interrupted head only from a complete, consecutive immutable history."""
    with operation_lock(directory):
        validate_inputs(directory)
        target = _target(directory, kind, identity)
        paths = sorted((target / "history").glob("*.json"))
        previous = None
        for n, path in enumerate(paths, 1):
            row = read_json(path)
            if (
                row["revision"] != n
                or row["previous_sha256"] != previous
                or path.name != f"{n:06d}-{fingerprint(row)[:16]}.json"
            ):
                raise ValueError("Incomplete draft history; restore exact preserved artifacts.")
            previous = file_hash(path)
        head_path = target / "head.json"
        if head_path.exists():
            head = read_json(head_path)
            if head["revision"] > len(paths):
                raise ValueError("Missing committed draft history; restore preserved artifacts.")
            if head["revision"] and file_hash(paths[head["revision"] - 1]) != head["sha256"]:
                raise ValueError("Draft head changed; restore preserved artifacts.")
        _durable_head(target, {"revision": len(paths), "sha256": previous})
        return load_draft(directory, kind, identity)


def validate_export(directory: Path, payload: dict) -> dict:
    if payload.get("kind") not in REVIEW_KINDS:
        raise ValueError(
            "This review interface cannot authorize spending, inference or collection."
        )
    _check_payload(directory, payload, payload["kind"], "")
    validate_review(directory, copy.deepcopy(payload))
    return {"status": "valid_review", "decision_sha256": fingerprint(payload), "decision": payload}


def submit(directory: Path, payload: dict, expected_history: str) -> dict:
    if payload.get("kind") not in REVIEW_KINDS or not isinstance(expected_history, str):
        raise ValueError("Explicit evidence-review decision and current history binding required.")
    _check_payload(directory, payload, payload["kind"], "")
    # review() validates again under its lock and checks optimistic concurrency/idempotence.
    return review(directory, copy.deepcopy(payload), expected_history=expected_history)


def import_drafts(directory: Path, original: dict, revisions: dict) -> dict:
    """Preserve the imported payload, then stage every explicitly selected document as unapproved."""
    with operation_lock(directory):
        validate_inputs(directory)
        kind = original.get("kind")
        if kind not in REVIEW_KINDS:
            raise ValueError("Import evidence-review payloads only; no spending or inference.")
        binding = _binding(directory)
        if original.get("manifest_sha256") != binding["manifest_sha256"] or any(
            k in original and original[k] != v for k, v in binding.items()
        ):
            raise ValueError(
                "Imported source/selection/method binding changed; no silent rebinding."
            )
        values = original.get("documents", []) if kind in DOCUMENT_KINDS else [None]
        if not values:
            raise ValueError("Import must include at least one reviewed document.")
        staged = []
        for doc in values:
            identity = doc["document_id"] if doc else ""
            payload = copy.deepcopy(original)
            if doc:
                payload["documents"] = [doc]
            payload.update(_binding(directory), approved=False)
            current = load_draft(directory, kind, identity)
            _check_payload(directory, payload, kind, identity)
            if current["read_only"] or revisions.get(identity) != current["revision"]:
                raise ValueError(
                    "Stale, locked or concurrently changed import; no drafts imported."
                )
            staged.append((payload, identity))
        if len({d for _, d in staged}) != len(staged):
            raise ValueError("Duplicate import document; no drafts imported.")
        immutable(directory / "review-imports" / (fingerprint(original) + ".json"), original)
        saved = [_save(directory, p, kind, d, revisions[d]) for p, d in staged]
        return {
            "status": "imported_as_unapproved_drafts",
            "documents": len(saved),
            "approved": False,
        }
