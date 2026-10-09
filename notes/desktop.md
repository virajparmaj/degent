# Local dashboard implementation

The runtime consists of `degent/web/` (the supplied mosaic visual language, with complete grid tracks), `degent/dashboard.py` (stdlib loopback HTTP service and worker controller), and `desktop/Degent.swift` (AppKit/WebKit launcher and native file downloads). The Python pipeline remains usable from the CLI.

The service binds only 127.0.0.1 and rejects unexpected Host headers. Mutations require its random session token and the exact local Origin. Downloads use a fixed filename allowlist. No archived HTML is executed; source content is escaped plain text. Only approved local navigation stays inside the native webview; external source links open in the default browser.

Workers launch argument arrays with the project Python interpreter and no shell, in their own process group. Stop and app shutdown terminate the group, preserving saved checkpoints. The CLI and review writes share an OS file lock per run. JSON replacement is atomic; JSONL readers tolerate an in-progress final line, and append repairs a truncated tail before resuming.

A new run saves selected configuration to `dashboard-config.json`. Existing runs use their saved dashboard config when available, otherwise the current repository defaults. Scope changes create another run so budgets and existing evidence cannot be silently reset. Readiness results and worker status are stored in each run directory.

Human decisions are append-only `reviews.jsonl` events. Requirement identities cover the exact source hash, extraction metadata, requirements and index. A revision check rejects conflicting edits. Source integrity is checked again on approval. `effective_extractions()` applies matching decisions without mutating the raw extraction file; `build_report()` consumes those effective latest versions. Invalid or excluded extractions never enter dashboard requirement counts. Partial periods are labeled independently and never receive differences against complete windows.

The earlier browser check was blocked. The October 8 implementation pass successfully inspected source/seniority setup, comparison settings, and the live review workspace in the in-app browser. Narrow layouts keep long evidence and forms scrollable. This was a focused check, not a complete accessibility or cross-browser audit.

For current implementation status, known limits and source evidence, see [capabilities and gaps](capabilities-and-gaps.md). The app now exposes current-feed collection, JSON import, one-command runs, concept/context corrections, document-level approval, skill combinations, literal wording, and recent activity. Saved historical runs are not automatically migrated to new semantics.

`document-reviews.jsonl` binds a whole-document decision to a stable extraction identity, including when extraction found no statements. All categories for known concepts derive from the versioned local vocabulary. Original model output remains in `model-outputs.jsonl`; normalized, source-validated candidates remain in `requirements.jsonl`. Human review is separate from both.
