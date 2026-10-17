"""Frozen scoped outcomes with separate live provenance and limitation acceptance."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .phase_inference import completed_outputs
from .phase_matching import POLICY, align, concept
from .phase_review import ATTRIBUTES, package, preflight
from .phase_state import file_hash, fingerprint, immutable, latest, reviewed_documents, usage
from .store import read_json, utc_now, write_json
from .study_evaluation import _interval, _scores


def _attribute_tallies(labels: list, predicted: list, matched: dict, assessable: bool) -> dict:
    tally = {k: {"assessed": 0, "correct": 0, "unassessed": 0, "not_stated": 0} for k in ATTRIBUTES}
    pairs = [(predicted[p], labels[g]) for p, g in matched["pairs"]]
    pairs += [(None, labels[g]) for g in matched["unmatched_gold"]]
    pairs += [(predicted[p], None) for p in matched["unmatched_predicted"]]
    for actual, expected in pairs:
        for key in ATTRIBUTES:
            a, e = (actual or {}).get(key), (expected or {}).get(key)
            if not assessable or (expected is not None and key not in expected):
                tally[key]["unassessed"] += 1
                continue
            if a in (None, [], "") and e in (None, [], ""):
                tally[key]["not_stated"] += 1
                continue
            if key == "alternatives":
                a, e = sorted(v.casefold() for v in a or []), sorted(v.casefold() for v in e or [])
            tally[key]["assessed"] += 1
            tally[key]["correct"] += actual is not None and expected is not None and a == e
    return tally


def _statement(row: dict) -> dict:
    return {
        "statement_sha256": fingerprint(row),
        "concept": concept(row),
        "start": row["evidence_start"],
        "end": row["evidence_end"],
        "evidence": row["evidence"],
        "status": row["status"],
        "statement_scope": row["statement_scope"],
        "attributes": {k: row.get(k) for k in ATTRIBUTES},
    }


def _duplicates(rows: list[dict]) -> list[list[int]]:
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[(concept(row), row["evidence_start"], row["evidence_end"])].append(i)
    return [v for _, v in sorted(groups.items()) if len(v) > 1]


def _tally(fixed: dict, labels: list, predicted: list, assessable: bool = True) -> dict:
    matched = align(predicted, labels)
    support = sum(
        fixed["full_text"][r["evidence_start"] : r["evidence_end"]] == r["evidence"]
        for r in predicted
    )
    captured = [
        i
        for i, g in enumerate(labels)
        if any(
            u["selected"] and u["start"] <= g["evidence_start"] and u["end"] >= g["evidence_end"]
            for u in fixed["selection"]["units"]
        )
    ]
    diagnostics = {
        "matched_pairs": [
            {"prediction": _statement(predicted[p]), "gold": _statement(labels[g]), "iou": score}
            for (p, g), score in zip(matched["pairs"], matched["pair_iou"])
        ],
        "unmatched_predictions": [_statement(predicted[p]) for p in matched["unmatched_predicted"]],
        "omitted_gold": [_statement(labels[g]) for g in matched["unmatched_gold"]],
        "selector_omissions": [_statement(g) for i, g in enumerate(labels) if i not in captured],
        "duplicate_predictions": [
            [_statement(predicted[p]) for p in group] for group in _duplicates(predicted)
        ],
        "ambiguous_predictions": [_statement(predicted[p]) for p in matched["ambiguous_predicted"]],
        "ambiguous_gold": [_statement(labels[g]) for g in matched["ambiguous_gold"]],
    }
    tp = len(matched["pairs"])
    return {
        "tp": tp,
        "fp": len(predicted) - tp,
        "fn": len(labels) - tp,
        "strength": sum(
            predicted[p]["status"] == labels[g]["status"]
            and predicted[p]["statement_scope"] == labels[g]["statement_scope"]
            for p, g in matched["pairs"]
        ),
        "captured": len(captured),
        "expected": len(labels),
        "predicted": len(predicted),
        "support": support,
        "attributes": _attribute_tallies(labels, predicted, matched, assessable),
        "documents": 1,
        "assessed_documents": int(assessable),
        "diagnostics": diagnostics,
    }


def _aggregate(rows: list[dict]) -> dict:
    totals = {
        k: sum(r[k] for r in rows)
        for k in (
            "tp",
            "fp",
            "fn",
            "strength",
            "captured",
            "expected",
            "predicted",
            "support",
            "documents",
            "assessed_documents",
        )
    }
    attrs = {}
    for key in ATTRIBUTES:
        t = {
            k: sum(r["attributes"][key][k] for r in rows)
            for k in ("assessed", "correct", "unassessed", "not_stated")
        }
        attrs[key] = {
            **t,
            "incorrect": t["assessed"] - t["correct"],
            "accuracy": t["correct"] / t["assessed"] if t["assessed"] else None,
            "interval_95": _interval(t["correct"], t["assessed"]),
        }
    return {
        "documents": totals["documents"],
        "assessed_documents": totals["assessed_documents"],
        "unassessed_documents": totals["documents"] - totals["assessed_documents"],
        "statement": _scores(totals["tp"], totals["fp"], totals["fn"]),
        "expected_statements": totals["expected"],
        "predicted_statements": totals["predicted"],
        "strength_context_accuracy": totals["strength"] / totals["tp"] if totals["tp"] else None,
        "strength_context_assessed": totals["tp"],
        "strength_context_correct": totals["strength"],
        "selector_recall": totals["captured"] / totals["expected"] if totals["expected"] else None,
        "selector_captured": totals["captured"],
        "exact_evidence_support": (
            totals["support"] / totals["predicted"] if totals["predicted"] else None
        ),
        "evidence_assessed": totals["predicted"],
        "evidence_supported": totals["support"],
        "explicit_attributes": attrs,
    }


def _gates(metrics: dict) -> bool:
    return (
        not metrics["unassessed_documents"]
        and (metrics["statement"]["precision"] or 0) >= 0.95
        and (metrics["statement"]["recall"] or 0) >= 0.90
        and (metrics["strength_context_accuracy"] or 0) >= 0.95
        and (metrics["selector_recall"] or 0) >= 0.90
        and metrics["exact_evidence_support"] == 1
        and all(
            not row["unassessed"] and (not row["assessed"] or row["accuracy"] >= 0.95)
            for row in metrics["explicit_attributes"].values()
        )
    )


def _input_binding(directory: Path) -> dict:
    from .phase_ledger import call_artifacts
    from .phase_state import calls

    return {
        "manifest_sha256": file_hash(directory / "phase-manifest.json"),
        "execution_sha256": file_hash(directory / "execution-manifest.json"),
        "gold_sha256": fingerprint(reviewed_documents(directory, "labels")),
        "call_artifacts": [call_artifacts(c) for c in calls(directory)],
        "matching_policy_sha256": fingerprint(POLICY),
    }


def _live_view(directory: Path, frozen: dict, blockers: list[str]) -> dict:
    terminal = directory / "held-out-result.json"
    receipt = read_json(directory / "held-out-result-receipt.json")
    if receipt["result_sha256"] != file_hash(terminal) or receipt[
        "input_binding"
    ] != _input_binding(directory):
        blockers.append("Frozen evaluation outcome or bound inputs changed")
    accepted = latest(directory, "limitations")
    acceptance_valid = (
        accepted.get("frozen_result_sha256") == receipt["result_sha256"]
        and accepted.get("coverage_limitations_accepted") is True
        and bool(accepted.get("notes"))
    )
    return {
        **frozen,
        "status": "blocked" if blockers else frozen["status"],
        "frozen_outcome": frozen["status"],
        "frozen_result_sha256": receipt["result_sha256"],
        "blockers": blockers,
        "coverage_limitations_accepted": bool(not blockers and acceptance_valid),
        "limitation_decision": accepted or None,
        "phase4_enabled": False,
    }


def evaluate_phase(directory: Path) -> dict:
    manifest = read_json(directory / "phase-manifest.json")
    check = preflight(directory)
    blockers = list(check["blockers"])
    terminal = directory / "held-out-result.json"
    if terminal.exists():
        try:
            result = _live_view(directory, read_json(terminal), blockers)
        except (ValueError, KeyError, OSError) as exc:
            result = {
                "status": "blocked",
                "blockers": blockers + [str(exc)],
                "phase4_enabled": False,
                "coverage_limitations_accepted": False,
            }
        write_json(directory / "phase-evaluation.json", result)
        return result
    try:
        labels, outputs = reviewed_documents(directory, "labels"), completed_outputs(directory)
    except (ValueError, KeyError, OSError) as exc:
        result = {
            "status": "blocked",
            "blockers": blockers + [str(exc)],
            "phase4_enabled": False,
            "coverage_limitations_accepted": False,
        }
        write_json(directory / "phase-evaluation.json", result)
        return result
    targets = manifest["document_ids"]
    if set(outputs) != set(targets):
        blockers.append("Complete valid outputs for every expected target required")
    if set(targets) - labels.keys():
        blockers.append("Complete independent full-text gold for every expected target required")
    if usage(directory)["pending"]:
        blockers.append("Unresolved dispatches")
    result = _result(directory, manifest, labels, outputs, blockers)
    if manifest["phase"] == "evaluation" and not blockers:
        sha = immutable(terminal, result)
        immutable(
            directory / "held-out-result-receipt.json",
            {"result_sha256": sha, "input_binding": _input_binding(directory)},
        )
        result = _live_view(directory, result, [])
    immutable(directory / "evaluations" / (fingerprint(result) + ".json"), result)
    write_json(directory / "phase-evaluation.json", result)
    return result


def _result(directory: Path, manifest: dict, labels: dict, outputs: dict, blockers: list) -> dict:
    tallies, cohorts, groups, diagnostics = _document_scores(
        directory, manifest["document_ids"], labels, outputs
    )
    metrics = _aggregate(tallies)
    status = (
        "blocked"
        if blockers
        else (
            "pilot_diagnostic"
            if manifest["phase"] == "calibration"
            else "passed" if _gates(metrics) else "failed"
        )
    )
    available = read_json(directory / "phase-report.json").get("unrepresented_features", [])
    subgroup = [
        {"function": k[0], "seniority": k[1], **_aggregate(rows)}
        for k, rows in sorted(groups.items())
    ]
    return {
        "status": status,
        "phase": manifest["phase"],
        "manifest_sha256": file_hash(directory / "phase-manifest.json"),
        "execution_manifest_sha256": (
            file_hash(directory / "execution-manifest.json")
            if (directory / "execution-manifest.json").exists()
            else None
        ),
        "blockers": blockers,
        "combined_scoped": metrics,
        "matching_policy": POLICY,
        "cohorts": {key: _aggregate(rows) for key, rows in sorted(cohorts.items())},
        "cohort_function_seniority": _cohort_strata(cohorts, groups),
        "function_seniority": subgroup,
        "document_diagnostics": diagnostics,
        "coverage_gaps": available,
        "coverage_gap_basis": "Prepared source cues; assessed subgroup tables use reviewed seniority.",
        "small_cells": [
            {
                "function": s["function"],
                "seniority": s["seniority"],
                "assessed_documents": s["assessed_documents"],
            }
            for s in subgroup
            if s["assessed_documents"] < 30
        ],
        "broad_original_40": "unavailable",
        "phase4_enabled": False,
        "coverage_limitations_accepted": False,
        "limitations": [
            "Pilot diagnostics are not held-out validation.",
            "Pooled scores do not establish quality in unassessed or small subgroups.",
            "Wilson statement intervals are descriptive; statements within descriptions are correlated.",
            "Failure requires a new calibration revision and fresh untouched evaluation evidence.",
        ],
        "evaluated_at": utc_now(),
    }


def _document_scores(directory: Path, targets: list, labels: dict, outputs: dict) -> tuple:
    fixed_docs, scope = package(directory), reviewed_documents(directory, "scope")
    tallies, cohorts, groups, diagnostics = [], defaultdict(list), defaultdict(list), []
    for doc in targets:
        fixed = fixed_docs[doc]
        assessable = doc in labels and doc in outputs
        tally = _tally(
            fixed,
            labels.get(doc, {}).get("labels") or [],
            outputs.get(doc, {}).get("requirements", []),
            assessable,
        )
        tally["strata"] = [
            (f, scope.get(doc, {}).get("reviewed_level", fixed["level"]))
            for f in fixed["functions"]
        ]
        tallies.append(tally)
        cohorts[fixed["cohort"]].append(tally)
        for stratum in tally["strata"]:
            groups[stratum].append(tally)
        diagnostics.append(
            {
                "document_id": doc,
                "cohort": fixed["cohort"],
                "assessed": assessable,
                **tally["diagnostics"],
            }
        )
    return tallies, cohorts, groups, diagnostics


def _cohort_strata(cohorts: dict, groups: dict) -> list[dict]:
    return [
        {
            "cohort": cohort,
            "function": func,
            "seniority": level,
            **_aggregate([r for r in cohorts[cohort] if (func, level) in r["strata"]]),
        }
        for cohort in sorted(cohorts)
        for func, level in sorted(groups)
        if any((func, level) in r["strata"] for r in cohorts[cohort])
    ]
