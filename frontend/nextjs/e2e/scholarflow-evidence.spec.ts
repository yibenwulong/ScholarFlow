import { test, expect, Page } from '@playwright/test';
import { mockWorkspace } from './fixtures';
import type { ExtractedField, Extraction } from '../types/scholarflow';

export async function mockEvidence(page: Page, options: { abstract?: boolean; unlocated?: boolean; conflict?: boolean } = {}) {
  const workspace = await mockWorkspace(page);
  workspace.papers[0].documents = [{ id: 'new-doc', kind: 'pdf', parse_status: 'ready' }];
  const text = 'The method uses a graph.';
  const field: ExtractedField = { value: text, claim_ids: ['claim1'], evidence_ids: ['e1'], review_status: 'needs_recheck' };
  const extraction: Extraction = { id: 'ex1', revision: 1, created_at: '2026-01-01T00:00:00Z', previous_id: null, project_id: 'p1', paper_version_id: 'va', document_version_ids: ['old-doc'],
    origin: 'ai', fields: { research_question: { ...field, value: null, claim_ids: [], evidence_ids: [] }, method: field,
      dataset: { ...field, value: null, claim_ids: [], evidence_ids: [] }, findings: { ...field, value: null, claim_ids: [], evidence_ids: [] }, limitations: { ...field, value: null, claim_ids: [], evidence_ids: [] } } };
  await page.route('**/api/scholarflow/projects/p1/extractions**', async route => {
    if (route.request().method() === 'PATCH') {
      if (options.conflict) return route.fulfill({ status: 409, json: { error: { code: 'revision_conflict', message: '此提取项有新修订' } } });
      const data = route.request().postDataJSON() as { changes: Record<string, string> };
      extraction.revision++; extraction.origin = 'human';
      extraction.fields.method = { ...field, value: data.changes.method, evidence_ids: [], claim_ids: ['human1'] };
      return route.fulfill({ json: extraction });
    }
    return route.fulfill({ json: route.request().url().endsWith('/extractions') ? { items: [extraction] } : extraction });
  });
  await page.route('**/api/scholarflow/claims/*/evidence', route => route.fulfill({ json: {
    claim: { id: 'claim1', text: field.value, source_scope: options.abstract ? 'abstract' : 'fulltext', status: 'needs_recheck' },
    items: [{ evidence: { id: 'e1', claim_revision_id: 'claim1', passage_id: 'passage1', quote: field.value,
      locator_status: options.unlocated ? 'unlocated' : 'located', quote_start: 0, quote_end: text.length },
      passage: { id: 'passage1', document_version_id: 'old-doc', text: field.value, page_number: options.abstract ? null : 2, start: 0, end: text.length },
      document: { id: 'old-doc', kind: options.abstract ? 'abstract' : 'pdf', sha256: 'abc', paper_version_id: 'va', original_name: 'source.pdf' }, reviews: [] }] } }));
  return extraction;
}

test('证据链接固定原文版本和物理页', async ({ page }) => {
  await mockEvidence(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /old-doc\/file#page=2$/);
  await expect(page.getByText('物理页 2', { exact: true })).toBeVisible();
  await expect(page.getByRole('combobox', { name: '选择提取结论' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '选择原文依据' })).toHaveCount(0);
});

test('摘要和未定位引句有明确边界', async ({ page }) => {
  await mockEvidence(page, { abstract: true, unlocated: true });
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  await expect(page.getByText('仅摘要依据', { exact: true })).toBeVisible();
  await expect(page.getByText('引句未定位', { exact: true })).toBeVisible();
  await expect(page.getByTestId('pdf-viewer')).toHaveCount(0);
});

test('人工编辑创建待复核修订', async ({ page }) => {
  await mockEvidence(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '编辑方法 科研论文 a' }).click();
  await page.getByRole('textbox', { name: '修改方法' }).fill('人工修正的方法');
  await page.getByRole('button', { name: '保存提取修订' }).click();
  await expect(page.getByText('人工修改 · 待复核', { exact: true })).toBeVisible();
});

test('矩阵冲突不丢失输入', async ({ page }) => {
  await mockEvidence(page, { conflict: true });
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '编辑方法 科研论文 a' }).click();
  await page.getByRole('textbox', { name: '修改方法' }).fill('未保存的修正');
  await page.getByRole('button', { name: '保存提取修订' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '输入已保留' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '修改方法' })).toHaveValue('未保存的修正');
});

async function mockMultipleEvidence(page: Page, options: { unpaged?: boolean } = {}) {
  const extraction = await mockEvidence(page);
  extraction.fields.method.claim_ids = ['claim1', 'claim2'];
  extraction.fields.method.evidence_ids = ['e1', 'e2', 'e3'];
  extraction.fields.dataset = { value: 'Separate dataset conclusion.', claim_ids: ['dataset-claim'], evidence_ids: ['dataset-evidence'], review_status: 'needs_recheck' };
  const makeItem = (id: string, claim: string, quote: string, pageNumber: number | null, reviewReason: string, revision: number) => ({
    evidence: { id, claim_revision_id: claim, passage_id: 'passage-' + id, quote,
      locator_status: 'located', quote_start: 0, quote_end: quote.length },
    passage: { id: 'passage-' + id, document_version_id: pageNumber === null ? 'abstract-doc' : 'old-doc',
      text: quote, page_number: pageNumber, start: 0, end: quote.length },
    document: { id: pageNumber === null ? 'abstract-doc' : 'old-doc', kind: pageNumber === null ? 'abstract' : 'pdf',
      sha256: 'hash-' + id, paper_version_id: 'va', original_name: 'source.pdf' },
    reviews: [{ id: 'review-' + id, revision, evidence_id: id, verdict: 'inferred', reason: reviewReason, reviewer: 'fixture' }],
  });
  const bundles = {
    claim1: { claim: { id: 'claim1', text: 'First extracted conclusion.', source_scope: 'fulltext', status: 'needs_recheck' },
      items: [makeItem('e1', 'claim1', 'Page two supports the first conclusion.', 2, '第二页旧复核', 1),
        makeItem('e2', 'claim1', 'Page seven is a separate source anchor.', 7, '第七页旧复核', 4)] },
    claim2: { claim: { id: 'claim2', text: 'Second extracted conclusion.', source_scope: 'abstract', status: 'needs_recheck' },
      items: [makeItem('e3', 'claim2', 'The abstract supports the second conclusion.', null, '摘要旧复核', 2)] },
  };
  if (options.unpaged) bundles.claim1.items[0].passage.page_number = null;
  const claimRequests: string[] = [];
  const reviewRequests: Array<{ evidenceId: string; data: { expected_revision: number; verdict: string; reason: string } }> = [];
  await page.route('**/api/scholarflow/claims/*/evidence', route => {
    const claim = new URL(route.request().url()).pathname.split('/').at(-2) as keyof typeof bundles;
    claimRequests.push(claim);
    return route.fulfill({ json: bundles[claim] });
  });
  await page.route('**/api/scholarflow/evidence/*/reviews', route => {
    const evidenceId = new URL(route.request().url()).pathname.split('/').at(-2)!;
    const data = route.request().postDataJSON();
    reviewRequests.push({ evidenceId, data });
    const item = Object.values(bundles).flatMap(bundle => bundle.items).find(item => item.evidence.id === evidenceId)!;
    item.reviews.push({ id: 'new-review', revision: data.expected_revision + 1, evidence_id: evidenceId,
      verdict: data.verdict, reason: data.reason, reviewer: 'fixture' });
    return route.fulfill({ json: item.reviews.at(-1) });
  });
  return { claimRequests, reviewRequests };
}

test('同一提取字段可切换所有结论并重置原文依据', async ({ page }) => {
  const state = await mockMultipleEvidence(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  const claims = page.getByRole('combobox', { name: '选择提取结论' });
  const evidence = page.getByRole('combobox', { name: '选择原文依据' });
  await expect(claims).toBeVisible();
  await expect(claims.locator('option')).toHaveText(['结论 1', '结论 2']);
  await evidence.selectOption('e2');
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /old-doc\/file#page=7$/);
  await claims.selectOption('claim2');
  await expect(page.getByText('Second extracted conclusion.', { exact: true })).toBeVisible();
  await expect(page.locator('.sf-source-quote')).toHaveText('The abstract supports the second conclusion.');
  await expect(page.getByText('仅摘要依据', { exact: true })).toBeVisible();
  await expect(evidence).toHaveCount(0);
  await expect(page.getByTestId('pdf-viewer')).toHaveCount(0);
  await page.getByText('记录人工复核', { exact: true }).click();
  await expect(page.getByText('推断 · 摘要旧复核', { exact: true })).toBeVisible();
  await expect(page.getByText('推断 · 第七页旧复核', { exact: true })).toHaveCount(0);
  await claims.selectOption('claim1');
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /old-doc\/file#page=2$/);
  await expect(evidence).toHaveValue('e1');
  expect(state.claimRequests).toEqual(['claim1', 'claim2', 'claim1']);
});

test('多锚点按独立页码切换且复核记录不混淆', async ({ page }) => {
  const state = await mockMultipleEvidence(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  const evidence = page.getByRole('combobox', { name: '选择原文依据' });
  await expect(evidence).toBeVisible();
  await expect(evidence.locator('option')).toHaveText(['物理页 2 · 依据 1', '物理页 7 · 依据 2']);
  await page.getByText('记录人工复核', { exact: true }).click();
  await expect(page.getByText('推断 · 第二页旧复核', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: '复核理由' }).fill('仅第二页的未保存草稿');
  await evidence.selectOption('e2');
  await expect(page.locator('.sf-source-quote')).toHaveText('Page seven is a separate source anchor.');
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /old-doc\/file#page=7$/);
  await expect(page.getByRole('textbox', { name: '复核理由' })).toHaveValue('');
  await expect(page.getByText('推断 · 第七页旧复核', { exact: true })).toBeVisible();
  await expect(page.getByText('推断 · 第二页旧复核', { exact: true })).toHaveCount(0);
  await page.getByRole('combobox', { name: '复核结论' }).selectOption('supported');
  await page.getByRole('textbox', { name: '复核理由' }).fill('第七页单独确认');
  await page.getByRole('button', { name: '保存复核记录' }).click();
  await expect(page.getByText('支持 · 第七页单独确认', { exact: true })).toBeVisible();
  expect(state.reviewRequests).toEqual([{ evidenceId: 'e2', data: { expected_revision: 4, verdict: 'supported', reason: '第七页单独确认' } }]);
  await evidence.selectOption('e1');
  await expect(page.locator('.sf-source-quote')).toHaveText('Page two supports the first conclusion.');
  await expect(page.getByText('支持 · 第七页单独确认', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: '人工复核已记录' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '复核结论' })).toHaveValue('needs_recheck');
  await evidence.selectOption('e2');
  await expect(page.getByText('支持 · 第七页单独确认', { exact: true })).toBeVisible();
});

test('无页码全文依据不会标为摘要', async ({ page }) => {
  await mockMultipleEvidence(page, { unpaged: true });
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  await expect(page.getByRole('combobox', { name: '选择原文依据' }).locator('option')).toHaveText(['全文片段 · 依据 1', '物理页 7 · 依据 2']);
});
