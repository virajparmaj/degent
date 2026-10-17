"""Offline review isolation, independent gold, draft recovery and real loopback UI."""

from __future__ import annotations

import copy
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_phases import Runner, envelope, human_ready, label, pair

from degent.dashboard import make_server
from degent.phase_inference import run_phase
from degent.phase_review import package, review
from degent.phase_state import decisions, file_hash, latest, reviewed_documents, usage
from degent.phase_workspace import (
    document,
    history_hash,
    import_drafts,
    load_draft,
    recover_draft,
    run_inventory,
    save_draft,
    state,
    submit,
    validate_export,
)
from degent.store import operation_lock, read_json, utc_now, write_json


def gold_payload(directory, identity="d0"):
    value = load_draft(directory, "labels", identity)["payload"]
    value.update(approved=True, reviewer="Independent fixture reviewer", reviewed_at=utc_now())
    value["documents"][0].update(
        labels=[label(package(directory)[identity])],
        independent_full_text=True,
        omission_audited=True,
        conventions_reviewed=True,
    )
    return value


def hashes(directory):
    return {
        str(p.relative_to(directory)): file_hash(p)
        for p in directory.rglob("*")
        if p.is_file() and p.name != ".operation.lock"
    }


def test_phase_queues_are_separate_and_independent_evidence_has_no_outputs(pair):
    cal = state(pair["calibration"])
    held = state(pair["evaluation"])
    assert len(cal["queues"]["pilot"]) == 12 and len(cal["queues"]["pre_exposure_gold"]) == 13
    assert (
        len(cal["queues"]["remaining_calibration"]) == 16 and len(cal["queues"]["supplements"]) == 9
    )
    assert len(held["queues"]["held_out"]) == 40
    assert (
        not cal["execution_approved"]
        and not cal["spending_authorized"]
        and not cal["dispatch_available"]
    )
    assert "model_output" not in document(pair["calibration"], "d0")
    assert document(pair["calibration"], "d0")["identical_text_documents"] == ["d0", "d3"]
    assert all(r["current"] for r in run_inventory(pair["calibration"].parent))


def test_draft_reload_persists_content_without_decisions_or_permissions(pair):
    directory = pair["calibration"]
    before = hashes(directory)
    draft = load_draft(directory, "labels", "d0")
    value = draft["payload"]
    value["documents"][0]["labels"] = [label(package(directory)["d0"])]
    save_draft(directory, value, "labels", "d0", 0)
    loaded = load_draft(directory, "labels", "d0")
    assert loaded["revision"] == 1 and loaded["payload"] == value
    assert (
        not decisions(directory)
        and not latest(directory, "spending")
        and usage(directory)["calls"] == 0
    )
    assert all(file_hash(directory / name) == sha for name, sha in before.items())


def test_draft_conflicts_and_concurrent_writers_cannot_overwrite(pair):
    directory = pair["calibration"]
    value = load_draft(directory, "scope", "d0")["payload"]
    save_draft(directory, value, "scope", "d0", 0)
    with pytest.raises(ValueError, match="Another window"):
        save_draft(directory, value, "scope", "d0", 0)
    with operation_lock(directory):
        with pytest.raises(RuntimeError, match="already writing"):
            save_draft(directory, value, "scope", "d0", 1)
    assert load_draft(directory, "scope", "d0")["revision"] == 1


def test_changed_source_and_binding_cannot_save_or_validate(pair):
    directory = pair["calibration"]
    value = load_draft(directory, "labels", "d0")["payload"]
    wrong = copy.deepcopy(value)
    wrong["selection_signature"] = "other"
    with pytest.raises(ValueError, match="binding changed"):
        save_draft(directory, wrong, "labels", "d0", 0)
    Path(package(directory)["d0"]["clean_file"]).write_text("changed")
    with pytest.raises(ValueError):
        save_draft(directory, value, "labels", "d0", 0)
    assert not decisions(directory)


def test_validation_is_read_only_and_duplicate_unsupported_labels_fail(pair):
    directory = pair["calibration"]
    value = gold_payload(directory)
    before = hashes(directory)
    assert validate_export(directory, value)["status"] == "valid_review"
    assert hashes(directory) == before
    value["documents"][0]["labels"] *= 2
    with pytest.raises(ValueError, match="Duplicate gold"):
        validate_export(directory, value)
    value = gold_payload(directory)
    value["documents"][0]["labels"][0]["experience_years"] = 123
    value["documents"][0]["labels"][0]["experience_range"] = [123, 123]
    with pytest.raises(ValueError, match="Unsupported gold"):
        validate_export(directory, value)
    assert hashes(directory) == before


def test_explicit_submit_is_source_bound_concurrent_safe_and_idempotent(pair):
    directory = pair["calibration"]
    value = gold_payload(directory)
    expected = history_hash(directory)
    submit(directory, value, expected)
    assert set(reviewed_documents(directory, "labels")) == {"d0"}
    assert submit(directory, value, expected)["status"] == "already_recorded"
    with pytest.raises(ValueError, match="Another reviewer"):
        submit(directory, gold_payload(directory, "d1"), expected)
    assert len(decisions(directory)) == 1 and usage(directory)["calls"] == 0
    assert not latest(directory, "spending")


def test_exclude_and_review_are_preserved_without_approval(pair):
    directory = pair["calibration"]
    value = load_draft(directory, "scope", "d0")["payload"]
    value.update(approved=True, reviewer="Fixture reviewer", reviewed_at=utc_now())
    value["documents"][0].update(
        decision="review", notes="Exact duties need human domain adjudication."
    )
    submit(directory, value, history_hash(directory))
    assert state(directory)["documents"][0]["scope_decision"] == "review"
    assert not latest(directory, "approval") and not latest(directory, "spending")


def test_drafts_cannot_be_approved_and_portal_has_no_spending_or_inference_action(pair):
    directory = pair["calibration"]
    value = gold_payload(directory)
    with pytest.raises(ValueError, match="unapproved"):
        save_draft(directory, value, "labels", "d0", 0)
    for kind in ("spending", "completion", "collect", "phase-run", "reconcile"):
        value = {**load_draft(directory, "taxonomy")["payload"], "kind": kind}
        with pytest.raises(ValueError):
            validate_export(directory, value)
        with pytest.raises(ValueError):
            submit(directory, value, history_hash(directory))
    assert not decisions(directory)


def test_partial_import_preserves_original_and_every_document_as_unapproved(pair):
    directory = pair["calibration"]
    first = gold_payload(directory)
    second = gold_payload(directory, "d1")
    first["documents"] += second["documents"]
    original = copy.deepcopy(first)
    result = import_drafts(directory, first, {"d0": 0, "d1": 0})
    assert result["documents"] == 2 and first == original
    for identity in ("d0", "d1"):
        assert load_draft(directory, "labels", identity)["payload"]["approved"] is False
    stored = next((directory / "review-imports").glob("*.json"))
    assert read_json(stored) == original and not decisions(directory)


def test_import_changed_or_duplicate_sources_is_atomic_and_never_rebound(pair):
    directory = pair["calibration"]
    value = gold_payload(directory)
    other = gold_payload(directory, "d1")
    other["documents"][0]["source_sha256"] = "changed"
    value["documents"] += other["documents"]
    before = hashes(directory)
    with pytest.raises(ValueError):
        import_drafts(directory, value, {"d0": 0, "d1": 0})
    assert hashes(directory) == before
    value = gold_payload(directory)
    value["manifest_sha256"] = "other"
    with pytest.raises(ValueError, match="no silent rebinding"):
        import_drafts(directory, value, {"d0": 0})
    value = gold_payload(directory)
    value["documents"] *= 2
    with pytest.raises(ValueError, match="Duplicate import"):
        import_drafts(directory, value, {"d0": 0})
    assert hashes(directory) == before


def test_interrupted_draft_head_recovers_only_complete_history(pair, monkeypatch):
    directory = pair["calibration"]
    value = load_draft(directory, "scope", "d0")["payload"]
    with monkeypatch.context() as context:

        def interrupted(*args):
            raise OSError("fixture interruption before head commit")

        context.setattr("degent.phase_workspace._durable_head", interrupted)
        with pytest.raises(OSError):
            save_draft(directory, value, "scope", "d0", 0)
    with pytest.raises(ValueError, match="Interrupted draft"):
        load_draft(directory, "scope", "d0")
    assert recover_draft(directory, "scope", "d0")["revision"] == 1
    assert not decisions(directory)
    snapshot = next((directory / "review-drafts").glob("*/history/*.json"))
    snapshot.unlink()
    with pytest.raises(ValueError, match="Missing committed"):
        recover_draft(directory, "scope", "d0")


def test_prediction_audit_is_separate_and_identical_gold_is_locked(pair):
    directory = pair["calibration"]
    with pytest.raises(ValueError, match="gold"):
        document(directory, "d0", "audit")
    human_ready(directory)
    run_phase(directory, Runner())
    assert "model_output" not in document(directory, "d0")
    assert document(directory, "d0", "audit")["model_output"]["raw_sha256"]
    assert load_draft(directory, "audit", "d0")["payload"]["documents"][0]["raw_sha256"]
    for identity in ("d0", "d3"):
        draft = load_draft(directory, "labels", identity)
        assert draft["read_only"]
        with pytest.raises(ValueError, match="locked"):
            save_draft(directory, draft["payload"], "labels", identity, 0)


@pytest.fixture
def local_server(pair):
    config = pair["calibration"].parent.parent / "dashboard-fixture.json"
    write_json(
        config,
        {
            "run_dir": str(pair["calibration"].parent),
            "data_dir": str(config.parent / "data"),
            "library_dir": str(config.parent / "companies"),
            "snapshot_dir": str(config.parent / "snapshots"),
        },
    )
    server = make_server(root=config.parent, config_path=config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(3)


def http(server, path, payload=None, authorized=True):
    origin = f"http://127.0.0.1:{server.server_port}"
    headers = {}
    if payload is not None and authorized:
        headers = {
            "Origin": origin,
            "X-Degent-Token": server.app.token,
            "Content-Type": "application/json",
        }
    request = urllib.request.Request(
        origin + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def test_http_surface_keeps_local_session_guard_and_rejects_legacy_and_spending(pair, local_server):
    run = pair["calibration"].name
    state_response = json.loads(http(local_server, "/api/phase/state?run=" + run))
    assert state_response["token"] == local_server.app.token
    payload = {
        "run_id": run,
        "decision": {**envelope(pair["calibration"], "spending")},
        "history_sha256": state_response["history_sha256"],
    }
    for authorized in (True, False):
        with pytest.raises(urllib.error.HTTPError):
            http(local_server, "/api/phase/submit", payload, authorized)
    with pytest.raises(urllib.error.HTTPError):
        http(local_server, "/api/jobs", {"run_id": run, "operation": "extract"})
    assert "current" in http(local_server, "/api/phases").decode()
    assert b"Scoped evidence review" in http(local_server, "/phase.html")
    assert not decisions(pair["calibration"]) and usage(pair["calibration"])["calls"] == 0


def test_browser_review_resumes_draft_and_records_only_explicit_gold(pair, local_server):
    from playwright.sync_api import sync_playwright

    directory = pair["calibration"]
    origin = f"http://127.0.0.1:{local_server.server_port}"
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Local Chromium unavailable")
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        page.goto(origin + "/phase.html?run=" + directory.name)
        page.wait_for_selector('#source:has-text("SQL required.")')
        page.get_by_text("Frozen candidate duty and US geography spans", exact=True).click()
        page.locator('[data-candidate="duty_evidence.0"]').click()
        selected = page.evaluate(
            '() => selectedSpan(document.querySelector("#source"),getSelection())'
        )
        assert selected["text"] == package(directory)["d0"]["duty_evidence"][0]["text"]
        page.locator('[data-attach="duty_evidence"]').click()
        page.locator("#kind").select_option("labels")
        page.wait_for_selector("#add-label")
        page.evaluate(
            """() => {const root=document.querySelector('#source');const start=root.textContent.indexOf('SQL required.');const range=document.createRange();range.setStart(root.firstChild,start);range.setEnd(root.firstChild,start+13);getSelection().removeAllRanges();getSelection().addRange(range);}"""
        )
        page.locator("#add-label").click()
        page.locator('[data-path="documents.0.labels.0.concept"]').fill("sql")
        page.locator('[data-path="documents.0.labels.0.status"]').select_option("required")
        page.locator('[data-path="documents.0.labels.0.statement_scope"]').select_option(
            "qualification"
        )
        for name in ("independent_full_text", "omission_audited", "conventions_reviewed"):
            page.locator(f'[data-path="documents.0.{name}"]').check()
        page.locator("#save").click()
        page.wait_for_function(
            "() => document.querySelector('#draft-state').textContent.includes('saved')"
        )
        assert not decisions(directory)
        page.reload()
        page.wait_for_selector('#source:has-text("SQL required.")')
        page.locator("#kind").select_option("labels")
        page.wait_for_selector('[data-path="documents.0.labels.0.concept"]')
        assert page.locator('[data-path="documents.0.labels.0.concept"]').input_value() == "sql"
        page.locator("#reviewer").fill("Independent browser fixture reviewer")
        page.locator("#approve").check()
        page.locator("#submit").click()
        page.wait_for_function(
            "() => document.querySelector('#status').textContent.includes('decision recorded')"
        )
        assert len(decisions(directory)) == 1 and set(reviewed_documents(directory, "labels")) == {
            "d0"
        }
        assert usage(directory)["calls"] == 0 and not latest(directory, "spending")
        # Real DOM ranges count Python-compatible code points, including repeated text after astral Unicode.
        actual = page.evaluate(
            """() => {const node=document.createElement('pre');node.textContent='😀SQL SQL';document.body.appendChild(node);const range=document.createRange();range.setStart(node.firstChild,6);range.setEnd(node.firstChild,9);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);return selectedSpan(node,selection);}"""
        )
        assert actual == {"start": 5, "end": 8, "text": "SQL"}
        assert not errors and all(url.startswith(origin) for url in requests)
        page.set_viewport_size({"width": 600, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        browser.close()


def test_taxonomy_validation_does_not_mutate_payload_or_create_approval(pair):
    directory = pair["calibration"]
    value = load_draft(directory, "taxonomy")["payload"]
    fixed = package(directory)["d0"]
    quote = fixed["duty_evidence"][0]["text"]
    value.update(
        approved=True,
        reviewer="Fixture definitions reviewer",
        reviewed_at=utc_now(),
        groups={
            "risk": {
                "name": "Risk",
                "definition": "Credit risk analysis from direct duties.",
                "function": "risk",
                "domains": ["credit"],
                "supporting_evidence": [
                    {"document_id": "d0", "source_sha256": fixed["source_sha256"], "quote": quote}
                ],
            }
        },
        assignments={
            "d0": {
                "source_sha256": fixed["source_sha256"],
                "role_group_id": "risk",
                "evidence": quote,
            }
        },
    )
    before = hashes(directory)
    original = copy.deepcopy(value)
    assert validate_export(directory, value)["status"] == "valid_review"
    assert value == original and hashes(directory) == before and not latest(directory, "taxonomy")
    value["assignments"]["d0"]["evidence"] = "Capital One"
    with pytest.raises(ValueError, match="direct duty"):
        validate_export(directory, value)
    assert hashes(directory) == before


def test_manifest_export_is_read_only_and_recording_it_never_authorizes_spending(pair):
    directory = pair["calibration"]
    human_ready(directory, authorize=False)
    # Preserve the fixture's prerequisite decisions and reconstruct the state immediately before approval.
    sorted((directory / "decisions").glob("*.json"))[-1].unlink()
    (directory / "execution-manifest.json").unlink()
    value = load_draft(directory, "approval")["payload"]
    value.update(
        approved=True,
        reviewer="Fixture manifest reviewer",
        reviewed_at=utc_now(),
        definitions_and_conventions_approved=True,
        duplicate_calibration_text_review=True,
        evaluation_assignments_approved=True,
    )
    for identity in value["identity_decisions"]:
        identity.update(
            decision="retain_distinct_requisitions_single_pilot_capture",
            rationale="Distinct fixture requisitions, one preserved pilot capture.",
        )
    before = hashes(directory)
    assert validate_export(directory, value)["status"] == "valid_review"
    assert hashes(directory) == before and not (directory / "execution-manifest.json").exists()
    submit(directory, value, history_hash(directory))
    assert (directory / "execution-manifest.json").exists() and not latest(directory, "spending")
    assert usage(directory)["calls"] == 0


def test_evaluation_definitions_inherit_final_method_without_copying_calibration_assignments(pair):
    from test_phases import complete_calibration

    from degent.phase_workspace import taxonomy_template

    complete_calibration(pair["calibration"])
    value = taxonomy_template(pair["evaluation"])
    frozen = read_json(pair["calibration"] / "taxonomy.json")
    assert value["groups"] == frozen["groups"] and value["skills"] == frozen["skills"]
    assert value["definitions_inherited"] and not value["approved"]
    assert value["assignments"] == {} and not latest(pair["evaluation"], "taxonomy")
    assert usage(pair["evaluation"])["calls"] == 0


def test_revision_draft_reuses_source_gold_but_resets_all_new_review_checks(pair, monkeypatch):
    from degent import phase_workspace as workspace

    directory = pair["calibration"]
    old = gold_payload(directory)["documents"][0]
    reference = directory.parent / "independent-gold-fixture.json"
    write_json(reference, {"d0": old})
    original = workspace.read_json

    def previous_gold(path):
        result = original(path)
        if path == directory / "phase-manifest.json":
            result["revision"] = {"independent_gold_reference": str(reference)}
        return result

    monkeypatch.setattr(workspace, "read_json", previous_gold)
    value = load_draft(directory, "labels", "d0")["payload"]
    assert value["documents"][0]["labels"] == old["labels"]
    assert not any(
        value["documents"][0][k]
        for k in ("independent_full_text", "omission_audited", "conventions_reviewed")
    )
    assert not value["approved"] and not decisions(directory)
