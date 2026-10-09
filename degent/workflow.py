"""One local research run; uncertain evidence stops at a visible review checkpoint."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .review import document_reviewed, effective_extractions
from .config import observation_in_period
from .sources import collect_current, import_postings
from .store import append_jsonl, read_json, read_jsonl, utc_now, write_json


def audit_evidence(config: dict, run_id: str) -> dict:
    if config.get("study_version") == 1:
        from .study import prepare
        from .study_metrics import report
        directory = Path(config["run_dir"]) / run_id
        prepare(config, run_id)
        result = report(directory, config)
        summary = {"eligible_distinct": result["distinct_vacancies"], "unassigned": result["unassigned_vacancies"],
                   "status": result["status"], "historical_wording_evidence": 0,
                   "automation_gate": result["automation_gate"]}
        write_json(directory / "feasibility.json", summary)
        return summary
    from .cli import run_directory

    directory = run_directory(config, run_id)
    from .extraction_run import refresh_observations
    from .sources import current_selections
    rows = refresh_observations(directory, config)
    manifest = read_json(directory / "manifest.json") if (directory / "manifest.json").exists() else {}
    reporting_periods = list({p["id"]: p for p in [*config["periods"], *manifest.get("observation_periods", [])]}.values())
    reasons = Counter(r.get("body_status", "unknown") for r in rows)
    cells = []
    for employer in config["employers"]:
        for period in reporting_periods:
            found = [
                r
                for r in rows
                if r.get("employer_id") == employer["id"]
                and observation_in_period(r, period)
            ]
            complete = [r for r in found if r.get("body_complete") and r.get("eligible")]
            n = len({r.get("vacancy_id") or r["document_id"] for r in complete})
            cells.append(
                {
                    "employer_id": employer["id"],
                    "period_id": period["id"],
                    "retrieved": len(found),
                    "eligible_distinct": n,
                    "decision": (
                        "descriptive_sample" if n >= 5 else "counts_only" if n else "no_evidence"
                    ),
                }
            )
    samples = []
    for reason in reasons:
        for row in [r for r in rows if r.get("body_status") == reason][:3]:
            samples.append(
                {
                    k: row.get(k)
                    for k in (
                        "observation_id",
                        "document_id",
                        "title",
                        "original_url",
                        "body_status",
                        "geography_status",
                        "role_classification",
                        "clean_file",
                    )
                }
            )
    result = {
        "run_id": run_id,
        "checked_at": utc_now(),
        "counts": dict(reasons),
        "current_screening_counts": dict(Counter(r["status"] for r in current_selections(directory))),
        "current_sources": {key: {k: v for k, v in info.items() if k != "decisions"}
                            for key, info in (read_json(directory / "manifest.json").get("current_sources", {}) if (directory / "manifest.json").exists() else {}).items()},
        "cells": cells,
        "review_samples": samples[:20],
        "historical_evidence": sum(
            r.get("body_complete", False) and r.get("source") in {"wayback", "commoncrawl"}
            for r in rows
        ),
        "recommendation": "Review failures and scope exclusions before increasing retrieval. Use current feeds/imports when dated historical bodies remain unavailable.",
        "limits": "Five is a display gate only. These are collected samples, not the whole job market.",
    }
    write_json(directory / "feasibility.json", result)
    return result


def run_pipeline(config: dict, run_id: str, use_codex: bool = False) -> dict:
    if config.get("study_version") == 1:
        from .study import run
        return run(config, run_id)
    from .cli import discover, extract, fetch, manifest_for, report, run_directory, save_manifest

    directory = run_directory(config, run_id)
    sources = config.get("sources", ["archives"])
    operations = []
    if "current" in sources:
        operations.append(("current", lambda: collect_current(config, run_id)))
    if "archives" in sources:
        operations.extend(
            [
                ("discover", lambda: discover(config, run_id)),
                ("fetch", lambda: fetch(config, run_id)),
            ]
        )
    operations.extend(
        [
            ("audit", lambda: audit_evidence(config, run_id)),
            ("extract", lambda: extract(config, run_id, use_codex)),
            ("report", lambda: report(config, run_id)),
        ]
    )
    results = {}
    for name, operation in operations:
        manifest = manifest_for(directory, run_id)
        manifest["workflow"] = {"stage": name, "status": "running", "updated_at": utc_now()}
        save_manifest(directory, manifest)
        append_jsonl(
            directory / "activity.jsonl", [{"at": utc_now(), "stage": name, "action": "Started"}]
        )
        results[name] = operation()
        append_jsonl(
            directory / "activity.jsonl",
            [{"at": utc_now(), "stage": name, "action": "Completed", "result": results[name]}],
        )
        if name == "current" and config.get("collection_version") == 2 and results[name].get("status") not in {"complete", "bounded"}:
            audit_evidence(config, run_id)
            manifest = manifest_for(directory, run_id)
            manifest["workflow"] = {"stage": "current", "status": results[name]["status"], "pause_reason": results[name].get("pause_reason"), "updated_at": utc_now()}
            save_manifest(directory, manifest)
            return {"run_id": run_id, "status": results[name]["status"], "stages": results, "next_action": "Continue collection or inspect source issues. Extraction can be started independently."}
        if name == "extract" and results[name].get("pause_reason"):
            break
    manifest = manifest_for(directory, run_id)
    eligible_ids = {
        r["document_id"]
        for r in read_jsonl(directory / "observations.jsonl")
        if r.get("body_complete") and r.get("eligible")
    }
    records = [
        r
        for r in effective_extractions(directory)
        if r.get("accepted") and r["document_id"] in eligible_ids
    ]
    pause = results.get("extract", {}).get("pause_reason")
    if pause and pause.endswith("_limit"):
        status = "stopped_by_limit"
    elif pause:
        status = "waiting_for_provider"
    elif not eligible_ids:
        status = "no_evidence"
    elif len(records) < len(eligible_ids):
        status = "partial_extraction"
    elif any(not document_reviewed(r) for r in records):
        status = "needs_review"
    else:
        status = "complete"
    manifest["workflow"] = {
        "stage": "review" if status == "needs_review" else "report",
        "status": status,
        "updated_at": utc_now(),
        "pause_reason": pause,
    }
    save_manifest(directory, manifest)
    return {
        "run_id": run_id,
        "status": status,
        "stages": results,
        "next_action": (
            "Review source-linked statements, then run again to rebuild comparisons."
            if status == "needs_review"
            else status.replace("_", " ")
        ),
    }


def suggestions(config: dict, selected: list[str]) -> dict:
    companies = config.get("employers", [])
    groups = {c.get("group") for c in companies if c["id"] in selected and c.get("group") not in {None, "unknown"}}
    peers = [
        {
            "id": c["id"],
            "name": c["name"],
            "reason": "Same configured business group: " + c["group"].replace("-", " "),
            "sources": ["https://" + path.rstrip("*") for path in c.get("paths", [])[:1]],
            "needs_review": True,
        }
        for c in companies
        if c["id"] not in selected and c.get("group") in groups
    ][:5]
    roles = {
        "credit_risk_analytics": ["Credit Risk Analyst", "Credit Strategy Analyst"],
        "fraud_analytics_strategy": ["Fraud Analytics Analyst", "Fraud Strategy Analyst"],
        "quantitative_credit_risk_capital_modeling": [
            "Quantitative Analyst",
            "Model Validation Analyst",
            "Capital Analytics Analyst",
        ],
    }
    return {
        "companies": peers,
        "related_roles": roles,
        "limits": "Suggestions come from the configured catalog. Titles remain distinct; no company or role is added automatically.",
    }


def evaluate(config: dict, path: Path, output: Path) -> dict:
    from .extract import rule_candidates

    if path.stat().st_size > 5_000_000:
        raise ValueError("Evaluation dataset exceeds 5 MB.")
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data or len(data) > 100:
        raise ValueError("Provide 1–100 labeled examples.")
    correct = predicted = expected = 0
    cases = []
    for row in data:
        actual = {
            (r.get("concept"), r["status"], r["statement_scope"])
            for r in rule_candidates(row["id"], row["text"])["requirements"]
        }
        target = {(r["concept"], r["status"], r["statement_scope"]) for r in row["expected"]}
        hits = actual & target
        correct += len(hits)
        predicted += len(actual)
        expected += len(target)
        cases.append(
            {"id": row["id"], "missing": sorted(target - actual), "extra": sorted(actual - target)}
        )
    result = {
        "method": "rules",
        "examples": len(cases),
        "correct": correct,
        "predicted": predicted,
        "expected": expected,
        "precision": correct / predicted if predicted else None,
        "recall": correct / expected if expected else None,
        "cases": cases,
        "synthetic": any(r.get("synthetic", False) for r in data),
        "limitation": "This evaluates the supplied labels only. Synthetic fixtures do not establish accuracy on real employer descriptions; evaluate Codex using separately human-labeled source records.",
    }
    write_json(output, result)
    return result
