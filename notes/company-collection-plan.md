# Company collection integration plan — October 8, 2026

The existing flow freezes dashboard/CLI configuration, collects current feeds or archive bodies,
audits eligibility, extracts with versioned cache dimensions, applies exact-version human decisions,
and reports gated comparisons. Existing employers are configuration rows. Current collection has a
60-document cumulative default, feed caches tied to one run, no separate refresh session, and no
cross-run content/extraction cache. Archive and import paths have independent provenance rules.

Implementation phases:
1. Local JSON company library, locked atomic writes, stable company/source IDs, search/pagination,
   manual add, explicit verification and source provenance. No network/model work during lookup/add.
2. Inventory sessions and checkpoints in run files. Round-robin source steps; optional document
   limit independent of request, elapsed-time, response/storage-byte and extraction budgets.
   Explicit budget extensions retain cumulative accounting. Manual refresh starts a new session.
3. Shared immutable full-SHA-256 blobs and SQLite vacancy/version/event index, with idempotent
   event projection into existing observations.jsonl. Extraction cache uses existing dimensions,
   destination ID/quotation validation and fresh review state. Workday list/detail adapter for PayPal.
4. Existing UI/CLI integration, deterministic acceptance tests, compatibility and usage documentation.

Run scope embeds company/source records; library changes apply only to new runs. A scope continuation
creates a new run with parent/differences recorded. Existing run files remain readable, retain original
budgets/checkpoints and are not silently migrated. Legacy evidence is not assigned invented first-seen
provenance; shared first-seen coverage starts with new captures.

Catalog strategy: a small attributed/versioned snapshot of selected Jobseek discovery leads, under
its separate CC BY-NC notice, combined with Degent's existing configuration. No bulk CSV, logos or
job descriptions are redistributed. Lead membership is not verification. Explicit source probes
must distinguish provider availability from employer ownership. Manual unsupported companies persist.

Reference: Jobseek Workday endpoint/list/detail conventions and failure semantics are adapted;
provider identity, URL aliases, immutable snapshots and full hash storage are independently fitted
to Degent. Preserve MIT notice for adapted logic. Do not adopt Redis/Postgres/Typesense/Go/cloud
workers, latest-description overwrites, int64 hashes, first-JobPosting selection, or publication-only
incremental watermarks. Every manual refresh reconciles the configured inventory; unseen jobs are
never automatically marked closed.

Acceptance: >60 jobs over bounded continuations; checkpoint/cumulative reservation survival;
partial-page failure; unchanged/changed captures across runs; cache/review invalidation; source/date
separation; library search/add/snapshot through HTTP and CLI; old adapter/archive/import/report tests.
Network/model calls are mocked. No broad live collection or Codex extraction is planned.

Deferred: scheduling, Lever/other ATS until needed, arbitrary web crawling, automatic closure,
market-wide catalog/coverage, automatic historical recovery, non-US research controls, rich imports.
