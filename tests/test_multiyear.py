"""Temporal attribution, career-route and lossless preparation regressions."""

from pathlib import Path

import pytest

from degent.analysis_text import analysis_text, masked_text
from degent.career_evidence import career_evidence
from degent.multiyear import build, coverage, inspect_observation, temporal_evidence
from degent.store import digest, read_json, read_jsonl, write_json, write_jsonl


def description(
    requirements="At least 2 years of experience in data analysis", title="Senior Analyst"
):
    return (
        f"Capital One\n{title}\nLocation: McLean, Virginia, United States\n"
        "What You'll Do:\nAnalyze datasets and build analytical tools.\n"
        f"Basic Qualifications:\nBachelor's degree\n{requirements}\n"
    )


def row(text, observation="o1", **kwargs):
    return {
        "observation_id": observation,
        "document_id": observation,
        "title": "Senior Analyst",
        "clean_file": "unused",
        "clean_sha256": digest(text),
        "vacancy_id": observation,
        "collection_kind": "current",
        "current_capture_at": "2026-10-08T10:00:00Z",
        "geography_status": "us_location",
        **kwargs,
    }


def test_mixed_intro_retains_exact_role_evidence_and_unicode_mapping():
    text = (
        "At Capital One, we’re building a leading technology company. "
        "You will build data pipelines and analyze customers’ needs.\n"
        "Basic Qualifications:\nAt least 3 years of experience\n"
    )
    view = analysis_text(text)
    assert "leading technology company" not in view["text"]
    assert "You will build data pipelines" in view["text"]
    assert "3 years" in view["text"]
    for span in view["retained_spans"]:
        assert (
            text[span["source_start"] : span["source_end"]]
            == view["text"][span["derived_start"] : span["derived_end"]]
        )
    for span in view["removed_spans"]:
        assert text[span["start"] : span["end"]] == span["text"]
    assert len(masked_text(text, view["removed_spans"])) == len(text)


def test_company_copy_cannot_establish_role_relevance_and_sponsorship_survives():
    text = (
        "Capital One is a high-tech company that develops AI systems.\n"
        "Capital One will consider sponsoring a qualified applicant for this position.\n"
    )
    view = analysis_text(text)
    assert "AI systems" not in view["text"]
    assert "sponsoring" in view["text"]


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            description(
                "At least 2 years of experience\nPreferred Qualifications:\nAt least 9 years of experience"
            ),
            "within_year_screen",
        ),
        (
            description(
                "Bachelor's degree and 11 years of experience OR Master's degree and 9 years of experience OR PhD and 6 years of experience"
            ),
            "above_year_screen",
        ),
        (
            description(
                "Bachelor's degree and 6 years of experience OR Master's degree and 4 years of experience"
            ),
            "within_year_screen",
        ),
        (
            description(
                "At least 4 years of application development experience\nAt least 3 years of experience in Python"
            ),
            "within_year_screen",
        ),
        (
            description(
                "At least 3 years of experience\nPreferred Qualifications:\n2 years of people management experience"
            ),
            "within_year_screen",
        ),
        (
            description("At least 3 years of people management experience"),
            "people_leadership_requires_semantic_review",
        ),
        (description("Bachelor's degree"), "experience_unstated"),
    ],
)
def test_required_preferred_alternatives_and_overlapping_experience(text, expected):
    assert career_evidence(text)["status"] == expected


def test_manager_title_not_used_for_career_decision():
    record, _ = inspect_observation(
        row(description(), title="Manager, Data Analysis"), description()
    )
    assert record["disposition"] == "proposed_eligible"
    assert not record["career"]["title_used_for_eligibility"]


def test_degree_field_and_programming_alternatives_do_not_invent_zero_year_route():
    text = description(
        "Master's degree in Science, Engineering, or Mathematics plus 9 years of experience\n"
        "At least 6 years of experience in Python, Scala, or R"
    )
    result = career_evidence(text)
    assert result["status"] == "above_year_screen"
    assert result["required_route_screen_years"] == 9
    assert all(len(routes) == 1 for routes in result["required_statements"])


def test_historical_retrieval_and_cohort_title_do_not_invent_wording_date():
    r = row(
        "text",
        collection_kind="historical",
        verified_historical_wording_at=None,
        capture_at="2026-10-08T10:00:00Z",
        stated_publication_at="2020-01-01",
    )
    assert temporal_evidence(r)["year"] == "undated"
    assert temporal_evidence(r)["publication_year"] == "2020"
    r["verified_historical_wording_at"] = "2025-08-12T10:00:00+00:00"
    assert temporal_evidence(r)["quarter"] == "2025-Q3"


def test_unpadded_publication_date_normalizes_without_rewriting_source_assertion():
    r = row("text", stated_publication_at="2023-3-16")
    temporal = temporal_evidence(r)
    assert temporal["publication_at"] == "2023-03-16"
    assert temporal["original_date_assertions"]["stated_publication_at"] == "2023-3-16"
    r["stated_publication_at"] = "2023-99-16"
    assert temporal_evidence(r)["publication_at"] is None


def test_conflicting_employer_content_survives_outside_primary_proposal():
    text = description()
    record, _ = inspect_observation(
        row(text, employer_status="conflicting_employer_assertion"), text
    )
    assert record["disposition"] == "review_scope"
    assert record["record_retained"]


def test_undated_content_is_retained_and_small_years_never_drop_records():
    text = description()
    r = row(text, collection_kind="historical", verified_historical_wording_at=None)
    record, _ = inspect_observation(r, text)
    assert record["disposition"] == "proposed_eligible"
    assert record["cohort"] == "undated_content"
    r["verified_historical_wording_at"] = "2020-02-01"
    record, _ = inspect_observation(r, text)
    year = next(item for item in coverage([record]) if item["year"] == "2020")
    assert year["observations"] == 1
    assert year["target_gap"] == 29
    assert year["records_removed_for_small_sample"] == 0


def test_source_structured_us_locations_enable_historical_domain_proposal():
    text = description().replace("Location: McLean, Virginia, United States\n", "")
    r = row(
        text,
        collection_kind="historical",
        location_assertions=[
            {"kind": "source_structured_location", "value": {"addressCountry": "United States"}}
        ],
    )
    record, _ = inspect_observation(r, text)
    assert record["disposition"] == "proposed_eligible"
    assert record["geography"]["evidence"][0]["kind"] == "source_structured_location"


def test_non_us_header_is_direct_exclusion_evidence():
    text = description().replace(
        "Location: McLean, Virginia, United States", "Voyager (94001), India, Bangalore, Karnataka"
    )
    record, _ = inspect_observation(row(text), text)
    assert record["disposition"] == "out_of_scope_preserved"
    assert record["reason"] == "exact_non_us_posting_evidence"


def test_source_change_blocks_inventory_and_rebuild_is_immutable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    text = description()
    original = source / "description.txt"
    original.write_text(text)
    input_row = row(text, clean_file=str(original))
    write_jsonl(source / "observations.jsonl", [input_row])
    policy = tmp_path / "policy.json"
    write_json(policy, {"annual_target": 30})
    result = build(source, policy, tmp_path)
    destination = Path(result["run_dir"])
    assert original.read_text() == text
    assert read_jsonl(destination / "observations.jsonl")[0]["record_retained"]
    assert read_json(destination / "receipt.json")["model_calls"] == 0
    assert build(source, policy, tmp_path)["run_dir"] == result["run_dir"]
    (destination / "report.md").write_text("tampered")
    with pytest.raises(ValueError, match="Immutable"):
        build(source, policy, tmp_path)
    original.write_text(text + "changed")
    with pytest.raises(ValueError, match="source text changed"):
        build(source, policy, tmp_path)
