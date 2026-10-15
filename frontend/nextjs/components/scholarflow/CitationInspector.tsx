'use client';
import { useState } from 'react';
import Link from 'next/link';
import type { ReportDetail } from '@/types/scholarflow';
import { claimLabels } from '@/types/scholarflow';

export default function CitationInspector({ detail, dirtyBlocks }: { detail: ReportDetail; dirtyBlocks: Set<string> }) {
  const [selected, setSelected] = useState<string>();
  const occurrence = detail.version.occurrences.find(item => item.id === selected);
  const block = detail.version.blocks.find(block => block.id === occurrence?.block_id);
  const check = detail.verification?.checks.find(check => check.occurrence_id === occurrence?.id);
  return <aside className="sf-card"><h3>逐处引用核验</h3><p className="sf-muted sf-small">同一篇论文的不同引用位置分别检查。</p><div className="sf-stack" style={{ marginTop: 16 }}>
    {detail.version.occurrences.map((item, index) => { const result = detail.verification?.checks.find(check => check.occurrence_id === item.id);
      const status = dirtyBlocks.has(item.block_id) ? 'needs_recheck' : result?.status || 'needs_recheck';
      return <button key={item.id} aria-label={`引用 ${index + 1} · ${claimLabels[status]}`} onClick={() => setSelected(item.id)} className={status === 'supported' ? '' : 'sf-citation-issue'}>
        引用 {index + 1} · {claimLabels[status]}</button>; })}</div>
    {occurrence && block && <div style={{ marginTop: 20 }}><h3>此位置的论断</h3><blockquote className="sf-source-quote">{occurrence.claim_start !== null && occurrence.claim_end !== null ? Array.from(block.text).slice(occurrence.claim_start, occurrence.claim_end).join('') : '此引用尚未绑定明确论断范围'}</blockquote>
      <p style={{ marginTop: 12 }}>{check?.reason || '需要重新建立原文依据并复核'}</p><p className="sf-muted sf-small">检查方式：{check?.checker?.startsWith('human') ? '人工记录' : '定位与模型支持性检查'}</p>
      {occurrence.claim_revision_ids.map((id, index) => <Link className="sf-button" style={{ marginTop: 12 }} key={id}
        href={`/projects/${detail.version.project_id}?tab=evidence&claim=${encodeURIComponent(id)}&evidence=${encodeURIComponent(occurrence.evidence_ids[index] || occurrence.evidence_ids[0] || '')}`}>查看此处原文依据</Link>)}
    </div>}
    <p className="sf-muted sf-small" style={{ marginTop: 20 }}>模型判断是辅助复核，不能作为事实保证。</p>
  </aside>;
}
