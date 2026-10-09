# Broader company-source acceptance — October 8, 2026

All 84 frozen targets have one reconciled result: **65 verified, 13 unsupported, five unverified, one unavailable**. The baseline had 17 verified; all 17 remain verified, including Stripe. There are 48 new verified companies. Of the 84 checks, 82 completed and two are explicitly deferred at the per-company request limit (DRW and MX Technologies); no target is unprocessed. These deferrals are a separate acceptance dimension and retain existing source status names.

Aggregate usage, including selected retries: **1,598 requests, 378,430,482 response bytes, 1733.021 active seconds (28.88 minutes)**. Limits remained 4,000 requests, 2 GB and three cumulative active hours, with two company workers and one browser. All individual attempts retained 20 requests, 12 MB and 120 seconds.

CSV rows use shared evidence-column references and JSON pointers for larger ownership chains; the full JSON retains every reference. All 84 CSV rows parse with the default Python reader.

JSON, CSV, Markdown, append-only attempt files, checked receipts, the complete pre-batch library and hashed evidence are in `/Users/veerr_89/Work/agents/degent/data/company-library/verification-2026-10-08T19-00-27.618920+00-00`. The target-list hash is `c6b332ba713ab483ff1490ac086bb1968717d01054e112b202716f14cd81c268`. The supplied list and seven-employer default research scope are unchanged.

## Verified sources

Provider distribution counts source records, not companies; multiple sources and retained destination aliases can belong to one company.

| Provider | Verified source records |
| --- | ---: |
| ashby | 16 |
| capital_one | 1 |
| dom | 4 |
| greenhouse | 29 |
| lever | 8 |
| sitemap | 2 |
| workday | 7 |

Greenhouse availability checks use current board metadata and employer identity. Ashby checks use the current canonical board shell, explicitly distinguishing shell availability from an unprobed full API. Their native adapters are implemented; verification does not claim a sampled full description or complete inventory for those sources. Lever samples parse explicit JSON feeds, while valid empty Dwolla establishes availability with an unsampled extractor. Generic DOM/structured and sitemap sources have passed at most two posting samples per source.

The evidence audit reproduced all **26 recorded title/description samples**, including description hashes and character counts, from captured bytes using the final parsers. It checked all **928 hashed evidence files**, all original source records, library IDs/aliases/notes/provenance/top-level metadata, and all **135 saved-run files**. All checks passed. No conflicting verified board owner or baseline regression remains.

## Unresolved results and next actions

| Company | Latest source status | Acceptance | Next action |
| --- | --- | --- | --- |
| Airbase (Paylocity) | unsupported | completed_unresolved | shared_parent_board_requires_airbase_attribution |
| Bilt Rewards | unverified | completed_unresolved | generic_extractor_sampling_failed: missing_matching_jobposting_or_dom_configuration; missing_matching_jobposting_or_dom_configuration; inspect posting URL/container and configure complete employer-specific fields; observed vendor: Gem |
| Coast | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| Credit Karma | unavailable | completed_unresolved | invalid_provider_response_or_HTTP_404; locate the current official recruiting destination |
| Discover (Capital One) | unsupported | completed_unresolved | shared_parent_board_requires_discover_filter |
| DRW | unverified | deferred | budget_exhausted:browser_network_incomplete: browser_origin_rejected; request_limit; retry with an explicitly recorded allowance |
| Fundbox | unsupported | completed_unresolved | adapter_or_configuration_missing: observed HiBob; configure posting-specific DOM/JSON-LD or implement the observed vendor adapter |
| Increase | unsupported | completed_unresolved | hiring_info_not_job_inventory |
| M1 Finance | unverified | completed_unresolved | generic_extractor_sampling_failed: missing_matching_jobposting_or_dom_configuration; inspect posting URL/container and configure complete employer-specific fields; observed vendor: SmartRecruiters |
| Moov | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| Morningstar | unsupported | completed_unresolved | blocked_or_unreachable: &lt;urlopen error timed out&gt;; recheck public access/destination |
| MX Technologies | unsupported | deferred | budget_exhausted:request_limit; retry with an explicitly recorded allowance |
| PayPal | unverified | completed_unresolved | ownership_ambiguous: provider responds; obtain a current official relationship or matching employer identity |
| PEX | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| QED Investors | unsupported | completed_unresolved | portfolio_jobs_not_employer_inventory |
| Sardine | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| Tradeweb | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| Unit | unsupported | completed_unresolved | no_job_board_destination_or_posting_links_found; inspect official hiring destination and configure its listing/extractor |
| Zest AI | unverified | completed_unresolved | generic_extractor_sampling_failed: missing_matching_jobposting_or_dom_configuration; missing_matching_jobposting_or_dom_configuration; inspect posting URL/container and configure complete employer-specific fields |

DRW’s attributed Greenhouse lead identifies **DRW Montreal**, which does not establish general DRW inventory. Discover and Airbase retain separate identities and reject shared-parent attribution. QED remains portfolio jobs; Increase remains hiring information. These destinations do not establish the requested employer inventory.

Observed adapter gaps are Gem (Bilt Rewards), HiBob (Fundbox), SmartRecruiters (M1 Finance), and Zest AI’s LinkedIn-only destination. Bilt exposes substantial Open Graph descriptions but lacks a validated posting employer field. M1 exposes scoped microdata and a differing canonical posting ID; employer-specific extraction and current canonical-link handling still need configuration or an adapter. Failed/unsampled generic attempts never retain a current verified ownership claim from historical samples.

Citadel initially returned 403s, and one complete description used the heading “Your Opportunity.” A validated manual selector configuration, strict posting brand check and explicit role-heading recognition recovered two current posting samples. The current generic source is verified with bounded inventory; earlier failures and configuration are retained.

Rendering remains confined: a fresh Chromium context runs under macOS network denial; permitted HTTPS traffic is fulfilled through the pinned transport. External origins, POST traffic, redirected subresources and required blocked resources remain explicit limitations. No unrestricted browser fallback was enabled.

## Validation

- `.venv/bin/python -m unittest discover -s tests -v`: **192 tests, 184 passed, eight optional browser tests skipped**, 6.152s. The original collection/HTTP checks are included.
- `.venv/bin/python -m degent companies browser-check`: **all eight integration tests passed**, 32.844s, after installing the optional browser extra and Chromium. These cover rendering, request/byte accounting, document redirects, hostile/private/IPv6/custom-port requests, OS network denial, DNS rebinding, cancellation, load-more cycles and hung-script descendant cleanup.
- `node tests/frontend.test.cjs`: passed, including source/history/evidence escaping, deferred counts, progress, budgets and stop/resume controls.
- `./desktop/build.sh`: passed; built and signed `build/Degent.app`.

Exact logs and the hash-bound browser receipt are in `/Users/veerr_89/Work/agents/degent/data/company-library/verification-2026-10-08T19-00-27.618920+00-00/checks/`; machine-readable test results are in `validation.json`. `acceptance-audit.json` contains checks, sample inspections and preservation evidence.

No research collection, research run creation or model extraction occurred during acceptance. Verification parses a small sample only for source validation; current inventory completeness remains unchecked. The Jobseek reference checkout was read only.

## Scope and attribution

The implementation includes bounded discovery and pinned manual redirects; separate verification dimensions with timestamps, reason codes and evidence references; lock-protected conflict checks and metadata merges; Lever global/EU collection and resumable checkpoints; validated DOM/CSS fields, structured JSON/JSON-LD and sitemap traversal; optional confined rendering; resumable frozen-target batches; synchronous CLI verification and asynchronous dashboard start/status/stop/resume; and independent latest/historical evidence views.

Code adaptation attribution and selected data attribution remain separate in `notices/Jobseek-MIT.txt` and `notices/Jobseek-JOB-DATA.txt`. The selected snapshot contains 48 target-matched recruiting leads; it contains no bulk catalog, logos or job descriptions. Leads were checked as candidates, not treated as ownership proof.

Lever pagination and JSON negotiation follow the [public API documentation](https://github.com/lever/postings-api). Browser interception constraints are documented in the [Playwright network guide](https://playwright.dev/python/docs/network); the production implementation also enforces and tests the independent OS boundary.
