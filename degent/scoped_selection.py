"""Offline, immutable current-domain selection; never authorizes or runs inference.

Run with ``python -m degent.scoped_selection --run PATH``. Every current
observation receives a disposition; historical observations are only referenced.
This is a conservative proposal, not human adjudication or an absence claim.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .evidence import select_units
from .model_runner import method_signature, request_for
from .openrouter import payload_for
from .preparation_audit import format_tags, identity_audit
from .store import digest, read_json, read_jsonl, write_json, write_jsonl

VERSION = "current-domain-selection-v1"
ACTION = re.compile(
    r"\b(?:analy[sz]\w*|assess\w*|build\w*|creat\w*|design\w*|develop\w*|"
    r"deploy\w*|deliver\w*|implement\w*|maintain\w*|monitor\w*|manag\w*|"
    r"lead\w*|own\w*|defin\w*|conduct\w*|evaluat\w*|validat\w*|test\w*|"
    r"oversee\w*|challeng\w*|review\w*|support\w*|mitigat\w*|identif\w*|"
    r"perform\w*|extract\w*|configur\w*|advise\w*|advanc\w*|optim\w*|"
    r"ensur\w*|driv\w*|influenc\w*|establish\w*|translat\w*|engag\w*|"
    r"leverag\w*|articulat\w*|reveal\w*|obtain\w*)\b",
    re.I,
)
ENGINEER = re.compile(r"engineer|developer|software|\barchitect\b|\bIT\b|tech operations", re.I)
UNRELATED_TITLE = re.compile(
    r"recruit\w*|talent acquisition|administrative|branch ambassador|caf[ée] ambassador|"
    r"(?:retail|relationship) bank\w*|(?:customer|operations|service) representative|"
    r"concierge|dealer (?:success|sales|lien)|relationship manager|tech operations|"
    r"help ?desk|desktop support|caf[ée] coach|market manager|area sales manager|"
    r"inside sales|client development ad sales|business banker|call center|"
    r"fulfillment operations|senior counsel|general counsel|business counsel",
    re.I,
)
TITLE_CUES = re.compile(
    r"\bAI\b|\bML\b|machine learning|risk|fraud|credit|underwrit|product|"
    r"analyst|analytic|data|scientist|science|quant|model|research|engineer|"
    r"developer|software|cyber|compliance|audit",
    re.I,
)
# These BA lines are copied across unrelated teams; they cannot identify domain.
BA_TEMPLATE = re.compile(
    r"^(?:Credit Risk:|"
    r"Product: (?:Perform modeling/analytics to assist|Develop and implement new product and pricing)|"
    r"Analysis: Identify business challenges and opportunities|"
    r"Strategic & analytic orientation:|As a (?:Senior )?Business Analyst at Capital One|"
    r"Marketing: (?:Support direct.to.consumer|Lead direct to consumer)|"
    r"Execution: Involve problem frame|Partnership: Work closely with colleagues across Capital One including)",
    re.I,
)
INCIDENTAL = re.compile(
    r"^(?:Do you love|Do you enjoy|Capital One is|At Capital One|Data is at the center|"
    r"Product Management at Capital One|We believe our portfolio|We are seeking|"
    r"We.re (?:currently )?seeking|Share your passion|Stay (?:current|abreast)|"
    r"Keep (?:up|current)|Basic Qualifications|Preferred Qualifications)",
    re.I,
)
AI_DUTY = re.compile(
    r"\b(?:train(?:ing)?|fine.tun(?:e|ing)|deploy(?:ing)?|build(?:ing)?|develop(?:ing)?|"
    r"design(?:ing)?|evaluat(?:e|ing)|configur(?:e|ing)|creat(?:e|ing)|advanc(?:e|ing)|"
    r"optimiz(?:e|ing)|implement(?:ing)?|maintain(?:ing)?|own(?:ing)?|lead(?:ing)?)\b"
    r".{0,180}(?:AI (?:software|systems?|models?|foundation|platform|agents?|infrastructure|reliability|engineering)|"
    r"ML (?:models?|components|platform|infrastructure|systems?|applications|pipelines?)|"
    r"machine learning (?:models?|platform|pipelines?|infrastructure)|foundation model|"
    r"LLM|large language|agentic (?:models?|systems?|platform|AI)|neural network)",
    re.I,
)
DOMAINS = {
    "ai_ml": AI_DUTY.pattern,
    "risk": r"(?:risk (?:management|assessment|assessments|mitigation|oversight|controls?|frameworks?|analysis|analytics|monitoring)|(?:identify|assess|mitigate|monitor|manage|evaluat\w*|analy[sz]\w*).{0,90}(?:operational|credit|compliance|financial|model|business|technology|cyber|security|enterprise|emerging|potential) risks?|(?:test\w*|validat\w*|challeng\w*|review\w*).{0,80}(?:business controls|risk controls|compliance control|credit loss|loss projections))",
    "fraud": r"(?:\bfraud (?:detection|prevention|strategy|strategies|risk|analytics|models?|investigations?)|(?:detect|prevent|investigat|mitigat|analy[sz]|manag|monitor|design|implement)\w*.{0,120}(?:\bfraud\b|money laundering|\bAML\b|financial crime)|(?:\bAML\b|anti.money laundering).{0,80}(?:program|strategies|controls|soundness|risk))",
    "credit": r"(?:credit (?:risk|loss|performance|models?|modeling|decision|underwriting|polic|portfolio|(?:and market )?data)|loss (?:forecast|estimat|projection)|underwrit\w*.{0,90}(?:loan|credit|portfolio)|(?:analy[sz]\w*|assess\w*|review\w*|manage\w*|monitor\w*).{0,90}(?:creditworthiness|credit quality|loan portfolio))",
    "product_management": r"(?:own\w*|defin\w*|develop\w*|manag\w*|prioriti[sz]\w*|creat\w*|establish\w*).{0,80}(?:product (?:vision|strategy|strategies|roadmap|backlog|requirements|development|lifecycle)|feature development roadmaps?|product and pricing strategies)|(?:translat\w*|defin\w*).{0,90}(?:business|customer|user) needs.{0,90}(?:product|requirements)",
    "data_analysis_science": r"(?:analy[sz]\w*|explor\w*|manipulat\w*|extract\w*|interpret\w*|translat\w*|reveal\w*).{0,110}(?:data\b|datasets?|financial results|performance (?:and efficiency )?metrics|trends|statistic)|(?:build\w*|creat\w*|develop\w*|design\w*|deliver\w*).{0,110}(?:dashboards?|analytical (?:tools|solutions)|data (?:insights|analytics)|predictive models?|statistical models?|financial (?:forecast|report))|(?:conduct|perform|lead)\w*.{0,90}(?:quantitative|statistical|data|financial) analys[ie]s",
    "quantitative_modeling": r"(?:build\w*|develop\w*|design\w*|validat\w*|review\w*|challeng\w*|test\w*|implement\w*|evaluat\w*|creat\w*|maintain\w*).{0,140}(?:(?:statistical|quantitative|credit|risk|financial|valuation|forecasting|predictive|econometric|capital|loss) models?|credit loss (?:forecast|estimat)|loss projections)|(?:model (?:development|validation|risk management)|statistical modeling|quantitative modeling)",
    "research": r"(?:conduct\w*|design\w*|lead\w*|develop\w*|perform\w*|advanc\w*|creat\w*|engag\w*).{0,100}(?:research (?:studies|program|projects?|agenda|experiments?)|primary research|user research|applied research|scientific research|experiments|research and testing agents)|(?:research).{0,100}(?:publish|novel|hypothes|experiments)|(?:prepare|publish).{0,100}(?:conference submissions|research papers|novel methods)",
    "data_model_platform": r"(?:design\w*|build\w*|develop\w*|architect\w*|maintain\w*|implement\w*|deliver\w*|support\w*|own\w*|operat\w*).{0,150}(?:data (?:pipelines?|platforms?|products?|solutions?|warehouse|lake|infrastructure)|analytics (?:platform|infrastructure|tools)|model (?:platform|serving|infrastructure)|feature store|ETL|data engineering (?:design|patterns)|machine learning (?:platform|infrastructure|pipelines?))",
}
# Stem wildcards would confuse developers/development with develop and creative
# with create. Keep action morphology separate from noun/domain vocabulary.
VERB_FORMS = {
    "analy[sz]": r"analy[sz](?:e|es|ed|ing)",
    "assess": r"assess(?:es|ed|ing)?",
    "build": r"build(?:s|ing)?",
    "creat": r"creat(?:e|es|ed|ing)",
    "design": r"design(?:s|ed|ing)?",
    "develop": r"develop(?:s|ed|ing)?",
    "deploy": r"deploy(?:s|ed|ing)?",
    "deliver": r"deliver(?:s|ed|ing)?",
    "implement": r"implement(?:s|ed|ing)?",
    "maintain": r"maintain(?:s|ed|ing)?",
    "monitor": r"monitor(?:s|ed|ing)?",
    "manag": r"manag(?:e|es|ed|ing)",
    "lead": r"lead(?:s|ing)?",
    "own": r"own(?:s|ed|ing)?",
    "defin": r"defin(?:e|es|ed|ing)",
    "conduct": r"conduct(?:s|ed|ing)?",
    "evaluat": r"evaluat(?:e|es|ed|ing)",
    "validat": r"validat(?:e|es|ed|ing)",
    "test": r"test(?:s|ed|ing)?",
    "oversee": r"oversee(?:s|ing)?",
    "challeng": r"challeng(?:e|es|ed|ing)",
    "review": r"review(?:s|ed|ing)?",
    "support": r"support(?:s|ed|ing)?",
    "mitigat": r"mitigat(?:e|es|ed|ing)",
    "identif": r"identif(?:y|ies|ied|ying)",
    "perform": r"perform(?:s|ed|ing)?",
    "extract": r"extract(?:s|ed|ing)?",
    "configur": r"configur(?:e|es|ed|ing)",
    "advise": r"advis(?:e|es|ed|ing)",
    "advanc": r"advanc(?:e|es|ed|ing)",
    "optim": r"optimi[sz](?:e|es|ed|ing)",
    "ensur": r"ensur(?:e|es|ed|ing)",
    "driv": r"driv(?:e|es|ing)",
    "influenc": r"influenc(?:e|es|ed|ing)",
    "establish": r"establish(?:es|ed|ing)?",
    "translat": r"translat(?:e|es|ed|ing)",
    "engag": r"engag(?:e|es|ed|ing)",
    "leverag": r"leverag(?:e|es|ed|ing)",
    "articulat": r"articulat(?:e|es|ed|ing)",
    "reveal": r"reveal(?:s|ed|ing)?",
    "obtain": r"obtain(?:s|ed|ing)?",
    "explor": r"explor(?:e|es|ed|ing)",
    "manipulat": r"manipulat(?:e|es|ed|ing)",
    "interpret": r"interpret(?:s|ed|ing)?",
    "prioriti[sz]": r"prioriti[sz](?:e|es|ed|ing)",
    "architect": r"architect(?:s|ed|ing)?",
    "operat": r"operat(?:e|es|ed|ing)",
}


def verb_pattern(pattern: str) -> str:
    for stem, form in VERB_FORMS.items():
        pattern = pattern.replace(stem + r"\w*", r"\b" + form + r"\b")
    return pattern


ACTION = re.compile(verb_pattern(ACTION.pattern), re.I)
COMPILED = {key: re.compile(verb_pattern(value), re.I) for key, value in DOMAINS.items()}


def duty_units(text: str, title: str) -> tuple[list[dict], list[dict], dict]:
    selection = select_units(text)
    usable, rejected = [], []
    for unit in selection["units"]:
        if not unit["selected"] or unit["heading"] or unit["text"] == title:
            continue
        if unit["statement_scope"] not in {"responsibility", "unknown"}:
            continue
        if BA_TEMPLATE.search(unit["text"]) or INCIDENTAL.search(unit["text"]):
            rejected.append(unit)
            continue
        # Evaluate one exact sentence at a time: a duty and an incidental domain
        # several sentences later must never establish relevance together.
        for sentence in re.finditer(r".+?(?:[.!?](?=\s+[A-Z]|$)|$)", unit["text"]):
            quote = sentence.group().strip()
            begin = (
                unit["start"]
                + sentence.start()
                + len(sentence.group())
                - len(sentence.group().lstrip())
            )
            part = {**unit, "start": begin, "end": begin + len(quote), "text": quote}
            if re.search(
                r"\b(?:years? of experience|hands.on experience|experience (?:building|developing|in|with)|track record|background in)\b",
                quote,
                re.I,
            ) and not re.search(r"\b(?:you will|you.ll|responsible for|this role)\b", quote, re.I):
                rejected.append(part)
                continue
            explicit = bool(
                re.search(
                    r"\b(?:you|your|this role|the role|the candidate|successful candidate|position)\b",
                    quote,
                    re.I,
                )
            )
            imperative = bool(
                ACTION.match(quote) or re.match(r"[\w &/-]{1,70}:\s*" + ACTION.pattern, quote, re.I)
            )
            if ACTION.search(quote) and (
                explicit or imperative or unit["statement_scope"] == "responsibility"
            ):
                usable.append(part)
    return usable, rejected, selection


def evidence(unit: dict, kind: str) -> dict:
    return {
        "kind": kind,
        "start": unit["start"],
        "end": unit["end"],
        "text": unit["text"],
        "unit_id": unit["unit_id"],
    }


def classify(row: dict, text: str) -> dict:
    duties, rejected, selection = duty_units(text, row["title"])
    matches = defaultdict(list)
    for unit in duties:
        for domain, pattern in COMPILED.items():
            if pattern.search(unit["text"]):
                matches[domain].append(evidence(unit, domain))
        if re.search(
            r"product manag|product owner|director.{0,15}product", row["title"], re.I
        ) and re.search(
            r"\b(?:drive|lead|shape|articulate|own|prioritize)\w*.{0,90}(?:product (?:roadmap|strategy|vision)|backlog|feature development roadmap)",
            unit["text"],
            re.I,
        ):
            matches["product_management"].append(evidence(unit, "product_management"))
    engineering = bool(ENGINEER.search(row["title"]))
    if not re.search(r"product manag|product owner|director.{0,15}product", row["title"], re.I):
        matches["product_management"] = [
            e
            for e in matches["product_management"]
            if re.search(
                r"\b(?:own|define|prioritize|manage)(?:s|d|ing)?\b.{0,60}(?:product (?:roadmap|vision|strategy)|backlog)",
                e["text"],
                re.I,
            )
        ]
    # Partnering with PMs is not product management; risk/credit references in
    # generic delivery copy are not direct domain engineering responsibilities.
    if engineering:
        matches.pop("product_management", None)
        for domain in (
            "risk",
            "credit",
            "fraud",
            "research",
            "data_analysis_science",
            "quantitative_modeling",
        ):
            matches[domain] = [
                e
                for e in matches[domain]
                if re.search(
                    verb_pattern(
                        r"(?:build|develop|design|implement|maintain|deploy|architect|deliver|own)\w*.{0,160}"
                        r"(?:risk|credit|fraud|analyt|model|research|data).{0,80}"
                        r"(?:platform|system|solution|tool|pipeline|model|application|infrastructure)|"
                        r"(?:build|develop|design|implement|maintain|deploy|own)\w*.{0,80}"
                        r"(?:systems?|solutions?|tools?|applications?|technology stack).{0,160}(?:risk|credit|fraud|analytics|models?)"
                    ),
                    e["text"],
                    re.I,
                )
            ]
    matches = {key: value for key, value in matches.items() if value}
    decision = (
        "include"
        if matches
        else "review" if TITLE_CUES.search(row["title"]) or not duties else "exclude"
    )
    reason = (
        "direct_domain_duties"
        if matches
        else (
            "insufficient_specific_domain_duties"
            if decision == "review"
            else "duties_outside_requested_domains"
        )
    )
    if (
        not matches
        and engineering
        and duties
        and not re.search(
            r"\bAI\b|\bML\b|machine learning|data engineer|risk tech|fraud|credit|research",
            row["title"],
            re.I,
        )
    ):
        decision, reason = (
            "exclude",
            "generic_engineering_without_direct_domain_or_data_model_platform_duties",
        )
        # A domain-bearing duty that the strict patterns cannot resolve is an
        # ambiguity, not proof that the engineering role is unrelated.
        if any(
            re.search(
                r"\bAI\b|\bML\b|machine learning|fraud|credit|risk (?:intelligence|mitigation|reduction)|data (?:platform|pipeline|product)|analytics|models?\b",
                u["text"],
                re.I,
            )
            for u in duties
        ):
            decision, reason = "review", "engineering_domain_link_requires_exact_duty_review"
    if (
        matches
        and set(matches) <= {"risk", "fraud"}
        and not re.search(
            r"risk|fraud|compliance|audit|cyber|security|governance|oversight|AML",
            row["title"],
            re.I,
        )
        and not engineering
    ):
        decision, reason = "review", "primary_domain_vs_incidental_control_duties_requires_review"
    if UNRELATED_TITLE.search(row["title"]) and duties:
        decision, reason = (
            "exclude",
            "primary_retail_service_administrative_recruiting_or_generic_it_duties",
        )
        matches = {}
    geo = [
        evidence(u, "posting_geography")
        for u in selection["units"]
        if u["text"].startswith("Location:")
    ]
    if row.get("geography_status") == "outside_us_scope":
        decision, reason = "exclude", "outside_us_scope"
    elif not any(re.search(r"United States|\bUSA\b|\bUS\b", e["text"]) for e in geo):
        decision, reason = "review", "us_geography_requires_exact_posting_evidence"
    if not row.get("description_readable", True):
        decision, reason = "review", "unreadable_or_closed_preserved_description"
    spans = list(
        {
            (e["kind"], e["start"], e["end"]): e
            for domain in sorted(matches)
            for e in matches[domain]
        }.values()
    )
    return {
        "decision": decision,
        "reason": reason,
        "functions": sorted(matches),
        "engineering": engineering,
        "duty_evidence": spans or [evidence(u, "observed_duty") for u in duties],
        "geography_evidence": geo,
        "rejected_scope_evidence": [evidence(u, "template_or_incidental") for u in rejected],
        "review_context_evidence": (
            [
                evidence(u, "context_only_not_scope_evidence")
                for u in selection["units"]
                if u["selected"] and not u["heading"] and u["text"] != row["title"]
            ]
            if not spans
            else []
        ),
        "description_completeness": row.get("description_completeness", "unknown"),
        "selection_completeness": "unknown_requires_full_text_omission_review",
        "human_decision": None,
        "absence_claim_supported": False,
    }


def token_estimate(row: dict, text: str, models: list[str]) -> dict:
    selected = select_units(text, row["source_sha256"])
    request = request_for(selected)
    estimates = {}
    for model in models:
        payload = payload_for(request, model)
        compact = json.dumps(payload, separators=(",", ":"))
        estimates[model] = {
            "input_character_estimate_range": [
                math.ceil(len(compact) / 5),
                math.ceil(len(compact) / 3),
            ],
            "input_byte_reservation": len(json.dumps(payload).encode()) + 512,
            "output_planning_tokens": 400,
            "output_reservation_tokens": 4096,
        }
    return {
        "document_id": row["document_id"],
        "vacancy_id": row["vacancy_id"],
        "full_characters": len(text),
        "selected_characters": selected["selected_characters"],
        "models": estimates,
        "native_tokens": None,
    }


def aggregate_tokens(estimates: list[dict], models: list[str]) -> dict:
    return {
        model: {
            key: sum(e["models"][model][key] for e in estimates)
            for key in (
                "input_byte_reservation",
                "output_planning_tokens",
                "output_reservation_tokens",
            )
        }
        | {
            "input_character_estimate_range": [
                sum(e["models"][model]["input_character_estimate_range"][i] for e in estimates)
                for i in (0, 1)
            ]
        }
        for model in models
    }


def coverage(rows: list[dict]) -> list[dict]:
    cells = defaultdict(list)
    for row in rows:
        for function in row["functions"] or ["unresolved"]:
            cells[(row["decision"], function, row["level"])].append(row)
    return [
        {
            "decision": key[0],
            "function": key[1],
            "seniority": key[2],
            "observations": len(group),
            "candidate_vacancies": len({r["vacancy_id"] for r in group}),
            "dispatch_documents": sum(r["proposed_dispatch"] for r in group),
            "assessed_vacancies": 0,
            "percentage": None,
        }
        for key, group in sorted(cells.items())
    ]


def features(row: dict, text: str) -> set[str]:
    return (
        {"function:" + f for f in row["functions"]}
        | {"cell:" + f + ":" + row["level"] for f in row["functions"]}
        | {"level:" + row["level"]}
        | {"format:" + f for f in format_tags(text)}
    )


def packet_audit(packet: dict, rows: list[dict], texts: dict) -> tuple[dict, dict]:
    by_doc = {r["document_id"]: r for r in rows}
    included = [r for r in rows if r["proposed_dispatch"]]
    all_features = set().union(*(features(r, texts[r["document_id"]]) for r in included))
    audit, proposals = {}, []
    frozen_vacancies = {d["vacancy_id"] for d in packet["documents"]}
    frozen_hashes = {d["source_sha256"] for d in packet["documents"]}
    used_vacancies, used_hashes = set(frozen_vacancies), set(frozen_hashes)
    # Allocate scarce remaining strata to evaluation first. Calibration can
    # accept an explicit gap; it must not consume the last held-out candidate.
    for split in ("held_out", "calibration"):
        docs = [d for d in packet["documents"] if d["split"] == split]
        relevant = [
            by_doc[d["document_id"]]
            for d in docs
            if d["document_id"] in by_doc and by_doc[d["document_id"]]["decision"] == "include"
        ]
        found = set().union(*(features(r, texts[r["document_id"]]) for r in relevant))
        missing = all_features - found
        audit[split] = {
            "total": len(docs),
            "dispositions": dict(
                Counter(
                    (
                        by_doc[d["document_id"]]["decision"]
                        if d["document_id"] in by_doc
                        else "unmapped"
                    )
                    for d in docs
                )
            ),
            "relevant_feature_counts": dict(
                Counter(f for r in relevant for f in features(r, texts[r["document_id"]]))
            ),
            "missing_features": sorted(missing),
            "unfillable_features": [],
        }
        # Greedy set cover fills actual marginal gaps, without a document cap.
        while missing:
            pool = [
                r
                for r in included
                if r["vacancy_id"] not in used_vacancies and r["source_sha256"] not in used_hashes
            ]
            candidate = max(
                pool,
                key=lambda r: (
                    len(features(r, texts[r["document_id"]]) & missing),
                    digest(r["document_id"]),
                ),
                default=None,
            )
            covered = (
                features(candidate, texts[candidate["document_id"]]) & missing
                if candidate
                else set()
            )
            if not covered:
                audit[split]["unfillable_features"] = sorted(missing)
                break
            used_vacancies.add(candidate["vacancy_id"])
            used_hashes.add(candidate["source_sha256"])
            proposals.append(
                {
                    "document_id": candidate["document_id"],
                    "vacancy_id": candidate["vacancy_id"],
                    "source_sha256": candidate["source_sha256"],
                    "title": candidate["title"],
                    "proposed_split": split,
                    "fills": sorted(covered),
                    "full_text": texts[candidate["document_id"]],
                    "labels": None,
                    "omission_audited": False,
                    "human_decision": None,
                }
            )
            missing -= covered
    audit["documents"] = [
        {
            "document_id": d["document_id"],
            "split": d["split"],
            "title": d["title"],
            "decision": by_doc.get(d["document_id"], {}).get("decision", "unmapped"),
            "reason": by_doc.get(d["document_id"], {}).get("reason"),
            "functions": by_doc.get(d["document_id"], {}).get("functions", []),
        }
        for d in packet["documents"]
    ]
    hashes = defaultdict(list)
    for document in packet["documents"]:
        hashes[document["source_sha256"]].append(document)
    audit["identical_text_groups"] = [
        {
            "source_sha256": sha,
            "documents": [
                {
                    "document_id": d["document_id"],
                    "vacancy_id": d["vacancy_id"],
                    "split": d["split"],
                }
                for d in group
            ],
            "cross_split": len({d["split"] for d in group}) > 1,
            "human_decision": None,
        }
        for sha, group in sorted(hashes.items())
        if len(group) > 1
    ]
    audit["distinct_text_hashes"] = len(hashes)
    audit["policy"] = (
        "Original 20/40 is unchanged. Relevance is a proposal, not a relabeling or performance score. Marginal feature coverage is not representativeness."
    )
    return audit, {
        "status": "proposed_not_frozen_or_approved",
        "documents": proposals,
        "policy": "Human-review assignments and full-text labels, then freeze separately before calibration/pilot. Never move a frozen held-out vacancy/hash into the pilot.",
    }


def dispatch_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["vacancy_id"]].append(row)
    duplicates = []
    for vacancy, members in sorted(groups.items()):
        hashes = {r["source_sha256"] for r in members}
        changed = len(hashes) > 1
        if len(members) > 1:
            duplicates.append(
                {
                    "vacancy_id": vacancy,
                    "document_ids": [r["document_id"] for r in members],
                    "description_hashes": sorted(hashes),
                    "status": (
                        "changed_current_versions_need_review"
                        if changed
                        else "repeated_capture_same_source_identity"
                    ),
                    "human_decision": None,
                    "merged": False,
                }
            )
        for i, row in enumerate(sorted(members, key=lambda r: r["document_id"])):
            if changed and row["decision"] == "include":
                row.update(
                    decision="review",
                    reason="changed_current_versions_need_identity_and_version_review",
                )
            row["proposed_dispatch"] = row["decision"] == "include" and i == 0
            row["dispatch_reason"] = (
                "one_capture_for_unchanged_confirmed_current_source_identity"
                if row["proposed_dispatch"]
                else (
                    "repeated_capture_retained"
                    if row["decision"] == "include"
                    else "outside_proposed_batch"
                )
            )
    return rows, {
        "groups": duplicates,
        "policy": "No observations merged/deleted. Repeated unchanged captures share one proposed dispatch; changed versions remain in review. Equal text across different requisitions is only an identity-review cue.",
    }


def phase_plan(
    config: dict, rows: list[dict], packet: dict, supplement: dict, estimates: dict
) -> dict:
    included = {r["document_id"]: r for r in rows if r["decision"] == "include"}
    held_ids = {d["vacancy_id"] for d in packet["documents"] if d["split"] == "held_out"}
    held_hashes = {d["source_sha256"] for d in packet["documents"] if d["split"] == "held_out"}
    candidates = [
        d
        for d in packet["documents"]
        if d["split"] == "calibration" and d["document_id"] in included
    ]
    candidates += [d for d in supplement["documents"] if d["proposed_split"] == "calibration"]
    pilot, used, used_hashes = [], set(), set()
    for d in candidates:
        if (
            d["vacancy_id"] not in used
            and d["source_sha256"] not in used_hashes
            and d["vacancy_id"] not in held_ids
            and d["source_sha256"] not in held_hashes
        ):
            pilot.append(d["document_id"])
            used.add(d["vacancy_id"])
            used_hashes.add(d["source_sha256"])
    # Cover actual domain/level gaps within the explicit pilot budget. The full
    # selection and the uncapped supplement proposal remain intact.
    selected_pilot, covered = [], set()
    while pilot and len(selected_pilot) < 12:
        doc = max(
            pilot,
            key=lambda d: (
                len(set(included[d]["functions"]) - covered),
                len(included[d]["functions"]),
                digest(d),
            ),
        )
        selected_pilot.append(doc)
        covered.update(included[doc]["functions"])
        pilot.remove(doc)
    pilot = selected_pilot
    return {
        "status": "proposal_only_no_authorization",
        "approved_models": config["inference"]["approved_models"],
        "selected_model_identity_unchanged": config["inference"]["identity"],
        "phase2": {
            "pilot_document_ids": pilot,
            "unique_document_cap": 12,
            "call_cap": 16,
            "retry_calls_within_cap": 4,
            "input_token_cap": 400000,
            "output_token_cap": 65536,
            "per_call_output_cap": 4096,
            "dollar_cap_usd": 1,
            "all_caps_shared_across_models_and_retries": True,
            "model_policy": "Use the currently selected approved model. Any explicit model comparison shares these total caps and needs a separate reviewed method signature; never automatic fallback.",
            "pricing_policy": "Offline proposal only. Before authorization, verify dated model/context-tier rates and provider ceilings. Stop if conservative byte reservations or rates exceed any cap. No live-price claim.",
            "candidate_token_estimates": {doc: estimates[doc] for doc in pilot if doc in estimates},
            "prerequisites": [
                "Human approve scoped selection and engineering/risk/research boundaries.",
                "Resolve pilot identity, US geography, seniority and employment/remote uncertainties.",
                "Review full originals and excluded units; label omissions and completeness.",
                "Approve stable taxonomy/lineage and label schema including explicit attributes.",
                "Review supplement assignments and freeze held-out membership before tuning.",
                "Review frozen identical-text groups and all corpus multi-requisition identity cues; no title/text similarity merges. Confirm no cross-split source-hash leakage.",
                "Label calibration independently; approve pilot IDs and exact method signature.",
                "Separate explicit spending authorization with dated rates and durable caps.",
            ],
        },
        "phase3": {
            "status": "blocked_pending_human_labels_and_method_freeze",
            "original_packet": "unchanged 20 calibration / 40 held_out",
            "supplement": "separate proposed supplement; no labels or approvals generated",
            "thresholds": {
                "statement_precision": 0.95,
                "statement_recall": 0.90,
                "strength_context_accuracy": 0.95,
                "selector_recall": 0.90,
                "explicit_attribute_accuracy": 0.95,
                "exact_evidence_support": 1.0,
            },
            "policy": "Evaluate untouched held-out full-text labels once after freezing method and taxonomy. Report original full 40 and preregistered scoped slice/supplement separately, including subgroup limits. Review packet identity and duplicate/leakage findings before evaluation; a pooled broad-scope pass alone does not qualify the scoped method. Failed/unlabeled/unassessed gates do not pass; tuning after a failure needs new untouched held-out evidence and human approval.",
        },
        "phase4": {
            "status": "blocked_until_phase3_pass_and_separate_authorization",
            "policy": "Use an explicit immutable approved dispatch manifest, approved method and full-selection budget. Review queue stays outside until separate adjudication. Audit ceil(10%) per function/level plus all flagged outputs. Preserve raw output, exact spans, retries and native usage; suppress percentages below 30 assessed vacancies or with unresolved completeness.",
        },
        "active_allowances": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "dollars": 0},
        "human_approvals_generated": 0,
    }


def source_selection_audit(row: dict, text: str) -> dict:
    selection = select_units(text, row["source_sha256"])
    selected = [u for u in selection["units"] if u["selected"]]
    outside = [
        e
        for e in row["duty_evidence"]
        if not any(u["start"] <= e["start"] < e["end"] <= u["end"] for u in selected)
    ]
    return {
        "document_id": row["document_id"],
        "source_sha256": row["source_sha256"],
        "selector_version": selection["selector_version"],
        "full_characters": len(text),
        "selected_characters": selection["selected_characters"],
        "selected_unit_count": len(selected),
        "excluded_units": [u for u in selection["units"] if not u["selected"]],
        "domain_duty_spans_outside_extraction_selection": outside,
        "description_completeness": row["description_completeness"],
        "selection_completeness": "unknown",
        "human_omission_audit": False,
        "policy": "Every omitted unit remains reviewable. No source/selector absence claim or measured recall.",
    }


def budget_reference(run: Path, plan: dict) -> dict:
    path = run.resolve().parents[1] / "config" / "openrouter-prices-2026-10-08.json"
    if not path.exists():
        return {"status": "saved_reference_unavailable_no_price_claim"}
    catalog = read_json(path)
    caps = plan["phase2"]
    prices = {}
    for model in catalog["models"]:
        tiers = [model["pricing"], *model["pricing"].get("overrides", [])]
        input_rate = max(float(t.get("prompt", model["pricing"]["prompt"])) for t in tiers)
        output_rate = max(float(t.get("completion", model["pricing"]["completion"])) for t in tiers)
        prices[model["id"]] = {
            "conservative_input_per_million": input_rate * 1e6,
            "conservative_output_per_million": output_rate * 1e6,
            "cost_at_shared_phase2_token_caps_usd": round(
                caps["input_token_cap"] * input_rate + caps["output_token_cap"] * output_rate, 6
            ),
        }
    return {
        "status": "offline_reference_only_verify_before_authorization",
        "file": str(path),
        "sha256": digest(path.read_bytes()),
        "retrieved_at": catalog["retrieved_at"],
        "models": prices,
        "policy": "Uses maximum saved input/output tier, without cache discounts. These are neither live prices nor an authorization.",
    }


def build(run: Path) -> Path:
    config = read_json(run / "dashboard-config.json")
    if config["inference"]["enabled"] or config["inference"]["spending_authorized"]:
        raise ValueError("Phase 1 requires disabled inference and spending.")
    observations = read_jsonl(run / "observations.jsonl")
    texts, rows = {}, []
    for original in observations:
        if original.get("collection_kind") != "current":
            continue
        text = Path(original["clean_file"]).read_text()
        if digest(text) != original["clean_sha256"]:
            raise ValueError("Preserved description hash mismatch: " + original["document_id"])
        texts[original["document_id"]] = text
        rows.append(
            {
                "observation_id": original["observation_id"],
                "document_id": original["document_id"],
                "vacancy_id": original["vacancy_id"],
                "requisition_id": original.get("requisition_id"),
                "source_sha256": original["clean_sha256"],
                "clean_file": original["clean_file"],
                "title": original["title"],
                "level": original.get("level", "unknown"),
                "seniority_evidence": original.get("seniority_evidence"),
                "employment_type": original.get("employment_type", "unknown"),
                "remote_eligibility": original.get("remote_eligibility", "unknown"),
                "original_eligible": original.get("eligible"),
                **classify(original, text),
            }
        )
    rows, duplicate_audit = dispatch_rows(rows)
    packet = read_json(run / "gold-packet.json")
    relevance, supplement = packet_audit(packet, rows, texts)
    models = config["inference"]["approved_models"]
    estimates = [
        token_estimate(r, texts[r["document_id"]], models) for r in rows if r["proposed_dispatch"]
    ]
    # Also estimate frozen calibration captures even when a different unchanged
    # capture is the representative; this does not add them to full dispatch.
    estimate_by_id = {r["document_id"]: r for r in estimates}
    for doc in packet["documents"]:
        match = next(
            (
                r
                for r in rows
                if r["document_id"] == doc["document_id"] and r["decision"] == "include"
            ),
            None,
        )
        if match and match["document_id"] not in estimate_by_id:
            estimate_by_id[match["document_id"]] = token_estimate(
                match, texts[match["document_id"]], models
            )
    signature = digest(
        json.dumps(
            {
                "version": VERSION,
                "implementation": digest(Path(__file__).read_bytes()),
                "input_sha256": digest((run / "observations.jsonl").read_bytes()),
                "config_sha256": digest((run / "dashboard-config.json").read_bytes()),
                "gold_sha256": digest((run / "gold-packet.json").read_bytes()),
                "extraction_method_signature": method_signature(config["inference"]["identity"]),
                "rows": rows,
            },
            sort_keys=True,
        )
    )
    target = run / "scoped-selections" / (VERSION + "-" + signature[:16])
    if target.exists():
        manifest = read_json(target / "manifest.json")
        if any(
            not (target / name).is_file() or digest((target / name).read_bytes()) != sha
            for name, sha in manifest["files"].items()
        ):
            raise ValueError(
                "Immutable selection artifact changed; preserve it and create a new version."
            )
        return target  # Any later, separately named human files survive.
    destination = target
    target.parent.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix=".building-", dir=target.parent))
    write_jsonl(target / "selection.jsonl", rows)
    write_jsonl(
        target / "source-selection-audit.jsonl",
        [source_selection_audit(r, texts[r["document_id"]]) for r in rows],
    )
    write_jsonl(target / "review-queue.jsonl", [r for r in rows if r["decision"] == "review"])
    write_jsonl(target / "proposed-dispatch.jsonl", [r for r in rows if r["proposed_dispatch"]])
    write_json(target / "current-identity-audit.json", duplicate_audit)
    write_json(target / "all-identity-cues.json", identity_audit(observations))
    write_json(target / "evaluation-relevance.json", relevance)
    write_json(target / "supplement-proposal.json", supplement)
    write_json(
        target / "token-estimates.json",
        {
            "kind": "planning_only_not_native_usage",
            "documents": estimates,
            "totals_per_single_pass": aggregate_tokens(estimates, models),
        },
    )
    plan = phase_plan(config, rows, packet, supplement, estimate_by_id)
    plan["saved_pricing_reference"] = budget_reference(run, plan)
    plan["phase2"]["pilot_one_pass_input_reservations"] = aggregate_tokens(
        [estimate_by_id[doc] for doc in plan["phase2"]["pilot_document_ids"]], models
    )
    write_json(target / "phase-plan.json", plan)
    with (target / "coverage.csv").open("w") as handle:
        cells = coverage(rows)
        writer = csv.DictWriter(handle, fieldnames=list(cells[0]))
        writer.writeheader()
        writer.writerows(cells)
    summary = make_summary(run, observations, rows, signature, relevance, supplement)
    write_json(target / "report.json", summary)
    write_json(
        target / "manifest.json",
        {
            "selection_signature": signature,
            "version": VERSION,
            "files": {
                p.name: digest(p.read_bytes()) for p in sorted(target.iterdir()) if p.is_file()
            },
            "original_observations_sha256": digest((run / "observations.jsonl").read_bytes()),
            "config_sha256": digest((run / "dashboard-config.json").read_bytes()),
            "gold_sha256": digest((run / "gold-packet.json").read_bytes()),
        },
    )
    target.rename(destination)
    return destination


def make_summary(run, observations, rows, signature, relevance, supplement):
    return {
        "version": VERSION,
        "signature": signature,
        "preparation_run": str(run.resolve()),
        "counts": {
            "all_original_observations": len(observations),
            "historical_observations_untouched": sum(
                r.get("collection_kind") == "historical" for r in observations
            ),
            "current_observations": len(rows),
            "current_candidate_vacancies": len({r["vacancy_id"] for r in rows}),
            "decisions": dict(Counter(r["decision"] for r in rows)),
            "vacancies_by_decision": {
                d: len({r["vacancy_id"] for r in rows if r["decision"] == d})
                for d in ("include", "exclude", "review")
            },
            "proposed_dispatch_documents": sum(r["proposed_dispatch"] for r in rows),
            "repeated_included_captures_retained": sum(
                r["decision"] == "include" and not r["proposed_dispatch"] for r in rows
            ),
            "supplement_proposals": dict(
                Counter(d["proposed_split"] for d in supplement["documents"])
            ),
            "approved_extractions": 0,
            "human_decisions_generated": 0,
            "model_calls": 0,
        },
        "reason_counts": dict(Counter(r["reason"] for r in rows)),
        "include_functions": dict(
            Counter(f for r in rows if r["proposed_dispatch"] for f in r["functions"])
        ),
        "include_seniority": dict(Counter(r["level"] for r in rows if r["proposed_dispatch"])),
        "include_employment": dict(
            Counter(r["employment_type"] for r in rows if r["proposed_dispatch"])
        ),
        "include_remote": dict(
            Counter(r["remote_eligibility"] for r in rows if r["proposed_dispatch"])
        ),
        "completeness": {
            "description": dict(Counter(r["description_completeness"] for r in rows)),
            "selection": "all unreviewed; no measured selection recall or certified absence",
            "disposition_complete": len(rows)
            == sum(r.get("collection_kind") == "current" for r in observations),
            "duty_evidence_missing": [r["document_id"] for r in rows if not r["duty_evidence"]],
            "inventory_claim": "Only already preserved descriptions. No new collection or claim of exhaustive employer inventory.",
        },
        "evaluation_dispositions": {
            split: relevance[split]["dispositions"] for split in ("calibration", "held_out")
        },
        "policy": "Include means proposed domain scope, not approval, extraction readiness, source completeness or inference authorization. No document-count cap applied to Phase 1. Historical scope/identity/human decisions remain separate and unchanged.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.run).resolve())


if __name__ == "__main__":
    main()
