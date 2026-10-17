# ScholarFlow

[中文说明](README-zh_CN.md)

An evidence-backed multi-agent literature research workspace for graduate students and researchers. Plan a search, confirm included papers, compare methods, inspect source passages and save a versioned report with citations that can be checked independently.

ScholarFlow is based on [GPT Researcher](https://github.com/assafelovic/gpt-researcher). It retains the existing general research entry and adds an independent scientific workflow at **`/projects`**. See [UPSTREAM.md](UPSTREAM.md) for attribution.

## What v0.1 provides

- Planner → human approval → academic retrieval → screening suggestions → human inclusion → parallel readers → writer → verifier.
- arXiv, Semantic Scholar and OpenAlex metadata; PDFs and abstract-only evidence are kept separate. A missing PDF does not discard a paper.
- Immutable PDF/abstract versions, physical PDF pages starting at 1, original quote offsets and an editable comparison matrix.
- Successful readings appear as each paper finishes. Failed papers show their own status and a single-paper retry in the evidence view; an isolated retry never starts report writing.
- The Reader checks all five fields, records source-scoped missing reasons and rejects wholly empty extractions. Successful batches survive a later batch failure; old sparse results can be refreshed per paper without clearing the matrix.
- Readers extract each paper's own facts and select numbered source units. The server saves the exact text and offsets of each unit as separate evidence; semantic support still requires review.
- Immutable report versions, per-occurrence citation checks, explicit issue-bearing draft acceptance, Markdown/DOCX/BibTeX exports from one saved version.
- SQLite jobs, checkpoints, leases and event recovery. Default: 20 included papers, two concurrent readers, 100 recorded model attempts, at most one automatic rewrite.

Execution status and quality status are separate. Source location and model support assessments help review a report; they do not establish scientific truth or guarantee exhaustive search coverage.

## Run locally

Requires Python **3.12+** and Node **22+**. Commands below use an activated virtual environment.

```bash
git clone https://github.com/yibenwulong/ScholarFlow.git
cd ScholarFlow
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,scholarflow]"
cp .env.example .env
```

Configure `.env` with a server-side model credential and `SCHOLARFLOW_MODEL=provider:model`. The new gateway supports OpenAI-compatible, Azure OpenAI, Anthropic and Ollama adapters; Anthropic requires the optional `langchain-anthropic` package. Configure any endpoint/credentials using the existing provider environment variables. Provider requests may require source API keys or encounter rate limits; the workspace records coverage/errors instead of inventing papers.

Start API and worker in two terminals from the repository root:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
python -m backend.scholarflow.worker
```

Start the frontend in another terminal:

```bash
cd frontend/nextjs
npm ci
npm run dev
```

Open **http://localhost:3000/projects**. The frontend proxy defaults to `http://127.0.0.1:8000`; set server-only `SCHOLARFLOW_API_URL` for another address. Secrets are never frontend configuration.

## Explicit demonstration without credentials

```bash
python scripts/scholarflow_demo.py --port 8000
# In a separate terminal: cd frontend/nextjs && npm run dev
```

This starts a real API and a separate durable worker with **synthetic, self-authored papers/PDFs and deterministic model responses**. The UI shows an explicit demo banner. Create a project, generate and approve a plan, select the two “Synthetic Graph Collaboration” papers, then inspect evidence and export the report. The demo is a workflow example, not a research-quality benchmark. Missing credentials in normal mode never enable these fixtures automatically.

## Containers

```bash
# Set SCHOLARFLOW_DEMO=true in .env to use the explicit demo fixtures
docker compose up --build
```

Open **`/projects`**. API and worker share a named volume containing the domain database, checkpoints, documents and exports. The frontend uses the API through its server proxy. Ports bind to localhost. This Compose configuration targets the ScholarFlow workspace; the inherited general research UI has separate public API/output URL settings. This v0.1 is a single-user application for local or trusted environments; it does not implement public multi-user authentication. See [architecture and boundaries](docs/scholarflow/architecture.md).

## Verify

```bash
GPTR_BLOCK_NETWORK=1 python -m pytest tests/scholarflow -q
cd frontend/nextjs
npm run lint
npm exec tsc -- --noEmit
npm run build
npx playwright install chromium
npm run test:e2e -- --workers=1
# Real API + separate worker browser test:
SCHOLARFLOW_LIVE_TEST=true npm run test:e2e -- e2e/scholarflow-live.spec.ts --workers=1
```

On Windows use `$env:GPTR_BLOCK_NETWORK='1'` / `$env:SCHOLARFLOW_LIVE_TEST='true'`. Existing Edge can be used with `$env:PLAYWRIGHT_CHANNEL='msedge'`. Set `PLAYWRIGHT_PYTHON` to the desired Python executable when the environment is not `.venv`. Ports are configurable with `PLAYWRIGHT_PORT` and `PLAYWRIGHT_API_PORT`.

CI retains upstream offline regressions and adds the ScholarFlow workflow and browser checks. See [evaluation protocol](docs/scholarflow/evaluation.md) for measured validation and unmeasured research-quality evaluation.

## Data and limitations

Instance data defaults to `data/scholarflow`; keep API and worker on the same `SCHOLARFLOW_DATA_DIR`. Back up the entire directory with processes stopped. Uploaded papers, generated reports, caches and credentials are excluded from Git and container build contexts. Do not distribute papers without the right to do so.

Text-layer PDFs are supported; scanned PDFs need an external OCR step. Metadata coverage and access differ by source. Exact source identifiers establish paper identity; title similarity alone does not merge papers. Restart recovery reuses committed results, but an interrupted remote request may be repeated and billed before its response was committed. Unknown token usage/prices stay unknown. URL validation includes public-address checks and redirect validation, with a remaining DNS re-resolution/rebinding limitation.

## Project walkthrough and interview guide

The [Chinese interview guide](docs/scholarflow/interview-guide.md) covers the project workflow, résumé examples, module explanations, 24 technical questions, eight engineering retrospectives, source navigation and demo troubleshooting. Download the [PDF](docs/scholarflow/ScholarFlow项目讲解与面试手册.pdf) or [editable Word document](docs/scholarflow/ScholarFlow项目讲解与面试手册.docx). It distinguishes implemented behavior and recorded engineering checks from research quality that has not yet been measured.

## License and contributions

Apache-2.0, following the upstream root [LICENSE](LICENSE). Upstream authorship and third-party notices are retained; inherited MIT metadata has been aligned with that root license. See [contributing](CONTRIBUTING.md), [security](SECURITY.md), and [upstream attribution](UPSTREAM.md). No PyPI or npm publication is part of this release.
