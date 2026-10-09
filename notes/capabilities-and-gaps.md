# What Degent can run, and what is still missing

Implementation review: October 8, 2026. This follows the [research design](../deep-research/skills-and-workflows-plan.md). The earlier document remains a record of the planning stage; this file describes the implementation now.

**The core research workflow is equipped for a bounded pilot. The full product vision is not finished.** The largest unresolved issue is sufficient dated historical descriptions. More extraction code cannot supply missing source material.

## Company collection integration, October 8

The [company collection guide](company-collection.md) records the new workflow, CLI commands,
architecture, accounting limits, migration and remaining gaps. The [initial plan](company-collection-plan.md)
records the integration decisions. This update adds persistent manual companies and paginated
name/alias/domain lookup; multiple sources and separate bounded verification; frozen run scope and
explicit continuations; inventory-driven sessions with optional document limits; round-robin progress,
checkpoints and recorded budget extensions; immutable full-hash raw/text snapshots and transactional
capture identities; cross-run automatic extraction reuse with fresh review state; and Workday list/detail.
Collection is independent of extraction/model caps. Existing archive/import/review/comparison gates remain.

Stripe/Block Greenhouse and PayPal Workday are configured as **unverified leads**, with separate
[Jobseek attribution/data/code notices](../notices/README.md). Upstart is still unsupported. No live
source collection, source-verification pilot, or model call was performed for this integration. The
new verification path requires provider availability plus ownership evidence; a catalog lead is not
proof. Successful fixture collection does not establish current availability or historical coverage.

Validation now includes **135 Python tests**, frontend state/render checks, rendered narrow/desktop
browser checks using synthetic local data, and a successful macOS app build. New cases cover 73
postings across three chunks, request/time/storage pauses, cumulative extensions, Workday later-page
failure/resume, exact source bytes, changed/unchanged/reverted snapshots, interruption idempotency,
cache/review invalidation, and HTTP/CLI search/addition/continuation.

Existing saved runs are deliberately not backfilled or rewritten. They retain legacy limits and
checkpoints. Their uncertain first-observed provenance stays uncertain. Use a new scope continuation
to opt into the session collector. The storage allowance meters evidence payloads rather than OS
filesystem overhead. Redirecting source sites, complex Workday inventories/facets and generic sites
may still need additional verified source records or adapters. Automatic closure, scheduling, bulk
catalog ingestion, arbitrary crawling and cross-run report pooling remain deferred.

## Existing, added, and unfinished

| Capability from the design | What already existed | What is implemented now | Remaining limit |
|---|---|---|---|
| Local research app | Python pipeline, seven-stage interface, Mac launcher | Reused all three | Still requires this checkout and Python; not a standalone distributed app |
| Research setup | Six employers, three fintech role tracks, saved periods | Persistent company library seeded with seven employers; paginated search/manual additions, multiple source selection/verification, five seniority options, separate collection/extraction budgets and frozen CLI/UI settings | Free-text roles, arbitrary geography, date-range/granularity controls and model selection in the UI are unfinished |
| Company and related-role suggestions | Configured company groups | Small catalog-based suggestions through the CLI/API | No researched AI suggestion engine or accept/reject suggestion screen; catalog membership is not verified equivalence |
| Current descriptions | Archive collectors only | Greenhouse/Ashby, official Capital One and bounded Workday list/detail adapters; Stripe/Block/PayPal leads; exact raw responses and resumable inventory sessions | Stripe, Block and PayPal need live verification. Upstart's old Greenhouse board failed; its current career-site adapter is still needed |
| Historical descriptions | Wayback and Common Crawl discovery/retrieval | Fixed conventional CDX JSON parsing; added a bounded failure/coverage audit; preserved original source gates | Targeted exact-posting/source-map recovery, more employer adapters, and adequate historical samples remain necessary |
| Imported evidence | No import workflow | JSON file or pasted JSON with full text, source URL, company, title, location, and optional attested capture date | PDF, DOCX, saved HTML and drag-and-drop extraction are not implemented |
| People skills and work habits | Mostly technical phrase matching | Versioned 60-concept guide spanning technical/domain skills, communication, collaboration, initiative, ownership, independent work, judgment and related habits | Human-labeled real descriptions are needed to measure accuracy and expand the guide |
| Practical AI use | Limited AI/ML vocabulary | Separate everyday AI use, AI workflows, agents, prompting, checking output, adoption, governance, named tools, and model-building concepts | Broad claims such as “AI fluency” still need interpretation; a tool mention alone does not prove a workflow |
| Meaning and context | Required/preferred/unclear | Explicitly not required; separate applicant qualifications, job tasks, company expectations, company descriptions and hiring notices | Rules propose candidates; they are not a complete language-understanding system |
| AI extraction | Optional existing-login Codex CLI | Full-description prompt, structured output, quote validation, known-category normalization, raw model-output log, durable call reservations | One live source passed the structural/evidence checks. Quality across employers and roles is not established |
| Human review | Requirement status and notes | Correct concept and context, exclude unsupported statements, approve a whole reviewed document including an empty result | No dedicated editor for years/alternatives/task fields or role/geography overrides; unknown concepts require a vocabulary update and re-extraction |
| Source/version integrity | Hashes and append-only decisions | Stable whole-document review IDs; invalidation on re-extraction; report-time source checks; method/version compatibility gates | Cross-run automatic cache now exists; legacy runs are not automatically backfilled/migrated. Human approvals remain local |
| Comparisons | Same-vacancy differences and separate-vacancy shares | Required-only, preferred-only, combined and explicitly-stated views; exact wording; configured pairs/triples within the same posting; assessed/unresolved counts | Pair/triple selection currently lives in configuration. No statistical inference or market-wide estimates |
| Time and provenance | Archive capture versus retrieval dates | Current capture clock, original feed dates, attested import dates and undated imports kept distinct; UTC ordering | Supplied import dates are user claims, not independent verification |
| Stop, resume, limits | Single worker, file lock, saved downloads, stop button | One `run` command; per-stage activity; cached current feeds; cumulative import/current-document caps; reserved network/model attempts | New current sessions enforce elapsed-time and byte/payload budgets with recorded extensions and cross-run snapshots. No token/dollar meter or scheduler. Interrupted reservations consume allowance |
| Exports and quality checks | JSON/CSV/Markdown reports | Combination/wording exports, feasibility report, review log, activity log, model-output log, rules evaluation command | Codex accuracy evaluation against a held-out real dataset is unfinished |

## The combination the agent now uses

1. Use official public feeds for available current descriptions and the existing archive collectors for dated historical records. Accept saved text through the import form.
2. Save original records and clean text with source identifiers and dates. Do not select postings because they contain AI or high-agency words; that would bias the question being measured.
3. Check whether the description is substantive and belongs to the selected role and US location. Keep excluded observations visible.
4. Match known phrases cheaply, or use the existing ChatGPT-authenticated Codex CLI to read the complete description. AI mode does not silently accept a rule result when the model fails.
5. Check the structure and exact quotations. Normalize the category of a known concept using the local guide. Keep unfamiliar wording unresolved. A second model read is bounded and optional; it does not constitute human approval.
6. Let the user review statements beside their source. A document with no statements needs an explicit document-level decision before it can serve as reviewed absence evidence.
7. Count comparable descriptions and export the results with their coverage limits.

This is a fixed workflow with AI used for reading and interpretation. It does not autonomously add companies, buy data, or replace missing history with invented examples.

## How people skills and AI wording are handled

| Source wording | Candidate meaning | What is not inferred automatically |
|---|---|---|
| “high agency,” “self-starter,” “takes initiative” | Initiative | Ownership, independence, persistence and judgment are separate concepts |
| “owns outcomes,” “accountability” | Ownership | Stock or equity ownership is not a work habit |
| “work independently without supervision” | Independent work | “Without” does not negate this skill |
| “comfortable with AI tools” | AI familiarity plus an explicit AI-tool mention | No named workflow or engineering ability is invented |
| “agentic workflows” | AI agents/workflows wording | No particular vendor or implementation is assumed |
| “we use AI to screen applications” | Hiring-process context | This does not count as an applicant AI qualification |
| “we build AI tools for customers” | Company/product description | This does not count as employee AI proficiency |
| “Python preferred” versus “no Python experience required” | Preferred versus explicitly not required | Neither is counted as required |

Each row keeps its original phrase and supporting excerpt. The normal comparison requires human-reviewed descriptions. Automated-only comparisons remain an explicitly labeled exploratory option.

For a given concept, the denominator is the number of distinct postings that can actually be assessed under the selected review policy. Pending reviews, missing extraction, and relevant unresolved wording are not treated as absence. Unmapped education wording does not automatically make an unrelated SQL judgment unknowable. Five assessed postings is only a display threshold, not evidence of a representative sample.

Within a period, use the latest eligible saved version of each posting. Do not fall back to an older extracted version when its latest version lacks extraction. Keep employers, brands, role subtype, seniority, location status, employment type and date-evidence basis separate. Differences require completed, equal-duration windows and compatible extraction methods. Partial periods remain visible without full-period change claims.

## What the live checks established

- The earlier saved historical run had **150 body attempts: 47 failed and 103 rejected; zero complete, eligible descriptions**. Its exclusions include career landing pages, JavaScript shells and unrelated roles. This is a result for that bounded run, not proof that historical descriptions do not exist.
- The current-feed check made **three requests**. Affirm and SoFi returned feeds; the old Upstart board returned HTTP 404. Three role-relevant descriptions were saved from Affirm. After location checks, **one US posting qualified**; the other two were UK and Canada postings.
- The usable source was Affirm's [Quantitative Analyst II posting](https://job-boards.greenhouse.io/affirm/jobs/7815952003), preserved locally with a collector capture date of October 7, 2026 in the configured Chicago timezone. The current page may subsequently change or disappear; the saved source remains the evidence for this check.
- The live model checks found and fixed an invalid response schema and a category-normalization problem. The corrected extraction produced **34 statements with exact-source quotations** in `runs/current-model-validation/requirements.jsonl`. They remain **unapproved by a human**. The optional second-model review was not completed because the small validation run reached its call cap. Failed attempts remain in the earlier validation run.
- The comparison report contains **no supported percentages or trends** for this one-description, unreviewed sample.
- The earlier validation included **98 Python tests** (the company integration brings this to 135), plus frontend state/render checks and a successful Mac app build. The 12 labeled synthetic meaning cases match all 14 expected labels. Synthetic results are regression checks, not measured real-world extraction accuracy.
- Rendered browser checks cover source/seniority setup, comparison settings and the live review workspace. Long source text and forms remain reachable in narrow windows. This is not a complete accessibility or cross-browser certification.

Capital One validation: 60 titles screened, 10 saved descriptions, 6 eligible senior US credit analytics descriptions and 78 pending rule candidates; 14/24 requests, 10/10 documents, no approvals. See [the complete pilot record](capital-one-pilot.md).

Local evidence: `runs/capital-one-credit-pilot/`, `runs/current-capabilities-check/`, `runs/current-model-validation/`, and `runs/synthetic-meaning-check/`. These folders are ignored by Git, as are downloaded descriptions.

## What to implement or validate next, in order

| Priority | Concrete work | Acceptance condition |
|---|---|---|
| 1. Historical evidence feasibility | Pick two employers, one role subtype/level, and two equal completed periods. Build exact posting URL/source maps; test ATS detail/API archive routes and inspect a bounded rejection sample | Every accepted historical record has a complete body and dated provenance. Report retrieved/eligible/distinct counts per company-period. If insufficient, narrow scope or keep the result current-only |
| 2. Real extraction evaluation | Human-label 10 development and 20 held-out descriptions, collected without AI/agency keyword selection. Include negative and ambiguous cases. Compare candidate models on the same labels | Measure concept, status, context and missed-statement errors; agree thresholds before selecting a configuration. Use a separately bounded evaluation run |
| 3. Complete current-source coverage | Add verified sources for Stripe, PayPal, Block and Upstart; distinguish a provider outage from a board with zero relevant jobs | A complete description passes the same source gates for each supported company; unsupported companies are explicit |
| 4. Finish review controls | Edit experience/alternatives/task fields; resolve role, level and geography; attach reviewer identity; add a reviewed unknown phrase to the guide with examples | Corrections preserve originals, invalidate dependent results when needed, and can be reproduced after restart |
| 5. Finish configurable research | Company lookup/manual addition and frozen scope continuations now exist. Remaining: free-text roles, researched related-role suggestions, custom dates and non-US geography | No expansion happens without an explicit saved scope. Related titles remain distinguishable |
| 6. Complete operational limits | Current sessions now have runtime/byte/payload bounds, recovery, per-source progress and recorded collection/model budget extensions. Remaining: archive-wide runtime and observable token/cost accounting | Interrupt/restart tests prove no lost completed work, no uncontrolled retries, and an accurate reason for each stop |
| 7. Extend future history | Cross-run content/artifact reuse and manual refresh are implemented. Remaining: explicit scheduling, richer import formats and cross-run report pooling | New snapshots retain independent observation times; unchanged content avoids repeat model work. Scheduling remains an explicit user choice |
| 8. Broader sources and distribution | Evaluate licensed history only if coverage needs it; add any paid/crawler adapter with enforced credits; package the app for distribution if needed | Prove access, coverage, costs and failure handling before adding the source to normal runs |

A Firecrawl credit limit existed in the configuration, but **there is no production Firecrawl collection adapter in this app**. It must not be presented as an enforced spending control. The implementation uses public ATS/archives and the existing Codex login; there is no paid API fallback.

## Running it now

In the app: create a new run, select the companies/role tracks/seniority/periods, choose sources and the extraction method, then use **Run / resume research**. For a current pilot, existing configured sources include Capital One, Affirm and SoFi. Verify new sources separately before selecting their frozen configuration. See [Capital One pilot results and exact continuation steps](capital-one-pilot.md). Inspect the source coverage before expecting any comparison. New runs add a current partial observation period when necessary. Saved runs retain their original scope.

For the command line, copy `config/research.toml` to a pilot config, set `sources = ["current"]`, choose the employers/levels, and reduce limits as appropriate. Then:

```sh
.venv/bin/python -m degent --config config/my-pilot.toml --run-id my-current-pilot run --codex
# Review in the app, then rebuild without another model call:
.venv/bin/python -m degent --run-id my-current-pilot report
```

Omit `--codex` for rule candidates. Existing runs use their frozen `dashboard-config.json`; changing repository defaults does not raise legacy budgets. New current sessions support recorded explicit budget extensions.

An import is a JSON list containing `employer_id`, `title`, `location`, `source_url`, and `text`. Add `captured_at` with a timezone and `capture_date_source` only when there is an actual saved capture date. Undated imports can be read and reviewed, but are excluded from historical comparisons. Imports must fit the 5 MB file limit and the run's cumulative document allowance. Direct CLI use:

```sh
.venv/bin/python -m degent --run-id imported-pilot import /absolute/path/postings.json
.venv/bin/python -m degent --run-id imported-pilot extract --codex
.venv/bin/python -m degent --run-id imported-pilot report
.venv/bin/python -m degent --run-id meaning-check evaluate tests/fixtures/meaning-cases.json
```

## Sources behind these choices

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html): public job-board retrieval, posting IDs and content. Feed publication/update fields remain distinct from this collector's own clock.
- [Ashby public job posting API](https://developers.ashbyhq.com/docs/public-job-posting-api): structured public postings; `publishedAt` is treated as the last-published timestamp, not proof of original publication.
- [Internet Archive CDX documentation](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server): archived capture discovery and pagination; an index row still needs a retrievable body.
- [Common Crawl getting started](https://commoncrawl.org/get-started): crawl indexes and stored web records; available crawl coverage determines what can be recovered.
- [Upstart's current careers page](https://careers.upstart.com/jobs/search): verified replacement location for its current listings. This is a source discovery finding; the app adapter is not implemented.
- [O*NET work styles](https://www.onetonline.org/find/descriptor/browse/1.D): a useful reference for separating initiative, independence, persistence and other work habits. It does not validate this app's classifier.
- [Codex noninteractive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [authentication](https://learn.chatgpt.com/docs/auth), and [plan limits](https://learn.chatgpt.com/docs/pricing): basis for the existing-login CLI route. The completed local call verifies this route worked for the tested request; it does not establish unlimited access.

The fuller research rationale and additional primary examples remain in the linked design document. Implementation status is supported by the source files and run evidence above, not by vendor documentation alone.
