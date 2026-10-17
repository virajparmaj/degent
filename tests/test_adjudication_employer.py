"""Raw employer support cannot erase conflicts or join a different requisition."""

import copy

import pytest

from degent.adjudication_employer import validate_application_evidence
from degent.store import digest


@pytest.fixture
def evidence(tmp_path):
    url = "https://capitalone.wd1.myworkdayjobs.com/Capital_One/job/Chicago/Analyst_R123-2/apply"
    source = '😀<a href="' + url + '">Apply</a>'
    raw = source.encode()
    path = tmp_path / "response.bin"
    path.write_bytes(raw)
    organization = {"@type": "Organization", "name": "People Center"}
    original = dict(
        raw_source_sha256=digest(raw),
        original_posting_record=dict(hiringOrganization=organization),
    )
    start = source.index(url)
    proof = dict(
        raw_source_file=str(path),
        raw_source_sha256=digest(raw),
        decoded_source_sha256=digest(source),
        offset_unit="unicode_codepoints_in_decoded_source",
        hiring_organization_assertion=copy.deepcopy(organization),
        conflicting_employer_assertion_retained=True,
        source_spans=[dict(start=start, end=start + len(url), text=url)],
        resolution="Posting-specific source brand support; original organization retained.",
    )
    return original, proof


def test_unicode_offsets_and_employer_conflict_remain_bound(evidence):
    original, proof = evidence
    validate_application_evidence(original, proof, "R123")
    assert original["original_posting_record"]["hiringOrganization"]["name"] == "People Center"


@pytest.mark.parametrize("change", ["offset", "req", "org", "conflict", "source", "host"])
def test_false_employer_support_is_rejected(evidence, change):
    original, proof = evidence
    req = "R123"
    if change == "offset":
        proof["source_spans"][0]["start"] += 3
    elif change == "req":
        req = "R456"
    elif change == "org":
        proof["hiring_organization_assertion"]["name"] = "Capital One"
    elif change == "conflict":
        proof["conflicting_employer_assertion_retained"] = False
    elif change == "source":
        proof["raw_source_sha256"] = "0" * 64
    else:
        proof["source_spans"][0]["text"] = proof["source_spans"][0]["text"].replace(
            "capitalone.wd1.myworkdayjobs.com", "example.org"
        )
    with pytest.raises(ValueError):
        validate_application_evidence(original, proof, req)
