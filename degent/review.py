"""Append-only human decisions tied to an exact extraction and source version."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .store import digest, read_jsonl


def requirement_id(record: dict[str, Any], index: int) -> str:
    version = {key: record.get(key) for key in (
        "document_id", "source_sha256", "cache_key", "extracted_at", "requirements"
    )}
    return digest(json.dumps(version, sort_keys=True))[:24] + f":{index}"


def effective_extractions(run_dir: Path) -> list[dict[str, Any]]:
    latest = {row["document_id"]: row for row in read_jsonl(run_dir / "requirements.jsonl")}
    decisions = {row["requirement_id"]: row for row in read_jsonl(run_dir / "reviews.jsonl")}
    documents = {r["extraction_id"]: r for r in read_jsonl(run_dir / "document-reviews.jsonl")}
    result = []
    for original in latest.values():
        record = copy.deepcopy(original)
        record["extraction_id"] = extraction_identity(original)
        for index, requirement in enumerate(record.get("requirements", [])):
            identity = requirement_id(original, index)
            decision = decisions.get(identity)
            requirement["human_review"] = "pending"
            if decision:
                for field in ("status", "concept", "category", "statement_scope", "excluded"):
                    if field in decision:
                        requirement[field] = decision[field]
                requirement["negated"] = requirement.get("status") == "not_required"
                requirement["human_review"] = "approved"
                requirement["review_note"] = decision["note"]
                requirement["review_revision"] = decision["revision"]
            requirement["requirement_id"] = identity
        doc_decision = documents.get(extraction_identity(original))
        record["document_review"] = "approved" if doc_decision else "pending"
        if record.get("requires_omission_audit") and doc_decision and not doc_decision.get("omission_audited"):
            record["document_review"] = "pending"
            doc_decision = None
        if doc_decision:
            record["omission_audited"] = doc_decision.get("omission_audited", False)
            record["description_completeness"] = doc_decision.get("description_completeness", "unreviewed")
            record["selection_review"] = doc_decision.get("selection_review", "unreviewed")
            record["requirements"].extend(copy.deepcopy(doc_decision.get("additions", [])))
            for requirement in record.get("requirements", []):
                requirement["human_review"] = "approved"
                requirement.setdefault("requirement_id", record["extraction_id"] + ":human:" + digest(json.dumps(requirement, sort_keys=True))[:16])
        statuses = [item["human_review"] for item in record.get("requirements", [])]
        record["review_status"] = "human_approved" if statuses and all(
            status == "approved" for status in statuses
        ) else record.get("review_status", "needs_review")
        result.append(record)
    return result


def document_reviewed(record: dict[str, Any]) -> bool:
    """A zero-statement document needs an explicit document review."""
    if record.get('document_review') == 'approved':
        return True
    if record.get('requires_omission_audit'):
        return False
    rows = record.get('requirements', [])
    return bool(rows) and all(r.get('human_review') == 'approved' for r in rows)


def extraction_identity(record: dict[str, Any]) -> str:
    if record.get("extraction_id"):
        return record["extraction_id"]
    return requirement_id(record, 0).rsplit(':', 1)[0]
