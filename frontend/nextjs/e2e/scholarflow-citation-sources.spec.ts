import { test, expect } from '@playwright/test';
import { mockReport } from './report-fixtures';

test('手动切换绑定位置后再次定位同一引用，正文与绑定控件仍指向同一处', async ({ page }) => {
  const state = await mockReport(page);
  const text = 'First finding. [@sf_one]\nSecond finding. [@sf_one]';
  state.version.blocks[0].text = text;
  state.version.blocks[1].text = '其他内容';
  state.version.occurrences[0].claim_end = 'First finding.'.length;
  state.version.occurrences[1] = { ...state.version.occurrences[1], block_id: 'b1', token_index: 1,
    claim_start: text.indexOf('Second'), claim_end: text.indexOf('Second') + 'Second finding.'.length };
  await page.goto('/projects/p1?tab=report');
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.getByRole('button', { name: '引用核验', exact: true }).click();
    await page.getByRole('button', { name: '引用 2 · 冲突', exact: true }).click();
    await page.getByRole('region', { name: '引用 2 详情', exact: true }).getByRole('button', { name: '定位正文并编辑绑定', exact: true }).click();
    await expect(page.getByRole('combobox', { name: '引用位置', exact: true })).toHaveValue('1');
    if (!attempt) await page.getByRole('combobox', { name: '引用位置', exact: true }).selectOption('0');
  }
  await expect(page.getByRole('textbox', { name: '报告正文', exact: true })).toHaveValue(text);
  await expect(page.getByText('存在未保存修改', { exact: true })).toHaveCount(0);
});

test('一处引用的多论断与乱序原文按真实关系链接，只展示本处使用的依据', async ({ page }) => {
  const state = await mockReport(page);
  state.version.occurrences[0].claim_revision_ids = ['claim1', 'claim2'];
  state.version.occurrences[0].evidence_ids = ['e2', 'e1', 'e3'];
  await page.route('**/api/scholarflow/reports/*/citation-options', route => route.fulfill({ json: { items: [
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'e1', quote: '原文一', source_scope: 'fulltext', locator_status: 'located' },
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim2', evidence_id: 'e2', quote: '原文二', source_scope: 'abstract', locator_status: 'located' },
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'e3', quote: '原文三', source_scope: 'fulltext', locator_status: 'located' },
    { key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'unused', quote: '本处未引用', source_scope: 'fulltext', locator_status: 'located' },
  ] } }));
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  await page.getByRole('button', { name: '引用 1 · 支持', exact: true }).click();
  const detail = page.getByRole('region', { name: '引用 1 详情', exact: true });
  const links = detail.getByRole('link', { name: '查看此处原文依据', exact: true });
  await expect(links).toHaveCount(3);
  expect((await links.evaluateAll(nodes => nodes.map(node => node.getAttribute('href')))).sort()).toEqual([
    '/projects/p1?tab=evidence&claim=claim1&evidence=e1',
    '/projects/p1?tab=evidence&claim=claim1&evidence=e3',
    '/projects/p1?tab=evidence&claim=claim2&evidence=e2',
  ]);
});

test('原文关系加载失败时不猜测入口，可只读重试恢复对应来源', async ({ page }) => {
  await mockReport(page);
  let available = false;
  await page.route('**/api/scholarflow/reports/*/citation-options', route => {
    return !available ? route.fulfill({ status: 503, json: { error: { code: 'temporary_failure', message: '稍后重试' } } })
      : route.fulfill({ json: { items: [{ key: 'sf_one', title: '科研论文 a', claim_revision_id: 'claim1', evidence_id: 'e1', quote: '原文', source_scope: 'fulltext', locator_status: 'located' }] } });
  });
  await page.goto('/projects/p1?tab=report');
  await page.getByRole('button', { name: '引用核验', exact: true }).click();
  const detail = page.getByRole('region', { name: '引用 1 详情', exact: true });
  await expect(detail).toContainText('原文对应关系暂时无法加载');
  await expect(detail.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveCount(0);
  available = true;
  await detail.getByRole('button', { name: '重新加载原文信息', exact: true }).click();
  await expect(detail.getByRole('link', { name: '查看此处原文依据', exact: true })).toHaveAttribute('href', '/projects/p1?tab=evidence&claim=claim1&evidence=e1');
});
