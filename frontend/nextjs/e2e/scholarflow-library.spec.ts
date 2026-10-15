import { test, expect } from '@playwright/test';
import { mockWorkspace } from './fixtures';

test('创建项目并在刷新后恢复服务器数据', async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.goto('/projects');
  await page.getByRole('textbox', { name: '研究问题' }).fill('科研文献工具的评估方法');
  await page.getByRole('button', { name: '创建项目' }).click();
  await expect(page).toHaveURL(/\/projects\/p1/, { timeout: 30000 });
  await page.reload();
  await expect(page.getByRole('heading', { name: '科研文献工具的评估方法' })).toBeVisible();
  expect(state.project.question).toBe('科研文献工具的评估方法');
});

test('批准明确计划后继续研究', async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.goto('/projects/p1');
  await page.getByRole('button', { name: '批准此计划' }).click();
  await expect(page.getByText('计划已批准')).toBeVisible();
  expect(state.calls).toContain('POST /projects/p1/plans/plan1/approve');
  expect(state.calls).toContain('POST /runs/run1/actions');
});

test('pending 候选不会阻塞明确选择的子集', async ({ page }) => {
  const state = await mockWorkspace(page, { selectionGate: true });
  await page.goto('/projects/p1?tab=papers');
  await expect(page.getByText('AI 建议：纳入').first()).toBeVisible();
  await expect(page.getByText('人工决定：待决定').first()).toBeVisible();
  await page.getByRole('checkbox', { name: '纳入 科研论文 a' }).check();
  await page.getByRole('button', { name: '确认纳入并分析' }).click();
  await expect.poll(() => state.selections.length).toBe(1);
  expect(state.selections[0].paper_version_ids).toEqual(['va']);
  expect(state.calls.some(call => call.includes('/api/chat'))).toBe(false);
});

test('版本冲突保留计划输入', async ({ page }) => {
  await mockWorkspace(page, { conflict: true });
  await page.goto('/projects/p1');
  await page.getByRole('textbox', { name: '检索式（每行一个）' }).fill('my unsaved query');
  await page.getByRole('button', { name: '保存计划修订' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '输入已保留' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '检索式（每行一个）' })).toHaveValue('my unsaved query');
});

test('排除已选择的论文后仍可撤销选择', async ({ page }) => {
  await mockWorkspace(page, { selectionGate: true });
  await page.goto('/projects/p1?tab=papers');
  const article = page.getByRole('article').filter({ hasText: '科研论文 a' });
  const selected = article.getByRole('checkbox', { name: '纳入 科研论文 a' });
  await selected.check();
  await article.getByText('编辑纳排决定、笔记或补充全文').click();
  await article.getByRole('combobox', { name: '人工决定', exact: true }).selectOption('excluded');
  await article.getByRole('textbox', { name: '决定理由（必填）' }).fill('与范围不符');
  await article.getByRole('button', { name: '保存决定' }).click();
  await expect(article.getByText('人工决定：排除')).toBeVisible();
  await selected.uncheck();
  await expect(selected).not.toBeChecked();
});
