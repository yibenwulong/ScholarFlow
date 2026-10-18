# ScholarFlow v0.1 architecture

The Next workspace calls only the same-origin `/api/scholarflow` proxy. It forwards an allowlist of domain paths to server-only `SCHOLARFLOW_API_URL` and preserves status, queries, idempotency keys, range headers and binary bodies. `/api/v1` is independent of the upstream report/chat/WebSocket contracts.

FastAPI and one separate worker share `SCHOLARFLOW_DATA_DIR`: domain SQLite, checkpoint SQLite, content-addressed documents and exports. SQLite transactions are short; external calls run outside them. The supported deployment is one trusted user and one worker process, with default reader concurrency two. Authentication and public multi-tenant hosting are outside v0.1.

## Human decisions and frozen inputs

Planning needs only a project. Search requires an approved exact PlanRevision. Analysis requires that plan and a frozen explicit SelectionSnapshot. Pending candidates outside the selected subset do not block it. AI recommendations and human decisions are separate fields.

Run startup snapshots freeze the project, model/prompt configuration and limits. Phase manifests append the approved plan, selection, documents, extractions and reports. They cannot be replaced. Changing fixed inputs requires a new Run. Matrix updates validate both extraction revision and selected PaperVersion.

## Source and report provenance

Paper identity is separate from editions such as arXiv v1/v2 and a DOI publication. Exact identifiers establish relations; title similarity does not silently merge papers. Metadata records remain independent of full-text access.

Each source retains its normalized response snapshot. An availability projection can fill missing abstracts/PDF links from another source for the exact same edition, without changing the original PaperVersion. Abstract documents bind the source record that actually supplied the text. Document-preparation cache inputs include source-record IDs. New research runs freeze published human matrix revision IDs and reuse them only for matching document inputs; later corrections do not replace the earlier run's inputs.

DocumentVersion retains content hash and the original PDF/abstract. Passages preserve offsets and physical PDF pages starting at one; abstracts use null page numbers. Quote normalization maps whitespace/line hyphens back to original offsets and never removes digits or negation. Reader coverage records every supplied fragment. Scanned/encrypted/invalid documents have explicit statuses and cannot fabricate passages.

Claims and evidence form a source chain. Report citations bind each occurrence to a block, exact text span, claim revisions and evidence IDs. Bibliographic metadata is snapshotted. Report edits create new versions; later checks and human acceptances append records. Unchanged paragraph checks may be projected forward, while changed spans require rechecking. Matrix/source changes mark related reports outdated without mutating historical text or old source URLs.

Exports render numbering once from the saved report's first citation order. Markdown, DOCX and BibTeX use that version; only actually cited papers appear in bibliography output. Problem drafts require acceptance of that exact version/check pair and retain a draft marker and source version audit.

The report editor supports editing text and saving new versions, with read-only inspection of citations and their available source evidence. Users do not maintain internal citation bindings, and the report UI provides no manual rebinding controls. Changed text does not inherit prior approval. System-generated associations can still be incomplete; a recheck evaluates the saved version but does not rewrite text or repair missing associations. Saving locks editing until the new version loads. Every explicit recheck is a fresh operation; recovery may reuse that operation's result. A new export requires the latest published check, and draft files retain occurrence warnings, the full issue appendix and stored search provenance. Existing exported files remain immutable historical artifacts.

## Recovery and budgets

Driver and artifact jobs have owner, attempt and expiring leases. Child jobs use a separate concurrency allowance. Successful artifacts are reused by immutable input/configuration hashes. Run execution generations fence takeover, and LangGraph checkpoint threads use `run_id:generation`; old checkpoint writes cannot overwrite a new generation.

Immutable records may be created before a stale task loses its lease. Canonical listings therefore follow fenced Run publications and explicit human saves rather than “most recently created” rows. Cancellation blocks later formal publication. Event sequence numbers are durable; the UI deduplicates and resumes HTTP event reads independently of task execution.

Each fenced successful `read_paper` artifact also publishes its matching paper/document extraction, including work committed before a parent run was cancelled. Ordinary Reader errors are isolated per paper: other readings finish, and a partial batch waits for review before report writing. The evidence view polls results and individual statuses independently. Its single-paper retry creates a `paper_read` run with the failed job's frozen inputs; source-level transactional deduplication prevents parallel retries. Generic run actions allow only cancellation for this kind. Version and human-edit guards run before and after reading. This auxiliary run never resumes its parent or calls the Writer.

The Reader requires every comparison field to be checked, with an explicit missing reason for each empty field; an entirely empty paper is a reading failure. A missing value in a legacy extraction means incomplete extraction, not evidence that the paper did not report it. The UI distinguishes that state from absence in the exact PDF/abstract text that was read. The reader version enters paper and batch cache keys, allowing old sparse AI results to be refreshed while retaining published human revisions. Each successful `read_paper_batch` artifact binds an immutable response audit to the exact project, model, documents and fragment hashes. Only fenced, validated batch results are reused; raw audit rows alone cannot authorize reuse. A transient batch failure retries that batch without multiplying retries at the paper level.

Reading extracts facts from the selected paper independently of the project's topic. The model receives numbered, contiguous source units and selects their IDs for each claim. The server creates a separate evidence item for each selected unit using its exact original text and character offsets. It never asks the model to reconstruct or join quotation text. Unknown unit IDs are rejected. Source location is deterministic; whether a claim is supported by those sources still requires semantic review.

Reader compatibility handling separates an unbound absence entry from a scientific claim only when the same field already has an explicit missing reason. It never invents a value or source anchor. A response rejected by an earlier parser can be revalidated inside a new leased batch operation only when a failed batch job and its original audit match the complete frozen input. The original audit remains unchanged; the new audit links to it and records normalization steps. Publication still requires the new batch and paper artifacts to finish under their ownership fences.

An explicit `source_review` can record that a checked batch contains no extractable facts about the paper, such as pages containing only bibliography entries. Its audit records the reviewer and reason, five empty field arrays and batch-scoped missing reasons. This review runs as a separate leased batch operation; the paper still uses its successfully read body batches and their original evidence.

Model attempts are reserved transactionally before dispatch. The legacy helper retains its default retry behavior for existing callers; ScholarFlow uses one helper attempt and disables known SDK retries, leaving audited retries to jobs. OpenAI-compatible, Azure OpenAI, Anthropic and Ollama are the enabled new gateway adapters. Unknown token usage/prices remain unknown. An uncommitted response may be repeated after a crash and incur additional billing.

## Boundaries

No OCR, systematic-review completeness guarantee, automated meta-analysis, or multi-user authorization is provided. Model assessments do not establish scientific truth. Download checks validate public URLs and each redirect, with a remaining DNS re-resolution/rebinding limitation. Keep the service local/trusted, use lawful source access, and back up the whole data directory with writers stopped. Do not commit runtime data or keys.
