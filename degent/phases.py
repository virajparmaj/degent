"""Offline preparation of immutable scoped calibration/evaluation assignments."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .evidence import select_units
from .model_runner import method_signature
from .openrouter import APPROVED_MODELS, configuration, model_identity
from .phase_matching import POLICY
from .phase_state import file_hash, fingerprint, immutable
from .scoped_selection import features
from .store import digest, read_json, read_jsonl, write_jsonl

CAPS = {
    "calibration": {
        "documents": 12,
        "calls": 16,
        "retry_calls": 4,
        "retries_per_document": 1,
        "input_tokens": 400000,
        "output_tokens": 65536,
        "output_per_call": 4096,
        "cost_usd": 1,
    },
    "evaluation": {
        "documents": 40,
        "calls": 44,
        "retry_calls": 4,
        "retries_per_document": 1,
        "input_tokens": 1000000,
        "output_tokens": 180224,
        "output_per_call": 4096,
        "cost_usd": 1,
    },
}
THRESHOLDS = {
    "statement_precision": 0.95,
    "statement_recall": 0.90,
    "strength_context_accuracy": 0.95,
    "selector_recall": 0.90,
    "explicit_attribute_accuracy": 0.95,
    "exact_evidence_support": 1.0,
}


def implementation() -> dict:
    root = Path(__file__).parent
    return {
        name: file_hash(root / name)
        for name in (
            "phases.py",
            "phase_state.py",
            "phase_ledger.py",
            "phase_labels.py",
            "phase_review.py",
            "phase_inference.py",
            "phase_evaluation.py",
            "phase_matching.py",
            "phase_revision.py",
            "phase_workspace.py",
            "study_evaluation.py",
            "taxonomy.py",
            "openrouter.py",
        )
    }


def _extended_held(rows: dict, packet: dict, supplement: dict, pilot: list[str]) -> tuple:
    calibration = [d["document_id"] for d in packet["documents"] if d["split"] == "calibration"]
    calibration += [
        d["document_id"] for d in supplement["documents"] if d["proposed_split"] == "calibration"
    ]
    originals = [
        d["document_id"]
        for d in packet["documents"]
        if d["split"] == "held_out" and rows[d["document_id"]]["decision"] == "include"
    ]
    held = originals + [
        d["document_id"] for d in supplement["documents"] if d["proposed_split"] == "held_out"
    ]
    protected = [rows[doc] for doc in set(calibration + pilot)]
    vacancies = {r["vacancy_id"] for r in protected}
    hashes = {r["source_sha256"] for r in protected}
    for doc in held:
        row = rows[doc]
        if row["vacancy_id"] in vacancies or row["source_sha256"] in hashes:
            raise ValueError("Duplicate text or vacancy crosses frozen assignments.")
        vacancies.add(row["vacancy_id"])
        hashes.add(row["source_sha256"])
    feature_sets = {
        doc: features(row, Path(row["clean_file"]).read_text())
        for doc, row in rows.items()
        if row["decision"] == "include"
    }
    while len(held) < 40:
        counts = Counter(f for doc in held for f in feature_sets[doc])
        available = [
            doc
            for doc in feature_sets
            if rows[doc].get("proposed_dispatch")
            and rows[doc]["vacancy_id"] not in vacancies
            and rows[doc]["source_sha256"] not in hashes
        ]
        if not available:
            raise ValueError("Insufficient untouched descriptions; no silent shrinking.")

        # Prioritize unrepresented cells, then scarce cells; hash order breaks every tie.
        def priority(doc: str) -> tuple:
            cells = feature_sets[doc]
            return (
                -sum(f not in counts for f in cells),
                -sum(1 / (1 + counts[f]) for f in sorted(cells)),
                digest(doc),
            )

        doc = min(available, key=priority)
        held.append(doc)
        vacancies.add(rows[doc]["vacancy_id"])
        hashes.add(rows[doc]["source_sha256"])
    if (len(pilot), len(calibration), len(originals), len(held)) != (12, 29, 19, 40):
        raise ValueError("Expected 12 pilot, 29 calibration and 19+21 evaluation descriptions.")
    if len({rows[d]["source_sha256"] for d in pilot}) != 12:
        raise ValueError("Pilot contains duplicate calibration text.")
    return calibration, originals, held


def prepare_phases(preparation: Path, output: Path) -> dict:
    preparation, output = preparation.resolve(), output.resolve()
    index = read_json(preparation / "scoped-selection-index-v1.json")
    selection = Path(
        index.get("current_selection")
        or index.get("selection_directory")
        or index.get("path")
        or ""
    )
    if not selection.is_absolute():
        selection = preparation / selection
    if not (selection / "manifest.json").exists():
        raise ValueError("Provide a preparation run with a frozen scoped-selection index.")
    parent = read_json(selection / "manifest.json")
    for name, expected in parent["files"].items():
        if file_hash(selection / name) != expected:
            raise ValueError("Phase 1 selection artifact changed.")
    packet = read_json(preparation / "gold-packet.json")
    if file_hash(preparation / "gold-packet.json") != parent["gold_sha256"]:
        raise ValueError("Original frozen 20/40 packet changed.")
    rows = {r["document_id"]: r for r in read_jsonl(selection / "selection.jsonl")}
    pilot = read_json(selection / "phase-plan.json")["phase2"]["pilot_document_ids"]
    calibration, originals, held = _extended_held(
        rows, packet, read_json(selection / "supplement-proposal.json"), pilot
    )
    for doc in pilot + held:
        if rows[doc]["decision"] != "include":
            raise ValueError("Excluded/review records cannot enter inference manifests.")
    inputs = {
        str(selection / "manifest.json"): file_hash(selection / "manifest.json"),
        str(preparation / "gold-packet.json"): parent["gold_sha256"],
        str(preparation / "observations.jsonl"): parent["original_observations_sha256"],
    }
    common = _common_manifest(parent, inputs, rows, calibration, selection)
    common["calibration_supplements"] = [
        {k: rows[d["document_id"]][k] for k in ("document_id", "source_sha256", "vacancy_id")}
        for d in read_json(selection / "supplement-proposal.json")["documents"]
        if d["proposed_split"] == "calibration"
    ]
    bundle = fingerprint({**common, "pilot": pilot, "held": held})[:16]
    paths = {phase: output / f"capital-one-scoped-{phase}-{bundle}" for phase in CAPS}
    _reject_exposed(output, paths, held, pilot, rows)
    observations = {r["document_id"]: r for r in read_jsonl(preparation / "observations.jsonl")}
    for phase, docs, review in (("calibration", pilot, calibration), ("evaluation", held, held)):
        _materialize(
            paths[phase], phase, docs, review, originals, rows, packet, common, paths, observations
        )
    return {
        "status": "prepared_without_authorization",
        "runs": {k: str(v) for k, v in paths.items()},
        "pilot": 12,
        "calibration_review": 29,
        "scoped_evaluation": 40,
        "original_scoped": 19,
        "supplement": 21,
        "additional_supplement": 13,
    }


def _common_manifest(
    parent: dict, inputs: dict, rows: dict, calibration: list, selection: Path
) -> dict:
    return {
        "version": 1,
        "selection_version": parent["version"],
        "selection_signature": parent["selection_signature"],
        "parent_inputs": inputs,
        "model_identity": model_identity(),
        "approved_models": list(APPROVED_MODELS),
        "model_signature": method_signature(model_identity()),
        "implementation": implementation(),
        "thresholds": THRESHOLDS,
        "matching_policy": POLICY,
        "selection_directory": str(selection),
        "calibration_reserved": [
            {k: rows[d][k] for k in ("document_id", "vacancy_id", "source_sha256")}
            for d in calibration
        ],
        "scope_review_cases": sum(r["decision"] == "review" for r in rows.values()),
        "original_packet_counts": {"calibration": 20, "held_out": 40},
    }


def _materialize(
    directory, phase, docs, review, originals, rows, packet, common, paths, observations
):
    if (directory / "phase-manifest.json").exists():
        from .phase_review import validate_inputs

        validate_inputs(directory)
        return
    directory.mkdir(parents=True, exist_ok=True)
    documents, frozen = _review_package(
        directory,
        review,
        docs,
        originals,
        rows,
        packet,
        observations,
        common["calibration_reserved"],
    )
    config = {
        "phase_only": True,
        "study_version": 1,
        "run_dir": str(directory.parent),
        "phase": phase,
        "inference": configuration(),
        "collection_enabled": False,
    }
    frozen["dashboard-config.json"] = immutable(directory / "dashboard-config.json", config)
    manifest = {
        **common,
        "phase": phase,
        "caps": CAPS[phase],
        "files": frozen,
        "document_ids": docs,
        "review_document_ids": review,
        "documents": [
            {k: rows[doc][k] for k in ("document_id", "vacancy_id", "source_sha256", "clean_file")}
            for doc in docs
        ],
        "calibration_run": str(paths["calibration"]),
        "evaluation_run": str(paths["evaluation"]),
        "status": "frozen_candidates_pending_human_approval",
    }
    manifest_hash = immutable(directory / "phase-manifest.json", manifest)
    _templates(directory, documents, manifest_hash)
    from .phase_review import reports

    reports(directory)


def _review_package(
    directory: Path,
    review: list,
    docs: list,
    originals: list,
    rows: dict,
    packet: dict,
    observations: dict,
    calibration_reserved: list,
) -> tuple:
    frozen = {}
    documents = []
    for doc in review:
        row = rows[doc]
        text = Path(row["clean_file"]).read_text()
        if digest(text) != row["source_sha256"]:
            raise ValueError("Preserved source changed.")
        selection = select_units(text)
        selection["document_id"] = doc
        entry = {
            **row,
            "source_observation": observations[doc],
            "full_text": text,
            "selection": selection,
            "inference_target": doc in docs,
            "cohort": (
                "original_scoped"
                if doc in originals
                else (
                    "original_calibration"
                    if any(
                        d["document_id"] == doc and d["split"] == "calibration"
                        for d in packet["documents"]
                    )
                    else "supplement"
                )
            ),
        }
        documents.append(entry)
    frozen["review-package.json"] = immutable(
        directory / "review-package.json",
        {
            "documents": documents,
            "original_packet": packet,
            "duplicate_policy": "R1001683 and R249262 remain separate requisitions. Only the proposed pilot representative receives a call. Human identity adjudication required; no automatic merge.",
            "instructions": "Label independent full text before output exposure. Review excluded selector units, omissions, duties, US geography, identity, seniority and completeness.",
        },
    )
    write_jsonl(
        directory / "observations.jsonl",
        [
            observations[d]
            for d in dict.fromkeys(review + [r["document_id"] for r in calibration_reserved])
        ],
    )
    frozen["observations.jsonl"] = file_hash(directory / "observations.jsonl")
    return documents, frozen


def _templates(directory: Path, documents: list[dict], manifest_hash: str) -> None:
    envelope = {
        "approved": False,
        "reviewer": None,
        "reviewed_at": None,
        "manifest_sha256": manifest_hash,
    }
    _document_templates(directory, documents, envelope)
    supplement_ids = {
        d["document_id"]
        for d in read_json(directory / "phase-manifest.json")["calibration_supplements"]
    }
    scope_template = read_json(directory / "scope-template.json")
    immutable(
        directory / "supplements-template.json",
        {
            **envelope,
            "kind": "supplements",
            "documents": [
                d for d in scope_template["documents"] if d["document_id"] in supplement_ids
            ],
        },
    )
    _identity_requests(directory, documents)
    for kind, fields in {
        "taxonomy": {"groups": {}, "skills": {}, "assignments": {}},
        "approval": {
            "taxonomy_sha256": None,
            "definitions_and_conventions_approved": False,
            "duplicate_calibration_text_review": False,
            "evaluation_assignments_approved": False,
            "identity_decisions": [],
        },
        "spending": {
            "execution_manifest_sha256": None,
            "spending_authorized": False,
            "caps": read_json(directory / "phase-manifest.json")["caps"],
            "pricing": {
                "verified_at": None,
                "source_url": None,
                "model": "openai/gpt-6-luna",
                "input_per_million": None,
                "output_per_million": None,
            },
        },
        "completion": {"findings_resolved": False, "limitations": [], "method_frozen": False},
        "calibration-revision": {
            "kind": "calibration_revision",
            "previous_result_sha256": None,
            "reason": None,
        },
        "recover-raw": {"call_id": None, "raw_sha256": None},
        "reconcile": {
            "call_id": None,
            "provider_receipt": {
                "generation_id": None,
                "model": "openai/gpt-6-luna",
                "source_url": None,
                "dispatch_terminal": False,
                "outcome": None,
            },
        },
        "limitations": {
            "coverage_limitations_accepted": False,
            "frozen_result_sha256": None,
            "notes": None,
        },
    }.items():
        immutable(directory / f"{kind}-template.json", {**envelope, "kind": kind, **fields})


def _document_templates(directory: Path, documents: list, envelope: dict) -> None:
    for kind in ("scope", "labels", "audit"):
        rows = []
        for doc in documents:
            row = {"document_id": doc["document_id"], "source_sha256": doc["source_sha256"]}
            if kind == "scope":
                row.update(
                    decision=None,
                    us_geography_confirmed=False,
                    identity_resolved=False,
                    description_complete=False,
                    selection_complete=False,
                    seniority_reviewed=False,
                    reviewed_level=None,
                    seniority_evidence=None,
                    seniority_unknown_reason=None,
                    reviewed_functions=None,
                    function_evidence={},
                    uncertainties_accepted=False,
                    duty_evidence=[],
                    geography_evidence=[],
                    notes=None,
                )
            elif kind == "labels":
                row.update(
                    labels=None,
                    independent_full_text=False,
                    omission_audited=False,
                    selector_omissions=[],
                    conventions_reviewed=False,
                )
            else:
                if not doc["inference_target"]:
                    continue
                row.update(
                    raw_sha256=None,
                    omissions_reviewed=False,
                    attribution_reviewed=False,
                    findings_resolved=False,
                    corrections=[],
                    notes=None,
                )
            rows.append(row)
        immutable(
            directory / f"{kind}-template.json", {**envelope, "kind": kind, "documents": rows}
        )


def _identity_requests(directory: Path, documents: list) -> None:
    duplicate_hashes = {}
    for doc in documents:
        duplicate_hashes.setdefault(doc["source_sha256"], []).append(doc)
    identity_requests = [
        {
            "source_sha256": key,
            "vacancy_ids": sorted(d["vacancy_id"] for d in group),
            "documents": [
                {k: d[k] for k in ("document_id", "vacancy_id", "source_sha256")} for d in group
            ],
            "decision": None,
            "rationale": None,
        }
        for key, group in duplicate_hashes.items()
        if len(group) > 1
    ]
    immutable(
        directory / "identity-review-requests.json",
        {
            "groups": identity_requests,
            "policy": "Retain distinct requisitions; choose one pilot representative. Never merge on identical text alone.",
        },
    )


def _reject_exposed(
    output: Path, paths: dict, held: list[str], pilot: list[str], rows: dict
) -> None:
    from .phase_state import calls

    held_hashes = {rows[d]["source_sha256"] for d in held}
    pilot_hashes = {rows[d]["source_sha256"] for d in pilot}
    pilot_exposed = False
    for prior in output.glob("capital-one-scoped-*-*"):
        if prior in paths.values() or not (prior / "phase-manifest.json").exists():
            continue
        manifest = read_json(prior / "phase-manifest.json")
        exposed = {c["reservation"]["document_id"] for c in calls(prior)}
        exposed_hashes = {
            d["source_sha256"] for d in manifest["documents"] if d["document_id"] in exposed
        }
        if exposed_hashes & held_hashes:
            raise ValueError(
                "Held-out evidence has already been exposed in another revision. Prepare explicitly approved fresh untouched evaluation assignments."
            )
        pilot_exposed |= bool(exposed_hashes & pilot_hashes)
    if pilot_exposed:
        raise ValueError(
            "Pilot exposure requires an explicit calibration revision preserving independent gold; ordinary preparation cannot reset it."
        )
