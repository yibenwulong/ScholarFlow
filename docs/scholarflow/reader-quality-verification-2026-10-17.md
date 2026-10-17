# Reader repair verification — 2026-10-17

The repair addresses sparse successful extractions, malformed missing-data entries, repeated reading of successful batches, and model-reconstructed quotations.

## Behavior verified

- Every field is checked; an empty field requires a reason scoped to the supplied text. A wholly empty paper remains a failure.
- Numbered source units determine exact quotations and character offsets. Unknown or duplicate IDs are rejected.
- Explicit unbound missing entries are separated from claims only when the same field already has a missing reason. Valid source IDs are preserved in full.
- Validated successful batches survive later failures. Previously rejected responses can be revalidated under a new leased operation with complete input matching and an immutable audit trail.
- An explicit, attributed source review can record a bibliography-only batch as containing no facts about the paper. It creates no claim and makes no model request.
- Readers retain existing results until replacement succeeds, preserve human revisions, and support isolated paper retries. The evidence panel exposes each claim and each separate source unit.

## Software checks

| Check | Result |
| --- | --- |
| ScholarFlow backend suite, network blocked | 294 passed |
| Evidence UI tests, desktop and mobile | 14 passed |
| TypeScript and ESLint | Passed |
| Production frontend build | Passed |
| Read-only browser checks against the local production app | Passed |

The backend suite reports one existing `langchain-community` deprecation warning.

## Local data repair

The authorized repair used the existing frozen inclusion set and document versions: five PDFs and fifteen abstracts. Search and report writing were not run.

| Measure | Before | After |
| --- | --- | --- |
| Published paper extractions | 19 | 20 |
| Fields containing extracted text | 15 of 95 | 85 of 100 |
| Empty fields | 80 | 15, all with explicit abstract-scoped reasons |
| Current reading failures | — | 0 |
| Evidence quotations checked against original source offsets | — | 307 of 307 exact |

The remaining missing fields are thirteen limitations fields and two research-object/dataset fields, all from abstract-only inputs. Their reasons describe the supplied abstracts, rather than asserting that the complete papers omitted those details.

The entire repair used **35 of the authorized 40 model requests**, including failed attempts and early validation calls. Each request was capped at 4,000 output tokens. One final bibliography-only batch was reviewed locally, with its reviewer and reason recorded separately from model responses; it used zero additional model calls. The two cancelled parent runs stayed cancelled. The candidate library remained at sixty papers, and no report was created.

Quotation location establishes traceability, not scientific support. Extracted claims remain `needs_recheck`; the first-paper semantic check also recorded attributed review notes for three claims involving experiment settings, terminology, or incomplete source coverage. This session does not establish that all extracted scientific claims are correct.
