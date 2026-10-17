"""Preserve overlapping archive acquisitions without overwriting observation identities."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from pathlib import Path

from .store import digest, operation_lock, read_json, read_jsonl, write_json, write_jsonl


def reconcile(root: Path, journal: Path, parent: Path) -> dict:
    """Freeze all acquisitions, pin prior metadata, and alias only colliding new records.

    The native collection files remain untouched. A second retrieval of the same
    capture is an acquisition observation, never evidence for a second opening.
    Exact input metadata and collection hashes stay in the reconciliation receipt.
    """
    paths = sorted(root.glob("*/observations.jsonl"))
    inputs = {str(p.resolve()): digest(p.read_bytes()) for p in paths}
    prior = {r["observation_id"]: r for r in read_jsonl(journal / "source-metadata.jsonl")}
    groups = defaultdict(list)
    for p in paths:
        for index, row in enumerate(read_jsonl(p)):
            groups[row["observation_id"]].append((p, index, row))
    rows, acquisitions = [], []
    for native_id, members in sorted(groups.items()):
        pinned = prior.get(native_id)
        matching = [m for m in members if m[2] == pinned]
        if pinned is not None and not matching:
            raise ValueError("Previously preserved acquisition metadata changed or disappeared.")
        canonical = matching[0] if matching else members[0]
        for p, index, original in members:
            row = copy.deepcopy(original)
            if (p, index, original) != canonical:
                key = {"input": str(p.resolve()), "row_index": index, "original": original}
                suffix = digest(json.dumps(key, sort_keys=True, ensure_ascii=False))
                row["observation_id"] = native_id + ":acquisition:" + suffix
                row["acquisition_identity"] = {
                    "native_observation_id": native_id,
                    "original_metadata": original,
                    "input_file": str(p.resolve()),
                    "input_record_sha256": digest(
                        json.dumps(original, sort_keys=True, ensure_ascii=False)
                    ),
                    "input_row_index": index,
                    "opening_identity_inferred": False,
                    "reason": "Repeated native capture identity acquired through another input.",
                }
            rows.append(row)
            acquisitions.append(
                {
                    "observation_id": row["observation_id"],
                    "native_observation_id": native_id,
                    "input_file": str(p.resolve()),
                    "input_row_index": index,
                    "original_metadata_sha256": digest(
                        json.dumps(original, sort_keys=True, ensure_ascii=False)
                    ),
                }
            )
    if len({r["observation_id"] for r in rows}) != len(rows):
        raise ValueError("Acquisition identity collision remains unresolved.")
    # Prior derived aliases are pinned too if a reconciliation is continued later.
    by_id = {r["observation_id"]: r for r in rows}
    for oid, row in prior.items():
        if row.get("collection_id") in {p.parent.name for p in paths} and by_id.get(oid) != row:
            raise ValueError("Prior acquisition identity would change; use an explicit revision.")
    manifest = {
        "version": "archive-acquisition-inputs-v1",
        "input_collection_hashes": inputs,
        "parent_metadata_sha256": digest((journal / "source-metadata.jsonl").read_bytes()),
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "acquisitions": acquisitions,
        "observations": len(rows),
        "native_capture_identities": len(groups),
        "new_acquisition_aliases": sum("acquisition_identity" in r for r in rows),
        "observations_discarded": 0,
        "automatic_opening_merges": 0,
    }
    signature = digest(json.dumps(manifest, sort_keys=True))[:16]
    destination = parent / ("capital-one-archive-acquisition-inputs-" + signature)
    with operation_lock(destination):
        if (destination / "receipt.json").exists():
            if read_json(destination / "manifest.json") != manifest:
                raise ValueError("Immutable acquisition manifest changed.")
            for name, sha in read_json(destination / "receipt.json")["files_sha256"].items():
                if digest((destination / name).read_bytes()) != sha:
                    raise ValueError("Immutable acquisition input changed.")
        else:
            write_json(destination / "manifest.json", manifest)
            write_jsonl(destination / "observations.jsonl", rows)
            write_json(
                destination / "receipt.json",
                {
                    "files_sha256": {
                        name: digest((destination / name).read_bytes())
                        for name in ("manifest.json", "observations.jsonl")
                    }
                },
            )
    return {"run_dir": str(destination.resolve()), **manifest}
