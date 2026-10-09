from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .config import observation_in_period, periods
from .review import effective_extractions
from .store import digest, read_json, read_jsonl


def build_report(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    if config.get("study_version") == 1:
        from .study_metrics import report
        return report(run_dir, config)
    observations = read_jsonl(run_dir / "observations.jsonl")
    requirements = effective_extractions(run_dir)
    manifest = read_json(run_dir / "manifest.json") if (run_dir / "manifest.json").exists() else {}
    config = {**config, "periods": list({p["id"]: p for p in [*config["periods"], *manifest.get("observation_periods", [])]}.values())}
    candidates = [o for o in observations if o.get("eligible") and o.get("body_complete") and o.get("eligible_for_primary", True) and o.get("capture_date")]
    archive_rows = read_jsonl(run_dir / "archive-records.jsonl")
    matrix = read_json(run_dir / "coverage-matrix.json") if (run_dir / "coverage-matrix.json").exists() else []
    period_rows = []
    for period in periods(config):
        items = [o for o in candidates if observation_in_period(o, period)]
        distinct: dict[str, dict[str, Any]] = {}
        for observation in items:
            vacancy_id = observation.get("vacancy_id") or observation["document_id"]
            if vacancy_id not in distinct or observation.get("capture_at", "") > distinct[vacancy_id].get("capture_at", ""):
                distinct[vacancy_id] = observation
        statuses = [row for row in matrix if row.get("period_id") == period["id"]]
        if statuses and all(row.get("inventory_status") == "uninspected" for row in statuses):
            inspection = "uninspected"
        elif any(row.get("inventory_status") == "partially_inspected" for row in statuses):
            inspection = "partially_inspected"
        elif any(row.get("blocked_queries", 0) for row in statuses):
            inspection = "partly_blocked"
        elif statuses and all(row["status"] in {"inspected_empty", "partial_no_collection"} for row in statuses):
            inspection = "inspected_empty_or_no_collection"
        elif statuses:
            inspection = "records_found" if any(row["records_found"] for row in statuses) else "inspected"
        else:
            inspection = "uninspected"
        window_days = (date.fromisoformat(period["end"]) - date.fromisoformat(period["start"])).days + 1
        period_rows.append({"period": period["id"], "kind": period["kind"], "window_days": window_days, "archive_inspection": inspection, "archive_records": sum(row.get("records_found", 0) for row in statuses), "complete_eligible_descriptions": len(distinct), "status": "observed" if distinct else "no_eligible_complete_descriptions"})
    eligible_documents = {o["document_id"] for o in candidates}
    by_doc = {r.get("document_id"): r for r in requirements
              if r.get("accepted") and r.get("document_id") in eligible_documents}
    for obs in candidates:
        record = by_doc.get(obs["document_id"])
        if record and obs.get("clean_file"):
            path = Path(obs["clean_file"])
            if not path.is_file() or digest(path.read_bytes()) != record.get("source_sha256"):
                by_doc.pop(obs["document_id"], None)
    by_vacancy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for obs in candidates:
        by_vacancy[obs.get("vacancy_id") or obs["document_id"]].append(obs)
    changes = []
    for vacancy_id, items in by_vacancy.items():
        ordered = sorted(items, key=lambda row: row.get("capture_at") or "")
        for before, after in zip(ordered, ordered[1:]):
            a, b = by_doc.get(before["document_id"]), by_doc.get(after["document_id"])
            if not a or not b:
                continue
            from .review import document_reviewed
            if config.get("comparison_review", "accepted") == "reviewed" and not (document_reviewed(a) and document_reviewed(b)):
                continue
            if a.get("method_signature", "legacy") != b.get("method_signature", "legacy"):
                continue
            if before.get("evidence_basis", "archive_capture") != after.get("evidence_basis", "archive_capture"):
                continue
            requirement_changes = _requirement_changes(a.get("requirements", []), b.get("requirements", []))
            if requirement_changes["added"] or requirement_changes["no_longer_stated"] or requirement_changes["modified"]:
                changes.append({"vacancy_id": vacancy_id, "before_observation_id": before["observation_id"], "after_observation_id": after["observation_id"], "before_capture": before.get("capture_at"), "after_capture": after.get("capture_at"), "before_source_url": before.get("archive_url") or before.get("original_url"), "after_source_url": after.get("archive_url") or after.get("original_url"), **requirement_changes, "edit_date": None})
    requirement_shares = _requirement_shares(candidates, by_doc, config)
    from .comparisons import requirement_shares as calculate_shares
    views = {name: calculate_shares(candidates, by_doc, {**config, "comparison_status": name})
             for name in ("required", "preferred", "required_or_preferred")}
    combinations = calculate_shares(candidates, by_doc, config, mode="combination")
    wording = calculate_shares(candidates, by_doc, config, mode="wording")
    attempts = manifest.get("body_attempts", [])
    next_query = _next_targeted_query(archive_rows, attempts)
    if candidates and not archive_rows:
        next_query['recommendation'] = 'Current or imported descriptions passed the source gates. Review their statements before comparing. Historical archive coverage remains unestablished.'
    retrieval_summary = {"unique_body_attempts": len(attempts), "attempt_status_counts": dict(Counter(row.get("status", "unknown") for row in attempts)), "attempt_reason_counts": dict(Counter(row.get("reason", "none") for row in attempts)), "observation_body_status_counts": dict(Counter(row.get("body_status", "unknown") for row in observations)), "role_classification_counts": dict(Counter(row.get("role_classification", {}).get("reason", "unknown") for row in observations)), "geography_status_counts": dict(Counter(row.get("geography_status", "unknown") for row in observations)), "budget_reallocation": manifest.get("body_budget_reallocation"), "eligible_complete_observations": len(candidates)}
    from .sources import current_selections
    screened = current_selections(run_dir)
    retrieval_summary.update(
        collection_sessions=manifest.get("collection_sessions", []),
        current_titles_screened=len(screened),
        current_screening_status_counts=dict(Counter(r["status"] for r in screened)),
        current_detail_attempts=len(manifest.get("current_body_attempts", [])),
        current_requests_used=manifest.get("current_requests_used", 0),
        current_documents_saved=manifest.get("current_documents_saved", 0),
        current_request_cap=config.get("current_request_cap"),
        current_document_cap=config.get("current_document_cap"),
        current_source_outcomes={key: {k: v for k, v in info.items() if k != "decisions"}
                                 for key, info in manifest.get("current_sources", {}).items()},
    )
    report = {"run_id": run_dir.name, "coverage": period_rows, "employer_period_coverage": matrix, "retrieval_summary": retrieval_summary, "same_vacancy_changes": changes, "requirement_shares": requirement_shares, "comparison_views": views, "requirement_combinations": combinations, "wording_shares": wording, "comparison_settings": {k: config.get(k) for k in ("comparison_review", "comparison_status", "comparison_scope")}, "accepted_requirement_documents": len(by_doc), "next_targeted_query": next_query, "limitations": ["Archive sample coverage is not the employer's full vacancy population.", "Publication dates, capture dates, and retrieval dates are distinct.", "No trend is inferred for periods without eligible observations.", "Percentages are displayed only for groups with at least five assessed distinct vacancies in a period; this is a display gate, not a representativeness claim.", "Automated extraction produces candidates. The saved comparison settings state whether human review is required.", "Incompatible period lengths or extraction versions never receive percentage-point changes.", "Current captures and user-attested imports are distinct evidence groups. Undated imports are excluded from time comparisons."]}
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (run_dir / "coverage.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["period", "kind", "window_days", "archive_inspection", "archive_records", "complete_eligible_descriptions", "status"])
        writer.writeheader(); writer.writerows(period_rows)
    _write_flat_csv(run_dir / "requirement-shares.csv", requirement_shares)
    _write_flat_csv(run_dir / "requirement-combinations.csv", combinations)
    _write_flat_csv(run_dir / "wording-shares.csv", wording)
    _write_flat_csv(run_dir / "observations.csv", observations)
    _write_flat_csv(run_dir / "requirements.csv", requirements)
    _write_flat_csv(run_dir / "same-vacancy-changes.csv", changes, ["vacancy_id", "before_observation_id", "after_observation_id", "before_capture", "after_capture", "before_source_url", "after_source_url", "added", "no_longer_stated", "modified", "edit_date"])
    _write_flat_csv(run_dir / "employer-period-coverage.csv", matrix, ["employer_id", "period_id", "period_kind", "inventory_status", "queries_completed", "expected_queries", "blocked_queries", "records_found", "status"])
    lines = ["# Historical fintech job-requirement evidence report", "", f"Run: `{run_dir.name}`", "", "## Coverage", "", "| Period | Kind | Archive inspection | Index records | Eligible complete descriptions | Status |", "|---|---|---|---:|---:|---|"]
    lines.extend(f"| {r['period']} | {r['kind']} | {r['archive_inspection']} | {r['archive_records']} | {r['complete_eligible_descriptions']} | {r['status']} |" for r in period_rows)
    extraction_note = "No extraction or comparison is reported because no complete eligible body passed the body, role, and US-scope gates." if not candidates else "Eligible bodies were preserved; only evidence-validated accepted extractions contribute to the requirement comparisons."
    cap_note = "The initial unique-body-attempt cap is exhausted; this targeted query is a next-experiment recommendation, not an additional retrieval in this run." if len(attempts) >= int(config["body_attempt_cap"]) else ""
    lines += ["", "## Retrieval and role gate", "", f"Unique body attempts: {len(attempts)} / {config['body_attempt_cap']}. Complete eligible observations after body, role, and US-scope validation: {len(candidates)}. {extraction_note}", "", "Attempt outcomes: " + (", ".join(f"`{key}` {value}" for key, value in sorted(retrieval_summary["attempt_reason_counts"].items())) or "none") + ".", "", "Observation body checks: " + (", ".join(f"`{key}` {value}" for key, value in sorted(retrieval_summary["observation_body_status_counts"].items())) or "none") + ".", "", "Geography checks: " + (", ".join(f"`{key}` {value}" for key, value in sorted(retrieval_summary["geography_status_counts"].items())) or "none") + ".", "", "## Same-vacancy comparisons", ""]
    if changes:
        lines.append("The comparison is between dated captures of the same source-scoped vacancy. It does not identify when an edit occurred.")
        lines += ["", "| Vacancy | Capture pair | Requirement changes |", "|---|---|---|"]
        observation_by_id = {item.get("observation_id"): item for item in candidates}
        for change in changes:
            before = observation_by_id.get(change.get("before_observation_id"), {})
            label = _md_cell(f"{before.get('employer', before.get('employer_id', 'unknown'))} / {before.get('title', change.get('vacancy_id'))}")
            pair = _md_cell(f"{change.get('before_capture')} → {change.get('after_capture')}")
            details = []
            for item in change.get("added", []):
                details.append(f"Added **{_md_cell(item.get('concept'))}** ({item.get('status')}); quote: “{_md_cell(item.get('evidence'))}”")
            for item in change.get("no_longer_stated", []):
                details.append(f"No longer stated: **{_md_cell(item.get('concept'))}** ({item.get('status')}); quote: “{_md_cell(item.get('evidence'))}”")
            for item in change.get("modified", []):
                fields_changed = ", ".join(f"{field}: {value.get('before')} → {value.get('after')}" for field, value in item.get("fields", {}).items())
                details.append(f"**{_md_cell(item.get('concept'))}** changed ({_md_cell(fields_changed)}); “{_md_cell(item.get('before_evidence'))}” → “{_md_cell(item.get('after_evidence'))}”")
            details.append(f"[before source]({change.get('before_source_url')}) · [after source]({change.get('after_source_url')})")
            lines.append(f"| {label} | {pair} | {'<br>'.join(details)} |")
    else:
        lines.append("No accepted same-vacancy requirement changes were found in the eligible evidence.")
    share_rows = [row for row in requirement_shares if row.get("share_percent") is not None]
    lines += ["", "## Comparable-vacancy requirements", ""]
    if share_rows:
        lines += ["Percentages use distinct vacancies assessed for this concept under the saved review policy. Missing extraction, pending review, and unresolved wording do not count as absence. Employers, role tracks, subtypes, levels, and date evidence stay separate.", "", "| Employer | Role track / subtype | Level | Period | Concept | Count / assessed | Share | Change |", "|---|---|---|---|---|---:|---:|---:|"]
        for row in share_rows:
            change = "—" if row.get("difference_percentage_points") is None else f"{row['difference_percentage_points']:+.1f} pp"
            lines.append(f"| {_md_cell(row.get('brand') or row.get('employer_id'))} | {_md_cell(row.get('role_track'))} / {_md_cell(row.get('role_subtype'))} | {_md_cell(row.get('level'))} | {row.get('period')} ({row.get('period_kind')}) | {_md_cell(row.get('concept'))} | {row.get('count')} / {row.get('assessed_vacancies')} | {row.get('share_percent')}% | {change} |")
    else:
        lines.append(f"No percentages met the minimum of five distinct extracted vacancies in a group. {len(requirement_shares)} suppressed or unavailable period rows are in `requirement-shares.csv`.")
    lines += ["", "## Interpretation", "", "Comparison settings: " + json.dumps(report["comparison_settings"]),
              "", "Review status, unresolved counts, period compatibility, source basis, and method versions are included in CSV/JSON outputs.",
              f"Selected combinations: {len(combinations)} rows. Exact wording: {len(wording)} rows."]
    lines += ["", "## Next targeted query", "", next_query["recommendation"], "", cap_note, "", f"Target capture: `{next_query.get('period_id')}` — {next_query.get('url')}", "", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    (run_dir / "evidence-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _next_targeted_query(archive_records: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    role_pattern = re.compile(r"fraud-analytics|fraud-strategy|credit-risk-analyst|credit-strategy-analyst|quantitative-analyst|model-validation|capital-analytics", re.I)
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in archive_records:
        url = str(row.get("original_url", ""))
        if role_pattern.search(url) and row.get("source_record", {}).get("status") == "200":
            by_url[url].append(row)
    attempted = {row.get("record_key") for row in attempts}
    candidates = []
    for url, rows in by_url.items():
        periods_observed = {row.get("period_id") for row in rows}
        if len(periods_observed) < 2:
            continue
        pending = []
        for row in rows:
            key = digest(json.dumps([row.get("source"), row.get("capture_at"), row.get("original_url")]))
            if key not in attempted:
                pending.append(row)
        if pending:
            candidates.append(("credit-strategy-analyst" not in url.lower(), url, pending, rows))
    if candidates:
        _, url, pending, rows = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
        target = sorted(pending, key=lambda row: row.get("capture_at", ""))[-1]
        return {"employer_id": target.get("employer_id"), "period_id": target.get("period_id"), "url": url, "archive_source": target.get("source"), "capture_at": target.get("capture_at"), "recommendation": "Retrieve the remaining indexed capture for this exact US credit-strategy posting, validate that the archived body contains substantive requirements and responsibilities, and compare it only if the other capture also passes the same body gate."}
    return {"employer_id": None, "period_id": None, "url": None, "archive_source": None, "capture_at": None, "recommendation": "The bounded sample did not produce a complete eligible description. Next, target exact US role-slug postings and their archived ATS detail/API routes; verify a nonempty job description before increasing model processing or making comparisons."}


def _requirement_shares(candidates: list[dict[str, Any]], by_doc: dict[str, dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    from .comparisons import requirement_shares
    return requirement_shares(candidates, by_doc, config)


def _requirement_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def identity(row: dict[str, Any]) -> tuple[str, str, str]:
        concept = row.get("concept") or re.sub(r"\W+", "_", str(row.get("phrase", "")).lower()).strip("_")
        return (str(row.get("category", "unknown")), str(concept), str(row.get("section") or ""))

    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {identity(row): row for row in rows if row.get("explicit") and not row.get("excluded") and row.get("statement_scope", "qualification") in {"qualification", "responsibility"}}

    left, right = index(before), index(after)
    added = []
    removed = []
    modified = []
    tracked = ("status", "experience_years", "alternatives", "qualifier", "negated", "explicit")
    for key in sorted(right.keys() - left.keys()):
        row = right[key]
        added.append({"concept": key[1], "status": row.get("status"), "phrase": row.get("phrase"), "evidence": row.get("evidence")})
    for key in sorted(left.keys() - right.keys()):
        row = left[key]
        removed.append({"concept": key[1], "status": row.get("status"), "phrase": row.get("phrase"), "evidence": row.get("evidence")})
    for key in sorted(left.keys() & right.keys()):
        old, new = left[key], right[key]
        field_changes = {field: {"before": old.get(field), "after": new.get(field)} for field in tracked if old.get(field) != new.get(field)}
        if field_changes:
            modified.append({"concept": key[1], "before_phrase": old.get("phrase"), "after_phrase": new.get("phrase"), "before_evidence": old.get("evidence"), "after_evidence": new.get("evidence"), "fields": field_changes})
    return {"added": added, "no_longer_stated": removed, "modified": modified}


def _write_flat_csv(path: Path, records: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    keys = fieldnames or sorted({key for row in records for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        if not keys:
            stream.write("")
            return
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in record.items()})


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")
