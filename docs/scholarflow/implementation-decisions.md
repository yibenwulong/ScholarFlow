# Implementation decisions

These decisions were recorded while executing the approved v0.1 plan. Each includes its practical cost if the decision proves wrong.

## Rulings I made

1. use the approved plan's worktree setup consent; no additional permission prompt — the plan explicitly includes isolation — cost if wrong: changes remain on an isolated branch.

2. isolated .venv and repo-local uv cache replace global Anaconda writes — sandbox denied default cache, existing environment lacks json_repair/pytest-asyncio — cost if wrong: local dependency install can be repeated.

3. SQLite entities use typed JSON payloads plus FK entity_links, with repository-enforced domain types and immutable version records — spec permits nested JSON and requires source provenance — cost if wrong: add dedicated relational projections via a future migration.

4. publish an independent ancestry instead of the upstream commit graph — actual GitHub push protection reports historical LangSmith credentials; current source and attribution can be retained without those ancestors — cost if wrong: GitHub will not display original upstream commit ancestry; local upstream remains available.

5. run pytest with escalation inside the worktree — Python 3.13 creates owner-only temp directories whose ACL excludes the restricted Windows token, even under --basetemp — cost if wrong: tests execute with ordinary user filesystem access; tests remain offline.

6. plan collection expected_revision is latest plan revision (0 if empty); plan approval expected_revision refers to that immutable plan — avoids confusing project revision with plan version — cost if wrong: API client must follow the documented per-resource revision.

7. local sf_tmp_path test fixture inherits workspace ACLs instead of pytest mkdir(0700) — permits ordinary sandbox test runs on Python 3.13/Windows, preserving real SQLite and ASGI behavior — cost if wrong: test-only temporary files remain after interrupted tests.

8. wire worker/__main__.py in Task 8 with the actual research graph instead of a placeholder driver — graph is the CLI runtime dependency — cost if wrong: CLI becomes available three tasks later. All published stage results must flow through fenced successful jobs and fixed Run manifests; immutable orphan records must not be selected as latest results.

9. enable openai/azure_openai/anthropic/ollama in the new gateway and reject other providers explicitly — their SDK retry behavior is verified and old adapter support remains intact — cost if wrong: additional providers need a tested budget adapter before enabling. Task 6 RED observed missing modules, then empty-response audit regression; gateway tests 9/9 and legacy max-token tests 2/2 now pass.

10. implement the RunService before Task 9 routes — transactional input gates and resume actions are needed to test the real graph — cost if wrong: API wiring must reuse this service instead of duplicating state rules. Added an optional shared transaction to enqueue. Immutable orphan report/extraction rows from stale operations must be filtered from canonical API listings using successful artifact results and Run manifests (Task 9). Auxiliary target validation/handlers belong to their Task 9 import/check/export services.

11. expose only Run-manifest publications and explicit human saves as canonical reports/extractions/checks — stale operations may leave immutable audit rows before fencing — cost if wrong: an unbound low-level service result stays hidden until explicitly published. Visible collection revisions can skip hidden orphan sequence numbers. Added import status and selection history read routes so refresh can recover asynchronous operations and CAS cursors. Legacy validation errors remain on their original response contract. Regression: late unpublished verification could replace current quality (observed RED, now fixed). Legacy report/chat 2, WebSocket cleanup 1, WebSocket manager 3, URL safety 21 tests passed in separate processes.

12. replace remote Google Lexend with the existing local Geist font dependency and move viewport into the supported export — observed remote font timeouts prevented browser startup; local assets permit offline build and normal mobile zoom — cost if wrong: the legacy landing page typography changes. TDD RED: missing project form/plan controls; exclusion checkbox regression also observed RED. GREEN: desktop/mobile 10/10, tsc passed, lint passed with NEXT_TELEMETRY_DISABLED=1 to avoid global profile writes. First-time dev compilation needed a 30-second route transition assertion; server readiness now uses an existing static asset.

13. matrix PATCH additionally requires paper_version_id and checks it in the write transaction — extraction revisions are scoped to a paper version, so concurrent version switching can otherwise target another version with the same revision number — cost if wrong: clients must include the version they edited. Regression observed 422 before contract support; correct changed-version response is now 409. GREEN: evidence UI desktop/mobile 8/8, relevant backend 12/12, tsc passed. Mobile min-content grid overflow was reproduced by real clicks and fixed with min-width:0 (no force-click workaround).

14. align metadata to the root Apache-2.0 license and retain GPT Researcher distribution/import names and original authors — inspected upstream has the same inherited MIT metadata mismatch; source attribution and vendor licenses remain — cost if wrong: a separately named Python/npm release will need further packaging work.

15. Docker CLI is unavailable, so record actual container validation as unexecuted and use static YAML checks plus a fresh-directory native API/separate-worker live test — no claim that YAML parsing validates a container — cost if wrong: Docker-specific failures remain possible; CI now checks Compose configuration.

16. Compose targets /projects; remove the invalid browser-facing container hostname and document separate legacy UI URL configuration — the new same-origin proxy is correct, while inherited general research deployment settings differ — cost if wrong: legacy UI users need additional container configuration.

17. live provider availability, DOI recall and real-model scientific quality remain unmeasured — synthetic tests establish software contracts only and evaluation.md states this — cost if wrong: research-quality assumptions require external evaluation before reliance.

18. actual Docker build/start remains unverified locally — Docker CLI is absent and native clean-instance tests are a distinct check — cost if wrong: container-specific defects may remain.

19. Anthropic remains an explicitly documented optional installation — default adapters do not silently enable it without its package — cost if wrong: Anthropic users must install one additional dependency.

20. retain immutable unpublished orphan audit rows behind canonical publication projections — generation-fenced publications determine visible results — cost if wrong: storage grows and new read paths must use projections.

21. keep multi-user auth, OCR, collaboration and extra export styles outside v0.1 — the approved release is a trusted single-user text-PDF workflow — cost if wrong: those use cases require another release.

22. preserve original local ancestry under upstream-history and use the clean published main in the root checkout, retaining the implementation worktree and its local dependencies — GitHub rejected old upstream credential ancestors and the user wants this project usable from the original directory — cost if wrong: original-ancestry development must stay separate from public main; no history is deleted.

## Deferred minors

None reported by the independent whole-branch reviewer. Eight important findings were fixed with failing-then-passing regressions; see verification.md.
