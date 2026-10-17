"""Replay tranches retain the uncapped queue and stop on archive failures."""

from datetime import datetime, timezone

from degent import archive_continue
from degent.store import write_json, write_jsonl


def test_queue_retains_uncued_candidates_and_backoff_stops_transport(monkeypatch, tmp_path):
    directory = tmp_path / "2020"
    candidates = [
        {
            "timestamp": f"2020030112000{i}",
            "original": f"https://www.capitalonecareers.com/job/x/{title}/1732/{i}",
            "digest": str(i),
        }
        for i, title in enumerate(["data-analyst", "engineer", "risk-analyst", "service"])
    ]
    write_jsonl(directory / "candidates-2020.jsonl", candidates)
    calls = []

    def replay(candidate, directory):
        calls.append(candidate)
        return {
            "accepted": False,
            "reason": "unavailable",
            "receipt": {"status": 503, "body_sha256": "failed"},
        }

    monkeypatch.setattr(archive_continue, "replay", replay)
    result = archive_continue.continue_replays(
        tmp_path, request_limit=10, sleeper=lambda seconds: None
    )
    assert len(calls) == 2
    assert result["status"] == "host_cooldown"
    assert len(archive_continue.queue(tmp_path)) == 4
    assert not result["selection_capped"]
    assert archive_continue.continue_replays(tmp_path)["new_requests"] == 0


def test_backoff_has_no_network_call_until_expiry(monkeypatch, tmp_path):
    write_json(
        tmp_path / "continuation/host-state.json", {"cooldown_until": "2030-01-01T00:00:00+00:00"}
    )
    monkeypatch.setattr(
        archive_continue,
        "replay",
        lambda *args: (_ for _ in ()).throw(AssertionError("network called")),
    )
    result = archive_continue.continue_replays(
        tmp_path, now=datetime(2026, 10, 10, tzinfo=timezone.utc)
    )
    assert result["status"] == "host_cooldown"
    assert result["new_requests"] == 0


def test_malformed_original_url_is_retained_without_blocking_other_replays(monkeypatch, tmp_path):
    malformed = {
        "timestamp": "20240426130952",
        "original": "https://www.capitalonecareers.com/job/-/-/1732/64205565984%0A",
        "digest": "archive_digest",
    }
    valid = {**malformed, "original": malformed["original"].removesuffix("%0A")}
    write_jsonl(tmp_path / "2024/candidates-2024.jsonl", [malformed, valid])
    calls = []

    def replay(candidate, directory):
        calls.append(candidate)
        return {"accepted": False, "receipt": {"status": 200, "body_sha256": "shell"}}

    monkeypatch.setattr(archive_continue, "replay", replay)
    result = archive_continue.continue_replays(tmp_path, sleeper=lambda seconds: None)
    assert calls == [valid]
    assert result["source_url_review_candidates"] == 1
    jobs = archive_continue.queue(tmp_path)
    deferred = next(j for j in jobs if j["state"] == "requires_source_url_review")
    assert deferred["candidate"] == malformed
    assert not deferred["source_url_rewritten"]
