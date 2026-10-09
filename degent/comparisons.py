"""Reproducible counts with explicit review, context, and unknown denominators."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .review import document_reviewed
from .config import observation_in_period
from .vocabulary import vocabulary


def window_kind(period: dict) -> str:
    start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if start.day == 1 and (end.replace(day=28).toordinal() <= end.toordinal()):
        from calendar import monthrange

        if end.day == monthrange(end.year, end.month)[1]:
            return f"{months}-months"
    return f"{(end-start).days+1}-days"


def dims(row: dict) -> tuple:
    return (
        row["employer_id"],
        row.get("brand") or row["employer_id"],
        row.get("role_track", "unknown"),
        row.get("role_subtype", "unknown"),
        row.get("level", "unknown"),
        row.get("geography_status", "unknown"),
        row.get("employment_type") or "unknown",
        row.get("evidence_basis") or "archive_capture",
    )


def selected_rows(record: dict, config: dict) -> list[dict]:
    scope = config.get("comparison_scope", "qualification")
    statuses = (
        {"required", "preferred"}
        if config.get("comparison_status", "required_or_preferred") == "required_or_preferred"
        else {config["comparison_status"]}
    )
    if config.get("comparison_status") == "stated":
        statuses = {"required", "preferred", "unclear"}
    return [
        r
        for r in record.get("requirements", [])
        if r.get("explicit")
        and not r.get("excluded")
        and not r.get("negated")
        and r.get("status") in statuses
        and r.get("statement_scope", "qualification") == scope
    ]


def metric_matches(row: dict, metric: tuple[str, ...], mode: str) -> bool:
    if mode == "wording":
        return bool(
            re.search(r"(?<!\w)" + re.escape(metric[0]) + r"(?!\w)", row.get("phrase", ""), re.I)
        )
    return row.get("concept") in metric


def assess(record: dict, metric: tuple[str, ...], config: dict, mode: str) -> tuple[bool, bool]:
    if config.get("comparison_review", "accepted") == "reviewed" and not document_reviewed(record):
        return False, False
    scope = config.get("comparison_scope", "qualification")
    categories = {vocabulary().get(concept, {}).get("category") for concept in metric}
    meaningful = [
        r
        for r in record.get("requirements", [])
        if not r.get("excluded") and r.get("statement_scope", "qualification") in {scope, "unknown"}
    ]

    def relevant_unknown(row):
        if metric_matches(row, metric, mode):
            return True
        return (
            mode != "wording"
            and not row.get("concept")
            and row.get("category", "other") in categories | {"other", "unknown"}
        )

    unknown = any(
        (
            not r.get("concept")
            or r.get("status") == "unclear"
            or r.get("statement_scope") == "unknown"
        )
        and relevant_unknown(r)
        for r in meaningful
    )
    rows = selected_rows(record, config)
    if mode == "wording":
        present = any(metric_matches(r, metric, mode) for r in rows)
    else:
        present = all(any(r.get("concept") == concept for r in rows) for concept in metric)
    return present or not unknown, present


def requirement_shares(
    candidates: list[dict], by_doc: dict, config: dict, mode: str = "concept"
) -> list[dict]:
    periods = sorted(config.get("periods", []), key=lambda p: p["start"])
    groups = defaultdict(dict)
    for period in periods:
        for obs in candidates:
            if not observation_in_period(obs, period):
                continue
            if obs.get("level") not in config.get("roles", {}).get(
                "entry_mid_levels", ["entry", "mid"]
            ):
                continue
            key = (*dims(obs), period["id"])
            vacancy = obs.get("vacancy_id") or obs["document_id"]
            old = groups[key].get(vacancy)
            if old is None or (obs.get("capture_at") or "") > (old.get("capture_at") or ""):
                groups[key][vacancy] = obs
    dimensions = sorted({key[:-1] for key in groups})
    results = []
    for group in dimensions:
        concepts = {
            r["concept"]
            for key, values in groups.items()
            if key[:-1] == group
            for obs in values.values()
            for r in selected_rows(by_doc.get(obs["document_id"], {}), config)
            if r.get("concept")
        }
        metrics = [(c,) for c in sorted(concepts | set(config.get("watched_concepts", [])))]
        if mode == "combination":
            metrics = [tuple(c) for c in config.get("combinations", []) if 2 <= len(c) <= 3]
        if mode == "wording":
            metrics = [
                (p,)
                for p in config.get(
                    "literal_phrases", ["high agency", "AI tools", "agentic workflows"]
                )
            ]
        for metric in metrics:
            previous = None
            for period in periods:
                observations = list(groups.get((*group, period["id"]), {}).values())
                extracted = [
                    by_doc[o["document_id"]] for o in observations if o["document_id"] in by_doc
                ]
                checks = [assess(r, metric, config, mode) for r in extracted]
                denominator = sum(known for known, _ in checks)
                count = sum(present for _, present in checks)
                signatures = sorted({r.get("method_signature", "legacy") for r in extracted})
                version_ok = len(signatures) <= 1
                share = (
                    round(100 * count / denominator, 1) if denominator >= 5 and version_ok else None
                )
                row = dict(
                    zip(
                        (
                            "employer_id",
                            "brand",
                            "role_track",
                            "role_subtype",
                            "level",
                            "geography_status",
                            "employment_type",
                            "evidence_basis",
                        ),
                        group,
                    )
                )
                row.update(
                    period=period["id"],
                    period_kind=period["kind"],
                    window_days=(
                        date.fromisoformat(period["end"]) - date.fromisoformat(period["start"])
                    ).days
                    + 1,
                    window_type=window_kind(period),
                    eligible_vacancies=len(observations),
                    extracted_vacancies=len(extracted),
                    reviewed_vacancies=sum(document_reviewed(r) for r in extracted),
                    assessed_vacancies=denominator,
                    unresolved_vacancies=len(observations) - denominator,
                    concept=" + ".join(metric),
                    components=list(metric),
                    count=count,
                    share_percent=share,
                    difference_percentage_points=None,
                    mode=mode,
                    requirement_status=config.get("comparison_status", "required_or_preferred"),
                    statement_scope=config.get("comparison_scope", "qualification"),
                    review_policy=config.get("comparison_review", "accepted"),
                    method_versions=signatures,
                    unavailable_reason=(
                        "mixed_extraction_versions"
                        if not version_ok
                        else "insufficient_assessed_sample" if denominator < 5 else None
                    ),
                )
                compatible = (
                    previous
                    and previous["period_kind"] == period["kind"] == "completed"
                    and previous["window_type"] == row["window_type"]
                    and previous["method_versions"] == signatures
                )
                if compatible and previous["share_percent"] is not None and share is not None:
                    row["difference_percentage_points"] = round(
                        share - previous["share_percent"], 1
                    )
                results.append(row)
                previous = row
    return results
