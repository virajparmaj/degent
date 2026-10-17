"""Explicit offline revision after failure, using untouched preserved included evidence."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .phase_state import (
    calls,
    file_hash,
    fingerprint,
    human,
    immutable,
    reviewed_documents,
)
from .scoped_selection import features
from .store import operation_lock, read_json, read_jsonl


def _prior_failure(previous: Path, approval: dict) -> tuple[dict, dict]:
    manifest = read_json(previous / "phase-manifest.json")
    human(approval, file_hash(previous / "phase-manifest.json"))
    outcome = read_json(previous / "held-out-result.json")
    receipt = read_json(previous / "held-out-result-receipt.json")
    if (
        manifest["phase"] != "evaluation"
        or outcome["status"] != "failed"
        or receipt["result_sha256"] != file_hash(previous / "held-out-result.json")
        or approval.get("previous_result_sha256") != receipt["result_sha256"]
        or approval.get("kind") != "calibration_revision"
        or not approval.get("reason")
    ):
        raise ValueError(
            "A source-bound failed evaluation and explicit human calibration-revision decision required."
        )
    if receipt["input_binding"]["manifest_sha256"] != file_hash(previous / "phase-manifest.json"):
        raise ValueError("Prior failed manifest changed.")
    from .phase_ledger import call_artifacts

    bound = receipt["input_binding"]
    if (
        bound["execution_sha256"] != file_hash(previous / "execution-manifest.json")
        or bound["gold_sha256"] != fingerprint(reviewed_documents(previous, "labels"))
        or bound["call_artifacts"] != [call_artifacts(c) for c in calls(previous)]
    ):
        raise ValueError("Prior failed evaluation inputs or call artifacts changed.")
    for name, sha in {**manifest["parent_inputs"], **manifest["files"]}.items():
        target = Path(name) if Path(name).is_absolute() else previous / name
        if file_hash(target) != sha:
            raise ValueError("Prior failed evaluation evidence changed.")
    calibration = Path(manifest["calibration_run"])
    frozen = read_json(calibration / "method-freeze.json")
    gold = reviewed_documents(calibration, "labels")
    if frozen["calibration_labels_sha256"] != fingerprint(gold):
        raise ValueError("Prior independent calibration gold changed after method freeze.")
    return manifest, gold


def _protected_runs(output: Path) -> tuple[set, set]:
    vacancies, hashes = set(), set()
    # Protect assignments even if preparation stopped before either manifest was written.
    for plan in sorted((output / "phase-revision-requests").glob("*-assignments.json")):
        for doc in read_json(plan)["sources"]:
            vacancies.add(doc["vacancy_id"])
            hashes.add(doc["source_sha256"])
    for previous in sorted(output.glob("capital-one-scoped-*-*")):
        path = previous / "phase-manifest.json"
        if not path.exists():
            continue
        m = read_json(path)
        # Every held-out assignment is protected, not only the successfully scored records.
        docs = m["documents"] if m["phase"] == "evaluation" else m["calibration_reserved"]
        for doc in docs:
            vacancies.add(doc["vacancy_id"])
            hashes.add(doc["source_sha256"])
        for call in calls(previous):
            doc = next(
                d for d in m["documents"] if d["document_id"] == call["reservation"]["document_id"]
            )
            vacancies.add(doc["vacancy_id"])
            hashes.add(doc["source_sha256"])
    return vacancies, hashes


def _fresh_documents(rows: dict, output: Path, packet: dict) -> list[str]:
    vacancies, hashes = _protected_runs(output)
    for doc in packet["documents"]:
        vacancies.add(doc["vacancy_id"])
        hashes.add(doc["source_sha256"])
    eligible = {
        d: r
        for d, r in rows.items()
        if r.get("proposed_dispatch")
        and r["decision"] == "include"
        and r["vacancy_id"] not in vacancies
        and r["source_sha256"] not in hashes
    }
    cells = {d: features(r, Path(r["clean_file"]).read_text()) for d, r in eligible.items()}
    chosen, counts = [], Counter()
    while len(chosen) < 40:
        candidates = [
            d
            for d, r in eligible.items()
            if r["vacancy_id"] not in vacancies and r["source_sha256"] not in hashes
        ]
        if not candidates:
            raise ValueError(
                "Fewer than 40 untouched included descriptions; no replacement collection or shrinking."
            )
        doc = min(
            candidates,
            key=lambda d: (
                -sum(f not in counts for f in cells[d]),
                -sum(1 / (1 + counts[f]) for f in sorted(cells[d])),
                fingerprint(d),
            ),
        )
        chosen.append(doc)
        counts.update(cells[doc])
        vacancies.add(rows[doc]["vacancy_id"])
        hashes.add(rows[doc]["source_sha256"])
    return chosen


def prepare_revision(preparation: Path, previous: Path, output: Path, approval: dict) -> dict:
    """Serialize assignment selection and materialization across all revision requests."""
    with operation_lock(output.resolve() / "phase-revision-requests"):
        return _prepare_revision(preparation, previous, output, approval)


def _prepare_revision(preparation: Path, previous: Path, output: Path, approval: dict) -> dict:
    from .phases import _extended_held

    preparation, previous, output = preparation.resolve(), previous.resolve(), output.resolve()
    old, gold = _prior_failure(previous, approval)
    receipt_path = output / "phase-revision-requests" / (fingerprint(approval) + "-prepared.json")
    if receipt_path.exists():
        from .phase_review import validate_inputs

        saved = read_json(receipt_path)
        for phase, name in saved["runs"].items():
            path = Path(name)
            validate_inputs(path)
            if file_hash(path / "phase-manifest.json") != saved["manifest_hashes"][phase]:
                raise ValueError("Prepared revision manifest changed; no silent reassignment.")
        return saved["result"]
    selection = (
        preparation / read_json(preparation / "scoped-selection-index-v1.json")["current_selection"]
    )
    parent = read_json(selection / "manifest.json")
    if parent["selection_signature"] != old["selection_signature"]:
        raise ValueError(
            "Revision must retain the preserved scoped selection; no historical/scope expansion."
        )
    for name, expected in parent["files"].items():
        if file_hash(selection / name) != expected:
            raise ValueError("Preserved selection changed.")
    packet = read_json(preparation / "gold-packet.json")
    rows = {r["document_id"]: r for r in read_jsonl(selection / "selection.jsonl")}
    pilot = read_json(selection / "phase-plan.json")["phase2"]["pilot_document_ids"]
    supplement = read_json(selection / "supplement-proposal.json")
    calibration, _, _ = _extended_held(rows, packet, supplement, pilot)
    held = _revision_plan(output, approval, selection, rows, packet)
    result = _materialize_revision(
        preparation,
        previous,
        output,
        approval,
        old,
        gold,
        parent,
        selection,
        rows,
        packet,
        supplement,
        pilot,
        calibration,
        held,
    )
    immutable(
        receipt_path,
        {
            "runs": result["runs"],
            "result": result,
            "manifest_hashes": {
                p: file_hash(Path(name) / "phase-manifest.json")
                for p, name in result["runs"].items()
            },
        },
    )
    return result


def _materialize_revision(
    preparation,
    previous,
    output,
    approval,
    old,
    gold,
    parent,
    selection,
    rows,
    packet,
    supplement,
    pilot,
    calibration,
    held,
):
    from .phases import _common_manifest, _materialize

    inputs, gold_path, request = _revision_inputs(
        preparation, previous, output, approval, old, gold, parent, selection
    )
    common = _common_manifest(parent, inputs, rows, calibration, selection)
    common["calibration_supplements"] = [
        {k: rows[d["document_id"]][k] for k in ("document_id", "vacancy_id", "source_sha256")}
        for d in supplement["documents"]
        if d["proposed_split"] == "calibration"
    ]
    common["revision"] = {
        "previous_evaluation": str(previous),
        "previous_result_sha256": approval["previous_result_sha256"],
        "independent_gold_reference": str(gold_path),
        "human_request": str(request),
        "policy": "Fresh 40 supplements, no reuse of any held-out vacancy/hash; prior source gold retained for calibration.",
    }
    bundle = fingerprint({**common, "pilot": pilot, "held": held})[:16]
    paths = {p: output / f"capital-one-scoped-{p}-{bundle}" for p in ("calibration", "evaluation")}
    observations = {r["document_id"]: r for r in read_jsonl(preparation / "observations.jsonl")}
    for phase, docs, review in (("calibration", pilot, calibration), ("evaluation", held, held)):
        _materialize(
            paths[phase], phase, docs, review, [], rows, packet, common, paths, observations
        )
    return {
        "status": "revision_prepared_without_spending",
        "runs": {k: str(v) for k, v in paths.items()},
        "pilot": 12,
        "scoped_evaluation": 40,
        "original_scoped": 0,
        "fresh_supplement": 40,
        "previous_evaluation": str(previous),
        "human_approvals_copied": False,
    }


def validate_reference_gold(manifest: dict, document: dict) -> None:
    revision = manifest.get("revision")
    if not revision or manifest["phase"] != "calibration":
        return
    prior = read_json(Path(revision["independent_gold_reference"]))[document["document_id"]]
    # Keep the independent full-text truth; selector omissions can be reviewed for the new method.
    if document["source_sha256"] != prior["source_sha256"] or document["labels"] != prior["labels"]:
        raise ValueError(
            "Revised calibration must retain prior independent source gold, not relabel from exposed predictions. Gold/taxonomy remapping needs a separately reviewed migration."
        )


def _revision_plan(
    output: Path, approval: dict, selection: Path, rows: dict, packet: dict
) -> list[str]:
    from .phases import implementation

    path = output / "phase-revision-requests" / (fingerprint(approval) + "-assignments.json")
    expected = {
        "implementation": implementation(),
        "selection_manifest_sha256": file_hash(selection / "manifest.json"),
        "approval_sha256": fingerprint(approval),
    }
    if path.exists():
        saved = read_json(path)
        if any(saved.get(k) != v for k, v in expected.items()):
            raise ValueError("Frozen revision plan changed; new human revision decision required.")
        if saved["sources"] != [
            {k: rows[d][k] for k in ("document_id", "vacancy_id", "source_sha256")}
            for d in saved["document_ids"]
        ]:
            raise ValueError("Frozen revision candidate identities changed.")
        return saved["document_ids"]
    held = _fresh_documents(rows, output, packet)
    immutable(
        path,
        {
            **expected,
            "document_ids": held,
            "sources": [
                {k: rows[d][k] for k in ("document_id", "vacancy_id", "source_sha256")}
                for d in held
            ],
        },
    )
    return held


def _revision_inputs(
    preparation: Path,
    previous: Path,
    output: Path,
    approval: dict,
    old: dict,
    gold: dict,
    parent: dict,
    selection: Path,
) -> tuple:
    request = output / "phase-revision-requests" / (fingerprint(approval) + ".json")
    gold_path = output / "phase-revision-gold" / (fingerprint(gold) + ".json")
    inputs = {
        str(selection / "manifest.json"): file_hash(selection / "manifest.json"),
        str(preparation / "gold-packet.json"): parent["gold_sha256"],
        str(preparation / "observations.jsonl"): parent["original_observations_sha256"],
        str(previous / "phase-manifest.json"): file_hash(previous / "phase-manifest.json"),
        str(previous / "held-out-result.json"): file_hash(previous / "held-out-result.json"),
    }
    prior_cal = Path(old["calibration_run"])
    for path in (
        previous / "held-out-result-receipt.json",
        prior_cal / "phase-manifest.json",
        prior_cal / "method-freeze.json",
    ):
        inputs[str(path)] = file_hash(path)
    plan_path = output / "phase-revision-requests" / (fingerprint(approval) + "-assignments.json")
    inputs[str(plan_path)] = file_hash(plan_path)
    inputs[str(request)] = immutable(request, approval)
    inputs[str(gold_path)] = immutable(gold_path, gold)
    return inputs, gold_path, request
