"""Review lineage cannot fabricate a fresh review or silently change its policy."""

import copy
from pathlib import Path

import pytest
from test_adjudication import journal

from degent.adjudication import append_batch, history
from degent.adjudication_revision import carry_forward
from degent.multiyear import build
from degent.store import digest, read_json, write_json


def test_carry_preserves_parent_and_records_no_new_reviews(journal, tmp_path):
    parent, batch, _, _ = journal
    append_batch(parent, batch)
    sha = digest((parent / "batches/000001.json").read_bytes())
    inventory = Path(read_json(parent / "manifest.json")["inventory"])
    child = tmp_path / "child"
    result = carry_forward(parent, inventory, child, identity_reason_amendments={"a": "Source R1."})
    assert result["new_full_text_reviews"] == 0 and result["carried_observations"] == 1
    assert digest((parent / "batches/000001.json").read_bytes()) == sha
    carried = history(child)[0]["payload"]
    assert carried["decisions"] == batch["decisions"]
    assert carried["carried_from"]["batch_sha256"] == sha
    assert carried["identities"][0]["reason"] == "Source R1."
    assert (
        carry_forward(parent, inventory, child, identity_reason_amendments={"a": "Source R1."})
        == result
    )


def test_unknown_identity_amendment_blocks_before_any_review_carries(journal, tmp_path):
    parent, batch, _, _ = journal
    append_batch(parent, batch)
    inventory = Path(read_json(parent / "manifest.json")["inventory"])
    child = tmp_path / "child"
    with pytest.raises(ValueError, match="known reviewed identity"):
        carry_forward(parent, inventory, child, identity_reason_amendments={"unknown": "Reason"})
    assert not history(child)


def test_changed_policy_cannot_carry_old_eligibility(journal, tmp_path):
    parent, batch, source, _ = journal
    append_batch(parent, batch)
    policy = tmp_path / "new-policy.json"
    write_json(policy, {"annual_target": 40})
    inventory = Path(build(source.parent, policy, tmp_path / "revised-inventory")["run_dir"])
    with pytest.raises(ValueError, match="Policy changed"):
        carry_forward(parent, inventory, tmp_path / "child")


def test_omission_revision_preserves_original_decision_and_explicit_audit(journal, tmp_path):
    parent, batch, source, _ = journal
    text = source.read_text()
    append_batch(parent, batch)
    before = (parent / "batches/000001.json").read_bytes()
    view = copy.deepcopy(batch["decisions"][0]["boilerplate_review"])
    view["removed_spans"] = [dict(start=0, end=11, text=text[:11], reason="company name")]
    amendment = dict(
        source_sha256=digest(text),
        reason="Reviewed omission correction",
        reviewed_at="2026-10-10T00:00:00Z",
        reviewer=batch["reviewer"],
        human_gold=False,
        inference_authorized=False,
        boilerplate_review=view,
    )
    inventory = Path(read_json(parent / "manifest.json")["inventory"])
    child = tmp_path / "corrected"
    result = carry_forward(parent, inventory, child, boilerplate_amendments={"a": amendment})
    carried = history(child)[0]["payload"]["decisions"][0]
    expected = {**batch["decisions"][0], "boilerplate_review": view}
    assert carried == expected
    assert (parent / "batches/000001.json").read_bytes() == before
    assert result["new_full_text_reviews"] == 0
    assert result["amended_boilerplate_reviews"] == 1
    assert read_json(child / "lineage/revision-request.json")["boilerplate_amendments"] == {
        "a": amendment
    }
    amendment["human_gold"] = True
    with pytest.raises(ValueError, match="honest agent provenance"):
        carry_forward(
            parent, inventory, tmp_path / "invalid", boilerplate_amendments={"a": amendment}
        )
