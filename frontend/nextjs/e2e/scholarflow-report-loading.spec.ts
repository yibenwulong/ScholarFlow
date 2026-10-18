import { test, expect, type Page } from '@playwright/test';
import type { Run, RunStatus } from '../types/scholarflow';
import { mockWorkspace } from './fixtures';
import { mockReport } from './report-fixtures';

async function emptyReport(page: Page, status: RunStatus) {
  const workspace = await mockWorkspace(page);
  const research: Run = { ...workspace.run, kind: 'literature_review', target_id: null, start_at: 'analyze',
    status, stage: 'write', pending_action: null, created_at: '2026-01-03T00:00:00Z' };
  const imported: Run = { ...research, id: 'import1', kind: 'import', status: 'completed', stage: 'import', created_at: '2026-01-04T00:00:00Z' };
  const older: Run = { ...research, id: 'older1', status: 'failed', created_at: '2026-01-02T00:00:00Z' };
  await page.route('**/api/scholarflow/projects/p1/runs', route => route.fulfill({ json: { items: [imported, research, older] } }));
  await page.route('**/api/scholarflow/runs/run1', route => route.fulfill({ json: research }));
}

function detail(state: Awaited<ReturnType<typeof mockReport>>) {
  return { version: state.version, verification: state.verification, outdated: false, citations: state.version.occurrences,
    references: [{ number: 1, key: 'sf_one', title: '科研论文 a' }], rendered_markdown: '' };
}

for (const status of ['queued', 'running'] as const) {
  test(`无报告时说明最新研究的${status}写作状态和自动展示`, async ({ page }) => {
    await emptyReport(page, status);
    await page.goto('/projects/p1?tab=report');
    const empty = page.locator('.sf-empty');
    await expect(empty).toContainText(status === 'queued' ? '报告写作已排队' : '正在撰写报告');
    await expect(empty).toContainText('自动显示');
    await expect(empty).toContainText('下方运行进度');
  });
}

test('无报告且最新研究失败时解释未生成并指向失败进度', async ({ page }) => {
  await emptyReport(page, 'failed');
  await page.goto('/projects/p1?tab=report');
  await expect(page.locator('.sf-empty')).toContainText('未生成');
  await expect(page.locator('.sf-empty')).toContainText('下方运行进度');
});

test('初始报告列表为空后首份发布报告自动显示', async ({ page }) => {
  const state = await mockReport(page);
  let published = false, listReads = 0;
  await page.route('**/api/scholarflow/projects/p1/reports', route => {
    listReads += 1;
    return route.fulfill({ json: { items: published ? [state.version] : [] } });
  });
  await page.goto('/projects/p1?tab=report');
  await expect.poll(() => listReads).toBeGreaterThan(0);
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveCount(0);
  published = true;
  await expect(page.getByTestId('report-preview')).toContainText('The method uses a graph. [1]');
});

test('首次报告详情暂时失败后轮询初始化正文并清除加载错误', async ({ page }) => {
  const state = await mockReport(page);
  let detailReads = 0;
  await page.route('**/api/scholarflow/reports/report1', route => {
    detailReads += 1;
    return detailReads === 1
      ? route.fulfill({ status: 502, json: { error: { code: 'backend_unavailable', message: '首次详情连接中断' } } })
      : route.fulfill({ json: detail(state) });
  });
  await page.goto('/projects/p1?tab=report');
  await expect(page.getByText('首次详情连接中断', { exact: true })).toBeVisible();
  await expect(page.getByTestId('report-preview')).toContainText('The method uses a graph. [1]');
  await expect(page.getByText('首次详情连接中断', { exact: true })).toHaveCount(0);
});

test('报告详情轮询更新核验时保留正在编辑的段落和正文', async ({ page }) => {
  const state = await mockReport(page);
  let detailReads = 0;
  await page.route('**/api/scholarflow/reports/report1', route => {
    detailReads += 1;
    return route.fulfill({ json: detail(state) });
  });
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '2. 分歧', exact: true }).click();
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  const body = page.getByRole('textbox', { name: '报告正文', exact: true });
  await body.fill('用户尚未保存的修改 [@sf_one]');
  const previousReads = detailReads;
  state.version.blocks[1].text = '轮询返回的服务端正文 [@sf_one]';
  state.verification.quality_status = 'passed';
  await expect.poll(() => detailReads).toBeGreaterThan(previousReads);
  await expect(page.getByText('质量检查通过', { exact: true }).first()).toBeVisible();
  await expect(body).toHaveValue('用户尚未保存的修改 [@sf_one]');
  await expect(page.getByText('存在未保存修改', { exact: true })).toBeVisible();
});

test('报告列表出现新版本时不自动切换已选版本', async ({ page }) => {
  const state = await mockReport(page);
  let items = [state.version];
  await page.route('**/api/scholarflow/projects/p1/reports', route => route.fulfill({ json: { items } }));
  await page.goto('/projects/p1?tab=report');
  const version = page.getByRole('combobox', { name: '报告版本', exact: true });
  await expect(version).toHaveValue('report1');
  items = [{ ...state.version, id: 'report2', revision: 2 }, state.version];
  await expect(version.locator('option')).toHaveCount(2);
  await expect(version).toHaveValue('report1');
  await expect(page.getByTestId('report-preview')).toContainText('The method uses a graph. [1]');
});
