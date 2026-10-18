import { test, expect, type Page } from '@playwright/test';
import { mockReport } from './report-fixtures';

async function unboundReport(page: Page) {
  const state = await mockReport(page);
  const text = Array.from({ length: 14 }, (_, index) => `第${index + 1}句研究结果 [@sf_one]。`).join('\n');
  const version = { ...state.version, blocks: [{ ...state.version.blocks[0], text }],
    occurrences: Array.from({ length: 14 }, (_, index) => ({ id: 'unbound' + index, block_id: 'b1', token_index: index,
      claim_revision_ids: [], evidence_ids: [], claim_start: null, claim_end: null })) };
  const verification = { ...state.verification, checks: version.occurrences.map(occurrence => ({ occurrence_id: occurrence.id,
    status: 'needs_recheck', checker: 'source_guard_v1', reason: 'Citation has no explicit claim and evidence binding' })) };
  await page.route('**/api/scholarflow/reports/report1', route => route.fulfill({ json: {
    version, verification, outdated: false, citations: version.occurrences,
    references: [{ number: 1, key: 'sf_one', title: '科研论文 a' }], rendered_markdown: '',
  } }));
  return version;
}

test('长引用列表点击后详情立即可见，并明确未绑定的原因与对应论文', async ({ page }) => {
  await unboundReport(page);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/api/scholarflow/')) writes.push(request.method()); });
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  const first = page.getByRole('button', { name: '引用 1 · 待复核', exact: true });
  await first.click();
  await expect(first).toHaveAttribute('aria-expanded', 'true');
  const detail = page.getByRole('region', { name: '引用 1 详情', exact: true });
  await expect(detail).toBeInViewport();
  await expect(detail).toContainText('科研论文 a');
  await expect(detail).toContainText('未绑定原文证据');
  await expect(detail).toContainText('第1句研究结果');
  await expect(detail).not.toContainText('Citation has no explicit');
  await expect(detail).not.toContainText('模型支持性检查');
  await expect(detail.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('选择列表末尾的引用后可直接定位原始标记并选中正确的绑定位置', async ({ page }) => {
  const version = await unboundReport(page);
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  const last = page.getByRole('button', { name: '引用 14 · 待复核', exact: true });
  await last.click();
  const detail = page.getByRole('region', { name: '引用 14 详情', exact: true });
  await expect(detail).toBeInViewport();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: '引用 1 · 待复核', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await detail.getByRole('button', { name: '定位正文并补充绑定', exact: true }).click();
  const body = page.getByRole('textbox', { name: '报告正文', exact: true });
  await expect(body).toBeVisible();
  await expect(body).toBeFocused();
  await expect(body).toHaveValue(version.blocks[0].text);
  await expect(page.getByRole('combobox', { name: '引用位置', exact: true })).toHaveValue('13');
  const selection = await body.evaluate((node: HTMLTextAreaElement) => node.value.slice(node.selectionStart, node.selectionEnd));
  expect(selection).toBe('[@sf_one]');
  await expect(page.getByText('存在未保存修改', { exact: true })).toHaveCount(0);
});

test('有绑定时分别显示准确论断与原文入口，按Unicode字符截取且不混淆同文献其他位置', async ({ page }) => {
  const state = await mockReport(page);
  const sentence = '🧪 The method uses a graph.';
  state.version.blocks[0].text = sentence + ' [@sf_one]';
  state.version.occurrences[0].claim_end = Array.from(sentence).length;
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await page.getByRole('button', { name: '引用 1 · 支持', exact: true }).click();
  const first = page.getByRole('region', { name: '引用 1 详情', exact: true });
  await expect(first.locator('blockquote')).toHaveText(sentence);
  await expect(first.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveAttribute('href', '/projects/p1?tab=evidence&claim=claim1&evidence=e1');
  await page.getByRole('button', { name: '引用 2 · 冲突', exact: true }).click();
  const second = page.getByRole('region', { name: '引用 2 详情', exact: true });
  await expect(second).toContainText('第二处否定了原文');
  await expect(second.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveAttribute('href', '/projects/p1?tab=evidence&claim=claim2&evidence=e2');
  await expect(first).toHaveCount(0);
});

test('未保存正文修改时不能将旧绑定当作当前结果，也不能用旧位置直接重绑', async ({ page }) => {
  await mockReport(page);
  await page.goto('/projects/p1?tab=report');
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('尚未保存的新解释 [@sf_one]');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await page.getByRole('button', { name: '引用 1 · 待复核', exact: true }).click();
  const detail = page.getByRole('region', { name: '引用 1 详情', exact: true });
  await expect(detail).toContainText('未保存修改');
  await expect(detail).toContainText('已保存版本');
  await expect(detail.getByRole('button', { name: '定位正文并编辑绑定', exact: true })).toBeDisabled();
});
