"""Offline, immutable multi-year content inventory; never dispatches inference.

Run ``python -m degent.multiyear --run PATH --policy PATH``. Every observation
survives, including undated content, thin years, earlier context and review cases.
The automated source screens are proposals, not resolved semantic adjudications.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .analysis_text import analysis_text, masked_text
from .career_evidence import career_evidence
from .openrouter import APPROVED_MODELS
from .scoped_selection import classify
from .store import digest, operation_lock, read_json, read_jsonl, write_json, write_jsonl

VERSION = "capital-one-multiyear-inventory-v1"
DATE_FIELDS = (
    "verified_historical_wording_at",
    "current_capture_at",
    "capture_at",
    "stated_publication_at",
    "stated_updated_at",
    "collector_first_observed_at",
)


def temporal_evidence(row: dict) -> dict:
    """Date observed wording separately from publication; modern retrieval is not history."""
    historical = row.get("collection_kind") == "historical"
    field = "verified_historical_wording_at" if historical else "current_capture_at"
    observed = row.get(field)
    if not historical and not observed:
        field, observed = "capture_at", row.get("capture_at")
    observed = _valid_date(observed)
    published = _valid_date(row.get("stated_publication_at"))
    conflict = bool(observed and published and published[:10] > observed[:10])
    return {
        "wording_observed_at": observed,
        "wording_date_basis": field if observed else None,
        "year": observed[:4] if observed else "undated",
        "month": observed[:7] if observed else None,
        "quarter": f"{observed[:4]}-Q{(int(observed[5:7]) - 1) // 3 + 1}" if observed else None,
        "publication_at": published,
        "publication_year": published[:4] if published else "unknown",
        "publication_month": published[:7] if published else None,
        "publication_after_observation": conflict,
        "retrieval_is_historical_wording_date": False,
        "original_date_assertions": {field: row.get(field) for field in DATE_FIELDS},
        "date_provenance": {
            key: row.get(key)
            for key in ("publication_date_source", "wording_date_evidence", "capture_date_source")
        },
    }


def _valid_date(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
            parts = [int(part) for part in value.split("-")]
            return datetime(*parts).date().isoformat()
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def geography_evidence(row: dict, text: str) -> dict:
    evidence = copy.deepcopy(row.get("location_assertions", []))
    if any(
        str(item.get("value", {}).get("addressCountry", "")).casefold()
        in {"us", "usa", "united states", "united states of america"}
        for item in evidence
    ):
        return {"status": "us_source_evidence", "evidence": evidence}
    for match in re.finditer(r"[^\n]+", text):
        if match.start() > sum(len(line) + 1 for line in text.splitlines()[:4]):
            break
        if re.search(
            r"United States of America|^Location:.*(?:United States|\bUSA\b|\bUS\b)", match.group()
        ):
            return {
                "status": "us_source_evidence",
                "evidence": [
                    {
                        "kind": "posting_header",
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(),
                    }
                ],
            }
        if re.search(r"\), (?:India|Mexico|Canada|United Kingdom|Philippines),", match.group()):
            return {
                "status": "outside_us",
                "evidence": [
                    {
                        "kind": "posting_header",
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(),
                    }
                ],
            }
    if row.get("geography_status") == "outside_us_scope":
        return {"status": "outside_us", "evidence": evidence}
    return {"status": "geography_unresolved", "evidence": evidence}


def _source_group(row: dict) -> str:
    """Group source identity assertions without claiming cross-source vacancy equivalence."""
    return (
        row.get("requisition_id")
        or row.get("source_posting_group")
        or row.get("vacancy_id")
        or row["document_id"]
    ).removeprefix("requisition:")


def inspect_observation(row: dict, text: str) -> tuple[dict, dict]:
    cleaned = analysis_text(text)
    masked = masked_text(text, cleaned["removed_spans"])
    scope = classify(copy.deepcopy(row), masked)
    geo = geography_evidence(row, text)
    if (
        scope["reason"] == "us_geography_requires_exact_posting_evidence"
        and geo["status"] == "us_source_evidence"
        and scope["functions"]
    ):
        scope.update(decision="include", reason="direct_domain_duties_with_preserved_us_location")
    career = career_evidence(masked)
    _restore_evidence(scope, text)
    _restore_evidence(career, text)
    temporal = temporal_evidence(row)
    status, reason = _disposition(scope, geo, career, text)
    if row.get("employer_status") == "conflicting_employer_assertion":
        status, reason = "review_scope", "conflicting_source_employer_assertion_content_retained"
    year = temporal["year"]
    record = {
        "observation_id": row["observation_id"],
        "document_id": row["document_id"],
        "title": row["title"],
        "source_file": row["clean_file"],
        "source_sha256": digest(text),
        "analysis_sha256": cleaned["analysis_sha256"],
        "source_identity_group": _source_group(row),
        "vacancy_id": row.get("vacancy_id"),
        "identity_status": row.get("identity_status"),
        "employer_status": row.get("employer_status", "capital_one_preserved_source"),
        "employer_source_assertion": row.get("employer_source_assertion"),
        "collection_kind": row.get("collection_kind"),
        "original_url": row.get("original_url"),
        "comparison_subset": row.get("comparison_subset"),
        "temporal": temporal,
        "cohort": (
            "2026"
            if year == "2026"
            else (
                "pre_2026"
                if year != "undated" and "2020" <= year < "2026"
                else "undated_content" if year == "undated" else "earlier_context"
            )
        ),
        "disposition": status,
        "reason": reason,
        "domain_screen": scope,
        "geography": geo,
        "career": career,
        "description_completeness": row.get("description_completeness", "unknown"),
        "selection_completeness": "not_yet_full_text_adjudicated",
        "reviewer": "offline_source_screen",
        "human_gold": False,
        "inference_authorized": False,
        "record_retained": True,
    }
    return record, cleaned


def _restore_evidence(value, text: str) -> None:
    """Classifiers see a removal mask; persisted evidence quotes the original source."""
    if isinstance(value, dict):
        if {"start", "end", "text"} <= value.keys():
            value["text"] = text[value["start"] : value["end"]]
        for item in value.values():
            _restore_evidence(item, text)
    elif isinstance(value, list):
        for item in value:
            _restore_evidence(item, text)


def _disposition(scope: dict, geo: dict, career: dict, text: str) -> tuple[str, str]:
    if geo["status"] == "outside_us":
        return "out_of_scope_preserved", "exact_non_us_posting_evidence"
    if scope["decision"] == "exclude":
        return "out_of_scope_preserved", scope["reason"]
    if not text.strip():
        return "review_content", "missing_description_content"
    if scope["decision"] != "include" or geo["status"] != "us_source_evidence":
        return "review_scope", scope["reason"]
    if career["status"] == "above_year_screen":
        return "review_career", "required_years_above_screen_requires_duty_and_route_review"
    if career["status"] == "people_leadership_requires_semantic_review":
        return "review_career", "distinguish_people_management_from_project_or_technical_leadership"
    if career["status"] == "experience_unstated":
        return "review_career", "retain_content_with_unstated_experience_for_semantic_review"
    return (
        "proposed_eligible",
        "direct_domain_duties_us_evidence_and_experience_route_within_screen",
    )


def _duplicates(records: list[dict]) -> dict:
    hashes, groups = defaultdict(list), defaultdict(list)
    for record in records:
        hashes[record["source_sha256"]].append(record["observation_id"])
        groups[record["source_identity_group"]].append(record)
    return {
        "identical_text_groups": [
            {"sha256": sha, "observations": ids}
            for sha, ids in sorted(hashes.items())
            if len(ids) > 1
        ],
        "source_identity_groups": [
            {
                "source_identity_group": group,
                "observations": [r["observation_id"] for r in items],
                "years": sorted({r["temporal"]["year"] for r in items}),
                "text_versions": len({r["source_sha256"] for r in items}),
                "cross_collection": len({r["collection_kind"] for r in items}) > 1,
                "identity_adjudicated_by_grouping": False,
            }
            for group, items in sorted(groups.items())
            if len(items) > 1
        ],
        "automatic_requisition_merges": 0,
        "discarded_observations": 0,
    }


def coverage(records: list[dict], minimum: int = 30) -> list[dict]:
    years = sorted({r["temporal"]["year"] for r in records} | {str(y) for y in range(2020, 2027)})
    result = []
    for year in years:
        rows = [r for r in records if r["temporal"]["year"] == year]
        eligible = [r for r in rows if r["disposition"] == "proposed_eligible"]
        groups = {r["source_identity_group"] for r in eligible}
        result.append(
            {
                "year": year,
                "observations": len(rows),
                "source_description_versions": len({r["document_id"] for r in rows}),
                "proposed_eligible_observations": len(eligible),
                "proposed_source_identity_groups": len(groups),
                "resolved_distinct_openings": None,
                "review_observations": sum(r["disposition"].startswith("review_") for r in rows),
                "out_of_scope_observations_retained": sum(
                    r["disposition"] == "out_of_scope_preserved" for r in rows
                ),
                "annual_target": minimum,
                "target_gap": (
                    max(0, minimum - len(groups))
                    if year.isdigit() and "2020" <= year <= "2026"
                    else None
                ),
                "months_observed": ",".join(
                    sorted({r["temporal"]["month"] for r in rows if r["temporal"]["month"]})
                ),
                "percentages_reported": False,
                "records_removed_for_small_sample": 0,
            }
        )
    return result


def _cross_coverage(records: list[dict]) -> dict:
    functions = Counter(
        (r["temporal"]["year"], r["cohort"], r["disposition"], function)
        for r in records
        for function in r["domain_screen"]["functions"]
    )
    careers = Counter(
        (r["temporal"]["year"], r["cohort"], r["disposition"], r["career"]["status"])
        for r in records
    )
    return {
        "year_and_cohort_by_function": [
            dict(zip(("year", "cohort", "disposition", "function"), key), observations=count)
            for key, count in sorted(functions.items())
        ],
        "year_and_cohort_by_career": [
            dict(zip(("year", "cohort", "disposition", "career_screen"), key), observations=count)
            for key, count in sorted(careers.items())
        ],
        "functions_overlap": True,
        "counts_are_observations_not_confirmed_openings": True,
    }


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _export(
    directory: Path, records: list[dict], texts: dict, manifest: dict, policy: dict
) -> dict:
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "policy.json", policy)
    write_jsonl(directory / "observations.jsonl", records)
    write_json(directory / "identity-audit.json", _duplicates(records))
    years = coverage(records, policy["annual_target"])
    write_json(directory / "coverage.json", years)
    _csv(directory / "coverage.csv", years)
    _csv(directory / "opening-inventory.csv", [_opening_row(r) for r in records])
    write_json(directory / "function-coverage.json", _cross_coverage(records))
    write_jsonl(
        directory / "case-review.jsonl",
        [r for r in records if r["disposition"].startswith("review_")],
    )
    for sha, view in texts.items():
        (directory / "analysis-text").mkdir(exist_ok=True)
        (directory / "analysis-text" / (sha + ".txt")).write_text(view["text"], encoding="utf-8")
        write_json(
            directory / "analysis-text" / (sha + ".json"),
            {k: v for k, v in view.items() if k != "text"},
        )
    report = _report(records, texts, years)
    write_json(directory / "report.json", report)
    (directory / "report.md").write_text(_markdown(report, years), encoding="utf-8")
    hashes = {
        str(p.relative_to(directory)): digest(p.read_bytes())
        for p in directory.rglob("*")
        if p.is_file()
    }
    write_json(
        directory / "receipt.json",
        {
            "manifest_sha256": digest((directory / "manifest.json").read_bytes()),
            "files_sha256": hashes,
            "model_calls": 0,
            "collection_calls": 0,
        },
    )
    return report


def _opening_row(record: dict) -> dict:
    return {
        "observation_id": record["observation_id"],
        "document_id": record["document_id"],
        "title": record["title"],
        "source_identity_group": record["source_identity_group"],
        "identity_status": record["identity_status"],
        "disposition": record["disposition"],
        "reason": record["reason"],
        "cohort": record["cohort"],
        "wording_observed_at": record["temporal"]["wording_observed_at"],
        "wording_year": record["temporal"]["year"],
        "wording_quarter": record["temporal"]["quarter"],
        "publication_at": record["temporal"]["publication_at"],
        "functions": ",".join(record["domain_screen"]["functions"]),
        "career_screen": record["career"]["status"],
        "source_sha256": record["source_sha256"],
        "source_file": record["source_file"],
        "description_completeness": record["description_completeness"],
        "inference_authorized": False,
    }


def _report(records: list[dict], texts: dict, years: list[dict]) -> dict:
    return {
        "version": VERSION,
        "status": "complete_inventory_semantic_adjudication_pending",
        "observations": len(records),
        "source_description_versions": len({r["document_id"] for r in records}),
        "dispositions": dict(Counter(r["disposition"] for r in records)),
        "career_screens": dict(Counter(r["career"]["status"] for r in records)),
        "cohorts": dict(Counter(r["cohort"] for r in records)),
        "wording_months": dict(
            sorted(Counter(r["temporal"]["month"] or "undated" for r in records).items())
        ),
        "publication_months": dict(
            sorted(
                Counter(r["temporal"]["publication_month"] or "unknown" for r in records).items()
            )
        ),
        "publication_after_observation": sum(
            r["temporal"]["publication_after_observation"] for r in records
        ),
        "boilerplate_spans_removed": sum(len(view["removed_spans"]) for view in texts.values()),
        "original_characters": sum(len(Path(r["source_file"]).read_text()) for r in records),
        "analysis_characters": sum(len(texts[r["source_sha256"]]["text"]) for r in records),
        "annual_coverage": years,
        "automatic_identity_merges": 0,
        "observations_discarded": 0,
        "model_calls": 0,
        "collection_calls": 0,
        "spending_enabled": False,
        "evaluation_packet_changed": False,
        "unknown_dates_reject_content": False,
        "absence_claims_supported": False,
        "method_frozen_for_inference": False,
    }


def _markdown(report: dict, years: list[dict]) -> str:
    lines = [
        "# Multiyear content inventory",
        "",
        "This is a source-backed automated proposal, not the final adjudicated opening set.",
        "Every observation survives. Unknown dates retain content; small samples retain records.",
        "",
        "Observed wording dates and publication dates are separate. Source identity groups are not confirmed unique openings.",
        "",
        "| Wording year | Observations | Proposed groups | Review observations | Target gap |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {r['year']} | {r['observations']} | {r['proposed_source_identity_groups']} | {r['review_observations']} | {r['target_gap'] if r['target_gap'] is not None else '—'} |"
        for r in years
    )
    lines.extend(
        [
            "",
            f"Dispositions: `{json.dumps(report['dispositions'], sort_keys=True)}`.",
            "",
            "All exact duty, qualification and removal spans refer to preserved original text. Preferred tenure is separate from required tenure.",
            "Experience thresholds and leadership cues require semantic review; titles do not decide eligibility.",
            "Positive evidence can survive partial descriptions. Unknown completeness cannot establish absence.",
            "The pilot/evaluation assignments and spending gates remain unchanged. No model or collection calls ran.",
            "",
        ]
    )
    return "\n".join(lines)


def build(run: Path, policy_path: Path, parent: Path, collections: tuple[Path, ...] = ()) -> dict:
    inputs = [
        run / "observations.jsonl",
        *(collection / "observations.jsonl" for collection in collections),
    ]
    if any(not path.is_file() for path in inputs):
        raise ValueError(
            "Input observation collection missing; cannot claim selection completeness."
        )
    input_hashes = {str(path.resolve()): digest(path.read_bytes()) for path in inputs}
    rows = [row for path in inputs for row in read_jsonl(path)]
    if len({r["observation_id"] for r in rows}) != len(rows):
        raise ValueError("Every input observation needs a unique preserved identity.")
    policy = read_json(policy_path)
    if not isinstance(policy.get("annual_target"), int) or policy["annual_target"] < 1:
        raise ValueError("An explicit positive annual coverage target is required.")
    method = {
        name: digest((Path(__file__).parent / name).read_bytes())
        for name in (
            "multiyear.py",
            "analysis_text.py",
            "career_evidence.py",
            "scoped_selection.py",
            "evidence.py",
        )
    }
    records, texts = [], {}
    for row in rows:
        text = Path(row["clean_file"]).read_text(encoding="utf-8")
        if digest(text) != row["clean_sha256"]:
            raise ValueError("Preserved source text changed: " + row["observation_id"])
        record, view = inspect_observation(row, text)
        records.append(record)
        texts[view["source_sha256"]] = view
    manifest = {
        "version": VERSION,
        "input_run": str(run.resolve()),
        "observations_sha256": digest((run / "observations.jsonl").read_bytes()),
        "input_collection_hashes": input_hashes,
        "prior_selection_index_sha256": (
            digest((run / "scoped-selection-index-v1.json").read_bytes())
            if (run / "scoped-selection-index-v1.json").is_file()
            else None
        ),
        "policy_sha256": digest(policy_path.read_bytes()),
        "method_files": method,
        "model_identities_retained": list(APPROVED_MODELS),
        "model_calls_authorized_by_manifest": False,
        "sources": [
            {
                "observation_id": r["observation_id"],
                "document_id": r["document_id"],
                "source_sha256": r["source_sha256"],
            }
            for r in records
        ],
    }
    signature = digest(json.dumps(manifest, sort_keys=True))[:16]
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("capital-one-multiyear-preparation-2026-10-09-" + signature)
    with operation_lock(parent / ".multiyear-preparation"):
        if destination.exists():
            if read_json(destination / "manifest.json") != manifest:
                raise ValueError("Immutable multiyear manifest changed.")
            for name, sha in read_json(destination / "receipt.json")["files_sha256"].items():
                if digest((destination / name).read_bytes()) != sha:
                    raise ValueError("Immutable multiyear inventory changed: " + name)
            report = read_json(destination / "report.json")
        else:
            with tempfile.TemporaryDirectory(prefix=".multiyear-", dir=parent) as tmp:
                staging = Path(tmp) / "run"
                staging.mkdir()
                report = _export(staging, records, texts, manifest, policy)
                staging.rename(destination)
    return {"run_dir": str(destination.resolve()), **report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--collection", type=Path, action="append", default=[])
    parser.add_argument("--output-parent", type=Path, default=Path("runs"))
    args = parser.parse_args()
    result = build(args.run, args.policy, args.output_parent, tuple(args.collection))
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("run_dir", "observations", "dispositions", "cohorts", "model_calls")
            }
        )
    )


if __name__ == "__main__":
    main()
