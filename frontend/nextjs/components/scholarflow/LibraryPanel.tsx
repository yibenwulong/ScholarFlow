'use client';
import { useEffect, useState } from 'react';
import type { Decision, Plan, ProjectPaper, Run, Selection } from '@/types/scholarflow';
import { decisionLabels } from '@/types/scholarflow';
import { api, keyFor, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';

export default function LibraryPanel({ projectId, papers, selections, plans, run, onRefresh }: {
  projectId: string; papers: ProjectPaper[]; selections: Selection[]; plans: Plan[]; run?: Run; onRefresh: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<string[]>(selections[0]?.paper_version_ids || []), [dirty, setDirty] = useState(false);
  const [filter, setFilter] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [identifier, setIdentifier] = useState('');
  const [limit, setLimit] = useState(20);
  useEffect(() => { scholarRequest<{ max_included_papers: number }>('/status').then(status => { if (status.max_included_papers > 0) setLimit(status.max_included_papers); }).catch(() => {}); }, []);
  useEffect(() => { if (!dirty) setSelected(selections[0]?.paper_version_ids || []); }, [selections, dirty]);
  async function perform(operation: () => Promise<void>) { setBusy(true); setError(''); try { await operation(); } catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); } }
  async function analyze() {
    const approved = plans.find(plan => plan.approved);
    if (!approved) throw new Error('请先批准检索计划，再分析纳入文献。');
    const snapshot = await scholarRequest<Selection>(`/projects/${projectId}/selections`, write('POST', {
      expected_revision: selections[0]?.revision || 0, paper_version_ids: selected }, keyFor()));
    if (run?.status === 'waiting_for_user' && run.pending_action?.type === 'confirm_selection') await api.action(run, 'continue', { selection_id: snapshot.id });
    else await api.start(projectId, { start_at: 'analyze', plan_revision_id: approved.id, selection_id: snapshot.id });
    setDirty(false); setNotice('纳入集合已确认，分析任务已创建'); await onRefresh();
  }
  async function upload(file: File, versionId?: string) {
    const body = new FormData(); body.append('file', file); if (versionId) body.append('paper_version_id', versionId);
    await scholarRequest(`/projects/${projectId}/imports`, { method: 'POST', body, headers: { 'Idempotency-Key': keyFor() } });
    setNotice('PDF 已接收，正在导入和解析'); await onRefresh();
  }
  return <div className="sf-stack"><section className="sf-card"><div className="sf-between"><div><h2>文献库与人工纳排</h2><p className="sf-muted">AI 建议供你参考。只确认本次明确选择的文献，其他待决定候选会保留。</p></div><span className="sf-badge">{papers.length} 篇候选</span></div>
    {error && <p role="alert" className="sf-error" style={{ marginTop: 14 }}>{error}</p>}{notice && <p role="status" className="sf-notice" style={{ marginTop: 14 }}>{notice}</p>}
    <div className="sf-grid" style={{ marginTop: 18 }}><div><label htmlFor="paper-filter">筛选文献</label><input id="paper-filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="标题或作者" /></div>
      <div><label htmlFor="pdf-upload">导入 PDF</label><input id="pdf-upload" type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void perform(() => upload(file)); e.target.value = ''; }} /></div></div>
    <details style={{ marginTop: 18 }}><summary>通过 DOI 或 arXiv 标识导入</summary><label htmlFor="identifier">DOI / arXiv 标识</label><div className="sf-row"><input id="identifier" value={identifier} onChange={e => setIdentifier(e.target.value)} placeholder="10.xxxx/… 或 2401.12345v1" style={{ flex: 1 }} />
      <button disabled={busy || !identifier.trim()} onClick={() => void perform(async () => { const value = identifier.trim(); await scholarRequest(`/projects/${projectId}/imports`, write('POST', { identifiers: value.includes('10.') ? { doi: value } : { arxiv: value } }, keyFor())); setNotice('标识导入任务已创建'); setIdentifier(''); await onRefresh(); })}>导入标识</button></div></details>
    <div className="sf-between" style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #e4e9f2' }}><span className="sf-muted">本次选择 <strong style={{ color: '#2855d9' }}>{selected.length}</strong> 篇 · 最多 {limit} 篇</span>
      <button className="sf-primary" disabled={busy || !selected.length || selected.length > limit} onClick={() => void perform(analyze)}>确认纳入并分析</button></div>
  </section>
    {papers.filter(paper => (paper.version.title + paper.version.authors.join(' ')).toLowerCase().includes(filter.toLowerCase())).map(paper => <PaperRow key={paper.id} paper={paper}
      selected={selected.includes(paper.paper_version_id)} onSelect={checked => { setDirty(true); setSelected(values => checked ? [...values, paper.paper_version_id] : values.filter(id => id !== paper.paper_version_id)); }}
      onRefresh={onRefresh} onUpload={file => perform(() => upload(file, paper.paper_version_id))} />)}
    {!papers.length && <div className="sf-card sf-empty"><h3>还没有候选文献</h3><p>批准计划开始检索，或先导入你的论文资料。</p></div>}
  </div>;
}

function PaperRow({ paper, selected, onSelect, onRefresh, onUpload }: { paper: ProjectPaper; selected: boolean; onSelect: (checked: boolean) => void;
  onRefresh: () => Promise<void>; onUpload: (file: File) => Promise<void> }) {
  const [decision, setDecision] = useState<Decision>(paper.human_decision), [reason, setReason] = useState(paper.reason), [notes, setNotes] = useState(paper.notes);
  const [baseRevision, setBaseRevision] = useState(paper.revision), [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { if (!dirty) { setDecision(paper.human_decision); setReason(paper.reason); setNotes(paper.notes); setBaseRevision(paper.revision); } }, [paper, dirty]);
  async function save() {
    setBusy(true); setError('');
    try { await scholarRequest(`/projects/${paper.project_id}/papers/${paper.paper_id}`, write('PATCH', { expected_revision: baseRevision, decision, reason, notes })); setDirty(false); await onRefresh(); }
    catch (failure) { setError(messageFor(failure)); } finally { setBusy(false); }
  }
  return <article id={'paper-' + paper.paper_id} className="sf-paper"><div className="sf-row"><label className="sf-check-label"><input type="checkbox" aria-label={'纳入 ' + paper.version.title} checked={selected} disabled={paper.human_decision === 'excluded' && !selected} onChange={e => onSelect(e.target.checked)} /><span className="sf-paper-title">{paper.version.title}</span></label></div>
    <p className="sf-muted sf-small" style={{ margin: '8px 0' }}>{paper.version.authors.join(' · ') || '作者未报告'} · {paper.version.year || '年份未报告'} · {paper.version.publication_status === 'preprint' ? '预印本' : paper.version.publication_status === 'published' ? '已发表' : '出版状态待核对'}</p>
    <div className="sf-row"><span className="sf-badge">AI 建议：{decisionLabels[paper.ai_recommendation]}</span><span className="sf-badge sf-green">人工决定：{decisionLabels[paper.human_decision]}</span>
      <span className="sf-badge">{paper.documents.some(d => d.kind === 'pdf' && d.parse_status === 'ready') ? '有可读全文' : paper.version.abstract ? '有摘要 · 全文待补充' : '缺少可读原文'}</span></div>
    {paper.ai_reason && <p className="sf-muted sf-small" style={{ marginTop: 8 }}>建议理由：{paper.ai_reason}</p>}
    <details style={{ marginTop: 12 }}><summary>摘要与文献标识</summary><p style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>{paper.version.abstract || '来源未提供摘要'}</p>
      <p className="sf-muted sf-small" style={{ overflowWrap: 'anywhere', marginTop: 8 }}>{Object.entries(paper.version.identifiers).map(([name, value]) => name + ': ' + value).join(' · ')}</p></details>
    <details className="sf-paper-controls" style={{ display: 'block' }}><summary>编辑纳排决定、笔记或补充全文</summary><div className="sf-paper-controls">
      <div><label htmlFor={'decision-' + paper.id}>人工决定</label><select id={'decision-' + paper.id} value={decision} onChange={e => { setDecision(e.target.value as Decision); setDirty(true); }}><option value="pending">待决定</option><option value="included">纳入</option><option value="excluded">排除</option></select>
        <label htmlFor={'reason-' + paper.id}>决定理由{decision === 'excluded' ? '（必填）' : ''}</label><input id={'reason-' + paper.id} value={reason} onChange={e => { setReason(e.target.value); setDirty(true); }} /></div>
      <div><label htmlFor={'notes-' + paper.id}>研究笔记</label><textarea id={'notes-' + paper.id} value={notes} onChange={e => { setNotes(e.target.value); setDirty(true); }} /></div></div>
      {error && <p role="alert" className="sf-error">{error}</p>}<div className="sf-row" style={{ marginTop: 12 }}><button disabled={busy || !dirty || (decision === 'excluded' && !reason.trim())} onClick={() => void save()}>保存决定</button>
      {dirty && <button onClick={() => { setDirty(false); setError(''); }}>载入服务器版本</button>}
      <label className="sf-button" style={{ margin: 0 }}>补充 PDF<input type="file" accept="application/pdf,.pdf" aria-label={'为 ' + paper.version.title + ' 补充 PDF'} style={{ maxWidth: 210 }} onChange={e => { const file = e.target.files?.[0]; if (file) void onUpload(file); e.target.value = ''; }} /></label></div>
    </details></article>;
}
