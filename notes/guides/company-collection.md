# Company library and manual capture sessions

See [reference attribution](../../notices/README.md). This extends the local research workflow;
it introduces no hosted service or recurring scheduler. Static selectors use BeautifulSoup; Playwright is optional.

## Use the app

1. Open **+ New → Scope**. Search the local library by name, alias, or website domain.
   Results are paginated. Check/uncheck companies to define the new study.
2. Open **Details** to see boards, provider, provenance, verification result/time, and manual
   business classification. **Add company or another career source** saves a persistent local
   addition. Repeating a name, alias, domain, or board preserves the company ID. Ambiguous
   matches between different existing companies require resolving the conflict.
3. **Verify / discover career sources** runs a separate bounded probe. Search/addition do not
   fetch postings or call models. Unsupported companies still save; supply an explicit
   Greenhouse, Ashby, Lever, Workday or official Capital One URL using the same company name,
   or configure a DOM source. Verification inspects the supplied website and career destination
   independently, follows validated HTTPS redirects, and prioritizes explicit ATS evidence.
   It does not guess a token from a company name. Choose boards individually in Details;
   deselect an unsupported/stale lead if another board is the intended source.
4. Keep the role tracks, seniority, periods and comparison controls. Manual companies default
   to `unknown` business group; adding them does not establish comparability with fintech peers.
5. In **Budgets**, leave **Optional document limit** empty for inventory collection. Requests,
   download bytes, saved evidence bytes, time, extraction documents and model calls are separate.
6. Create the run, then **Collect current jobs**, or open **Recover → Progress / continue /
   refresh**. Inspect discovered, captured, eligible, distinct, new-version, changed, unchanged,
   rejected, unavailable, pending-extraction and pending-review counts. Collection completeness
   and inventory completeness are separate. A successful chunk is not a completed inventory.
7. **Continue collection** resumes checkpoints. When a resource allowance is exhausted, expand
   **Extend this session allowance**, enter additional resources, save the extension, then continue.
   An extension adds to the existing allowance without clearing usage. **Refresh with a new
   capture** starts a later session after completion/bounded inventory or an explicit failure outcome.
8. Audit, extract saved descriptions, review exact quotations, and build reports as before.
   Collection can finish with all extraction still pending. Inspect **captures** and the source
   panel for capture history, full hashes, source provenance and first-observed times.

Library edits never change saved scope. Verify/add sources **before** creating a run. To change an
existing study's companies or sources, use **Create scope continuation**, review the new scope,
and create a new run. It records parent, before/after company/source records, and added/removed
company IDs. Old evidence and decisions remain in the parent; continuation does not silently
copy human approvals or turn the two studies into one cohort. Capture history can span both runs.

## CLI

### Supplied targets and local library

The [84-company source list](../../config/company-targets-2026-10-08.json) is a reference
snapshot. A fresh checkout starts from the seven-company research configuration; the actual
selectable library is stored locally in `data/company-library/companies.json`. Library edits
apply to future runs and do not change existing frozen scopes.

Source leads, supplied classifications, and related recruiting destinations require separate
verification. A reachable board does not establish employer ownership, research comparability,
or complete inventory. The verifier records conflicts when another company already owns a board.
Use the batch commands below with `--targets` to check a supplied target list. Verification
reports, attempts, snapshots, and evidence remain under ignored `data/company-library/`.

Use the same config for related runs so they share the library and snapshot store.

```sh
.venv/bin/python -m degent companies search stripe --page 0 --size 10
.venv/bin/python -m degent companies add 'Example Company' --website https://example.com \
  --career-url https://boards.greenhouse.io/explicit-board --alias 'Example Alias'
.venv/bin/python -m degent companies show stripe
.venv/bin/python -m degent companies verify stripe

# --companies takes comma-separated IDs returned by the library. New run only.
.venv/bin/python -m degent --run-id stripe-study current --companies stripe
# Each invocation processes a bounded chunk. Repeat until complete or a visible pause/issue.
.venv/bin/python -m degent --run-id stripe-study current
# Explicit extra allowance, only when needed; then resume with current.
.venv/bin/python -m degent --run-id stripe-study continue --requests 20 --seconds 120
.venv/bin/python -m degent --run-id stripe-study current
# Optional explicit research limit on a NEW run:
.venv/bin/python -m degent --run-id limited-pilot current --companies stripe --document-limit 10
# Later manual full inventory reconciliation:
.venv/bin/python -m degent --run-id stripe-study refresh
.venv/bin/python -m degent --run-id stripe-study audit
.venv/bin/python -m degent --run-id stripe-study extract
.venv/bin/python -m degent --run-id stripe-study report
# Explicit scope continuation; use verified library records.
.venv/bin/python -m degent --run-id stripe-block-study scope --from-run stripe-study \
  --companies stripe,block
```

`continue` also accepts `--bytes`, `--storage`, and `--documents` (only when a document limit exists).
Use `--model-calls`, `--extraction-documents`, and `--semantic-review-calls` to extend extraction
allowances, including after collection completes. These extensions preserve consumed calls/attempts
and the frozen scope. Effective budget overrides and receipts are stored atomically in the manifest;
the original configuration file remains unchanged. Run extraction after recording its extension.
`current --requests N` sets the first session's request allowance on a new run. Other initial
resource settings live in the selected config or the New Run form. Saved scope/budgets reject
implicit CLI overrides. `run` stops visibly at collection pauses/issues; stage-specific extraction
can run independently on already saved evidence. `refresh` never launches a scheduled task.

## Records and identities

- `data/company-library/companies.json`: locked, atomic local company/source library; stable IDs,
  aliases, website, group, board URLs/configuration, provenance and verification outcomes.
- `runs/<run>/dashboard-config.json`: frozen selected company/source and research configuration.
- `manifest.json`: append-preserved collection sessions, immutable scope hash, per-source cursors,
  restrictions, accounting, extensions, and daily current-observation reporting windows.
- `runs/<run>/collections/<session>/`: response checkpoints linking original response bytes to
  request URL, status, headers and actual retrieval time. Error responses are retained too.
- `data/snapshots/raw/` and `clean/`: immutable full-SHA-256 blobs. Raw HTTP responses are distinct
  from parsed posting records and clean text. Atomic publication protects interrupted writes.
- `data/snapshots/index.sqlite3`: transactional vacancy identity, content-version identity,
  capture-event records and automatic extraction artifacts. This is Python's standard SQLite.
- `observations.jsonl`: idempotent run-local projection into Degent's existing evidence interface.
- `requirements.jsonl`, `reviews.jsonl`, `document-reviews.jsonl`: existing append-only extraction
  and exact-version human-decision records. Cached automatic artifacts contain no approvals.

`library_dir` and `snapshot_dir` can explicitly override their default locations under `data_dir`.
Vacancies are scoped by company + provider board/tenant + explicit provider posting ID. Workday
external posting paths and requisition assertions remain separate; similar titles never merge.
Capital One retains its official-source employer/URL/requisition validation. Full original URLs,
canonical URLs and available aliases are saved. No unseen vacancy is automatically marked closed.

Parsed content versions bind vacancy + parser + full clean-text hash. Unchanged text reuses the
clean blob/version, records a fresh event/time and retains durable first observation across runs.
Changed wording appends a version and retains the old bytes/text. A wording reversion is a change
from the most recent capture even if its earlier content version already exists.

Sessions reserve requests before dispatch and derive deterministic capture IDs from session,
company, source and posting. A crash after event commit but before run-file append can safely
replay that event without a second capture or completed-body fetch. Reservation gaps may consume
allowance; they do not invent evidence. A killed process conservatively consumes its reserved
remaining runtime until the user explicitly extends it. Completed response checkpoints survive restart.

Extraction reuse uses `extraction_cache_key()` dimensions: source text, parser, prompt, schema,
vocabulary/aliases, method/model/effort, and semantic-review configuration. Destination document
IDs are rebound and all quotations revalidated. Model/config/parser changes invalidate the key.
Human decisions stay local to exact source/extraction versions; identical text in unrelated
vacancies is never enough to share approval. Extracting a changed description does not approve it.

## Bounds and completeness

Defaults are 25 source steps per invocation, 100 requests (including retries), 120 cumulative
active seconds per session, 50 MB response bytes and 100 MB saved evidence payload allowance.
The step chunk is a safety boundary, not a posting target; resuming advances inventory and can
collect more than 60 documents. The optional document limit has no 60-document ceiling.

Response reading is capped (8 MB per response by default); a capped response is incomplete,
never a successful empty feed. Configure `collection_response_bytes` before creating a new run
when a larger verified board needs it. Budget extensions increase session allowances, not this
separate frozen response-size bound. HTTP errors, malformed rows, missing identities, inconsistent
Workday totals/duplicates and later-page failures remain explicit. Workday resumes its last
successful offset and cached detail responses. There is no publication-only watermark; every new
refresh checks the configured full inventory, including edits to older postings.

The saved-evidence allowance charges blob payloads, event metadata and run observation projections
before writes; interrupted reservations can overcount. It is not an OS disk quota and excludes
SQLite page overhead and control/checkpoint files. Network reads and provider calls have finite
timeouts; wall-clock exit can lag the deadline by an in-flight read. Source verification is separate: 20 requests, 12 MB and 120 seconds per company,
with five validated redirect hops. Batch evidence is bounded, hashed and saved outside research
runs. Verification samples at most two generic postings per source; native empty feeds are
extractor-not-sampled. Availability, current ownership, collector capability, sampling and
inventory completeness remain independent. Successful verification does not prove inventory completeness. Private/local addresses, credentials and custom ports are rejected. DNS waiting is bounded; a validated public address is pinned through the TLS connection while preserving hostname/certificate checks. The collector uses direct HTTPS connections, without environment proxies.

Capital One is still a role-keyword/search-page sample. Its keywords, page bounds and truncation
are saved; `bounded` is never promoted to an exhaustive inventory. Greenhouse/Ashby bodies are
collected without selection on AI, skills or work-habit vocabulary, then classified by the
existing research gates. Workday uses unfiltered list requests and full detail bodies. Lever preserves region/tenant identity and paginated raw checkpoints. DOM/sitemap sources use
posting-specific JSON-LD or validated selectors and remain bounded unless static configured
pagination proves exhaustion. JSON-LD selects by URL and hiring employer; ambiguous records
are rejected. Sitemap lastmod never becomes publication time.

## Migration, dates and remaining limits

Existing saved runs retain their frozen configuration, legacy collector/cache behavior and
cumulative limits. No bulk rewrite/backfill is performed. Use a new scope continuation to opt an
old study into the new sessions. The shared index does not claim to know when legacy evidence
was first seen. Existing archive and attested/undated import paths remain separate and tested.
New manual capture sessions add explicit current reporting windows without changing historical
scope. Current observations cannot enter historical windows, even if an upstream date is old.

Workday `startDate` is a labeled upstream assertion, not original publication. Ashby `publishedAt`
remains last-published; Greenhouse first-publication/update assertions remain separate. Collector
capture, retrieval, durable first observation, archive time and attested import time are distinct.
Same-vacancy reports and separate-vacancy requirement shares retain their existing eligibility,
review, geography, level, method-compatibility and evidence-quality gates. Missing/unreviewed
extraction remains unknown. Legacy vacancy identities are not automatically merged with new provider/board identities when provenance is incomplete. Cross-run capture history is available; comparisons are built from
each run's own evidence and approvals. The continuation link is not a pooled-study report.

Fresh seed configurations start as unverified leads. The local acceptance library records current checks separately from historical successes. Upstart was verified through its currently checked Greenhouse source; historical failed checks remain in the evidence. A provider adapter passing fixtures is not evidence of live availability.
Login-dependent sources, unconfigured custom DOM/embedded formats, ATS facets beyond basic Workday pagination, automatic
closure, scheduling, rich imports, global catalog ingestion, automatic historical recovery and
cross-run report pooling remain deferred. Historical coverage and market-wide trends remain unproven.

Earlier collection validation: 135 deterministic Python tests plus frontend state/render checks, rendered browser
checks of synthetic company search/addition, source details and budgets in narrow/desktop layouts,
and a successful native app build. The tests include 73 postings over three chunks, failure/resume,
exact raw bytes, unchanged/changed/reverted versions, artifact/review invalidation, and HTTP/CLI paths.


## Broader verification workflow

`companies verify-all` accepts all library companies, `--targets PATH` matched by stable IDs,
or `--companies ID,ID`. It accepts `--requests`, `--bytes`, `--seconds`, `--batch-requests`,
`--batch-bytes`, `--batch-seconds`, and `--concurrency` (1–4; default 2). Use `--resume DIR`
without overrides to retain the frozen denominator and cumulative allowance. Completed checks
are skipped; `--retry-incomplete` or `--retry-ids ID,ID` records fresh attempts explicitly.
A fresh invocation (`--refresh` is an explicit new-batch marker) snapshots a new denominator.
An incomplete or blocked result remains distinct from a company with no active openings.

Before the first live library write, a batch saves `companies-before.json`, its target-list hash,
ID mapping, budgets, effective configuration and starting scope hash. `progress.json` saves
per-company progress. `checked-<attempt>.json` precedes the library commit; `attempt-<attempt>.json`
records the report projection. Restart replays durable checked/committed attempts without probes.
Interrupted requests remain charged conservatively; an interrupted active company reserves its
remaining per-company runtime against the cumulative batch allowance. Library writes are atomic,
serialized within a process, and protected by a bounded OS-lock wait between processes.
Verification history is append-only; legacy source state is retained as a labeled prior event.
Canonical source identities are checked under the write lock to prevent shared-board assignment.
Manual additions also preserve arbitrary top-level library metadata.

`report.json`, `report.csv` and `report.md` contain one row per frozen company, including deferred
rows when stopped, statuses, timestamps, attempted URLs, redirect chains, bounded evidence hashes,
provider/board records, availability, ownership basis, extractor sampling, conflicts, usage, and
next actions. Reports compare stable IDs with the pre-batch state and reconcile successes,
regressions, unchanged failures and added/removed sources. Evidence bytes stay ignored locally.
Prior reports and historical successes are retained separately from failed current attempts.

### DOM and structured source configuration

Install core dependencies with `.venv/bin/python -m pip install -e .`. Example selector JSON:

```json
{
  "version": 1,
  "allowed_origins": ["https://example.com"],
  "job_links": "main a[href]",
  "next_page": "a.next",
  "empty_selector": ".no-openings",
  "empty_text": "No openings",
  "fields": {
    "title": "h1",
    "description": ".job-description",
    "employer": ".hiring-company",
    "location": ".location"
  },
  "render": false,
  "exhaustive": false,
  "max_pages": 20
}
```

`container` optionally scopes both listing and posting extraction to one matching element.
Field selectors must match exactly once. A field can also use a validated object such as `{ "selector": "meta[itemprop=name]", "attribute": "content" }`; only content, datetime, alt and title attributes are allowed. Explicit posting brands must agree with the selected employer. Structured JobPosting data can supply posting fields
without manual selectors. Recognized JSON scripts and strict JSON assignments are parsed;
arbitrary JavaScript and expressions are never evaluated by the extractor. Explicit employer
identity and a meaningful complete description are required before collector labels are added.
A generic page's visible text is not a description fallback. Configuration changes reset source
verification and change parser/cache versions; they never change frozen saved run scopes.

Use `companies configure-source ID --url URL --provider dom --dom-config PATH`, then verify.
The `sitemap` provider supports bounded indexes and detail extraction with the same configuration;
discovery defaults are six documents, child depth two, 1,000 examined URLs and 4 MB expanded XML.
Malformed XML, entities, cycles, unrelated origins and failed child fetches remain explicit.
Static DOM completeness additionally requires an explicit `advertised_total_selector`, a stable advertised count and terminal pagination with reconciled unique rows. Missing totals remain bounded even when `exhaustive` is requested. Generic collection is bounded by default; rendering/load-more and sitemap discovery never imply
exhaustive employer inventory. Native Lever collection marks complete only after a valid terminal
page and all pending rows are captured. No unseen vacancy is automatically closed.

### Optional browser setup and boundary

```sh
.venv/bin/python -m pip install -e '.[browser]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m degent companies browser-check
```

The optional policy tests save a receipt tied to browser, transport and integration-test hashes.
The production renderer requires this receipt and a successful macOS confinement probe. Chromium
runs in a temporary clean profile under `sandbox-exec`: IPv4/IPv6 networking is denied; local
Unix-domain IPC is allowed for browser process communication. Context request interception uses
only the pinned Python HTTPS client, never `route.continue_`, `route.fetch` or an unrestricted
proxy. HTTPS origins are allowlisted; credentials/custom ports/private addresses are rejected
before dispatch and DNS is validated/pinned at each retry/redirect. Document redirects are
navigated explicitly to the validated final URL; redirected subresources require explicit final
configuration. Service workers/WebSockets/popups/downloads are disabled. Only required HTML,
script/CSS and fetch/XHR resources may be fetched. POST browser traffic is rejected in this version.

One browser runs at a time, with five navigations and ten load-more actions per company (persisted cumulatively in collection sessions);
network requests/bytes share company and aggregate budgets. Rendered HTML has a 4 MB bound.
A watchdog kills the owned browser process tree at deadline/cancellation, and contexts/browsers
are closed on normal/error paths. Browser errors, blocked required resources, missing Chromium,
or unavailable confinement are explicit and keep unsafe rendering disabled. HTTP workflows
continue without browser dependencies. Browser-fixture transports exist only in tests.


## Inspect verification results

Read each batch's JSON/CSV/Markdown reports for company statuses, deferred checks, provider
coverage, resource usage, and next actions. Source details retain current evidence, earlier
attempts, and prior successful checks separately. Check the actual local library before
creating a study; a previous verification report does not prove current availability or
complete employer inventory.
