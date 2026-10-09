# Capital One evidence-backed comparison study

Implemented as an opt-in study. Existing current/archive runs retain their policies and evidence. The first study is `capital-one-knowledge-baseline-2026-10-08`; its preserved descriptions can be reviewed before paid inference. Extraction, paid benchmarking, embeddings execution, stronger-model escalation and comparative conclusions remain gated.

## Start, collect and resume

```sh
.venv/bin/python -m degent --run-id my-capital-one-study study create --seed-run capital-one-credit-pilot
.venv/bin/python -m degent --run-id my-capital-one-study current
.venv/bin/python -m degent --run-id my-capital-one-study study prepare
.venv/bin/python -m degent --run-id my-capital-one-study study groups
.venv/bin/python -m degent --run-id my-capital-one-study study gold
.venv/bin/python -m degent --run-id my-capital-one-study extract
.venv/bin/python -m degent --run-id my-capital-one-study report
```

`study create` requires a new ID, freezes the Capital One official source and scope, and copies no approvals. Optional seed observations reference their existing immutable files. `current` automatically processes checkpoint chunks, with no study document or request-count research cap. It enumerates all advertised listing pages before remaining details. Explicit retail/service titles and non-US listings are recorded as excluded; unfamiliar titles are fetched for scope review. A title-based inclusion is a candidate until its duties and role boundary are reviewed.

The decimal allowances are 200,000,000 downloaded bytes, 7,200 cumulative active seconds and 1,000,000,000 saved evidence bytes. HTTP pacing, bounded retries and per-response limits still apply. Requests and time are durably reserved before dispatch; normal completion reconciles actual use. Interrupted requests retain conservative byte reservations with actual bytes explicitly unknown. The displayed in-progress runtime includes the remaining reserved allowance, not measured elapsed work. Session summaries distinguish byte/time/storage pauses, cancellation, unavailable bodies/sources, pagination failure and live inventory drift. Extensions retain consumed usage. No extension is made automatically.

```sh
# Resume an interrupted or chunk-paused session:
.venv/bin/python -m degent --run-id my-capital-one-study current
# Only when the user authorizes additional allowances:
.venv/bin/python -m degent --run-id my-capital-one-study continue --bytes 50000000 --seconds 900
# Queue missing failed descriptions, then resume (same consumed allowance):
.venv/bin/python -m degent --run-id my-capital-one-study study retry-failed
.venv/bin/python -m degent --run-id my-capital-one-study current
```

`refresh` creates a new capture session; it is not an extension of the existing session. Scope/model defaults are read from the saved run configuration, not silently replaced by the global defaults. The same-run lock prevents simultaneous writers. SIGINT and app SIGTERM checkpoint the new study cleanly. A changing page total prevents an atomic-complete inventory claim even when every observed page has been enumerated.

## Canonical evidence and dates

The data contracts are documented by `schemas/comparison-contract-v1.schema.json` (`$defs` for individual record families), with the provider wire schema in `schemas/compact-requirement-v1.schema.json`. Canonical ordinary categories and relational evidence records remain the source of truth; CSVs and heatmaps are derived artifacts.

- The frozen company/source record retains stable company/brand IDs, aliases, provider board identity and official ownership verification. Posting aliases and requisition IDs remain separate from title/text similarity.
- `observations.jsonl` separates vacancy, content version and capture observation IDs. Full readable text and provider/raw evidence are hash-bound; parser signatures distinguish reparsing from wording evidence. Repeated captures do not create new vacancies. Confirmed Capital One requisition identity joins its known URL aliases; similarity alone never merges or establishes a repost. Other repost questions remain unresolved candidates.
- Employment type is normalized only from recognizable source assertions; raw values and uncertainty remain separate. Capital One currently supplies function codes such as `FBENG` in its `employmentType` field. These are preserved as unrecognized assertions and never used as employment strata. A taxonomy assignment can add a reviewed `employment_type` with exact `employment_type_evidence`; unknown remains a visible stratum.
- Publication and update assertions preserve raw values, provenance and uncertainty. Missing/invalid/ambiguous dates remain unknown. Collector first observation and version first observation are distinct. Seed first-observation fields describe the earliest preserved study evidence when no indexed collector value exists. A source-stated future publication date is flagged for review and assigned UNKNOWN in the cohort view.
- `source-units.jsonl` retains all units with exact full-text character offsets, heading/context, selection reason, hash and selector version. Selected units include Basic/Minimum/Preferred Qualifications, heading variants, Responsibilities and unheaded context. Explicit salary/legal boilerplate is omitted from model input while preserved in full text. Unknown structure expands; it never silently truncates to a token target.
- `requirements.jsonl` is an append-only extraction history. Each record contains the compact original wire output, expanded exact evidence spans, normalized or unresolved concepts, categories, strength/context, explicit experience/proficiency and alternative logic. Effective requirement and extraction IDs bind human decisions to their precise source/output version. Invalid/truncated responses remain in `model-outputs.jsonl` and cannot enter assessed denominators.
- Taxonomy versions and their immutable snapshots bind definitions and assignments to source evidence. Definition changes require new stable IDs with explicit `supersedes` split/merge lineage. Approved skill aliases cannot ambiguously map to two concepts. Current taxonomies do not copy approval from another run.

Detailed periods are 2025–2026 quarters, 2022–2024 halves, PRE-2022 and UNKNOWN, with broad eras in separate fields. Calendar completed/partial/future status is independent of observed source coverage. Capture-period analysis uses actual observed text. Publication cohorts use the latest surviving posting wording observed today. They cannot establish wording at publication or historic changes. Unknown publication dates still contribute to a known capture baseline.

## Provider-neutral extraction and compute

`ModelRunner` in `degent/model_runner.py` requires an identity dictionary, `count_tokens(request)` and `extract(request) -> ModelResponse`. OpenRouter is selected through `degent/openrouter.py`, using `openai/gpt-6-luna` by default. The approved benchmark alternatives are `openai/gpt-oss-20b` and `anthropic/claude-haiku-5.5`. Model IDs were verified against the public catalog on 2026-10-08; availability can change. The CLI returns `waiting_for_allowance` until explicitly authorized; `--codex` does not bypass the study gate. An enabled matching identity, call/token limits and output allowance are required. Paid inference additionally requires recorded spending authorization, a dated price schedule and a cost allowance. There is no model fallback list, auto model routing, or stronger-model escalation. Provider fallbacks are disabled and supported schema parameters required. HTTP failures retry only the selected model within the recorded attempt limit. Redirects are rejected and error bodies never enter logs.

Normally one structured call processes all selected units. The optional compact fields are `t` category, `q` exact qualifier wording, `y` explicit year bounds (null preserves an unstated bound), `a` alternative phrases and `op` alternative logic. Local code reconstructs quotations, source spans, concept aliases and canonical metadata. Full validation rejects invented spans/qualifiers/experience numbers. Exact evidence support still does not establish semantic correctness; full-text gold review supplies that check.

The 1,100 input / 400 output token target is planning guidance, not truncation. `token-estimates.json` reports character-derived ranges, separately from provider tokenizer and billed usage. Selected input includes the actual schema and instruction overhead. All calls/retries are reserved and measured where available; unknown failed-call usage retains allowance reservations. There are at most two attempts per signature by default. Cache keys include source, selector implementation, prompt/schema, provider settings and normalization versions. Vocabulary-only updates renormalize saved wire output locally; they do not require another model call. Review identities change, requiring fresh decisions. Full model/selector changes invalidate automatic artifacts.

Local preparation records CPU/elapsed time and whole-process peak memory. Collection records requests, downloaded/saved bytes and active time. Remote CPU/memory and missing provider usage are explicitly unmeasured. API costs require actual usage and recorded prices; character estimates are not billed tokens.

## Role and skill review

Local TF-IDF duty/domain neighbors and mutual-neighbor clusters are exploratory proposals. They use responsibility/context profiles rather than measured skill prevalence. They neither equate titles nor approve groups. Optional externally supplied embeddings must match each preserved profile hash and include a model signature; their execution remains deferred.

Use `study groups --vectors reviewed-profile-vectors.json` only with compatible, authorized external vectors. Review `role-proposals.json` against full text. Approve explicit functions, domains, boundaries and assignments using a JSON object such as:

```json
{
  "approved": true,
  "reviewer": "reviewer name",
  "groups": {
    "credit-analytics": {
      "name": "Credit analytics",
      "definition": "Credit portfolio analysis and decision strategy; excludes platform implementation without credit duties.",
      "function": "analytics",
      "domains": ["credit"]
    }
  },
  "assignments": {
    "document-id": {
      "source_sha256": "exact source hash",
      "role_group_id": "credit-analytics",
      "evidence": "Exact duty/domain quotation from this description.",
      "scope_decision": "included"
    }
  },
  "skills": {}
}
```

Provide the full retained taxonomy when revising assignments. An assignment can additionally supply a reviewed `level` and exact `seniority_evidence` quotation. New skill definitions need category, aliases/boundaries and `supporting_evidence` entries with `document_id`, `source_sha256` and exact `quote`. Groups are supported by assignments or the same evidence-entry format. Splits/merges record parent IDs in `supersedes`; comparisons require reassignment under one common taxonomy. Unknown roles and seniority remain explicit. Title-classified seniority is unreviewed evidence, not a claim of perfect organizational grade equivalence.

Role assignment evidence must be in the preserved duty/domain/title profile, rather than only in a Qualifications block. An out-of-scope assignment may omit a role group, with `scope_decision: "excluded"`, exact source evidence and an explicit `exclusion_reason`. These reviewed exclusions remain in versioned taxonomy history.

```sh
.venv/bin/python -m degent --run-id my-capital-one-study study approve-taxonomy --file reviewed-taxonomy.json
.venv/bin/python -m degent --run-id my-capital-one-study study prepare
```

The dashboard includes the **Capital One knowledge study** preset and an Actions → **Knowledge study / groups and evaluation** workspace. It can prepare source units, propose clusters, freeze the gold set, prepare audits, import reviewed taxonomy/labels, and rebuild reports. Exports include the source units and review packets. Full-document approval explicitly records omission auditing, full/partial description completeness and full/partial selection completeness. Approving only extracted statements does not certify absent requirements. Omitted requirements can be added using compact evidence-unit/span items against any preserved unit.

## Evaluation and acceptance

`gold-packet.json` freezes 60 distinct descriptions, 20 calibration and 40 held-out, round-robin across title-function/seniority and observable format strata (unheaded, short/long, negation, alternatives, AI context and standard). Full descriptions, format tags, source hashes and split IDs are preserved. Reviewers must additionally check that these observable tags capture the difficult formats of interest. Insufficient capture diversity remains an evaluation limitation, not a reason to claim representativeness.

Fill each document's `labels` with canonical `concept` or `original_concept`, `evidence_start`, `evidence_end`, `status` and `statement_scope`; record `omission_audited: true`. Label full-text omissions as well as candidates. Optional role and synonym pair labels are evaluated separately. Calibration labels do not contribute to held-out scores. Human-corrected extraction is not used as raw model performance. A passing statement evaluation needs all 20/40 labels, one method signature, exact accepted evidence, ≥95% statement precision, ≥90% recall, ≥95% strength/context accuracy and ≥90% selector recall. Reports include subgroup scores and descriptive statement intervals; 40 documents do not establish universal accuracy.

Explicit attributes also need gold labels (`experience_range`, `experience_years`, `qualifier`, `alternatives`, `alternative_logic`, using null/empty for no stated value). Incorrect attribution can coexist with a correct quotation, so nonempty attributes are checked separately at the 95% accuracy standard. Unlabeled predicted attributes keep evaluation exploratory. Annotated label files and evaluation results receive immutable hash-named snapshots.

```sh
.venv/bin/python -m degent --run-id my-capital-one-study study evaluate --file labeled-gold-packet.json
.venv/bin/python -m degent --run-id my-capital-one-study study audit-packet
```

Subsequent automated results require full-text audits of a stratified 10% per function/level (rounded up), plus flagged concepts/unclear statements/unknown section structure. Missing or failed audit gates keep automation exploratory. All accepted model output must retain exact evidence support. Review corrections are append-only, tied to extraction IDs. Source or method changes require reassessment; approval does not silently transfer.

## Metrics and report interpretation

`report.json`, CSVs, `evidence-report.md` and `role-baseline.html` derive from the same dataset. The heatmap compares approved groups within a single company/brand, seniority, employment type, time basis/period, strength and assessment basis. Each cell exposes count, assessed denominator, unresolved count, method versions and audit state. Percentages are suppressed below 30 assessed distinct vacancies and for mixed methods. Repeated captures/statements do not increase prevalence. Required and preferred percentages can overlap and are not additive.

`SQL or Python` remains an alternative; it is not two unconditional required skills. Negation, recruitment AI, practical AI use and AI engineering are separate contexts/categories. Incomplete descriptions support explicit positive statements but cannot establish full-description non-mention. A human finding of not-stated-as-required/preferred differs from automated non-detection. Missing/failed/unmapped evidence never becomes zero.

Mention summaries count statements and distinct mentioning vacancies separately. Co-occurrence uses vacancies assessed for every selected component. Experience/proficiency summaries preserve ranges, alternatives and wording with distinct stated-value denominators; absent values are not zero years. Coverage exports retain every detailed period, including unavailable historical windows. A useful comparison requires passing the current method's quality gates and at least two approved groups with ≥30 assessed cells for the same skill/strength at matching known seniority; an unknown-seniority stratum does not establish a seniority match. Otherwise the deliverable is counts, coverage and unresolved evidence.

## Decision provenance

The original register is preserved in `research-decision-register-baseline-2026-10-08.md/.json`. Version 2 adds the agreed study overlay and keeps proposals, rejected alternatives, assumptions and unresolved gates distinct. `research-decisions.jsonl` is the append-only decision journal. `research-policy-review-recovered-2026-10-08.json` preserves the nine edited decisions and four notes transcribed from the browser workbook, including truncated/uncertain readback. It is explicitly a transcription, not an exported workbook snapshot.

## Local OpenRouter setup

Put the secret after `OPENROUTER_API_KEY=` in `/Users/veerr_89/Work/agents/degent/.env`. Do not paste it into chat. `.env` and related local variants are gitignored; `.env.example` contains no secret. Environment variables override literal local dotenv values, without shell expansion.

Choose an approved model for a saved study:

```sh
.venv/bin/python -m degent --run-id capital-one-knowledge-baseline-2026-10-08 study model --model openai/gpt-6-luna
```

This preserves the earlier frozen configuration and journals the change. Switching models resets inference authorization and allowances, while retaining consumed usage. An optional `OPENROUTER_MODEL` in `.env` must match the saved identity before paid calls can run. Configure allowances only following explicit spending authorization: `inference.enabled`, `spending_authorized`, positive `call_limit`, `input_token_limit`, `output_token_limit`, `cost_limit_usd`, and dated `pricing` (`effective_date`, `input_per_million`, `output_per_million`). Use conservative rates for the chosen context tier. The adapter passes these rates as provider price ceilings and records returned native token counts and billed cost, including provider token/cost details when supplied. Missing usage retains reservations; OpenRouter reserves a conservative serialized-byte allowance plus protocol overhead when no tokenizer is available. Character estimates are not billed usage. The saved dated catalog is reference pricing, not authorization.

The transport follows the official [API contract](https://openrouter.ai/docs/api/reference/overview), [structured output contract](https://openrouter.ai/docs/guides/features/structured-outputs), and [provider selection controls](https://openrouter.ai/docs/guides/routing/provider-selection).
