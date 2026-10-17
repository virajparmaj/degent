"""Source-bound agent decisions, separate from human gold and frozen phase decisions.

Every observation is retained. An unreviewed screen can never become an inclusion
or a confirmed opening through this module. Journal batches and report revisions
are immutable; a complete orphan batch after interruption can be retried exactly.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .adjudication_identity import asserted_requisition
from .analysis_text import reviewed_analysis_text
from .store import digest, operation_lock, read_json, read_jsonl, utc_now, write_json, write_jsonl

VERSION = "capital-one-agent-adjudication-v2"


def signature(value: dict) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def initialize(directory: Path, inventory: Path) -> dict:
    manifest = read_json(inventory / "manifest.json")
    originals = [
        row for path in manifest["input_collection_hashes"] for row in read_jsonl(Path(path))
    ]
    for path, sha in manifest["input_collection_hashes"].items():
        if digest(Path(path).read_bytes()) != sha:
            raise ValueError("Original observation manifest changed.")
    binding = {
        "version": VERSION,
        "inventory": str(inventory.resolve()),
        "inventory_sha256": digest((inventory / "observations.jsonl").read_bytes()),
        "policy_sha256": digest((inventory / "policy.json").read_bytes()),
        "source_metadata_sha256": signature({r["observation_id"]: r for r in originals}),
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "reviewer_kind": "agent",
        "human_gold": False,
        "inference_authorized": False,
    }
    with operation_lock(directory):
        if (directory / "manifest.json").exists():
            if read_json(directory / "manifest.json") != binding:
                raise ValueError("Journal binding changed; use a separate journal revision.")
        else:
            write_jsonl(directory / "source-metadata.jsonl", originals)
            write_json(directory / "manifest.json", binding)
    return binding


def sources(directory: Path) -> tuple[dict, dict, dict]:
    manifest = read_json(directory / "manifest.json")
    inventory = Path(manifest["inventory"])
    if digest((inventory / "observations.jsonl").read_bytes()) != manifest["inventory_sha256"]:
        raise ValueError("Bound inventory changed.")
    if digest((inventory / "policy.json").read_bytes()) != manifest["policy_sha256"]:
        raise ValueError("Bound policy changed.")
    metadata = {r["observation_id"]: r for r in read_jsonl(directory / "source-metadata.jsonl")}
    if signature(metadata) != manifest["source_metadata_sha256"]:
        raise ValueError("Bound metadata changed.")
    records = {r["observation_id"]: r for r in read_jsonl(inventory / "observations.jsonl")}
    texts = {}
    for row in records.values():
        text = Path(row["source_file"]).read_text(encoding="utf-8")
        if digest(text) != row["source_sha256"]:
            raise ValueError("Bound source text changed.")
        texts[row["source_sha256"]] = text
    return records, metadata, texts


def _exact(span: dict, text: str) -> None:
    start, end = span.get("start"), span.get("end")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(text)
        or span.get("text", span.get("quote")) != text[start:end]
    ):
        raise ValueError("Decision evidence must match original source offsets exactly.")


def _evidence(value, text: str) -> None:
    if isinstance(value, dict):
        if "start" in value or "end" in value:
            _exact(value, text)
        for item in value.values():
            _evidence(item, text)
    elif isinstance(value, list):
        for item in value:
            _evidence(item, text)


def validate_batch(batch: dict, records: dict, metadata: dict, texts: dict) -> None:
    reviewer = batch.get("reviewer", {})
    if reviewer.get("kind") != "agent" or not reviewer.get("name") or not reviewer.get("method"):
        raise ValueError("Honest named agent reviewer provenance is required.")
    if batch.get("human_gold") is not False or batch.get("inference_authorized") is not False:
        raise ValueError("Agent decisions cannot create human gold or inference permission.")
    seen, reading_packets = set(), {}
    for decision in batch.get("decisions", []):
        oid = decision["observation_id"]
        if oid not in records or oid in seen:
            raise ValueError("Duplicate or unknown observation decision.")
        seen.add(oid)
        row, original = records[oid], metadata[oid]
        text = texts[row["source_sha256"]]
        if decision["source_sha256"] != row["source_sha256"]:
            raise ValueError("Decision source hash changed.")
        if decision.get("disposition") not in {"include", "exclude", "context"}:
            raise ValueError("Every reviewed observation needs an explicit disposition.")
        if decision.get("full_text_review") != {
            "source_sha256": row["source_sha256"],
            "characters_read": len(text),
        }:
            raise ValueError("Full preserved text review attestation is required.")
        basis = decision.get("reading_basis")
        if basis is not None:
            # A lossless reading packet is source evidence, never an eligibility label.
            from .adjudication_packets import validate as validate_packet

            packet = Path(basis["packet_directory"])
            if str(packet) not in reading_packets:
                validate_packet(packet, packet.parent.parent)
                reading_packets[str(packet)] = {
                    r["observation_id"]: r for r in read_jsonl(packet / "observations.jsonl")
                }
            packet_row = reading_packets[str(packet)].get(oid)
            if (
                packet_row is None
                or packet_row["source_sha256"] != row["source_sha256"]
                or packet_row["variations"] != basis.get("exact_variations")
                or digest((packet / "common-text.txt").read_bytes()) != basis.get("common_sha256")
                or digest((packet / "manifest.json").read_bytes())
                != basis.get("packet_manifest_sha256")
                or any(
                    basis.get(key) is not True
                    for key in (
                        "common_text_read",
                        "all_exact_variations_read",
                        "identity_assessed_separately",
                    )
                )
            ):
                raise ValueError(
                    "Lossless reading evidence or explicit review attestation changed."
                )
        for key in ("reason", "career_assessment", "scope_assessment", "uncertainties"):
            if key not in decision:
                raise ValueError("Missing full-text assessment: " + key)
        _evidence(decision, text)
        geo = decision.get("geography", {})
        for assertion in geo.get("source_assertions", []):
            if assertion not in original.get("location_assertions", []):
                raise ValueError("Geography assertion is not in preserved source metadata.")
        if not geo.get("evidence") and not geo.get("source_assertions"):
            raise ValueError("Exact geography evidence or explicit unresolved reason is required.")
        if decision["disposition"] == "include":
            if geo.get("status") != "us" or not decision.get("duty_evidence"):
                raise ValueError("Inclusion requires direct duty and US evidence.")
            us_quote = any(
                re.search(r"United States(?: of America)?|\bUSA\b", s["text"], re.I)
                for s in geo.get("evidence", [])
            )
            us_assertion = any(
                str(s.get("value", {}).get("addressCountry", "")).casefold()
                in {"us", "usa", "united states", "united states of america"}
                for s in geo.get("source_assertions", [])
            )
            if not (us_quote or us_assertion):
                raise ValueError("US geography is unsupported by the cited source.")
            if not decision.get("functions") or not decision["career_assessment"].get(
                "required_evidence"
            ):
                raise ValueError(
                    "Inclusion requires assessed functions and mandatory qualification evidence."
                )
            if decision["career_assessment"].get("eligible") is not True:
                raise ValueError("Inclusion requires assessed career eligibility.")
        omission = decision.get("boilerplate_review", {})
        if omission.get("full_text_review_complete") is not True:
            raise ValueError("Reviewed decisions need a full-text omission review.")
        reviewed_analysis_text(text, omission["removed_spans"], omission["retained_role_spans"])
    for identity in batch.get("identities", []):
        ids = identity["observation_ids"]
        if not ids or len(set(ids)) != len(ids) or not identity.get("reason"):
            raise ValueError("Identity needs explicit observations and reasoning.")
        if identity["status"] not in {"confirmed_opening", "unresolved_repost_or_version"}:
            raise ValueError("Unknown identity disposition.")
        if len(identity.get("source_evidence", [])) != len(ids):
            raise ValueError("Identity evidence is required separately for every observation.")
        for oid, assertion in zip(ids, identity["source_evidence"]):
            if oid not in metadata or assertion["observation_id"] != oid:
                raise ValueError("Identity observation is outside the bound inventory.")
            for key, value in assertion["fields"].items():
                if metadata[oid].get(key) != value:
                    raise ValueError("Identity assertion differs from preserved metadata.")
            if not assertion["fields"].get("original_url"):
                raise ValueError("Identity requires an attributable original URL.")
        reqs = {
            asserted_requisition(metadata[oid], assertion)
            for oid, assertion in zip(ids, identity["source_evidence"])
        }
        for oid, assertion in zip(ids, identity["source_evidence"]):
            if "employer_application_evidence" in assertion:
                from .adjudication_employer import validate_application_evidence

                validate_application_evidence(
                    metadata[oid],
                    assertion["employer_application_evidence"],
                    asserted_requisition(metadata[oid], assertion),
                )
        if identity["status"] == "confirmed_opening":
            if None in reqs or len(reqs) != 1:
                raise ValueError("Distinct requisitions cannot be silently merged.")
            if identity.get("opening_id") != "capital_one:" + next(iter(reqs)):
                raise ValueError("Confirmed source opening needs its explicit requisition ID.")


def history(directory: Path) -> list[dict]:
    manifest_sha = signature(read_json(directory / "manifest.json"))
    head = read_json(directory / "head.json") if (directory / "head.json").exists() else None
    batches, previous = [], manifest_sha
    for index in range(1, (head["sequence"] if head else 0) + 1):
        path = directory / "batches" / f"{index:06}.json"
        batch = read_json(path)
        if batch["sequence"] != index or batch["previous_sha256"] != previous:
            raise ValueError("Decision journal chain changed.")
        previous = digest(path.read_bytes())
        batches.append(batch)
    if head and previous != head["batch_sha256"]:
        raise ValueError("Decision journal head changed.")
    return batches


def append_batch(directory: Path, batch: dict) -> dict:
    with operation_lock(directory):
        records, metadata, texts = sources(directory)
        validate_batch(batch, records, metadata, texts)
        batches = history(directory)
        for prior in batches:
            if prior["batch_id"] == batch["batch_id"]:
                if prior["payload"] != batch:
                    raise ValueError("Existing batch ID has different content.")
                return prior
        reviewed = {d["observation_id"] for b in batches for d in b["payload"]["decisions"]}
        if reviewed & {d["observation_id"] for d in batch["decisions"]}:
            raise ValueError("Observation already reviewed; freeze a separate amendment revision.")
        prior_identity = {
            oid
            for b in batches
            for i in b["payload"].get("identities", [])
            for oid in i["observation_ids"]
        }
        incoming = [oid for i in batch.get("identities", []) for oid in i["observation_ids"]]
        if prior_identity & set(incoming) or len(incoming) != len(set(incoming)):
            raise ValueError("Observation has overlapping identity decisions.")
        views = {}
        for decision in [
            *(d for b in batches for d in b["payload"]["decisions"]),
            *batch["decisions"],
        ]:
            sha = decision["source_sha256"]
            omission = decision["boilerplate_review"]
            view = reviewed_analysis_text(
                texts[sha], omission["removed_spans"], omission["retained_role_spans"]
            )
            if sha in views and views[sha] != view:
                raise ValueError(
                    "Identical text has inconsistent omission reviews; freeze an amendment revision."
                )
            views[sha] = view
        sequence = len(batches) + 1
        previous = (
            digest((directory / "batches" / f"{sequence - 1:06}.json").read_bytes())
            if batches
            else signature(read_json(directory / "manifest.json"))
        )
        envelope = {
            "sequence": sequence,
            "previous_sha256": previous,
            "batch_id": batch["batch_id"],
            "payload": batch,
        }
        path = directory / "batches" / f"{sequence:06}.json"
        if path.exists():
            if read_json(path) != envelope:
                raise ValueError("Interrupted batch differs; preserve it and resolve explicitly.")
        else:
            write_json(path, envelope)
        write_json(
            directory / "head.json",
            {"sequence": sequence, "batch_sha256": digest(path.read_bytes())},
        )
        return envelope


def rebuild(directory: Path, parent: Path) -> dict:
    records, metadata, texts = sources(directory)
    batches = history(directory)
    if len(list((directory / "batches").glob("*.json"))) != len(batches):
        raise ValueError(
            "Uncommitted journal batch exists; retry its exact payload before reporting."
        )
    decisions = {d["observation_id"]: d for b in batches for d in b["payload"]["decisions"]}
    identities = {
        oid: i
        for b in batches
        for i in b["payload"].get("identities", [])
        for oid in i["observation_ids"]
    }
    for b in batches:
        validate_batch(b["payload"], records, metadata, texts)
    manifest = {
        "version": VERSION,
        "journal_manifest_sha256": signature(read_json(directory / "manifest.json")),
        "head": read_json(directory / "head.json") if batches else None,
        "report_implementation_sha256": digest(Path(__file__).read_bytes()),
        "report_method_files": {
            name: digest((Path(__file__).parent / name).read_bytes())
            for name in (
                "adjudication_identity.py",
                "adjudication_employer.py",
                "adjudication_packets.py",
                "analysis_text.py",
            )
        },
    }
    destination = parent / ("capital-one-agent-selection-" + signature(manifest)[:16])
    rows = []
    for oid, source in records.items():
        row = copy.deepcopy(source)
        decision, identity = decisions.get(oid), identities.get(oid)
        row.update(
            agent_decision=decision,
            identity_decision=identity,
            assessed_disposition=decision["disposition"] if decision else "unreviewed",
            confirmed_opening_id=(
                identity["opening_id"]
                if identity and identity["status"] == "confirmed_opening"
                else None
            ),
        )
        rows.append(row)
    years = sorted({r["temporal"]["year"] for r in rows} | {str(y) for y in range(2020, 2027)})
    annual = [_coverage([r for r in rows if r["temporal"]["year"] == y], y) for y in years]
    groups = defaultdict(list)
    for row in rows:
        if row["assessed_disposition"] == "include":
            for function in row["agent_decision"]["functions"]:
                career = row["agent_decision"]["career_assessment"]["group"]
                groups[(row["temporal"]["year"], function, career)].append(row)
    comparable = [
        {"year": y, "function": f, "career_group": c, **_coverage(g, y)}
        for (y, f, c), g in sorted(groups.items())
    ]
    report = {
        "version": VERSION,
        "status": (
            "final_agent_selection"
            if len(decisions) == len(rows)
            and all(
                r["confirmed_opening_id"] for r in rows if r["assessed_disposition"] == "include"
            )
            else "partial_agent_adjudication"
        ),
        "observations": len(rows),
        "reviewed_observations": len(decisions),
        "reviewed_source_texts": len({d["source_sha256"] for d in decisions.values()}),
        "lossless_packet_reviewed_observations": sum(
            "reading_basis" in decision for decision in decisions.values()
        ),
        "distinct_preserved_texts": len(texts),
        "source_document_versions": len({r["document_id"] for r in rows}),
        "confirmed_distinct_eligible_openings": len(
            {
                r["confirmed_opening_id"]
                for r in rows
                if r["assessed_disposition"] == "include" and r["confirmed_opening_id"]
            }
        ),
        "unreviewed_observations": len(rows) - len(decisions),
        "dispositions": dict(Counter(r["assessed_disposition"] for r in rows)),
        "annual_coverage": annual,
        "agent_decisions_are_human_gold": False,
        "comparative_conclusions_supported": False,
        "collection_volume_is_hiring_prevalence": False,
        "model_calls": 0,
        "model_calls_metric": "external_provider_dispatch_only",
        "direct_chat_agent_reviews": True,
        "paid_provider_calls": 0,
        "inference_authorized": False,
        "observations_discarded": 0,
        "frozen_phase_packets_changed": False,
    }
    if destination.exists():
        for name, sha in read_json(destination / "receipt.json")["files_sha256"].items():
            if digest((destination / name).read_bytes()) != sha:
                raise ValueError("Immutable selection report changed.")
        return {"run_dir": str(destination.resolve()), **read_json(destination / "report.json")}
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".agent-selection-", dir=parent) as tmp:
        staging = Path(tmp)
        write_json(staging / "manifest.json", manifest)
        write_jsonl(staging / "observations.jsonl", rows)
        write_jsonl(
            staging / "review-queue.jsonl",
            [r for r in rows if r["assessed_disposition"] == "unreviewed"],
        )
        write_json(staging / "report.json", report)
        write_json(staging / "coverage.json", annual)
        write_json(
            staging / "screen-audit.json",
            {
                "agent_review_is_human_gold": False,
                "counts": [
                    {"automated_proposal": a, "agent_disposition": d, "observations": n}
                    for (a, d), n in sorted(
                        Counter(
                            (r["disposition"], r["assessed_disposition"])
                            for r in rows
                            if r["agent_decision"]
                        ).items()
                    )
                ],
                "all_proposals_audited": len(decisions) == len(rows),
            },
        )
        write_jsonl(
            staging / "remaining-factual-uncertainties.jsonl",
            [
                {
                    "observation_id": r["observation_id"],
                    "source_sha256": r["source_sha256"],
                    "disposition": r["assessed_disposition"],
                    "uncertainties": r["agent_decision"]["uncertainties"],
                    "context_reason": (
                        r["agent_decision"]["reason"]
                        if r["assessed_disposition"] == "context"
                        else None
                    ),
                    "description_completeness": r["description_completeness"],
                    "identity_status": (r["identity_decision"] or {}).get("status"),
                }
                for r in rows
                if r["agent_decision"]
                and (
                    r["agent_decision"]["uncertainties"]
                    or r["assessed_disposition"] == "context"
                    or r["description_completeness"] != "complete"
                )
            ],
        )
        write_json(
            staging / "boilerplate-review.json",
            {
                "reviewed_source_texts": report["reviewed_source_texts"],
                "remaining_source_texts": len(texts) - report["reviewed_source_texts"],
                "automatic_views_are_full_text_reviews": False,
                "reviewed_removal_spans": sum(
                    len(d["boilerplate_review"]["removed_spans"])
                    for sha, d in {d["source_sha256"]: d for d in decisions.values()}.items()
                ),
                "originals_retained": True,
                "mixed_role_paragraphs_preserved_except_explicit_reviewed_spans": True,
            },
        )
        write_json(
            staging / "comparable-groups.json",
            {
                "groups": comparable,
                "functions_overlap": True,
                "pooled_conclusions": False,
                "note": "Counts retain thin and undated groups; no hiring-prevalence or change estimate from these samples.",
            },
        )
        write_json(
            staging / "within-year.json",
            {
                "wording": _months(rows, "month"),
                "publication": _months(rows, "publication_month"),
                "publication_does_not_date_wording": True,
            },
        )
        for d in decisions.values():
            view = reviewed_analysis_text(
                texts[d["source_sha256"]],
                d["boilerplate_review"]["removed_spans"],
                d["boilerplate_review"]["retained_role_spans"],
            )
            path = staging / "analysis-text" / (d["source_sha256"] + ".json")
            if path.exists() and read_json(path) != view:
                raise ValueError("Identical text has inconsistent omission reviews.")
            write_json(path, view)
        (staging / "report.md").write_text(
            f"# Agent selection revision\n\nStatus: {report['status']}. {len(decisions)} of {len(rows)} observations received full-text agent decisions.\n\n"
            f"This revision confirms {report['confirmed_distinct_eligible_openings']} distinct eligible source openings across the assessed evidence. "
            f"Dispositions: {json.dumps(report['dispositions'], sort_keys=True)}.\n\n"
            "| Wording year | Preserved observations | Reviewed | Included observations | Confirmed eligible openings | Gap to 30 |\n"
            "| --- | ---: | ---: | ---: | ---: | ---: |\n"
            + "\n".join(
                f"| {r['year']} | {r['observations']} | {r['assessed_observations']} | {r['included_observations']} | {r['confirmed_distinct_openings']} | {r['gap'] if r['gap'] is not None else '—'} |"
                for r in annual
            )
            + "\n\n"
            "All sources, earlier proposals, versions and undated records survive. Agent decisions are not human gold. Distinct requisitions are retained; unresolved identities are outside confirmed-opening counts.\n\n"
            "Source document versions, distinct text hashes, and opening/text versions are counted separately. A confirmed identity is not a certification of original description completeness.\n\n"
            "No comparative conclusion or hiring prevalence is supported by collection volume. Publication and wording dates remain separate. Frozen evaluation packets and spending gates remain unchanged.\n",
            encoding="utf-8",
        )
        write_json(
            staging / "receipt.json",
            {
                "files_sha256": {
                    str(p.relative_to(staging)): digest(p.read_bytes())
                    for p in staging.rglob("*")
                    if p.is_file()
                },
                "model_calls": 0,
                "paid_provider_calls": 0,
            },
        )
        staging.rename(destination)
    return {"run_dir": str(destination.resolve()), **report}


def _coverage(rows: list[dict], year: str) -> dict:
    included = [r for r in rows if r["assessed_disposition"] == "include"]
    openings = {r["confirmed_opening_id"] for r in included if r["confirmed_opening_id"]}
    return {
        "year": year,
        "observations": len(rows),
        "description_versions": len({r["document_id"] for r in rows}),
        "distinct_wording_texts": len({r["source_sha256"] for r in rows}),
        "eligible_opening_wording_versions": len(
            {
                (r["confirmed_opening_id"], r["source_sha256"])
                for r in included
                if r["confirmed_opening_id"]
            }
        ),
        "assessed_observations": sum(r["assessed_disposition"] != "unreviewed" for r in rows),
        "included_observations": len(included),
        "confirmed_distinct_openings": len(openings),
        "included_identity_unresolved": sum(not r["confirmed_opening_id"] for r in included),
        "target": 30,
        "gap": max(0, 30 - len(openings)) if "2020" <= year <= "2026" else None,
        "target_complete": len(openings) >= 30,
        "percentages_reported": False,
    }


def _months(rows: list[dict], field: str) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[r["temporal"][field] or "undated"].append(r)
    return [
        {"month": month, **_coverage(group, month[:4])} for month, group in sorted(groups.items())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "append", "report"])
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--output-parent", type=Path, default=Path("runs"))
    args = parser.parse_args()
    if args.action == "initialize":
        result = initialize(args.journal, args.inventory)
    elif args.action == "append":
        result = append_batch(args.journal, read_json(args.batch))
        result = {"sequence": result["sequence"], "batch_id": result["batch_id"]}
    else:
        result = rebuild(args.journal, args.output_parent)
        result = {
            k: result[k]
            for k in ("run_dir", "reviewed_observations", "unreviewed_observations", "dispositions")
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
