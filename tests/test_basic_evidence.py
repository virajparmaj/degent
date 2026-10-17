"""Frozen assistance scope and cumulative provider reservations without real calls."""

import json
from pathlib import Path

import pytest
from test_adjudication import journal

from degent.basic_evidence_execution import authorize, run, usage, validate_references
from degent.basic_review import prepare
from degent.store import digest, read_json, write_json


@pytest.fixture
def proposal(journal, tmp_path):
    directory, _, _, _ = journal
    rates = tmp_path / "rates"
    rates.mkdir()
    body = json.dumps(
        {
            "data": [
                {
                    "id": "openai/gpt-oss-20b",
                    "pricing": {"prompt": "0.000000018", "completion": "0.00000009"},
                }
            ]
        }
    ).encode()
    (rates / "public-models-response.bin").write_bytes(body)
    write_json(
        rates / "receipt.json",
        {"status": 200, "body_sha256": digest(body), "retrieved_at": "2026-10-10T04:00:00+00:00"},
    )
    path = Path(prepare(directory, rates, tmp_path / "proposals")["run_dir"])
    return path, tmp_path / "execution"


def approval(path):
    manifest = read_json(path / "manifest.json")
    return {
        "manifest_sha256": digest((path / "manifest.json").read_bytes()),
        "caps": manifest["caps"],
        "model_identity": manifest["model_identity"],
        "approved": True,
        "reviewer_kind": "human",
        "reviewer": "Synthetic test user",
        "approval_source": "Synthetic test approval; not study authorization.",
    }


def response(payload, key):
    output = {
        "references": [
            {"category": "duty", "first_line": 5, "last_line": 5, "note": "source duty"}
        ],
        "uncertainties": [],
    }
    return {
        "model": payload["model"],
        "id": "synthetic",
        "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.00001},
    }


def test_proposal_deduplicates_text_without_merging_requisitions(proposal):
    path, _ = proposal
    manifest = read_json(path / "manifest.json")
    assert len(manifest["documents"]) == 1
    assert manifest["documents"][0]["observation_ids"] == ["a", "b"]
    assert manifest["caps"]["descriptions"] == 1
    assert not manifest["provider_dispatch_enabled"]


def test_exact_human_allowance_required_then_provider_usage_durable(proposal):
    path, execution = proposal
    bad = approval(path)
    bad["caps"] = {**bad["caps"], "cost_usd": 99}
    with pytest.raises(ValueError, match="Exact numerical"):
        authorize(path, execution, bad)
    authorize(path, execution, approval(path))
    result = run(execution, transport=response)
    assert result["usage"]["calls"] == 1
    assert result["usage"]["valid_outputs"] == 1
    assert result["usage"]["cost_usd"] == 0.00001
    assert (
        run(execution, transport=lambda *args: pytest.fail("duplicate call"))["usage"]["calls"] == 1
    )


def test_failed_transport_keeps_reserved_cost_and_never_auto_retries(proposal):
    path, execution = proposal
    authorize(path, execution, approval(path))

    def failure(*args):
        raise RuntimeError("synthetic outage")

    result = run(execution, transport=failure)
    assert result["status"] == "transport_failed_no_automatic_retry"
    assert result["usage"]["cost_usd"] > 0
    assert result["usage"]["input_tokens"] > 0
    assert (
        run(execution, transport=lambda *args: pytest.fail("automatic retry"))["usage"]["calls"]
        == 1
    )


def test_bad_model_or_reservation_usage_is_terminal(proposal):
    path, execution = proposal
    authorize(path, execution, approval(path))

    def wrong(payload, key):
        result = response(payload, key)
        result["model"] = "not-approved"
        return result

    assert run(execution, transport=wrong)["status"] == "blocked_terminal_usage_fault"
    assert (
        run(execution, transport=lambda *args: pytest.fail("terminal restart"))["status"]
        == "blocked_terminal_usage_fault"
    )


def test_fulltext_exact_unicode_line_offsets_and_invalid_range():
    text = "😀 Intro\nBuild models.\n"
    output = {
        "references": [{"category": "duty", "first_line": 2, "last_line": 2, "note": "source"}],
        "uncertainties": [],
    }
    spans, errors = validate_references(output, text)
    assert (
        not errors
        and text[spans[0]["start"] : spans[0]["end"]] == spans[0]["text"] == "Build models.\n"
    )
    output["references"][0]["last_line"] = 3
    assert validate_references(output, text)[1] == ["unsupported_source_reference"]
