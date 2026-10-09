# Implement broader company-source verification and test all 84 targets

You are working in `/Users/veerr_89/Work/agents/degent`, a local Python research
application with a native macOS launcher and a local web dashboard. Implement the
work below, run the automated checks, then run and inspect a live source-verification
batch for every company in the supplied target list. Deliver working changes and
the actual verification report, not just an implementation plan.

## Objective

Improve career-source discovery and verification by integrating, in this order:

1. Better HTTP discovery, validated redirects, and explicit career-board leads.
2. A Lever provider adapter.
3. A configurable DOM discovery/extraction adapter with optional Playwright rendering.
4. Bounded sitemap discovery and posting-specific JSON-LD/embedded-data extraction.

Reuse existing Greenhouse, Ashby, Workday, and Capital One support. Check all 84
companies and maximize evidence-backed coverage. Do not promise or manufacture
84 successful verifications: hiring-contact pages, portfolio boards, shared parent
boards, blocked pages, and genuinely unsupported systems need accurate outcomes.

Keep these distinct: a reachable website, a company-owned careers destination,
a responding provider, a usable description extractor, and complete job inventory.
A successful HTTP request is not sufficient evidence for all five.

## Read the repository before editing

Follow applicable `AGENTS.md` instructions, including `/Users/veerr_89/Work/AGENTS.md`.
Inspect Git status and preserve unrelated work. This checkout may have many
untracked project files; do not reset, clean, overwrite, commit, or push them.

Read at least:

- `README.md`, `notes/company-collection.md`, `notes/company-collection-plan.md`.
- `config/company-targets-2026-10-08.json` and `config/research.toml`.
- `degent/companies.py`, `degent/source_verification.py`, `degent/collection_http.py`.
- `degent/collection.py`, `degent/sources.py`, `degent/workday.py`.
- `degent/snapshots.py`, `degent/config.py`, `degent/cli.py`, `degent/dashboard.py`.
- `degent/web/app.js` and relevant frontend tests.
- `tests/test_company_collection.py`, `tests/test_company_http.py`, and other affected tests.
- `pyproject.toml`, `notices/README.md`, and the Jobseek attribution notices.

The live library is `data/company-library/companies.json`; it is ignored by Git.
Existing saved research runs are under `runs/`. Back up the library before live
changes. Preserve company IDs, aliases, supplied notes, existing source identities,
verification history, and top-level library metadata. Library edits must not rewrite
saved run scopes or expand the seven-employer default research configuration.

## Known baseline and defects

The October 8, 2026 verification report is under
`data/company-library/verification-2026-10-08T17-48-51.342168+00-00/`.
It processed 84 companies: 17 verified, three unverified, one unavailable, and
63 unsupported. Modern Treasury, Parafin, and PayPal were unverified; Jump Trading
was unavailable. Re-read the actual files rather than assuming these remain current.

Current discovery reads at most two pages, primarily accepts career URLs on the
company domain, does not render JavaScript, and does not follow redirects. Some
unsupported companies likely already use supported ATS providers. The saved
Jobseek registry includes Greenhouse leads for Adyen, Brex, Chime, Coinbase,
Credit Karma, Marqeta, Navan, Robinhood, and Upstart, and other provider leads.
Treat those entries as discovery candidates, not current verified facts.

An earlier live pass incorrectly assigned Capital One's shared Workday board to
Discover. `CompanyLibrary.verify()` now rejects boards assigned to another library
company and records `source_conflicts`. Preserve and strengthen this safeguard.
Neither a link from a subsidiary's site nor a provider response proves that the
entire parent-company inventory belongs to that subsidiary.

Also inspect `Collector.process_source()`: its existing provider routing falls back
to the Capital One path, and its status gate does not reject every unsupported
status. New providers need explicit dispatch and explicit verification/capability
gates. Do not allow a DOM or Lever source to fall through to Capital One collection.

## Jobseek reference code

Use the local reference checkout at `/Users/veerr_89/Work/stolen/jobseek` read-only.
Read its applicable repository instructions before inspecting or adapting code.
Relevant files include:

- `apps/crawler/src/core/monitors/lever.py`.
- `apps/crawler/src/core/monitors/dom.py`.
- `apps/crawler/src/core/monitors/sitemap.py`.
- `apps/crawler/src/core/monitors/nextdata.py` and `api_sniffer.py` for focused examples.
- `apps/crawler/src/core/scrapers/dom.py`, `jsonld.py`, and `embedded.py`.
- `apps/crawler/src/shared/browser.py` and `shared/http.py`.
- `apps/crawler/data/boards.csv` and `data/companies.csv` for selected source leads.

Adapt small relevant pieces to Degent's synchronous, bounded, local architecture.
Do not import Jobseek's full runtime, Redis, Postgres, Typesense, Go workers,
production infrastructure, scheduler, or cloud services. Preserve MIT attribution
for adapted code and the existing separate attribution for selected catalog data.
Do not bulk redistribute Jobseek's registry, logos, or job descriptions.

Confirm current public API behavior against primary documentation where needed:

- Lever Postings API: `https://github.com/lever/postings-api`.
- Playwright Python: `https://playwright.dev/python/docs/intro`.

## 1. Improve discovery and HTTP verification

- Inspect the supplied website and explicit careers destination independently,
  including official careers subdomains and externally hosted recruiting pages.
- Add bounded redirect handling, preserving original URL, redirect chain, final
  URL, response status, retrieval time, and the reason any hop was rejected.
- Validate and pin public addresses at every HTTP redirect hop. Preserve HTTPS,
  hostname/certificate checks, credential/private-address/custom-port restrictions,
  and the existing no-environment-proxy behavior. Resolve relative Location headers.
- Bound redirect hops, detect cycles, and charge each hop/retry against the same
  request/runtime/byte budgets. Do not silently enable unrestricted redirects.
- Discover explicit ATS links in HTML anchors, iframe sources, relevant structured
  data, and known embedded recruiting configurations. Parse rather than execute
  arbitrary scripts. Resolve relative links and deduplicate candidates.
- Prioritize direct careers URLs and recognized ATS evidence over unrelated footer,
  login, privacy, marketing, and talent-community links. Retain excluded-candidate
  reasons when useful for explaining a failure.
- Use selected Jobseek registry records as labeled candidates. Verify their current
  provider availability and company relationship before saving verified status.
  Never invent a token by guessing from the company name.
- Record attempted URLs and concrete outcomes. Replace misleading generic success
  messages when no page was successfully inspected or a budget was exhausted.
- Use configurable, finite discovery budgets. Start near 20 HTTP requests, 12 MB,
  five redirect hops, and 120 total seconds per company; tune only with evidence
  and document the final defaults. Preserve existing collection budgets separately.

## 2. Add Lever end to end

- Recognize explicit global and EU Lever board/posting/API URLs. Keep region and
  board token in identity where needed; do not conflate different tenants/regions.
- Use Lever's published public API with an explicit JSON Accept header.
- Verify valid provider responses separately from employer ownership. An empty
  valid board can establish availability, but cannot supply a description sample.
  HTML shells, malformed JSON, error objects, and challenge pages are not empty feeds.
- Implement bounded skip/limit pagination, stable posting IDs, duplicate detection,
  retries/backoff, interrupted-page checkpoints, and honest completeness status.
- Normalize full description sections, title, location, canonical posting URL,
  employment type, and upstream date assertions into Degent's existing records.
- Integrate with collection session accounting, immutable snapshots, vacancy/version
  identities, resume behavior, extraction caching, and current-observation periods.
- Keep provider timestamps, retrieval time, capture time, and durable first observation
  separate. Do not overwrite prior description versions or reuse human approvals.
- Wire provider recognition, verification, explicit collection dispatch, CLI, and UI
  source selection/help. Do not merely add `lever` to a provider set.

## 3. Add DOM discovery with optional browser rendering

- Implement static HTML discovery first; escalate to Playwright only when the
  captured evidence indicates JavaScript rendering or interaction is needed.
- Define a small validated configuration for job-link selectors, relevant containers,
  pagination/load-more actions, allowed origins, and extraction fields. Avoid
  arbitrary JavaScript execution/configuration as the normal adapter interface.
- Make Playwright an optional project extra installed in the per-project `.venv`.
  Document/install the required Chromium runtime. Existing HTTP-only workflows
  must continue working without browser dependencies.
- Use a clean browser context without the user's login, profile, cookies, extensions,
  or credentials. Disable unnecessary downloads/popups and always clean up contexts,
  browsers, and workers on success, error, stop, timeout, or cancellation.
- Enforce public-network restrictions for navigation, redirects, subresources,
  fetch/XHR, frames, and popups. Do not assume that checking only `page.goto()` is
  equivalent to the pinned HTTP transport. Address DNS rebinding and document the
  chosen enforcement mechanism; leave unsafe browser capability disabled if it
  cannot meet the boundary. Do not add a production private-network bypass.
- Bound browser concurrency (start with one), navigation/action counts, network
  requests, download bytes, and runtime. Account for browser traffic rather than
  counting an entire rendered page as one invisible request.
- Preserve selector/configuration/parser versions, evidence URLs/hashes, and the
  ownership chain. Resume pagination without silently skipping unfinished pages.
- Extract a posting-specific complete description, not the whole careers page,
  navigation, cookie banners, or arbitrary visible text. Missing fields, ambiguous
  matches, truncated content, and later-page failures need explicit outcomes.
- If collection is bounded or configuration cannot prove inventory exhaustion,
  mark it bounded; never infer complete inventory from rendering a page successfully.

## 4. Add sitemap and structured-data fallback

- Discover sitemaps from robots.txt and explicit sitemap links, with bounded sitemap
  indexes, child depth, URL counts, bytes, time, and deduplication.
- Limit candidates to plausible job-detail URLs for the intended employer and
  validated provider relationships. A sitemap listing is discovery, not proof
  of ownership, a complete description, or current opening status.
- Parse JSON-LD objects, arrays, and `@graph`, plus recognized embedded JSON where
  needed. Select the JobPosting matching the current posting URL and employer;
  do not take the first JobPosting when multiple unrelated records are present.
- Require a meaningful title and description, preserve original source bytes or
  bounded verification evidence as appropriate, and reuse existing research gates.
- Preserve publication assertions without converting sitemap lastmod or update time
  into an invented original publication date.
- Make missing/ambiguous/invalid structured data explicit. Keep rich full-description
  collection separate from the limited sampling needed for verification.

## 5. Honest verification semantics and company exceptions

Preserve existing source status compatibility or provide an explicit tested migration.
Represent availability, ownership, collection capability, and inventory completeness
separately, with timestamps, concrete reasons, and evidence references. A generic
careers page can be confirmed as an official hiring destination while its collector
remains unsupported; never present that as a verified collectible board.

Verification must remain separate from research collection and model extraction.
For generic extractors, use at most two posting samples per source to validate the
configuration. Label zero-opening boards as extractor-not-sampled rather than
inventing a successful extraction. Do not collect a full inventory just to verify.

Honor these exceptions:

- **Discover:** keep separate from Capital One. Reject the parent's general board
  unless independently tested Discover-specific filtering establishes attribution.
- **Airbase:** retain the Paylocity relationship without labeling every Paylocity
  opening an Airbase job.
- **QED Investors:** preserve `portfolio_jobs`; portfolio vacancies are not QED jobs.
- **Increase:** preserve `hiring_info`; a hiring-contact page need not have a job feed.
- Preserve brands, aliases, supplied acquisition notes, and their provenance.
- Do not treat a talent-community signup as the company's active job inventory.

Check board conflicts atomically when persisting; concurrent discovery must not
assign the same board to unrelated companies. Keep prior verification events
append-only and show the latest attempt separately from historical success.

## 6. Batch workflow, CLI, and dashboard

- Add a first-class batch verification command, for example
  `.venv/bin/python -m degent companies verify-all`, retaining per-company verification.
- Support all targets, selected IDs, explicit budgets, and resume/retry of incomplete
  checks. Save progress after each company; one failure must not abort the batch.
- Snapshot the starting IDs and target-list hash so a concurrent library addition
  does not change the denominator silently. Never rerun completed checks on resume
  unless explicitly refreshing or retrying them.
- Use bounded concurrency with serialized/locked atomic library writes. Expose stop
  and cancellation; a stopped batch must remain resumable and report incomplete work.
- Save a pre-batch library snapshot and JSON, CSV, and Markdown reports under ignored
  `data/company-library/verification-<timestamp>/`. Preserve existing reports.
- Report per company: ID/name, attempted URLs, discovered provider/board, redirects,
  availability, ownership evidence, collector capability, sampling result, final
  status/reason, conflicts, budget usage, timestamps, and remaining action.
- Compare before/after by stable company ID: new successes, regressions, unchanged
  failures, added/removed sources, and unresolved reasons. Counts must reconcile.
- Update company details with useful destination/provider/verification information.
  Add batch verification progress, stop/resume, and errors without blocking the UI.
  Preserve per-company source selection and frozen research scope behavior.

## 7. Automated tests to implement and run

Use deterministic fixtures and controlled transports; ordinary tests must not call
live employer sites or models. Test behavior and boundaries, not just implementation
details. At minimum cover:

1. Relative/cross-domain HTTPS redirects, redirect cycles/limits, and final-URL
   provenance; rejection of credentials, private/loopback/link-local IPv4/IPv6,
   HTTP downgrades, custom ports, and DNS rebinding, including browser requests.
2. Official careers destinations on different domains; embedded ATS links; duplicate,
   login, talent-community, malformed, and unrelated links; no guessed board tokens.
3. Provider availability without ownership, ownership without an available provider,
   stale prior ownership, and generic HTTP-200/challenge pages.
4. Discover/Capital One, Airbase/Paylocity, QED portfolio jobs, and Increase hiring info;
   shared-board conflicts including concurrent verification writes.
5. Lever global/EU identity, empty valid lists, malformed bodies, 404/429/5xx,
   pagination boundaries, duplicate IDs, missing IDs, later-page failure, timeout,
   checkpoint resume, resource exhaustion, and changed-description versions.
6. Static versus rendered DOM discovery, selectors matching zero/multiple postings,
   load-more/pagination loops, explicit empty states, partial descriptions, missing
   browser installation, cancellation, process cleanup, and request/byte accounting.
7. Sitemap indexes/cycles/bounds, external and unrelated URLs, malformed XML, multiple
   JSON-LD JobPostings, `@graph`, ambiguous employer/URL matches, and date provenance.
8. Batch resume after interruption, per-company exceptions, lock contention, monotonic
   usage, accurate aggregate counts, metadata preservation, and before/after reports.
9. Explicit collector dispatch and refusal to collect unsupported/unverified sources.
   Retain legacy configured-source compatibility only where already intentional.
10. Stable company IDs, no cross-company board assignment, frozen saved scopes,
    current-versus-historical periods, snapshot hashes, and exact-version review rules.
11. CLI and loopback HTTP integration, frontend progress/error/stop/resume rendering,
    and HTML escaping of remote evidence and error messages.

Run focused tests first, then the complete required checks:

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/frontend.test.cjs
./desktop/build.sh
```

Run optional-browser integration tests explicitly after installing that extra.
Keep browser-fixture test accommodations isolated from production network policy.
Use existing style tools if available. Fix failures caused by these changes; record
unrelated baseline failures with evidence. Report actual test counts and commands.

## 8. Live verification acceptance run

After deterministic tests pass, run the new batch workflow against all 84 targets
in `config/company-targets-2026-10-08.json`, matched to current stable library IDs.
Use the improved HTTP discovery first, then supported provider probes, then bounded
browser/structured-data fallbacks for unresolved companies. Apply finite aggregate
batch limits in addition to per-company limits and record their configured values.

Inspect the results rather than merely reporting that the process exited zero:

- Every target has exactly one company-level result, with 84 processed or an explicit
  count of stopped/budget-deferred targets requiring continuation.
- Follow remaining supported-provider leads within the configured limits and rerun
  affected companies after correcting implementation or source configuration issues.
- Recheck plausible ownership regressions for Stripe and other previously verified
  employers. Preserve failed-current versus successful-historical evidence honestly.
- Confirm no general parent/portfolio board was assigned to the wrong employer.
- Verify sampled titles/descriptions against the actual target company; counts and
  matching tokens alone are insufficient.
- Every unresolved result names a concrete cause and next action. Distinguish
  blocked/login/browser dependency, ownership ambiguity, no job-board destination,
  adapter missing, invalid feed, and budget exhaustion.
- Record provider distribution and which additional vendor adapter would address
  observed remaining cases. Do not add every Jobseek vendor speculatively.
- Do not run a full research collection, model extraction, or create research runs
  as part of this live verification acceptance batch.

## Deliverables and completion criteria

Deliver the implemented collectors/discovery/batch workflow, meaningful automated
tests, updated setup/use documentation and attribution, and the live before/after
verification reports. Update `README.md` and `notes/company-collection.md` with
optional browser setup, CLI usage, budgets, statuses, and actual remaining gaps.

The work is complete when the implementation is integrated, required checks have
run, every target has been processed or explicitly identified as unable to finish
under a real recorded constraint, and all results are auditable. Do not silently
defer a difficult phase or call all 84 verified to meet a numerical target.

In the final response, state what changed, the exact test results, baseline versus
new verification counts, confirmed provider coverage, unresolved companies/reasons,
and clickable absolute paths to the reports. Explain any material limitations.
