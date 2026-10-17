# Partial Reading Results and Single-Paper Retry Implementation Plan

> **For agentic workers:** Use the established test-driven workflow, implementing the three bounded tasks below in the existing isolated worktree.

**Goal:** Keep successful paper readings visible when another paper fails, and retry only the selected failed paper.

**Architecture:** Treat each successfully finished, fenced `read_paper` artifact as a publishable reading result. Ordinary paper errors stop report generation with a recoverable review state while other papers finish. A separate `paper_read` auxiliary run retries frozen inputs without resuming the original research run or starting the Writer.

**Tech Stack:** Existing FastAPI, SQLite, LangGraph, Next.js and Playwright; no new dependencies.

**Spec:** User-confirmed behavior in this conversation: retain and display successes; identify each failed paper; retry only that paper.

## Global Constraints

- No new live model requests during implementation or verification.
- Keep cancelled runs cancelled; old successful artifacts remain eligible for display.
- Keep project, paper version, documents and original request snapshot fixed for retries.
- Respect lease, cancellation and budget control signals; do not mistake them for ordinary paper errors.
- Do not expose uncommitted extraction rows or overwrite human revisions silently.
- Do not automatically generate or rewrite a report after an isolated retry.

## Interfaces

- `GET /projects/{project_id}/reading-status`: paged `items` containing `paper_version_id`, `source_run_id`, `source_job_id`, `status`, `extraction_id`, `error`, `can_retry`, `active_retry_run_id`, optional `retry_block_reason`.
- `POST /projects/{project_id}/reading-retries`: body `{source_job_id}`, normal `Idempotency-Key`, response `{run}`. The run has `kind=paper_read` and targets the original failed Reader job.
- Block a new retry when the same source already has an active or successful retry, even with another idempotency key.
- An ordinary batch reading failure ends in `needs_review` with `pending_action.type=paper_read_failures`; never publish an incomplete manifest as if every selected paper had been read.

## Review Focus

1. One failed reader does not cancel other ordinary readers or launch the Writer.
2. Old cancelled runs expose only genuinely committed successful artifacts, including legal cached results from another run.
3. Different request keys and delayed UI refresh do not create duplicate paid retries.
4. Paper version replacement or a human edit before/during a retry cannot silently replace canonical human work.
5. Background polling never resets unsaved matrix edits.

## Task 1: Isolate ordinary paper failures

**Files:** `backend/scholarflow/workflows/nodes.py`, `tests/scholarflow/test_partial_reading_workflow.py`.

- [x] Write and observe failing tests for partial/all reader failure, preserving global control errors and cancellation.
- [x] Catch only ordinary domain reading errors within individual tasks; wait for other papers and record explicit failed paper IDs.
- [x] Stop before writing a report when any reader failed; keep the existing successful full-batch path.
- [x] Run the new tests and existing workflow gate/recovery tests.

## Task 2: Publish per-paper successes and provide scoped retries

**Files:** `domain/runs.py`, `services/runs.py`, new `services/readings.py`, `persistence/projections.py`, `api/evidence.py`, `worker/auxiliary.py`, new `tests/scholarflow/test_paper_readings.py`.

- [x] Observe RED for legacy partial results, frozen single-paper retry, provenance, duplicate requests and stale/human-edit guards.
- [x] Expose validated succeeded artifacts through the existing extraction projection; do not mutate old runs for recovery.
- [x] Implement the interfaces above and the Reader-only auxiliary execution, preserving reusable artifact inputs.
- [x] Verify retry success/failure/retry-again, cross-project rejection, cancellation and no unrelated model calls.

## Task 3: Show progress and single-paper retry in the matrix view

**Files:** `components/scholarflow/EvidencePanel.tsx`, new focused status component if needed, `types/scholarflow.ts`, `lib/scholarflow/api.ts`, related workspace/progress copy, new `e2e/scholarflow-paper-readings.spec.ts`.

- [x] Observe RED for partial display, polling, scoped retry, duplicate submission and edit preservation.
- [x] Poll extraction/status independently; preserve existing results across temporary failures.
- [x] Show failed titles and causes, one retry action per eligible paper, and immediate submission locking.
- [x] Verify desktop/mobile cases, TypeScript and lint.

## Integration and Delivery

- [x] Review the combined change and run the complete ScholarFlow backend suite once the implementation is stable.
- [ ] Build the production frontend; preserve local data and credentials.
- [ ] Restart only this checkout's verified services when no task is running.
- [ ] Read-only smoke check: the historical 17 successes remain visible, and the latest 20-paper run displays 19 successes and one failure. Do not click retry.
- [ ] Commit and normally push source/tests/documentation to the user's existing GitHub repository; never include local papers or diagnostic data.
