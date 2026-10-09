# Degent — local historical research

Degent collects and reviews current, archived, and imported job descriptions for US fintech roles. It keeps fraud analytics/strategy, credit-risk analytics, and quantitative credit/risk/capital modeling separate, and now recognizes people skills, work habits, and practical AI use alongside technical requirements. The local company library supports persistent manual additions, name/alias/domain search, multiple career boards and explicit source verification. The supplied 84-company fintech and quant target list has been imported into this checkout's local library; [the source list](config/company-targets-2026-10-08.json) is versioned separately from the seven-employer research configuration. Entry/mid levels remain the default; new runs can choose other supported levels.

See [what is implemented and what remains](notes/capabilities-and-gaps.md) for the design-to-code audit, live evidence, priorities, and sources. Historical coverage is still unproven; the earlier historical run produced no eligible complete descriptions.

The new [Capital One comparison study](notes/comparison-pipeline.md) covers public US analytics, credit/risk, quantitative modeling, data/AI and engineering roles across seniority. It enumerates the full accessible inventory, preserves source/version/date evidence and pauses at cumulative resource limits. Reviewed role definitions, full-text gold/audit packets and a skill heatmap keep unknowns and assessment bases explicit. OpenRouter is configured with an explicit approved model and a gitignored local `.env` key. Paid inference allowances and validation remain pending; the study cannot yet support validated skill percentages. The [implementation acceptance report](notes/comparison-implementation-acceptance.md) records the first bounded collection and remaining gates.

Create it from **+ New → Capital One knowledge study**, or use `degent --run-id new-study study create`. The guide documents collection, recovery, contracts, review and quality gates. Existing saved runs retain their original policies.

## Open the desktop app

The seven-stage mosaic is now the live frontend for the Python agent. It runs locally in a native macOS window, using WebKit and a loopback-only Python service. The UI and run files stay local, with no frontend runtime dependencies or remote fonts. Optional Codex extraction uses your existing login to process selected source text.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
./desktop/build.sh
open build/Degent.app
```

The app is built at `build/Degent.app`. It loads existing runs automatically and starts its own service on an available local port. Closing the app stops its service and active worker; saved checkpoints remain available. The app requires macOS 12+ and the project checkout with Python 3.11+. Rebuild after moving the checkout or changing the native launcher. Python/frontend edits are loaded on the next launch.

For a browser window, run:

```sh
.venv/bin/python -m degent.dashboard --port 8765
```

Open [the local dashboard](http://127.0.0.1:8765). The dashboard fills the viewport with six or seven tiles. Clicking a tile opens a three-pane workspace in the same window; Escape returns to the mosaic. Record lists, source text, provenance fields and downloads use pagination. At narrow widths, three bottom buttons select the visible panel. Source text and expanded forms allow scrolling when a window or a long record needs it.

Use **+ New** to search/add companies, inspect and verify career boards, and select reporting periods, role tracks, extraction method and separate resource budgets. Leave the optional document limit empty to collect available inventory. **Recover → Progress / continue / refresh** shows per-source outcomes, session limits, extensions, and pending extraction/review. Existing run scope and budgets are retained. **Check system** reports archive connectivity and local dependencies; **Run / resume research** moves through the selected collection sources, evidence audit, extraction and reporting; stage-specific controls remain available. Only one worker runs per dashboard, and a file lock prevents the CLI or another app instance from writing the same run concurrently. Errors and stop/resume controls stay in their workflow tiles.

**Review** saves append-only human decisions in `reviews.jsonl`, tied to the exact source hash and extraction version. Changing records keeps each draft separate. Saving a decision does not rewrite extraction evidence. **Build reports** applies current decisions to comparisons and exports. Changed extraction versions require fresh review; stale reports are labeled for rebuilding.

The supplied `degent_mosaic_states.html` and reference screenshots remain design references. The connected application is implemented in `degent/web/` and `degent/dashboard.py`. Its counts, source text and downloads come from local run files. An operation completing does not imply sufficient eligible evidence; later stages show an explicit empty state when necessary.

## Run the pipeline

Python 3.11 or later is required. Install the project with `.venv/bin/python -m pip install -e .`; static HTML/CSS-selector discovery uses BeautifulSoup. Optional browser, test and WARC libraries are declared in `pyproject.toml`. HTTP-only collection works without Playwright.

```sh
.venv/bin/python -m degent doctor
.venv/bin/python -m degent --run-id fintech-pilot discover
.venv/bin/python -m degent --run-id fintech-pilot fetch
.venv/bin/python -m degent --run-id fintech-pilot extract
.venv/bin/python -m degent --run-id fintech-pilot report
```

`current` collects Greenhouse/Ashby feeds, Lever global/EU paginated feeds, verified DOM/structured sources, official Capital One descriptions, and Workday list/detail sources; `import <file.json>` accepts saved descriptions. `run --codex` runs the complete selected workflow and stops for review or an explicit limit/provider issue. `audit` creates a source-coverage check; `evaluate <labels.json>` checks rule output against supplied labels.

`discover` uses a resumable manifest and a hard cap of 300 archive requests, including retries. It stores index outcomes and capture records after each query. `fetch` starts with 25 body attempts per employer, records why unused allocations cannot be filled, and may reassign those slots while keeping the global cap at 150. It filters known non-200 Common Crawl rows before retrieval. The extraction default is rules-only; add `--codex` to use the existing Codex login sequentially, with a 30-document cap, 40 total model calls, at most two attempts per document/version, and up to 10 semantic review calls. Model calls are reserved before dispatch. New current-collection sessions use cumulative request, runtime, response-byte and saved-evidence budgets, optional document limits, durable checkpoints, and recorded extensions. Extraction limits do not cap collection. Legacy saved runs and imports retain their existing cumulative limits. Codex runs are ephemeral, read-only, ignore user tool configuration, and reject detected command, MCP, or web tool events. No API key fallback is used.

Run output is stored under ignored `runs/<run-id>/`. Legacy bytes remain under `data/<run-id>/`; new shared immutable full-SHA-256 snapshots and a local SQLite identity/artifact index live under `data/snapshots/`, with the company library under `data/company-library/`. These files are local because archived job descriptions may contain redistributed copyrighted material. Code, configuration, prompts, schemas, aliases, and synthetic tests remain versioned.

## Company collection

See [company-library workflow, CLI commands, storage and migration](notes/company-collection.md)
and the [integration plan](notes/company-collection-plan.md).

```sh
.venv/bin/python -m degent companies search stripe
.venv/bin/python -m degent companies verify stripe
.venv/bin/python -m degent --run-id stripe-study current --companies stripe
# Resume bounded chunks; extend allowances explicitly when exhausted:
.venv/bin/python -m degent --run-id stripe-study current
.venv/bin/python -m degent --run-id stripe-study continue --requests 20 --seconds 120
.venv/bin/python -m degent --run-id stripe-study current
# Later, after completing this capture:
.venv/bin/python -m degent --run-id stripe-study refresh
```

Search/addition stay local and make no model calls. Source verification is separate. Fresh seed configurations are unverified leads; check current library evidence before creating a run.
Unsupported companies persist with concrete verification outcomes. Source availability, employer ownership, extractor sampling and inventory completeness are reported separately.

Saved runs freeze selected company/source records. Library edits apply to future runs; use an
explicit scope continuation for an existing study. Fresh observations reuse unchanged content and
compatible automatic extraction across runs, retain first-observed time, and preserve every changed
version. Human approvals are never copied through the shared cache. Existing runs are readable and
are not silently migrated. No live collection/model pilot was performed for this integration.
Selected Jobseek leads and adapted Workday conventions have [separate attribution and license
notices](notices/README.md); no bulk catalog, logos or descriptions are redistributed.

## Verify company sources in batches

Verification does not create research runs or call models. It samples at most two generic posting descriptions per source and never treats a reachable page as exhaustive inventory.

```sh
.venv/bin/python -m degent companies verify stripe --requests 20 --bytes 12000000 --seconds 120
.venv/bin/python -m degent companies verify-all --targets config/company-targets-2026-10-08.json
# Copy the absolute directory returned by the command:
.venv/bin/python -m degent companies verify-all --resume /absolute/verification-directory
.venv/bin/python -m degent companies verify-all --resume /absolute/verification-directory --retry-incomplete
# Retry selected stable IDs explicitly; completed checks otherwise stay untouched:
.venv/bin/python -m degent companies verify-all --resume /absolute/verification-directory --retry-ids stripe,paypal
```

Default verification bounds: 20 HTTP requests (including redirects/retries), 12 MB and 120 seconds per company, five redirect hops; batch bounds are 4,000 requests, 2 GB and three cumulative active hours, with two company workers. Collection budgets remain separate. Ctrl-C/SIGTERM saves a resumable stop. Each batch freezes target IDs/hash and configuration, backs up the library, and writes JSON/CSV/Markdown reports and bounded hashed evidence under ignored `data/company-library/verification-<timestamp>/`. The dashboard’s company library exposes background verification, progress, stop, resume and retry controls.

The October 8 acceptance run reconciled all **84 targets: 65 verified, 13 unsupported, five unverified and one unavailable**, up from 17 verified. Two checks (DRW and MX Technologies) remain explicitly deferred at their request limits. All prior verified companies remain verified. The [acceptance report](notes/company-source-acceptance-2026-10-08.md) lists provider coverage, all unresolved companies, next actions and limitations. Verification used 1,598 requests, 378.4 MB and 28.9 active minutes; no research runs or model calls were made.


Optional rendering requires Chromium and a passing policy test on macOS:

```sh
.venv/bin/python -m pip install -e '.[browser]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m degent companies browser-check
```

Rendering uses a clean ephemeral profile. macOS denies browser IPv4/IPv6 networking; only local Unix IPC is permitted. Allowed HTTPS resources are fetched through Degent’s public-address-pinned Python transport and fulfilled into Chromium. Service workers, WebSockets, downloads, popups and unnecessary resources are blocked. A hash-bound test receipt gates rendering; code changes require rerunning `browser-check`. Platforms lacking the tested confinement boundary remain HTTP-only. Browser redirects and blocked/missing resources produce explicit outcomes rather than unrestricted fallback.

Generic configuration uses validated CSS selectors, exact allowed origins, posting fields, optional next-page/load-more selectors, and explicit empty states. Supply JSON through:

```sh
.venv/bin/python -m degent companies configure-source COMPANY_ID \
  --url https://example.com/careers --provider dom --dom-config /absolute/selectors.json
```

See [company collection documentation](notes/company-collection.md) for configuration, statuses, evidence, compatibility and actual live coverage.

## Evidence interpretation

An index row is only a candidate. A historical description is counted only after replay or WARC validation, text completeness checks, employer/role classification, posting-specific US-scope evidence, and preserved source hashes. Publication assertion, archive capture, retrieval, and collector first-observation timestamps remain separate. Reports distinguish same-vacancy changes from requirement shares across distinct openings; missing periods remain unavailable, groups below five assessed distinct vacancies do not receive percentages, and the archive sample is not treated as the employer's full vacancy population.

New comparisons separate required/preferred statements, applicant qualifications/job tasks/company expectations, and reviewed/exploratory output. Configured pairs and triples must appear within the same posting. Undated imports are excluded from time comparisons, and unequal completed windows or mixed extraction versions receive no change percentage.

Additional outputs include `requirement-combinations.csv`, `wording-shares.csv`, `feasibility.json`, `activity.jsonl`, `model-outputs.jsonl`, and `document-reviews.jsonl`.

Generated files include `manifest.json`, `coverage-matrix.json`, `archive-records.jsonl`, `observations.jsonl`, `requirements.jsonl`, `coverage.csv`, `employer-period-coverage.csv`, `observations.csv`, `requirements.csv`, `same-vacancy-changes.csv`, `requirement-shares.csv`, `report.json`, and `evidence-report.md`.

The separate [Capital One pilot and continuation steps](notes/capital-one-pilot.md) document its saved scope, source verification, stage counts, exclusions and pending human review. Capital One uses title screening followed by full-description classification, including explicit seniority and template-only credit-context checks. New Run separates optional document limits from cumulative session resource allowances; current captures use a separate `CURRENT-*` period.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/frontend.test.cjs
./desktop/build.sh
```

Latest checks: 192 Python tests (184 passed and eight browser tests run separately), all eight browser integration tests passed, frontend checks passed, and the native build passed. Exact logs are linked from the acceptance report.

The tests cover the existing research pipeline plus local HTTP access, safe downloads, worker lifecycle, budget exhaustion, source pagination, review conflicts, interrupted writes and report effects. Frontend checks exercise state/render functions without launching a browser. The optional browser check separately tests rendering, pinned request accounting, redirects, hostile requests, DNS rebinding, cancellation and descendant cleanup under the production macOS confinement policy.
