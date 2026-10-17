"""Overlapping index queries preserve repeated acquisitions and prior identities."""

from pathlib import Path

import pytest

from degent.acquisition_inputs import reconcile
from degent.store import read_jsonl, write_jsonl


def record(oid="capture", collection="annual", retrieval="old", req="R1"):
    return dict(
        observation_id=oid,
        collection_id=collection,
        retrieved_at=retrieval,
        requisition_id=req,
        clean_sha256="identical-description",
    )


def test_repeat_capture_retains_both_retrievals_and_prior_metadata(tmp_path):
    root = tmp_path / "collections"
    old = record()
    repeated = record(collection="narrow", retrieval="new")
    write_jsonl(root / "annual/observations.jsonl", [old])
    write_jsonl(root / "narrow/observations.jsonl", [repeated])
    journal = tmp_path / "journal"
    write_jsonl(journal / "source-metadata.jsonl", [old])
    result = reconcile(root, journal, tmp_path / "runs")
    rows = read_jsonl(Path(result["run_dir"]) / "observations.jsonl")
    assert old in rows and len(rows) == 2
    alias = next(r for r in rows if r != old)
    assert alias["acquisition_identity"]["original_metadata"] == repeated
    assert alias["acquisition_identity"]["native_observation_id"] == old["observation_id"]
    assert read_jsonl(root / "narrow/observations.jsonl") == [repeated]
    assert result["observations_discarded"] == result["automatic_opening_merges"] == 0
    assert reconcile(root, journal, tmp_path / "runs")["run_dir"] == result["run_dir"]
    # Appending later observations must not rewrite the earlier alias metadata.
    write_jsonl(journal / "source-metadata.jsonl", rows)
    write_jsonl(root / "narrow/observations.jsonl", [repeated, record("second", "narrow")])
    newer = reconcile(root, journal, tmp_path / "runs")
    for r in rows:
        assert r in read_jsonl(Path(newer["run_dir"]) / "observations.jsonl")


def test_same_text_different_requisitions_are_never_merged(tmp_path):
    root = tmp_path / "collections"
    originals = [record("first", req="R1"), record("second", req="R2")]
    write_jsonl(root / "annual/observations.jsonl", originals)
    journal = tmp_path / "journal"
    write_jsonl(journal / "source-metadata.jsonl", [])
    result = reconcile(root, journal, tmp_path / "runs")
    assert result["observations"] == result["native_capture_identities"] == 2
    assert read_jsonl(Path(result["run_dir"]) / "observations.jsonl") == originals


def test_changed_prior_metadata_and_tampered_reconciliation_fail_closed(tmp_path):
    root = tmp_path / "collections"
    journal = tmp_path / "journal"
    write_jsonl(journal / "source-metadata.jsonl", [record()])
    write_jsonl(root / "annual/observations.jsonl", [record(req="changed")])
    with pytest.raises(ValueError, match="changed or disappeared"):
        reconcile(root, journal, tmp_path / "runs")
    write_jsonl(root / "annual/observations.jsonl", [record()])
    result = reconcile(root, journal, tmp_path / "runs")
    (Path(result["run_dir"]) / "observations.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="Immutable acquisition input"):
        reconcile(root, journal, tmp_path / "runs")
