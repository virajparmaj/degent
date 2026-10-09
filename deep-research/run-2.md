# Feasibility study for a US historical job-requirement comparison tool

## Feasibility verdict and pilot scope

**Verdict: proceed with a narrowly scoped retrieval-and-comparison prototype, but do not yet proceed to a three-company “trend” product for Fraud Analyst / Fraud Strategy Analyst.** The analytical part of the idea is feasible: there are good methods for distinguishing vacancies, normalising roles, extracting explicit requirements, preserving supporting text, and identifying meaningful semantic changes. The immediate blocker is **historical data density and provenance**, not NLP. In the July–September 2026 window, I could locate several genuine fraud-strategy/analytics vacancies and some stale or historical copies, but I could not establish the requested **5–10 usable, deduplicated postings per company in both July and September for three comparable payments-focused fintech employers**. It would therefore be misleading to publish a monthly cross-company trend from the evidence currently recovered. Relevant labour-market research likewise shows that longitudinal job-posting analysis is valuable, but the strongest studies have generally relied on proprietary near-universe vacancy datasets rather than reconstructing a few employers retrospectively from the open web. citeturn21search6turn20search1

The exact requested observation window is:

| Purpose | Exact dates |
|---|---|
| First comparison month | **1–31 July 2026** |
| Supporting month | **1–31 August 2026** |
| Last comparison month | **1–30 September 2026** |
| Research date | **7 October 2026** |

The provisional role definition should remain **fraud analytics / fraud strategy individual contributors**, not general “fraud”. An included job should primarily analyse fraud patterns, devise or tune fraud strategies/rules/decisioning, quantify fraud performance, or use data to drive fraud controls. Jobs primarily doing case investigation, manual alert review, chargebacks, customer disputes, AML investigation or fraud operations should be separately classified rather than silently pooled. That distinction is visible in the market itself: Stripe's current US Fraud Patterns Analyst is explicitly responsible for advanced analysis and fraud rulesets, while fraud-investigator roles can have quite different operational responsibilities. citeturn22search9turn17search22

I also **would not switch to a different role group merely because it is easier to scrape**. I found indications that applied ML and payment-fraud data-science roles are more numerous at some employers—for example, Airwallex's current Ashby board exposes payment-fraud data-science positions—but I did not obtain enough July–September historical evidence to demonstrate that a three-company Applied AI/ML cohort would pass the requested coverage threshold. A role switch is therefore not evidence-backed yet. citeturn18search5

The smallest defensible adjustment is instead to **keep US + fintech + fraud analytics, but widen the retrospective search from three months to twelve months if the archive tests below remain sparse**. That preserves the intended occupational question rather than changing it to fit available data. Simultaneously, begin a forward snapshot collector now; current ATS APIs are useful for that future panel but are not a substitute for July–September historical evidence. Greenhouse's Job Board API exposes published jobs, and Ashby's public postings API exposes currently published openings; neither is documented as a historical vacancy archive. citeturn1search2turn1search3turn1search22

One requested input was unavailable: the “attached initial research” was not exposed to the file-retrieval tools in this session. I therefore treated the landscape review as background described by your prompt, but independently verified the claims needed for this feasibility study rather than assuming the attachment's conclusions.

## Historical-source tests and recommended source order

### What was tested live

Wayback was **not dismissed without testing**. I attempted representative access in several forms: CDX capture-index queries, the Wayback availability interface, and archived-resource URLs. One explicit CDX test targeted a Stripe Greenhouse URL (`boards.greenhouse.io/stripe/jobs/8261213`); another test targeted an ATS-hosted Ashby URL. Four representative Wayback endpoint/page attempts produced **zero retrievable archive payloads**, because this research environment itself rejected access to the `web.archive.org`/archive endpoints before a CDX or snapshot response was returned. An independent command-line network attempt also failed DNS resolution for `web.archive.org`. This is an **environment-blocked test, not evidence that Wayback lacks those captures**.

That distinction matters because the Internet Archive's own documentation says the CDX interface is specifically an index of URL captures and supports filtering by URL and date; Wayback's APIs can be used to discover whether a URL is stored and to identify snapshots. The open-source CDX server similarly describes indexing captures by URL and timestamp. citeturn1search0turn1search16turn1search24

Common Crawl received the same treatment. I attempted representative index access through the browser environment and direct network access to `index.commoncrawl.org`; **zero index payloads were returned because this environment blocked/directly failed that network path**. Again, that is not a negative coverage result. Common Crawl officially publishes searchable crawl indices and WARC data, and the relevant 2026 monthly crawls definitely exist: `CC-MAIN-2026-30` was crawled in July, `CC-MAIN-2026-34` from 7–20 August, and `CC-MAIN-2026-39` from 4–17 September. citeturn1search1turn1search5turn6search8turn10view0turn10view1

Those release dates make Common Crawl unusually valuable for this pilot: unlike an annual historical dataset, it gives an independent crawl corresponding to each requested month. Common Crawl says its corpus spans more than 300 billion pages over roughly 15 years and adds billions of pages per monthly crawl, but inclusion of a domain does **not** imply that a particular vacancy URL was captured. citeturn8search15

### Ranked recommendation

| Rank | Historical source | Evidence and what it contributes | Key limitation for this pilot |
|---|---|---|---|
| **First** | **Wayback CDX + archived original employer/ATS pages** | Best source for reconstructing a known job URL across multiple timestamps. CDX provides capture discovery; raw snapshots can preserve employer-origin content. Official API/CDX capability is documented. citeturn1search0turn1search24 | Actual payload retrieval was blocked in this research environment, so target-employer coverage is **not yet measured**. Dynamic pages may archive a shell without the JD. |
| **Second** | **Common Crawl index + WARC** | Independent monthly July/August/September 2026 crawls; useful for exact URLs, career-site wildcards and sometimes JSON/API responses. citeturn6search8turn10view0turn10view1 | Crawl inclusion is opportunistic; JavaScript-rendered content can be absent. A hit must be followed to the WARC record and inspected. |
| **Third** | **Employer page + indexed/stale copies on the public web** | The live tests recovered a complete direct Stripe JD and historical/stale third-party records for SoFi, Earnest and Ramp. citeturn22search9turn19search12turn19search24turn22search11 | Search indexing is not a controlled archive. Dates and provenance vary, and duplicates are common. |
| **Fourth** | **Commercial historical job-posting providers** | TheirStack says it has aggregated millions of jobs monthly since 2021; People Data Labs describes daily crawling/lifecycle information; other vendors advertise historical job feeds. These are potentially much faster fallback routes. citeturn1search31turn8search18turn8search21 | Those are **vendor claims**, not independently verified here; licences, redistribution rights, historical field definitions and pricing must be checked before use. |
| **Fifth** | **Current ATS APIs + collector started now** | Greenhouse and Ashby provide straightforward structured current-job feeds, excellent for IDs, URLs and raw descriptions going forward. citeturn1search2turn1search3 | Does **not** reconstruct July–September 2026 unless an archive captured the earlier responses. |

Two other open-data approaches are useful but do not solve this particular pilot. The Web Data Commons Schema.org series extracts structured data, including `JobPosting`, from Common Crawl and has releases going back years, but its main releases are not a complete employer-by-month panel and the project explicitly notes that Common Crawl is only a subset of the web. citeturn8search0turn8search6 An openly available USAJobs longitudinal collection contains millions of federal announcements and is useful as a methodological test bed, but federal jobs are outside the requested direct-employer fintech population. citeturn8search14

### Reproducible archive tests

The highest-value next experiment can be run outside the network restrictions encountered here. For Wayback, start by discovering URLs before downloading snapshots:

```bash
# Discover Stripe career-page captures in the pilot interval.
curl -G 'https://web.archive.org/cdx/search/cdx' \
  --data-urlencode 'url=stripe.com/careers/*' \
  --data-urlencode 'from=20260701000000' \
  --data-urlencode 'to=20260930235959' \
  --data-urlencode 'output=json' \
  --data-urlencode 'filter=statuscode:200' \
  --data-urlencode 'collapse=digest'

# Repeat for ATS hostnames once known, e.g.:
# boards.greenhouse.io/<employer>/*
# jobs.ashbyhq.com/<employer>/*
```

Do **not** stop after receiving CDX rows. For each candidate capture, fetch the snapshot and verify that the returned body contains the expected employer, title, ID and substantive JD rather than a redirect, consent page or JavaScript shell. Waybackpack is a useful thin wrapper around this workflow: it downloads snapshots for a URL, accepts date ranges, has `--raw`, `--uniques-only` and CDX-collapse options, depends only on Python `requests`, and is MIT-licensed. fileciteturn3file0L2-L2 The repository was not archived and its latest push in the GitHub metadata was April 2025, so it is usable but not a fast-moving framework. fileciteturn2file0L2-L2

For Common Crawl, query all three exact crawl indices, not merely the latest:

```bash
curl 'https://index.commoncrawl.org/CC-MAIN-2026-30-index?url=stripe.com/careers/*&output=json'
curl 'https://index.commoncrawl.org/CC-MAIN-2026-34-index?url=stripe.com/careers/*&output=json'
curl 'https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=stripe.com/careers/*&output=json'
```

A returned row should then be dereferenced using its WARC `filename`, `offset` and `length`; only after inspecting the WARC response body should it count as a successful historical JD recovery. Common Crawl's own `cc-downloader` is a current, Apache-2.0-licensed Rust project with Python bindings; its README documents polite retries, concurrent downloads and retrieval of WARC/WET/WAT/index data, and the repository was pushed in August 2026. fileciteturn9file0L2-L2 fileciteturn8file0L2-L2

### Timestamp semantics

The tool should make three time fields impossible to confuse:

**`stated_publication_at`** is a date asserted by the employer or source.  
**`archive_capture_at`** is when Wayback/Common Crawl captured the representation.  
**`first_observed_at`** is the earliest time *your collector or another identified observer* saw the posting.

A capture on 20 September proves that the page was retrievable at approximately that time; it does **not** prove that the vacancy opened on 20 September. Similarly, the first Common Crawl hit is a left-censored observation of availability, not necessarily an employer publication date. This separation is essential to avoid turning crawler behaviour into apparent employer behaviour.

## Employer screening, real retrieval evidence and coverage

### Candidate screening

The strongest evidence from live web retrieval did **not** yield a clean three-company payments cohort.

| Employer | Fit to proposed employer class | Live evidence recovered | July | August | September | Pilot disposition |
|---|---|---|---:|---:|---:|---|
| **Stripe** | Excellent: payments/financial infrastructure | Complete direct-employer US **Fraud Patterns Analyst**, employer job ID `7913410`; remote US/South SF. citeturn22search2turn22search9 | 0 recovered | 0 recovered | **1 usable posting** | **Best candidate**, but insufficient historical density |
| **Ramp** | Good fintech fit, though spend-management rather than pure payments | Historical **Fraud Strategy Analyst (All Levels)** mirror recovered, but it is older than the pilot period and combines levels. citeturn22search11 | 0 | 0 | 0 validated | Keep for archive search; existing recovered record is unsuitable for month-to-month level-equivalent comparison |
| **PayPal** | Excellent large payments platform | Official US and Trust/Safety/Fraud career sections retrieved; no qualifying July/September individual fraud-analytics vacancy was recovered in this research. citeturn23search6turn23search11turn23search21 | 0 | 0 | 0 | Strong archive-search candidate, **not validated historical coverage** |
| **Block / Cash App / Square** | Excellent payments peer | Official careers site retrieved but rendered “0 open jobs” in the fetched representation, indicating that the page/data path cannot be relied on through this retrieval method. citeturn18search0 | 0 | 0 | 0 | Archive/underlying-feed discovery required |
| **Airwallex** | Strong B2B payments peer | Ashby board retrievable through search; it shows payment-fraud data-science work, but no qualifying Fraud Analyst/Fraud Strategy Analyst record was recovered. citeturn18search5 | 0 | 0 | 0 | Do not broaden to data science simply to create sample size |
| **SoFi** | Fintech, but broader consumer finance and less clean payments peer | Historical third-party **Senior Staff Fraud Strategy Analyst, Card**, removed 27 September. citeturn19search12 | 0 | 0 | **1 near-fit** | Useful evidence, wrong peer category/level for proposed cohort |
| **Earnest** | Fintech lender, not payments-focused | Historical **Senior Fraud Strategy Analyst**, US remote/hybrid, removed 11 September. citeturn19search24turn22search26 | 0 | 0 | **1 near-fit** | Useful historical-retrieval example, not peer cohort |
| **Betterment** | Consumer/investment fintech | A Sr Fraud Analyst listing was discoverable in July 2026. citeturn14search29 | **1 near-fit** | 0 | 0 | Different product category and seniority |
| **Weave** | Vertical SaaS with payments | July Fraud Analyst result relating to payments risk was discoverable. citeturn14search11 | **1 near-fit** | 0 | 0 | Company category/scale diverges |
| **USAA** | Financial institution, not fintech peer | Direct-employer mid-level fraud-strategy job dated 8 September, plus a Lead job on the same date. citeturn22search4turn22search13 | 0 | 0 | **2 direct records, separate levels** | Excellent methodological control, excluded from fintech peer cohort |

The important conclusion is not that there were “no fraud jobs” in July or August. The conclusion is narrower: **this research did not recover enough complete, provenance-checked, level-comparable vacancies from three comparable payments-focused fintech employers to estimate a July-to-September requirement trend**. Search-result absence is not archive absence.

On business-category grounds, **Stripe, PayPal and Block** are the cleanest candidate trio to take into the next archive test: all operate major payments platforms and have substantial US employment footprints. PayPal's official careers material explicitly has a Trust, Safety & Fraud category and a large US workforce; Stripe's direct role demonstrates target-role fit; Block's business portfolio includes Square, Cash App and Afterpay. citeturn23search6turn23search11turn22search9turn18search0 I am deliberately calling this the **candidate trio**, not a validated analytical cohort, because historical role coverage remains unmeasured.

### Representative retrieval records

| Employer / role | Original record | Publication evidence | Archive capture | Full JD recovered? | Provenance result |
|---|---|---|---|---|---|
| Stripe — Fraud Patterns Analyst | Employer ID **7913410**, direct Stripe listing/apply pages. citeturn22search2turn22search9 | Direct page does not expose a date in the retrieved text; LinkedIn shows **24 Sep 2026**, so that date is third-party evidence rather than an employer-verified publication timestamp. citeturn22search5 | **Not recovered**; Wayback calls environment-blocked | **Yes**, current direct page | Employer, role, location and ID verified directly |
| Ramp — Fraud Strategy Analyst (All Levels) | Redpoint historical page `30140030`; underlying Ramp employer requisition ID not verified. citeturn22search11 | Only “posted 6+ months ago” in recovered page | Not recovered | Historical third-party copy recovered | Employer/title credible, exact publication time and original employer ID unresolved |
| SoFi — Senior Staff Fraud Strategy Analyst, Card | Built In record `11314584`. citeturn19search12 | Page says removed **27 Sep 2026**; this is not the same as publication date | Not recovered | Substantial third-party content | Useful historical evidence; employer-original record not recovered |
| Earnest — Senior Fraud Strategy Analyst | Built In record `8977056`. citeturn19search24 | Removed **11 Sep 2026**; exact employer publication timestamp unresolved | Not recovered | Partial/substantial third-party page | Role exists historically, but weaker provenance than direct employer copy |
| USAA — Fraud Strategy Analytics (Mid-level), Money Movement | Direct employer URL with page record `97475968368`; whether that URL number is a formal requisition ID was not independently established. citeturn22search4 | **8 Sep 2026** on employer page | Not recovered | Substantial direct-employer text | High provenance but employer is outside selected fintech class |

The Wayback attempt count for this research was **four representative archive endpoint/page calls, zero snapshot/CDX payloads recovered because of environment restrictions**. Representative Common Crawl index access likewise produced **zero index payloads in this environment**. By contrast, ordinary web retrieval successfully recovered the direct Stripe page, direct USAA page and several historical third-party pages. These success counts should therefore not be interpreted as comparative Wayback versus Common Crawl coverage; no such comparison can responsibly be made until the APIs are run from an unrestricted environment.

The auditable machine-readable sample assembled from the records above is available here:

[Download the pilot audit sample CSV](sandbox:/mnt/data/job_requirement_change_pilot_audit_sample.csv)

It intentionally includes non-peer and seniority-mismatched records as **controls/examples**, and should not be treated as a trend dataset.

## Reusable research, code and methods

The closest prior work strongly supports the *concept* of measuring requirement change, while also showing why data construction and composition control matter more than sophisticated language generation.

| Work | What it actually solves | Data / evaluation | Reusable component | Limitation for this pilot |
|---|---|---|---|---|
| **Deming & Noray, “Earnings Dynamics, Changing Job Skills, and STEM Careers”, QJE 2020** — published research. citeturn21search6 | Measures occupational skill-content change over time and links it to careers. | Near-universe online postings, **2007–2019**; analysis compares skills across time and, in some specifications, holds firm/occupation constant. A non-technical author summary reports that roughly 29% of same-firm/same-occupation vacancies in the later period contained at least one new skill requirement. citeturn21search12 | Core idea: compare **same employer + same occupation**, and use skill mention shares rather than pretending every vacancy has a one-to-one successor. | Underlying Burning Glass data are not an openly downloadable employer-level historical corpus; time horizon is years, not monthly. |
| **Hershbein & Kahn, AER 2018** — peer reviewed. citeturn20search1 | Tests whether employer skill requirements changed more in labour markets hit harder by the Great Recession. | Burning Glass vacancies through 2015; changes in advertised requirements are related to local shocks and firm investment. citeturn20search1 | Strong warning about **composition effects**: shifts in observed postings can reflect which firms/jobs are open, not edits to a representative job. | Macro identification design is much larger than your company-level pilot; proprietary data. |
| **Deming & Kahn, “Skill Requirements across Firms and Labor Markets”** — published Journal of Labor Economics version / NBER working paper. citeturn21search9 | Categorises advertised skills and studies within-occupation variation across employers. | Keyword-based grouping into ten general skills; substantial within-occupation employer variation. citeturn21search9 | A useful deterministic baseline for broad cognitive/social/technical categories before adding models. | Categories are deliberately coarse; not enough for Python-vs-SQL, fraud-rule or emerging-AI changes. |
| **SkillSpan, NAACL 2022** — peer reviewed, with code/data. [GitHub](https://github.com/kris927b/SkillSpan). citeturn20search2 | Span-level extraction of hard and soft skills from job postings, including skills not constrained to a fixed inventory. | 14.5k sentences and >12.5k expert-annotated spans; BERT/SpanBERT/domain-adapted models were evaluated, with domain adaptation improving results. citeturn20search2 | Annotation guidelines, two public de-identified subsets, training/evaluation code and JobBERT checkpoints. The repo is MIT-licensed. fileciteturn6file0L2-L2 | Repo's documented reproduction environment includes old Python/Torch assumptions; it extracts spans but does not solve required/preferred status, vacancy identity or temporal change. fileciteturn7file0L2-L2 |
| **Nguyen et al., “Rethinking Skill Extraction … using LLMs”, NLP4HR 2024** — workshop paper. citeturn20search8 | Tests few-shot LLM extraction against conventional supervised skill extraction across multiple datasets. | Six harmonised skill-extraction datasets; LLMs handled some syntactically complex mentions well but did **not** outperform traditional supervised approaches overall. citeturn20search8 | Evidence against making an LLM the unreviewed MVP extractor; use it as a challenger or adjudicator. | Focuses on span extraction, not temporal comparison/provenance. |
| **JobBERT** — 2021 arXiv preprint and later model iterations. citeturn25search11 | Normalises/matches job titles using domain representations informed by skill co-occurrence. | Released a title-normalisation benchmark; newer JobBERT-v3 is trained specifically for title matching. citeturn25search7turn25search31 | Useful **secondary** title-similarity feature for candidate role matching. | Because JobBERT uses skill/requirement information in representation learning, it should not be the primary criterion when *skill change is the outcome being measured*. |
| **Waybackpack**. [GitHub](https://github.com/jsvine/waybackpack) | Downloads/list snapshots for a known URL with date/collapse controls. | Working CLI; Python + `requests`; MIT licence. fileciteturn3file0L2-L2 | Fast archive downloader after URL discovery. | Not job-specific; cannot prove snapshot completeness or reconstruct JS APIs on its own. |
| **Common Crawl cc-downloader**. [GitHub](https://github.com/commoncrawl/cc-downloader) | Reliable bulk retrieval of Common Crawl WARC/WET/index content. | Official Common Crawl project; Apache-2.0; actively updated in 2026. fileciteturn8file0L2-L2 | WARC retrieval after an index match; retries/concurrency already implemented. fileciteturn9file0L2-L2 | Still need URL discovery, WARC parsing and job-page validation. |

For taxonomy support, **O*NET is the best low-friction US-oriented reference**, and O*NET OnLine is licensed under CC BY 4.0. It is useful for broad occupational concepts, work activities and skill categories. citeturn25search12 It should not be treated as a closed vocabulary for the project: SkillSpan's motivation is particularly relevant here, because labour-market taxonomies lag emerging terminology and span-level extraction can preserve newly appearing concepts. citeturn20search5

Commercial job-posting datasets remain credible fallbacks precisely because published labour-market research has benefited from such broad historical coverage. Lightcast is the successor to Burning Glass/EMSI and offers labour-market intelligence built around postings and skills, while major academic work used Burning Glass historical vacancy data. citeturn25search13turn21search6 But access rights, record-level redistribution and price are commercial questions, so “use Lightcast” cannot be treated as an open-source solution.

## Role identity, deduplication and comparability specification

The fundamental modelling mistake to avoid is equating “similar text” with “same/comparable job”. Text similarity can hide real requirement change, while title equality can hide material level or specialisation differences.

The initial tool should represent **three distinct relationships**.

**Same vacancy, edited over time.** This requires a strong identity anchor: employer ATS/requisition ID is strongest; canonical employer URL is next; only after those should title/location/content clues be used. Multiple snapshots of employer ID `7913410`, for example, would be versions of the same Stripe vacancy, not separate observations. Stripe currently exposes that ID in both its `/listing/.../7913410` and `/apply/.../7913410` paths. citeturn22search2turn22search9

**Duplicate representation of the same vacancy.** The direct Stripe listing and direct apply page are a real example. They share employer, title and ID `7913410`, so they should collapse to one vacancy entity with two source representations. A LinkedIn copy with the same employer/title around 24 September is likely another representation, but because the retrieved LinkedIn text did not expose the employer job ID, it should remain a *probable* duplicate until evidence links it more strongly. citeturn22search5turn22search9

**Different vacancies that are comparable enough for a cohort comparison.** These should be matched on employer, occupational function, specialisation, seniority, geography and work arrangement—not on the very requirements whose change you are trying to detect.

A practical baseline can be encoded as follows:

| Dimension | Baseline rule |
|---|---|
| Employer | Canonical legal/brand employer ID; aliases map to one entity, but do not merge parent/subsidiary automatically when hiring practices differ. |
| Vacancy identity | Exact ATS/requisition ID > canonical employer URL > employer + normalised title + location + highly similar body + overlapping active interval. |
| Role family | Fraud analytics/strategy only when analysis/strategy/rules/decisioning is a primary function. Investigation/operations is a separate subtype. |
| Seniority | Explicit title tokens/internal level first: Analyst, Senior, Staff, Lead, etc. Salary level may support. **Years of experience is not the primary classifier**, because it is an outcome to measure. |
| Specialisation | Card fraud, transaction fraud, money movement, identity/ATO, merchant fraud, etc. Preserve rather than throwing these away. |
| Geography | US city/state or explicit US-remote eligibility. |
| Work arrangement | On-site, hybrid, remote-US or unclear as a separate field. |
| Match confidence | `high`, `medium`, `low`, plus reason codes; ambiguous cases go to human review. |

The USAA records illustrate why seniority must be explicit. Its **mid-level Money Movement** Fraud Strategy Analytics role and **Fraud Strategy Analyst Lead** were both posted on 8 September 2026. Treating them as two interchangeable samples could make senior-level duties appear to be a temporal change even though they coexist on the same date. citeturn22search4turn22search13

For distinct-vacancy matching, I would initially require exact agreement on `employer`, `role_family`, `seniority_band` and `specialisation_band`, while allowing location differences only where both fall inside the same US/US-remote scope. Title embeddings such as JobBERT can rank ambiguous title candidates for review, but a model should not make the final match solely from skills or years-of-experience fields. JobBERT's demonstrated title-normalisation purpose makes it well suited to candidate generation rather than causal matching. citeturn25search11turn25search7

For **within-company** reporting, two outputs must remain separate:

`vacancy_version_changes`: requirement edits to the **same vacancy** across archive snapshots.

`role_cohort_changes`: changes in mention rates across **different but comparable vacancies** in the same employer/month.

The second is much weaker evidence about employer policy because opening composition can change. This is exactly the type of compositional issue that broader job-posting studies have had to control. citeturn20search1turn21search9

For the **cross-company** question, do not pool all postings and calculate one overall percentage. Compute each company's July→September change first, then report whether the same direction occurs independently at two or three companies. Otherwise, the employer with the most vacancies dominates the result.

## Requirement extraction and change-detection baseline

The MVP does not need an LLM-first architecture. The evidence supports a simpler, more auditable system: deterministic section parsing and pattern rules, a small alias map, optional taxonomy linking, and human review. SkillSpan provides evidence that domain-specific NLP can improve extraction, while the 2024 LLM study found that few-shot LLM extraction was not generally superior to trained extraction models. citeturn20search2turn20search8

Each extracted requirement should have both **normalised structure** and the **exact evidence phrase**. The minimum record is:

| Field | Example |
|---|---|
| `requirement_type` | `tool`, `technical_skill`, `nontechnical_skill`, `responsibility`, `experience`, `education`, `certification` |
| `normalised_concept` | `SQL` |
| `surface_form` | exact phrase in JD |
| `status` | `required`, `preferred`, `unclear` |
| `explicitness` | `explicit` or `inferred` |
| `section` | `minimum_requirements`, `preferred`, `responsibilities`, etc. |
| `years_min` / `years_max` | numeric where explicitly stated |
| `experience_scope` | `total`, `fraud`, `analytics`, `payments`, `domain_specific`, etc. |
| `qualifier` | `advanced`, `working knowledge`, `hands-on`, etc. |
| `evidence_start/end` | offsets into the preserved raw text |
| `extractor` | `rule`, `human`, `SkillSpan`, `LLM`, etc. |
| `confidence` | numeric or categorical |

The extraction pass should first detect sections such as “minimum requirements”, “preferred qualifications”, “what you'll do” and “responsibilities”. Then apply exact alias normalisation—for example `Postgres` → `PostgreSQL`—without destroying the original term. O*NET can supply broad US occupational concepts, but unknown terms must remain as first-class concepts rather than being discarded because no taxonomy entry exists. O*NET's openness makes it suitable for enrichment, while SkillSpan explicitly addresses open-ended span extraction from changing labour-market text. citeturn25search12turn20search5

Required/preferred status should be based first on section and explicit cues. “Minimum”, “required”, “must”, “at least” and equivalent language support `required`; “preferred”, “bonus”, “nice to have” and equivalent language support `preferred`; otherwise use `unclear`. A missing mention becomes **`not_stated` in that posting**, never “not required”.

Years-of-experience extraction needs to preserve its scope. “Five years of data analysis” and “five years of fraud strategy” cannot both become `experience=5`. Store the quantity and the phrase it modifies.

Change detection should operate over these structured objects:

| Observed difference | Classification |
|---|---|
| Concept absent in old observation, explicitly present in new | `newly_mentioned` |
| Present before, absent later | `no_longer_stated` — **not** “no longer required” |
| Same concept, `preferred → required` | `status_strengthened` |
| Same concept, `required → preferred` | `status_relaxed` |
| Same scoped experience, `3+ years → 5+ years` | `threshold_increased` |
| `SQL → advanced SQL` | `specificity_increased`, not a new skill |
| `machine learning → ML` | alias/synonym, `no_material_change` |
| Sentence reordered or paraphrased but same normalised proposition | `cosmetic_rewrite` |
| Model infers “statistical modelling” from a responsibility but employer never states it | store as `inferred`, excluded from primary requirement trend |

A sentence embedding or LLM can be useful **after** deterministic extraction to decide whether apparently deleted and added clauses are paraphrases, but the interface should always show the two original phrases and the model's reason. That is more defensible than returning an unexplained semantic-similarity score.

### Real worked extraction

Stripe's September-relevant Fraud Patterns Analyst is a good extraction example, though **not a historical change example because no July counterpart was recovered**. The direct employer page requires advanced data analysis and transaction-fraud-rule experience, explicitly requires advanced SQL, and puts Python/Splunk/Tableau and several analytical methods in preferred qualifications. citeturn22search9

A structured representation would include, among others:

| Evidence class | Normalised value | Status |
|---|---|---|
| Experience | advanced data analysis / transaction fraud rulesets, minimum **5 years** | required |
| Tool/skill | SQL, `advanced` | required |
| Domain capability | transaction fraud ruleset management | required |
| Collaboration | modelling/data-science/intelligence stakeholders | required |
| Tool | Python | preferred |
| Tool | Splunk | preferred |
| Tool | Tableau | preferred |
| Method | clustering | preferred |
| Method | classification | preferred |
| Method | link analysis | preferred |
| Education | undergraduate or advanced degree in analytics/data science/statistics | preferred |

The correct July→September output today is therefore **“July comparison record not recovered; change cannot be established”**, not “SQL was added in September”. That distinction is central to the product's credibility.

The same Stripe evidence also gives a real deduplication test. `/careers/listing/fraud-patterns-analyst/7913410` and `/careers/apply/fraud-patterns-analyst/7913410` are two URLs for the same employer vacancy ID, so the correct count is **one posting**, not two. citeturn22search2turn22search9

## Evaluation, trend reliability and go/no-go tests

The evaluation should measure the parts of the system separately. An end-to-end accuracy number would hide whether an error came from bad archive reconstruction, a wrong role match, a missed requirement or an incorrect change classification.

Create a manually reviewed gold sample of approximately 50–100 posting observations once the archive run succeeds. Every ambiguous pair should be independently labelled for:

`same vacancy / duplicate / comparable distinct / not comparable`; role family; seniority; requirement spans; required/preferred/unclear status; years/scope; and final change class.

SkillSpan's expert-annotated span methodology is useful as precedent for evaluating exact-span extraction rather than merely asking whether a broad skill category appears somewhere in the document. citeturn20search2

The main metrics should be:

| Component | Metric |
|---|---|
| Archive reconstruction | proportion of indexed records yielding a complete, employer-valid JD |
| Posting identity / dedup | precision and recall on same-vacancy/duplicate decisions |
| Comparable-role matching | precision first, then recall; report by confidence band |
| Requirement extraction | span-level precision/recall/F1 plus concept-level precision/recall |
| Required/preferred status | categorical accuracy / macro-F1 |
| Experience extraction | exact match on number + scope + status |
| Change detector | precision/recall by `addition`, `removal/not-stated`, `status`, `threshold`, `specificity`, `cosmetic` |
| Trend reliability | sensitivity of X/N results to deduplication, strict matching and role-composition controls |

Two negative controls are particularly important.

First, construct **paraphrase controls**: reorder bullets, replace aliases with synonyms and paraphrase unchanged requirements. The system should produce almost no substantive changes.

Second, take one month's postings and **randomly divide them into two pseudo-periods**. Any apparent broad “shift” then reveals sampling/composition noise rather than temporal change. This matters especially at N=5–10, where one posting can move a mention rate by 10–20 percentage points.

The following thresholds are **proposed product design criteria, not established scientific standards**:

| Decision | Proposed threshold |
|---|---|
| Data go | At least **5 deduplicated, complete postings in both comparison months for each employer used in a trend**, preferably 10 |
| Same-vacancy/dedup go | ≥95% precision on human-reviewed high-confidence matches |
| Comparable-role go | ≥90% precision; ambiguous matches excluded from primary analysis |
| Requirement extraction go | ≥90% concept precision on the target fraud-domain sample; recall can initially be lower if omissions are visibly reviewable |
| Meaningful-change go | ≥90% of surfaced “material changes” judged material and evidence-supported by reviewers |
| Paraphrase control | ≤5% false material-change rate |
| Trend robustness | Main direction should survive exact deduplication and a stricter role-match filter |
| Cross-company claim | Same directional change should appear in at least **two independently adequate company cohorts**, with each company's X/N shown |

At small N, percentages should always be displayed as **counts and denominators**, for example “advanced SQL: 6/8 September postings versus 3/7 July postings”, accompanied by the number of postings in each seniority/location/specialisation cell. Deming and Noray's broader approach of measuring the share of vacancies mentioning skills is conceptually appropriate; the difference is that your pilot's much smaller N requires much more explicit uncertainty and composition reporting. citeturn21search6turn21search12

A finding should be downgraded to “composition-sensitive” when it disappears after keeping seniority/specialisation constant. That check is not optional. The coexistence of USAA's mid-level and Lead fraud-strategy openings on the same posting date shows how easily a change in the mix of levels could masquerade as a change in employer expectations. citeturn22search4turn22search13

## Concrete initial tool specification

The MVP should be a **small auditable data pipeline**, not a dashboard.

The source layer stores every representation before parsing. Its minimal relational/logical schema is:

| Entity | Essential fields |
|---|---|
| `employer` | `employer_id`, canonical name, aliases, business category |
| `vacancy` | internal `vacancy_id`, employer, ATS/requisition ID, canonical original URL, title, role family, specialisation, seniority, location, work arrangement |
| `observation` | `vacancy_id`, source type, source URL, archive record ID, `stated_publication_at`, `archive_capture_at`, `first_observed_at`, retrieval timestamp, HTTP/status metadata, complete-description flag |
| `document` | raw HTML/WARC body, extracted raw text, SHA-256 hashes, parser version |
| `requirement` | observation ID, category, normalised concept, exact evidence, required/preferred/unclear, explicit/inferred, years/scope, qualifier, offsets, extractor/version, confidence |
| `relationship` | vacancy A/B, `same_vacancy_edit`, `duplicate`, `comparable_distinct`, `not_comparable`, score/reason, review status |
| `change` | old/new observation IDs, old/new requirement IDs, change class, exact evidence on each side, confidence, reviewer |
| `cohort_stat` | company, month, exact role/seniority/specialisation filter, numerator, denominator, inclusion/exclusion list |

Raw HTML or WARC data matters because parsers will improve. Storing only extracted JSON would prevent later auditing when a parsing bug is discovered.

### Baseline pipeline

The first implementation should proceed in this order.

**Discover URLs.** Query Wayback CDX, Common Crawl July/August/September indices, employer career paths, and known ATS hostnames. Do not yet filter aggressively by role title; store the URL inventory and IDs.

**Retrieve historical bodies.** Dereference every archive hit. Record redirects and failures. A URL is only “usable” when the body contains a substantive description and the employer/vacancy can be validated.

**Parse provenance before NLP.** Recover employer, title, location, ID, stated publication date, structured `JobPosting` fields where present, source timestamp and raw hashes. Schema.org's `JobPosting` vocabulary can provide fields such as posting dates when employers actually expose them, but those fields should be treated as source assertions rather than recreated from the crawl time. citeturn8search1turn8search8

**Classify the target role.** Human-authored rules first. Separate fraud strategy/analytics from investigation/operations; then assign seniority and fraud specialisation.

**Resolve identity and duplicates.** Exact employer IDs first, content hashes second, fuzzy similarity only after that. Preserve all source observations under one vacancy.

**Extract requirements.** Sections → sentences/bullets → aliases → deterministic patterns → optional open-span model. For the first corpus, a human should correct every extraction.

**Construct comparison cohorts.** Same-vacancy edits are their own analysis. For distinct vacancies, group only equivalent role/seniority/specialisation cells.

**Calculate changes.** Compare normalised propositions and statuses while preserving old/new evidence. Mark missing counterpart data explicitly.

**Aggregate only after review.** Produce X/N mention changes within one employer; then compare company-level directions. Never merge all three companies into one raw posting pool.

### Essential dependencies

The archive layer can reuse **Waybackpack** and **Common Crawl's cc-downloader** instead of building retry/download logic from scratch. fileciteturn3file0L2-L2 fileciteturn9file0L2-L2 For extraction, SkillSpan's data and annotation guidelines are more useful initially than reproducing its full older training stack; the repository explicitly publishes two de-identified subsets plus evaluation code and model links. fileciteturn7file0L2-L2 O*NET can provide open US taxonomy enrichment. citeturn25search12 JobBERT can be tested later as a title-matching challenger, not the primary match rule. citeturn25search11

### Expected effort and blockers

A single engineer/researcher should budget roughly **one working week for the decisive archive experiment** and another **one to two weeks for a carefully reviewed MVP corpus and comparison pipeline** if the archive yields enough pages. That is a project estimate, not an externally benchmarked figure.

Infrastructure cost for this scale should be trivial compared with labour: hundreds of HTML/WARC extracts fit comfortably in ordinary local storage, and deterministic extraction requires no paid model. LLM inference should be optional until evaluated against the rules/human baseline. The potentially material cost is a commercial historical-job source if open archives fail; I did not obtain verified pricing and will not invent it.

The major blockers, in priority order, are **historical recall**, then **complete-body recovery from dynamic ATS pages**, then **enough same-level vacancies within a narrow fraud role**. Extraction itself is lower risk because there are already published datasets, code and strong deterministic baselines for advertised skills. citeturn20search2turn20search8

## Pilot package and smallest next experiment

**1. Feasibility verdict.**  
**Narrow/continue.** Building the retrieval, identity, extraction and evidence-diff tool is justified. Publishing July→September 2026 three-company fraud-analytics trends is **not yet justified** because the requested historical sample size was not recovered. The bottleneck is evidence coverage, not the comparison concept.

**2. Historical-source recommendation.**  
Run Wayback CDX/raw snapshots first for discovered employer and ATS URLs; run July/August/September Common Crawl indices in parallel as an independent source; use indexed/stale job pages only to discover URLs or corroborate records; consider a commercial historical provider only after measuring the open-source recall. Wayback and Common Crawl were live-tested in this research environment but their API payloads were blocked by the environment, so their target-company success rates remain **untested**, not zero. Their documented archive/index capabilities are real. citeturn1search0turn1search1turn10view0turn10view1

**3. Companies, role boundaries and dates.**  
Use **Stripe, PayPal and Block** as the next **candidate** employer trio because they are the cleanest business-category peers among the companies screened; do not declare them the final comparison cohort until the archive run demonstrates coverage. citeturn22search9turn23search6turn18search0 Keep the role boundary at **Fraud Analyst / Fraud Strategy Analyst whose principal work is analytical fraud strategy, rules/decisioning or quantitative fraud control**, split by seniority and fraud specialisation. Dates remain **July 2026 versus September 2026, with August supporting**.

**4. Coverage result.**  
The requested 5–10 postings/company/month threshold is **not met by the currently recovered corpus**. Stripe yielded one directly verified September target posting; Ramp yielded a topically strong but older mixed-level posting; PayPal and Block did not yield qualifying individual July/September records through the available retrieval path; near-fit September evidence exists at SoFi and Earnest, and a direct mid-level September control exists at USAA. citeturn22search9turn22search11turn19search12turn19search24turn22search4

**5. Auditable sample corpus.**  
The five-record source/provenance sample is available as [CSV](sandbox:/mnt/data/job_requirement_change_pilot_audit_sample.csv). It deliberately records uncertainty and includes controls rather than pretending to be a valid trend panel.

**6. Minimal schema.**  
Persist `vacancy`, `observation`, raw `document`, structured `requirement`, vacancy `relationship`, detected `change`, and `cohort_stat` entities. Never overwrite capture/publication/first-observed timestamps or discard original phrases.

**7. Baseline pipeline and worked case.**  
URL discovery → archive-body retrieval → provenance parsing → role/seniority classification → posting identity/deduplication → section/rule extraction → human correction → structured diff → X/N aggregation. The real Stripe case demonstrates both extraction and deduplication: two employer URL variants with ID `7913410` are one vacancy; its current requirements include required advanced SQL and fraud-rule experience and preferred Python/Splunk/Tableau, but **no July version was recovered, so no temporal change is claimed**. citeturn22search2turn22search9

**8. Essential reusable work.**  
Reuse Wayback/CDX plus [waybackpack](https://github.com/jsvine/waybackpack) for snapshots, Common Crawl indices plus [cc-downloader](https://github.com/commoncrawl/cc-downloader) for WARC recovery, [SkillSpan](https://github.com/kris927b/SkillSpan) for extraction annotation/model baselines, O*NET for open US taxonomy enrichment, and JobBERT only as a secondary title-normalisation candidate generator. fileciteturn3file0L2-L2 fileciteturn9file0L2-L2 fileciteturn7file0L2-L2 citeturn25search12turn25search11

**9. Effort, costs and remaining blockers.**  
The decisive open-archive experiment is small enough to perform before building a platform. The unresolved question is whether Wayback/Common Crawl contain **complete descriptions at sufficient density**, especially for dynamically rendered ATS pages. Commercial historical data is a fallback, not a prerequisite; vendor claims indicate historical products exist, but access/licensing was not verified deeply enough here to recommend purchasing one yet. citeturn1search31turn8search18turn8search21

**10. Smallest next experiment worth implementing.**  
Do **nothing else** until this test is run: take Stripe, PayPal and Block; enumerate every career/ATS URL found by Wayback CDX and `CC-MAIN-2026-30`, `CC-MAIN-2026-34`, `CC-MAIN-2026-39`; retrieve and manually inspect every body matching fraud-related title terms; record URL, employer ID, capture time, stated date, completeness, role subtype and seniority. Stop after the inventory is complete and count usable deduplicated records. If all three employers have at least five comparable July and five September records, build the extraction/diff MVP. If only one or two employers pass, narrow the pilot to those employers or extend the window to twelve months. If the combined Wayback/Common Crawl search yields only isolated records, **stop the retrospective open-web three-month trend approach** and begin a forward longitudinal collector while evaluating one commercial historical source.

That experiment directly answers the question that remains unresolved: not whether job requirements *can* be extracted, but whether sufficiently comparable historical evidence exists to say, with defensible provenance, **what actually changed**.