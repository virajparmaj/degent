"""Resumable extraction with durable call reservations and versioned results."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable

from .classify import classify_geography, classify_role
from .extract import codex_failure_status, extraction_cache_key, rule_candidates, validate_output
from .store import (
    append_jsonl,
    body_usability,
    clean_html,
    digest,
    read_jsonl,
    utc_now,
    write_jsonl,
)
from .vocabulary import VERSION, VOCABULARY_PATH, vocabulary

ROOT = Path(__file__).resolve().parent.parent


def refresh_observations(directory: Path, config: dict) -> list[dict]:
    from .phase_state import reject_broad
    reject_broad(config, directory=directory)
    from .cli import _title_from_body

    rows = read_jsonl(directory / "observations.jsonl")
    for row in rows:
        raw = Path(row.get("raw_file", ""))
        clean = Path(row["clean_file"])
        if config.get("study_version") != 1 and row.get("content_format") != "posting-json" and raw.is_file():
            payload = raw.read_bytes()
            text = clean_html(payload)
            clean.parent.mkdir(parents=True, exist_ok=True)
            clean.write_text(text, encoding="utf-8")
            row.update(
                clean_sha256=digest(text),
                parser_version="html-text-v3",
                title=_title_from_body(payload, row.get("original_url", "")),
            )
        else:
            text = clean.read_text(encoding="utf-8") if clean.is_file() else ""
            if digest(text) != row.get("clean_sha256"):
                row.update(
                    eligible=False,
                    body_complete=False,
                    body_status="source_hash_mismatch",
                    eligible_for_primary=False,
                )
                continue
        usable, reason = body_usability(text, row.get("employer", ""))
        if config.get("study_version") == 1:
            from .evidence import decorate_observation
            decorate_observation(row, config, text)
            if row.get("raw_source_sha256"):
                raw_source = Path(row.get("raw_source_file") or "")
                if not raw_source.is_file() or digest(raw_source.read_bytes()) != row["raw_source_sha256"]:
                    row.update(eligible=False, body_complete=False, eligible_for_primary=False,
                               body_status="raw_source_hash_mismatch")
            for ref in row.get("evidence_references", []):
                path = Path(ref["path"])
                if not path.is_file() or digest(path.read_bytes()) != ref["sha256"]:
                    row.update(eligible=False, body_complete=False, eligible_for_primary=False,
                               body_status="historical_evidence_hash_mismatch")
                    break
            continue
        if row.get("raw_source_sha256"):
            source_raw = Path(row.get("raw_source_file") or "")
            if not source_raw.is_file() or digest(source_raw.read_bytes()) != row.get("raw_source_sha256"):
                usable, reason = False, "raw_source_hash_mismatch"
            elif row.get("source") == "capital_one" and row.get("verified_employer") != "Capital One":
                usable, reason = False, "employer_not_verified"
        role = classify_role(row.get("title", ""), text, config)
        geo = classify_geography(row.get("original_url", ""), text)
        complete = (
            usable and role["eligible"] and geo["status"] in {"us_location", "remote_us_eligible"}
        )
        row.update(
            role_classification=role,
            role_track=role["tracks"][0] if role["eligible"] else None,
            role_subtype=role.get("subtype", "unknown"),
            level=role["level"],
            eligible=role["eligible"],
            geography_status=geo["status"],
            geography_evidence=geo["evidence"],
            body_text_complete=usable,
            body_complete=complete,
            eligible_for_primary=complete and bool(row.get("capture_date")),
            body_status=(
                reason if not usable else role["reason"] if not role["eligible"] else geo["status"]
            ),
            classification_version="title-context-v4",
        )
    write_jsonl(directory / "observations.jsonl", rows)
    return rows


def reserved_call(
    directory: Path,
    manifest: dict,
    config: dict,
    runner: Callable,
    *,
    text: str,
    doc_id: str,
    review: bool = False,
):
    from .cli import save_manifest

    calls = int(manifest.get("model_calls_used", 0))
    if calls >= int(config.get("model_call_cap", 40)):
        raise RuntimeError("model_call_limit: explicitly extend the saved model allowance or create a new bounded run")
    if review and int(manifest.get("semantic_review_calls", 0)) >= int(
        config["semantic_review_call_cap"]
    ):
        raise RuntimeError("semantic_review_call_limit")
    manifest["model_calls_used"] = calls + 1
    if review:
        manifest["semantic_review_calls"] = int(manifest.get("semantic_review_calls", 0)) + 1
    manifest["active_action"] = {
        "stage": "extract",
        "document_id": doc_id,
        "action": "Review wording" if review else "Read requirements",
    }
    save_manifest(directory, manifest)
    append_jsonl(
        directory / "activity.jsonl",
        [{**manifest["active_action"], "at": utc_now(), "model_call": calls + 1}],
    )
    output, detail, code = runner(
        text,
        doc_id,
        ROOT / "schemas/requirement-v2.schema.json",
        config["review_model" if review else "codex_model"],
        config["review_effort" if review else "codex_effort"],
        ROOT / ("prompts/review-v2.md" if review else "prompts/extract-v2.md"),
    )
    append_jsonl(
        directory / "model-outputs.jsonl",
        [
            {
                "document_id": doc_id,
                "call": calls + 1,
                "review": review,
                "output": output,
                "detail": detail[-2000:],
                "returncode": code,
                "at": utc_now(),
            }
        ],
    )
    # Category is a property of our versioned concept, not a separate model judgment.
    if isinstance(output, dict) and isinstance(output.get("requirements"), list):
        concepts = vocabulary()
        for row in output["requirements"]:
            if (
                isinstance(row, dict)
                and isinstance(row.get("concept"), str)
                and row["concept"] in concepts
            ):
                row["category"] = concepts[row["concept"]]["category"]
    return output, detail, code


def extract_run(config: dict, run_id: str, use_codex: bool, runner: Callable) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    from .cli import manifest_for, run_directory, save_manifest

    directory = run_directory(config, run_id)
    manifest = manifest_for(directory, run_id)
    config = {**config, **manifest.get("extraction_budgets", {})}
    manifest.pop("extraction_pause_reason", None)
    manifest["extraction_status"] = "needs_review"
    observations = refresh_observations(directory, config)
    eligible = {
        row["document_id"]: row
        for row in observations
        if row.get("body_complete") and row.get("eligible")
    }
    old = read_jsonl(directory / "requirements.jsonl")
    latest = {row["document_id"]: row for row in old}
    schema_sha = digest((ROOT / "schemas/requirement-v2.schema.json").read_bytes())
    prompt_sha = digest((ROOT / "prompts/extract-v2.md").read_bytes())
    review_sha = digest((ROOT / "prompts/review-v2.md").read_bytes())
    vocab_sha = digest(
        VOCABULARY_PATH.read_bytes()
        + (ROOT / "aliases-v1.json").read_bytes()
        + (ROOT / "degent/vocabulary.py").read_bytes()
    )
    attempts = manifest.setdefault("extraction_attempts", {})
    processed = 0
    from .snapshots import SnapshotStore
    shared = SnapshotStore(config) if config.get("collection_version") == 2 else None
    for doc_id, observation in eligible.items():
        key = extraction_cache_key(
            source_sha256=observation["clean_sha256"],
            parser_version=observation.get("parser_version", "unknown"),
            prompt_sha256=prompt_sha,
            schema_sha256=schema_sha,
            alias_sha256=vocab_sha,
            method="codex" if use_codex else "rules",
            model=config["codex_model"] if use_codex else None,
            effort=config["codex_effort"] if use_codex else None,
            review_prompt_sha256=review_sha if use_codex else None,
            review_model=config["review_model"] if use_codex else None,
            review_effort=config["review_effort"] if use_codex else None,
        )
        if latest.get(doc_id, {}).get("cache_key") == key and latest[doc_id].get("accepted"):
            continue
        source = Path(observation["clean_file"]).read_text(encoding="utf-8")
        cached = shared.artifact(key) if shared else None
        if cached:
            reused = copy.deepcopy(cached)
            reused["document_id"] = doc_id
            valid, errors = validate_output({"document_id": doc_id, "requirements": reused.get("requirements")}, doc_id, source)
            if valid:
                reused.update(extracted_at=utc_now(), reused_artifact=True, review_status="needs_review", validation_errors=[])
                append_jsonl(directory / "requirements.jsonl", [reused])
                latest[doc_id] = reused
                processed += 1
                continue
        if doc_id not in attempts and len(attempts) >= int(config["extract_document_cap"]):
            manifest["extraction_status"] = "document_limit"
            manifest["extraction_pause_reason"] = "extraction_document_limit"
            break
        attempt_key = key
        prior = attempts.setdefault(doc_id, {})
        if int(prior.get(attempt_key, 0)) >= int(config.get("model_retry_cap", 2)):
            continue
        prior[attempt_key] = int(prior.get(attempt_key, 0)) + 1
        save_manifest(directory, manifest)
        source = Path(observation["clean_file"]).read_text(encoding="utf-8")
        result = rule_candidates(doc_id, source)
        valid, errors = validate_output(result, doc_id, source)
        method = "rules"
        stop_reason = None
        if use_codex:
            try:
                output, detail, code = reserved_call(
                    directory, manifest, config, runner, text=source, doc_id=doc_id
                )
                if code == 0 and output is not None:
                    valid, errors = validate_output(output, doc_id, source)
                    if valid:
                        result, method = output, "codex"
                else:
                    valid = False
                    stop_reason = codex_failure_status(detail, code)
                    errors = [{"field": "codex", "reason": stop_reason, "detail": detail[-500:]}]
                ambiguous = any(
                    r.get("review_reason") or r.get("status") == "unclear" or not r.get("concept")
                    for r in result["requirements"]
                )
                if (
                    method == "codex"
                    and ambiguous
                    and int(manifest.get("semantic_review_calls", 0))
                    < int(config["semantic_review_call_cap"])
                ):
                    reviewed, detail, code = reserved_call(
                        directory,
                        manifest,
                        config,
                        runner,
                        text=source + "\n\nCANDIDATE EXTRACTION:\n" + json.dumps(result),
                        doc_id=doc_id,
                        review=True,
                    )
                    review_valid, review_errors = (
                        validate_output(reviewed, doc_id, source)
                        if code == 0 and reviewed is not None
                        else (
                            False,
                            [{"field": "review", "reason": codex_failure_status(detail, code)}],
                        )
                    )
                    if review_valid:
                        result = reviewed
                    else:
                        errors.extend(review_errors)
                        stop_reason = codex_failure_status(detail, code) if code else None
            except RuntimeError as exc:
                stop_reason = str(exc).split(":")[0]
                if method != "codex":
                    valid = False
                errors.append({"field": "budget", "reason": stop_reason})
        method_signature = digest(
            json.dumps(
                [
                    VERSION,
                    vocab_sha,
                    prompt_sha,
                    schema_sha,
                    method,
                    observation.get("parser_version"),
                    config["codex_model"] if use_codex else None,
                    config["codex_effort"] if use_codex else None,
                    (
                        [review_sha, config["review_model"], config["review_effort"]]
                        if use_codex
                        else None
                    ),
                ]
            )
        )
        result.update(
            document_id=doc_id,
            accepted=valid,
            validation_errors=errors,
            extraction_method=method,
            cache_key=key,
            source_sha256=observation["clean_sha256"],
            prompt_sha256=prompt_sha,
            schema_sha256=schema_sha,
            alias_map_version=VERSION,
            vocabulary_version=VERSION,
            method_signature=method_signature,
            extracted_at=utc_now(),
            review_status="needs_review",
        )
        append_jsonl(directory / "requirements.jsonl", [result])
        latest[doc_id] = result
        if shared and valid and not stop_reason:
            shared.put_artifact(key, result)
        processed += 1
        manifest["counts"]["extraction_documents"] = len(latest)
        manifest["codex_extraction_enabled"] = use_codex
        manifest["extraction_status"] = (
            "stopped_by_limit"
            if stop_reason and stop_reason.endswith("_limit")
            else (
                "waiting_for_provider"
                if stop_reason
                else "needs_review" if valid else "needs_attention"
            )
        )
        save_manifest(directory, manifest)
        if stop_reason:
            manifest["extraction_pause_reason"] = stop_reason
            break
    manifest["counts"]["eligible_observations"] = len(eligible)
    if not eligible:
        manifest["extraction_status"] = "no_evidence"
    elif not manifest.get("extraction_pause_reason") and not any(
        latest.get(doc_id, {}).get("accepted") for doc_id in eligible
    ):
        manifest["extraction_status"] = "needs_attention"
    manifest["active_action"] = None
    save_manifest(directory, manifest)
    packet = [
        "# Requirement review",
        "",
        "Automatic labels are candidates. Review source, meaning, and context before comparing.",
        "",
    ]
    for doc_id, record in latest.items():
        if doc_id not in eligible:
            continue
        packet += [
            f"## {eligible[doc_id]['employer']} — {eligible[doc_id]['title']}",
            f"Source: {eligible[doc_id].get('original_url')}",
            "",
        ]
        for row in record.get("requirements", []):
            packet.append(
                f"- {row.get('concept') or 'New wording'} / {row.get('statement_scope', 'unknown')} / {row['status']}: {row['phrase']}"
            )
    (directory / "review-packet.md").write_text("\n".join(packet) + "\n")
    return {
        "run_dir": str(directory),
        "eligible_documents": len(eligible),
        "excluded_observations": len(observations) - len(eligible),
        "extracted": processed,
        "codex": use_codex,
        "status": manifest.get("extraction_status", "no_evidence"),
        "pause_reason": manifest.get("extraction_pause_reason"),
        "review_packet": str(directory / "review-packet.md"),
    }
