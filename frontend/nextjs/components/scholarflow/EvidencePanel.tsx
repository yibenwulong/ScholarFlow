'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import type { ClaimStatus, EvidenceBundle, Extraction, ProjectPaper } from '@/types/scholarflow';
import { claimLabels } from '@/types/scholarflow';
import { api, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';
import ComparisonMatrix from './ComparisonMatrix';
import PdfEvidenceViewer from './PdfEvidenceViewer';
import ReadingStatusPanel from './ReadingStatusPanel';

export default function EvidencePanel({ projectId, paperId, evidenceId, claimId, papers, onRefresh }: {
  projectId: string; paperId: string; evidenceId?: string; claimId?: string; papers: ProjectPaper[]; onRefresh?: () => Promise<void>;
}) {
  const [extractions, setExtractions] = useState<Extraction[]>([]), [selectedClaim, setSelectedClaim] = useState(claimId);
  const [bundle, setBundle] = useState<EvidenceBundle>(), [selectedEvidence, setSelectedEvidence] = useState(evidenceId);
  const [view, setView] = useState<'matrix' | 'source'>(claimId ? 'source' : 'matrix');
  const [error, setError] = useState(''), [reason, setReason] = useState(''), [verdict, setVerdict] = useState<ClaimStatus>('needs_recheck'), [notice, setNotice] = useState('');
  const [extractionError, setExtractionError] = useState('');
  const loading = useRef(false), reviewSelection = useRef(0);
  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try { setExtractions((await api.extractions(projectId)).items); setExtractionError(''); }
    catch (failure) { setExtractionError(messageFor(failure)); }
    finally { loading.current = false; }
  }, [projectId]);
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 3000); return () => clearInterval(timer); }, [load]);
  useEffect(() => { if (claimId) { setSelectedClaim(claimId); setView('source'); setSelectedEvidence(evidenceId);
    reviewSelection.current++; setReason(''); setVerdict('needs_recheck'); setNotice(''); } }, [claimId, evidenceId]);
  useEffect(() => {
    if (!selectedClaim) return;
    const controller = new AbortController(); setBundle(undefined); setError('');
    api.evidence(selectedClaim, controller.signal).then(result => { if (!controller.signal.aborted) setBundle(result); }).catch(failure => { if (!controller.signal.aborted) setError(messageFor(failure)); });
    return () => controller.abort();
  }, [selectedClaim]);
  const field = extractions.flatMap(extraction => Object.values(extraction.fields)).find(field => field.claim_ids.includes(selectedClaim || ''));
  const item = bundle?.items.find(item => item.evidence.id === selectedEvidence) || bundle?.items[0];
  function resetReview() {
    reviewSelection.current++; setReason(''); setVerdict('needs_recheck'); setNotice(''); setError('');
  }
  function selectClaim(id: string, evidence?: string) {
    setSelectedClaim(id); setSelectedEvidence(evidence); setView('source'); resetReview();
  }
  async function review() {
    if (!item || !bundle) return;
    const selection = reviewSelection.current;
    try {
      const revision = Math.max(0, ...item.reviews.map(review => review.revision));
      await scholarRequest(`/evidence/${item.evidence.id}/reviews`, write('POST', { expected_revision: revision, verdict, reason }));
      const refreshed = await api.evidence(bundle.claim.id);
      setBundle(current => current?.claim.id === refreshed.claim.id ? refreshed : current);
      if (selection === reviewSelection.current) { setReason(''); setNotice('人工复核已记录。请重新检查报告以更新质量记录。'); }
    } catch (failure) { if (selection === reviewSelection.current) setError(messageFor(failure)); }
  }
  return <div className="sf-stack"><div className="sf-row sf-mobile-switch"><button onClick={() => setView('matrix')} aria-pressed={view === 'matrix'}>比较矩阵</button><button onClick={() => setView('source')} aria-pressed={view === 'source'}>原文证据</button></div>
    <ReadingStatusPanel key={projectId} projectId={projectId} papers={papers} onRefresh={onRefresh} />
    {error && <p role="alert" className="sf-error">{error}</p>}{extractionError && <p role="alert" className="sf-error">{extractionError}</p>}{notice && <p role="status" className="sf-notice">{notice}</p>}
    <div className="sf-evidence-layout"><div className={'sf-evidence-matrix ' + (view === 'source' ? 'sf-mobile-hidden' : '')}><ComparisonMatrix projectId={projectId} papers={papers} extractions={extractions}
      onSaved={load} onEvidence={selectClaim} /></div>
      <aside className={'sf-card sf-evidence-source ' + (view === 'matrix' ? 'sf-mobile-hidden' : '')}><h2>证据阅读</h2>
        {field && field.claim_ids.length > 1 && <div style={{ marginBottom: 15 }}><label htmlFor="evidence-claim">选择提取结论</label>
          <select id="evidence-claim" value={selectedClaim} onChange={event => selectClaim(event.target.value)}>{field.claim_ids.map((id, index) => <option key={id} value={id}>结论 {index + 1}</option>)}</select></div>}
        {bundle ? <><p style={{ marginBottom: 15 }}>{bundle.claim.text}</p>{item ? <>
          {bundle.items.length > 1 && <div style={{ marginBottom: 15 }}><label htmlFor="source-evidence">选择原文依据</label>
            <select id="source-evidence" value={item.evidence.id} onChange={event => { setSelectedEvidence(event.target.value); resetReview(); }}>
              {bundle.items.map((source, index) => <option key={source.evidence.id} value={source.evidence.id}>{source.document.kind === 'pdf' ? source.passage.page_number === null ? '全文片段' : `物理页 ${source.passage.page_number}` : '摘要'} · 依据 {index + 1}</option>)}</select></div>}
          <PdfEvidenceViewer item={item} />
          <details style={{ marginTop: 20 }}><summary>记录人工复核</summary><p className="sf-muted sf-small">引句定位与支持性判断分开记录，模型判断不构成事实保证。</p>
            <label htmlFor="verdict">复核结论</label><select id="verdict" value={verdict} onChange={e => setVerdict(e.target.value as ClaimStatus)}>{(Object.keys(claimLabels) as ClaimStatus[]).map(status => <option value={status} key={status}>{claimLabels[status]}</option>)}</select>
            <label htmlFor="review-reason">复核理由</label><textarea id="review-reason" value={reason} onChange={e => setReason(e.target.value)} /><button disabled={!reason.trim()} onClick={() => void review()}>保存复核记录</button>
            {item.reviews.map(review => <p className="sf-muted sf-small" key={review.id}>{claimLabels[review.verdict]} · {review.reason}</p>)}</details></> : <div className="sf-notice">此人工修订尚未绑定原文证据，需要补充依据后复核。</div>}</> : <div className="sf-empty">{selectedClaim ? '正在载入原文…' : '选择矩阵中的“查看证据”，查看对应的原文版本和位置。'}</div>}
        <p style={{ marginTop: 20 }}><Link className="sf-button" href={`/projects/${projectId}?tab=papers#paper-${paperId}`}>补充全文 PDF</Link></p>
      </aside></div></div>;
}
