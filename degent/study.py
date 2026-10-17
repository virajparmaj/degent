"""A frozen comparison study with explicit inference allowances."""
from __future__ import annotations

import copy
import json
import math
import resource
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .evidence import period_definitions, role_profile, select_units
from .model_runner import canonical_validation, expand_wire, method_signature, normalization_signature, request_for
from .store import append_jsonl, digest, read_jsonl, safe_id, utc_now, write_json, write_jsonl
from .taxonomy import load_taxonomy, propose
from .openrouter import configuration


def study_config(base: dict) -> dict:
    config = copy.deepcopy(base)
    company = next(e for e in config["employers"] if e["id"] == "capital_one")
    from .companies import CompanyLibrary
    saved_company = CompanyLibrary(base).get("capital_one")
    verified_sources = [s for s in saved_company.get("current_sources", [])
                        if s.get("provider") == "capital_one" and s.get("status") == "verified" and s.get("ownership_basis")]
    if verified_sources:
        company = copy.deepcopy(saved_company)
        company["current_sources"] = verified_sources
    company["current_sources"] = [s for s in company["current_sources"] if s["provider"] == "capital_one"]
    if not company["current_sources"]:
        raise ValueError("Capital One official source is required.")
    for source in company["current_sources"]:
        source.update(inventory_mode="all")
        source.setdefault("ownership_basis", "fixed official Capital One adapter; exact employer must be verified in each captured JobPosting")
        source.setdefault("ownership_evidence", [{"url": "https://www.capitalonecareers.com/", "method": "official adapter destination; source employer checked at capture"}])
        source.pop("keywords", None)
        source.pop("max_pages", None)
    config.update(study_version=1, collection_version=2, collection_auto_chunks=True,
                  current_document_cap=None, current_request_cap=None,
                  collection_byte_cap=200_000_000, collection_runtime_seconds=7200,
                  collection_storage_cap=1_000_000_000, extract_document_cap=None,
                  model_call_cap=0, semantic_review_call_cap=0,
                  sources=["current"], employers=[company], dashboard_codex=False,
                  comparison_minimum=30, comparison_review="reviewed",
                  inference=configuration(),
                  evaluation_policy={"calibration_documents": 20, "held_out_documents": 40,
                                     "audit_fraction": 0.1, "precision": 0.95, "recall": 0.90,
                                     "strength_context_accuracy": 0.95},
                  token_planning={"input": 1100, "output": 400, "hard_limit": False})
    config["roles"] = {"tracks": ["knowledge_roles"], "entry_mid_levels": ["entry", "mid", "senior", "management", "executive", "internship", "unknown"], "exclude": []}
    today = datetime.now(ZoneInfo(config["timezone"])).date().isoformat()
    config["periods"] = period_definitions(today)
    config.pop("_path", None)
    return config


def create(base: dict, run_id: str, seed: Path | None = None) -> dict:
    directory = Path(base["run_dir"]) / safe_id(run_id)
    if directory.exists():
        raise ValueError("Existing study preserved; choose a new run ID.")
    config = study_config(base)
    directory.mkdir(parents=True)
    write_json(directory / "dashboard-config.json", config)
    write_json(directory / "company-source.json", {"company": config["employers"][0], "frozen_at": utc_now(),
                                                  "source": "saved verified library record where available; official adapter capture policy otherwise"})
    write_json(directory / "study-policy.json", {
        "version": "comparison-policy-v1", "contract_version": "comparison-contract-v1",
        "scope": "Capital One public US analytics, credit/risk, quantitative modeling, data/AI, engineering; all levels",
        "config_sha256": digest(json.dumps(config, sort_keys=True)),
        "minimum_assessed_vacancies": 30, "human_and_automated_bases": "separate",
        "periods": config["periods"], "evaluation": config["evaluation_policy"],
        "budgets": {"downloaded_bytes": 200_000_000, "active_seconds": 7200, "saved_evidence_bytes": 1_000_000_000},
        "inference": "OpenRouter selected; paid inference allowances deferred", "created_at": utc_now(),
    })
    write_json(directory / "manifest.json", {"run_id": run_id, "created_at": utc_now(), "status": "prepared", "queries": [], "body_attempts": [], "counts": {}})
    if seed:
        rows = []
        for original in read_jsonl(seed / "observations.jsonl"):
            if original.get("employer_id") != "capital_one":
                continue
            row = copy.deepcopy(original)
            for field in ("raw_file", "clean_file", "raw_source_file"):
                if row.get(field):
                    row[field] = str(Path(row[field]).resolve())
            if digest(Path(row["clean_file"]).read_bytes()) != row["clean_sha256"]:
                raise ValueError("Seed source hash mismatch.")
            row.update(seed_run=str(seed.resolve()), description_completeness="unreviewed")
            rows.append(row)
        write_jsonl(directory / "observations.jsonl", rows)
        write_json(directory / "seed-provenance.json", {"run": str(seed.resolve()), "observations": len(rows), "approvals_copied": False})
    prepare(config, run_id)
    return {"run_id": run_id, "run_dir": str(directory.resolve()), "status": "prepared", "inference": "provider_and_authorization_required"}


def configure_model(config: dict, run_id: str, model: str) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    from .cli import run_directory
    directory = run_directory(config, run_id)
    previous = copy.deepcopy(config.get("inference"))
    replacement = configuration(model)
    if previous == replacement:
        return {"status": "unchanged", "model": model}
    write_json(directory / ("config-before-provider-" + digest(json.dumps(config, sort_keys=True))[:12] + ".json"), config)
    config["inference"] = replacement
    write_json(directory / "dashboard-config.json", config)
    append_jsonl(directory / "configuration-decisions.jsonl", [{"at": utc_now(), "kind": "agreed_change",
        "reason": "User selected OpenRouter and explicitly approved these model choices; spending remains deferred",
        "previous_inference": previous, "inference": replacement}])
    return {"status": "configured", "model": model, "inference_enabled": False}


def prepare(config: dict, run_id: str) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    from .cli import manifest_for, run_directory, save_manifest
    from .extraction_run import refresh_observations
    directory = run_directory(config, run_id)
    start, cpu = time.monotonic(), time.process_time()
    observations = refresh_observations(directory, config)
    def observation_stamp(row):
        return row.get("retrieved_at") or row.get("current_capture_at") or row.get("capture_date")
    earliest, version_earliest, historical_earliest = {}, {}, {}
    for row in observations:
        stamp = observation_stamp(row)
        if stamp:
            vacancy, version = row["vacancy_id"], row["document_id"]
            earliest[vacancy] = min(stamp, earliest.get(vacancy, stamp))
            version_earliest[version] = min(stamp, version_earliest.get(version, stamp))
        wording = row.get("verified_historical_wording_at")
        if wording:
            doc = row["document_id"]
            historical_earliest[doc] = min(wording, historical_earliest.get(doc, wording))
    for row in observations:
        for field, candidate in (("collector_first_observed_at", earliest.get(row["vacancy_id"])),
                                 ("version_first_observed_at", version_earliest.get(row["document_id"]))):
            known = row.get(field)
            row[field] = min(known, candidate) if known and candidate else known or candidate
        row["first_observation_basis"] = "earliest preserved study observation and collector index when available"
        if row.get("collection_kind") == "historical":
            row["first_observation_basis"] = "earliest preserved local retrieval for an unresolved version identity; confirmed vacancy first observation unknown"
            row["version_first_verified_wording_at"] = historical_earliest.get(row["document_id"])
    write_jsonl(directory / "observations.jsonl", observations)
    selections, profiles, estimates = {}, {}, []
    for row in observations:
        if not row.get("eligible"):
            continue
        text = Path(row["clean_file"]).read_text()
        selected = select_units(text, row["clean_sha256"])
        selected["document_id"] = row["document_id"]
        selections[row["document_id"]] = selected
        profiles[row["document_id"]] = {**role_profile(selected, row["title"]), "document_id": row["document_id"], "level": row.get("level", "unknown")}
        wire = request_for(selected)
        if (config.get("inference", {}).get("identity") or {}).get("provider") == "openrouter":
            from .openrouter import payload_for
            wire = payload_for(wire, config["inference"]["identity"]["model"])
        chars = len(json.dumps(wire, separators=(",", ":")))
        estimates.append({"document_id": row["document_id"], "selected_characters": selected["selected_characters"],
                          "full_characters": len(text), "input_token_estimate_range": [math.ceil(chars/5), math.ceil(chars/3)],
                          "measured_input_tokens": None, "planning_output_tokens": 400})
    write_jsonl(directory / "source-units.jsonl", selections.values())
    write_jsonl(directory / "role-profiles.jsonl", profiles.values())
    definitions = load_taxonomy(directory)["skills"]
    norm_signature = normalization_signature(definitions)
    latest = {r["document_id"]: r for r in read_jsonl(directory / "requirements.jsonl")}
    for doc, previous in latest.items():
        if not previous.get("accepted") or "wire_output" not in previous or doc not in selections or previous.get("normalization_signature") == norm_signature:
            continue
        source = Path(next(o["clean_file"] for o in observations if o["document_id"] == doc)).read_text()
        result, errors = expand_wire(previous["wire_output"], doc, selections[doc], source, definitions)
        signature = digest(previous["model_signature"] + norm_signature)
        refreshed = {**previous, **result, "accepted": not errors, "validation_errors": errors,
                     "normalization_signature": norm_signature, "method_signature": signature,
                     "cache_key": digest(selections[doc]["source_sha256"] + signature),
                     "extracted_at": utc_now(), "renormalized_locally": True, "requires_omission_audit": True}
        append_jsonl(directory / "requirements.jsonl", [refreshed])
    write_json(directory / "token-estimates.json", {"kind": "character_estimates_not_model_usage", "documents": estimates})
    manifest = manifest_for(directory, run_id)
    manifest["counts"]["eligible_observations"] = sum(bool(r.get("eligible")) for r in observations)
    manifest["preparation"] = {"documents": len(selections), "elapsed_seconds": time.monotonic()-start,
                               "cpu_seconds": time.process_time()-cpu, "process_peak_ram_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if __import__('sys').platform == 'darwin' else 1024),
                               "ram_scope": "whole Python process peak, not per-stage allocation"}
    save_manifest(directory, manifest)
    return manifest["preparation"]


def extract(config: dict, run_id: str, runner=None) -> dict:
    from .phase_state import reject_broad
    reject_broad(config, run_id)
    from .cli import manifest_for, run_directory, save_manifest
    from .snapshots import SnapshotStore
    directory = run_directory(config, run_id)
    if config.get("preparation_only"):
        return {"status": "preparation_only", "reason": "explicit_new_inference_authorization_required", "model_calls": 0}
    prepare(config, run_id)
    manifest = manifest_for(directory, run_id)
    authorization = config.get("inference", {})
    if runner is None and (authorization.get("identity") or {}).get("provider") == "openrouter":
        from .openrouter import OpenRouterRunner, local_settings, model_identity
        settings = local_settings()
        selected = settings.get("OPENROUTER_MODEL") or authorization["identity"]["model"]
        identity = model_identity(selected)
        reason = ("inference_allowance_required" if not authorization.get("enabled") or not authorization.get("spending_authorized")
                  else "model_override_requires_matching_frozen_identity" if identity != authorization["identity"]
                  else "api_key_required" if not settings.get("OPENROUTER_API_KEY") else None)
        if reason:
            manifest.update(extraction_status="waiting_for_allowance" if reason == "inference_allowance_required" else "needs_configuration",
                            extraction_pause_reason=reason)
            save_manifest(directory, manifest)
            return {"status": manifest["extraction_status"], "reason": reason, "model": selected, "model_calls": 0}
        runner = OpenRouterRunner(selected, settings["OPENROUTER_API_KEY"], authorization)
    if runner is None or not authorization.get("enabled") or authorization.get("identity") != runner.identity:
        manifest.update(extraction_status="waiting_for_provider", extraction_pause_reason="provider_selection_and_inference_authorization_required")
        save_manifest(directory, manifest)
        return {"status": manifest["extraction_status"], "reason": manifest["extraction_pause_reason"], "model_calls": 0}
    if authorization.get("paid") and (not authorization.get("spending_authorized") or not authorization.get("pricing") or not authorization.get("cost_limit_usd")):
        raise ValueError("Paid inference requires an explicit spending allowance and dated pricing.")
    if authorization.get("paid"):
        price = authorization["pricing"]
        try:
            allowance = authorization["cost_limit_usd"]
            if type(allowance) not in {int, float} or not math.isfinite(allowance) or allowance <= 0:
                raise ValueError("Invalid spending allowance")
            __import__('datetime').date.fromisoformat(price.get("effective_date") or price.get("as_of"))
            rates = [price["input_per_million"], price["output_per_million"]]
            if any(type(v) not in {int, float} or not math.isfinite(v) or v < 0 for v in rates):
                raise ValueError("Invalid rate")
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Pricing needs an ISO effective_date/as_of and nonnegative input/output rates per million.") from exc
    shared = SnapshotStore(config)
    taxonomy = load_taxonomy(directory)
    model_signature = method_signature(runner.identity)
    norm_signature = normalization_signature(taxonomy["skills"])
    signature = digest(model_signature + norm_signature)
    selections = {s["document_id"]: s for s in read_jsonl(directory / "source-units.jsonl")}
    observations = {o["document_id"]: o for o in read_jsonl(directory / "observations.jsonl") if o.get("eligible")}
    old = {r["document_id"]: r for r in read_jsonl(directory / "requirements.jsonl")}
    usage = manifest.setdefault("inference_usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0})
    attempts = manifest.setdefault("compact_attempts", {})
    processed, reused, stop = 0, 0, None
    for doc, selection in selections.items():
        source = Path(observations[doc]["clean_file"]).read_text()
        key = digest(selection["source_sha256"] + signature)
        raw_key = digest(selection["source_sha256"] + model_signature)
        if old.get(doc, {}).get("cache_key") == key and old[doc].get("accepted"):
            reused += 1
            continue
        cached = shared.artifact(raw_key)
        if cached:
            cached["usage"] = {"measurement": "cache_reuse_no_new_provider_call", "input_tokens": 0, "output_tokens": 0,
                               "original_usage": cached.get("usage")}
            expanded, errors = expand_wire(cached["wire_output"], doc, selection, source, taxonomy["skills"])
            cached.update(expanded, document_id=doc, extracted_at=utc_now(), reused_artifact=True,
                          cache_key=key, method_signature=signature, normalization_signature=norm_signature,
                          validation_errors=errors, requires_omission_audit=True)
            valid, _ = canonical_validation(cached, source)
            if valid and not errors:
                append_jsonl(directory / "requirements.jsonl", [cached])
                reused += 1
                continue
        request = request_for(selection)
        for attempt in range(int(config.get("model_retry_cap", 2))):
            if attempts.get(key, 0) >= int(config.get("model_retry_cap", 2)):
                break
            token_count = runner.count_tokens(request)
            if token_count is not None and (type(token_count) is not int or token_count < 0):
                raise ValueError("Invalid tokenizer count.")
            input_reservation = token_count if token_count is not None else math.ceil(len(json.dumps(request))/3)
            if token_count is None and hasattr(runner, "reserve_input_tokens"):
                input_reservation = runner.reserve_input_tokens(request)
                if type(input_reservation) is not int or input_reservation < 0:
                    raise ValueError("Invalid input allowance reservation.")
            output_reservation = int(authorization.get("max_output_tokens", 4096))
            price = authorization.get("pricing", {})
            cost = (input_reservation*price.get("input_per_million", 0)+output_reservation*price.get("output_per_million", 0))/1_000_000
            if (usage["calls"] >= authorization.get("call_limit", 0)
                or usage["input_tokens"] + input_reservation > authorization.get("input_token_limit", 0)
                or usage["output_tokens"] + output_reservation > authorization.get("output_token_limit", 0)
                or authorization.get("paid") and usage["cost_usd"] + cost > authorization["cost_limit_usd"]):
                stop = "inference_budget_limit"
                break
            usage["calls"] += 1
            usage["input_tokens"] += input_reservation
            usage["output_tokens"] += output_reservation
            usage["cost_usd"] += cost
            attempts[key] = attempts.get(key, 0) + 1
            request["max_output_tokens"] = output_reservation
            manifest["active_action"] = {"stage": "extract", "document_id": doc, "attempt": attempts[key]}
            save_manifest(directory, manifest)  # Reserve before dispatch; crashes never reset usage.
            started, cpu = time.monotonic(), time.process_time()
            try:
                response = runner.extract(request)
                result, errors = expand_wire(response.output, doc, selection, source, taxonomy["skills"])
                valid, canonical_errors = canonical_validation(result, source)
                errors += canonical_errors
                if response.finish_reason not in {"stop", "completed"}:
                    errors.append({"reason": "incomplete_model_output", "finish_reason": response.finish_reason})
                actual_input, actual_output = response.usage.get("input_tokens"), response.usage.get("output_tokens")
                actual_cost = response.usage.get("cost_usd")
                if actual_cost is not None and (type(actual_cost) not in {int, float} or not math.isfinite(actual_cost) or actual_cost < 0):
                    raise ValueError("Invalid billed cost measurement.")
                for value in (actual_input, actual_output):
                    if value is not None and (type(value) is not int or value < 0):
                        raise ValueError("Invalid usage measurement.")
                usage["input_tokens"] += (actual_input if actual_input is not None else input_reservation) - input_reservation
                usage["output_tokens"] += (actual_output if actual_output is not None else output_reservation) - output_reservation
                if actual_cost is not None:
                    usage["cost_usd"] += actual_cost - cost
                elif actual_input is not None and actual_output is not None:
                    usage["cost_usd"] += (actual_input*price.get("input_per_million", 0)+actual_output*price.get("output_per_million", 0))/1_000_000 - cost
                diagnostics = response.diagnostics
                output = response.output
            except (RuntimeError, ValueError, OSError, TypeError) as exc:
                result, valid, errors = {"document_id": doc, "requirements": []}, False, [{"reason": "provider_failed", "detail": str(exc)[:500]}]
                actual_input = actual_output = None
                actual_cost = None
                diagnostics, output = str(exc)[:500], None
            model_usage = {"input_tokens": actual_input, "output_tokens": actual_output,
                           "cost_usd": actual_cost, "cost_basis": "provider_reported" if actual_cost is not None else "dated_price_estimate_or_reserved",
                           "provider_usage": response.usage if actual_input is not None or actual_output is not None else None,
                           "reserved_input_tokens": input_reservation, "reserved_output_tokens": output_reservation,
                           "measurement": "actual" if actual_input is not None and actual_output is not None else "incomplete_reserved_allowance",
                           "elapsed_seconds": time.monotonic()-started, "local_cpu_seconds": time.process_time()-cpu,
                           "remote_resources": "unmeasured", "pricing": price or None}
            append_jsonl(directory / "model-outputs.jsonl", [{"document_id": doc, "cache_key": key, "output": output, "diagnostics": diagnostics, "usage": model_usage, "at": utc_now()}])
            result.update(accepted=valid and not errors, validation_errors=errors, extraction_method="structured",
                          model_identity=runner.identity,
                          method_signature=signature, model_signature=model_signature, normalization_signature=norm_signature,
                          wire_output=output, cache_key=key, source_sha256=selection["source_sha256"],
                          selector_version=selection["selector_version"], selected_unit_ids=selection["selected_unit_ids"],
                          requires_omission_audit=True, review_status="needs_review", extracted_at=utc_now(),
                          description_completeness="unreviewed", selection_review="unreviewed", usage=model_usage)
            result["capture_completeness"] = ("full_provider_description" if observations[doc].get("verified_employer") == "Capital One" and observations[doc].get("raw_source_sha256") else "unverified")
            append_jsonl(directory / "requirements.jsonl", [result])
            if result["accepted"]:
                shared.put_artifact(raw_key, result)
                processed += 1
                break
        if stop:
            break
    latest_results = {r["document_id"]: r for r in read_jsonl(directory / "requirements.jsonl")}
    failed = sum(not r.get("accepted") for r in latest_results.values())
    manifest.update(extraction_status="stopped_by_limit" if stop else "needs_attention" if failed else "needs_review", extraction_pause_reason=stop, active_action=None)
    manifest["counts"]["extraction_documents"] = sum(bool(r.get("accepted")) for r in latest_results.values())
    save_manifest(directory, manifest)
    return {"status": manifest["extraction_status"], "extracted": processed, "reused": reused, "usage": usage}


def review_document(config: dict, run_id: str, payload: dict) -> dict:
    from .cli import run_directory
    from .review import effective_extractions, extraction_identity
    directory = run_directory(config, run_id)
    record = next((r for r in effective_extractions(directory) if r["document_id"] == payload.get("document_id") and r.get("accepted")), None)
    if not record or payload.get("extraction_id") != extraction_identity(record):
        raise ValueError("Extraction changed; refresh before reviewing.")
    if payload.get("omission_audited") is not True or payload.get("description_completeness") not in {"complete", "partial"} or payload.get("selection_review") not in {"complete", "partial"}:
        raise ValueError("Record a full-text omission audit and explicit description/selection completeness.")
    observation = next(r for r in read_jsonl(directory / "observations.jsonl") if r["document_id"] == record["document_id"])
    source = Path(observation["clean_file"]).read_text()
    if digest(source) != record["source_sha256"]:
        raise ValueError("Source changed; extract again before reviewing.")
    selection = next(s for s in read_jsonl(directory / "source-units.jsonl") if s["document_id"] == record["document_id"])
    selection = copy.deepcopy(selection)
    for unit in selection["units"]:
        unit["selected"] = True
    additions, errors = expand_wire({"r": payload.get("additions", [])}, record["document_id"], selection, source, load_taxonomy(directory)["skills"])
    if errors:
        raise ValueError("Invalid omitted-statement evidence: " + json.dumps(errors))
    decision = {"document_id": record["document_id"], "extraction_id": extraction_identity(record),
                "source_sha256": record["source_sha256"], "omission_audited": True,
                "description_completeness": payload["description_completeness"], "selection_review": payload["selection_review"],
                "additions": additions["requirements"], "note": payload.get("note", ""),
                "reviewer": "local-user", "reviewed_at": utc_now()}
    append_jsonl(directory / "document-reviews.jsonl", [decision])
    return decision


def run(config: dict, run_id: str) -> dict:
    from .collection import collect
    from .study_metrics import report
    collection = collect(config, run_id)
    prepare(config, run_id)
    propose(Path(config["run_dir"]) / run_id)
    extraction = extract(config, run_id)
    result = report(Path(config["run_dir"]) / run_id, config)
    return {"collection": collection, "extraction": extraction, "report_status": result["status"]}
