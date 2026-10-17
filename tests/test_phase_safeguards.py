"""Regression reproductions for dispatch, durable recovery and independent review gates."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
from test_phases import Runner, complete_calibration, envelope, human_ready, label, pair

from degent.model_runner import ModelResponse
from degent.phase_inference import completed_outputs, run_phase
from degent.phase_review import package, preflight, review
from degent.phase_state import calls, file_hash, usage
from degent.store import read_json


@pytest.mark.parametrize("stage", ["during_first_call", "after_reservation"])
def test_changed_source_stops_before_next_dispatch(pair, stage, monkeypatch):
    directory = pair["calibration"]
    human_ready(directory)
    source = Path(package(directory)["d0"]["clean_file"])
    if stage == "after_reservation":
        from degent.phase_inference import commit_reservation

        def changed(*args):
            path = commit_reservation(*args)
            source.write_text("changed before dispatch")
            return path

        monkeypatch.setattr("degent.phase_inference.commit_reservation", changed)

    class Mutation(Runner):
        def extract(self, request):
            response = super().extract(request)
            source.write_text("changed after first call")
            return response

    runner = Mutation()
    result = run_phase(directory, runner)
    assert result["status"] == "blocked"
    assert runner.count == (1 if stage == "during_first_call" else 0)
    assert usage(directory)["calls"] == 1


@pytest.mark.parametrize(
    "native",
    [
        {"input_tokens": 100, "output_tokens": 40, "cost_usd": 0.02},
        {"input_tokens": None, "output_tokens": 40, "cost_usd": 0.02},
        {"input_tokens": -1, "output_tokens": 40, "cost_usd": 0.001},
        {"input_tokens": 100, "output_tokens": 4097, "cost_usd": 0.001},
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "cost_usd": 0.001,
            "reported_model": "other/model",
        },
    ],
)
def test_usage_or_model_fault_remains_terminal_on_resume(pair, native):
    directory = pair["calibration"]
    human_ready(directory)

    class Fault(Runner):
        def extract(self, request):
            response = super().extract(request)
            response.usage = native
            return response

    first = Fault()
    assert run_phase(directory, first)["status"] == "blocked"
    second = Runner()
    assert run_phase(directory, second, retry=True)["status"] == "blocked"
    assert second.count == 0
    assert any("Terminal" in s for s in preflight(directory)["blockers"])
    if native.get("cost_usd") == 0.02:
        assert usage(directory)["cost_usd"] == 0.02


def _audit_all(directory):
    docs = package(directory)
    review(
        directory,
        {
            **envelope(directory, "audit"),
            "documents": [
                {
                    "document_id": doc,
                    "source_sha256": docs[doc]["source_sha256"],
                    "raw_sha256": raw["raw_sha256"],
                    "omissions_reviewed": True,
                    "attribution_reviewed": True,
                    "findings_resolved": True,
                    "corrections": [],
                }
                for doc, raw in completed_outputs(directory).items()
            ],
        },
    )


def test_reconciled_retry_can_finish_method_freeze_without_refunding(pair):
    directory = pair["calibration"]
    human_ready(directory)
    run_phase(directory, Runner("timeout"))
    before = usage(directory)
    review(
        directory,
        {
            **envelope(directory, "reconcile"),
            "call_id": before["pending"][0],
            "provider_receipt": {
                "generation_id": "fixture-no-output",
                "model": "openai/gpt-6-luna",
                "source_url": "https://openrouter.ai/activity",
                "dispatch_terminal": True,
                "outcome": "terminal_no_output",
            },
        },
    )
    assert run_phase(directory, Runner(), retry=True)["status"] == "outputs_complete_pending_audit"
    _audit_all(directory)
    review(
        directory,
        {
            **envelope(directory, "completion"),
            "method_frozen": True,
            "findings_resolved": True,
            "limitations": ["Unknown original billing retained."],
        },
    )
    freeze = read_json(directory / "method-freeze.json")
    assert any(c["terminal_artifact"] == "reconciliation.json" for c in freeze["call_artifacts"])
    assert freeze["usage"]["calls"] == 13
    assert freeze["usage"]["output_tokens"] >= before["output_tokens"]


def test_received_raw_write_can_recover_without_another_dispatch(pair, monkeypatch):
    directory = pair["calibration"]
    human_ready(directory)
    from degent.phase_inference import immutable

    def fail_completion(path, value):
        if path.name == "completion.json":
            raise OSError("fixture interruption before completion write")
        return immutable(path, value)

    with monkeypatch.context() as context:
        context.setattr("degent.phase_inference.immutable", fail_completion)
        runner = Runner()
        with pytest.raises(OSError):
            run_phase(directory, runner)
        assert runner.count == 1
    original = calls(directory)[0]
    raw = original["directory"] / "raw.json"
    before = file_hash(raw)
    review(
        directory,
        {
            **envelope(directory, "recover-raw"),
            "call_id": original["reservation"]["call_id"],
            "raw_sha256": before,
        },
    )
    assert file_hash(raw) == before
    assert usage(directory)["calls"] == 1
    assert len(completed_outputs(directory)) == 1
    remaining = Runner()
    run_phase(directory, remaining)
    assert remaining.count == 11
    assert usage(directory)["calls"] == 12


@pytest.mark.parametrize(
    "artifact", ["directory", "reservation", "ledger_tail", "ledger_head", "partial"]
)
def test_missing_or_partial_artifacts_cannot_reset_consumption(pair, artifact):
    directory = pair["calibration"]
    human_ready(directory)
    run_phase(directory, Runner("timeout"))
    call = calls(directory)[0]
    if artifact == "directory":
        shutil.rmtree(call["directory"])
    elif artifact == "reservation":
        (call["directory"] / "reservation.json").unlink()
    elif artifact == "ledger_tail":
        next((directory / "call-ledger").glob("*.json")).unlink()
    elif artifact == "ledger_head":
        (directory / "call-ledger-head.json").unlink()
    else:
        (call["directory"] / "reservation.json").write_text('{"call_id":')
    assert preflight(directory)["status"] == "blocked"
    runner = Runner()
    assert run_phase(directory, runner, retry=True)["status"] == "blocked"
    assert runner.count == 0


def test_preflight_has_no_method_freeze_or_other_write_side_effects(pair):
    complete_calibration(pair["calibration"])
    human_ready(pair["evaluation"])
    paths = [
        p
        for root in (pair["calibration"], pair["evaluation"])
        for p in root.rglob("*")
        if p.is_file()
    ]
    before = {p: (file_hash(p), p.stat().st_mtime_ns) for p in paths}
    assert preflight(pair["evaluation"])["status"] == "ready"
    assert {p: (file_hash(p), p.stat().st_mtime_ns) for p in paths} == before


@pytest.mark.parametrize(
    "fault", ["number", "range", "unknown_concept", "duplicate", "qualifier", "alternatives"]
)
def test_gold_rejects_unsupported_attributes_and_duplicate_statements(pair, fault):
    directory = pair["calibration"]
    fixed = package(directory)["d0"]
    row = label(fixed)
    if fault == "number":
        row.update(experience_years=123, experience_range=[123, 123])
    elif fault == "range":
        row.update(experience_years=2, experience_range=[3, 4])
    elif fault == "unknown_concept":
        row["concept"] = "not-a-known-concept"
    elif fault == "qualifier":
        row["qualifier"] = "Capital One"  # Elsewhere in text, unrelated to this statement.
    elif fault == "alternatives":
        row.update(alternatives=["credit risk"], alternative_logic="or")
    labels = [row, copy.deepcopy(row)] if fault == "duplicate" else [row]
    with pytest.raises(ValueError):
        review(
            directory,
            {
                **envelope(directory, "labels"),
                "documents": [
                    {
                        "document_id": "d0",
                        "source_sha256": fixed["source_sha256"],
                        "labels": labels,
                        "independent_full_text": True,
                        "omission_audited": True,
                        "conventions_reviewed": True,
                        "selector_omissions": [],
                    }
                ],
            },
        )


@pytest.mark.parametrize("fault", ["seniority", "function", "unknown"])
def test_reviewed_metadata_needs_exact_evidence_or_explicit_unknown(pair, fault):
    directory = pair["calibration"]
    fixed = package(directory)["d0"]
    row = {
        "document_id": "d0",
        "source_sha256": fixed["source_sha256"],
        "decision": "include",
        "us_geography_confirmed": True,
        "identity_resolved": True,
        "description_complete": True,
        "selection_complete": True,
        "seniority_reviewed": True,
        "reviewed_level": "unknown",
        "uncertainties_accepted": True,
        "duty_evidence": fixed["duty_evidence"],
        "geography_evidence": fixed["geography_evidence"],
        "seniority_unknown_reason": "Fixture unknown accepted.",
        "reviewed_functions": fixed["functions"],
        "function_evidence": {"risk": fixed["duty_evidence"]},
    }
    if fault == "seniority":
        row.update(reviewed_level="executive", seniority_evidence=fixed["duty_evidence"][0])
    elif fault == "function":
        row["function_evidence"] = {}
    else:
        row["seniority_unknown_reason"] = None
    with pytest.raises(ValueError):
        review(directory, {**envelope(directory, "scope"), "documents": [row]})


@pytest.mark.parametrize(
    "action", ["groups", "approve-taxonomy", "gold", "evaluate", "audit-packet"]
)
def test_dashboard_legacy_study_writes_cannot_mutate_phase_artifacts(pair, action):
    from degent.dashboard import Dashboard

    app = Dashboard(pair["calibration"].parent.parent)
    directory = pair["calibration"]
    before = {
        p: file_hash(p) for p in directory.rglob("*") if p.is_file() and p.name != ".operation.lock"
    }
    with pytest.raises(ValueError, match="Isolated"):
        app.study_action(directory.name, {"action": action, "decisions": {}})
    assert {p: file_hash(p) for p in before} == before


def test_all_nine_supplements_need_explicit_source_bound_approval(pair, monkeypatch):
    directory = pair["calibration"]
    from test_phases import review as original_review

    def omit_supplements(target, payload):
        if payload["kind"] == "supplements":
            return {}
        return original_review(target, payload)

    monkeypatch.setattr("test_phases.review", omit_supplements)
    with pytest.raises(ValueError, match="supplements: 9"):
        human_ready(directory, authorize=False)
    assert not (directory / "execution-manifest.json").exists()


def test_preserved_dispatch_intent_is_required_for_received_output(pair):
    directory = pair["calibration"]
    human_ready(directory)
    run_phase(directory, Runner("invalid"))
    (calls(directory)[0]["directory"] / "dispatch.json").unlink()
    assert preflight(directory)["status"] == "blocked"
    assert "dispatch intent" in preflight(directory)["blockers"][0]


def test_model_change_after_first_call_stops_next_reservation(pair, monkeypatch):
    directory = pair["calibration"]
    human_ready(directory)

    class ChangeMethod(Runner):
        def extract(self, request):
            result = super().extract(request)
            monkeypatch.setattr("degent.phase_review.method_signature", lambda identity: "changed")
            return result

    runner = ChangeMethod()
    assert run_phase(directory, runner)["status"] == "blocked"
    assert runner.count == 1
    assert usage(directory)["calls"] == 1


def test_usage_fault_on_last_call_cannot_complete_or_pass(pair):
    directory = pair["calibration"]
    human_ready(directory)

    class LastFault(Runner):
        def extract(self, request):
            result = super().extract(request)
            if self.count == 12:
                result.usage["input_tokens"] = 400001
            return result

    runner = LastFault()
    assert run_phase(directory, runner)["status"] == "blocked"
    from degent.phase_evaluation import evaluate_phase

    assert evaluate_phase(directory)["status"] == "blocked"
    with pytest.raises(ValueError):
        review(
            directory,
            {
                **envelope(directory, "completion"),
                "method_frozen": True,
                "findings_resolved": True,
                "limitations": [],
            },
        )


@pytest.mark.parametrize("boundary", ["head", "reservation", "intent"])
def test_interrupted_reservation_and_intent_writes_never_dispatch_again(
    pair, monkeypatch, boundary
):
    directory = pair["calibration"]
    human_ready(directory)
    from degent.phase_state import immutable

    def fail(path, value):
        if (
            boundary == "head"
            and path.name == "call-ledger-head.json.tmp"
            or boundary == "reservation"
            and path.name == "reservation.json"
            or boundary == "intent"
            and path.name == "dispatch.json"
        ):
            raise OSError("fixture interrupted durable boundary")
        return immutable(path, value)

    target = (
        "degent.phase_inference.immutable"
        if boundary == "intent"
        else "degent.phase_ledger.immutable"
    )
    runner = Runner()
    with monkeypatch.context() as context:
        context.setattr(target, fail)
        with pytest.raises(OSError):
            run_phase(directory, runner)
    assert runner.count == 0
    assert preflight(directory)["status"] == "blocked"
    assert run_phase(directory, runner, retry=True)["status"] == "blocked"
    assert runner.count == 0


def test_supported_explicit_attributes_and_evidence_link_are_valid():
    from degent.evidence import select_units
    from degent.phase_labels import attribute_errors, validate_gold
    from degent.store import digest

    text = "Basic Qualifications\nAt least five years of SQL experience required.\nSQL or Python preferred."
    row = {
        "qualifier": "At least",
        "experience_range": [5, None],
        "experience_years": None,
        "alternatives": [],
        "alternative_logic": None,
    }
    assert not attribute_errors(row, "At least five years of SQL experience required.")
    row.update(
        qualifier=None,
        experience_range=None,
        alternatives=["SQL", "Python"],
        alternative_logic="or",
    )
    assert not attribute_errors(row, "SQL or Python preferred.")
    selection = select_units(text)
    fixed = {"full_text": text, "source_sha256": digest(text), "selection": selection}
    start = text.index("SQL experience")
    gold = {
        **row,
        "concept": "sql",
        "evidence": "SQL experience",
        "evidence_start": start,
        "evidence_end": start + len("SQL experience"),
        "alternatives": [],
        "alternative_logic": None,
        "experience_range": [5, None],
        "attribute_evidence": [],
    }
    validate_gold([gold], fixed, {})


@pytest.mark.parametrize("decision", ["review", "exclude"])
def test_human_unresolved_or_excluded_decision_is_preserved_and_blocks_pilot(pair, decision):
    directory = pair["calibration"]
    human_ready(directory)
    fixed = package(directory)["d0"]
    review(
        directory,
        {
            **envelope(directory, "scope"),
            "documents": [
                {
                    "document_id": "d0",
                    "source_sha256": fixed["source_sha256"],
                    "decision": decision,
                    "notes": "Independent reviewer rejected scope or needs better evidence.",
                    "review_evidence": fixed["duty_evidence"],
                }
            ],
        },
    )
    from degent.phase_state import reviewed_documents

    assert reviewed_documents(directory, "scope")["d0"]["decision"] == decision
    assert preflight(directory)["status"] == "blocked"
    runner = Runner()
    assert run_phase(directory, runner)["status"] == "blocked"
    assert runner.count == 0


def test_title_alone_cannot_establish_phase_role_assignment(pair, monkeypatch):
    directory = pair["calibration"]
    from test_phases import review as original_review

    def title_only(target, payload):
        if payload["kind"] == "taxonomy":
            for doc, assignment in payload["assignments"].items():
                assignment["evidence"] = package(target)[doc]["title"]
        return original_review(target, payload)

    monkeypatch.setattr("test_phases.review", title_only)
    with pytest.raises(ValueError, match="direct duty"):
        human_ready(directory)
    assert not (directory / "taxonomy.json").exists()


def test_cancelled_before_dispatch_retains_usage_and_freezes_actual_artifact(pair, monkeypatch):
    directory = pair["calibration"]
    human_ready(directory)
    source = Path(package(directory)["d0"]["clean_file"])
    original_text = source.read_text()
    from degent.phase_inference import commit_reservation

    def change_source(*args):
        result = commit_reservation(*args)
        source.write_text("interrupted source binding before provider call")
        return result

    runner = Runner()
    with monkeypatch.context() as context:
        context.setattr("degent.phase_inference.commit_reservation", change_source)
        assert run_phase(directory, runner)["status"] == "blocked"
    assert runner.count == 0
    before = usage(directory)
    assert not before["pending"]
    source.write_text(original_text)
    assert run_phase(directory, runner, retry=True)["status"] == "outputs_complete_pending_audit"
    _audit_all(directory)
    review(
        directory,
        {
            **envelope(directory, "completion"),
            "method_frozen": True,
            "findings_resolved": True,
            "limitations": ["Cancelled reservation retained."],
        },
    )
    frozen = read_json(directory / "method-freeze.json")
    assert any(c["terminal_artifact"] == "cancelled.json" for c in frozen["call_artifacts"])
    assert usage(directory)["calls"] == 13
    assert usage(directory)["cost_usd"] >= before["cost_usd"]
