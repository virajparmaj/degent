"""Repeatable local identity, selection, scope and evaluation-readiness audits."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .evidence import SELECTOR_VERSION, knowledge_scope, select_units
from .pay import pay_records
from .store import digest, read_json, read_jsonl, write_json, write_jsonl


def normalized_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit(("", parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def identity_audit(rows: list[dict]) -> dict:
    indexes = {kind: defaultdict(list) for kind in ("requisition", "url_alias", "source_posting_group", "exact_text", "exact_title")}
    for row in rows:
        req = row.get("requisition_id")
        # A handover's grouping key is an identity candidate, never an approval.
        group = row.get("source_posting_group", "")
        if req:
            indexes["requisition"][req].append(row)
        elif group.startswith("requisition:"):
            indexes["requisition"][group.split(":", 1)[1]].append(row)
        for address in {row.get("original_url"), row.get("canonical_url"), *row.get("url_aliases", [])} - {None, ""}:
            indexes["url_alias"][normalized_url(address)].append(row)
        if group:
            indexes["source_posting_group"][group].append(row)
        indexes["exact_text"][row["clean_sha256"]].append(row)
        indexes["exact_title"][row.get("title", "").casefold()].append(row)
    findings, quarantine = [], set()
    for kind, index in indexes.items():
        for key, members in sorted(index.items()):
            observations = {m["observation_id"]: m for m in members}
            members = list(observations.values())
            if len(members) < 2:
                continue
            reqs = sorted({m["requisition_id"] for m in members if m.get("requisition_id")})
            conflict = kind in {"url_alias", "source_posting_group"} and len(reqs) > 1
            if conflict:
                quarantine.update(observations)
            findings.append({"kind": kind, "key": key, "observation_ids": sorted(observations),
                             "document_ids": sorted({m["document_id"] for m in members}),
                             "collection_kinds": sorted({m.get("collection_kind", "current") for m in members}),
                             "requisitions": reqs, "description_hashes": sorted({m["clean_sha256"] for m in members}),
                             "status": "conflicting_identity_assertions" if conflict else "repeated_observation" if len({m["document_id"] for m in members}) == 1
                             else "possible_repost_or_separate_vacancies" if kind in {"exact_title", "exact_text"}
                             else "identity_or_changed_description_candidate",
                             "merged": False})
    return {"version": "identity-audit-v1", "findings": findings,
            "quarantined_observation_ids": sorted(quarantine),
            "policy": "No similarity merges. Historical source groups, aliases and requisitions require vacancy/repost adjudication."}


def function_tags(title: str) -> list[str]:
    patterns = {"ai_ml": r"\bAI\b|\bML\b|machine learning|artificial intelligence",
                "risk": r"risk|compliance", "fraud": r"fraud|money laundering|sanctions",
                "credit": r"credit|underwrit", "product_management": r"product manage",
                "research": r"research", "data_analysis_science": r"analyst|analytic|data|scientist",
                "quantitative_modeling": r"quant|model", "engineering": r"engineer|developer|software|cyber"}
    return [key for key, pattern in patterns.items() if re.search(pattern, title, re.I)]


def format_tags(text: str) -> list[str]:
    tags = []
    if len(text) < 1500: tags.append("short")
    if len(text) > 12000: tags.append("long")
    if not re.search(r"^(?:basic|minimum|preferred)?\s*qualifications?\s*:?$", text, re.I | re.M): tags.append("unheaded_or_inline")
    if re.search(r"\b(?:not required|no experience|without experience)\b", text, re.I): tags.append("negation")
    if re.search(r"\b(?:SQL or Python|Python or SQL|and/or)\b", text, re.I): tags.append("skill_or_format_alternative")
    if re.search(r"\b(?:AI|artificial intelligence|machine learning)\b", text, re.I): tags.append("ai_context")
    return tags or ["standard"]


def frozen_packet_audit(packet: dict, rows: list[dict]) -> dict:
    documents = packet["documents"]
    counts = Counter(d["split"] for d in documents)
    by_id = {r["document_id"]: r for r in rows}
    failures = []
    for doc in documents:
        row = by_id.get(doc["document_id"])
        if not row or digest(doc["full_text"]) != doc["source_sha256"] or row["clean_sha256"] != doc["source_sha256"]:
            failures.append(doc["document_id"])
    coverage = {}
    for split in ("calibration", "held_out"):
        found = [d for d in documents if d["split"] == split]
        coverage[split] = {"functions": dict(Counter(t for d in found for t in function_tags(d["title"]))),
                           "seniority": dict(Counter(by_id[d["document_id"]].get("level", "unknown") for d in found if d["document_id"] in by_id)),
                           "formats": dict(Counter(t for d in found for t in format_tags(d["full_text"])))}
    corpus = {key: dict(Counter(t for r in rows if r.get("eligible") for t in
                              (function_tags(r["title"]) if key == "functions" else format_tags(Path(r["clean_file"]).read_text()))))
              for key in ("functions", "formats")}
    gaps = {split: {key: sorted(set(corpus[key]) - set(coverage[split][key])) for key in corpus} for split in coverage}
    return {"counts": dict(counts), "hash_failures": failures,
            "distinct_vacancy_ids": len({d["vacancy_id"] for d in documents}),
            "structural_pass": counts == {"calibration": 20, "held_out": 40} and not failures
                               and len({d["vacancy_id"] for d in documents}) == 60,
            "labels_present": sum(d.get("labels") is not None for d in documents),
            "split_coverage": coverage, "eligible_corpus_coverage": corpus, "missing_from_split": gaps,
            "original_format_tags": dict(Counter(t for d in documents for t in d.get("format_tags", []))),
            "status": "human_format_representativeness_and_labels_required",
            "policy": "Frozen split retained. Title/format tags are review cues, not human function labels or measured representativeness."}


def audit_versions(rows: list[dict], selections: dict) -> tuple:
    versions = {r["document_id"]: r for r in rows}
    proposals, packet, pay, selection_audit = [], [], [], []
    for doc, row in versions.items():
        source = Path(row["clean_file"]).read_text()
        selection = selections.get(doc) or select_units(source, row["clean_sha256"])
        pay.extend(pay_records(source, doc, row["clean_sha256"]))
        omitted = [u for u in selection["units"] if not u["selected"]]
        selection_audit.append({"document_id": doc, "source_sha256": row["clean_sha256"],
            "selector_version": SELECTOR_VERSION, "full_characters": len(source),
            "selected_characters": selection["selected_characters"],
            "excluded_units": [{k: u[k] for k in ("unit_id", "start", "end", "selection_reason", "text")} for u in omitted],
            "description_completeness": row.get("description_completeness"),
            "selection_completeness": "unknown", "omission_audited": False,
            "possible_expectation_omissions": [u["unit_id"] for u in omitted
                 if u["selection_reason"] != "posting_metadata" and re.search(r"\b(?:must|required|preferred|experience|ability|responsib\w*)\b", u["text"], re.I)
                 and not re.match(r"No agencies please|All qualified applicants|If you have visited our website|Capital One will consider for employment", u["text"], re.I)]})
        if row.get("collection_kind") != "historical":
            continue
        scope = knowledge_scope(row["title"], source)
        tags = function_tags(row["title"])
        proposed = not row["comparison_subset"] and scope["decision"] != "out_of_scope" and bool(tags)
        proposals.append({"proposal_id": digest(doc + row["clean_sha256"] + "scope-v2"), "document_id": doc,
                          "source_sha256": row["clean_sha256"], "existing_subset": row["comparison_subset"],
                          "proposed_action": "review_for_inclusion" if proposed else "review_focused_membership" if row["comparison_subset"] else "retain_outside_subset_pending_review",
                          "function_cues": tags, "title_evidence": row["title"],
                          "geography_status": row.get("geography_status"), "human_decision": None,
                          "reason": "Candidate cues only; review exact duties and source geography before changing membership."})
        packet.append({"document_id": doc, "source_sha256": row["clean_sha256"], "title": row["title"],
                       "focused_subset": row["comparison_subset"], "full_text": source,
                       "source_mapping": row["source_mapping"], "evidence_references": row["evidence_references"],
                       "source_completeness_assertion": row["description_completeness"],
                       "selected_units": [u for u in selection["units"] if u["selected"]],
                       "excluded_units": omitted, "function_cues": tags, "metadata": {k: row.get(k) for k in
                           ("level", "seniority_evidence", "employment_type_evidence", "locations_raw", "location_assertions", "location_review", "remote_eligibility", "remote_evidence", "comparison_quarantine")},
                       "human_review": {"reviewer": None, "selection_completeness": None,
                                        "description_completeness": None, "omission_audited": False,
                                        "scope_decision": None, "identity_decision": None, "labels": None}})
    return proposals, packet, pay, selection_audit


def save_packets(directory, baseline, rows, proposals, packet, pay, selection_audit, selector_signature):
    # Stable proposal IDs avoid duplicated journal entries on repeat preparation.
    journal = {r["proposal_id"]: r for r in read_jsonl(directory / "scope-proposals.jsonl")}
    journal.update({r["proposal_id"]: r for r in proposals if r["proposal_id"] not in journal})
    write_jsonl(directory / "scope-proposals.jsonl", journal.values())
    packet_path = directory / ("historical-selection-review-" + selector_signature[:16] + ".json")
    if not packet_path.exists():
        write_json(packet_path, {"version": 1, "selector_version": SELECTOR_VERSION, "selector_signature": selector_signature, "documents": packet,
                                "instructions": "Review originals and all omitted units, scope, metadata and completeness. Blank decisions carry no approval. Unknown/partial descriptions cannot support absence claims."})
    write_json(directory / "historical-selection-review-index.json", {"current_packet": packet_path.name,
               "selector_signature": selector_signature, "previous_packets_preserved": True})
    write_jsonl(directory / "pay-records.jsonl", [p for p in pay if p["record_kind"] == "pay_candidate"])
    write_jsonl(directory / "monetary-mentions-review.jsonl", [p for p in pay if p["record_kind"] != "pay_candidate"])
    write_jsonl(directory / "selection-audit.jsonl", selection_audit)
    gold = frozen_packet_audit(read_json(baseline / "gold-packet.json"), rows)
    write_json(directory / "gold-coverage-audit.json", gold)
    return packet_path, gold


def audit_counts(rows, proposals, pay, selection_audit, conflicting):
    historical = [r for r in rows if r.get("collection_kind") == "historical"]
    focused = [r for r in historical if r.get("comparison_subset")]
    counts = {"all_observations": len(rows), "historical_observations": len(historical),
              "historical_versions": len({r["document_id"] for r in historical}),
              "focused_versions": len({r["document_id"] for r in focused}),
              "focused_source_posting_groups": len({r["source_posting_group"] for r in focused}),
              "historical_identity_candidates": len({r["vacancy_id"] for r in historical}),
              "historical_without_verified_wording": sum(not r.get("verified_historical_wording_at") for r in historical),
              "historical_publication_date_conflicts": sum("after_verified_wording" in r["publication_date_evidence"]["uncertainty"] for r in historical),
              "focused_location_reviews": dict(Counter(r["location_review"] for r in focused)),
              "focused_completeness": dict(Counter(r["description_completeness"] for r in focused)),
              "proposed_additional_versions": sum(p["proposed_action"] == "review_for_inclusion" for p in proposals),
              "pay_candidates": sum(p["record_kind"] == "pay_candidate" for p in pay),
              "pay_documents": len({p["document_id"] for p in pay if p["record_kind"] == "pay_candidate"}),
              "unclassified_money_mentions": sum(p["record_kind"] != "pay_candidate" for p in pay),
              "selection_possible_omission_documents": sum(bool(s["possible_expectation_omissions"]) for s in selection_audit),
              "identity_conflicting_observations": len(conflicting)}
    return counts


def audit(directory: Path, baseline: Path) -> dict:
    from .model_runner import method_signature
    selector_signature = method_signature({"provider": "local_selection_review", "model": "none"})
    rows = read_jsonl(directory / "observations.jsonl")
    identities = identity_audit(rows)
    write_json(directory / "identity-review.json", identities)
    conflicting = set(identities["quarantined_observation_ids"])
    for row in rows:
        reasons = []
        if row["observation_id"] in conflicting:
            reasons.append("conflicting_identity_assertions")
        if row.get("identity_status") != "confirmed_source_identity":
            reasons.append("vacancy_identity_unresolved")
        if row.get("collection_kind") == "historical" and not row.get("verified_historical_wording_at"):
            reasons.append("no_verified_historical_wording")
        row["comparison_quarantine"] = reasons
    write_jsonl(directory / "observations.jsonl", rows)
    selections = {s["document_id"]: s for s in read_jsonl(directory / "source-units.jsonl")}
    proposals, packet, pay, selection_audit = audit_versions(rows, selections)
    packet_path, gold = save_packets(directory, baseline, rows, proposals, packet, pay, selection_audit, selector_signature)
    counts = audit_counts(rows, proposals, pay, selection_audit, conflicting)
    result = {"version": 1, "counts": counts, "gold_structural_pass": gold["structural_pass"],
              "model_calls": 0, "approvals_generated": 0,
              "blockers": ["Historical vacancy identities and possible reposts need human adjudication.",
                           "Approve scope/function/level boundaries and common taxonomy with stable IDs and lineage.",
                           "Review historical full text, omitted units and source completeness; all 31 focused versions are unknown.",
                           "Review gold format/function gaps, then label the frozen 20/40 packet and historical packet separately.",
                           "Resolve pay location labels, unspecified dollar currency and missing pay periods.",
                           "Inference/spend remain disabled; this preparation grants no authorization."],
              "reports": ["report.json", "coverage.csv", "identity-review.json", "scope-proposals.jsonl", "pay-records.jsonl",
                          "selection-audit.jsonl", packet_path.name, "gold-coverage-audit.json"]}
    write_json(directory / "readiness.json", result)
    return result
