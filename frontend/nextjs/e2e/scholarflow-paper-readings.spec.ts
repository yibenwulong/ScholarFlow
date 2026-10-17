import { test, expect, Page } from '@playwright/test';
import { mockWorkspace, paper } from './fixtures';
import type { Extraction } from '../types/scholarflow';
import { createServer } from 'node:http';

type Reading = {
  paper_version_id: string; source_run_id: string; source_job_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  extraction_id: string | null; error: { code: string; message: string } | null;
  can_retry: boolean; active_retry_run_id: string | null; retry_block_reason?: string | null;
};

function extraction(versionId: string, method = '已保存的阅读结果'): Extraction {
  const empty = { value: null, claim_ids: [], evidence_ids: [], review_status: 'needs_recheck' as const };
  return { id: 'ex-' + versionId, revision: 1, created_at: '2026-01-02T00:00:00Z', project_id: 'p1',
    paper_version_id: versionId, document_version_ids: ['doc-' + versionId], origin: 'ai', previous_id: null,
    fields: { research_question: { ...empty }, method: { ...empty, value: method }, dataset: { ...empty },
      findings: { ...empty }, limitations: { ...empty } } };
}

function reading(versionId: string, status: Reading['status']): Reading {
  return { paper_version_id: versionId, source_run_id: 'run1', source_job_id: 'job-' + versionId, status,
    extraction_id: status === 'succeeded' ? 'ex-' + versionId : null,
    error: status === 'failed' ? { code: 'invalid_model_output', message: 'Reader returned invalid structured fields; review or retry this paper' } : null,
    can_retry: status === 'failed', active_retry_run_id: null };
}

async function mockReadings(page: Page, count = 2) {
  const workspace = await mockWorkspace(page);
  workspace.run.status = 'cancelled'; workspace.run.stage = 'read';
  workspace.papers = Array.from({ length: count }, (_, index) => paper(String(index + 1)));
  const retryRun = { ...workspace.run, id: 'retry1', kind: 'paper_read', target_id: 'job-v' + count,
    status: 'queued', stage: 'read', pending_action: null, created_at: '2026-01-03T00:00:00Z' };
  const state = { workspace, readings: workspace.papers.map((item, index) => reading(item.paper_version_id, index === count - 1 ? 'failed' : 'succeeded')),
    extractions: workspace.papers.slice(0, -1).map(item => extraction(item.paper_version_id)),
    statusError: false, extractionError: false, posts: [] as Array<{ body: unknown; key: string | undefined }>,
    mutations: [] as string[], waitForRetry: undefined as Promise<void> | undefined, retryError: false, retryBlocked: false,
    statusGets: 0, extractionGets: 0, retryRun };
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/api/scholarflow/')) state.mutations.push(request.method() + ' ' + new URL(request.url()).pathname); });
  await page.route('**/api/scholarflow/projects/p1/extractions', async route => {
    state.extractionGets++;
    return route.fulfill(state.extractionError ? { status: 503, json: { error: { message: '提取结果暂时不可用' } } } : { json: { items: state.extractions } });
  });
  await page.route('**/api/scholarflow/projects/p1/reading-status', async route => {
    state.statusGets++;
    return route.fulfill(state.statusError ? { status: 503, json: { error: { message: '阅读状态暂时不可用' } } } : { json: { items: state.readings } });
  });
  await page.route('**/api/scholarflow/projects/p1/reading-retries', async route => {
    state.posts.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    await state.waitForRetry;
    return route.fulfill(state.retryBlocked ? { status: 409, json: { error: { code: 'reading_retry_blocked', message: 'A retry is already queued or running' } } }
      : state.retryError ? { status: 503, json: { error: { message: '重试暂时无法提交' } } }
      : { status: 202, json: { run: state.retryRun } });
  });
  await page.route('**/api/scholarflow/runs/retry1', route => route.fulfill({ json: state.retryRun }));
  return state;
}

test('已取消研究的17篇成功结果可查看，失败论文单独列出', async ({ page }) => {
  await mockReadings(page, 18);
  await page.goto('/projects/p1?tab=evidence');
  const status = page.getByRole('region', { name: '逐篇阅读状态' });
  await expect(status.getByText('已成功 17 篇', { exact: true })).toBeVisible();
  await expect(status.getByText('失败 1 篇', { exact: true })).toBeVisible();
  await expect(status.getByText('科研论文 18', { exact: true })).toBeVisible();
  await expect(status.getByText('阅读结果格式不完整，可仅重试此篇。', { exact: true })).toBeVisible();
  await expect(status.getByRole('button', { name: '仅重试此篇 科研论文 18' })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: '科研论文 1' }).first()).toContainText('已保存的阅读结果');
  await expect(page.getByRole('button', { name: /^仅重试此篇/ })).toHaveCount(1);
});

test('逐篇成功后轮询显示新矩阵行，无需切换页面', async ({ page }) => {
  const state = await mockReadings(page);
  state.readings[1] = { ...reading('v2', 'running'), can_retry: false };
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByText('已保存的阅读结果', { exact: true })).toBeVisible();
  state.readings[1] = reading('v2', 'succeeded'); state.extractions.push(extraction('v2', '第二篇新完成的结果'));
  await expect(page.getByText('第二篇新完成的结果', { exact: true })).toBeVisible();
  await expect(page.getByText('已成功 2 篇', { exact: true })).toBeVisible();
});

test('仅重试源失败任务，提交与刷新窗口都防重复且不恢复旧研究', async ({ page }) => {
  const state = await mockReadings(page);
  let release!: () => void;
  state.waitForRetry = new Promise<void>(resolve => { release = resolve; });
  await page.goto('/projects/p1?tab=evidence');
  const retry = page.getByRole('button', { name: '仅重试此篇 科研论文 2' });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(retry).toBeDisabled();
  await retry.dispatchEvent('click');
  await expect.poll(() => state.posts.length).toBe(1);
  release();
  await expect(page.getByText('单篇重试已排队，只补充阅读结果，不会自动生成报告。', { exact: true })).toBeVisible();
  const previousGets = state.statusGets;
  await expect.poll(() => state.statusGets).toBeGreaterThan(previousGets);
  await expect(retry).toBeDisabled();
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0].body).toEqual({ source_job_id: 'job-v2' });
  expect(state.posts[0].key).toBeTruthy();
  expect(state.mutations).toEqual(['POST /api/scholarflow/projects/p1/reading-retries']);
  state.readings[1] = { ...reading('v2', 'running'), active_retry_run_id: 'retry1', can_retry: false };
  await expect(page.getByRole('region', { name: '逐篇阅读状态' }).getByText('科研论文 2', { exact: true })).toBeVisible();
  state.readings[1] = reading('v2', 'succeeded'); state.extractions.push(extraction('v2', '重试成功的阅读结果'));
  await expect(page.getByText('重试成功的阅读结果', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '仅重试此篇 科研论文 2' })).toHaveCount(0);
  await expect(page.getByText('已成功 2 篇', { exact: true })).toBeVisible();
});

test('重试请求失败后保留原因并允许重新提交', async ({ page }) => {
  const state = await mockReadings(page); state.retryError = true;
  await page.goto('/projects/p1?tab=evidence');
  const retry = page.getByRole('button', { name: '仅重试此篇 科研论文 2' });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByRole('alert').filter({ hasText: '重试暂时无法提交' })).toBeVisible();
  await expect(retry).toBeEnabled();
  state.retryError = false;
  await retry.click();
  await expect(page.getByText('单篇重试已排队，只补充阅读结果，不会自动生成报告。', { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(2);
});

test('单篇重试本身失败后能够再次重试，不被本地排队状态永久锁住', async ({ page }) => {
  const state = await mockReadings(page);
  await page.goto('/projects/p1?tab=evidence');
  const retry = page.getByRole('button', { name: '仅重试此篇 科研论文 2' });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(retry).toBeDisabled();
  state.retryRun.status = 'failed';
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => state.posts.length).toBe(2);
});

test('状态轮询失败不清除状态与矩阵，提取结果仍独立更新', async ({ page }) => {
  const state = await mockReadings(page);
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByText('已成功 1 篇', { exact: true })).toBeVisible();
  state.statusError = true; state.extractions.push(extraction('v2', '状态接口失败时仍可出现的结果'));
  await expect(page.getByRole('alert').filter({ hasText: '阅读状态暂时不可用' })).toBeVisible();
  await expect(page.getByText('已成功 1 篇', { exact: true })).toBeVisible();
  await expect(page.getByText('状态接口失败时仍可出现的结果', { exact: true })).toBeVisible();
  state.statusError = false;
  await expect(page.getByRole('alert').filter({ hasText: '阅读状态暂时不可用' })).toHaveCount(0);
});

test('提取结果轮询失败保留成功行，阅读状态仍独立更新', async ({ page }) => {
  const state = await mockReadings(page);
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByText('已保存的阅读结果', { exact: true })).toBeVisible();
  state.extractionError = true; state.readings[1] = reading('v2', 'succeeded');
  await expect(page.getByRole('alert').filter({ hasText: '提取结果暂时不可用' })).toBeVisible();
  await expect(page.getByText('已保存的阅读结果', { exact: true })).toBeVisible();
  await expect(page.getByText('已成功 2 篇', { exact: true })).toBeVisible();
  state.extractionError = false;
  await expect(page.getByRole('alert').filter({ hasText: '提取结果暂时不可用' })).toHaveCount(0);
});

test('后台更新成果不覆盖矩阵尚未保存的编辑', async ({ page }) => {
  const state = await mockReadings(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '编辑方法 科研论文 1' }).click();
  const editor = page.getByRole('textbox', { name: '修改方法' });
  await editor.fill('尚未保存的人工修正');
  state.extractions[0] = { ...extraction('v1', '后台更新的服务端内容'), revision: 2 };
  const previousGets = state.extractionGets;
  await expect.poll(() => state.extractionGets).toBeGreaterThan(previousGets);
  await expect(editor).toHaveValue('尚未保存的人工修正');
});

test('后端阻止重试时显示原因，不提供可用的重试按钮', async ({ page }) => {
  const state = await mockReadings(page);
  state.readings[1].can_retry = false; state.readings[1].retry_block_reason = 'A retry is already queued or running';
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByText('该论文已有单篇重试任务正在运行', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '仅重试此篇 科研论文 2' })).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});

test('重试接口拒绝重复请求时给出中文原因', async ({ page }) => {
  const state = await mockReadings(page); state.retryBlocked = true;
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '仅重试此篇 科研论文 2' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '该论文已有单篇重试任务正在运行' })).toBeVisible();
});

test('已完成重试但成果尚未同步时不再提示排队', async ({ page }) => {
  const state = await mockReadings(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '仅重试此篇 科研论文 2' }).click();
  state.retryRun.status = 'completed';
  await expect(page.getByText('单篇阅读已完成，正在同步结果。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '仅重试此篇 科研论文 2' })).toBeDisabled();
  state.readings[1] = reading('v2', 'succeeded'); state.extractions.push(extraction('v2', '同步后的阅读结果'));
  await expect(page.getByText('同步后的阅读结果', { exact: true })).toBeVisible();
  await expect(page.getByText('单篇阅读已完成，正在同步结果。', { exact: true })).toHaveCount(0);
});

test('单篇重试进度指向逐篇结果并避免整轮重试入口', async ({ page }) => {
  const state = await mockReadings(page);
  state.workspace.run.kind = 'paper_read'; state.workspace.run.status = 'failed'; state.workspace.run.stage = 'read_paper';
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByRole('heading', { name: '单篇阅读重试进度' })).toBeVisible();
  await expect(page.getByText('单篇阅读', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '重试失败阶段', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '查看逐篇阅读结果' })).toHaveAttribute('href', '/projects/p1?tab=evidence');
});

test('部分阅读失败的研究进度说明成功结果保留并指向逐篇处理', async ({ page }) => {
  const state = await mockReadings(page);
  Object.assign(state.workspace.run, { status: 'needs_review', stage: 'read', pending_action: { type: 'paper_read_failures' } });
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByText('部分论文阅读失败，已成功的结果仍可在证据与矩阵查看。请仅重试失败论文。', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看逐篇阅读结果' })).toHaveAttribute('href', '/projects/p1?tab=evidence');
  await expect(page.getByRole('button', { name: '重试失败阶段', exact: true })).toHaveCount(0);
});

test('旧版本阅读阶段失败也保留逐篇成果并隐藏整批重试', async ({ page }) => {
  const state = await mockReadings(page, 20);
  Object.assign(state.workspace.run, { status: 'failed', stage: 'read', pending_action: null,
    issues: [{ code: 'worker_error', message: 'ExceptionGroup' }] });
  await page.goto('/projects/p1?tab=evidence');
  await expect(page.getByRole('heading', { name: '研究运行进度' })).toBeVisible();
  await expect(page.getByRole('button', { name: '重试失败阶段', exact: true })).toHaveCount(0);
  await expect(page.getByText('已成功 19 篇', { exact: true })).toBeVisible();
  await expect(page.getByText('失败 1 篇', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '仅重试此篇 科研论文 20' })).toBeVisible();
  await expect(page.getByText('部分论文阅读失败，已成功的结果仍可在证据与矩阵查看。请仅重试失败论文。', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看逐篇阅读结果' })).toHaveAttribute('href', '/projects/p1?tab=evidence');
});

test('同源代理透传逐篇状态与重试接口以及幂等键', async ({ request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', '同源代理与视口无关，只验证一次以避免并行占用假上游端口');
  const calls: Array<{ path?: string; method?: string; key?: string | string[]; body: string }> = [];
  const server = createServer(async (incoming, response) => {
    let body = ''; for await (const chunk of incoming) body += chunk;
    calls.push({ path: incoming.url, method: incoming.method, key: incoming.headers['idempotency-key'], body });
    response.writeHead(incoming.method === 'POST' ? 202 : 200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(incoming.method === 'POST' ? { run: { id: 'proxy-retry' } } : { items: [reading('v2', 'failed')] }));
  });
  await new Promise<void>(resolve => server.listen(Number(process.env.PLAYWRIGHT_API_PORT || '8100'), '127.0.0.1', resolve));
  try {
    const status = await request.get('/api/scholarflow/projects/p1/reading-status');
    const retry = await request.post('/api/scholarflow/projects/p1/reading-retries', { data: { source_job_id: 'job-v2' }, headers: { 'Idempotency-Key': 'proxy-key' } });
    expect(status.status()).toBe(200); expect(retry.status()).toBe(202);
    expect((await status.json()).items[0].source_job_id).toBe('job-v2');
    expect((await retry.json()).run.id).toBe('proxy-retry');
    expect(calls.map(call => [call.method, call.path])).toEqual([
      ['GET', '/api/v1/projects/p1/reading-status'], ['POST', '/api/v1/projects/p1/reading-retries']]);
    expect(calls[1].key).toBe('proxy-key'); expect(JSON.parse(calls[1].body)).toEqual({ source_job_id: 'job-v2' });
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
});
