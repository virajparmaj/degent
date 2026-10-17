"""Resumable free replay tranches with host backoff; no selection-count cap."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .archive_inventory import replay
from .capital_one import official_url
from .store import digest, operation_lock, read_json, read_jsonl, utc_now, write_json

VERSION = "public-archive-continuation-v1"
CUE = re.compile(r"data|risk|fraud|credit|product|quant|model|research|machine|analyst|scien", re.I)


def queue(root: Path) -> list[dict]:
    """Retain every candidate, including ones without useful title/URL cues."""
    jobs = {}
    for path in sorted(root.glob("*/candidates-*.jsonl")):
        for candidate in read_jsonl(path):
            url = f"https://web.archive.org/web/{candidate['timestamp']}id_/{candidate['original']}"
            key = digest(url)
            completed = list((path.parent / "replays").glob(key + "-*.json"))
            if (path.parent / "replays" / (key + ".json")).exists():
                completed.append(path.parent / "replays" / (key + ".json"))
            source_url_issue = None
            try:
                official_url(candidate["original"], job=True)
            except ValueError as exc:
                source_url_issue = str(exc)
            state = "pending"
            if completed:
                state = "response_preserved"
            elif source_url_issue:
                state = "requires_source_url_review"
            # A failed replay remains preserved and deferred; retries need a later explicit tranche.
            jobs[key] = {
                "request_sha256": key,
                "candidate": candidate,
                "collection": str(path.parent.resolve()),
                "state": state,
                "source_url_review_reason": source_url_issue,
                "source_url_rewritten": False,
                "priority_cue": bool(CUE.search(candidate["original"])),
            }
    buckets = defaultdict(list)
    for job in jobs.values():
        buckets[job["candidate"]["timestamp"][:4]].append(job)
    # Interleave years and first captures of different URL identities. These are
    # collection priorities only: every version stays queued and no opening merge occurs.
    for year, bucket in buckets.items():
        counts = defaultdict(int)
        for job in sorted(bucket, key=lambda r: (r["candidate"]["timestamp"], r["request_sha256"])):
            identity = urlsplit(job["candidate"]["original"]).path.rstrip("/").split("/")[-1]
            job["capture_priority"] = counts[identity]
            counts[identity] += 1
        buckets[year] = sorted(
            bucket,
            key=lambda r: (
                r["state"] != "pending",
                not r["priority_cue"],
                r["capture_priority"],
                r["candidate"]["timestamp"],
            ),
        )
    years = sorted(
        buckets, key=lambda y: (sum(j["state"] == "response_preserved" for j in buckets[y]), y)
    )
    return [
        buckets[y][i]
        for i in range(max((len(b) for b in buckets.values()), default=0))
        for y in years
        if i < len(buckets[y])
    ]


def continue_replays(
    root: Path,
    *,
    request_limit: int = 10,
    interval: float = 20,
    cooldown_hours: float = 6,
    now=None,
    sleeper=time.sleep,
) -> dict:
    """Stop after two transport failures; limits bound one tranche, never inclusion."""
    if request_limit < 1 or interval < 10 or cooldown_hours < 1:
        raise ValueError("Positive tranche size and conservative host backoff are required.")
    now = now or datetime.now(timezone.utc)
    control = root / "continuation"
    with operation_lock(control):
        jobs = queue(root)
        write_json(
            control / "queue.json",
            {
                "version": VERSION,
                "jobs": jobs,
                "all_candidates_retained": True,
                "transport_limit_is_selection_cap": False,
            },
        )
        state_path = control / "host-state.json"
        state = read_json(state_path) if state_path.exists() else {}
        until = state.get("cooldown_until")
        if until and datetime.fromisoformat(until) > now:
            return {
                "status": "host_cooldown",
                "cooldown_until": until,
                "new_requests": 0,
                "pending_candidates": sum(j["state"] == "pending" for j in jobs),
            }
        results, failures, network_requests = [], 0, 0
        for job in [j for j in jobs if j["state"] == "pending"][:request_limit]:
            if results:
                sleeper(interval)
            response_dir = Path(job["collection"]) / "responses"
            prior_responses = set(response_dir.glob("*.json"))
            result = replay(job["candidate"], Path(job["collection"]))
            network_requests += len(set(response_dir.glob("*.json")) - prior_responses)
            receipt = result["receipt"]
            transport_failure = receipt["status"] is None or receipt["status"] in {
                429,
                500,
                502,
                503,
                504,
            }
            failures = failures + 1 if transport_failure else 0
            results.append(
                {
                    "request_sha256": job["request_sha256"],
                    "accepted": result["accepted"],
                    "status": receipt["status"],
                    "response_sha256": receipt["body_sha256"],
                    "reason": result.get("reason"),
                    "observation_id": result.get("observation_id"),
                }
            )
            if failures >= 2:
                state = {
                    "cooldown_until": (
                        datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                    ).isoformat(),
                    "reason": "two_consecutive_archive_transport_failures",
                    "responses_preserved": True,
                }
                write_json(state_path, state)
                break
        report = {
            "version": VERSION,
            "started_at": now.isoformat(),
            "finished_at": utc_now(),
            "status": "host_cooldown" if failures >= 2 else "tranche_complete",
            "new_requests": network_requests,
            "replay_operations": len(results),
            "results": results,
            "pending_candidates": sum(j["state"] == "pending" for j in queue(root)),
            "source_url_review_candidates": sum(
                j["state"] == "requires_source_url_review" for j in queue(root)
            ),
            "index_pagination_complete": False,
            "selection_capped": False,
            "model_calls": 0,
            "paid_provider_calls": 0,
        }
        write_json(
            control / "tranches" / (digest(json.dumps(report, sort_keys=True)) + ".json"), report
        )
        write_json(
            control / "queue.json",
            {
                "version": VERSION,
                "jobs": queue(root),
                "all_candidates_retained": True,
                "transport_limit_is_selection_cap": False,
            },
        )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--request-limit", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(continue_replays(args.collection, request_limit=args.request_limit)))


if __name__ == "__main__":
    main()
