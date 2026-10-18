import { test, expect, type Page } from '@playwright/test';
import { mockReport } from './report-fixtures';

test('unbound context keeps citations whole at both excerpt boundaries', async ({ page }) => {
  const state = await mockReport(page);
  const otherKey = 'sf_11112222333344445555666677778888';
  const text = `[@${otherKey}]${'a'.repeat(140)}[@sf_one]${'b'.repeat(60)}[@${otherKey}]after`;
  const version = { ...state.version, blocks: [{ ...state.version.blocks[0], text }],
    bindings: [...state.version.bindings, { key: otherKey, paper_version_id: 'vb', bibliography_snapshot: { title: 'Paper B' } }],
    occurrences: [0, 1, 2].map(index => ({ id: `excerpt${index}`, block_id: 'b1', token_index: index,
      claim_revision_ids: [], evidence_ids: [], claim_start: null, claim_end: null })) };
  await page.route('**/api/scholarflow/reports/report1', route => route.fulfill({ json: {
    version, verification: null, outdated: false, citations: version.occurrences,
    references: [{ number: 1, key: 'sf_one', title: 'Paper A' }, { number: 2, key: otherKey, title: 'Paper B' }], rendered_markdown: '',
  } }));
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await page.getByRole('button', { name: '引用 2 · 待复核', exact: true }).click();
  const detail = page.getByRole('region', { name: '引用 2 详情', exact: true });
  await expect(detail.locator('blockquote')).toHaveText(`[2]${'a'.repeat(140)}[1]${'b'.repeat(60)}[2]`);
  await detail.getByRole('button', { name: '定位报告正文', exact: true }).click();
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveValue(text);
});

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
  await expect(detail).toContainText('尚无关联的原文证据');
  await expect(detail).toContainText('第1句研究结果');
  await expect(detail).not.toContainText('Citation has no explicit');
  await expect(detail).not.toContainText('模型支持性检查');
  await expect(detail.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('选择列表末尾的引用后可定位对应报告章节，查看不修改原文', async ({ page }) => {
  const version = await unboundReport(page);
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  const last = page.getByRole('button', { name: '引用 14 · 待复核', exact: true });
  await last.click();
  const detail = page.getByRole('region', { name: '引用 14 详情', exact: true });
  await expect(detail).toBeInViewport();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: '引用 1 · 待复核', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await detail.getByRole('button', { name: '定位报告正文', exact: true }).click();
  await expect(page.locator('.sf-report-editor')).toBeFocused();
  await expect(page.getByTestId('report-preview')).toContainText('第14句研究结果');
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  const body = page.getByRole('textbox', { name: '报告正文', exact: true });
  await expect(body).toBeVisible();
  await expect(body).toHaveValue(version.blocks[0].text);
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

test('未保存正文修改时仍明确标记旧版本原文，相关引用待复核', async ({ page }) => {
  await mockReport(page);
  await page.goto('/projects/p1?tab=report');
  await page.locator('.sf-report-editor').getByRole('button', { name: '编辑正文', exact: true }).click();
  await page.getByRole('textbox', { name: '报告正文', exact: true }).fill('尚未保存的新解释 [@sf_one]');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await page.getByRole('button', { name: '引用 1 · 待复核', exact: true }).click();
  const detail = page.getByRole('region', { name: '引用 1 详情', exact: true });
  await expect(detail).toContainText('未保存修改');
  await expect(detail).toContainText('已保存版本');
  await expect(detail.getByRole('link', { name: '查看已保存版本原文依据', exact: true })).toHaveAttribute('href', '/projects/p1?tab=evidence&claim=claim1&evidence=e1');
  await expect(detail.getByRole('button', { name: /绑定/ })).toHaveCount(0);
});
