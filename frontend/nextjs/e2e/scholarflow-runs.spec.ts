import { test, expect, type Page } from '@playwright/test';
import type { Run } from '../types/scholarflow';
import { mockWorkspace, run } from './fixtures';

function researchRun(overrides: Partial<Run> = {}): Run {
  return { ...run, kind: 'literature_review', target_id: null, start_at: 'analyze', id: 'analysis1', status: 'running', stage: 'write',
    pending_action: null, created_at: '2026-01-02T00:00:00Z',
    manifests: { plan: { plan_revision_id: 'plan1' }, selection: { selection_id: 'old-selection', paper_version_ids: ['va', 'vb'] } },
    ...overrides };
}

async function mockRuns(page: Page, initialRuns: Run[], options: { delayedListing?: boolean } = {}) {
  const workspace = await mockWorkspace(page, { selectionGate: true });
  workspace.selections.push({ id: 'selection0', revision: 1, paper_version_ids: ['va', 'vb'] });
  const state = { runs: initialRuns, created: [] as Run[], runReads: 0,
    actions: [] as Array<{ id: string; body: Record<string, unknown> }>, workspace };
  await page.route('**/api/scholarflow/projects/p1/runs', async route => {
    if (route.request().method() === 'GET') {
      state.runReads += 1;
      return route.fulfill({ json: { items: state.runs } });
    }
    const data = route.request().postDataJSON();
    const selection = workspace.selections.find(item => item.id === data.selection_id)!;
    const created = researchRun({ id: 'created-' + (state.created.length + 1), status: 'queued', stage: 'analyze',
      created_at: '2026-01-05T00:00:00Z', manifests: { plan: { plan_revision_id: data.plan_revision_id },
        selection: { selection_id: selection.id, paper_version_ids: selection.paper_version_ids } } });
    state.created.push(created);
    if (!options.delayedListing) state.runs.unshift(created);
    return route.fulfill({ status: 202, json: created });
  });
  await page.route('**/api/scholarflow/runs/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').pop();
    return route.fulfill({ json: [...state.runs, ...state.created].find(item => item.id === id) });
  });
  await page.route('**/api/scholarflow/runs/*/events**', route => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route('**/api/scholarflow/runs/*/actions', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!;
    const body = route.request().postDataJSON();
    state.actions.push({ id, body });
    const current = state.runs.find(item => item.id === id)!;
    current.status = 'queued'; current.pending_action = null; current.revision += 1;
    const selection = workspace.selections.find(item => item.id === body.selection_id);
    if (selection) current.manifests.selection = { selection_id: selection.id, paper_version_ids: selection.paper_version_ids };
    return route.fulfill({ status: 202, json: current });
  });
  return state;
}

const progressPanel = (page: Page) => page.locator('section').filter({ has: page.getByRole('heading', { name: '研究运行进度', exact: true }) });
const analysisButton = (page: Page) => page.getByRole('button', { name: '确认纳入并分析', exact: true });

test('正在写作的运行不会被较新排队导入与分析遮挡', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'queued', stage: 'import', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ id: 'queued1', status: 'queued', stage: 'analyze', created_at: '2026-01-03T00:00:00Z' }),
    researchRun(),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('执行：研究中', { exact: true })).toBeVisible();
  await expect(progressPanel(page).getByText('综合写作', { exact: true })).toBeVisible();
  await expect(page.locator('.sf-container > .sf-between').first().getByText('研究中', { exact: true })).toBeVisible();
});

test('没有执行中任务时展示最早排队项', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'queued', stage: 'import', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ id: 'queued1', status: 'queued', stage: 'documents' }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('准备原文', { exact: true })).toBeVisible();
  await expect(page.locator('.sf-container > .sf-between').first().getByText('排队中', { exact: true })).toBeVisible();
});

test('没有活跃任务时较新的导入不会遮挡失败研究和重试入口', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ status: 'failed', issues: [{ code: 'model_request_failed', message: '模型连接中断' }] }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('模型连接中断', { exact: true })).toBeVisible();
  await expect(progressPanel(page).getByRole('button', { name: '重试失败阶段' })).toBeVisible();
  await expect(page.locator('.sf-container > .sf-between').first().getByText('执行失败', { exact: true })).toBeVisible();
});

for (const status of ['failed', 'waiting_for_user', 'needs_review'] as const) {
  test(`旧${status}研究不会盖过较新完成研究`, async ({ page }) => {
    await mockRuns(page, [
      researchRun({ id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-05T00:00:00Z' }),
      researchRun({ id: 'finished1', status: 'completed', stage: 'finish', created_at: '2026-01-04T00:00:00Z' }),
      researchRun({ status }),
    ]);
    await page.goto('/projects/p1?tab=papers');
    await expect(progressPanel(page).getByText('执行：已完成', { exact: true })).toBeVisible();
    await expect(progressPanel(page).getByText('结束', { exact: true })).toBeVisible();
    await expect(page.locator('.sf-container > .sf-between').first().getByText('已完成', { exact: true })).toBeVisible();
  });
}

test('较新待处理报告复核不会被已完成研究和导入遮挡', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-05T00:00:00Z' }),
    researchRun({ id: 'check1', kind: 'report_check', target_id: 'report1', status: 'needs_review', stage: 'report_check', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ status: 'completed', stage: 'finish' }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('执行：待复核', { exact: true })).toBeVisible();
  await expect(progressPanel(page).getByText('报告复核', { exact: true })).toBeVisible();
});

test('旧报告复核不会盖过较新完成研究', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-05T00:00:00Z' }),
    researchRun({ status: 'completed', stage: 'finish', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ id: 'check1', kind: 'report_check', target_id: 'old-report', status: 'needs_review', stage: 'report_check' }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('结束', { exact: true })).toBeVisible();
});

test('后续检查完成后不再回显旧失败检查', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-07T00:00:00Z' }),
    researchRun({ id: 'latest-check', kind: 'report_check', target_id: 'report1', status: 'completed', stage: 'report_check', created_at: '2026-01-06T00:00:00Z' }),
    researchRun({ id: 'old-check', kind: 'report_check', target_id: 'report1', status: 'failed', stage: 'report_check', created_at: '2026-01-05T00:00:00Z' }),
    researchRun({ status: 'completed', stage: 'finish', created_at: '2026-01-04T00:00:00Z' }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await expect(progressPanel(page).getByText('执行：已完成', { exact: true })).toBeVisible();
  await expect(progressPanel(page).getByText('结束', { exact: true })).toBeVisible();
});

for (const status of ['queued', 'running'] as const) {
  test(`${status}的同计划同集合分析按版本集合判重并说明查看入口`, async ({ page }) => {
    const state = await mockRuns(page, [researchRun({ status, manifests: {
      plan: { plan_revision_id: 'plan1' }, selection: { selection_id: 'another-selection', paper_version_ids: ['vb', 'va'] },
    } })]);
    await page.goto('/projects/p1?tab=papers');
    await expect(analysisButton(page)).toBeDisabled();
    await expect(page.getByRole('status').filter({ hasText: status === 'queued' ? '排队' : '分析' })).toBeVisible();
    await expect(page.getByRole('link', { name: '查看证据与矩阵', exact: true })).toHaveAttribute('href', '/projects/p1?tab=evidence');
    await expect(page.getByRole('link', { name: '查看研究报告', exact: true })).toHaveAttribute('href', '/projects/p1?tab=report');
    expect(state.created).toHaveLength(0);
    expect(state.workspace.calls.filter(call => call === 'POST /projects/p1/selections')).toHaveLength(0);
  });
}

test('分析提交成功但列表尚未刷新时仍保持防重复并在终态后解除', async ({ page }) => {
  const state = await mockRuns(page, [], { delayedListing: true });
  await page.goto('/projects/p1?tab=papers');
  await analysisButton(page).click();
  await expect.poll(() => state.runReads).toBeGreaterThan(2);
  await expect(analysisButton(page)).toBeDisabled();
  expect(state.created).toHaveLength(1);
  expect(state.workspace.calls.filter(call => call === 'POST /projects/p1/selections')).toHaveLength(1);
  state.created[0].status = 'completed';
  state.runs.push(state.created[0]);
  await expect(analysisButton(page)).toBeEnabled();
});

test('更改版本选择集合允许创建不同输入的分析', async ({ page }) => {
  const state = await mockRuns(page, [researchRun()]);
  await page.goto('/projects/p1?tab=papers');
  await expect(analysisButton(page)).toBeDisabled();
  await page.getByRole('checkbox', { name: '纳入 科研论文 b', exact: true }).uncheck();
  await expect(analysisButton(page)).toBeEnabled();
  await analysisButton(page).click();
  await expect.poll(() => state.created.length).toBe(1);
  expect(state.workspace.selections[0].paper_version_ids).toEqual(['va']);
});

for (const status of ['completed', 'failed', 'cancelled', 'needs_review'] as const) {
  test(`${status}不会永久阻止同输入重新分析`, async ({ page }) => {
    await mockRuns(page, [researchRun({ status })]);
    await page.goto('/projects/p1?tab=papers');
    await expect(analysisButton(page)).toBeEnabled();
  });
}

test('不同批准计划的相同集合允许新建分析', async ({ page }) => {
  await mockRuns(page, [researchRun({ manifests: { plan: { plan_revision_id: 'old-plan' },
    selection: { selection_id: 'old-selection', paper_version_ids: ['va', 'vb'] } } })]);
  await page.goto('/projects/p1?tab=papers');
  await expect(analysisButton(page)).toBeEnabled();
});

for (const status of ['failed', 'completed', 'queued', 'running'] as const) {
  test(`较新${status}研究存在时不会继续历史纳入任务`, async ({ page }) => {
    const state = await mockRuns(page, [
      researchRun({ id: 'import1', kind: 'import', status: 'completed', created_at: '2026-01-05T00:00:00Z' }),
      researchRun({ id: 'newer1', status, created_at: '2026-01-04T00:00:00Z' }),
      researchRun({ id: 'old-gate', status: 'waiting_for_user', stage: 'confirm_selection',
        pending_action: { type: 'confirm_selection' }, manifests: { plan: { plan_revision_id: 'plan1' } } }),
    ]);
    await page.goto('/projects/p1?tab=papers');
    await page.getByRole('checkbox', { name: '纳入 科研论文 b', exact: true }).uncheck();
    await analysisButton(page).click();
    await expect.poll(() => state.created.length).toBe(1);
    expect(state.actions).toHaveLength(0);
    expect(state.workspace.selections[0].paper_version_ids).toEqual(['va']);
  });
}

test('最新研究等待纳入时较新的导入不妨碍继续对应任务', async ({ page }) => {
  const state = await mockRuns(page, [
    researchRun({ id: 'import1', kind: 'import', status: 'completed', created_at: '2026-01-05T00:00:00Z' }),
    researchRun({ id: 'latest-gate', status: 'waiting_for_user', stage: 'confirm_selection', created_at: '2026-01-04T00:00:00Z',
      pending_action: { type: 'confirm_selection' }, manifests: { plan: { plan_revision_id: 'plan1' } } }),
    researchRun({ id: 'failed1', status: 'failed' }),
  ]);
  await page.goto('/projects/p1?tab=papers');
  await analysisButton(page).click();
  await expect.poll(() => state.actions.length).toBe(1);
  expect(state.actions[0].id).toBe('latest-gate');
  expect(state.created).toHaveLength(0);
});

for (const status of ['completed', 'queued'] as const) {
  test(`批准当前计划不会继续被较新${status}研究取代的历史等待任务`, async ({ page }) => {
    const state = await mockRuns(page, [
      researchRun({ id: 'newer1', status, created_at: '2026-01-04T00:00:00Z' }),
      researchRun({ id: 'old-gate', status: 'waiting_for_user', stage: 'approve_plan',
        pending_action: { type: 'approve_plan', plan_revision_id: 'plan1' }, manifests: {} }),
    ]);
    await page.goto('/projects/p1?tab=plan');
    await page.getByRole('button', { name: '批准此计划', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '计划已批准' })).toBeVisible();
    expect(state.actions).toHaveLength(0);
    expect(state.workspace.calls).toContain('POST /projects/p1/plans/plan1/approve');
  });
}

test('较旧研究仍在执行时也禁止重复生成计划', async ({ page }) => {
  await mockRuns(page, [
    researchRun({ id: 'newer1', status: 'completed', created_at: '2026-01-04T00:00:00Z' }),
    researchRun({ id: 'active1', status: 'running', created_at: '2026-01-03T00:00:00Z' }),
    researchRun({ id: 'old-gate', status: 'waiting_for_user', stage: 'approve_plan',
      pending_action: { type: 'approve_plan', plan_revision_id: 'plan1' }, manifests: {} }),
  ]);
  await page.goto('/projects/p1?tab=plan');
  await expect(page.getByRole('button', { name: '生成检索计划', exact: true })).toBeDisabled();
});

test('最新研究确实等待批准时仍可继续对应任务', async ({ page }) => {
  const state = await mockRuns(page, [
    researchRun({ id: 'latest-gate', status: 'waiting_for_user', stage: 'approve_plan', created_at: '2026-01-04T00:00:00Z',
      pending_action: { type: 'approve_plan', plan_revision_id: 'plan1' }, manifests: {} }),
    researchRun({ id: 'finished1', status: 'completed' }),
  ]);
  await page.goto('/projects/p1?tab=plan');
  await page.getByRole('button', { name: '批准此计划', exact: true }).click();
  await expect.poll(() => state.actions.length).toBe(1);
  expect(state.actions[0].id).toBe('latest-gate');
  expect(state.actions[0].body.action).toBe('continue');
});
