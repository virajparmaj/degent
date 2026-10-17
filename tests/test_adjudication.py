"""Decision integrity, interruption recovery and honest confirmed-opening counts."""

import copy
from pathlib import Path

import pytest

from degent.adjudication import append_batch, history, initialize, rebuild
from degent.analysis_text import reviewed_analysis_text
from degent.multiyear import build
from degent.store import digest, read_json, read_jsonl, write_json, write_jsonl


def test_conflicting_repeat_text_omissions_cannot_commit_unreportable_batch(journal):
    directory, batch, source, _ = journal
    text = source.read_text()
    append_batch(directory, batch)
    second = copy.deepcopy(batch)
    second["batch_id"] = "second"
    second["decisions"][0]["observation_id"] = "b"
    second["identities"] = []
    second["decisions"][0]["boilerplate_review"]["removed_spans"] = [
        dict(start=0, end=11, text=text[:11], reason="company name")
    ]
    with pytest.raises(ValueError, match="inconsistent omission reviews"):
        append_batch(directory, second)
    assert len(history(directory)) == 1
    assert not (directory / "batches/000002.json").exists()


@pytest.fixture
def journal(tmp_path):
    text = (
        "Capital One\nAnalyst\nLocation: McLean, Virginia, United States\n"
        "What You'll Do:\nAnalyze datasets and build analytical tools.\n"
        "Basic Qualifications:\nBachelor's degree\nAt least 2 years of experience\n"
    )
    source = tmp_path / "source"
    source.mkdir()
    path = source / "source.txt"
    path.write_text(text)
    rows = [
        {
            "observation_id": oid,
            "document_id": oid,
            "title": "Analyst",
            "clean_file": str(path),
            "clean_sha256": digest(text),
            "vacancy_id": oid,
            "collection_kind": "current",
            "current_capture_at": "2026-10-08T10:00:00Z",
            "geography_status": "us_location",
            "requisition_id": req,
            "original_url": f"https://www.capitalonecareers.com/job/x/y/1732/{req[1:]}",
        }
        for oid, req in [("a", "R1"), ("b", "R2")]
    ]
    write_jsonl(source / "observations.jsonl", rows)
    policy = tmp_path / "policy.json"
    write_json(policy, {"annual_target": 30})
    inventory = Path(build(source, policy, tmp_path / "runs")["run_dir"])
    directory = tmp_path / "journal"
    initialize(directory, inventory)

    def span(quote):
        start = text.index(quote)
        return {"start": start, "end": start + len(quote), "text": quote}

    duty = span("Analyze datasets and build analytical tools.")
    decision = {
        "observation_id": "a",
        "source_sha256": digest(text),
        "disposition": "include",
        "full_text_review": {"source_sha256": digest(text), "characters_read": len(text)},
        "reason": "Direct data analysis and two-year IC route.",
        "functions": ["data_analysis_science"],
        "career_assessment": {
            "eligible": True,
            "group": "early_ic",
            "required_evidence": [span("At least 2 years of experience")],
        },
        "scope_assessment": "Direct dataset analysis.",
        "uncertainties": [],
        "duty_evidence": [duty],
        "geography": {
            "status": "us",
            "evidence": [span("Location: McLean, Virginia, United States")],
        },
        "boilerplate_review": {
            "full_text_review_complete": True,
            "removed_spans": [],
            "retained_role_spans": [duty],
        },
    }
    batch = {
        "batch_id": "first",
        "reviewer": {"kind": "agent", "name": "Test agent", "method": "full preserved text"},
        "human_gold": False,
        "inference_authorized": False,
        "decisions": [decision],
        "identities": [
            {
                "observation_ids": ["a"],
                "status": "confirmed_opening",
                "opening_id": "capital_one:R1",
                "reason": "Separate original requisition and URL.",
                "source_evidence": [
                    {
                        "observation_id": "a",
                        "fields": {"original_url": rows[0]["original_url"], "requisition_id": "R1"},
                    }
                ],
            }
        ],
    }
    return directory, batch, path, tmp_path / "reports"


def test_identical_text_different_requisition_unreviewed_is_not_merged(journal):
    directory, batch, path, parent = journal
    append_batch(directory, batch)
    assert append_batch(directory, batch)["sequence"] == 1
    result = rebuild(directory, parent)
    assert result["reviewed_observations"] == 1
    assert result["unreviewed_observations"] == 1
    assert result["status"] == "partial_agent_adjudication"
    year = next(r for r in result["annual_coverage"] if r["year"] == "2026")
    assert year["confirmed_distinct_openings"] == 1
    assert year["gap"] == 29
    assert rebuild(directory, parent)["run_dir"] == result["run_dir"]


def test_lossless_packet_attestation_is_bound_and_each_identity_stays_separate(journal):
    from degent.adjudication_packets import freeze, groups

    directory, batch, _, parent = journal
    grouped, _ = groups(directory)
    packet = freeze(directory, next(iter(grouped)))
    row = next(r for r in read_jsonl(packet / "observations.jsonl") if r["observation_id"] == "a")
    basis = dict(
        packet_directory=str(packet),
        common_sha256=digest((packet / "common-text.txt").read_bytes()),
        packet_manifest_sha256=digest((packet / "manifest.json").read_bytes()),
        exact_variations=row["variations"],
        common_text_read=True,
        all_exact_variations_read=True,
        identity_assessed_separately=True,
    )
    batch["decisions"][0]["reading_basis"] = basis
    false = copy.deepcopy(batch)
    false["decisions"][0]["reading_basis"]["all_exact_variations_read"] = False
    with pytest.raises(ValueError, match="reading evidence"):
        append_batch(directory, false)
    append_batch(directory, batch)
    assert rebuild(directory, parent)["unreviewed_observations"] == 1
    (packet / "common-text.txt").write_text("Changed wording")
    with pytest.raises(ValueError, match="common wording changed"):
        rebuild(directory, parent)


@pytest.mark.parametrize(
    "change", ["source_hash", "evidence", "human_gold", "identity_merge", "metadata"]
)
def test_rejects_false_provenance_or_source_evidence(journal, change):
    directory, batch, _, _ = journal
    if change == "source_hash":
        batch["decisions"][0]["source_sha256"] = "false"
    elif change == "evidence":
        batch["decisions"][0]["duty_evidence"][0]["text"] = "invented"
    elif change == "human_gold":
        batch["human_gold"] = True
    elif change == "identity_merge":
        identity = batch["identities"][0]
        identity["observation_ids"].append("b")
        identity["source_evidence"].append(
            {
                "observation_id": "b",
                "fields": {
                    "original_url": "https://www.capitalonecareers.com/job/x/y/1732/2",
                    "requisition_id": "R2",
                },
            }
        )
    else:
        batch["identities"][0]["source_evidence"][0]["fields"]["requisition_id"] = "R9"
    with pytest.raises(ValueError):
        append_batch(directory, batch)


def test_exact_orphan_batch_can_resume_but_changed_retry_is_blocked(journal):
    directory, batch, _, _ = journal
    append_batch(directory, batch)
    (directory / "head.json").unlink()
    assert not history(directory)
    changed = copy.deepcopy(batch)
    changed["decisions"][0]["reason"] = "different"
    with pytest.raises(ValueError, match="Interrupted"):
        append_batch(directory, changed)
    assert append_batch(directory, batch)["sequence"] == 1
    assert len(history(directory)) == 1


def test_source_and_journal_corruption_block_reports(journal):
    directory, batch, source, parent = journal
    append_batch(directory, batch)
    source.write_text("changed")
    with pytest.raises(ValueError, match="source text changed"):
        rebuild(directory, parent)


def test_reviewed_removal_keeps_mixed_paragraph_role_duties_and_offsets():
    text = "Today, we are a great company. You will validate credit models.\n"
    intro = {"start": 0, "end": 29, "text": text[:29], "reason": "company marketing"}
    start = text.index("You will")
    duty = {"start": start, "end": len(text) - 1, "text": text[start:-1]}
    view = reviewed_analysis_text(text, [intro], [duty])
    assert "validate credit models" in view["text"]
    assert view["full_text_omission_review_complete"]
    for s in view["retained_spans"]:
        assert (
            text[s["source_start"] : s["source_end"]]
            == view["text"][s["derived_start"] : s["derived_end"]]
        )
    with pytest.raises(ValueError, match="overlaps"):
        reviewed_analysis_text(
            text, [{"start": 0, "end": len(text), "text": text, "reason": "bad removal"}], [duty]
        )
