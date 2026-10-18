import { test, expect } from '@playwright/test';
import { mockReport } from './report-fixtures';

const first = 'sf_aac4b4db320845adbf5c3d68107482ad';
const second = 'sf_9783cb00a6f7436ea8fe1ed3b6d4f277';

test('默认阅读报告，引用跨章节保持编号并可查看论文，原始正文不变', async ({ page }) => {
  const state = await mockReport(page);
  const original = JSON.stringify(state.version);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/api/scholarflow/')) writes.push(request.method()); });
  await page.goto('/projects/p1?tab=report');
  const preview = page.getByTestId('report-preview');
  await expect(preview).toContainText('The method uses a graph. [1]');
  await expect(preview).not.toContainText('sf_one');
  await preview.getByRole('link', { name: '[1]', exact: true }).click();
  await expect(page.getByRole('region', { name: '本报告参考文献' })).toContainText('科研论文 a');
  await page.getByRole('button', { name: '2. 分歧', exact: true }).click();
  await expect(preview).toContainText('The method does not use a graph. [1]');
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveValue(state.version.blocks[1].text);
  expect(JSON.stringify(state.version)).toBe(original);
  expect(writes).toEqual([]);
});

test('正文短编号只按唯一且已正式引用的文献转换，不改保存内容', async ({ page }) => {
  const state = await mockReport(page);
  state.version.bindings = [
    { key: first, paper_version_id: 'va', bibliography_snapshot: { title: '噪声下的推理研究' } },
    { key: second, paper_version_id: 'vb', bibliography_snapshot: { title: '多模态生存分析' } },
  ];
  state.version.blocks[0].text = `sf_aac4b4db提出方法；sf_9783cb00进行比较。 [@${first}] [@${second}]`;
  state.version.blocks[1].text = `补充比较 [@${second}] [@${first}]`;
  const original = JSON.stringify(state.version);
  await page.goto('/projects/p1?tab=report');
  const preview = page.getByTestId('report-preview');
  await expect(preview).toContainText('文献[1]提出方法；文献[2]进行比较。 [1] [2]');
  await expect(preview).not.toContainText('sf_');
  await page.getByRole('button', { name: '2. 分歧', exact: true }).click();
  await expect(preview).toContainText('补充比较 [2] [1]');
  await expect(page.getByRole('region', { name: '本报告参考文献' })).toContainText('[1] 噪声下的推理研究');
  await expect(page.getByRole('region', { name: '本报告参考文献' })).toContainText('[2] 多模态生存分析');
  expect(JSON.stringify(state.version)).toBe(original);
  await expect(page.getByText('存在未保存修改', { exact: true })).toHaveCount(0);
});

test('短编号冲突、未引用文献和未知正式引用均不猜测匹配', async ({ page }) => {
  const state = await mockReport(page);
  state.version.bindings = [
    { key: first, paper_version_id: 'va', bibliography_snapshot: { title: '论文 A' } },
    { key: 'sf_aac4b4dbffffffffffffffffffffffff', paper_version_id: 'vc', bibliography_snapshot: { title: '同前缀论文' } },
    { key: second, paper_version_id: 'vb', bibliography_snapshot: { title: '尚未正式引用' } },
  ];
  state.version.blocks[0].text = `sf_aac4b4db与sf_9783cb00和sf_00000000待确认。 [@${first}] [@sf_missing]`;
  state.version.blocks[1].text = '';
  await page.goto('/projects/p1?tab=report');
  const preview = page.getByTestId('report-preview');
  await expect(preview).toContainText('sf_aac4b4db');
  await expect(preview).toContainText('sf_9783cb00');
  await expect(preview).toContainText('sf_00000000');
  await expect(page.getByRole('status').filter({ hasText: '引用未匹配' })).toBeVisible();
  await expect(preview.getByRole('link', { name: '[2]', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '本报告参考文献' })).not.toContainText('尚未正式引用');
});

test('引用转换保护代码、链接和长编号边界，论文标题按纯文本展示', async ({ page }) => {
  const state = await mockReport(page);
  state.version.bindings = [{ key: first, paper_version_id: 'va', bibliography_snapshot: { title: '<img src=x onerror="window.__sfXss=true">论文' } }];
  state.version.blocks[0].text = `sf_aac4b4db提出方法 [@${first}]。\n\n\`sf_aac4b4db\`\n\n\`\`\`text\nsf_aac4b4db\n\`\`\`\n\n[来源](https://example.org/sf_aac4b4db)\n\nsf_aac4b4db000000000000000000000000`;
  state.version.blocks[1].text = '';
  await page.goto('/projects/p1?tab=report');
  const preview = page.getByTestId('report-preview');
  await expect(preview).toContainText('文献[1]提出方法 [1]');
  await expect(preview.locator('code').first()).toHaveText('sf_aac4b4db');
  await expect(preview.locator('pre code')).toContainText('sf_aac4b4db');
  await expect(preview.getByRole('link', { name: '来源', exact: true })).toHaveAttribute('href', 'https://example.org/sf_aac4b4db');
  await expect(preview).toContainText('sf_aac4b4db000000000000000000000000');
  await expect(page.getByRole('region', { name: '本报告参考文献' })).toContainText('<img src=x onerror="window.__sfXss=true">论文');
  await expect(page.locator('[onerror]')).toHaveCount(0);
  expect(await page.evaluate(() => Boolean((window as unknown as { __sfXss?: boolean }).__sfXss))).toBe(false);
});

test('未保存编辑的预览使用当前全文编号，保存仍提交原始引用标记', async ({ page }) => {
  const state = await mockReport(page);
  state.version.bindings.push({ key: 'sf_two', paper_version_id: 'vb', bibliography_snapshot: { title: '科研论文 b' } });
  await page.goto('/projects/p1?tab=report');
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  const raw = '🧪 新增结果 [@sf_two]；原有结果 [@sf_one]';
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill(raw);
  await page.getByRole('button', { name: '预览正文', exact: true }).click();
  await expect(page.getByTestId('report-preview')).toContainText('🧪 新增结果 [1]；原有结果 [2]');
  await page.getByRole('button', { name: '2. 分歧', exact: true }).click();
  await expect(page.getByTestId('report-preview')).toContainText('The method does not use a graph. [2]');
  await page.getByRole('button', { name: '保存新报告版本', exact: true }).click();
  await expect.poll(() => state.version.id).toBe('report2');
  expect(state.version.blocks[0].text).toBe(raw);
});
