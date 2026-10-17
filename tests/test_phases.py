"""Offline phase authorization, separation, interruption and metric regressions."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from degent.evidence import select_units
from degent.model_runner import ModelResponse, expand_wire
from degent.openrouter import model_identity
from degent.phase_evaluation import evaluate_phase
from degent.phase_inference import run_phase
from degent.phase_review import package, preflight, reports, review
from degent.phase_state import calls, file_hash, usage
from degent.phases import prepare_phases
from degent.store import digest, read_json, utc_now, write_json, write_jsonl


@pytest.fixture
def pair(tmp_path):
    prep = tmp_path / "readiness"
    scope = prep / "scoped-selections" / "fixture"
    scope.mkdir(parents=True)
    observations, rows = [], []
    for number in range(200):
        text = (
            f"Capital One\nSenior Credit Risk Analyst {number}\nLocation: Virginia, United States\n"
            "Responsibilities\nAnalyze credit risk models.\nBasic Qualifications\nSQL required."
        )
        if number == 3:
            text = observations[0]["text"]
        path = tmp_path / f"source-{number}.txt"
        path.write_text(text)
        obs = {
            "document_id": f"d{number}",
            "vacancy_id": f"v{number}",
            "text": text,
            "clean_file": str(path),
            "clean_sha256": digest(text),
            "title": f"Senior Credit Risk Analyst {number}",
        }
        observations.append(obs)
        duty = "Analyze credit risk models."
        geo = "Location: Virginia, United States"
        decision = "exclude" if 39 <= number < 60 else "include"
        rows.append(
            {
                **obs,
                "source_sha256": digest(text),
                "decision": decision,
                "proposed_dispatch": decision == "include",
                "functions": ["risk"],
                "level": "senior",
                "duty_evidence": [
                    {"start": text.index(duty), "end": text.index(duty) + len(duty), "text": duty}
                ],
                "geography_evidence": [
                    {"start": text.index(geo), "end": text.index(geo) + len(geo), "text": geo}
                ],
            }
        )
    rows[20]["proposed_dispatch"] = False  # Frozen original may retain a repeated capture.
    packet = {
        "documents": [
            {**r, "split": "calibration" if i < 20 else "held_out"} for i, r in enumerate(rows[:60])
        ]
    }
    write_json(prep / "gold-packet.json", packet)
    write_jsonl(prep / "observations.jsonl", observations)
    write_jsonl(scope / "selection.jsonl", rows)
    write_jsonl(scope / "proposed-dispatch.jsonl", [r for r in rows if r["proposed_dispatch"]])
    write_json(
        scope / "phase-plan.json",
        {"phase2": {"pilot_document_ids": ["d0", "d1", "d2"] + [f"d{i}" for i in range(60, 69)]}},
    )
    write_json(
        scope / "supplement-proposal.json",
        {
            "documents": [
                {"document_id": f"d{i}", "proposed_split": "calibration" if i < 69 else "held_out"}
                for i in range(60, 77)
            ]
        },
    )
    write_json(
        scope / "manifest.json",
        {
            "version": "fixture-selection-v1",
            "selection_signature": "fixture",
            "gold_sha256": file_hash(prep / "gold-packet.json"),
            "original_observations_sha256": file_hash(prep / "observations.jsonl"),
            "files": {p.name: file_hash(p) for p in scope.iterdir()},
        },
    )
    write_json(
        prep / "scoped-selection-index-v1.json", {"current_selection": "scoped-selections/fixture"}
    )
    result = prepare_phases(prep, tmp_path / "runs")
    return {**{k: Path(v) for k, v in result["runs"].items()}, "prep": prep, "summary": result}


def envelope(directory, kind):
    return {
        "kind": kind,
        "approved": True,
        "reviewer": "Independent test reviewer",
        "reviewed_at": utc_now(),
        "manifest_sha256": file_hash(directory / "phase-manifest.json"),
    }


def label(fixed):
    text = fixed["full_text"]
    start = text.index("SQL required.")
    return {
        "concept": "sql",
        "evidence_start": start,
        "evidence_end": start + len("SQL required."),
        "evidence": "SQL required.",
        "status": "required",
        "statement_scope": "qualification",
        "qualifier": None,
        "experience_range": None,
        "experience_years": None,
        "alternatives": [],
        "alternative_logic": None,
        "negated": False,
    }


def human_ready(directory, authorize=True, skills=None):
    docs = package(directory)
    manifest = read_json(directory / "phase-manifest.json")
    targets = manifest["document_ids"]
    if skills is None and manifest["phase"] == "evaluation":
        parent_taxonomy = Path(manifest["calibration_run"]) / "taxonomy.json"
        skills = read_json(parent_taxonomy)["skills"] if parent_taxonomy.exists() else {}
    review(
        directory,
        {
            **envelope(directory, "scope"),
            "documents": [
                {
                    "document_id": d,
                    "source_sha256": docs[d]["source_sha256"],
                    "decision": "include",
                    "us_geography_confirmed": True,
                    "identity_resolved": True,
                    "description_complete": True,
                    "selection_complete": True,
                    "seniority_reviewed": True,
                    "reviewed_level": "senior",
                    "uncertainties_accepted": True,
                    "seniority_evidence": {
                        "start": docs[d]["full_text"].index("Senior"),
                        "end": docs[d]["full_text"].index("Senior") + 6,
                        "text": "Senior",
                    },
                    "reviewed_functions": docs[d]["functions"],
                    "function_evidence": {
                        f: docs[d]["duty_evidence"] for f in docs[d]["functions"]
                    },
                    "duty_evidence": docs[d]["duty_evidence"],
                    "geography_evidence": docs[d]["geography_evidence"],
                }
                for d in targets
            ],
        },
    )
    review(
        directory,
        {
            **envelope(directory, "labels"),
            "documents": [
                {
                    "document_id": d,
                    "source_sha256": fixed["source_sha256"],
                    "labels": [label(fixed)],
                    "independent_full_text": True,
                    "omission_audited": True,
                    "conventions_reviewed": True,
                    "selector_omissions": [],
                }
                for d, fixed in docs.items()
            ],
        },
    )
    review(
        directory,
        {
            **envelope(directory, "taxonomy"),
            "groups": {
                "risk": {
                    "name": "Risk",
                    "definition": "Duties analyze credit risk models.",
                    "function": "risk",
                    "domains": ["credit"],
                    **({"approved": True} if manifest["phase"] == "evaluation" else {}),
                }
            },
            "skills": skills or {},
            "assignments": {
                d: {
                    "source_sha256": docs[d]["source_sha256"],
                    "role_group_id": "risk",
                    "evidence": "Analyze credit risk models.",
                }
                for d in targets
            },
        },
    )
    if manifest["phase"] == "calibration":
        supplement_ids = {r["document_id"] for r in manifest["calibration_supplements"]}
        scope_rows = [
            {
                "document_id": d,
                "source_sha256": docs[d]["source_sha256"],
                "decision": "include",
                "us_geography_confirmed": True,
                "identity_resolved": True,
                "description_complete": True,
                "selection_complete": True,
                "seniority_reviewed": True,
                "reviewed_level": "unknown",
                "seniority_unknown_reason": "Independent fixture reviewer retained unknown.",
                "uncertainties_accepted": True,
                "duty_evidence": docs[d]["duty_evidence"],
                "geography_evidence": docs[d]["geography_evidence"],
                "reviewed_functions": docs[d]["functions"],
                "function_evidence": {f: docs[d]["duty_evidence"] for f in docs[d]["functions"]},
            }
            for d in supplement_ids
        ]
        review(directory, {**envelope(directory, "supplements"), "documents": scope_rows})
    review(
        directory,
        {
            **envelope(directory, "approval"),
            "taxonomy_sha256": file_hash(directory / "taxonomy.json"),
            "definitions_and_conventions_approved": True,
            "duplicate_calibration_text_review": True,
            "evaluation_assignments_approved": True,
            "identity_decisions": [
                {
                    "vacancy_ids": ["v0", "v3"],
                    "source_sha256": read_json(directory / "phase-manifest.json")[
                        "calibration_reserved"
                    ][0]["source_sha256"],
                    "decision": "retain_distinct_requisitions_single_pilot_capture",
                    "rationale": "Separate preserved source requisition IDs; d0 receives pilot call.",
                }
            ],
        },
    )
    if authorize:
        spending(directory)


def spending(directory, **changes):
    payload = {
        **envelope(directory, "spending"),
        "spending_authorized": True,
        "execution_manifest_sha256": file_hash(directory / "execution-manifest.json"),
        "caps": read_json(directory / "phase-manifest.json")["caps"],
        "pricing": {
            "verified_at": utc_now(),
            "source_url": "https://openrouter.ai/openai/gpt-6-luna",
            "model": "openai/gpt-6-luna",
            "input_per_million": 0.2,
            "output_per_million": 0.75,
        },
    }
    payload.update(changes)
    return review(directory, payload)


class Runner:
    identity = model_identity()

    def __init__(self, mode="valid", usage_known=True):
        self.mode, self.usage_known, self.count = mode, usage_known, 0

    def extract(self, request):
        self.count += 1
        if self.mode == "interrupt":
            raise KeyboardInterrupt
        if self.mode == "timeout":
            raise TimeoutError("sensitive upstream diagnostic must not be logged")
        unit = next(u for u in request["units"] if "SQL required." in u["text"])
        start = unit["text"].index("SQL required.")
        output = {
            "r": [
                {
                    "u": unit["u"],
                    "span": [start, start + len("SQL required.")],
                    "c": "SQL",
                    "s": "preferred" if self.mode == "wrong" else "required",
                    "k": "qualification",
                }
            ]
        }
        if self.mode == "invalid":
            output["r"][0]["span"] = [9999, 10000]
        usage_values = (
            {"input_tokens": 100, "output_tokens": 40, "cost_usd": 0.001}
            if self.usage_known
            else {}
        )
        return ModelResponse(output, usage_values, "length" if self.mode == "truncated" else "stop")


def complete_calibration(directory, skills=None):
    human_ready(directory, skills=skills)
    assert run_phase(directory, Runner())["status"] == "outputs_complete_pending_audit"
    docs = package(directory)
    from degent.phase_inference import completed_outputs

    review(
        directory,
        {
            **envelope(directory, "audit"),
            "documents": [
                {
                    "document_id": d,
                    "source_sha256": docs[d]["source_sha256"],
                    "raw_sha256": raw["raw_sha256"],
                    "omissions_reviewed": True,
                    "attribution_reviewed": True,
                    "findings_resolved": True,
                    "corrections": [],
                }
                for d, raw in completed_outputs(directory).items()
            ],
        },
    )
    review(
        directory,
        {
            **envelope(directory, "completion"),
            "findings_resolved": True,
            "method_frozen": True,
            "limitations": ["Synthetic single-function fixture."],
        },
    )


def test_prepare_freezes_12_and_19_plus_21_without_approvals(pair):
    cal, held = pair["calibration"], pair["evaluation"]
    assert pair["summary"]["additional_supplement"] == 13
    assert reports(cal)["counts"]["inference_targets"] == 12
    assert reports(held)["counts"]["cohorts"] == {"original_scoped": 19, "supplement": 21}
    before = file_hash(held / "phase-manifest.json")
    prepare_phases(pair["prep"], held.parent)
    assert file_hash(held / "phase-manifest.json") == before
    c, h = package(cal), package(held)
    assert not {d["source_sha256"] for d in c.values()} & {d["source_sha256"] for d in h.values()}
    assert reports(cal)["identity"]["calibration_duplicate_text_groups"] == [["v0", "v3"]]
    assert run_phase(cal, Runner())["status"] == "blocked"
    assert not calls(cal)


def test_review_and_spending_are_independent(pair):
    directory = pair["calibration"]
    human_ready(directory, authorize=False)
    assert preflight(directory)["status"] == "blocked"
    runner = Runner()
    assert run_phase(directory, runner)["status"] == "blocked"
    assert runner.count == 0
    spending(directory)
    assert preflight(directory)["status"] == "ready"


@pytest.mark.parametrize("mode", ["invalid", "truncated", "timeout", "interrupt"])
def test_interruptions_invalid_and_truncation_stop_and_resume_keep_usage(pair, mode):
    directory = pair["calibration"]
    human_ready(directory)
    if mode == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            run_phase(directory, Runner(mode))
    else:
        assert run_phase(directory, Runner(mode))["status"] == "blocked"
    assert usage(directory)["calls"] == 1
    assert evaluate_phase(directory)["status"] == "blocked"
    retry = Runner()
    result = run_phase(directory, retry)
    assert result["status"] == "blocked"
    assert retry.count == 0
    if mode in {"invalid", "truncated"}:
        result = run_phase(directory, retry, retry=True)
        assert result["status"] == "outputs_complete_pending_audit"
        assert usage(directory)["calls"] == 13
        assert usage(directory)["retry_calls"] == 1
    else:
        assert usage(directory)["pending"]
        assert run_phase(directory, retry, retry=True)["status"] == "blocked"
        assert retry.count == 0


def test_unknown_usage_keeps_reservations_and_resume_reuses_only_same_phase(pair):
    directory = pair["calibration"]
    human_ready(directory)
    runner = Runner(usage_known=False)
    run_phase(directory, runner)
    before = usage(directory)
    assert len(before["unknown_usage"]) == 12
    assert before["output_tokens"] == 12 * 4096
    run_phase(directory, runner)
    assert runner.count == 12
    assert usage(directory) == before
    assert evaluate_phase(directory)["status"] == "pilot_diagnostic"


@pytest.mark.parametrize(
    "mutation", ["source", "selection", "configuration", "method", "gold", "taxonomy"]
)
def test_changed_sources_methods_and_decisions_invalidate_approval(pair, mutation, monkeypatch):
    directory = pair["calibration"]
    human_ready(directory)
    if mutation == "source":
        Path(next(iter(package(directory).values()))["clean_file"]).write_text("changed source")
    elif mutation == "selection":
        path = pair["prep"] / "scoped-selections/fixture/selection.jsonl"
        path.write_text(path.read_text() + "\n")
    elif mutation == "configuration":
        path = directory / "dashboard-config.json"
        path.write_text(path.read_text() + "\n")
    elif mutation == "method":
        monkeypatch.setattr("degent.phase_review.method_signature", lambda identity: "new-prompt")
    elif mutation == "gold":
        paths = sorted((directory / "decisions").glob("*.json"))
        path = paths[1]
        path.write_text(path.read_text().replace('"SQL required."', '"changed"'))
    else:
        tax = read_json(directory / "taxonomy.json")
        tax["skills"] = {"new": {"approved": True}}
        write_json(directory / "taxonomy.json", tax)
    assert preflight(directory)["status"] == "blocked"
    runner = Runner()
    assert run_phase(directory, runner)["status"] == "blocked"
    assert runner.count == 0


def test_no_unapproved_or_heldout_documents_in_pilot_reviews(pair):
    directory = pair["calibration"]
    held = next(iter(package(pair["evaluation"]).values()))
    with pytest.raises(ValueError, match="unapproved"):
        review(directory, {**envelope(directory, "labels"), "documents": [held]})
    payload = read_json(directory / "scope-template.json")
    payload.update(envelope(directory, "scope"))
    with pytest.raises(ValueError, match="Explicit scope|Unresolved"):
        review(directory, payload)


def test_retry_total_and_per_document_caps_are_cumulative(pair):
    directory = pair["calibration"]
    human_ready(directory)
    run_phase(directory, Runner("invalid"))
    run_phase(directory, Runner("invalid"), retry=True)
    assert usage(directory)["calls"] == 2
    runner = Runner()
    assert run_phase(directory, runner, retry=True)["status"] == "blocked"
    assert runner.count == 0
    with pytest.raises(ValueError, match="reset|extended"):
        spending(directory)


@pytest.mark.parametrize("bad", ["expensive", "old", "unbound", "increased"])
def test_spending_rates_binding_and_caps_rejected(pair, bad):
    directory = pair["calibration"]
    human_ready(directory, authorize=False)
    changes = {}
    if bad in {"expensive", "old"}:
        changes["pricing"] = {
            "verified_at": "2020-01-01T00:00:00Z" if bad == "old" else utc_now(),
            "source_url": "https://openrouter.ai/openai/gpt-6-luna",
            "model": "openai/gpt-6-luna",
            "input_per_million": 100 if bad == "expensive" else 0.2,
            "output_per_million": 100 if bad == "expensive" else 0.75,
        }
    elif bad == "unbound":
        changes["execution_manifest_sha256"] = "wrong"
    else:
        changes["caps"] = {**read_json(directory / "phase-manifest.json")["caps"], "calls": 17}
    with pytest.raises(ValueError):
        spending(directory, **changes)
    assert run_phase(directory, Runner())["status"] == "blocked"


def test_phase3_requires_phase2_completion_then_separate_40_evaluation(pair):
    with pytest.raises(ValueError, match="prior-phase|Phase 2"):
        human_ready(pair["evaluation"])
    complete_calibration(pair["calibration"])
    # Scope, labels, taxonomy and exact evaluation approval were recorded, but no allowance.
    human_ready(pair["evaluation"])
    runner = Runner()
    assert run_phase(pair["evaluation"], runner)["documents"] == 40
    result = evaluate_phase(pair["evaluation"])
    assert result["status"] == "passed"
    assert result["cohorts"]["original_scoped"]["documents"] == 19
    assert result["cohorts"]["supplement"]["documents"] == 21
    assert result["broad_original_40"] == "unavailable"
    assert result["phase4_enabled"] is False
    assert runner.count == 40


def test_labels_cannot_be_revised_after_output_exposure(pair):
    directory = pair["calibration"]
    human_ready(directory)
    run_phase(directory, Runner())
    payload = read_json(directory / "labels-template.json")
    payload.update(envelope(directory, "labels"))
    with pytest.raises(ValueError, match="after dispatch"):
        review(directory, payload)


def test_phase_only_guards_collection_and_broad_actions():
    from degent.collection import collect, extend, retry_failed
    from degent.extraction_run import extract_run, refresh_observations
    from degent.study import configure_model, extract, prepare
    from degent.workflow import run_pipeline

    config = {"phase_only": True}
    actions = [
        lambda: collect(config, "p"),
        lambda: extend(config, "p", {}),
        lambda: retry_failed(config, "p"),
        lambda: extract_run(config, "p", False, None),
        lambda: refresh_observations(Path("."), config),
        lambda: configure_model(config, "p", "openai/gpt-6-luna"),
        lambda: extract(config, "p"),
        lambda: prepare(config, "p"),
        lambda: run_pipeline(config, "p"),
    ]
    for action in actions:
        with pytest.raises(ValueError, match="Isolated"):
            action()


def test_four_retries_total_stop_before_a_fifth_retry(pair):
    directory = pair["calibration"]
    human_ready(directory)

    class Alternating(Runner):
        def extract(self, request):
            self.mode = "invalid" if self.count % 2 == 0 else "valid"
            return super().extract(request)

    runner = Alternating()
    run_phase(directory, runner)
    for _ in range(4):
        run_phase(directory, runner, retry=True)
    assert usage(directory)["retry_calls"] == 4
    assert runner.count == 9
    result = run_phase(directory, runner, retry=True)
    assert result["status"] == "blocked"
    assert runner.count == 9


@pytest.mark.parametrize(
    "key,value",
    [
        ("calls", 15),
        ("input_tokens", 100),
        ("output_tokens", 4096),
        ("cost_usd", 0.00001),
        ("documents", 11),
    ],
)
def test_conservative_reservations_must_fit_every_separate_ceiling(pair, key, value):
    directory = pair["calibration"]
    human_ready(directory, authorize=False)
    caps = {**read_json(directory / "phase-manifest.json")["caps"], key: value}
    with pytest.raises(ValueError, match="exceeds allowance"):
        spending(directory, caps=caps)
    assert usage(directory)["calls"] == 0


def test_failed_heldout_result_is_frozen_and_fresh_revision_cannot_reuse_it(pair, monkeypatch):
    complete_calibration(pair["calibration"])
    human_ready(pair["evaluation"])
    runner = Runner("wrong")
    run_phase(pair["evaluation"], runner)
    first = evaluate_phase(pair["evaluation"])
    assert first["status"] == "failed"
    assert evaluate_phase(pair["evaluation"]) == first
    run_phase(pair["evaluation"], runner)
    assert runner.count == 40
    from degent.phases import implementation

    old = implementation()
    monkeypatch.setattr("degent.phases.implementation", lambda: {**old, "revision": "next"})
    with pytest.raises(ValueError, match="already been exposed"):
        prepare_phases(pair["prep"], pair["evaluation"].parent)


def test_original_evidence_and_configs_remain_unchanged(pair):
    protected = {p: file_hash(p) for p in pair["prep"].rglob("*") if p.is_file()}
    for directory in (pair["calibration"], pair["evaluation"]):
        reports(directory)
        evaluate_phase(directory)
    assert all(file_hash(p) == expected for p, expected in protected.items())


def test_phase_guard_detects_manifest_even_with_broad_config(pair):
    from degent.collection import collect
    from degent.sources import import_postings

    config = {"run_dir": str(pair["calibration"].parent)}
    with pytest.raises(ValueError, match="Isolated"):
        collect(config, pair["calibration"].name)
    with pytest.raises(ValueError, match="Isolated"):
        import_postings(config, pair["calibration"].name, Path("unapproved.json"))


def test_manual_provider_reconciliation_does_not_refund_or_reset_unknown_dispatch(pair):
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
                "generation_id": "test-terminal-generation",
                "model": "openai/gpt-6-luna",
                "source_url": "https://openrouter.ai/activity",
                "dispatch_terminal": True,
                "outcome": "terminal_no_output",
            },
        },
    )
    after = usage(directory)
    assert not after["pending"]
    for key in ("calls", "input_tokens", "output_tokens", "cost_usd"):
        assert before[key] == after[key]
    assert run_phase(directory, Runner())["status"] == "blocked"
    assert run_phase(directory, Runner(), retry=True)["status"] == "outputs_complete_pending_audit"
    assert usage(directory)["calls"] == 13
    assert usage(directory)["retry_calls"] == 1
    assert len(usage(directory)["unknown_usage"]) == 1


def test_heldout_inherits_frozen_skills_with_calibration_evidence(pair):
    fixed = package(pair["calibration"])["d0"]
    skills = {
        "custom_fixture": {
            "name": "Fixture capability",
            "definition": "Explicit fixture skill boundary.",
            "category": "technical",
            "supporting_evidence": [
                {
                    "document_id": "d0",
                    "source_sha256": fixed["source_sha256"],
                    "quote": "SQL required.",
                }
            ],
        }
    }
    complete_calibration(pair["calibration"], skills=skills)
    human_ready(pair["evaluation"])
    assert preflight(pair["evaluation"])["status"] == "ready"


def test_legacy_collection_entry_points_cannot_touch_phase_runs(pair):
    from degent.cli import discover, fetch
    from degent.sources import collect_current

    config = {"run_dir": str(pair["calibration"].parent)}
    for action in (discover, fetch, collect_current):
        with pytest.raises(ValueError, match="Isolated"):
            action(config, pair["calibration"].name)


def test_blocked_phase_does_not_read_credentials(pair, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Credentials must not be read during preflight")

    monkeypatch.setattr("degent.phase_inference.OpenRouterRunner", unexpected)
    assert run_phase(pair["calibration"])["status"] == "blocked"


def test_shortened_candidate_manifest_cannot_reduce_required_pilot(pair):
    directory = pair["calibration"]
    manifest = read_json(directory / "phase-manifest.json")
    manifest["document_ids"] = manifest["document_ids"][:-1]
    write_json(directory / "phase-manifest.json", manifest)
    assert preflight(directory)["status"] == "blocked"
    assert "Exactly 12" in preflight(directory)["blockers"][0]


def test_identical_calibration_text_needs_independent_labels_before_pilot(pair):
    from degent.phase_review import label_targets

    directory = pair["calibration"]
    assert len(label_targets(directory)) == 13
    assert "d3" in label_targets(directory)
    human_ready(directory)
    run_phase(directory, Runner())
    fixed = package(directory)["d3"]
    payload = {
        **envelope(directory, "labels"),
        "documents": [
            {
                "document_id": "d3",
                "source_sha256": fixed["source_sha256"],
                "labels": [label(fixed)],
                "independent_full_text": True,
                "omission_audited": True,
                "conventions_reviewed": True,
                "selector_omissions": [],
            }
        ],
    }
    with pytest.raises(ValueError, match="after dispatch"):
        review(directory, payload)
