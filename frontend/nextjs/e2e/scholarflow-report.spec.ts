import { test, expect } from '@playwright/test';
import { mockReport } from './report-fixtures';

test('保存响应等待期间锁定编辑，成功后保留已提交文字', async ({ page }) => {
  const state = await mockReport(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/scholarflow/projects/p1/reports', async route => {
    if (route.request().method() === 'POST') await pending;
    await route.fallback();
  });
  await page.goto('/projects/p1?tab=report');
  const body = page.getByRole('textbox', { name: '报告正文', exact: true });
  await body.fill('等待保存的正文 [@sf_one]');
  await page.getByRole('button', { name: '保存新报告版本' }).click();
  await expect(body).toBeDisabled();
  await expect(page.getByRole('button', { name: '放弃本地修改' })).toBeDisabled();
  release();
  await expect(body).toHaveValue('等待保存的正文 [@sf_one]');
  await expect.poll(() => state.version.id).toBe('report2');
});

test('编辑后选择论断和现有原文，明确重新绑定引用', async ({ page }) => {
  const state = await mockReport(page);
  await page.route('**/api/scholarflow/reports/*/citation-options', route => route.fulfill({ json: { items: [
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'e1', quote: 'The method uses a graph.', source_scope: 'fulltext', locator_status: 'located' } ] } }));
  await page.goto('/projects/p1?tab=report');
  const body = page.getByRole('textbox', { name: '报告正文', exact: true });
  await body.fill('🧪 The method uses a graph! [@sf_one]');
  await body.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(0, 26));
  await page.getByRole('button', { name: '使用所选文字作为论断' }).click();
  await page.getByRole('combobox', { name: '原文依据', exact: true }).selectOption('e1');
  await page.getByRole('button', { name: '确认此处引用绑定' }).click();
  await page.getByRole('button', { name: '保存新报告版本' }).click();
  await expect.poll(() => state.version.id).toBe('report2');
  expect(state.version.blocks[0].claims).toEqual([{ start: 0, end: 25, token_index: 0, claim_revision_ids: ['claim1'], evidence_ids: ['e1'] }]);
});

test('编辑段落后相关引用待复核，未保存时不能导出', async ({ page }) => {
  await mockReport(page); await page.goto('/projects/p1?tab=report');
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('本地修改 [@sf_one]');
  await expect(page.getByText('存在未保存修改', { exact: true })).toBeVisible();
  await expect(page.getByText('引用待复核', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '导出已保存版本' })).toBeDisabled();
});

test('保存冲突保留本地报告', async ({ page }) => {
  await mockReport(page, { conflict: true }); await page.goto('/projects/p1?tab=report');
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('本地未保存正文');
  await page.getByRole('button', { name: '保存新报告版本' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '输入已保留' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveValue('本地未保存正文');
});

test('同论文两处引用独立显示，确认草稿保留问题标签', async ({ page }) => {
  const state = await mockReport(page); await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await expect(page.getByRole('button', { name: '引用 1 · 支持' })).toBeVisible();
  await expect(page.getByRole('button', { name: '引用 2 · 冲突' })).toBeVisible();
  await page.getByRole('button', { name: '接受此版本草稿' }).click();
  await expect.poll(() => state.accepted?.report_version_id).toBe('report1');
  expect(state.accepted?.verification_record_id).toBe('check1');
  await expect(page.getByText('存在质量问题', { exact: true }).first()).toBeVisible();
});

test('事件断线后按序号补读并去重', async ({ page }) => {
  const state = await mockReport(page, { reconnect: true }); await page.goto('/projects/p1?tab=report');
  await expect(page.getByTestId('run-event')).toHaveCount(3, { timeout: 15000 });
  expect(state.after).toContain('2');
});

test('Markdown 预览不执行原始事件处理器', async ({ page }) => {
  await mockReport(page); await page.goto('/projects/p1?tab=report');
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('<img src=x onerror="window.__sfXss=true">');
  await page.getByRole('button', { name: '预览正文' }).click();
  await expect(page.getByTestId('report-preview').locator('[onerror]')).toHaveCount(0);
  expect(await page.evaluate(() => Boolean((window as unknown as { __sfXss?: boolean }).__sfXss))).toBe(false);
});

test('刷新恢复此报告版本的临时草稿', async ({ page }) => {
  await mockReport(page); await page.goto('/projects/p1?tab=report');
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('刷新前的本地草稿');
  await expect(page.getByText('存在未保存修改', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveValue('刷新前的本地草稿');
});
