# Starting plan: historical fintech job-requirement comparisons

Prepared October 7, 2026. Status: researched implementation plan; no collector, model-extraction run, or comparison dataset has been built.

## 1. Decisions and research findings

Build a local, auditable research pipeline using public archives and the existing ChatGPT-authenticated Codex CLI. The first deliverable is a small collection of dated descriptions and defensible requirement comparisons. Historical coverage must be established before expanding the tool.

### Confirmed scope

- US-located or explicitly US-eligible remote jobs; direct employers; entry- and mid-level individual contributors.
- Three separate tracks: fraud analytics/strategy; credit-risk analytics; quantitative credit/risk/capital modeling. Preserve narrower subtypes, including model validation and capital/pricing analytics. Do not pool these tracks into one trend.
- Exclude primarily investigative/operational fraud work, AML casework, generic financial analysis/FP&A, trading/investment quant, managers, internships, and staffing-agency postings. ML infrastructure and embedded ML remain outside this first implementation.
- Existing subscriptions and credits only. No new API billing, credit purchases, commercial datasets, paid hosting, or subscription changes.
- Historical schedule: 2023 H1/H2; 2024 H1/H2; 2025 Q1–Q4; 2026 Q1–Q3. These are eleven completed periods. Preserve October 1–7, 2026 as a separate partial Q4/current observation period for this research cutoff; do not compare it as a completed quarter. Later runs must record their own explicit cutoff.

### What is established

The workspace currently contains two research notes and an empty README. The audit CSV linked through `sandbox:/mnt/data/...` in run-2 is not present. Exported `turn...` citations and narrative examples are not a recovered historical corpus.

Codex CLI 0.159.2 is installed and reports that it is logged in using ChatGPT. Official documentation confirms that `codex exec` reuses saved authentication and can constrain its final response with `--output-schema`. `--json` supplies execution events, not the extraction object. This provides a supported local batch-processing route without a new API key. Included usage is finite; separately billed API access is not part of this plan. [Authentication](https://learn.chatgpt.com/docs/auth), [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [plan usage](https://learn.chatgpt.com/docs/pricing).

Use GPT-6 Luna at High effort as the initial extraction candidate and GPT-6.1 Sol at Medium for selected review cases. This is a testable starting configuration, not a claim that either is already best on fintech descriptions. Official guidance identifies Luna for repeatable extraction. Both model and effort must be overridden per invocation: the machine's existing default is Astra/Ultra, and Luna does not support Ultra. Leave global preferences unchanged. Actual model availability and successful schema output still require the first implementation smoke test. [Model guidance](https://learn.chatgpt.com/docs/models).

Firecrawl is installed and authenticated. Outside the network sandbox, its status check reported 1,031 available credits before the subsequent research scrape. The earlier `fetch failed` result was a sandbox/network limitation. Treat Firecrawl as an optional discovery/live-page fallback using existing credits; direct archive requests remain the historical retrieval path. No large crawl is needed.

### Bounded archive tests performed

| Probe | Observed result | Interpretation |
|---|---|---|
| Common Crawl collection catalogue | Valid JSON including recent and older crawl indexes | Catalogue is reachable; this does not demonstrate job-page coverage |
| Wayback availability endpoint, known Stripe job | HTTP 429 during an initial probe | Rate-limited, not evidence of an absent capture |
| Wayback CDX, exact Stripe Fraud Patterns Analyst URL ending `7913410` | Initial timeout; subsequent request outside sandbox returned HTTP 200 with `[]` | That exact query returned no indexed captures; do not generalize to other URL variants or employer history |
| Common Crawl September 2026 index, same exact Stripe URL | Initial HTTP 503; subsequent request returned HTTP 404 / no captures | No hit for that URL in that selected crawl |
| Common Crawl December 2024 index, Stripe careers/listing prefix | HTTP 404 / no captures | No hit for that queried prefix/crawl; historical paths and ATS hosts still need discovery |
| Current public Greenhouse boards for Affirm, Upstart, SoFi | Valid posting feeds in read-only checks | Useful identifiers and present-day seeds; no historical body established |

No complete historical job body was recovered in these planning probes. Coverage of the requested role groups and years remains unmeasured. Connectivity is sufficiently established to implement a controlled discovery experiment.

### Corrections to carry forward from run-2

- A current description plus an older publication-date assertion does not prove that wording existed on the older date. The Stripe example is a current extraction seed until a dated historical body is recovered.
- Preserve every capture observation even when content is identical. Applying CDX `collapse=digest` across reporting periods can remove evidence that unchanged text was observed in more than one period. Deduplicate stored bytes separately. [Wayback CDX documentation](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server).
- For small Common Crawl retrievals, use index offsets and HTTP byte ranges with `warcio`; a bulk Rust download framework is unnecessary. An index hit must be followed by payload validation. [Common Crawl guide](https://commoncrawl.org/get-started), [index documentation](https://commoncrawl.org/cdxj-index), [warcio](https://github.com/webrecorder/warcio).
- Preserve ATS field semantics. Greenhouse distinguishes posting IDs from underlying job IDs; Ashby's publication field means last published. Current feeds are not historical version APIs. [Greenhouse](https://docs.greenhouse.io/job-board.html), [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api).
- Earlier LLM benchmark findings cannot select the best current model. Use a target-domain evaluation. The 2024 skill-extraction comparison and the 2026 novelty-detection work support examining extraction errors and filtering candidates; neither validates this proposed fintech temporal pipeline. [NLP4HR 2024](https://aclanthology.org/2024.nlp4hr-1.3/), [LREC 2026](https://aclanthology.org/2026.lrec-1.611/).

## 2. First implementation: discover evidence before scaling extraction

### Candidate employers

| Candidate group | Employers | Initial role emphasis |
|---|---|---|
| Payments/financial infrastructure | Stripe, PayPal, Block | Fraud analytics/strategy; relevant quantitative risk roles where verified |
| Lending/consumer finance | Affirm, Upstart, SoFi | Credit-risk analytics; quantitative credit, model-risk, and capital analytics |

These are discovery candidates, not a validated peer cohort. Keep Block brands and distinct subsidiaries identifiable. Always compare within an employer first; only compare employers with compatible business and role subtypes. Company sizes differ and must remain visible.

The current Affirm Quantitative Analyst II description demonstrates an appropriate capital-analytics subtype, but not historical availability. Conversely, a title such as Fraud Analyst can describe investigation rather than analytics, and a pooled Analyst advertisement can cover several unrelated teams. Classify the actual responsibilities. [Affirm example](https://job-boards.greenhouse.io/affirm/jobs/7815952003).

### Stack and local interfaces

Use Python 3.11+ in a project-specific virtual environment, `requests`, Beautiful Soup, `warcio`, Pydantic, and pytest. Use the standard library for hashing, dates, CSV, JSONL, and subprocess execution. Avoid model training, embeddings, a vector database, a database server, and a web application in v1.

Implement a small `python -m degent` CLI with these operations:

| Operation | Responsibility |
|---|---|
| `doctor` | Check network paths, Codex ChatGPT login, configuration and optional crawler access; do not expose credentials |
| `discover` | Read employer/period configuration, inventory archive records, save queries, pagination state and outcomes |
| `fetch` | Retrieve a bounded set of candidate bodies, preserve provenance, parse text and record usability/exclusions |
| `extract` | Produce rule candidates and optional Codex extraction, validate evidence, cache results and create review records |
| `report` | Validate accepted inputs, construct comparisons and export coverage, requirements and an evidence report |

All operations accept a configuration path and run ID, resume from checkpoints, and report incomplete work honestly. The first implementation has no hosted API. Add a small TOML configuration for employer paths, explicit dates, role rules, models, and request limits; JSON Schema defines model output. Raw data and run outputs remain local and ignored by Git; code, configuration, prompts, schemas and synthetic tests can be versioned.

### Discovery and retrieval policy

1. Find historical employer career namespaces and ATS paths from archived listing pages, existing research seeds, official career links, and public search. Do not seed exclusively from jobs still live today. Preserve redirects and historical ATS migrations.
2. Query Wayback CDX for the configured dates and namespaces. Retain timestamp-level metadata and pagination checkpoints. Discover Common Crawl collections dynamically; query all collections intersecting the selected periods as the request budget allows.
3. Start with a discovery cap of 300 index/listing requests across six employers, with fair allocation across employers and years. Save unfinished queries and distinguish an incomplete inventory from an empty result.
4. Limit the first batch to 150 unique historical body retrieval attempts, initially 25 per employer. Within each employer, reserve five attempts for 2023, five for 2024, five for 2025, five for 2026, and five for potential paired captures. Reassign an unused reservation only after recording why it could not be used. Count archived listing/API bodies in this budget too.
5. Retrieve at most one request at a time per archive host, with at least two seconds between requests. Use a 30-second timeout and at most two retries for transient failures; honor `Retry-After`. Checkpoint and suspend a rate-limited host if its requested wait exceeds a minute rather than repeatedly polling it. Log retries separately from unique body attempts.
6. Rank candidates by verified role/date/identity evidence and opportunities for comparable pairs, never by whether they appear to contain an interesting change. For opaque numeric URLs, use archived lists or ATS responses to discover titles; a URL-only keyword filter would miss relevant jobs.
7. Validate successful bodies against the expected target URL, employer, title, identifiers and substantive requirements/responsibilities. Reject shells, closed-job notices, consent pages and truncated pages as complete descriptions. Preserve the failure reason.
8. For Common Crawl, request the indexed WARC byte range and validate the response record and embedded HTTP response. For Wayback, retain the actual replay/capture timestamp, including any redirect to a different capture. Never populate an archived shell with today's live API response and call it historical.

Use Firecrawl only when ordinary discovery or live-page extraction is insufficient. Set an initial ceiling of 20 existing credits, check reported usage, avoid features with unknown cost, and stop rather than buy credits. It cannot recover material that was never archived.

If no defensible temporal comparison is found within the initial limits, finish the coverage report and extraction fixtures. State the remaining deficit and the next targeted query; do not silently enlarge the corpus, switch occupations, or purchase data. Current snapshots can be saved as clearly dated current observations, but recurring scheduling is a later step.

## 3. AI extraction, evidence records and comparison rules

### Minimum data records

Use immutable source files plus schema-validated JSONL collections. Keep these concepts separate even without separate database tables:

- Vacancy: employer/brand, source posting and requisition IDs, canonical URL, role/subtype, title/level, location, US eligibility, work arrangement and classification evidence.
- Observation/document: original and archive URLs or WARC record, capture timestamp, publication-date assertion and its source, retrieval timestamp, first observation by this collector, raw/clean hashes, parser version, body completeness and retrieval status. Do not backdate this collector's first-seen time to an archive capture.
- Requirement: category, original phrase, normalized concept, required/preferred/unclear status, explicit versus inferred, qualifier, scoped experience values, alternatives/conditions, evidence offsets and extraction provenance.
- Review/change: compared observation IDs, relationship type, before/after evidence, change type, rationale, model/validation status, corrections and reviewer type.

Hashing identical documents does not establish that two different requisitions are the same vacancy. Exact IDs within the employer/source namespace are the strongest identity anchor; unsupported fuzzy matches remain review candidates.

### Codex workflow

1. Parse descriptions into labelled sections and numbered sentences/bullets. Apply a small, versioned alias map and rules for explicit years, education and qualification cues. Preserve original wording and unknown concepts.
2. Use the existing Codex login for one document per fresh, sequential extraction call. Start with 10 varied documents and cap the initial experiment at 30 unique documents. Track review and retry calls separately. No API-key fallback or top-up.
3. Select `gpt-6-luna` with `model_reasoning_effort="high"` per invocation; use `--output-schema`, final-output capture and `--ephemeral`. Supply the document through stdin using subprocess argument arrays. Keep execution events separate from the final JSON. Do not alter global Codex settings. [Supported execution interface](https://learn.chatgpt.com/docs/non-interactive-mode).
4. Restrict the extraction run to source analysis: read-only filesystem policy, web access disabled, and no enabled connector/MCP actions or shell tools where supported by the installed configuration. Validate the effective configuration before use. Treat all source text as untrusted data and reject runs that invoke unexpected tools. If this isolation cannot be configured, use supervised extraction in the Codex app for the small initial set instead of unattended runs.
5. Validate document IDs, field types, exact evidence phrases and section references independently. Compute offsets in Python rather than trusting generated numbers. Preserve logic such as “Python or R,” degree/experience substitutions, negation and experience scope. Schema-valid output alone is insufficient.
6. Permit one validation-repair attempt; unresolved outputs become review-required. Use `gpt-6.1-sol`/Medium only for ambiguous cases, disagreements or semantic paraphrases, with a maximum of 10 such review calls in the initial batch. If a selected model is unavailable, continue with the configured available review model; if neither is available, preserve a rules-only review packet.
7. Cache accepted results by input-text hash, parser/prompt/schema/alias versions and model configuration. Reuse extraction for identical text while retaining distinct observations. Record requested/resolved model identifiers when available and run timestamps. Usage-limit failures checkpoint without retry loops or new billing.
8. Review the first 10 descriptions and every surfaced material change against the full source. Distinguish deterministic validation, agent review and human review. Prepare a compact source-and-output packet for the user's inspection; do not label AI-only review as human verification or claim measured human-level accuracy.

### Comparison behavior

Within a role track, match principally on employer, occupational function, explicit level, specialization, geography and work arrangement. Do not match primarily on skills or required years. Keep unknown seniority unknown and out of primary equivalent-level cohorts. Preserve entry and mid-level groups separately.

Produce two outputs:

1. Same-vacancy version changes: exact dated descriptions, showing newly mentioned/no-longer-stated concepts, required/preferred changes, scoped experience thresholds, specificity and responsibility changes. The edit occurred somewhere between captures; do not invent an exact edit date.
2. Comparable-vacancy descriptions: observed requirement shares among distinct openings, separately by employer, subtype and level. A different opening is not automatically a successor to an earlier job.

For each reporting period, choose the latest complete capture of each eligible vacancy within that period. Count a vacancy at most once per period; retain all captures separately for version comparisons. Long-lived vacancies may recur across periods, so show overlap and do not treat samples as independent.

The denominator is unique eligible vacancies with complete descriptions observed in the archive sample, not every job the employer advertised. Show X/N and percentage-point differences. Missing periods remain unavailable; do not silently interpolate, treat missingness as zero, or bridge gaps as adjacent-period changes.

For half-year/quarter comparisons, flag unequal observation windows and add a comparison using the half-year's last three months when evidence allows. Do not compare raw opening counts as demand growth. Keep employer results separate; only summarize shared directions across compatible, adequately populated company cohorts.

Allow exploratory cohort percentages only with at least five eligible distinct vacancies per side; this is a display gate, not proof of statistical reliability or representative coverage. Smaller groups remain individual examples. Check sensitivity to strict matching and duplicate removal; same-period random splits diagnose sampling variability. Exclude inferred skills from primary explicit-requirement counts.

## 4. Deliverables, tests and completion gates

### Implementation sequence

| Milestone | Concrete output | Gate |
|---|---|---|
| A. Retrieval foundation | Environment checks, source/query manifest, resumable archive inventory and employer-by-period coverage matrix | Distinguish reachable/blocked/empty/uninspected; no model needed |
| B. Historical evidence | Preserved source bodies and a first corpus targeting 10–20 eligible observations | At least one defensible dated pair enables a real comparison; unchanged text is a valid result |
| C. Extraction baseline | Rule candidates, bounded Codex outputs, evidence validation and a first-10 review packet | Exact quote validation passes; unresolved semantics remain excluded/review-required |
| D. Comparison report | Coverage table, requirements export, ideally three real comparisons, and limitations | Every material change links to both source observations; clearly distinguish same-vacancy and comparable-distinct cases |

Do not require all employers or tracks to have data before demonstrating the method. Do not invent a positive change or a complete 2023–2026 panel to meet the target. The report must include all eleven completed periods and explicitly show their inspection/coverage status.

The output bundle contains a Markdown evidence report, JSONL records, CSV exports, raw/clean document files and a run manifest. A report with isolated examples or unresolved coverage is a valid experiment outcome if its limitations are explicit; it is not a successful multi-year trend dataset.

### Acceptance tests

- Listing/apply mirrors with the same verified vacancy ID count once; distinct IDs sharing text are not automatically merged.
- Identical text captured in different periods remains observed in each period.
- Publication date, archive capture, retrieval and collector first-seen time never substitute for one another.
- WARC mismatch, wrong capture replay, JS shell and closed-job content cannot pass as complete historical descriptions.
- Every accepted evidence quotation matches the preserved clean text exactly; parser/source hashes connect it to the original body.
- Required/preferred changes, scoped years, alternatives, negation and education substitutions retain their meaning.
- Reordering, punctuation and known aliases produce no material changes. Hand-labelled paraphrase fixtures have no false material changes before reporting.
- Missing earlier evidence returns comparison unavailable rather than newly added. Unknown or incompatible levels/subtypes are excluded from primary matching.
- A second run reuses retrieval/extraction caches without losing dated observations or duplicating rows.
- Simulated HTTP 429/503, timeouts, invalid model output and subscription exhaustion preserve checkpoints and produce explicit statuses.
- Completed versus partial periods, half-year boundaries, quarter boundaries and the six-to-three-month transition are tested.
- The first review packet records actual corrections and unresolved cases. No accuracy claim is inferred from a tiny or AI-labelled sample.

### Starting action and remaining uncertainty

The first implementation task is milestone A plus a bounded milestone-B retrieval, not a dashboard. Implement the archive inventory and evidence preservation, then demonstrate one real comparison before expanding model processing. Research has reduced uncertainty about supported tools and access; it has not established adequate historical coverage or extraction accuracy.

No new paid services are required for this starting experiment. Completion time depends mainly on archive coverage, rate limits and available subscription usage; the implementation must remain resumable rather than promising a fixed completion date.
