'use client';
import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import type { Page, ReportDetail } from '@/types/scholarflow';
import { claimLabels } from '@/types/scholarflow';
import { scholarRequest } from '@/lib/scholarflow/api';

interface SourceOption { key: string; claim_revision_id: string; evidence_id: string }

export default function CitationInspector({ detail, dirtyBlocks, onLocate, disabled = false }: {
  detail: ReportDetail; dirtyBlocks: Set<string>; disabled?: boolean;
  onLocate: (blockId: string, tokenIndex: number, edit: boolean) => void;
}) {
  const [selected, setSelected] = useState<string>();
  const [sources, setSources] = useState<{ versionId: string; items: SourceOption[]; failed: boolean }>();
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const needsSources = detail.version.occurrences.some(item => item.claim_revision_ids.length && item.evidence_ids.length);
  useEffect(() => {
    setSources(undefined);
    if (!needsSources) return;
    let alive = true;
    const versionId = detail.version.id;
    scholarRequest<Page<SourceOption>>(`/reports/${versionId}/citation-options`)
      .then(value => { if (alive) setSources({ versionId, items: value.items, failed: false }); })
      .catch(() => { if (alive) setSources({ versionId, items: [], failed: true }); });
    return () => { alive = false; };
  }, [detail.version.id, needsSources, sourceAttempt]);
  const currentSources = sources?.versionId === detail.version.id ? sources : undefined;
  const panel = useRef<HTMLDivElement>(null);
  const selectedId = detail.version.occurrences.some(item => item.id === selected) ? selected : detail.version.occurrences[0]?.id;
  useEffect(() => { if (selected) panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [selected, detail.version.id]);
  function readable(text: string) {
    return text.replace(/\[@([^\]\s]+)\]|(?<![A-Za-z0-9_])sf_[A-Za-z0-9_]+/g, (match, key: string | undefined) => {
      const candidates = detail.version.bindings.filter(binding => binding.key === (key || match) ||
        (!key && /^sf_[a-fA-F0-9]{8}$/.test(match) && binding.key.startsWith(match)));
      const reference = candidates.length === 1 ? detail.references.find(item => item.key === candidates[0].key) : undefined;
      return reference ? `${key ? '' : '文献'}[${reference.number}]` : match;
    });
  }
  return <aside className="sf-card"><h3>逐处引用核验</h3><p className="sf-muted sf-small">检查报告的每一次引用是否有原文支持。点击条目查看句子、论文和原因。</p>
    <p className="sf-muted sf-small">共 {detail.version.occurrences.length} 处引用；同一篇论文的不同引用位置分别检查。</p><div className="sf-stack sf-citation-list" style={{ marginTop: 16 }}>
    {detail.version.occurrences.map((item, index) => { const result = detail.verification?.checks.find(check => check.occurrence_id === item.id);
      const block = detail.version.blocks.find(block => block.id === item.block_id);
      const dirty = dirtyBlocks.has(item.block_id);
      const characters = Array.from(block?.text || '');
      const hasSpan = item.claim_start !== null && item.claim_end !== null && Number.isInteger(item.claim_start) && Number.isInteger(item.claim_end) && item.claim_start >= 0 && item.claim_end > item.claim_start && item.claim_end <= characters.length;
      const bound = hasSpan && item.claim_revision_ids.length > 0 && item.evidence_ids.length > 0;
      const status = dirty || !bound ? 'needs_recheck' : result?.status || 'needs_recheck';
      const expanded = item.id === selectedId;
      const tokens = Array.from((block?.text || '').matchAll(/\[@([^\]\s]+)\]/g));
      const token = tokens[item.token_index];
      const binding = detail.version.bindings.find(binding => binding.key === token?.[1]);
      const reference = detail.references.find(reference => reference.key === binding?.key);
      const sourceLinks = currentSources?.items.filter(source => source.key === binding?.key &&
        item.claim_revision_ids.includes(source.claim_revision_id) && item.evidence_ids.includes(source.evidence_id)) || [];
      const title = binding?.bibliography_snapshot.title;
      let context = hasSpan ? characters.slice(item.claim_start!, item.claim_end!).join('') : '无法在已保存正文中定位此引用，请检查报告版本。';
      if (!hasSpan && token && block) {
        let start = Math.max(0, token.index! - 160);
        let end = Math.min(block.text.length, token.index! + token[0].length + 80);
        // Keep adjacent citation tokens whole so the display can resolve their reference numbers.
        for (const adjacent of tokens) {
          const tokenStart = adjacent.index!, tokenEnd = tokenStart + adjacent[0].length;
          if (tokenStart < start && start < tokenEnd) start = tokenStart;
          if (tokenStart < end && end < tokenEnd) end = tokenEnd;
        }
        context = block.text.slice(start, end);
      }
      const reason = dirty ? '正文有未保存修改。下方显示已保存版本的内容，请先保存并重新复核。'
        : !bound ? '这处引用尚未绑定具体句子和原文证据，暂时无法判断论文是否支持这句话。'
        : result?.reason || '此处已有原文绑定，仍需要复核是否支持报告中的句子。';
      const checker = result?.checker?.startsWith('human') ? '人工复核' : result?.checker === 'model_support_v1' ? '模型支持性检查'
        : result?.checker === 'source_guard_v1' ? '引用完整性检查' : '辅助核验';
      return <div key={item.id}>
        <button aria-label={`引用 ${index + 1} · ${claimLabels[status]}`} aria-expanded={expanded} aria-controls={`sf-citation-detail-${item.id}`}
          onClick={() => { setSelected(item.id); if (expanded) panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }}
          className={`sf-citation-button ${status === 'supported' ? '' : 'sf-citation-issue'} ${expanded ? 'sf-citation-selected' : ''}`}>
          引用 {index + 1} · {claimLabels[status]}</button>
        {expanded && block && <div ref={panel} id={`sf-citation-detail-${item.id}`} role="region" aria-label={`引用 ${index + 1} 详情`} className="sf-citation-detail">
          <p className="sf-small sf-muted">所在章节：{block.heading || '正文'}</p>
          <p><strong>对应论文：</strong>{reference ? `[${reference.number}] ` : ''}{typeof title === 'string' ? title : '文献信息未匹配'}</p>
          <h4>{hasSpan ? '报告中的句子' : '引用上下文（尚未绑定具体句子）'}</h4>
          <blockquote className="sf-source-quote">{readable(context)}</blockquote>
          {!bound && <p className="sf-notice">未绑定原文证据。需要先选择报告中的句子及对应原文，再进行复核。</p>}
          <p>{reason}</p><p className="sf-muted sf-small">检查方式：{checker}</p>
          <div className="sf-stack" style={{ marginTop: 12 }}>
            <button disabled={disabled} onClick={() => onLocate(item.block_id, item.token_index, false)}>定位报告正文</button>
            <button disabled={disabled || dirty} onClick={() => onLocate(item.block_id, item.token_index, true)}>{bound ? '定位正文并编辑绑定' : '定位正文并补充绑定'}</button>
            {bound && !currentSources && <p role="status" className="sf-muted sf-small">正在载入原文对应关系…</p>}
            {bound && currentSources?.failed && <div className="sf-notice"><p>原文对应关系暂时无法加载，未生成未经确认的原文入口。</p><button onClick={() => setSourceAttempt(previous => previous + 1)}>重新加载原文信息</button></div>}
            {bound && currentSources && !currentSources.failed && sourceLinks.length < new Set(item.evidence_ids).size && <p className="sf-notice">部分原文对应关系未找到，请核对或重新绑定。</p>}
            {bound && sourceLinks.map(source => <Link className="sf-button" key={source.evidence_id}
              href={`/projects/${detail.version.project_id}?tab=evidence&claim=${encodeURIComponent(source.claim_revision_id)}&evidence=${encodeURIComponent(source.evidence_id)}`}>{dirty ? '查看已保存版本原文依据' : '查看此处原文依据'}</Link>)}
          </div>
        </div>}
      </div>; })}</div>
    {!detail.version.occurrences.length && <p className="sf-notice">报告中还没有可检查的引用。</p>}
    <p className="sf-muted sf-small" style={{ marginTop: 20 }}>模型判断是辅助复核，不能作为事实保证。</p>
  </aside>;
}
