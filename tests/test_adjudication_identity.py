"""Identity evidence retains employer conflicts and blocks forged source joins."""

import copy
import gzip
import json

import pytest

from degent.adjudication_identity import asserted_requisition, preserved_identity
from degent.store import digest


@pytest.fixture
def original(tmp_path):
    url = "https://www.capitalonecareers.com/job/mclean/analyst/1732/12345"
    job = {
        "@type": "JobPosting",
        "identifier": "R123",
        "url": url,
        "title": "Analyst",
        "description": "Analyze datasets. 😀",
        "hiringOrganization": {"name": "People Center"},
    }
    script = json.dumps(job, ensure_ascii=False)
    body = '<html>😀<script type="application/ld+json">' + script + "</script></html>"
    raw = gzip.compress(body.encode())
    path = tmp_path / "preserved.html.gz"
    path.write_bytes(raw)
    return {
        "raw_source_file": str(path),
        "raw_source_sha256": digest(raw),
        "requisition_id": None,
        "original_url": url,
        "title": "Analyst",
    }, body


def test_missing_imported_id_resolves_from_raw_source_without_employer_alias(original):
    row, source = original
    proof = preserved_identity(row)
    span = proof["source_span"]
    assert source[span["start"] : span["end"]] == span["text"]
    assert proof["decompression"] == "gzip"
    assert proof["hiring_organization_assertion"] == {"name": "People Center"}
    assert not proof["employer_conflict_resolved"]
    assert asserted_requisition(row, {"raw_identity_evidence": proof}) == "R123"
    assert row["requisition_id"] is None


@pytest.mark.parametrize("change", ["req", "span", "url", "imported", "raw"])
def test_false_requisition_source_or_cross_posting_join_is_blocked(original, change):
    row, _ = original
    proof = preserved_identity(row)
    modified = copy.deepcopy(row)
    if change == "req":
        proof["requisition_id"] = "R456"
    elif change == "span":
        proof["source_span"]["start"] += 1
    elif change == "url":
        modified["original_url"] = modified["original_url"].replace("12345", "45678")
    elif change == "imported":
        modified["requisition_id"] = "R456"
    else:
        from pathlib import Path

        Path(modified["raw_source_file"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        asserted_requisition(modified, {"raw_identity_evidence": proof})
