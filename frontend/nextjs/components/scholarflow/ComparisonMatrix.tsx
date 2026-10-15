'use client';
import { useState } from 'react';
import type { Extraction, FieldName, ProjectPaper } from '@/types/scholarflow';
import { claimLabels, fieldLabels } from '@/types/scholarflow';
import { messageFor, scholarRequest, write } from '@/lib/scholarflow/api';

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
  return <section className="sf-card"><h2>文献比较矩阵</h2><p className="sf-muted">缺失项保留为“未报告”。人工修改保存为新修订，相关报告需重新复核。</p>
    {error && <p role="alert" className="sf-error" style={{ marginTop: 14 }}>{error}</p>}
    <div className="sf-matrix-scroll"><table className="sf-matrix"><thead><tr><th>论文</th>{(Object.keys(fieldLabels) as FieldName[]).map(field => <th key={field}>{fieldLabels[field]}</th>)}</tr></thead><tbody>
      {extractions.map(extraction => { const paper = papers.find(p => p.paper_version_id === extraction.paper_version_id); return <tr key={extraction.id}>
        <th style={{ minWidth: 180 }}>{paper?.version.title || extraction.paper_version_id}<div className="sf-muted sf-small">修订 {extraction.revision}</div></th>
        {(Object.keys(fieldLabels) as FieldName[]).map(field => { const cell = extraction.fields[field]; const active = editing?.paperId === paper?.paper_id && editing?.field === field;
          return <td key={field} style={{ minWidth: 220 }}>{active && editing ? <><label htmlFor="cell-edit">修改{fieldLabels[field]}</label><textarea id="cell-edit" value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })} />
            <div className="sf-row"><button disabled={busy} onClick={() => void save()}>保存提取修订</button><button onClick={() => { setEditing(undefined); setError(''); }}>取消</button></div></> :
            <><p style={{ whiteSpace: 'pre-wrap' }}>{cell.value || '未报告'}</p>{cell.value && <p className="sf-muted sf-small">{extraction.origin === 'human' && !cell.evidence_ids.length ? '人工修改 · 待复核' : claimLabels[cell.review_status]}</p>}
              <div className="sf-row" style={{ marginTop: 10 }}>{cell.claim_ids.length > 0 && <button aria-label={`查看${fieldLabels[field]}证据 ${paper?.version.title}`} onClick={() => onEvidence(cell.claim_ids[0], cell.evidence_ids[0])}>查看证据</button>}
                {paper && <button aria-label={`编辑${fieldLabels[field]} ${paper.version.title}`} onClick={() => { setError(''); setEditing({ paperId: paper.paper_id, versionId: extraction.paper_version_id, field, revision: extraction.revision, value: cell.value || '' }); }}>编辑</button>}</div></>}
          </td>; })}</tr>; })}</tbody></table></div>
    {!extractions.length && <div className="sf-empty">完成纳入文献的阅读后，提取项会显示在这里。</div>}
  </section>;
}
