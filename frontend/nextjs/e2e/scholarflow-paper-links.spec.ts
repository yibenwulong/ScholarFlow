import { test, expect } from '@playwright/test';
import { mockWorkspace, paper } from './fixtures';

test('文献卡片提供当前文献最新PDF下载，保留原文件名', async ({ page }) => {
  await mockWorkspace(page);
  const record = paper('a');
  await page.route('**/api/scholarflow/projects/p1/papers', route => route.fulfill({ json: { items: [{ ...record,
    version: { ...record.version, source: 'local', source_id: 'upload:fixture', identifiers: {}, pdf_url: null },
    documents: [
      { id: 'other-paper', paper_version_id: 'vb', revision: 1, created_at: '2026-02-01T00:00:00Z',
        kind: 'pdf', storage_key: 'other.pdf', sha256: 'other', original_name: '其他论文.pdf', parse_status: 'ready', parse_error: null },
      { id: 'older-pdf', paper_version_id: 'va', revision: 1, created_at: '2025-12-01T00:00:00Z',
        kind: 'pdf', storage_key: 'older.pdf', sha256: 'older', original_name: '旧版.pdf', parse_status: 'ready', parse_error: null },
      { id: 'uploaded-pdf', paper_version_id: 'va', revision: 1, created_at: '2026-01-01T00:00:00Z',
        kind: 'pdf', storage_key: 'fixture.pdf', sha256: 'fixture', original_name: '上传论文.pdf', parse_status: 'failed', parse_error: 'encrypted' },
    ],
  }] } }));
  await page.goto('/projects/p1?tab=papers');
  const article = page.getByRole('article').filter({ hasText: '科研论文 a' });
  const link = article.getByRole('link', { name: '下载 PDF', exact: true });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', '/api/scholarflow/documents/uploaded-pdf/file');
  await expect(link).toHaveAttribute('download', '上传论文.pdf');
  await expect(article.getByRole('checkbox')).not.toBeChecked();
});

test('PDF与论文详情链接对应具体文献，arXiv版本号保持不变', async ({ page }) => {
  await mockWorkspace(page);
  const first = paper('a'), second = paper('b');
  await page.route('**/api/scholarflow/projects/p1/papers', route => route.fulfill({ json: { items: [
    { ...first, version: { ...first.version, identifiers: { arxiv: '2601.00001v2' }, pdf_url: 'https://papers.example/paper-a.pdf' } },
    { ...second, version: { ...second.version, identifiers: { arxiv: '2602.00002v3' }, pdf_url: null } },
  ] } }));
  await page.goto('/projects/p1?tab=papers');
  const a = page.getByRole('article').filter({ hasText: '科研论文 a' });
  const b = page.getByRole('article').filter({ hasText: '科研论文 b' });
  await expect(a.getByRole('link', { name: 'PDF 下载链接', exact: true })).toHaveAttribute('href', 'https://papers.example/paper-a.pdf');
  await expect(a.getByRole('link', { name: '论文详情', exact: true })).toHaveAttribute('href', 'https://arxiv.org/abs/2601.00001v2');
  await expect(b.getByRole('link', { name: 'PDF 下载链接', exact: true })).toHaveAttribute('href', 'https://arxiv.org/pdf/2602.00002v3');
  await expect(b.getByRole('link', { name: '论文详情', exact: true })).toHaveAttribute('href', 'https://arxiv.org/abs/2602.00002v3');
});

test('只有DOI时提供详情页，不将网页或无来源记录冒充PDF下载', async ({ page }) => {
  await mockWorkspace(page);
  const first = paper('a'), second = paper('b');
  await page.route('**/api/scholarflow/projects/p1/papers', route => route.fulfill({ json: { items: [
    { ...first, version: { ...first.version, source: 'semantic_scholar', identifiers: { doi: '10.1234/paper-a' }, pdf_url: null } },
    { ...second, version: { ...second.version, source: 'local', source_id: 'upload:unknown', identifiers: {}, pdf_url: 'javascript:alert(1)' } },
  ] } }));
  await page.goto('/projects/p1?tab=papers');
  const a = page.getByRole('article').filter({ hasText: '科研论文 a' });
  const b = page.getByRole('article').filter({ hasText: '科研论文 b' });
  await expect(a.getByRole('link', { name: '论文详情', exact: true })).toHaveAttribute('href', 'https://doi.org/10.1234/paper-a');
  await expect(a.getByRole('link', { name: /PDF/ })).toHaveCount(0);
  await expect(a).toContainText('暂无公开 PDF 下载链接');
  await expect(b.getByRole('link')).toHaveCount(0);
  await expect(b).toContainText('暂无下载链接');
});
