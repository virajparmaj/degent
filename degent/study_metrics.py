"""Local, vacancy-level metrics with separate time and assessment bases."""
from __future__ import annotations

import csv
import difflib
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

from .evidence import period_definitions
from .review import document_reviewed, effective_extractions, extraction_identity
from .store import read_json, read_jsonl, utc_now, write_json
from .study_evaluation import audit_packet
from .taxonomy import load_taxonomy
from .vocabulary import vocabulary


def analysis_signature(directory: Path) -> str:
    from .model_runner import normalization_signature
    from .store import digest
    return digest(json.dumps({"implementation": {name: digest(Path(__file__).with_name(name).read_bytes())
                  for name in ("study_metrics.py", "study_evaluation.py", "model_runner.py", "evidence.py", "taxonomy.py", "review.py", "historical.py", "metadata.py", "pay.py", "preparation_audit.py")},
                  "normalization": normalization_signature(load_taxonomy(directory)["skills"])}, sort_keys=True))


def write_csv(path: Path, rows: list[dict]):
    columns = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict,list)) else v for k,v in r.items()} for r in rows)


def automation_gate(directory: Path, records: dict) -> dict:
    evaluation = read_json(directory / "evaluation.json") if (directory / "evaluation.json").exists() else {}
    packet = audit_packet(directory)
    pending = [d["document_id"] for d in packet["documents"] if not document_reviewed(records[d["document_id"]])]
    raw = {r["document_id"]: r for r in read_jsonl(directory / "requirements.jsonl")}
    tp = fp = fn = correct = 0
    for doc in packet["documents"]:
        doc_id = doc["document_id"]
        reviewed = records[doc_id]
        if not document_reviewed(reviewed):
            continue
        final = [r for r in reviewed["requirements"] if not r.get("excluded")]
        before = raw[doc_id]["requirements"]
        used = set()
        for r in before:
            match = next((i for i,f in enumerate(final) if i not in used and r.get("concept") == f.get("concept") and r["evidence_start"] == f["evidence_start"] and r["evidence_end"] == f["evidence_end"]), None)
            if match is None:
                fp += 1
            else:
                used.add(match)
                tp += 1
                correct += int(all(r.get(k) == final[match].get(k) for k in
                    ("status", "statement_scope", "experience_range", "experience_years", "qualifier", "alternatives", "alternative_logic")))
        fn += len(final) - len(used)
    quality = {"precision": tp/(tp+fp) if tp+fp else None, "recall": tp/(tp+fn) if tp+fn else None,
               "strength_context_accuracy": correct/tp if tp else None}
    passed = (evaluation.get("status") == "passed" and bool(packet["documents"]) and not pending
              and all(records[d["document_id"]].get("accepted") and records[d["document_id"]].get("method_signature") == evaluation.get("method_signature") for d in packet["documents"])
              and (quality["precision"] or 0) >= .95 and (quality["recall"] or 0) >= .90
              and (quality["strength_context_accuracy"] or 0) >= .95)
    return {"status": "passed" if passed else "exploratory", "method_signature": evaluation.get("method_signature"),
            "pending_audits": pending, "audit_documents": len(packet["documents"]), "audit_quality": quality,
            "held_out_evaluation": evaluation.get("status", "missing")}


def statement_concepts(row):
    """Each concept counts once per statement, including conditional alternatives."""
    return {row.get("concept") or row.get("original_concept"), *(
        a.get("concept") or a.get("phrase") for a in row.get("alternative_concepts", []))} - {None}


def wording_stamp(row: dict) -> str:
    if row.get("collection_kind") == "historical":
        return row.get("verified_historical_wording_at") or ""
    return row.get("current_capture_at") or row.get("retrieved_at") or row.get("capture_at", "")


def assessed(record, concept, status, basis, gate, category, scope="qualification"):
    from .model_runner import normalize
    if not record or not record.get("accepted"):
        return False, False, "unextracted"
    approved = document_reviewed(record)
    if basis == "human_reviewed" and not approved:
        return False, False, "not_full_text_reviewed"
    if basis == "evaluated_automated" and (gate["status"] != "passed" or record.get("method_signature") != gate["method_signature"]):
        return False, False, "evaluation_or_audit_pending"
    rows = [r for r in record["requirements"] if not r.get("excluded")]
    present = any((r.get("concept") == concept or r.get("alternative_logic") == "and" and concept in statement_concepts(r)) and r["status"] == status and r["statement_scope"] == scope
                  and not (r.get("alternatives") and r.get("alternative_logic") == "or") for r in rows)
    if present:
        return True, True, "mentioned"
    unresolved = any(
        (r.get("concept") == concept and (r["status"] == "unclear" or r["statement_scope"] == "unknown"
         or r.get("alternatives") and r.get("alternative_logic") == "or"))
        or (not r.get("concept") and r.get("category") in {category, "other"})
        or any(a.get("concept") is None for a in r.get("alternative_concepts", []))
        or (r.get("alternative_logic") == "or"
            and concept in ({a["concept"] for a in r.get("alternative_concepts", [])} or {normalize(a)[0] for a in r.get("alternatives", [])}))
        for r in rows if r["statement_scope"] in {scope, "unknown"})
    if unresolved:
        return False, False, "ambiguous_or_unmapped"
    if approved and record.get("description_completeness") == "complete" and record.get("selection_review") == "complete":
        return True, False, "not_stated_as_" + status + "_in_reviewed_description"
    if (basis == "evaluated_automated" and record.get("description_completeness") == "complete"
            and record.get("selection_review") == "complete"
            and record.get("capture_completeness") == "full_provider_description"):
        return True, False, "not_detected_by_evaluated_method"
    return False, False, "completeness_unresolved"


def report(directory: Path, config: dict) -> dict:
    observations = read_jsonl(directory / "observations.jsonl")
    manifest = read_json(directory / "manifest.json") if (directory / "manifest.json").exists() else {}
    records = {r["document_id"]: r for r in effective_extractions(directory)}
    from .model_runner import method_signature, normalization_signature
    current_normalization = normalization_signature(load_taxonomy(directory)["skills"])
    stale_documents = []
    for record in records.values():
        if record.get("model_identity") and (record.get("model_signature") != method_signature(record["model_identity"])
                or record.get("normalization_signature") != current_normalization):
            record["accepted"] = False
            stale_documents.append(record["document_id"])
    taxonomy = load_taxonomy(directory)
    gate = automation_gate(directory, records)
    # Recheck hashes so changed/removed source files cannot enter a stale report.
    from .store import digest
    candidates, coverage_candidates = [], []
    for row in observations:
        assignment = taxonomy["assignments"].get(row["document_id"], {})
        source = Path(row["clean_file"])
        if not source.exists() or digest(source.read_bytes()) != row["clean_sha256"]:
            continue
        evidence = list(row.get("evidence_references", []))
        if row.get("raw_source_sha256"):
            evidence.append({"path": row["raw_source_file"], "sha256": row["raw_source_sha256"]})
        if any(not Path(ref["path"]).is_file() or digest(Path(ref["path"]).read_bytes()) != ref["sha256"] for ref in evidence):
            continue
        if row.get("eligible") and not (assignment.get("source_sha256") == row["clean_sha256"] and assignment.get("scope_decision") == "excluded"):
            coverage_candidates.append(row)
            if row.get("comparison_quarantine") or row.get("identity_status", "confirmed_source_identity") != "confirmed_source_identity":
                continue
            candidates.append({**row, "role_group_id": assignment.get("role_group_id") if assignment.get("source_sha256") == row["clean_sha256"] else None,
                               "level": assignment.get("level", row.get("level", "unknown")) if assignment.get("source_sha256") == row["clean_sha256"] else row.get("level", "unknown"),
                               "employment_type": assignment.get("employment_type", row.get("employment_type", "unknown")) if assignment.get("source_sha256") == row["clean_sha256"] else row.get("employment_type", "unknown"),
                               "taxonomy_version": taxonomy["version"]})
    concepts = {**vocabulary(), **taxonomy["skills"]}
    watched = set(config.get("watched_concepts", []))
    watched.update(r.get("concept") for record in records.values() for r in record.get("requirements", []) if r.get("concept"))
    watched.update(a["concept"] for record in records.values() for r in record.get("requirements", []) for a in r.get("alternative_concepts", []) if a.get("concept"))
    groups = defaultdict(dict)
    # Publication view uses only the latest observed wording/date assertion per vacancy.
    latest = {}
    for row in candidates:
        key = (row.get("collection_kind", "current"), row["vacancy_id"])
        if key not in latest or wording_stamp(row) > wording_stamp(latest[key]):
            latest[key] = row
    current = [r for r in candidates if r.get("collection_kind", "current") == "current"]
    historical = [r for r in candidates if r.get("collection_kind") == "historical" and r.get("verified_historical_wording_at")]
    views = (("capture_period", current, "capture_period_id"),
             ("publication_observed_today", [r for r in latest.values() if r.get("collection_kind", "current") == "current"], "publication_period_id"),
             ("historical_wording", historical, "wording_period_id"),
             ("historical_publication_cohort", [r for r in latest.values() if r.get("collection_kind") == "historical" and r.get("verified_historical_wording_at")], "publication_period_id"))
    for basis, rows, period_field in views:
        for row in rows:
            group = row.get("role_group_id")
            if group not in taxonomy["groups"] or not taxonomy["groups"][group].get("approved"):
                continue
            key = (basis, row["employer_id"], row.get("brand") or row["employer_id"], group,
                   row.get("level", "unknown"), row.get("employment_type") or "unknown", row.get(period_field, "UNKNOWN"), row.get("collection_kind", "current"))
            old = groups[key].get(row["vacancy_id"])
            if old is None or wording_stamp(row) > wording_stamp(old):
                groups[key][row["vacancy_id"]] = row
    metrics = []
    minimum = max(30, config.get("comparison_minimum", 30))
    for key, vacancies in sorted(groups.items()):
        for concept in sorted(watched):
            category = concepts.get(concept, {}).get("category", "other")
            for scope in ("qualification", "responsibility", "company_expectation"):
                for status in ("required", "preferred"):
                    for review_basis in ("human_reviewed", "evaluated_automated"):
                        checks = [assessed(records.get(o["document_id"]), concept, status, review_basis, gate, category, scope) for o in vacancies.values()]
                        denominator, count = sum(c[0] for c in checks), sum(c[1] for c in checks)
                        signatures = {records[o["document_id"]]["method_signature"] for o in vacancies.values() if o["document_id"] in records and records[o["document_id"]].get("accepted")}
                        compatible = len(signatures) == 1
                        metrics.append({**dict(zip(("time_basis", "employer_id", "brand", "role_group_id", "level", "employment_type", "period", "collection_kind"), key)),
                                        "taxonomy_version": taxonomy["version"], "concept": concept, "requirement_status": status,
                                        "statement_scope": scope, "assessment_basis": review_basis,
                                        "eligible_vacancies": len(vacancies), "extracted_vacancies": sum(bool(records.get(o["document_id"], {}).get("accepted")) for o in vacancies.values()),
                                        "reviewed_vacancies": sum(document_reviewed(records.get(o["document_id"], {})) for o in vacancies.values()),
                                        "assessed_vacancies": denominator, "count": count, "unresolved_vacancies": len(vacancies)-denominator,
                                        "outcomes": dict(Counter(c[2] for c in checks)), "method_versions": sorted(signatures),
                                        "share_percent": round(100*count/denominator, 1) if denominator >= minimum and compatible else None,
                                        "unavailable_reason": "mixed_method_versions" if not compatible else "insufficient_assessed_sample" if denominator < minimum else None,
                                        "difference_percentage_points": None})
    statements, experience = [], []
    for o in latest.values():
        record = records.get(o["document_id"], {})
        if not record.get("accepted"):
            continue
        counts = Counter(c for r in record["requirements"] if not r.get("excluded") for c in statement_concepts(r))
        statements.extend({"vacancy_id": o["vacancy_id"], "document_id": o["document_id"], "concept": c, "mention_count": n,
                           "review_basis": "human_reviewed" if document_reviewed(record) else "automated_candidate"} for c,n in counts.items())
        experience.extend({"vacancy_id": o["vacancy_id"], "document_id": o["document_id"], "concept": r.get("concept"),
                           "experience_range": r.get("experience_range"), "experience_years": r.get("experience_years"),
                           "qualifier": r.get("qualifier"), "evidence": r["evidence"], "status": r["status"]}
                          for r in record["requirements"] if r.get("experience_range") or r.get("experience_years") is not None or r.get("qualifier"))
    experience_summary, mention_summary = [], []
    facet_names = ("time_basis", "employer_id", "brand", "role_group_id", "level", "employment_type", "period", "collection_kind")
    for key, vacancies in groups.items():
        for basis in ("human_reviewed", "evaluated_automated"):
            explicit = defaultdict(list)
            mentions = defaultdict(list)
            for vacancy, observation in vacancies.items():
                record = records.get(observation["document_id"], {})
                if not record.get("accepted") or (basis == "human_reviewed" and not document_reviewed(record)) or (basis == "evaluated_automated" and (gate["status"] != "passed" or record.get("method_signature") != gate["method_signature"])):
                    continue
                for r in record["requirements"]:
                    if r.get("excluded"):
                        continue
                    concept = r.get("concept") or r.get("original_concept")
                    for mentioned in statement_concepts(r):
                        mentions[mentioned].append(vacancy)
                    if r.get("experience_range") or r.get("experience_years") is not None or r.get("qualifier"):
                        explicit[concept].append({"vacancy_id": vacancy, "document_id": observation["document_id"],
                                                  "range": r.get("experience_range"), "years": r.get("experience_years"),
                                                  "qualifier": r.get("qualifier"), "wording": r["evidence"],
                                                  "status": r["status"], "context": r["statement_scope"],
                                                  "alternatives": r.get("alternatives"), "alternative_logic": r.get("alternative_logic")})
            for concept, values in explicit.items():
                experience_summary.append({**dict(zip(facet_names, key)), "assessment_basis": basis, "concept": concept,
                    "explicit_experience_vacancies": len({r["vacancy_id"] for r in values if r["range"] or r["years"] is not None}),
                    "explicit_proficiency_vacancies": len({r["vacancy_id"] for r in values if r["qualifier"]}),
                    "stated_value_denominator": len({r["vacancy_id"] for r in values}), "values": values,
                    "denominator_basis": "distinct vacancies with an explicit value; missing wording is not zero experience"})
            for concept, values in mentions.items():
                mention_summary.append({**dict(zip(facet_names, key)), "assessment_basis": basis, "concept": concept,
                                        "evidence_statement_count": len(values), "distinct_mentioning_vacancies": len(set(values))})
    combinations = []
    for key, vacancies in groups.items():
        for components in config.get("combinations", []):
            for review_basis in ("human_reviewed", "evaluated_automated"):
                checked = [[assessed(records.get(o["document_id"]), c, "required", review_basis, gate, concepts.get(c, {}).get("category", "other")) for c in components] for o in vacancies.values()]
                n = sum(all(c[0] for c in checks) for checks in checked)
                count = sum(all(c[1] for c in checks) for checks in checked)
                signatures = {records[o["document_id"]]["method_signature"] for o in vacancies.values() if o["document_id"] in records and records[o["document_id"]].get("accepted")}
                combinations.append({**dict(zip(facet_names, key)),
                                     "components": components, "assessment_basis": review_basis, "count": count, "assessed_vacancies": n,
                                     "unresolved_vacancies": len(vacancies)-n, "share_percent": round(100*count/n,1) if n >= minimum and len(signatures) == 1 else None})
    as_of = __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo(config["timezone"])).date().isoformat()
    periods = period_definitions(as_of)
    coverage = []
    coverage_latest = {}
    for row in coverage_candidates:
        key = (row.get("collection_kind", "current"), row["vacancy_id"])
        if key not in coverage_latest or row.get("capture_at", "") > coverage_latest[key].get("capture_at", ""):
            coverage_latest[key] = row
    coverage_views = (("capture_period", "capture_period_id", "current", coverage_candidates),
                      ("publication_observed_today", "publication_period_id", "current", list(coverage_latest.values())),
                      ("historical_wording", "wording_period_id", "historical", coverage_candidates),
                      ("historical_publication_cohort", "publication_period_id", "historical", list(coverage_latest.values())))
    for time_basis, field, collection_kind, pool in coverage_views:
        for period in periods:
            found = [o for o in pool if o.get("collection_kind", "current") == collection_kind and o.get(field) == period["id"]
                     and (collection_kind != "historical" or o.get("verified_historical_wording_at"))]
            confirmed = [o for o in found if not o.get("comparison_quarantine") and o.get("identity_status", "confirmed_source_identity") == "confirmed_source_identity"]
            coverage.append({"time_basis": time_basis, "collection_kind": collection_kind, "period": period["id"], "calendar_kind": period["kind"],
                             "capture_coverage": "partial_observations" if found else "unavailable",
                             "observed_content_versions": len({o["document_id"] for o in found}),
                             "unresolved_identity_candidates": len({o["vacancy_id"] for o in found if o not in confirmed}),
                             "eligible_distinct_vacancies": len({o["vacancy_id"] for o in confirmed}),
                             "fully_reviewed_complete_vacancies": len({o["vacancy_id"] for o in found
                                  if document_reviewed(records.get(o["document_id"], {})) and records[o["document_id"]].get("description_completeness") == "complete"}),
                             "observed_dates": sorted({o["capture_date"] for o in found}),
                             "wording_basis": "publication cohort with later verified archive wording; wording at publication unproven" if time_basis == "historical_publication_cohort" else "verified archive point observation" if time_basis == "historical_wording" else "observed capture" if time_basis == "capture_period" else "publication cohort, wording observed today"})
    changes = []
    history = defaultdict(list)
    for o in candidates:
        history[o["vacancy_id"]].append(o)
    for vacancy, versions in history.items():
        versions.sort(key=wording_stamp)
        for before, after in zip(versions, versions[1:]):
            if before["clean_sha256"] == after["clean_sha256"]:
                continue
            changes.append({"vacancy_id": vacancy, "before_document_id": before["document_id"], "after_document_id": after["document_id"],
                            "before_capture": wording_stamp(before), "after_capture": wording_stamp(after),
                            "comparison": "captured_text_difference; edit date and requirement meaning not inferred",
                            "parser_compatible": before.get("parser_version") == after.get("parser_version"),
                            "diff": "\n".join(difflib.unified_diff(Path(before["clean_file"]).read_text().splitlines(), Path(after["clean_file"]).read_text().splitlines()))})
    display = [r for r in metrics if r["share_percent"] is not None and r["time_basis"] in {"capture_period", "historical_wording"} and r["statement_scope"] == "qualification"]
    levels = defaultdict(set)
    for r in display:
        levels[(r["level"], r["employment_type"], r["period"], r["assessment_basis"])].add(r["role_group_id"])
    evaluation_signature = gate.get("method_signature")
    matching_levels = defaultdict(set)
    for r in display:
        if r["method_versions"] == [evaluation_signature] and r["level"] != "unknown":
            matching_levels[(r["time_basis"], r["collection_kind"], r["employer_id"], r["brand"], r["level"], r["employment_type"], r["period"], r["assessment_basis"], r["concept"], r["requirement_status"])].add(r["role_group_id"])
    ready = any(len(ids) >= 2 for ids in matching_levels.values()) and gate["held_out_evaluation"] == "passed"
    result = {"run_id": directory.name, "study_version": 1, "status": "comparison_ready" if ready else "evidence_limited",
              "analysis_signature": analysis_signature(directory), "stale_extraction_documents": stale_documents,
              "collection_signature": digest(json.dumps(manifest.get("collection_sessions", []), sort_keys=True)),
              "inventory_coverage": [{"session_id": s["id"], "status": s["status"], "pause_reason": s.get("pause_reason"),
                    "usage": s["usage"], "budgets": s["budgets"], "uncertain_requests": s.get("uncertain_requests", []),
                    "sources": {key: {k:v for k,v in info.items() if k not in {"pending", "seen_paths", "detail_captures", "inventory_ids", "page_signatures"}}
                                for key,info in s["sources"].items()}} for s in manifest.get("collection_sessions", [])],
              "coverage_counts": {"eligible_distinct_vacancies": len(latest),
                    "eligible_identity_candidates": len({o["vacancy_id"] for o in coverage_candidates if o.get("identity_status") == "unresolved_historical_candidate"}),
                    "readable_observations": sum(bool(o.get("description_readable")) for o in observations),
                    "scope_ambiguous_vacancies": len({o["vacancy_id"] for o in candidates if o.get("scope_decision", {}).get("decision") == "ambiguous"}),
                    "fully_reviewed_documents": sum(document_reviewed(r) for r in records.values() if r.get("accepted")),
                    "audited_documents": gate["audit_documents"]-len(gate["pending_audits"]),
                    "accepted_extracted_documents": sum(bool(r.get("accepted")) for r in records.values()),
                    "unresolved_role_vacancies": sum(not o.get("role_group_id") for o in latest.values())},
              "resources": {"preparation": manifest.get("preparation"), "inference_usage": manifest.get("inference_usage"),
                            "collection_cpu_and_memory": [{"session_id": s["id"], "intervals": s.get("resource_intervals", []),
                                "coverage": "recorded intervals only; earlier invocations may be unmeasured" if s.get("resource_intervals") else "unmeasured"} for s in manifest.get("collection_sessions", [])],
                            "remote_cpu_and_memory": "unmeasured", "token_estimates": "character estimates only unless provider usage is recorded"},
              "generated_at": utc_now(), "coverage": coverage, "eligible_observations": len(candidates),
              "distinct_vacancies": len(latest), "unassigned_vacancies": sum(not o.get("role_group_id") for o in latest.values()),
              "accepted_requirement_documents": sum(bool(r.get("accepted")) for r in records.values()),
              "requirement_shares": metrics, "comparison_views": {s:[r for r in metrics if r["requirement_status"] == s] for s in ("required", "preferred")},
              "requirement_combinations": combinations, "mention_counts": statements, "experience_proficiency": experience,
              "mention_summary": mention_summary, "experience_summary": experience_summary,
              "same_vacancy_changes": changes, "wording_shares": [], "automation_gate": gate,
              "comparison_settings": {"minimum_assessed": minimum, "weighted_score": None, "time_bases": [v[0] for v in views]},
              "next_targeted_query": {"recommendation": "Review taxonomy and full-text gold/audit packets; provider and spend remain gated."},
              "limitations": ["Surviving current postings are not historical wording evidence.", "Live inventory and source coverage can change during collection.",
                              "Reposting and role/seniority composition affect publication cohorts.", "Non-detection and not-mentioned are not proof of hiring unimportance.",
                              "30 is a display policy, not statistical significance or population representativeness."]}
    write_json(directory / "report.json", result)
    write_csv(directory / "coverage.csv", coverage)
    write_csv(directory / "observations.csv", observations)
    observation_by_document = {o["document_id"]:o for o in observations}
    write_csv(directory / "requirements.csv", [{**r, "document_id": record["document_id"],
        "vacancy_id": observation_by_document.get(record["document_id"], {}).get("vacancy_id"),
        "source_sha256": record.get("source_sha256"), "method_signature": record.get("method_signature"),
        "source_url": observation_by_document.get(record["document_id"], {}).get("original_url"),
        "assessment_status": "accepted" if record.get("accepted") else "failed_or_stale"}
        for record in records.values() for r in record.get("requirements", [])])
    write_csv(directory / "requirement-shares.csv", metrics)
    write_csv(directory / "requirement-combinations.csv", combinations)
    write_csv(directory / "same-vacancy-changes.csv", changes)
    write_csv(directory / "mention-counts.csv", statements)
    write_csv(directory / "experience-proficiency.csv", experience)
    write_csv(directory / "experience-summary.csv", experience_summary)
    write_csv(directory / "mention-summary.csv", mention_summary)
    (directory / "evidence-report.md").write_text(f"# Capital One knowledge-role baseline\n\nStatus: {result['status']}. Distinct eligible vacancies: {len(latest)}; unassigned: {result['unassigned_vacancies']}.\n\nPercentages require {minimum} assessed vacancies. Human-reviewed and evaluated-automated bases are separate.\n\n" + "\n".join("- " + s for s in result["limitations"]) + "\n")
    chart(directory, result, taxonomy)
    return result


def chart(directory: Path, report: dict, taxonomy: dict):
    rows = [r for r in report["requirement_shares"] if r["statement_scope"] == "qualification"]
    payload = json.dumps({"rows": rows, "groups": taxonomy["groups"], "summary": {
        "vacancies": report["distinct_vacancies"], "unassigned": report["unassigned_vacancies"],
        "gate": report["automation_gate"], "minimum": report["comparison_settings"]["minimum_assessed"]},
        "coverage": report["coverage"]}, ensure_ascii=False).replace("<", "\\u003c")
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Degent role baseline</title><style>
body{font:16px system-ui;margin:32px;color:#19314b;background:#faf9f5;max-width:1400px}h1{font-size:32px}
.controls{display:flex;flex-wrap:wrap;gap:16px;margin:24px 0}label{display:grid;gap:5px;font-size:13px}select{padding:8px;max-width:260px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px;border:1px solid #ccd4dd;text-align:left}th{background:#e9edf0}
.cell{cursor:pointer}.cell:hover{outline:2px solid #304f72}.unavailable{background:repeating-linear-gradient(135deg,#f1f2f2,#f1f2f2 5px,#faf9f5 5px,#faf9f5 10px);color:#606972}
small{display:block;margin-top:5px}#detail{white-space:pre-wrap;padding:16px;background:#edf1f3;margin:18px 0}#matrix{overflow:auto}
</style><h1>Capital One role-group baseline</h1>
<p>Required and preferred skills across approved groups at matching seniority. Each vacancy counts once per skill and strength. Select one assessment basis and period; publication cohorts describe wording observed today.</p>
<p id="summary"></p><div class="controls" id="controls"></div><div id="matrix"></div><div id="detail">Select a cell to inspect its counts, unresolved evidence, methods and audit status.</div>
<details><summary>Capture and source coverage by period</summary><div id="coverage"></div></details>
<p>Unknowns stay unavailable. The 30-vacancy threshold is a display policy, not statistical proof. Mention frequency and non-detection do not establish hiring importance. Historical wording requires earlier captures.</p>
<script id="data" type="application/json">""" + payload + """</script><script>
const data=JSON.parse(document.getElementById('data').textContent), fields=[['employer_id','Company'],['brand','Brand'],['time_basis','Time basis'],['level','Seniority'],['employment_type','Employment'],['period','Period'],['assessment_basis','Assessment'],['requirement_status','Strength']];
const state={}, selectors={};
function el(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
document.getElementById('summary').textContent=`${data.summary.vacancies} eligible distinct vacancies; ${data.summary.unassigned} await role assignment. Automated gate: ${data.summary.gate.status}. Percentages need ${data.summary.minimum} assessed vacancies per cell.`;
for(const [key,name] of fields){const label=el('label',name),select=el('select');selectors[key]=select;label.append(select);document.getElementById('controls').append(label);select.addEventListener('change',()=>{state[key]=select.value;render();});}
function render(){let subset=data.rows;for(const [key] of fields){const vals=[...new Set(subset.map(r=>r[key]))].sort();if(key==='time_basis') vals.sort((a,b)=>a==='capture_period'?-1:b==='capture_period'?1:0);if(key==='requirement_status')vals.sort((a,b)=>a==='required'?-1:b==='required'?1:0);if(key==='assessment_basis')vals.sort((a,b)=>a==='human_reviewed'?-1:b==='human_reviewed'?1:0);if(!vals.includes(state[key]))state[key]=vals[0];selectors[key].replaceChildren(...vals.map(v=>{const o=el('option',v);o.value=v;return o;}));selectors[key].value=state[key]||'';subset=subset.filter(r=>r[key]===state[key]);}
const container=document.getElementById('matrix');container.replaceChildren();if(!subset.length){container.append(el('p','No approved comparison cells yet. Review role definitions, full-text gold labels and extraction evidence. The coverage map remains available.'));return;}
const groups=[...new Set(subset.map(r=>r.role_group_id))].sort(),skills=[...new Set(subset.map(r=>r.concept))].sort(), table=el('table'),header=el('tr');header.append(el('th','Skill'));for(const g of groups)header.append(el('th',data.groups[g].name));table.append(header);
for(const skill of skills){const tr=el('tr');tr.append(el('th',skill));for(const g of groups){const r=subset.find(r=>r.concept===skill&&r.role_group_id===g),td=el('td');td.className='cell';if(r&&r.share_percent!==null){td.textContent=r.share_percent+'%';td.style.background=`rgba(64,116,171,${r.share_percent/140})`;}else{td.textContent='Unavailable';td.classList.add('unavailable');}td.append(el('small',r?`${r.count} / ${r.assessed_vacancies} assessed; ${r.unresolved_vacancies} unresolved`:'No assessment'));td.tabIndex=0;const detail=()=>{document.getElementById('detail').textContent=JSON.stringify({role_group:data.groups[g].name,skill,cell:r||null,audit:data.summary.gate},null,2);};td.addEventListener('click',detail);td.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')detail();});tr.append(td);}table.append(tr);}container.append(table);}
const coverage=el('table');const ch=el('tr');for(const s of ['Time basis','Period','Calendar','Observed vacancies','Coverage'])ch.append(el('th',s));coverage.append(ch);for(const r of data.coverage){const tr=el('tr');for(const s of [r.time_basis,r.period,r.calendar_kind,r.eligible_distinct_vacancies,r.capture_coverage])tr.append(el('td',s));coverage.append(tr);}document.getElementById('coverage').append(coverage);render();
</script></html>"""
    (directory / "role-baseline.html").write_text(page)
