"""Offline, content-addressed historical import. No collection or approval transfer."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .evidence import decorate_observation
from .store import digest, operation_lock, read_json, read_jsonl, safe_id, write_json, write_jsonl


def package_file(package: Path, name: str) -> Path:
    path = (package / name).resolve()
    if not path.is_relative_to(package.resolve()) or not path.is_file():
        raise ValueError("Historical evidence path missing or outside package.")
    return path


def validate_package(package: Path) -> tuple[dict, list[dict]]:
    manifest = read_json(package / "manifest.json")
    for name, expected in manifest["files_sha256"].items():
        if digest(package_file(package, name).read_bytes()) != expected:
            raise ValueError("Historical package hash mismatch: " + name)
    records = read_jsonl(package / "historical-postings.jsonl")
    mappings = {m["source_record_id"]: m for m in manifest["records"]}
    if len(mappings) != len(manifest["records"]) or len({r["source_record_id"] for r in records}) != len(records):
        raise ValueError("Duplicate historical observation identity.")
    if {r["source_record_id"] for r in records} != set(mappings):
        raise ValueError("Historical observation coverage mismatch.")
    for record in records:
        mapping = mappings[record["source_record_id"]]
        if digest(record["description"]) != mapping["description_sha256"]:
            raise ValueError("Historical description hash mismatch.")
        if ("comparison_subset=true" in record["notes"]) != mapping["comparison_subset"]:
            raise ValueError("Historical subset flag mismatch.")
        for name in record["evidence_files"]:
            package_file(package, name)
        for name in mapping["original_evidence_files"]:
            if digest(package_file(package, name).read_bytes()) != mapping["original_sha256"]:
                raise ValueError("Historical original evidence hash mismatch.")
    stats = manifest["statistics"]
    if (len(records) != stats["record_count"]
        or len({m["version_key"] for m in mappings.values()}) != stats["canonical_version_count"]
        or sum(m["comparison_subset"] for m in mappings.values()) != stats["comparison_subset_record_count"]):
        raise ValueError("Historical manifest counts mismatch.")
    return manifest, records


def canonical_record(record: dict, mapping: dict, package: Path, destination: Path,
                     collection_id: str, config: dict) -> dict:
    text = record["description"]
    source_hash = digest(text)
    clean = destination / "text" / (source_hash + ".txt")
    original = package_file(package, mapping["original_evidence_files"][0])
    # Source posting groups and requisitions are assertions pending identity review.
    doc = "historical:" + mapping["version_key"]
    retrieval = record["captured_at"]
    from .evidence import date_assertion
    day = date_assertion(retrieval, record["captured_at_provenance"], config["timezone"])["date"]
    verified = mapping.get("verified_archive_capture_at")
    if verified and verified != record["wording_valid_at"]:
        raise ValueError("Conflicting verified historical wording dates.")
    row = {
        "document_id": doc, "content_version_id": doc,
        "observation_id": record["source_record_id"], "source_record_id": record["source_record_id"],
        "vacancy_id": "candidate:" + doc, "identity_status": "unresolved_historical_candidate",
        "source_posting_group": mapping["posting_key"], "source_version_key": mapping["version_key"],
        "source_request_key": mapping["request_key"], "source_mapping": mapping,
        "collection_id": collection_id, "collection_kind": "historical", "source": "historical_import",
        "employer_id": "capital_one", "employer": record["company"], "brand": record["company"],
        "title": record["title"], "original_url": record["url"], "url_aliases": record["url_aliases"],
        "requisition_id": record["requisition_id"], "provider": record["provider"],
        "provider_posting_id": record["provider_posting_id"], "historical_source": record,
        "comparison_subset": mapping["comparison_subset"], "locations_raw": record["locations"],
        "structured_locations": [x["address"] for x in record["locations"] if isinstance(x, dict) and isinstance(x.get("address"), dict)],
        "location": None, "geography_status": "unknown", "remote_eligibility": "unknown",
        "employment_type_raw": record["employment_type"],
        "clean_file": str(clean.resolve()), "clean_sha256": source_hash,
        "raw_file": str(original), "raw_source_file": str(original),
        "raw_source_sha256": mapping["original_sha256"], "parser_version": "archvive-preserved-v1",
        "content_format": "historical-preserved-description", "captured_at": retrieval,
        "retrieved_at": retrieval, "current_capture_at": None,
        "capture_at": retrieval, "capture_date": day, "capture_date_source": record["captured_at_provenance"],
        "collector_first_observed_at": retrieval, "version_first_observed_at": retrieval,
        "verified_historical_wording_at": verified,
        "stated_publication_at": record["published_at"], "publication_date_source": record["published_at_provenance"],
        "stated_updated_at": record["updated_at"], "update_date_source": record["updated_at_provenance"],
        "evidence_references": [{"path": str(package_file(package, n)), "sha256": digest(package_file(package, n).read_bytes())}
                                for n in record["evidence_files"]],
    }
    return decorate_observation(row, config, text)


def import_collection(package: Path, collections: Path, config: dict) -> tuple[Path, list[dict]]:
    package = package.resolve()
    manifest, records = validate_package(package)
    signature = digest((package / "manifest.json").read_bytes())
    collection_id = "historical-" + signature
    destination = collections / collection_id
    mappings = {m["source_record_id"]: m for m in manifest["records"]}
    expected = [canonical_record(r, mappings[r["source_record_id"]], package, destination, collection_id, config)
                for r in records]
    with operation_lock(collections):
        if destination.exists():
            receipt = read_json(destination / "receipt.json")
            if receipt["package_sha256"] != signature or read_jsonl(destination / "observations.jsonl") != expected:
                raise ValueError("Immutable historical collection changed.")
            for name, sha in receipt["files_sha256"].items():
                if digest(package_file(destination, name).read_bytes()) != sha:
                    raise ValueError("Immutable imported evidence changed.")
            return destination, expected
        pending = collections / (".pending-" + signature)
        pending.mkdir(parents=True, exist_ok=True)
        (pending / "receipt.json").unlink(missing_ok=True)
        for row, original in zip(expected, records):
            path = pending / "text" / (row["clean_sha256"] + ".txt")
            path.parent.mkdir(exist_ok=True)
            path.write_text(original["description"], encoding="utf-8")
        write_jsonl(pending / "observations.jsonl", expected)
        hashes = {str(p.relative_to(pending)): digest(p.read_bytes()) for p in pending.rglob("*") if p.is_file()}
        write_json(pending / "receipt.json", {"package_sha256": signature, "package": str(package),
                   "files_sha256": hashes, "observations": len(expected),
                   "versions": len({r["document_id"] for r in expected}), "approvals_copied": False})
        pending.rename(destination)  # Publication is atomic; incomplete staging is safely repeatable.
    return destination, expected


def preparation_config(directory: Path, baseline: Path) -> dict:
    config = copy.deepcopy(read_json(baseline / "dashboard-config.json"))
    config.update(preparation_only=True, sources=[], collection_auto_chunks=False)
    config["inference"].update(enabled=False, spending_authorized=False)
    config.update(model_call_cap=0, semantic_review_call_cap=0)
    saved = directory / "dashboard-config.json"
    if saved.exists():
        config = read_json(saved)
        if not config.get("preparation_only") or config["inference"].get("enabled") or config["inference"].get("spending_authorized"):
            raise ValueError("Preparation run must keep inference disabled.")
    else:
        write_json(saved, config)
    return config


def seed_preparation(directory: Path, baseline: Path, historical: list[dict]) -> None:
    if not (directory / "observations.jsonl").exists():
        current = read_jsonl(baseline / "observations.jsonl")
        for row in current:
            row.update(collection_id="current:" + baseline.name, collection_kind="current",
                       identity_status="confirmed_source_identity", baseline_run=str(baseline.resolve()))
        write_jsonl(directory / "observations.jsonl", current + historical)
    if not (directory / "gold-packet.json").exists():
        packet = read_json(baseline / "gold-packet.json")
        for document in packet["documents"]:
            document.update(labels=None, omission_audited=False, description_completeness=None,
                            selector_omissions=[])
        packet.update(role_pairs=[], skill_pairs=[])
        write_json(directory / "gold-packet.json", packet)


def prepare_historical(base: dict, run_id: str, package: Path, baseline: Path) -> dict:
    """Create/recover a separate preparation run, referencing frozen current evidence."""
    from .study import prepare
    from .study_metrics import report
    from .preparation_audit import audit
    directory = Path(base["run_dir"]) / safe_id(run_id)
    if directory.resolve() == baseline.resolve():
        raise ValueError("Historical preparation requires a separate run.")
    manifest, _ = validate_package(package)
    inputs = {"baseline": str(baseline.resolve()), "package": str(package.resolve()),
              "package_manifest_sha256": digest((package / "manifest.json").read_bytes()),
              "baseline_observations_sha256": digest((baseline / "observations.jsonl").read_bytes()),
              "baseline_config_sha256": digest((baseline / "dashboard-config.json").read_bytes()),
              "baseline_gold_sha256": digest((baseline / "gold-packet.json").read_bytes()),
              "approvals_copied": False}
    marker = directory / "preparation-inputs.json"
    if directory.exists() and not marker.exists():
        raise ValueError("Existing run preserved; use a new preparation run ID.")
    directory.mkdir(parents=True, exist_ok=True)
    with operation_lock(directory):
        if marker.exists() and read_json(marker) != inputs:
            raise ValueError("Frozen preparation inputs changed; choose a new run.")
        write_json(marker, inputs)
        config = preparation_config(directory, baseline)
        collection, historical = import_collection(package, directory / "collections", config)
        seed_preparation(directory, baseline, historical)
        prepare(config, run_id)
        findings = audit(directory, baseline)
        result = report(directory, config)
    return {"run_dir": str(directory.resolve()), "collection": str(collection.resolve()),
            "historical_observations": manifest["statistics"]["record_count"],
            "historical_versions": manifest["statistics"]["canonical_version_count"],
            "status": result["status"], "audit": findings["counts"], "model_calls": 0}
