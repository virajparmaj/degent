"""Scope boundary, provenance, duplicate and held-out leakage regressions."""

import pytest

from degent.openrouter import configuration
from degent.scoped_selection import (
    build,
    classify,
    dispatch_rows,
    packet_audit,
    phase_plan,
    token_estimate,
)
from degent.store import digest, read_json, read_jsonl, write_json, write_jsonl


def source(title, duties, location="McLean, Virginia, United States"):
    return f"Capital One\n{title}\nLocation: {location}\n{title}\nWhat You'll Do:\n{duties}\nBasic Qualifications:\nBachelor's degree\n"


def row(title, text, doc="d1", vacancy="r1"):
    return {
        "title": title,
        "document_id": doc,
        "vacancy_id": vacancy,
        "source_sha256": digest(text),
        "clean_sha256": digest(text),
        "observation_id": doc,
        "collection_kind": "current",
        "requisition_id": vacancy,
        "level": "senior",
        "geography_status": "us_location",
        "description_completeness": "unreviewed",
        "description_readable": True,
    }


@pytest.mark.parametrize(
    "title,duties,decision,domain",
    [
        (
            "Full-stack Engineer",
            "Build web applications using Java and AWS. Collaborate with product managers.",
            "exclude",
            None,
        ),
        (
            "Staff Engineer",
            "You will work alongside developers, machine learning experts and product managers.\nDesign full-stack applications.",
            "exclude",
            None,
        ),
        (
            "Data Engineer",
            "Design and build data pipelines and platforms for analytics.",
            "include",
            "data_model_platform",
        ),
        (
            "Full-stack Engineer",
            "In this role, you will build proprietary solutions for Risk management.",
            "include",
            "risk",
        ),
        (
            "AI Engineer",
            "Design and deploy AI software components including foundation model training.",
            "include",
            "ai_ml",
        ),
        (
            "Business Analyst - People Analytics",
            "Analyze historical workforce data to identify organizational trends.\nCredit Risk: Drive step-change improvements in credit performance by creating risk models.",
            "include",
            "data_analysis_science",
        ),
        (
            "Business Analyst - Travel",
            "Credit Risk: Support step-change improvements in credit performance by creating risk models.",
            "review",
            None,
        ),
        (
            "Product Manager",
            "Own and prioritize feature development roadmaps.",
            "include",
            "product_management",
        ),
        (
            "Product Manager",
            "Human Centered - Obsesses about customer needs.\nBusiness Focused - Delivers game-changing outcomes.",
            "review",
            None,
        ),
        (
            "Data Analyst",
            "Analyze operational data and build executive dashboards.",
            "include",
            "data_analysis_science",
        ),
        (
            "Applied Researcher",
            "Engage in high impact applied research on novel models.",
            "include",
            "research",
        ),
        (
            "Risk Manager",
            "Develop risk management frameworks and assess operational risks.",
            "include",
            "risk",
        ),
        (
            "Fraud Analyst",
            "Analyze fraud detection data and build fraud prevention models.",
            "include",
            "fraud",
        ),
        (
            "Credit Analyst",
            "Monitor credit quality and analyze loan portfolio data.",
            "include",
            "credit",
        ),
        (
            "Quantitative Analyst",
            "Validate statistical models and develop quantitative models.",
            "include",
            "quantitative_modeling",
        ),
        (
            "Executive Recruiting Manager",
            "Analyze recruiting data.\nManage executive search and hire candidates.",
            "exclude",
            None,
        ),
        (
            "Café Coach",
            "You will build seamless community partnerships.\nManage branch operations and host local events.",
            "exclude",
            None,
        ),
        (
            "Work Lead",
            "Manage alumni programs and build seamless community partnerships.",
            "exclude",
            None,
        ),
        (
            "Content Designer",
            "You will co-create solutions and influence product strategy.",
            "exclude",
            None,
        ),
    ],
)
def test_duty_scope(title, duties, decision, domain):
    text = source(title, duties)
    result = classify(row(title, text), text)
    assert result["decision"] == decision
    if domain:
        assert domain in result["functions"]
    if "People Analytics" in title:
        assert "credit" not in result["functions"]
        assert "quantitative_modeling" not in result["functions"]
    for e in result["duty_evidence"] + result["geography_evidence"]:
        assert text[e["start"] : e["end"]] == e["text"]
    assert result["human_decision"] is None
    assert not result["absence_claim_supported"]


def test_boilerplate_and_qualifications_cannot_establish_scope():
    title = "Software Engineer"
    text = source(title, "Design generic web applications.")
    text = text.replace(
        "Basic Qualifications:",
        "Capital One is a leader in AI systems and credit risk.\nBasic Qualifications:",
    )
    text += "Experience building ML models and data pipelines.\n"
    assert classify(row(title, text), text)["decision"] == "exclude"


def test_unheaded_experience_is_not_a_duty_and_uncertain_engineering_is_reviewed():
    title = "Staff Engineer"
    text = source(
        title, "You have experience building ML models.\nDesign generic web applications."
    )
    assert classify(row(title, text), text)["decision"] == "exclude"
    text = source(title, "Lead the strategy for AI-augmented developer tooling.")
    assert classify(row(title, text), text)["decision"] == "review"


def test_exact_us_header_and_missing_geography():
    title = "Data Engineer"
    text = source(title, "Build data pipelines.", "Bengaluru, India")
    r = row(title, text)
    r["geography_status"] = "outside_us_scope"
    result = classify(r, text)
    assert result["decision"] == "exclude"
    assert "India" in result["geography_evidence"][0]["text"]
    r["geography_status"] = "unknown"
    assert classify(r, text)["decision"] == "review"


def test_duplicates_preserved_changed_versions_quarantined():
    records = [
        {"document_id": "a", "vacancy_id": "r", "source_sha256": "h", "decision": "include"},
        {"document_id": "b", "vacancy_id": "r", "source_sha256": "h", "decision": "include"},
    ]
    result, audit = dispatch_rows(records)
    assert len(result) == 2 and sum(r["proposed_dispatch"] for r in result) == 1
    assert not audit["groups"][0]["merged"]
    records[1]["source_sha256"] = "changed"
    result, _ = dispatch_rows(records)
    assert all(r["decision"] == "review" and not r["proposed_dispatch"] for r in result)


def test_supplement_and_pilot_never_reuse_frozen_held_out_identity_or_hash():
    records, texts = [], {}
    for i, title in enumerate(["Risk Manager", "Data Engineer", "Fraud Analyst", "Researcher"]):
        text = source(title, "Build data pipelines and analyze fraud detection data.")
        r = {
            **row(title, text, f"d{i}", f"r{i}"),
            "decision": "include",
            "functions": [title],
            "proposed_dispatch": True,
        }
        records.append(r)
        texts[r["document_id"]] = text
    packet = {
        "documents": [
            {**records[i], "split": split} for i, split in [(0, "calibration"), (1, "held_out")]
        ]
    }
    _, supp = packet_audit(packet, records, texts)
    assert all(d["vacancy_id"] not in {"r0", "r1"} for d in supp["documents"])
    config = {"inference": configuration()}
    estimates = {
        r["document_id"]: token_estimate(
            r, texts[r["document_id"]], config["inference"]["approved_models"]
        )
        for r in records
    }
    plan = phase_plan(config, records, packet, supp, estimates)
    assert "d1" not in plan["phase2"]["pilot_document_ids"]
    assert plan["active_allowances"]["calls"] == 0
    assert plan["phase2"]["call_cap"] == 16


def test_frozen_duplicate_texts_stay_frozen_but_do_not_duplicate_pilot_calls():
    text = source("Data Analyst", "Analyze portfolio data.")
    rows = [
        {
            **row("Data Analyst", text, f"d{i}", f"r{i}"),
            "decision": "include",
            "functions": ["data_analysis_science"],
            "proposed_dispatch": True,
        }
        for i in range(2)
    ]
    packet = {"documents": [{**r, "split": "calibration"} for r in rows]}
    audit, supplement = packet_audit(packet, rows, {r["document_id"]: text for r in rows})
    assert len(packet["documents"]) == 2
    assert audit["distinct_text_hashes"] == 1
    assert len(audit["identical_text_groups"]) == 1
    assert not audit["identical_text_groups"][0]["cross_split"]
    config = {"inference": configuration()}
    estimates = {
        r["document_id"]: token_estimate(r, text, config["inference"]["approved_models"])
        for r in rows
    }
    plan = phase_plan(config, rows, packet, supplement, estimates)
    assert len(plan["phase2"]["pilot_document_ids"]) == 1


def test_build_preserves_inputs_is_repeatable_and_detects_artifact_tampering(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    text = source("Data Engineer", "Build data pipelines.")
    clean = tmp_path / "source.txt"
    clean.write_text(text)
    r = {**row("Data Engineer", text), "clean_file": str(clean)}
    historical = {
        **r,
        "observation_id": "h1",
        "document_id": "h1",
        "collection_kind": "historical",
        "comparison_subset": True,
    }
    write_jsonl(run / "observations.jsonl", [r, historical])
    write_json(run / "dashboard-config.json", {"inference": configuration()})
    write_json(
        run / "gold-packet.json", {"documents": [{**r, "split": "calibration", "full_text": text}]}
    )
    before = {p: p.read_bytes() for p in run.iterdir()}
    output = build(run)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert len(read_jsonl(output / "selection.jsonl")) == 1
    assert read_json(output / "report.json")["counts"]["historical_observations_untouched"] == 1
    (output / "human-decisions.jsonl").write_text('{"decision":"pending"}\n')
    assert build(run) == output
    (output / "coverage.csv").write_text("tampered")
    with pytest.raises(ValueError, match="Immutable"):
        build(run)
