"""Full-text gold labels, held-out metrics and reproducible stratified audit packets."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

from .model_runner import expand_wire, normalize
from .review import effective_extractions
from .store import digest, read_json, read_jsonl, utc_now, write_json
from .taxonomy import load_taxonomy


def stratum(row: dict) -> tuple:
    title = row.get("title", "").lower()
    family = next((label for label, pattern in [
        ("engineering", r"engineer|developer"), ("data_ai", r"data|scientist|machine learning|\bAI\b"),
        ("quantitative", r"quant|model"), ("credit_risk", r"credit|risk|underwrit"),
        ("analytics", r"analyst|analytic")
    ] if re.search(pattern, title, re.I)), "unresolved")
    return family, row.get("level", "unknown")


def round_robin(rows: list[dict]) -> list[dict]:
    strata = defaultdict(list)
    for row in rows:
        strata[(*stratum(row), row.get("_gold_format", ""))].append(row)
    for values in strata.values():
        values.sort(key=lambda r: digest(r["document_id"]))
    result = []
    while any(strata.values()):
        for key in sorted(strata):
            if strata[key]:
                result.append(strata[key].pop())
    return result


def gold_packet(directory: Path) -> dict:
    target = directory / "gold-packet.json"
    if target.exists():
        return read_json(target)  # Frozen split; never overwrite human labels.
    unique = {}
    selections = {s["document_id"]:s for s in read_jsonl(directory / "source-units.jsonl")}
    for o in read_jsonl(directory / "observations.jsonl"):
        if o.get("eligible"):
            source = Path(o["clean_file"]).read_text()
            selection = selections.get(o["document_id"], {})
            tags = []
            if selection.get("selection_status") == "expanded_unknown_structure": tags.append("unheaded")
            if len(source) < 1500: tags.append("short")
            if len(source) > 12000: tags.append("long")
            if re.search(r"\b(?:not required|no experience|without experience)\b", source, re.I): tags.append("negation")
            if re.search(r"\b(?:SQL or Python|Python or SQL|either|one of|and/or)\b", source, re.I): tags.append("alternatives")
            if re.search(r"\b(?:AI|artificial intelligence|machine learning)\b", source, re.I): tags.append("ai_context")
            unique[o["vacancy_id"]] = {**o, "_gold_format": tags[0] if tags else "standard", "_format_tags": tags}
    rows = round_robin(list(unique.values()))[:60]
    documents = []
    for i, row in enumerate(rows):
        source = Path(row["clean_file"]).read_text()
        if digest(source) != row["clean_sha256"]:
            raise ValueError("Gold source hash mismatch; prepare evidence first.")
        documents.append({"document_id": row["document_id"], "vacancy_id": row["vacancy_id"],
                          "source_sha256": row["clean_sha256"], "title": row["title"], "stratum": [*stratum(row), row["_gold_format"]],
                          "format_tags": row["_format_tags"], "source_format": row.get("content_format"),
                          "split": "calibration" if i % 3 == 0 else "held_out",
                          "full_text": source, "labels": None,
                          "omission_audited": False, "description_completeness": None,
                          "selector_omissions": [], "notes": "Label full text, including statements absent from selected units."})
    result = {"version": 1, "created_at": utc_now(), "documents": documents,
              "target": {"calibration": 20, "held_out": 40},
              "status": "ready_for_labels" if len(documents) == 60 else "insufficient_distinct_vacancies",
              "role_pairs": [], "skill_pairs": [],
              "instructions": "labels is a list of canonical evidence records: concept/original_concept, evidence_start/end, status, statement_scope, explicit experience_range/experience_years/qualifier/alternatives/alternative_logic (include null/empty for no stated value). Review all full text. Do not label only model candidates."}
    write_json(target, result)
    return result


def _scores(tp, fp, fn):
    precision = tp/(tp+fp) if tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn,
            "precision": precision, "recall": recall,
            "precision_interval_95": _interval(tp, tp+fp), "recall_interval_95": _interval(tp, tp+fn)}


def _interval(successes, total):
    if not total:
        return None
    z, p = 1.96, successes/total
    center = (p+z*z/(2*total))/(1+z*z/total)
    radius = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/(1+z*z/total)
    return [max(0, center-radius), min(1, center+radius)]


def _overlap(a, b):
    length = min(a["evidence_end"]-a["evidence_start"], b["evidence_end"]-b["evidence_start"])
    return length > 0 and max(0, min(a["evidence_end"], b["evidence_end"])-max(a["evidence_start"], b["evidence_start"])) / length >= 0.5


def evaluate(directory: Path, labels_path: Path) -> dict:
    frozen = gold_packet(directory)
    payload = read_json(labels_path)
    definitions = load_taxonomy(directory)
    expected_ids = {d["document_id"] for d in frozen["documents"]}
    labels = {d["document_id"]: d for d in payload.get("documents", [])}
    if set(labels) != expected_ids:
        raise ValueError("Labels must match the frozen gold document set.")
    # Human corrections are ground truth, never a way to improve raw model scores.
    by_doc = {r["document_id"]: r for r in read_jsonl(directory / "requirements.jsonl")}
    selections = {r["document_id"]: r for r in read_jsonl(directory / "source-units.jsonl")}
    totals, subgroup = [0, 0, 0, 0, 0, 0, 0], defaultdict(lambda: [0]*7)
    signatures, held_out, calibration, support = set(), 0, 0, True
    supported_statements = total_statements = 0
    attributes = {key: {"correct": 0, "assessed": 0, "unassessed": 0} for key in
                  ("experience_range", "experience_years", "qualifier", "alternatives", "alternative_logic")}
    for fixed in frozen["documents"]:
        label = labels[fixed["document_id"]]
        if label.get("source_sha256") != fixed["source_sha256"] or label.get("split") != fixed["split"]:
            raise ValueError("Gold source or held-out split changed.")
        expected = label.get("labels")
        if not isinstance(expected, list) or label.get("omission_audited") is not True:
            continue
        for r in expected:
            start, end = r.get("evidence_start"), r.get("evidence_end")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(fixed["full_text"]):
                raise ValueError("Gold label evidence is not in the full source.")
            if r.get("status") not in {"required", "preferred", "not_required", "unclear"} or r.get("statement_scope") not in {"qualification", "responsibility", "company_expectation", "company_description", "hiring_process", "unknown"}:
                raise ValueError("Invalid gold labels.")
            if not (r.get("concept") or r.get("original_concept")):
                raise ValueError("Gold labels need a concept ID or unresolved original phrase.")
        if fixed["split"] == "calibration":
            calibration += 1
            continue
        held_out += 1
        record = by_doc.get(fixed["document_id"], {})
        if record.get("model_identity"):
            from .model_runner import method_signature, normalization_signature
            if record.get("model_signature") != method_signature(record["model_identity"]) or record.get("normalization_signature") != normalization_signature(definitions["skills"]):
                record = {**record, "accepted": False}
        if record.get("method_signature"):
            signatures.add(record["method_signature"])
        predicted = record.get("requirements", []) if record.get("accepted") else []
        used, tp, accuracy = set(), 0, 0
        for r in predicted:
            s, e = r["evidence_start"], r["evidence_end"]
            supported = fixed["full_text"][s:e] == r["evidence"]
            support &= supported
            supported_statements += int(supported)
            total_statements += 1
            concept = r.get("concept") or r.get("original_concept", "").casefold()
            match = next((i for i,g in enumerate(expected) if i not in used and
                          (g.get("concept") or g.get("original_concept", "").casefold()) == concept and _overlap(r,g)), None)
            if match is not None:
                tp += 1
                used.add(match)
                accuracy += int(r["status"] == expected[match]["status"] and r["statement_scope"] == expected[match]["statement_scope"])
                for field, tally in attributes.items():
                    actual, target = r.get(field), expected[match].get(field)
                    if actual in (None, [], "") and target in (None, [], ""):
                        continue
                    if field not in expected[match]:
                        tally["unassessed"] += 1
                        continue
                    if field == "alternatives":
                        actual = sorted(a.casefold() for a in actual or [])
                        target = sorted(a.casefold() for a in target or [])
                    tally["assessed"] += 1
                    tally["correct"] += int(actual == target)
        selected = selections.get(fixed["document_id"], {}).get("units", [])
        captured = sum(any(u["selected"] and u["start"] <= r["evidence_start"] and u["end"] >= r["evidence_end"] for u in selected) for r in expected)
        counts = [tp, len(predicted)-tp, len(expected)-tp, accuracy, tp, captured, len(expected)]
        totals = [a+b for a,b in zip(totals, counts)]
        subgroup[tuple(fixed["stratum"])] = [a+b for a,b in zip(subgroup[tuple(fixed["stratum"])], counts)]
    scores = _scores(*totals[:3])
    accuracy = totals[3]/totals[4] if totals[4] else None
    selector_recall = totals[5]/totals[6] if totals[6] else None
    passed = (calibration == 20 and held_out == 40 and len(signatures) == 1 and support
              and (scores["precision"] or 0) >= 0.95 and (scores["recall"] or 0) >= 0.90
              and (accuracy or 0) >= 0.95 and (selector_recall or 0) >= 0.90)
    for tally in attributes.values():
        tally["accuracy"] = tally["correct"]/tally["assessed"] if tally["assessed"] else None
        if tally["unassessed"] or tally["assessed"] and tally["accuracy"] < .95:
            passed = False
    role_checks = []
    for pair in payload.get("role_pairs", []):
        left, right = (definitions["assignments"].get(pair[k]) for k in ("left", "right"))
        predicted = left["role_group_id"] == right["role_group_id"] if left and right else None
        role_checks.append({**pair, "predicted": predicted, "correct": predicted == pair["same_group"] if predicted is not None else None})
    skill_checks = []
    for pair in payload.get("skill_pairs", []):
        left, right = (normalize(pair[k], definitions["skills"])[0] for k in ("left", "right"))
        predicted = left == right if left and right else None
        skill_checks.append({**pair, "predicted": predicted, "correct": predicted == pair["equivalent"] if predicted is not None else None})
    result = {"status": "passed" if passed else "exploratory", "evaluated_at": utc_now(),
              "calibration_documents": calibration, "held_out_documents": held_out,
              "statement": scores, "evidence_support": supported_statements/total_statements if total_statements else None,
              "strength_context_accuracy": accuracy, "selector_recall": selector_recall,
              "explicit_attributes": attributes,
              "method_signature": next(iter(signatures)) if len(signatures) == 1 else None,
              "taxonomy_version": definitions["version"], "gold_sha256": digest(labels_path.read_bytes()),
              "subgroups": [{"stratum": list(k), **_scores(*v[:3]), "strength_context_accuracy": v[3]/v[4] if v[4] else None} for k,v in subgroup.items()],
              "role_mapping": role_checks, "skill_mapping": skill_checks,
              "limitations": "40 held-out descriptions do not establish universal quality or source representativeness. Wilson statement intervals are descriptive; correlated statements within descriptions reduce effective sample size."}
    write_json(directory / "evaluation.json", result)
    review_path = directory / "gold-reviews" / (result["gold_sha256"] + ".json")
    if not review_path.exists():
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_bytes(labels_path.read_bytes())
    snapshot = directory / "evaluations" / (digest(__import__('json').dumps(result, sort_keys=True)) + ".json")
    if not snapshot.exists():
        write_json(snapshot, result)
    return result


def audit_packet(directory: Path) -> dict:
    observations = {o["document_id"]: o for o in read_jsonl(directory / "observations.jsonl")}
    records = [r for r in effective_extractions(directory) if r.get("accepted") and r["document_id"] in observations]
    by_doc = {r["document_id"]: r for r in records}
    strata = defaultdict(list)
    for r in records:
        strata[stratum(observations[r["document_id"]])].append(observations[r["document_id"]])
    ids = {o["document_id"] for rows in strata.values() for o in round_robin(rows)[:math.ceil(len(rows)*0.1)]}
    flagged = {r["document_id"] for r in records if any(q.get("review_reason") for q in r["requirements"])}
    flagged.update(r["document_id"] for r in records if not r["requirements"])
    flagged.update(s["document_id"] for s in read_jsonl(directory / "source-units.jsonl")
                   if s["document_id"] in by_doc and s.get("selection_status") == "expanded_unknown_structure")
    result = {"fraction": 0.1, "documents": [
        {"document_id": doc, "source_sha256": by_doc[doc]["source_sha256"],
         "cache_key": by_doc[doc]["cache_key"], "reason": "flagged" if doc in flagged else "stratified_sample"}
        for doc in sorted(ids | flagged)], "requires_full_text_omission_audit": True}
    write_json(directory / "audit-packet.json", result)
    return result
