"""Frozen scoring, deterministic alignment, attribute denominators and fresh revisions."""

from __future__ import annotations

import copy
import itertools
import random
from pathlib import Path

import pytest
from test_phases import Runner, complete_calibration, envelope, human_ready, pair

from degent.phase_evaluation import _aggregate, _gates, _tally, evaluate_phase
from degent.phase_inference import run_phase
from degent.phase_matching import align, overlap
from degent.phase_review import package, preflight, review
from degent.phase_revision import prepare_revision
from degent.phase_state import decisions, file_hash, usage
from degent.store import read_json, write_json


def statement(start, end, status="required", concept="sql"):
    return {
        "concept": concept,
        "evidence_start": start,
        "evidence_end": end,
        "evidence": "x" * (end - start),
        "status": status,
        "statement_scope": "qualification",
        "qualifier": None,
        "experience_range": None,
        "experience_years": None,
        "alternatives": [],
        "alternative_logic": None,
    }


def test_matching_maximizes_cardinality_instead_of_greedy_first_overlap():
    gold = [statement(0, 10), statement(5, 15)]
    predicted = [statement(0, 15), statement(0, 10)]
    result = align(predicted, gold)
    assert set(result["pairs"]) == {(0, 1), (1, 0)}
    assert result["ambiguous_predicted"] == [0]


def test_tiny_overlap_cannot_claim_statement_match():
    assert not align([statement(9, 19)], [statement(0, 10)])["pairs"]
    assert not align([statement(0, 10, concept="python")], [statement(0, 10)])["pairs"]


def test_matching_quality_and_semantic_agreement_do_not_change_objective():
    gold = [statement(0, 10), statement(5, 15, "preferred")]
    predicted = [statement(0, 10, "preferred"), statement(5, 15)]
    assert set(align(predicted, gold)["pairs"]) == {(0, 0), (1, 1)}


def test_alignment_is_invariant_to_prediction_and_gold_order():
    gold = [statement(0, 10), statement(5, 15), statement(20, 30, concept="python")]
    predicted = [statement(0, 15), statement(0, 10), statement(20, 30, concept="python")]
    reference = {
        (
            predicted[p]["evidence_start"],
            predicted[p]["evidence_end"],
            gold[g]["evidence_start"],
            gold[g]["evidence_end"],
        )
        for p, g in align(predicted, gold)["pairs"]
    }
    for pp in itertools.permutations(predicted):
        for gg in itertools.permutations(gold):
            actual = {
                (
                    pp[p]["evidence_start"],
                    pp[p]["evidence_end"],
                    gg[g]["evidence_start"],
                    gg[g]["evidence_end"],
                )
                for p, g in align(list(pp), list(gg))["pairs"]
            }
            assert actual == reference


def test_assignment_matches_brute_force_optimum_on_small_graphs():
    rng = random.Random(910)
    for _ in range(30):
        gold = [statement(s, s + 10) for s in [rng.randrange(0, 15) for _ in range(3)]]
        predicted = [
            statement(s, s + rng.randrange(8, 16)) for s in [rng.randrange(0, 15) for _ in range(3)]
        ]

        def objective(pairs):
            return len(pairs), sum(round(overlap(predicted[p], gold[g]) * 1e6) for p, g in pairs)

        possible = []
        for cols in itertools.product([-1, 0, 1, 2], repeat=3):
            if len([c for c in cols if c >= 0]) != len({c for c in cols if c >= 0}):
                continue
            pairs = [(p, g) for p, g in enumerate(cols) if g >= 0]
            if all(overlap(predicted[p], gold[g]) >= 0.5 for p, g in pairs):
                possible.append(objective(pairs))
        assert objective(align(predicted, gold)["pairs"]) == max(possible)


def fixed_source():
    return {
        "full_text": "x" * 100,
        "selection": {"units": [{"start": 0, "end": 100, "selected": True}]},
    }


def test_omitted_attribute_and_extra_statement_are_not_hidden_by_matched_only_accuracy():
    gold = [statement(0, 10), {**statement(20, 30), "experience_range": [5, None]}]
    predicted = [statement(0, 10), statement(0, 10)]
    tally = _tally(fixed_source(), gold, predicted)
    metrics = _aggregate([tally])
    assert metrics["statement"]["false_positive"] == 1
    assert metrics["statement"]["false_negative"] == 1
    assert metrics["explicit_attributes"]["experience_range"]["assessed"] == 1
    assert metrics["explicit_attributes"]["experience_range"]["incorrect"] == 1
    assert tally["diagnostics"]["duplicate_predictions"]
    assert tally["diagnostics"]["omitted_gold"]
    assert not _gates(metrics)


def test_unassessed_attribute_and_document_cannot_pass():
    tally = _tally(
        fixed_source(),
        [{**statement(0, 10), "qualifier": "five years"}],
        [statement(0, 10)],
        assessable=False,
    )
    metrics = _aggregate([tally])
    assert metrics["unassessed_documents"] == 1
    assert metrics["explicit_attributes"]["qualifier"]["unassessed"] == 1
    assert not _gates(metrics)


def evaluated_pair(pair, mode="valid"):
    complete_calibration(pair["calibration"])
    human_ready(pair["evaluation"])
    run_phase(pair["evaluation"], Runner(mode))
    return evaluate_phase(pair["evaluation"])


def test_limit_acceptance_is_live_and_binds_frozen_outcome_without_rescoring(pair):
    directory = pair["evaluation"]
    result = evaluated_pair(pair)
    assert result["status"] == "passed" and not result["coverage_limitations_accepted"]
    before = file_hash(directory / "held-out-result.json")
    review(
        directory,
        {
            **envelope(directory, "limitations"),
            "coverage_limitations_accepted": True,
            "frozen_result_sha256": before,
            "notes": "Small and missing cells explicitly accepted.",
        },
    )
    accepted = evaluate_phase(directory)
    assert accepted["coverage_limitations_accepted"] is True
    assert accepted["combined_scoped"] == result["combined_scoped"]
    assert file_hash(directory / "held-out-result.json") == before
    review(
        directory,
        {
            **envelope(directory, "limitations"),
            "coverage_limitations_accepted": False,
            "frozen_result_sha256": before,
            "notes": "Acceptance withdrawn pending coverage review.",
        },
    )
    assert not evaluate_phase(directory)["coverage_limitations_accepted"]
    assert file_hash(directory / "held-out-result.json") == before
    assert usage(directory)["calls"] == 40


def test_acceptance_cannot_bind_wrong_result_or_changed_sources(pair):
    directory = pair["evaluation"]
    evaluated_pair(pair)
    with pytest.raises(ValueError):
        review(
            directory,
            {
                **envelope(directory, "limitations"),
                "coverage_limitations_accepted": True,
                "frozen_result_sha256": "wrong",
                "notes": "Wrong binding.",
            },
        )
    fixed = next(iter(package(directory).values()))
    Path(fixed["clean_file"]).write_text("changed after scores")
    assert evaluate_phase(directory)["status"] == "blocked"
    assert not evaluate_phase(directory)["coverage_limitations_accepted"]


def test_frozen_score_tampering_cannot_create_a_pass(pair):
    directory = pair["evaluation"]
    evaluated_pair(pair, "wrong")
    terminal = read_json(directory / "held-out-result.json")
    terminal["status"] = "passed"
    write_json(directory / "held-out-result.json", terminal)
    assert evaluate_phase(directory)["status"] == "blocked"


def test_cohort_and_subgroup_reports_preserve_original_scope_and_show_denominators(pair):
    result = evaluated_pair(pair)
    assert result["cohorts"]["original_scoped"]["assessed_documents"] == 19
    assert result["cohorts"]["supplement"]["assessed_documents"] == 21
    assert result["combined_scoped"]["assessed_documents"] == 40
    assert len(result["document_diagnostics"]) == 40
    assert {r["cohort"] for r in result["cohort_function_seniority"]} == {
        "original_scoped",
        "supplement",
    }
    assert result["broad_original_40"] == "unavailable"
    assert result["phase4_enabled"] is False


def revision_approval(directory):
    return {
        **envelope(directory, "calibration_revision"),
        "previous_result_sha256": file_hash(directory / "held-out-result.json"),
        "reason": "Human approved prompt calibration revision after context failure; separate budget pending.",
    }


def test_fresh_revision_has_no_heldout_leakage_no_copied_approvals_and_is_idempotent(pair):
    old = pair["evaluation"]
    evaluated_pair(pair, "wrong")
    approval = revision_approval(old)
    result = prepare_revision(pair["prep"], old, old.parent, approval)
    new = Path(result["runs"]["evaluation"])
    prior = read_json(old / "phase-manifest.json")
    manifest = read_json(new / "phase-manifest.json")
    assert len(manifest["document_ids"]) == 40
    for key in ("document_id", "vacancy_id", "source_sha256"):
        assert not {d[key] for d in manifest["documents"]} & {
            d[key] for d in prior["documents"] + prior["calibration_reserved"]
        }
    assert result["original_scoped"] == 0 and result["fresh_supplement"] == 40
    assert not decisions(new) and usage(new)["calls"] == 0
    assert preflight(new)["status"] == "blocked"
    assert prepare_revision(pair["prep"], old, old.parent, approval) == result
    assert evaluate_phase(old)["status"] == "failed"


def test_fresh_revision_requires_failure_and_explicit_human_approval(pair):
    evaluated_pair(pair)
    with pytest.raises(ValueError):
        prepare_revision(
            pair["prep"],
            pair["evaluation"],
            pair["evaluation"].parent,
            revision_approval(pair["evaluation"]),
        )


def test_revised_calibration_cannot_relabel_gold_using_exposed_predictions(pair):
    old = pair["evaluation"]
    evaluated_pair(pair, "wrong")
    result = prepare_revision(pair["prep"], old, old.parent, revision_approval(old))
    new = Path(result["runs"]["calibration"])
    from test_phases import label

    fixed = package(new)["d0"]
    changed = label(fixed)
    changed["status"] = "preferred"
    with pytest.raises(ValueError, match="prior independent"):
        review(
            new,
            {
                **envelope(new, "labels"),
                "documents": [
                    {
                        "document_id": "d0",
                        "source_sha256": fixed["source_sha256"],
                        "labels": [changed],
                        "independent_full_text": True,
                        "omission_audited": True,
                        "selector_omissions": [],
                        "conventions_reviewed": True,
                    }
                ],
            },
        )


def test_missing_gold_attribute_is_unassessed_even_when_prediction_is_empty():
    gold = statement(0, 10)
    del gold["experience_range"]
    metrics = _aggregate([_tally(fixed_source(), [gold], [statement(0, 10)])])
    assert metrics["explicit_attributes"]["experience_range"]["unassessed"] == 1
    assert not _gates(metrics)


def test_partial_revision_materialization_resumes_same_frozen_assignments(pair, monkeypatch):
    old = pair["evaluation"]
    evaluated_pair(pair, "wrong")
    approval = revision_approval(old)
    from degent.phases import _materialize

    def interrupted(*args):
        _materialize(*args)
        if args[1] == "evaluation":
            raise OSError("fixture interruption after evaluation manifest froze")

    with monkeypatch.context() as context:
        context.setattr("degent.phases._materialize", interrupted)
        with pytest.raises(OSError):
            prepare_revision(pair["prep"], old, old.parent, approval)
    manifests = {p: file_hash(p) for p in old.parent.glob("*/phase-manifest.json")}
    result = prepare_revision(pair["prep"], old, old.parent, approval)
    assert Path(result["runs"]["evaluation"]) / "phase-manifest.json" in manifests
    assert {p: file_hash(p) for p in old.parent.glob("*/phase-manifest.json")} == manifests


def test_interrupted_assignment_is_protected_from_another_revision(pair, monkeypatch):
    old = pair["evaluation"]
    evaluated_pair(pair, "wrong")
    first = revision_approval(old)
    with monkeypatch.context() as context:
        context.setattr(
            "degent.phase_revision._materialize_revision",
            lambda *args: (_ for _ in ()).throw(OSError("after frozen assignments")),
        )
        with pytest.raises(OSError):
            prepare_revision(pair["prep"], old, old.parent, first)
    plan = next((old.parent / "phase-revision-requests").glob("*-assignments.json"))
    protected = read_json(plan)["sources"]
    second = {
        **first,
        "reason": "Separately requested second revision; first assignment preserved.",
    }
    result = prepare_revision(pair["prep"], old, old.parent, second)
    fresh = read_json(Path(result["runs"]["evaluation"]) / "phase-manifest.json")["documents"]
    for key in ("document_id", "vacancy_id", "source_sha256"):
        assert not {d[key] for d in fresh} & {d[key] for d in protected}


def test_revision_preparation_cannot_overlap_another_writer(pair):
    from degent.store import operation_lock

    with operation_lock(pair["evaluation"].parent / "phase-revision-requests"):
        with pytest.raises(RuntimeError, match="already writing"):
            prepare_revision(pair["prep"], pair["evaluation"], pair["evaluation"].parent, {})


def test_ordinary_preparation_cannot_reset_gold_after_pilot_exposure(pair, monkeypatch):
    from degent.phases import implementation, prepare_phases

    human_ready(pair["calibration"])
    run_phase(pair["calibration"], Runner())
    old = implementation()
    monkeypatch.setattr("degent.phases.implementation", lambda: {**old, "revision": "next"})
    with pytest.raises(ValueError, match="preserving independent gold"):
        prepare_phases(pair["prep"], pair["calibration"].parent)
