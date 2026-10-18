# Local verification record

Date: 2026-10-15. Windows, Python 3.13.5, Node 24.19.0, Next 14.2.35, isolated Edge via Playwright. Tests use distributable synthetic fixtures and no paid model credentials.

Current report UI note (2026-10-18): manual citation rebinding controls have been removed. Users can edit and save report text, request a fresh check, and inspect available sources read-only; they do not maintain internal citation bindings. Incomplete automatic associations remain a limitation, and rechecking does not repair them. Rebinding mentioned in the historical results below describes the earlier implementation, not the current user workflow.

| Check | Observed result |
| --- | --- |
| `python -m pytest tests/scholarflow -q` with `GPTR_BLOCK_NETWORK=1` | 108 passed; one inherited LangChain deprecation warning |
| Entire test collection | 682 collected without collection errors; not a claim that all legacy tests were executed |
| Legacy report/chat routes, URL safety, max-token handling, Semantic Scholar retrieval/sort, OpenAlex malformed metadata | 37 passed in separate Python processes |
| `npm run lint`, `npm exec tsc -- --noEmit` | Passed |
| `npm run build` | Production build passed; no remote font download required |
| Mock API Playwright tests, desktop/mobile | 34 passed in 50.6s; 2 live cases explicitly skipped |
| Live API Playwright tests, desktop/mobile | 2 passed in 55.0s; real same-origin proxy, API and separate worker, fresh temporary instance; includes explicit citation rebinding and issue-bearing export |
| Wheel build and archive inspection | Built successfully; backend modules and migration SQL present; local environments/caches/credentials absent |
| Compose and workflow YAML | Static parsing passed; API/worker share a data volume, frontend uses the server-only proxy address |

The browser flow creates a project, approves a plan, selects two self-authored papers, reads their PDFs, creates a report, saves an edit, checks that exact version, accepts a problem draft and downloads a marked draft. Screenshots of desktop/mobile layouts were visually inspected. Generated PDFs, screenshots, databases, caches and logs remain excluded from source publication.

An independent whole-branch review found eight important issues. Each was reproduced before implementation and fixed with regression coverage: fresh explicit rechecks after human source review, frozen human matrix corrections on regeneration, pending-save input protection, approved screening criteria, edited citation rebinding, exported issue/search appendices, rejection of old passing verification during new export, and missing access metadata from later sources. The final backend and browser suites above passed after the fixes. No reviewer minor findings were deferred. This does not replace a security audit or research-quality evaluation.

## Acceptance evidence

| Design scenario | Coverage |
| --- | --- |
| Exact plan approval and explicit inclusion gates | `test_workflow_gates.py`, `test_projects_api.py`, live browser flow |
| AI/human decisions and pending candidates | `test_library.py`, `test_selections.py`, library browser tests |
| Metadata identity and separate editions | Provider/library tests and import API tests |
| Abstract-only evidence and conflicting numbers/negation | `test_evidence_location.py`, `test_report_verification.py` |
| Physical PDF page/offset and immutable old document | Document/file API tests and evidence browser tests |
| Model attempt budget and automatic rewrite cap | Gateway and workflow gate tests |
| Matrix/report edits, CAS conflicts, retained drafts | Extraction/report API tests and evidence/report browser tests |
| Six completed artifacts reused after worker recreation | `test_job_recovery.py`; real graph restart/takeover separately covered by `test_workflow_recovery.py` |
| Same saved version across Markdown/DOCX/BibTeX | Export tests and complete ASGI integration test |
| Desktop/mobile saved state and progress | Same API contract in both live device profiles; HTTP event recovery tested separately |
| Inherited entry points/contracts | Legacy route, WebSocket and retriever regression tests; original native entry retained |

## Checks not performed

Docker CLI is absent on this machine. `docker compose config`, image builds and actual container startup were **not** executed locally. YAML parsing does not replace Docker schema or runtime validation; CI includes the Compose configuration command. The fresh-directory native API/worker flow passed, which does not establish container compatibility.

Live academic provider queries, paid model calls and human research-quality evaluation were not run. There are no measured accuracy, recall or time-saving scores. See [evaluation.md](evaluation.md) for the three-topic evaluation protocol. GitHub-hosted CI results are separate from this local record.
