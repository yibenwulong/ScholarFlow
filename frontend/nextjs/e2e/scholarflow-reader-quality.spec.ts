import { test, expect, Page } from '@playwright/test';
import { mockWorkspace, paper } from './fixtures';
import type { Extraction, ExtractedField, FieldName } from '../types/scholarflow';

type QualityExtraction = Omit<Extraction, 'fields'> & {
  fields: Record<FieldName, ExtractedField & { missing_reason?: string | null }>;
  coverage?: { reader_version?: string; fragments?: Array<{ source_scope: string }> };
};
type Reading = { paper_version_id: string; source_run_id: string; source_job_id: string;
  status: 'succeeded' | 'failed' | 'queued' | 'running'; extraction_id: string | null;
  error: { code: string; message: string } | null; can_retry: boolean; active_retry_run_id: string | null;
  needs_reread?: boolean; retry_block_reason?: string | null };
function result(versionId: string, reason?: string): QualityExtraction {
  const empty = { value: null, claim_ids: [], evidence_ids: [], review_status: 'needs_recheck' as const,
    ...(reason ? { missing_reason: reason } : {}) };
  return { id: 'ex-' + versionId, revision: 1, created_at: '2026-01-01T00:00:00Z', project_id: 'p1',
    paper_version_id: versionId, document_version_ids: ['used-' + versionId], origin: 'ai', previous_id: null,
    fields: { research_question: { ...empty }, method: { ...empty }, dataset: { ...empty }, findings: { ...empty }, limitations: { ...empty } } };
}
async function mockQuality(page: Page, count = 5) {
  const workspace = await mockWorkspace(page);
  workspace.run.status = 'cancelled'; workspace.run.stage = 'read';
  workspace.papers = Array.from({ length: count }, (_, index) => paper(String(index + 1)));
  const extractions = workspace.papers.map(item => result(item.paper_version_id));
  extractions[0].fields.method.value = '旧版方法内容';
  if (count === 5) {
    extractions[1] = result('v2', '摘要未说明研究数据与实验设置');
    extractions[1].coverage = { reader_version: 'reader-v2', fragments: [{ source_scope: 'abstract' }] };
    workspace.papers[1].documents = [{ id: 'used-v2', kind: 'abstract', parse_status: 'ready' }, { id: 'new-pdf', kind: 'pdf', parse_status: 'ready' }];
    extractions[2] = result('v3', '所读全文未说明该项');
    extractions[2].coverage = { reader_version: 'reader-v2' };
    workspace.papers[2].documents = [{ id: 'used-v3', kind: 'pdf', parse_status: 'ready' }, { id: 'unused-abstract', kind: 'abstract', parse_status: 'ready' }];
    extractions[3].origin = 'human';
    extractions[4] = result('v5', '当前所读文本没有这项信息');
    extractions[4].coverage = { reader_version: 'reader-v2' };
    workspace.papers[4].documents = [{ id: 'another-version-pdf', kind: 'pdf', parse_status: 'ready' }];
  }
  const state = { workspace, extractions, readings: workspace.papers.map((item, index): Reading => ({
    paper_version_id: item.paper_version_id, source_run_id: 'run1', source_job_id: 'job-' + item.paper_version_id,
    status: 'succeeded', extraction_id: 'ex-' + item.paper_version_id, error: null, can_retry: index === 0,
    active_retry_run_id: null, needs_reread: index === 0 })),
    posts: [] as Array<{ body: unknown; key?: string }>, mutations: [] as string[], statusGets: 0, extractionGets: 0,
    waitForPost: undefined as Promise<void> | undefined,
    retryRun: { ...workspace.run, id: 'quality-retry', kind: 'paper_read', target_id: 'job-v1', status: 'queued', stage: 'read_paper', pending_action: null } };
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/api/scholarflow/')) state.mutations.push(new URL(request.url()).pathname); });
  await page.route('**/api/scholarflow/projects/p1/extractions', route => { state.extractionGets++; return route.fulfill({ json: { items: state.extractions } }); });
  await page.route('**/api/scholarflow/projects/p1/reading-status', route => { state.statusGets++; return route.fulfill({ json: { items: state.readings } }); });
  await page.route('**/api/scholarflow/projects/p1/reading-retries', async route => {
    state.posts.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    await state.waitForPost;
    return route.fulfill({ status: 202, json: { run: state.retryRun } });
  });
  await page.route('**/api/scholarflow/runs/quality-retry', route => route.fulfill({ json: state.retryRun }));
  return state;
}
const matrixRow = (page: Page, title: string) => page.getByRole('row').filter({ has: page.getByRole('rowheader', { name: new RegExp('^' + title + '\\b') }) });

test('旧版空字段显示尚未完整提取，不能宣称原文未报告', async ({ page }) => {
  await mockQuality(page); await page.goto('/projects/p1?tab=evidence');
  const row = matrixRow(page, '科研论文 1');
  await expect(row.getByRole('cell').first()).toContainText('尚未完整提取');
  await expect(row.getByText('未报告', { exact: true })).toHaveCount(0);
  await expect(row.getByText('旧版方法内容', { exact: true })).toBeVisible();
});

test('新版摘要缺项说明范围和原因，不被后来上传的PDF冒充全文', async ({ page }) => {
  await mockQuality(page); await page.goto('/projects/p1?tab=evidence');
  const row = matrixRow(page, '科研论文 2'), cell = row.getByRole('cell').first();
  await expect(row.getByText('阅读范围：摘要', { exact: true })).toBeVisible();
  await expect(cell.getByText('当前摘要未提供', { exact: true })).toBeVisible();
  await cell.getByText('查看缺失原因', { exact: true }).click();
  await expect(cell.getByText('摘要未说明研究数据与实验设置', { exact: true })).toBeVisible();
});

test('范围回退仅匹配实际阅读文档，无法匹配时不猜全文', async ({ page }) => {
  await mockQuality(page); await page.goto('/projects/p1?tab=evidence');
  const full = matrixRow(page, '科研论文 3'), unknown = matrixRow(page, '科研论文 5');
  await expect(full.getByText('阅读范围：全文', { exact: true })).toBeVisible();
  await expect(full.getByRole('cell').first()).toContainText('所读全文未提供');
  await expect(unknown.getByText('阅读范围未记录', { exact: true })).toBeVisible();
  await expect(unknown.getByRole('cell').first()).toContainText('所读文本未提供');
  await expect(unknown.getByText('阅读范围：全文', { exact: true })).toHaveCount(0);
});

test('人工留空显示人工未填写', async ({ page }) => {
  await mockQuality(page); await page.goto('/projects/p1?tab=evidence');
  await expect(matrixRow(page, '科研论文 4').getByRole('cell').first()).toContainText('人工未填写');
});

test('人工只改方法后继承的数据集缺失原因仍按摘要显示', async ({ page }) => {
  const state = await mockQuality(page), extraction = state.extractions[1];
  extraction.origin = 'human'; extraction.fields.method.value = '人工补充的方法'; extraction.fields.method.missing_reason = null;
  extraction.fields.research_question.missing_reason = null;
  await page.goto('/projects/p1?tab=evidence');
  const row = matrixRow(page, '科研论文 2'), dataset = row.getByRole('cell').nth(2);
  await expect(row.getByText('人工补充的方法', { exact: true })).toBeVisible();
  await expect(dataset.getByText('当前摘要未提供', { exact: true })).toBeVisible();
  await dataset.getByText('查看缺失原因', { exact: true }).click();
  await expect(dataset.getByText('摘要未说明研究数据与实验设置', { exact: true })).toBeVisible();
  await expect(row.getByRole('cell').first()).toContainText('人工未填写');
});

test('19篇旧成功结果默认折叠待补充列表，失败论文仍直接显示重试', async ({ page }) => {
  const state = await mockQuality(page, 20);
  state.readings.forEach((item, index) => { item.needs_reread = index < 19; item.can_retry = true; });
  Object.assign(state.readings[19], { status: 'failed', extraction_id: null, error: { code: 'invalid_model_output', message: 'invalid fields' } });
  state.extractions.pop();
  await page.goto('/projects/p1?tab=evidence');
  const panel = page.getByRole('region', { name: '逐篇阅读状态' });
  await expect(panel.getByText('待补充提取（19）', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: /^补充提取此篇/ })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '仅重试此篇 科研论文 20' })).toBeVisible();
  await panel.getByText('待补充提取（19）', { exact: true }).click();
  await expect(panel.getByRole('button', { name: /^补充提取此篇/ })).toHaveCount(19);
});

test('补读复用单篇API并在旧成功状态刷新窗口保持锁和旧值，完成后更新', async ({ page }) => {
  const state = await mockQuality(page);
  let release!: () => void; state.waitForPost = new Promise<void>(resolve => { release = resolve; });
  await page.goto('/projects/p1?tab=evidence');
  await page.getByText('待补充提取（1）', { exact: true }).click();
  const button = page.getByRole('button', { name: '补充提取此篇 科研论文 1' });
  await button.click(); await expect(button).toBeDisabled(); await button.dispatchEvent('click');
  await expect.poll(() => state.posts.length).toBe(1); release();
  const gets = state.statusGets;
  await expect.poll(() => state.statusGets).toBeGreaterThan(gets);
  await expect(button).toBeDisabled();
  await expect(page.getByText('旧版方法内容', { exact: true })).toBeVisible();
  expect(state.posts[0].body).toEqual({ source_job_id: 'job-v1' }); expect(state.posts[0].key).toBeTruthy();
  expect(state.mutations).toEqual(['/api/scholarflow/projects/p1/reading-retries']);
  state.retryRun.status = 'completed';
  await expect(page.getByText('单篇阅读已完成，正在同步结果。', { exact: true })).toBeVisible();
  await expect(button).toBeDisabled(); expect(state.posts).toHaveLength(1);
  const updated = result('v1', '所读文本未提供其余项目'); updated.id = 'new-ex-v1'; updated.revision = 2;
  updated.coverage = { reader_version: 'reader-v2', fragments: [{ source_scope: 'abstract' }] }; updated.fields.method.value = '补读后的方法内容';
  state.extractions[0] = updated; state.readings[0].needs_reread = false; state.readings[0].can_retry = false;
  await expect(page.getByText('补读后的方法内容', { exact: true })).toBeVisible();
  await expect(page.getByText('旧版方法内容', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '补充提取此篇 科研论文 1' })).toHaveCount(0);
});

test('补读结果轮询不覆盖矩阵尚未保存的人工编辑', async ({ page }) => {
  const state = await mockQuality(page); await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '编辑方法 科研论文 1' }).click();
  const editor = page.getByRole('textbox', { name: '修改方法' }); await editor.fill('我的未保存编辑');
  const updated = result('v1', '原文没有描述该项'); updated.id = 'new-ex-v1'; updated.revision = 2; updated.fields.method.value = '后台补读新值';
  updated.coverage = { reader_version: 'reader-v2' }; state.extractions[0] = updated; state.readings[0].needs_reread = false;
  const gets = state.extractionGets; await expect.poll(() => state.extractionGets).toBeGreaterThan(gets);
  await expect(editor).toHaveValue('我的未保存编辑');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByText('后台补读新值', { exact: true })).toBeVisible();
});

test('待补充但被后端保护的人工修订不能触发补读', async ({ page }) => {
  const state = await mockQuality(page); state.readings[0].can_retry = false;
  state.readings[0].retry_block_reason = 'A human extraction revision must be preserved';
  await page.goto('/projects/p1?tab=evidence'); await page.getByText('待补充提取（1）', { exact: true }).click();
  await expect(page.getByText('此篇已有人工修订，为保留你的修改，暂不允许重新阅读。', { exact: true })).toBeVisible();
  const button = page.getByRole('button', { name: '补充提取此篇 科研论文 1' });
  await expect(button).toBeDisabled(); expect(state.posts).toHaveLength(0);
});

test('失败补读再次提交后保持失败论文入口，不移入折叠的成功补读组', async ({ page }) => {
  const state = await mockQuality(page);
  Object.assign(state.readings[0], { status: 'failed', error: { code: 'invalid_model_output', message: 'invalid fields' } });
  await page.goto('/projects/p1?tab=evidence');
  const button = page.getByRole('button', { name: '仅重试此篇 科研论文 1' });
  await expect(button).toBeVisible(); await button.click();
  await expect(page.getByText('单篇重试已排队，只补充阅读结果，不会自动生成报告。', { exact: true })).toBeVisible();
  await expect(button).toBeVisible(); await expect(button).toBeDisabled();
  await expect(page.getByText('待补充提取（1）', { exact: true })).toHaveCount(0);
  await expect(page.getByText('旧版方法内容', { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1);
});
