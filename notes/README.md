# Documentation

Run CLI examples from the repository root.

## Shared guides

- [Desktop and local dashboard](guides/desktop.md)
- [Company collection and source verification](guides/company-collection.md)
- [Capital One comparison pipeline](guides/comparison-pipeline.md)
- [Third-party attribution](../notices/README.md)

## What belongs in Git

Application code, tests, synthetic fixtures, reusable operating guides, default configuration,
prompts, schemas, aliases, and license notices belong in the repository. The company source
leads and dated model-price catalog are application reference inputs. Keep `.env.example`
with blank credentials; actual `.env` files stay local.

## Local study records

Personal study plans, design records, handoffs, reports, policy decisions, validation receipts,
and feasibility research remain in their organized `notes/` subfolders. `.gitignore` publishes
only this index and `guides/`; other notes are local. An existing checkout can keep the detailed
local inventory in `notes/local-index.md`, also ignored.

Saved runs, acquired descriptions, imported handovers, source captures, local databases,
credentials, environments, build outputs, and caches are excluded from Git. They retain their
local paths so saved research and evidence references continue to work. A fresh clone does not
contain someone else's studies, decisions, or machine-specific validation snapshots.
