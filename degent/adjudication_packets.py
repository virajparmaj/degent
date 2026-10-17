"""Lossless reading packets for agent review of common preserved wording.

Only exact title headers, initial geography headers and strictly shaped salary
blocks vary. No eligibility decisions or opening merges are made here. Reviewers
must read the common text and every exact variation and identity assertion.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .adjudication import history, sources
from .store import digest, read_json, read_jsonl, write_json, write_jsonl

VERSION = "lossless-agent-reading-packet-v1"
SALARY_INTRO = (
    "The minimum and maximum full-time annual salaries for this role are listed below, by "
    "location. Please note that this salary information is solely for candidates hired to "
    "perform work within one of these locations, and refers to the amount Capital One is "
    "willing to pay at the time of this posting. Salaries for part-time roles will be prorated "
    "based upon the agreed upon number of hours to be regularly worked."
)


def split_text(text: str, title: str) -> tuple[str, list[dict]]:
    """Return common text and exact, independently readable neutral variations."""
    if re.search(r"\[(?:header|salary) VARIATION\]", text):
        raise ValueError("Source contains a reserved packet marker.")
    spans, offset = [], 0
    for number, line in enumerate(text.splitlines(keepends=True)):
        is_title = number < 4 and line.strip() == title
        is_geo = number < 2 and (
            line.startswith("Location:") or line.startswith("United States of America,")
        )
        if is_title or is_geo:
            spans.append(dict(start=offset, end=offset + len(line), text=line, kind="header"))
        offset += len(line)
    for match in re.finditer(re.escape(SALARY_INTRO), text):
        end = text.find("Candidates hired to work in other locations", match.end())
        if end < 0:
            continue
        block = text[match.start() : end]
        lines = block.splitlines()
        # A mixed salary paragraph containing requirements stays in the common body.
        if lines[0] != SALARY_INTRO or not all(
            not line.strip() or re.fullmatch(r"[^\n:]+: \$[\d,]+ - \$[\d,]+ for [^\n]+", line)
            for line in lines[1:]
        ):
            continue
        spans.append(dict(start=match.start(), end=end, text=block, kind="salary"))
    cursor, pieces = 0, []
    for span in sorted(spans, key=lambda item: item["start"]):
        if span["start"] < cursor:
            raise ValueError("Overlapping packet variations.")
        pieces.extend([text[cursor : span["start"]], f"[{span['kind']} VARIATION]\n"])
        cursor = span["end"]
    pieces.append(text[cursor:])
    common = "".join(pieces)
    if reconstruct(common, spans) != text:
        raise ValueError("Packet reconstruction changed source wording.")
    return common, sorted(spans, key=lambda item: item["start"])


def reconstruct(common: str, variations: list[dict]) -> str:
    result = common
    for span in variations:
        marker = f"[{span['kind']} VARIATION]\n"
        if marker not in result:
            raise ValueError("Missing packet variation marker.")
        result = result.replace(marker, span["text"], 1)
    if re.search(r"\[(?:header|salary) VARIATION\]", result):
        raise ValueError("Unfilled packet variation marker.")
    for span in variations:
        if result[span["start"] : span["end"]] != span["text"]:
            raise ValueError("Packet variation offsets changed.")
    return result


def groups(journal: Path) -> tuple[dict, dict]:
    records, metadata, texts = sources(journal)
    done = {d["observation_id"] for b in history(journal) for d in b["payload"]["decisions"]}
    rows, common_texts = defaultdict(list), {}
    for oid, record in records.items():
        if oid in done:
            continue
        text = texts[record["source_sha256"]]
        common, variations = split_text(text, record["title"])
        key = digest(common)
        common_texts[key] = common
        rows[key].append(
            dict(
                observation_id=oid,
                source_sha256=record["source_sha256"],
                source_file=record["source_file"],
                title=record["title"],
                requisition_id=metadata[oid].get("requisition_id"),
                original_url=metadata[oid].get("original_url"),
                wording_date=record["temporal"]["wording_observed_at"],
                characters=len(text),
                variations=variations,
                reconstruction_verified=True,
            )
        )
    return dict(rows), common_texts


def freeze(journal: Path, common_sha256: str) -> Path:
    directory = journal / "lossless-review-packets" / common_sha256
    if directory.exists():
        validate(directory, journal)
        return directory
    rows, texts = groups(journal)
    selected = rows[common_sha256]
    manifest = dict(
        version=VERSION,
        method_sha256=digest(Path(__file__).read_bytes()),
        journal_manifest_sha256=digest((journal / "manifest.json").read_bytes()),
        common_sha256=common_sha256,
        observations=len(selected),
        source_texts=len({row["source_sha256"] for row in selected}),
        observations_sha256=digest("".join(json.dumps(r, sort_keys=True) for r in selected)),
        opening_identities_merged=False,
        scope_or_qualification_text_normalized=False,
        review_complete=False,
    )
    directory.mkdir(parents=True)
    (directory / "common-text.txt").write_text(texts[common_sha256], encoding="utf-8")
    write_jsonl(directory / "observations.jsonl", selected)
    write_json(directory / "manifest.json", manifest)
    validate(directory, journal)
    return directory


def validate(directory: Path, journal: Path) -> dict:
    manifest = read_json(directory / "manifest.json")
    if manifest["version"] != VERSION or manifest["method_sha256"] != digest(
        Path(__file__).read_bytes()
    ):
        raise ValueError("Packet method binding changed.")
    if manifest["journal_manifest_sha256"] != digest((journal / "manifest.json").read_bytes()):
        raise ValueError("Packet journal binding changed.")
    common = (directory / "common-text.txt").read_text(encoding="utf-8")
    if digest(common) != manifest["common_sha256"]:
        raise ValueError("Packet common wording changed.")
    rows = read_jsonl(directory / "observations.jsonl")
    if (
        digest("".join(json.dumps(r, sort_keys=True) for r in rows))
        != manifest["observations_sha256"]
    ):
        raise ValueError("Packet observations changed.")
    records, metadata, texts = sources(journal)
    seen = set()
    for row in rows:
        oid = row["observation_id"]
        if oid in seen:
            raise ValueError("Duplicate packet observation.")
        seen.add(oid)
        record = records[oid]
        original = texts[record["source_sha256"]]
        if (
            row["source_sha256"] != record["source_sha256"]
            or reconstruct(common, row["variations"]) != original
        ):
            raise ValueError("Packet does not reconstruct the bound original.")
        expected_common, expected_variations = split_text(original, record["title"])
        if expected_common != common or expected_variations != row["variations"]:
            raise ValueError("Packet neutral variation boundaries changed.")
        if row["characters"] != len(original) or row["title"] != record["title"]:
            raise ValueError("Packet source assertions changed.")
        if (
            row["source_file"] != record["source_file"]
            or row["wording_date"] != record["temporal"]["wording_observed_at"]
        ):
            raise ValueError("Packet source location or wording date changed.")
        for field in ("requisition_id", "original_url"):
            if row[field] != metadata[oid].get(field):
                raise ValueError("Packet identity assertions changed.")
    if len(rows) != manifest["observations"]:
        raise ValueError("Packet observation count changed.")
    if len({row["source_sha256"] for row in rows}) != manifest["source_texts"]:
        raise ValueError("Packet wording-version count changed.")
    return manifest
