import { test, expect, Page } from '@playwright/test';
import { mockWorkspace } from './fixtures';

export async function mockEvidence(page: Page, options: { abstract?: boolean; unlocated?: boolean; conflict?: boolean } = {}) {
  const workspace = await mockWorkspace(page);
  workspace.papers[0].documents = [{ id: 'new-doc', kind: 'pdf', parse_status: 'ready' }];
  const field = { value: 'The method uses a graph.', claim_ids: ['claim1'], evidence_ids: ['e1'], review_status: 'needs_recheck' };
  const extraction = { id: 'ex1', revision: 1, project_id: 'p1', paper_version_id: 'va', document_version_ids: ['old-doc'],
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
      locator_status: options.unlocated ? 'unlocated' : 'located', quote_start: 0, quote_end: field.value.length },
      passage: { id: 'passage1', document_version_id: 'old-doc', text: field.value, page_number: options.abstract ? null : 2, start: 0, end: field.value.length },
      document: { id: 'old-doc', kind: options.abstract ? 'abstract' : 'pdf', sha256: 'abc', paper_version_id: 'va', original_name: 'source.pdf' }, reviews: [] }] } }));
  return extraction;
}

test('证据链接固定原文版本和物理页', async ({ page }) => {
  await mockEvidence(page);
  await page.goto('/projects/p1?tab=evidence');
  await page.getByRole('button', { name: '查看方法证据 科研论文 a' }).click();
  await expect(page.getByTestId('pdf-viewer')).toHaveAttribute('src', /old-doc\/file#page=2$/);
  await expect(page.getByText('物理页 2', { exact: true })).toBeVisible();
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
