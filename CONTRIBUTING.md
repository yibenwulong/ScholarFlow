# Contributing to ScholarFlow

Open issues and pull requests at https://github.com/yibenwulong/ScholarFlow. Describe the research task, the observed behavior and a minimal synthetic reproduction. Do not attach private papers, keys, databases or generated reports containing personal data.

Use Python 3.12+ and Node 22+. Install `.[test,scholarflow]`, run the offline ScholarFlow tests and the relevant upstream regressions. Legacy tests that mutate imported modules should run in separate processes on Windows; Linux CI retains upstream's isolated `--forked` suite.

Frontend changes require lint, TypeScript, build and the relevant Playwright tests. `SCHOLARFLOW_LIVE_TEST=true` starts a fresh real API and separate worker with explicit synthetic fixtures. Document checks that your environment cannot run.

Preserve immutable source/report versions, per-occurrence citations, human gates, CAS revisions and fenced result publication. Add a regression test for changes to these contracts. Provider adapters must preserve metadata without requiring a PDF; model retries must be counted before dispatch and may not hide SDK retries.

Keep new workflows free of upstream account-specific deployment, publication tokens and automatic infrastructure changes. Preserve Apache-2.0 and third-party attribution. Submit code as a pull request; package publishing is not configured.
