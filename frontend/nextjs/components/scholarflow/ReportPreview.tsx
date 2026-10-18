'use client';
import { useEffect, useMemo, useState } from 'react';
import type { CitationBinding, ReportBlock } from '@/types/scholarflow';
import { markdownToHtml } from '@/helpers/markdownHelper';
import { reportPreview } from '@/lib/scholarflow/reportCitations';

export default function ReportPreview({ blocks, bindings, active }: {
  blocks: ReportBlock[]; bindings: CitationBinding[]; active: number;
}) {
  const rendered = useMemo(() => reportPreview(blocks, bindings, active), [blocks, bindings, active]);
  const [html, setHtml] = useState('');
  useEffect(() => {
    let alive = true;
    markdownToHtml(rendered.markdown).then(value => { if (alive) setHtml(value); });
    return () => { alive = false; };
  }, [rendered.markdown]);
  return <div className="sf-stack sf-report-preview">
    <div data-testid="report-preview" className="prose max-w-none" dangerouslySetInnerHTML={{ __html: html }} onClick={event => {
      const link = event.target instanceof Element ? event.target.closest('a') : null;
      const match = link?.getAttribute('href')?.match(/^#sf-reference-(\d+)$/);
      if (!match || !rendered.references.some(reference => reference.number === Number(match[1]))) return;
      event.preventDefault();
      const target = document.getElementById(`sf-reference-${match[1]}`);
      target?.focus({ preventScroll: true }); target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }} />
    {rendered.unresolved.length > 0 && <p role="status" className="sf-notice">有 {rendered.unresolved.length} 个引用未匹配，已保留原标识，请在编辑正文中核对文献。</p>}
    {rendered.references.length > 0 && <section aria-label="本报告参考文献" className="sf-report-references">
      <h4>本报告参考文献</h4><p className="sf-muted sf-small">编号按全文首次引用顺序排列。引用是否支持正文，请查看“引用核验”。</p>
      <ol>{rendered.references.map(reference => {
        const bibliography = reference.bibliography_snapshot;
        const title = typeof bibliography.title === 'string' ? bibliography.title : '论文标题未记录';
        const authors = Array.isArray(bibliography.authors) ? bibliography.authors.filter(item => typeof item === 'string').join(' · ') : '';
        const year = typeof bibliography.year === 'number' || typeof bibliography.year === 'string' ? String(bibliography.year) : '';
        return <li id={`sf-reference-${reference.number}`} tabIndex={-1} key={reference.key}>
          <strong>[{reference.number}] {title}</strong>{(authors || year) && <p className="sf-muted sf-small">{[authors, year].filter(Boolean).join(' · ')}</p>}
        </li>;
      })}</ol>
    </section>}
  </div>;
}
