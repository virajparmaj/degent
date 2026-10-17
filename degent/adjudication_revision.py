"""Carry full-text reviews into a larger inventory without re-adjudicating them."""

from __future__ import annotations

import copy
from pathlib import Path

from .adjudication import append_batch, history, initialize, sources
from .analysis_text import reviewed_analysis_text
from .store import digest, read_json, write_json


def _unchanged(path: Path, value: dict) -> None:
    if path.exists() and read_json(path) != value:
        raise ValueError("Immutable journal revision request changed.")
    if not path.exists():
        write_json(path, value)


def _verify_parent(parent: Path, child: Path) -> list[dict]:
    old, old_metadata, _ = sources(parent)
    new, new_metadata, _ = sources(child)
    if (
        read_json(parent / "manifest.json")["policy_sha256"]
        != read_json(child / "manifest.json")["policy_sha256"]
    ):
        raise ValueError("Policy changed; previous eligibility cannot silently carry forward.")
    for oid, row in old.items():
        if (
            oid not in new
            or row["source_sha256"] != new[oid]["source_sha256"]
            or old_metadata[oid] != new_metadata[oid]
        ):
            raise ValueError("Original observation/text/flags changed or disappeared.")
    batches = history(parent)
    if len(list((parent / "batches").glob("*.json"))) != len(batches):
        raise ValueError("Parent has an uncommitted batch; recover it before revision.")
    return batches


def carry_forward(
    parent: Path,
    inventory: Path,
    child: Path,
    *,
    identity_reason_amendments: dict | None = None,
    boilerplate_amendments: dict | None = None,
) -> dict:
    """Carry reviews, preserving explicit source-bound identity/omission amendments."""
    if child.resolve() == parent.resolve():
        raise ValueError("A revision needs a separate journal directory.")
    initialize(child, inventory)
    batches = _verify_parent(parent, child)
    amendments = identity_reason_amendments or {}
    known = {
        "|".join(i["observation_ids"]) for b in batches for i in b["payload"].get("identities", [])
    }
    if set(amendments) - known or any(
        not isinstance(reason, str) or not reason.strip() for reason in amendments.values()
    ):
        raise ValueError("Identity amendment needs a known reviewed identity and explicit prose.")
    omissions = boilerplate_amendments or {}
    decisions = {d["observation_id"]: d for b in batches for d in b["payload"]["decisions"]}
    _, _, texts = sources(parent)
    for oid, amendment in omissions.items():
        old = decisions.get(oid)
        reviewer = amendment.get("reviewer", {})
        if (
            old is None
            or amendment.get("source_sha256") != old["source_sha256"]
            or not amendment.get("reason")
            or not amendment.get("reviewed_at")
            or reviewer.get("kind") != "agent"
            or not reviewer.get("name")
            or not reviewer.get("method")
            or amendment.get("human_gold") is not False
            or amendment.get("inference_authorized") is not False
            or amendment.get("boilerplate_review", {}).get("full_text_review_complete") is not True
        ):
            raise ValueError(
                "Omission amendment needs a reviewed source and honest agent provenance."
            )
        omission = amendment["boilerplate_review"]
        reviewed_analysis_text(
            texts[old["source_sha256"]], omission["removed_spans"], omission["retained_role_spans"]
        )
    request = {
        "parent_directory": str(parent.resolve()),
        "parent_manifest_sha256": digest((parent / "manifest.json").read_bytes()),
        "parent_head": read_json(parent / "head.json") if batches else None,
        "identity_reason_amendments": amendments,
        "reviewer_kind": "agent",
        "human_gold": False,
        "inference_authorized": False,
        "new_full_text_reviews": 0,
        "source_metadata_and_flags_unchanged": True,
    }
    if omissions:
        request["boilerplate_amendments"] = omissions
    _unchanged(child / "lineage" / "revision-request.json", request)
    seen = set()
    for batch in batches:
        payload = copy.deepcopy(batch["payload"])
        key = f"{batch['sequence']:06}"
        prior = parent / "batches" / (key + ".json")
        _unchanged(child / "lineage" / "parent-batches" / (key + ".json"), batch)
        payload["carried_from"] = {
            "journal": str(parent.resolve()),
            "batch_sha256": digest(prior.read_bytes()),
            "new_full_text_attestation": False,
        }
        for decision in payload["decisions"]:
            oid = decision["observation_id"]
            if oid in omissions:
                decision["boilerplate_review"] = copy.deepcopy(omissions[oid]["boilerplate_review"])
        for identity in payload.get("identities", []):
            ids = "|".join(identity["observation_ids"])
            if ids in amendments:
                identity["reason"] = amendments[ids]
                seen.add(ids)
        append_batch(child, payload)
    if seen != set(amendments):
        raise ValueError("Identity amendment targets an unreviewed or unknown identity.")
    result = {
        **request,
        "carried_batches": len(batches),
        "carried_observations": sum(len(b["payload"]["decisions"]) for b in batches),
        "amended_identity_reasons": len(seen),
    }
    if omissions:
        result["amended_boilerplate_reviews"] = len(omissions)
    _unchanged(child / "lineage" / "receipt.json", result)
    return result
