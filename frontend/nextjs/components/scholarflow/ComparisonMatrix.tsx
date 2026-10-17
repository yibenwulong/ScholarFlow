'use client';
import { useState } from 'react';
import type { ExtractedField, Extraction, FieldName, ProjectPaper } from '@/types/scholarflow';
import { claimLabels, fieldLabels } from '@/types/scholarflow';
import { messageFor, scholarRequest, write } from '@/lib/scholarflow/api';

type ReadingScope = 'abstract' | 'fulltext' | 'mixed' | 'unknown';
const scopeLabels: Record<ReadingScope, string> = { abstract: '阅读范围：摘要', fulltext: '阅读范围：全文', mixed: '阅读范围：摘要与全文', unknown: '阅读范围未记录' };
function classifyScope(scopes: Array<string | undefined>): ReadingScope {
  if (!scopes.length || scopes.some(scope => scope !== 'abstract' && scope !== 'fulltext')) return 'unknown';
  return scopes.every(scope => scope === 'abstract') ? 'abstract' : scopes.every(scope => scope === 'fulltext') ? 'fulltext' : 'mixed';
}
function readingScope(extraction: Extraction, paper?: ProjectPaper): ReadingScope {
  const recorded = classifyScope((extraction.coverage?.fragments || []).map(fragment => fragment.source_scope));
  if (recorded !== 'unknown') return recorded;
  return classifyScope(extraction.document_version_ids.map(id => {
    const document = paper?.documents.find(item => item.id === id);
    return document?.kind === 'pdf' ? 'fulltext' : document?.kind === 'abstract' ? 'abstract' : undefined;
  }));
}
function emptyLabel(extraction: Extraction, cell: ExtractedField, scope: ReadingScope) {
  if (!cell.missing_reason?.trim()) return extraction.origin === 'human' ? '人工未填写' : '尚未完整提取';
  return scope === 'abstract' ? '当前摘要未提供' : scope === 'fulltext' ? '所读全文未提供' : '所读文本未提供';
}

export default function ComparisonMatrix({ projectId, papers, extractions, onEvidence, onSaved }: {
  projectId: string; papers: ProjectPaper[]; extractions: Extraction[];
  onEvidence: (claimId: string, evidenceId?: string) => void; onSaved: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<{ paperId: string; versionId: string; field: FieldName; revision: number; value: string }>();
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function save() {
    if (!editing) return;
    setBusy(true); setError('');
    try { await scholarRequest(`/projects/${projectId}/extractions/${editing.paperId}`, write('PATCH', {
      expected_revision: editing.revision, paper_version_id: editing.versionId, changes: { [editing.field]: editing.value || null } }));
      setEditing(undefined); await onSaved();
    } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); }
  }
  return <section className="sf-card"><h2>文献比较矩阵</h2><p className="sf-muted">空白项会区分尚未完整提取、所读文本未提供和人工未填写。人工修改保存为新修订，相关报告需重新复核。</p>
    {error && <p role="alert" className="sf-error" style={{ marginTop: 14 }}>{error}</p>}
    <div className="sf-matrix-scroll"><table className="sf-matrix"><thead><tr><th>论文</th>{(Object.keys(fieldLabels) as FieldName[]).map(field => <th key={field}>{fieldLabels[field]}</th>)}</tr></thead><tbody>
      {extractions.map(extraction => { const paper = papers.find(p => p.paper_version_id === extraction.paper_version_id), scope = readingScope(extraction, paper); return <tr key={extraction.id}>
        <th style={{ minWidth: 180 }}>{paper?.version.title || extraction.paper_version_id}<div className="sf-muted sf-small">修订 {extraction.revision}</div><div className="sf-muted sf-small">{scopeLabels[scope]}</div></th>
        {(Object.keys(fieldLabels) as FieldName[]).map(field => { const cell = extraction.fields[field], hasValue = !!cell.value?.trim(); const active = editing?.paperId === paper?.paper_id && editing?.field === field;
          return <td key={field} style={{ minWidth: 220 }}>{active && editing ? <><label htmlFor="cell-edit">修改{fieldLabels[field]}</label><textarea id="cell-edit" value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })} />
            <div className="sf-row"><button disabled={busy} onClick={() => void save()}>保存提取修订</button><button onClick={() => { setEditing(undefined); setError(''); }}>取消</button></div></> :
            <><p style={{ whiteSpace: 'pre-wrap' }}>{hasValue ? cell.value : emptyLabel(extraction, cell, scope)}</p>{hasValue && <p className="sf-muted sf-small">{extraction.origin === 'human' && !cell.evidence_ids.length ? '人工修改 · 待复核' : claimLabels[cell.review_status]}</p>}
              {!hasValue && cell.missing_reason?.trim() && <details style={{ marginTop: 8 }}><summary>查看缺失原因</summary><p className="sf-small" style={{ whiteSpace: 'pre-wrap' }}>{cell.missing_reason}</p></details>}
              <div className="sf-row" style={{ marginTop: 10 }}>{cell.claim_ids.length > 0 && <button aria-label={`查看${fieldLabels[field]}证据 ${paper?.version.title}`} onClick={() => onEvidence(cell.claim_ids[0], cell.evidence_ids[0])}>查看证据</button>}
                {paper && <button aria-label={`编辑${fieldLabels[field]} ${paper.version.title}`} onClick={() => { setError(''); setEditing({ paperId: paper.paper_id, versionId: extraction.paper_version_id, field, revision: extraction.revision, value: cell.value || '' }); }}>编辑</button>}</div></>}
          </td>; })}</tr>; })}</tbody></table></div>
    {!extractions.length && <div className="sf-empty">完成纳入文献的阅读后，提取项会显示在这里。</div>}
  </section>;
}
