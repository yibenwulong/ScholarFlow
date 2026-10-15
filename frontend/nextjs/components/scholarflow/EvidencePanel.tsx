'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import type { ClaimStatus, EvidenceBundle, Extraction, ProjectPaper } from '@/types/scholarflow';
import { claimLabels } from '@/types/scholarflow';
import { api, messageFor, scholarRequest, write } from '@/lib/scholarflow/api';
import ComparisonMatrix from './ComparisonMatrix';
import PdfEvidenceViewer from './PdfEvidenceViewer';

export default function EvidencePanel({ projectId, paperId, evidenceId, claimId, papers }: {
  projectId: string; paperId: string; evidenceId?: string; claimId?: string; papers: ProjectPaper[];
}) {
  const [extractions, setExtractions] = useState<Extraction[]>([]), [selectedClaim, setSelectedClaim] = useState(claimId);
  const [bundle, setBundle] = useState<EvidenceBundle>(), [selectedEvidence, setSelectedEvidence] = useState(evidenceId);
  const [view, setView] = useState<'matrix' | 'source'>(claimId ? 'source' : 'matrix');
  const [error, setError] = useState(''), [reason, setReason] = useState(''), [verdict, setVerdict] = useState<ClaimStatus>('needs_recheck'), [notice, setNotice] = useState('');
  const load = useCallback(async () => { try { setExtractions((await api.extractions(projectId)).items); } catch (failure) { setError(messageFor(failure)); } }, [projectId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (claimId) { setSelectedClaim(claimId); setView('source'); setSelectedEvidence(evidenceId); } }, [claimId, evidenceId]);
  useEffect(() => {
    if (!selectedClaim) return;
    const controller = new AbortController(); setBundle(undefined);
    api.evidence(selectedClaim, controller.signal).then(setBundle).catch(failure => { if (!controller.signal.aborted) setError(messageFor(failure)); });
    return () => controller.abort();
  }, [selectedClaim]);
  const item = bundle?.items.find(item => item.evidence.id === selectedEvidence) || bundle?.items[0];
  async function review() {
    if (!item || !bundle) return;
    try {
      const revision = Math.max(0, ...item.reviews.map(review => review.revision));
      await scholarRequest(`/evidence/${item.evidence.id}/reviews`, write('POST', { expected_revision: revision, verdict, reason }));
      setBundle(await api.evidence(bundle.claim.id)); setReason(''); setNotice('人工复核已记录。请重新检查报告以更新质量记录。');
    } catch (failure) { setError(messageFor(failure)); }
  }
  return <div className="sf-stack"><div className="sf-row sf-mobile-switch"><button onClick={() => setView('matrix')} aria-pressed={view === 'matrix'}>比较矩阵</button><button onClick={() => setView('source')} aria-pressed={view === 'source'}>原文证据</button></div>
    {error && <p role="alert" className="sf-error">{error}</p>}{notice && <p role="status" className="sf-notice">{notice}</p>}
    <div className="sf-evidence-layout"><div className={'sf-evidence-matrix ' + (view === 'source' ? 'sf-mobile-hidden' : '')}><ComparisonMatrix projectId={projectId} papers={papers} extractions={extractions}
      onSaved={load} onEvidence={(id, evidence) => { setSelectedClaim(id); setSelectedEvidence(evidence); setView('source'); setNotice(''); }} /></div>
      <aside className={'sf-card sf-evidence-source ' + (view === 'matrix' ? 'sf-mobile-hidden' : '')}><h2>证据阅读</h2>
        {bundle ? <><p style={{ marginBottom: 15 }}>{bundle.claim.text}</p>{item ? <><PdfEvidenceViewer item={item} />
          <details style={{ marginTop: 20 }}><summary>记录人工复核</summary><p className="sf-muted sf-small">引句定位与支持性判断分开记录，模型判断不构成事实保证。</p>
            <label htmlFor="verdict">复核结论</label><select id="verdict" value={verdict} onChange={e => setVerdict(e.target.value as ClaimStatus)}>{(Object.keys(claimLabels) as ClaimStatus[]).map(status => <option value={status} key={status}>{claimLabels[status]}</option>)}</select>
            <label htmlFor="review-reason">复核理由</label><textarea id="review-reason" value={reason} onChange={e => setReason(e.target.value)} /><button disabled={!reason.trim()} onClick={() => void review()}>保存复核记录</button>
            {item.reviews.map(review => <p className="sf-muted sf-small" key={review.id}>{claimLabels[review.verdict]} · {review.reason}</p>)}</details></> : <div className="sf-notice">此人工修订尚未绑定原文证据，需要补充依据后复核。</div>}</> : <div className="sf-empty">{selectedClaim ? '正在载入原文…' : '选择矩阵中的“查看证据”，查看对应的原文版本和位置。'}</div>}
        <p style={{ marginTop: 20 }}><Link className="sf-button" href={`/projects/${projectId}?tab=papers#paper-${paperId}`}>补充全文 PDF</Link></p>
      </aside></div></div>;
}
