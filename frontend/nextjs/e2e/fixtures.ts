import { Page, Route } from '@playwright/test';

export const project = { id: 'p1', revision: 1, created_at: '2026-01-01T00:00:00Z', question: '多智能体如何辅助科研？',
  language: 'zh-CN', report_type: 'topic_review', sources: ['arxiv'], inclusion_criteria: [], exclusion_criteria: [], year_from: null, year_to: null };
export const plan = { id: 'plan1', project_id: 'p1', project_revision: 1, revision: 1, queries: ['multi-agent research'],
  inclusion_criteria: [], exclusion_criteria: [], subquestions: ['如何比较方法？'], approved: false };
export const run = { id: 'run1', project_id: 'p1', revision: 1, kind: 'literature_review', status: 'waiting_for_user',
  stage: 'approve_plan', quality_status: 'unchecked', pending_action: { type: 'approve_plan', plan_revision_id: 'plan1' },
  manifests: {}, request_snapshot: {}, issues: [], model_calls: 0, created_at: '2026-01-01T00:00:00Z' };
export const paper = (id: string) => ({ id, paper_id: id, project_id: 'p1', paper_version_id: 'v' + id,
  revision: 1, human_decision: 'pending', ai_recommendation: 'included', ai_reason: '主题相关', reason: '', notes: '', documents: [] as Array<{ id: string; kind: string; parse_status: string }>,
  version: { id: 'v' + id, paper_id: id, title: '科研论文 ' + id, authors: ['Fixture Author'], year: 2025,
    source: 'arxiv', source_id: id, identifiers: { arxiv: '2601.00001v1' }, abstract: 'The method uses a graph.', publication_status: 'preprint' } });

export async function mockWorkspace(page: Page, options: { conflict?: boolean; selectionGate?: boolean } = {}) {
  const state = { project: { ...project }, plans: [{ ...plan }], run: structuredClone(run), papers: [paper('a'), paper('b')],
    selections: [] as Array<{ id: string; revision: number; paper_version_ids: string[] }>, calls: [] as string[] };
  if (options.selectionGate) {
    state.plans[0].approved = true;
    state.run.stage = 'confirm_selection';
    state.run.pending_action = { type: 'confirm_selection', plan_revision_id: 'plan1' };
  }
  await page.route('**/api/scholarflow/**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/scholarflow', '');
    const method = route.request().method();
    state.calls.push(method + ' ' + path);
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/status') return respond({ demo: false, max_included_papers: 20 });
    if (path === '/projects' && method === 'GET') return respond({ items: [state.project], next_cursor: null });
    if (path === '/projects' && method === 'POST') {
      state.project = { ...state.project, ...route.request().postDataJSON() };
      return respond(state.project, 201);
    }
    if (path === '/projects/p1') return respond(state.project);
    if (path === '/projects/p1/plans' && method === 'GET') return respond({ items: state.plans });
    if (path === '/projects/p1/plans' && method === 'POST') {
      if (options.conflict) return respond({ error: { code: 'revision_conflict', message: '版本冲突，输入已保留', details: { current_revision: 2 } } }, 409);
      state.plans.unshift({ ...state.plans[0], ...route.request().postDataJSON(), id: 'plan2', revision: 2, approved: false });
      return respond(state.plans[0], 201);
    }
    if (path.endsWith('/approve')) { state.plans[0].approved = true; return respond({ id: 'approval' }); }
    if (path === '/projects/p1/papers') return respond({ items: state.papers });
    if (path.startsWith('/projects/p1/papers/') && method === 'PATCH') {
      const id = path.split('/').pop();
      const index = state.papers.findIndex(paper => paper.paper_id === id);
      const data = route.request().postDataJSON() as { decision: string; reason: string; notes: string };
      state.papers[index] = { ...state.papers[index], human_decision: data.decision, reason: data.reason, notes: data.notes, revision: 2 };
      return respond(state.papers[index]);
    }
    if (path === '/projects/p1/selections' && method === 'GET') return respond({ items: state.selections });
    if (path === '/projects/p1/selections' && method === 'POST') {
      state.selections.unshift({ ...route.request().postDataJSON(), id: 'selection1', revision: 1 });
      return respond(state.selections[0], 201);
    }
    if (path === '/projects/p1/runs') return respond(method === 'GET' ? { items: [state.run] } : state.run, method === 'GET' ? 200 : 202);
    if (path === '/runs/run1') return respond(state.run);
    if (path === '/runs/run1/actions') {
      state.run.status = 'queued'; state.run.revision += 1;
      return respond(state.run, 202);
    }
    if (path === '/runs/run1/events') return respond({ items: [], next_cursor: null });
    if (path === '/runs/run1/jobs') return respond({ items: [], model_calls: [], cost: null });
    if (path === '/projects/p1/reports' || path === '/projects/p1/extractions') return respond({ items: [] });
    if (path === '/projects/p1/reading-status' && method === 'GET') return respond({ items: [] });
    return respond({ error: { code: 'fixture_route_missing', message: method + ' ' + path } }, 404);
  });
  return state;
}
