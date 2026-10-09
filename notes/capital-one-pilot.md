# Capital One setup and current credit pilot

Validated October 8, 2026. Local run: `capital-one-credit-pilot`. Human review remains pending. No commit or push was performed.

## Implemented behavior

- The company catalog includes **Capital One** (`capital_one`). Existing runs use their saved settings; no existing scope or budgets were migrated.
- Official current collection uses [Capital One career search](https://www.capitalonecareers.com/search-jobs?k=credit) and the linked HTTPS job-detail pages on `capitalonecareers.com`. The adapter validates search keyword/page metadata and the structured `JobPosting` employer, URL and R-number requisition ID. It saves the complete structured description, original HTML, title, all structured locations, canonical URL, hashes, posting date and capture/retrieval timestamps.
- Search listings expose titles and locations, **not full descriptions**. Titles, explicit level, available description text and known location screen candidates before a detail request. Generic analyst titles returned by a credit search require a body check; this uncertainty is recorded. Full descriptions then determine eligibility. No agency/AI vocabulary influences collection selection.
- Numeric site job IDs deduplicate listing/tracking variants. Requisition ID and content deduplicate repeated descriptions; separate changed versions remain distinct. Each actual saved detail capture, including unparseable or rejected content, consumes the document allowance. Reserved interrupted captures retain their allowance and can resume from cached bytes.
- The same saved request cap covers listing/detail requests and network retries. Requests are reserved durably before dispatch. Cached listing/body responses and screening checkpoints support resume. Unsupported adapters, absent current sources, HTTP failures, parse failures, pagination truncation and limits have explicit outcomes.
- Seniority is explicit. **Senior Business Analyst** and **Senior Associate, Quantitative Analyst** both classify as `senior`; management/executive levels take precedence over “senior.” A senior-only pilot rejects unknown levels. Quantitative degree/orientation language does not identify a modeling function. Capital One's repeated standard business-analyst credit paragraph cannot alone establish a credit-risk function; audit/compliance titles are outside these analytics/modeling tracks.
- Current evidence uses its collector capture date in a separate `CURRENT-*` period. `JobPosting.datePosted`, current capture/retrieval and historical archive capture fields remain independent. A current body never enters historical windows merely because its publication date is old.

## Pilot scope and results

Capital One only; US credit-risk analytics; senior selected explicitly; current sources only; rules extraction; human-reviewed comparison policy; 24 current requests, 10 saved documents, 10 extraction-document slots. The reporting period is `CURRENT-2026-10-08`.

| Stage | Result |
|---|---:|
| Official listing pages collected | 4 |
| Unique listing records screened | 60 |
| Rejected before detail retrieval | 50 |
| Detail attempts / saved complete source descriptions | 10 / 10 |
| Passed completeness, employer, role, seniority and US checks | 6 |
| Rejected after full-description checks | 4 |
| Distinct eligible vacancies / valid eligible extraction documents | 6 / 6 |
| Eligible extracted statement candidates | 78 |
| Pending statement decisions / documents awaiting review | 78 / 6 |
| Human approvals / model calls / historical descriptions | 0 / 0 / 0 |
| Current requests used | 14 / 24 |
| Saved document allowance used | 10 / 10 |

The run stops at `needs_review`. Source collection reports `document_limit`. Resuming was verified to reuse evidence, spend zero additional requests, and save zero additional documents.

Accepted examples:

- **Senior Business Analyst, Credit Loss Forecasting** — R243495; posting-specific credit-loss forecast oversight.
- **Senior Business Analyst - Credit Reporting & Performance** — R248806; Commercial credit-risk reporting and portfolio analytics.
- **Senior Business Analyst - Consumer Credit Risk Management, Forecasting & Reporting** — R999710; Card credit-performance projections and analytics. [Official posting](https://www.capitalonecareers.com/job/mclean/senior-business-analyst-consumer-credit-risk-management-forecasting-and-reporting/1732/100142858832).
- **Senior Business Analyst - Card Loss Forecasting and Allowance**, **Senior Business Analyst - Commercial Risk Management (Strategy & Analytics)**, and **Sr Business Analyst, Commercial Corporate Bank** also passed. Their source-linked classifications remain available in Recover.

Rejected examples:

- **Credit Analyst I** — entry level, rejected before retrieval.
- **Senior Manager, Commercial Credit Review** — management level, rejected before retrieval.
- **Senior Associate, Quantitative Analyst - Commercial Credit Modeling Team** — senior quantitative modeling; outside this analytics pilot, rejected before retrieval. A separate modeling setup selects `quantitative_credit_risk_capital_modeling`. The [official source](https://www.capitalonecareers.com/job/mclean/senior-associate-quantitative-analyst-commercial-credit-modeling-team/1732/91957579552) was independently verified: R236369, McLean, Virginia, United States; stated posting date July 7, 2026. No modeling run was created or mixed into this pilot.
- **Senior Staff Auditor - Credit Risk Management (Hybrid)** — audit function, rejected after collection. Regression now also prevents its retrieval in a fresh pilot.
- **Sr. Business Analyst, Premium Products**, **Sr. Business Analyst, Spend Experiences**, **Sr. Business Analyst - People Strategy & Analytics** — only standard template credit language; insufficient role-specific credit context. Kept as rejected evidence requiring disambiguation.

The first live check exposed the auditor/template false positives. Their raw captures and initial extraction records were retained. Eligibility was corrected, the audit/report/review packet rebuilt, and their extractions excluded from the active review queue and comparisons. Immutable collection decisions remain in `current-selection.jsonl`; dashboard decisions overlay the latest eligibility audit.

## Continue in the app

1. Relaunch **Degent** to load the updated service, then choose **capital-one-credit-pilot** in the **Current research run** dropdown.
2. Choose **Recover** from **Choose a stage**. Open the **6 eligible observations** tile for complete saved descriptions, role/geography reasoning and provenance. **Current screening decisions** opens all 60 title decisions, including pre-retrieval rejections. Use **Search records** to find a title.
3. Choose **Review**, then open **Open the review queue**. Inspect each exact statement beside **Preserved source text**; correct strength, concept/context, notes or exclusions as needed. Use **Save human approval →** only for decisions you have reviewed. To record a whole-description review, return to **Recover → 6 eligible observations**, select the source, and use **Approve reviewed document** after checking the complete description, including omitted requirements. Nothing has been approved automatically.
4. Choose **Report** and click **Build reports** after reviewing. The default policy excludes unreviewed descriptions from assessed denominators. Download report/evidence files there. Re-running collection cannot exceed the saved 10-document limit; broader collection needs a separate bounded run.
In a narrow window, use the bottom **Stage & context**, **Records**, **Workspace**, and **Evidence & actions** controls to reach the corresponding panels.

5. For a separate quantitative pilot: **+ New → scope**: Capital One only and **Quantitative risk / capital** only; **sources**: Current only, Senior only; **periods**: clear historical selections; **budgets**: choose current requests, saved current descriptions (for example 10), extraction-document limit, and **Deterministic rules**. Create the run, then **Run / resume research**. This keeps model-development roles separate from business credit analytics.

## Limits and validation

This is a bounded convenience sample, not employer-wide coverage. Only pages 1–4 of the credit search's 26 pages (386 reported hits) were inspected. The document cap prevented additional underwriting/quantitative searches. Generic analyst titles can still require a request to establish their function because descriptions are absent from listing pages. Unsupported employers remain unsupported; no production Firecrawl or paid-source fallback was added.

Rules produce conservative source-linked candidates; several statements have unclear strength or context. The 78 candidates are not an accuracy claim. Human review is required before interpreting their presence or absence. No reviewed percentages, historical trends or same-vacancy changes are supported by this pilot.

Validation: **98 Python tests**, frontend state/render checks, successful macOS app build, and rendered dashboard checks of catalog, scope, screening decisions and review queue. Regression cases cover title/description ambiguity, Senior Associate precedence, separate tracks, employer/URL/requisition validation, missing/closed bodies, US scope, date separation, tracking/requisition deduplication, cumulative limits, interruption after a final cached body, and pending review.

A SHA-256 baseline check found **all 397 pre-existing run/data files unchanged**, including `runs/test/` settings, evidence and consumed budgets. Only the separate pilot was created under `runs/` and `data/`.

Local evidence: `runs/capital-one-credit-pilot/manifest.json`, `feasibility.json`, `current-selection.jsonl`, `observations.jsonl`, `requirements.jsonl`, `review-packet.md`, `report.json` and `evidence-report.md`; immutable source bytes live under `data/capital-one-credit-pilot/`.
