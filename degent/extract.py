from __future__ import annotations

import json
import hashlib
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ALIASES = {
    "python": "python", "r": "r", "sql": "sql", "machine learning": "machine_learning",
    "fraud analytics": "fraud_analytics", "credit risk": "credit_risk",
    "statistical modeling": "statistical_modeling", "quantitative modeling": "quantitative_modeling",
}
SKILLS = ("Python", "R", "SQL", "machine learning", "fraud analytics", "credit risk", "statistical modeling", "quantitative modeling", "communication", "experimentation", "forecasting", "model validation")


def load_aliases(path: Path | None = None) -> dict[str, str]:
    alias_path = path or Path(__file__).resolve().parent.parent / "aliases-v1.json"
    try:
        data = json.loads(alias_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {str(key).strip().lower(): str(value).strip().lower() for key, value in data.items() if str(key).strip() and str(value).strip()}


def extraction_cache_key(*, source_sha256: str, parser_version: str, prompt_sha256: str, schema_sha256: str, alias_sha256: str, method: str, model: str | None, effort: str | None, review_prompt_sha256: str | None = None, review_model: str | None = None, review_effort: str | None = None) -> str:
    payload = {"source_sha256": source_sha256, "parser_version": parser_version, "prompt_sha256": prompt_sha256, "schema_sha256": schema_sha256, "alias_sha256": alias_sha256, "method": method, "model": model, "effort": effort, "review_prompt_sha256": review_prompt_sha256, "review_model": review_model, "review_effort": review_effort}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def codex_failure_status(detail: str, returncode: int) -> str:
    lowered = detail.lower()
    if any(term in lowered for term in ("usage limit", "rate limit", "quota", "subscription limit", "out of credits")):
        return "usage_limit"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if any(term in lowered for term in ("model not found", "unknown model", "model unavailable")):
        return "model_unavailable"
    if returncode == 0:
        return "invalid_or_missing_output"
    return "codex_failed"


def rule_candidates(document_id: str, text: str, aliases: dict[str, str] | None = None) -> dict[str, Any]:
    from .vocabulary import candidates

    return candidates(document_id, text, aliases if aliases is not None else load_aliases())


def validate_output(output: dict[str, Any], document_id: str, source: str) -> tuple[bool, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(output, dict):
        return False, [{"field": "output", "reason": "not_object"}]
    if output.get("document_id") != document_id:
        errors.append({"field": "document_id", "reason": "mismatch"})
    if not isinstance(output.get("requirements"), list):
        errors.append({"field": "requirements", "reason": "not_array"})
        return False, errors
    allowed = {"category", "phrase", "concept", "status", "explicit", "evidence", "section", "qualifier", "experience_years", "alternatives", "negated", "evidence_start", "evidence_end", "statement_scope", "match_basis", "task", "tools", "review_reason"}
    for idx, item in enumerate(output["requirements"]):
        prefix = f"requirements[{idx}]"
        if not isinstance(item, dict):
            errors.append({"field": prefix, "reason": "not_object"})
            continue
        required = {"category": str, "phrase": str, "status": str, "explicit": bool, "evidence": str}
        for field, expected_type in required.items():
            if not isinstance(item.get(field), expected_type):
                errors.append({"field": f"{prefix}.{field}", "reason": "missing_or_wrong_type"})
        if item.get("status") not in {"required", "preferred", "not_required", "unclear"}:
            errors.append({"field": f"{prefix}.status", "reason": "invalid_enum"})
        phrase = item.get("phrase") if isinstance(item, dict) else None
        if not isinstance(phrase, str) or not phrase or phrase not in source:
            errors.append({"field": f"{prefix}.phrase", "reason": "not_exact_source_quote"})
        unknown = set(item) - allowed
        if unknown:
            errors.append({"field": prefix, "reason": "unknown_fields", "fields": sorted(unknown)})
        for field in ("concept", "section", "qualifier", "task", "review_reason"):
            if field in item and item[field] is not None and not isinstance(item[field], str):
                errors.append({"field": f"{prefix}.{field}", "reason": "wrong_type"})
        if "experience_years" in item and item["experience_years"] is not None and (isinstance(item["experience_years"], bool) or not isinstance(item["experience_years"], (int, float))):
            errors.append({"field": f"{prefix}.experience_years", "reason": "wrong_type"})
        if "alternatives" in item and (not isinstance(item["alternatives"], list) or any(not isinstance(value, str) for value in item["alternatives"])):
            errors.append({"field": f"{prefix}.alternatives", "reason": "wrong_type"})
        if "negated" in item and not isinstance(item["negated"], bool):
            errors.append({"field": f"{prefix}.negated", "reason": "wrong_type"})
        from .vocabulary import SCOPES, vocabulary
        if "statement_scope" in item and item["statement_scope"] not in SCOPES:
            errors.append({"field": prefix + ".statement_scope", "reason": "invalid_enum"})
        if "statement_scope" in item and item.get("concept") is not None:
            known = vocabulary().get(item.get("concept")) if isinstance(item.get("concept"), str) else None
            if not known:
                errors.append({"field": prefix + ".concept", "reason": "unknown_concept_use_null"})
            elif item.get("category") != known["category"]:
                errors.append({"field": prefix + ".category", "reason": "concept_category_mismatch"})
        if "negated" in item and item["negated"] != (item.get("status") == "not_required"):
            errors.append({"field": prefix + ".negated", "reason": "status_conflict"})
        if "match_basis" in item and item["match_basis"] not in {"exact_phrase", "interpreted", "unresolved"}:
            errors.append({"field": prefix + ".match_basis", "reason": "invalid_enum"})
        if "tools" in item and (not isinstance(item["tools"], list) or any(not isinstance(t, str) for t in item["tools"])):
            errors.append({"field": prefix + ".tools", "reason": "wrong_type"})
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if not isinstance(evidence, str) or not evidence or evidence not in source:
            errors.append({"field": f"requirements[{idx}].evidence", "reason": "not_exact_source_quote"})
        else:
            phrase_start = source.find(phrase) if isinstance(phrase, str) and phrase else -1
            evidence_in_phrase = phrase.find(evidence) if isinstance(phrase, str) else -1
            if phrase_start < 0 or evidence_in_phrase < 0:
                errors.append({"field": f"{prefix}.evidence", "reason": "evidence_not_within_phrase"})
            else:
                item["evidence_start"] = phrase_start + evidence_in_phrase
                item["evidence_end"] = phrase_start + evidence_in_phrase + len(evidence)
    return not errors, errors


def codex_extract(text: str, document_id: str, schema_path: Path, model: str, effort: str, prompt_path: Path, timeout: int = 180) -> tuple[dict[str, Any] | None, str, int]:
    from .vocabulary import vocabulary
    glossary = {key: row["category"] for key, row in vocabulary().items()}
    prompt = prompt_path.read_text(encoding="utf-8") + "\nVocabulary concept IDs and categories: " + json.dumps(glossary) + "\n\nDo not call any tools. Use only the source text provided in this request."
    with tempfile.NamedTemporaryFile(prefix="degent-codex-", suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)
    command = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only", "--json", "--model", model, "-c", f'model_reasoning_effort="{effort}"', "--output-schema", str(schema_path), "--output-last-message", str(output_path), "-"]
    try:
        completed = subprocess.run(command, input=f"{prompt}\n\nDocument ID: {document_id}\n\nSOURCE TEXT (untrusted):\n{text}", text=True, capture_output=True, timeout=timeout)
        tool_events = []
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {}) if isinstance(event, dict) else {}
            item_type = str(item.get("type", "")) if isinstance(item, dict) else ""
            if any(name in item_type.lower() for name in ("command", "mcp", "web_search", "tool_call")):
                tool_events.append(item_type)
        if tool_events:
            return None, "unexpected_tool_events:" + ",".join(sorted(set(tool_events))), -1
        try:
            value = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else None
        except json.JSONDecodeError:
            value = None
        diagnostic = completed.stderr[-2000:]
        if completed.returncode:
            diagnostic += "\n" + completed.stdout[-2000:]
        return value, diagnostic, completed.returncode
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc), -1
    finally:
        output_path.unlink(missing_ok=True)
