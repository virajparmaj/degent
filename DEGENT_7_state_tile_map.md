# Degent — Seven-state adaptive mosaic

This is the minimal visible information architecture for the Degent research dashboard. Every stage retains an in-tile research-phase chooser and changes the remaining tile composition to match the current work. All first-look tiles are borderless; details replace the mosaic instead of adding a sidebar, modal stack, or scrolling panel.

| State | Original workflow operations included | Tiles visible on first look | Expanded workspace |
|---|---|---|---|
| **1. Prepare** | Configure research; system and dependency checks | **6:** Stage chooser, scope/company group, research purpose, run blueprint, readiness, start/check actions | Company and period selection, role tracks, extraction method, request budgets, exclusions, configured paths, readiness detail |
| **2. Discover** | Query planning; Wayback/Common Crawl discovery | **7:** Stage chooser, archive paths, research purpose, candidate/query stream, source outcomes, remaining request budget, company-period coverage | Query-by-query attempt history, search outcomes (success-empty, failed, unqueried), retry counts, archive identifiers and affected captures |
| **3. Recover** | Retrieve bodies; completeness/identity/role/US validation; group duplicate captures | **7:** Stage chooser, screening rules, research purpose, eligible captures and retrieval counts, distinct vacancies, body budget, outcomes | Searchable/paginated observations and captures, rejection evidence, dates and provenance, vacancy identity groups |
| **4. Extract** | Extract requirements; verify structured output and source evidence | **6:** Stage chooser, document queue, major source-passage tile, extraction count/validation, extraction method, structured requirements | Passage pagination, normalized concept and qualifier details, exact evidence anchors, extraction provenance and validation state |
| **5. Review** | Human review and correction (separate from automated checks) | **6:** Stage chooser, review queue, major passage tile, pending-review status, next decision, inline correction control | Source alongside editable fields; requirement status and reviewer note; persistent per-requirement approval; versioned append-only audit records |
| **6. Compare** | Within-vacancy capture comparisons; distinct-vacancy cohort shares | **7:** Stage chooser, analysis modes, methodology statement, before/after capture tile, denominator rule, active comparison mode, change interpretation | Capture-level evidence and date interpretation, or comparable cohort counts and percentage display threshold |
| **7. Report** | Findings, limitations, coverage, report export | **6:** Stage chooser, run bundle, research purpose, result preview, export state, download actions | Evidence traceability, run manifest, limitations, JSON/CSV/Markdown outputs subject to existing pipeline capabilities |

## Interaction rules

- The first click on a tile opens its working details in the existing window. A small action such as changing a requirement status is handled inside the tile where practical.
- Expanded mode uses **three large, borderless tiles**: navigation/context, focused record/evidence editor, and supporting provenance/actions. Escape or Back restores the current stage mosaic.
- Do not add a permanent sidebar, a page-level title/header, universal KPI row, logs tile, or settings tile.
- Errors, archive blocks, system-readiness problems and empty-query outcomes live in their affected workflow state—not separate dashboards.
- Preserve company/period/document context while switching; show source IDs, capture dates and uncertainty where a finding is displayed.
- Publication, capture, retrieval and first-observation dates remain independent. Index records never masquerade as eligible observations. Missing evidence never proves a requirement was removed.
- A stage can be processing-complete yet evidence-insufficient. Percentages for comparable groups with fewer than **five extracted distinct vacancies** are suppressed. Partial periods remain flagged.
- The connected application in `degent/web/` reads the existing Python pipeline and local reports through `degent/dashboard.py`. The original standalone HTML remains a synthetic design reference. Runtime counts and exports are real; empty and evidence-insufficient states are explicit.

## Visual constraints

- Three asymmetric columns, wide central focal point, 6–7 large tiles rather than a permanent grid of 10–12 cards.
- Colors: quiet pale sky blue, warm ivory/cream, white gutters, dark neutral text, light blue actions.
- Font: clean modern sans-serif for controls, sparse editorial serif italics for important statements.
- No outer borders or box shadows on tiles; inputs can have subtle field strokes for accessibility.
- Fit 1440 × 900 and 1280 × 800 without page, tile, inspector or dialog scrolling. Paginate deep content instead.

## Implemented runtime behavior

- `desktop/build.sh` builds a native macOS WebKit wrapper at `build/Degent.app`; the app starts and stops its own loopback service. The same frontend can run in a local browser.
- Column tracks fill the entire content viewport, including the lower Extract/Review tile. White is used only for the intentional gutters.
- Expanded views retain filters and document context. Lists use four records per page; source text uses 640-character pages; long provenance values are paginated instead of hidden behind scrolling.
- Prepare creates a new immutable run configuration. Company/period choices are distinct from record filters. Selected role tracks are enforced by classification.
- Discover shows query outcomes and index-candidate coverage, never labeling it eligible evidence. Recover distinguishes retrieved rejections from failed body attempts.
- Review drafts are scoped to each requirement. Approval uses source integrity checks and optimistic revisions; extraction changes invalidate old approvals. Human decisions affect rebuilt reports.
- Exhausted budgets and absent eligible evidence disable dependent actions. Operations run sequentially, expose stop/resume and report errors in context. A run lock prevents overlapping writers.
- Layout targets are 1440×900 and 1280×800; narrower windows use an in-window panel switcher. Automated state/render checks pass. Rendered visual QA remains unconfirmed because the saved browser permission blocked local browser access.
