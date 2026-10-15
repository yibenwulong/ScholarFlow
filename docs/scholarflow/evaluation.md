# Validation and research-quality evaluation

Software tests and scientific evaluation answer different questions. Synthetic fixtures demonstrate API/UI workflow and provenance behavior; they are not evidence of literature search completeness or model accuracy.

## Software validation

The repository includes real SQLite and ASGI tests for human gates, immutable versions, source matching, per-occurrence citation checks, CAS conflicts, exports, budgets, leases and worker/checkpoint recovery. A complete offline API flow creates and approves a plan, selects a subset, reads self-authored PDFs, writes/checks/edits a report, accepts an exact problem draft and exports all three formats.

Playwright covers desktop/mobile plan/library, source evidence, matrix edits, report draft/conflicts, Markdown sanitization and event recovery. The live browser case starts a real API and a separate worker and exercises the Next proxy without mocking browser requests. CI runs these checks in explicit synthetic mode. See `verification.md` for the latest local execution record and environment limitations.

## Human research evaluation protocol — not yet measured

Use three independently curated topics:

| Topic | Suggested scope | Main risk to inspect |
| --- | --- | --- |
| Multi-agent scientific literature synthesis | Planning, retrieval, synthesis and evaluation | Agent role labels mistaken for demonstrated research improvements |
| Retrieval-augmented generation evaluation | Retrieval/answer metrics and datasets | Incomparable metrics turned into a universal ranking |
| Graph neural networks for molecular prediction | Dataset splits, task types and reported limitations | Dataset leakage, preprint/published edition mismatch and abstract overreach |

For each topic, two researchers should predefine questions, dates, sources and a manually curated comparison set. Record the exact provider/model/configuration snapshots. Independently score:

1. Paper identity and edition matching: correct identifiers/authors/year; record missing metadata rather than guessing.
2. Search coverage: relevant retrieved/missed papers, source failures and eligibility decisions. Do not claim systematic-review completeness from API success.
3. Extraction coverage: methods/datasets/findings/limitations reported vs missing; whole-text vs abstract source boundary.
4. Quote location: correct document hash, physical page/offset and verbatim span.
5. Semantic support per citation occurrence: supported, inferred, conflicting, insufficient or needs review. Check numbers, negation and comparisons separately.
6. Human effort: corrections, unresolved issues, time to a usable reviewed draft; actual recorded token/call usage when available.

Keep disagreements and reviewer rationale. Publish anonymized, distributable annotations and failure cases before reporting aggregate scores. **No accuracy, recall, time-saving percentage or research-quality score has yet been measured for this fork.**
