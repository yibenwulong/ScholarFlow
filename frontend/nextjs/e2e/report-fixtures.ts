import { Page } from '@playwright/test';
import { mockWorkspace, run } from './fixtures';

export async function mockReport(page: Page, options: { conflict?: boolean; reconnect?: boolean } = {}) {
  await mockWorkspace(page);
  const text = 'The method uses a graph.';
  const version = { id: 'report1', revision: 1, project_id: 'p1', run_id: 'run1', title: '研究报告', created_at: '2026-01-01T00:00:00Z',
    blocks: [{ id: 'b1', heading: '方法', text: text + ' [@sf_one]', claims: [] }, { id: 'b2', heading: '分歧', text: 'The method does not use a graph. [@sf_one]', claims: [] }],
    bindings: [{ key: 'sf_one', paper_version_id: 'va', bibliography_snapshot: { title: '科研论文 a' } }],
    occurrences: [{ id: 'occ1', block_id: 'b1', token_index: 0, claim_revision_ids: ['claim1'], evidence_ids: ['e1'], claim_start: 0, claim_end: text.length },
      { id: 'occ2', block_id: 'b2', token_index: 0, claim_revision_ids: ['claim2'], evidence_ids: ['e2'], claim_start: 0, claim_end: 30 }], manifest: {} };
  const verification = { id: 'check1', report_version_id: 'report1', quality_status: 'issues_present', checker: 'fixture', checks: [
    { occurrence_id: 'occ1', status: 'supported', reason: '第一处有依据' }, { occurrence_id: 'occ2', status: 'conflicting', reason: '第二处否定了原文' }] };
  const currentRun = { ...run, status: 'needs_review', stage: 'finish', quality_status: 'issues_present',
    pending_action: { type: 'review_report', report_version_id: 'report1', verification_record_id: 'check1' } as { type: string; report_version_id: string; verification_record_id: string } | null,
    manifests: {} as Record<string, unknown> };
  const state = { version, verification, currentRun, after: [] as string[], accepted: null as Record<string, unknown> | null };
  await page.route('**/api/scholarflow/projects/p1/reports', async route => {
    if (route.request().method() === 'POST') {
      if (options.conflict) return route.fulfill({ status: 409, json: { error: { code: 'revision_conflict', message: '报告有新修订' } } });
      const data = route.request().postDataJSON(); state.version = { ...version, ...data.draft, id: 'report2', revision: 2, previous_id: 'report1' };
      return route.fulfill({ status: 201, json: state.version });
    }
    return route.fulfill({ json: { items: [state.version] } });
  });
  await page.route('**/api/scholarflow/reports/*', route => route.fulfill({ json: { version: state.version, verification: state.verification,
    outdated: false, citations: state.version.occurrences, references: [{ number: 1, key: 'sf_one', title: '科研论文 a' }], rendered_markdown: '' } }));
  await page.route('**/api/scholarflow/reports/*/exports', route => route.fulfill({ json: { items: [] } }));
  await page.route('**/api/scholarflow/reports/*/citation-options', route => route.fulfill({ json: { items: [
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'e1', quote: text, source_scope: 'fulltext', locator_status: 'located' } ] } }));
  await page.route('**/api/scholarflow/projects/p1/runs', route => route.fulfill({ json: { items: [state.currentRun] } }));
  await page.route('**/api/scholarflow/runs/run1', route => route.fulfill({ json: state.currentRun }));
  await page.route('**/api/scholarflow/runs/run1/actions', route => {
    state.accepted = route.request().postDataJSON() as Record<string, unknown>;
    state.currentRun.status = 'completed'; state.currentRun.pending_action = null; state.currentRun.manifests.acceptance_id = 'accept1';
    return route.fulfill({ status: 202, json: state.currentRun });
  });
  await page.route('**/api/scholarflow/runs/run1/events*', route => {
    const after = new URL(route.request().url()).searchParams.get('after') || '0'; state.after.push(after);
    if (options.reconnect && state.after.length === 1) return route.fulfill({ status: 503, json: {} });
    const sequence = after === '0' ? [1, 2] : [2, 3];
    return route.fulfill({ json: { items: sequence.map(seq => ({ id: 'event' + seq, seq, type: 'stage', stage: 'read', data: {}, run_id: 'run1' })), next_cursor: null } });
  });
  return state;
}
